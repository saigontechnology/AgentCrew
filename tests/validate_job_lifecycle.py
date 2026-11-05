"""Credential-free job lifecycle and retained-client validation."""

from __future__ import annotations

import asyncio
import copy
import gc
import importlib
import json
import sys
import tempfile
import threading
import warnings
from contextlib import ExitStack
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
app_module = importlib.import_module("AgentCrew.app")
agent_runner = importlib.import_module("AgentCrew.modules.agents.agent_runner")
scopes = importlib.import_module("AgentCrew.modules.agents.tool_result_summary")
AgentResponseStream = importlib.import_module(
    "AgentCrew.modules.agents.agent_response_stream"
).AgentResponseStream
TokenUsage = importlib.import_module("AgentCrew.modules.llm.token_usage").TokenUsage
ApplicationSetup = importlib.import_module("AgentCrew.setup").ApplicationSetup
ServiceManager = importlib.import_module(
    "AgentCrew.modules.llm.service_manager"
).ServiceManager
SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
)


class Provider:
    provider_name = "local"
    model = "model"
    structured_output = None

    def __init__(self, case, name):
        self.case = case
        self.name = name
        self.owner = None
        self.close_count = 0
        self.client = object()
        self.clear_tools = Mock()

    async def touch(self, kind):
        loop = asyncio.get_running_loop()
        if self.owner is None:
            self.owner = loop
        assert self.owner is loop and not loop.is_closed()
        self.case.events.append(
            (kind, self.name, loop, scopes.current_inference_scope())
        )
        await asyncio.sleep(0)

    async def answer(self, history):
        await self.touch("request")
        scope = scopes.current_inference_scope()
        assert self.case.is_job_scope(scope)
        self.case.requests.append(
            (
                self,
                self.client,
                asyncio.get_running_loop(),
                scope,
                copy.deepcopy(history),
            )
        )
        response = self.case.responses[len(self.case.requests) - 1]
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self):
        assert asyncio.get_running_loop() is self.owner
        assert not self.owner.is_closed() and self.close_count == 0
        assert self.case.active_streams == 0
        assert scopes.current_inference_scope() == self.case.outer_scope
        self.close_count += 1
        self.case.events.append(("close", self.name, self.owner, self.case.outer_scope))


class JobAgent:
    def __init__(self, name, description, llm_service, services, tools):
        self.name = name
        self.description = description
        self.llm = llm_service
        self.services = services
        self.tools = tools
        self.case = services["fixture"]
        self.system_prompts = []
        self.case.created_agents.append(self)

    def set_system_prompt(self, prompt):
        self.default_prompt = prompt

    def set_custom_system_prompt(self, prompt):
        self.system_prompts.append(prompt)

    def process_messages(self, history, callback=None):
        async def stream(_state):
            self.case.active_streams += 1
            try:
                response = await self.llm.answer(history)
                index = len(self.case.requests)
                usage = TokenUsage(
                    input_tokens=10 * index,
                    output_tokens=index,
                    cached_tokens=index - 1,
                    cache_creation_tokens=index,
                    total_input_tokens=12 * index - 1,
                )
                self.case.usages.append(usage)
                if callback:
                    callback([], usage)
                yield response, response, None
            finally:
                self.case.active_streams -= 1
                self.case.stream_closes.append(asyncio.get_running_loop())

        return AgentResponseStream(self, lambda: None, stream)

    def format_message(self, kind, data):
        assert kind.name == "Assistant", "Job files must remain standard content blocks"
        return {"role": "assistant", "content": data["message"]}

    def extract_last_user_message_for_memory(self, history):
        return "task"

    def store_memory_if_available(self, *_args):
        return None


class Case:
    def __init__(
        self,
        directory,
        failures,
        *,
        responses=None,
        named=False,
        stage=None,
        schema=None,
        files=None,
        real_files=False,
        token_file=True,
        cleanup_error=None,
        outer_scope=None,
        capabilities=None,
        provider_factory=None,
    ):
        self.directory = Path(directory)
        self.failures = failures
        self.responses = responses if responses is not None else ["ok"]
        self.named = named
        self.stage = stage
        self.schema = schema
        self.files = files
        self.real_files = real_files
        self.token_path = (
            self.directory / "usage" / "tokens.json" if token_file else None
        )
        self.cleanup_error = cleanup_error
        self.outer_scope = outer_scope
        self.capabilities = (
            capabilities if capabilities is not None else ["structured_output"]
        )
        self.provider_factory = provider_factory or Provider
        self.error = ValueError(f"job failure: {stage}")
        self.requests = []
        self.events = []
        self.created_agents = []
        self.providers = []
        self.usages = []
        self.file_calls = []
        self.stream_closes = []
        self.active_streams = 0
        self.loops = []
        self.binds = []
        self.resets = []
        self.echoes = []
        self.token_writes = []
        self.selection_results = []
        self.return_value = None
        self.caught = None
        self.primary = None
        self.analysis = None
        self.dedicated = None
        self.cached = Provider(self, "cached")
        self.manager = SimpleNamespace(
            agents={},
            enforce_transfer=True,
            one_turn_process=False,
            get_local_agent=lambda name: self.manager.agents.get(name),
            register_agent=lambda item: self.manager.agents.__setitem__(
                item.name, item
            ),
            select_agent_async=AsyncMock(side_effect=self.select_agent),
            select_agent=Mock(side_effect=AssertionError("Synchronous job selection")),
            close_all_remote_agents=AsyncMock(),
            close_all_cursor_agents=AsyncMock(),
        )
        self.service_manager = ServiceManager.__new__(ServiceManager)
        self.service_manager.services = {"cached": self.cached}
        self.service_manager._closed_services = set()
        self.service_manager._pending_closes = set()
        self.setup = ApplicationSetup.__new__(ApplicationSetup)
        self.setup.services = None
        self.setup.agent_manager = None
        self.setup._standalone_llm_services = []
        self.setup._plugins_initialized = False
        self.setup.plugin_manager = SimpleNamespace(
            load_all=AsyncMock(
                side_effect=AssertionError("Job plugin activation changed")
            ),
            unload_all=AsyncMock(side_effect=self.unload_plugins),
        )
        self.setup.resolve_runtime_model = Mock(side_effect=self.resolve_model)
        self.setup.setup_services = Mock(side_effect=self.setup_services)
        self.setup.setup_agents = Mock(side_effect=self.setup_agents)
        self.app = app_module.AgentCrewApplication.__new__(
            app_module.AgentCrewApplication
        )
        self.app.setup = self.setup

    def resolve_model(self, provider, model_id):
        if self.stage == "model":
            raise self.error
        return SimpleNamespace(provider=provider or "local", model_id=model_id)

    async def setup_io(self, provider, kind):
        assert scopes.current_inference_scope() == self.outer_scope
        await provider.touch(kind)

    def setup_services(self, runtime, **kwargs):
        assert runtime.provider == "local"
        assert kwargs["need_memory"] is False
        assert kwargs["application_owned_llms"] is True
        assert kwargs["memory_llm"] == "memory-option"
        assert kwargs["reason_effort"] == "high"
        self.run_async = kwargs["run_async"]
        self.primary = self.provider_factory(self, "primary")
        self.analysis = Provider(self, "analysis")
        self.providers.extend((self.primary, self.analysis))
        self.setup._standalone_llm_services.extend(self.providers)
        self.setup.services = {
            "llm": self.primary,
            "code_analysis": self.analysis,
            "tool_result_summary": None,
            "fixture": self,
        }
        self.run_async(self.setup_io(self.primary, "setup"))
        self.run_async(self.setup_io(self.analysis, "analysis"))
        if self.stage == "services":
            raise self.error
        self.setup.agent_manager = self.manager
        return self.setup.services

    def setup_agents(self, services, config_uri, **kwargs):
        assert services is self.setup.services and config_uri == "agents-option"
        assert "use_standalone_provider" not in kwargs
        assert kwargs["runtime_model"].provider == "local"
        assert kwargs["reason_effort"] == "high"
        self.dedicated = self.provider_factory(self, "dedicated")
        self.providers.append(self.dedicated)
        selected = JobAgent(
            "other" if self.stage == "lookup" else "named",
            "configured",
            self.dedicated,
            services,
            ["configured-tool"],
        )
        self.manager.register_agent(selected)
        self.run_async(self.setup_io(self.dedicated, "onboarding"))
        if self.stage == "agents":
            raise self.error

    async def select_agent(self, name):
        assert self.is_job_scope(scopes.current_inference_scope())
        assert asyncio.get_running_loop() is self.loops[0]
        if self.stage == "selection":
            raise self.error
        assert name in self.manager.agents
        selected = self.stage != "selection_false"
        self.selection_results.append(selected)
        return selected

    async def unload_plugins(self):
        assert scopes.current_inference_scope() == self.outer_scope
        assert asyncio.get_running_loop() is self.loops[0]
        self.events.append(("unload",))
        if self.cleanup_error:
            raise self.cleanup_error

    def is_job_scope(self, scope):
        if self.outer_scope is not None:
            return scope == self.outer_scope
        return bool(scope and scope.startswith("job:"))

    def run(self):
        original_loop = asyncio.events.new_event_loop
        original_bind = scopes.bind_inference_scope
        original_reset = scopes.reset_inference_scope
        original_open = open
        outside_token = original_bind(self.outer_scope)

        def loop_factory():
            loop = original_loop()
            loop.set_exception_handler(
                lambda _loop, context: self.failures.append(context)
            )
            self.loops.append(loop)
            return loop

        def bind(scope):
            record = (asyncio.current_task(), asyncio.get_running_loop(), scope)
            token = original_bind(scope)
            self.binds.append((*record, token))
            return token

        def reset(token):
            original_reset(token)
            self.resets.append(
                (
                    asyncio.current_task(),
                    asyncio.get_running_loop(),
                    scopes.current_inference_scope(),
                    token,
                )
            )

        def output_file(path, *args, **kwargs):
            mode = kwargs.get("mode", args[0] if args else "r")
            if (
                self.token_path is not None
                and str(path) == str(self.token_path)
                and mode == "w"
            ):
                assert self.active_streams == 0
                assert not any(provider.close_count for provider in self.providers)
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Token-file writing blocked the owner loop")
                assert self.is_job_scope(scopes.current_inference_scope())
                self.token_writes.append(len(self.requests))
                if self.stage == "usage":
                    raise self.error
            return original_open(path, *args, **kwargs)

        def cleanup():
            assert self.loops[0].is_closed()
            assert scopes.current_inference_scope() == self.outer_scope
            self.events.append(("mcp-cleanup",))

        case = self

        class FileProcessor:
            async def async_process_file(self, path):
                assert asyncio.get_running_loop() is case.loops[0]
                assert case.is_job_scope(scopes.current_inference_scope())
                case.file_calls.append(path)
                if case.stage == "file":
                    raise case.error
                if path.endswith("skip"):
                    return None
                return {"type": "text", "text": f"file:{path}"}

            def process_file(self, _path):
                raise AssertionError("Job used synchronous file preparation")

        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch("asyncio.events.new_event_loop", side_effect=loop_factory)
                )
                stack.enter_context(
                    patch.object(scopes, "bind_inference_scope", side_effect=bind)
                )
                stack.enter_context(
                    patch.object(scopes, "reset_inference_scope", side_effect=reset)
                )
                stack.enter_context(patch("builtins.open", side_effect=output_file))
                stack.enter_context(
                    patch("AgentCrew.modules.agents.LocalAgent", JobAgent)
                )
                stack.enter_context(
                    patch.object(
                        ServiceManager,
                        "get_instance",
                        return_value=self.service_manager,
                    )
                )
                stack.enter_context(
                    patch(
                        "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
                        return_value=SimpleNamespace(cleanup=cleanup),
                    )
                )
                stack.enter_context(
                    patch(
                        "AgentCrew.modules.llm.model_registry.ModelRegistry.get_model_capabilities",
                        return_value=self.capabilities,
                    )
                )
                stack.enter_context(patch.object(app_module.logger, "exception"))
                stack.enter_context(
                    patch.object(
                        app_module.click,
                        "echo",
                        side_effect=lambda value, **_kwargs: self.echoes.append(
                            str(value)
                        ),
                    )
                )
                if self.files:
                    file_module = importlib.import_module(
                        "AgentCrew.modules.utils.file_handler"
                    )
                    if self.real_files:
                        native = file_module.FileHandler.async_process_file

                        async def async_file(handler, path):
                            self.file_calls.append(path)
                            assert asyncio.get_running_loop() is self.loops[0]
                            assert self.is_job_scope(scopes.current_inference_scope())
                            return await native(handler, path)

                        stack.enter_context(
                            patch.object(
                                file_module.FileHandler,
                                "async_process_file",
                                async_file,
                            )
                        )
                        stack.enter_context(
                            patch.object(
                                file_module,
                                "_run_async_bridge",
                                side_effect=AssertionError("Unexpected file bridge"),
                            )
                        )
                    else:
                        stack.enter_context(
                            patch.object(file_module, "FileHandler", FileProcessor)
                        )
                try:
                    self.return_value = self.app.run_job(
                        "task",
                        agent="named" if self.named else None,
                        files=self.files,
                        provider="local",
                        agent_config="agents-option",
                        mcp_config="mcp-option",
                        memory_llm="memory-option",
                        memory_path="unused-job-memory",
                        reason_effort="high",
                        output_schema=self.schema,
                        token_usage_file=str(self.token_path)
                        if self.token_path
                        else None,
                    )
                except BaseException as error:
                    self.caught = error
                assert scopes.current_inference_scope() == self.outer_scope
        finally:
            original_reset(outside_token)
        assert len(self.loops) == 1 and self.loops[0].is_closed()
        assert self.active_streams == 0
        assert all(provider.close_count == 1 for provider in self.providers)
        assert all(provider.owner is self.loops[0] for provider in self.providers)
        assert self.cached.close_count == 0
        self.setup.plugin_manager.load_all.assert_not_called()
        self.manager.select_agent.assert_not_called()
        if self.binds:
            assert len(self.binds) == len(self.resets)
            actual_bindings = [record for record in self.binds if record[3] is not None]
            actual_resets = [record for record in self.resets if record[3] is not None]
            bindings = {id(record[3]): record[:2] for record in actual_bindings}
            assert len(bindings) == len(actual_bindings) == len(actual_resets)
            assert len(bindings) == (0 if self.outer_scope is not None else 1)
            assert all(
                bindings[id(record[3])] == record[:2] for record in actual_resets
            )
            assert all(record[:2] == self.binds[0][:2] for record in self.binds)
            assert len({record[2] for record in self.binds}) == 1
            assert self.resets[-1][3] is self.binds[0][3]
            assert self.resets[-1][2] == self.outer_scope
        else:
            assert not self.resets
        if self.requests:
            assert len({id(request[1]) for request in self.requests}) == 1
            assert len({request[3] for request in self.requests}) == 1
            assert all(request[2] is self.loops[0] for request in self.requests)
        return self


def run_case(failures, **kwargs):
    with tempfile.TemporaryDirectory() as directory:
        case = Case(directory, failures, **kwargs).run()
        if case.caught:
            raise case.caught
        return case


def assert_usage(case, usage):
    assert json.loads(case.token_path.read_text()) == asdict(usage)


def schema_outcomes(failures):
    for named in (False, True):
        case = run_case(failures, named=named, responses=["  plain response  "])
        assert case.return_value == "plain response" and len(case.requests) == 1
        assert bool(case.setup.setup_agents.call_count) is named
        assert case.requests[0][0] is (case.dedicated if named else case.primary)
    scenarios = (
        (['{"value":1}'], '{"value":1}', 1),
        (['{"value":"bad"}', '{"value":2}'], '{"value":2}', 2),
        (['{"value":"bad"}'] * 4, '{"value":"bad"}', 4),
        (['```json\n{"value":3}\n```'], '{"value":3}', 1),
    )
    for responses, expected, count in scenarios:
        with tempfile.TemporaryDirectory() as directory:
            case = Case(
                directory, failures, named=True, schema=SCHEMA, responses=responses
            ).run()
            assert case.caught is None and case.return_value == expected
            assert len(case.requests) == count
            assert_usage(case, case.usages[-1])
            assert case.token_writes == [count]
            assert case.dedicated.structured_output == json.loads(SCHEMA)
            if count > 1:
                for request in case.requests[1:]:
                    assert request[4][-1]["content"][0]["text"].startswith(
                        "JSON Schema Validation Error:"
                    )
    case = run_case(
        failures, named=True, schema=SCHEMA, responses=['{"value":1}'], capabilities=[]
    )
    assert case.created_agents[0].system_prompts == [
        case.app._parse_output_schema(SCHEMA)[0]
    ]


def file_preparation(failures):
    case = run_case(failures, files=["  first  ", "second", "skip"])
    assert case.file_calls == ["first", "second", "skip"]
    assert case.requests[0][4][0] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "file:first"},
            {"type": "text", "text": "file:second"},
        ],
    }
    with tempfile.TemporaryDirectory() as directory:
        first = Path(directory) / "first.txt"
        second = Path(directory) / "second.txt"
        first.write_text("first document", encoding="utf-8")
        second.write_text("second document", encoding="utf-8")
        case = Case(
            directory, failures, files=[f" {first} ", str(second)], real_files=True
        ).run()
        assert case.caught is None and case.return_value == "ok"
        assert case.file_calls == [str(first), str(second)]
        content = case.requests[0][4][0]["content"]
        assert len(content) == 2 and all(part["type"] == "text" for part in content)
        assert (
            "first document" in content[0]["text"]
            and "second document" in content[1]["text"]
        )


def failure_paths(failures):
    for stage in (
        "model",
        "services",
        "agents",
        "lookup",
        "selection",
        "file",
        "usage",
    ):
        with tempfile.TemporaryDirectory() as directory:
            case = Case(
                directory,
                failures,
                named=True,
                stage=stage,
                files=["input"] if stage == "file" else None,
            ).run()
            assert case.caught is not None, stage
            if stage == "lookup":
                assert isinstance(case.caught, ValueError) and "not found" in str(
                    case.caught
                )
            else:
                assert case.caught is case.error, stage
            assert not case.token_path.exists()
            assert bool(case.binds) is (
                stage in ("lookup", "selection", "file", "usage")
            )
    for error in (
        RuntimeError("provider failure"),
        asyncio.CancelledError("job cancelled"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            case = Case(directory, failures, responses=[error]).run()
            assert type(case.caught) is type(error) and case.caught.args == error.args
            assert case.binds and not case.token_path.exists()
    with tempfile.TemporaryDirectory() as directory:
        case = Case(directory, failures, schema="{invalid").run()
        assert isinstance(case.caught, ValueError) and "Invalid JSON schema" in str(
            case.caught
        )
        assert case.binds and not case.requests
    with tempfile.TemporaryDirectory() as directory:
        from jsonschema.exceptions import SchemaError

        case = Case(
            directory,
            failures,
            schema='{"type":"invalid-type"}',
            responses=['{"value":1}'],
        ).run()
        assert isinstance(case.caught, SchemaError) and case.binds


def selection_false(failures):
    for named in (False, True):
        for outer_scope in (None, "caller-parent"):
            with tempfile.TemporaryDirectory() as directory:
                case = Case(
                    directory,
                    failures,
                    named=named,
                    stage="selection_false",
                    schema=SCHEMA,
                    files=["must-not-process.txt"],
                    responses=['{"value":1}'],
                    outer_scope=outer_scope,
                )
                previous_agent = object()
                case.manager.current_agent = previous_agent
                with patch.object(
                    case.app,
                    "_validate_response_against_schema",
                    wraps=case.app._validate_response_against_schema,
                ) as validate:
                    case.run()
                assert isinstance(case.caught, RuntimeError), (
                    "Job ignored a false selection result",
                    case.caught,
                )
                name = case.created_agents[-1].name
                assert str(case.caught) == f"Failed to select agent '{name}'"
                assert case.selection_results == [False]
                case.manager.select_agent_async.assert_awaited_once_with(name)
                assert case.manager.current_agent is previous_agent
                assert case.return_value is None
                assert not case.file_calls and not case.requests and not case.usages
                assert not case.stream_closes and not case.token_writes
                assert not case.token_path.exists()
                validate.assert_not_called()
                assert len(case.binds) == len(case.resets) == 1
                assert case.binds[0][:2] == case.resets[0][:2]
                assert case.resets[0][2] == outer_scope
                assert {provider.name for provider in case.providers} == (
                    {"primary", "analysis", "dedicated"}
                    if named
                    else {"primary", "analysis"}
                )
                assert all(
                    provider.close_count == 1 and provider.owner is case.loops[0]
                    for provider in case.providers
                )
                assert case.cached.close_count == 0
                case.setup.plugin_manager.unload_all.assert_awaited_once()
                case.manager.close_all_remote_agents.assert_awaited_once()
                case.manager.close_all_cursor_agents.assert_awaited_once()
                assert sum(event[0] == "mcp-cleanup" for event in case.events) == 1
                assert case.events[-1] == ("mcp-cleanup",)


def cleanup_and_scope(failures):
    case = run_case(failures, named=True, outer_scope="caller-parent")
    assert case.resets[-1][2] == "caller-parent"
    for primary in (None, RuntimeError("original job failure")):
        with tempfile.TemporaryDirectory() as directory:
            cleanup_error = ValueError("cleanup failure")
            case = Case(
                directory,
                failures,
                named=True,
                responses=[primary] if primary else ["ok"],
                cleanup_error=cleanup_error,
            ).run()
            assert case.caught is (primary or cleanup_error)
            assert all(provider.close_count == 1 for provider in case.providers)


def empty_response_retries(failures):
    with tempfile.TemporaryDirectory() as directory:
        case = Case(directory, failures, responses=["", "ok"]).run()
        assert case.caught is None and case.return_value == "ok"
        assert len(case.requests) == 2
        assert_usage(case, case.usages[0].merge(case.usages[1]))
    with tempfile.TemporaryDirectory() as directory:
        count = agent_runner.MAX_EMPTY_RESPONSE_RETRIES + 1
        case = Case(directory, failures, responses=[""] * count).run()
        assert isinstance(case.caught, RuntimeError) and "empty response" in str(
            case.caught
        )
        assert len(case.requests) == count and not case.token_path.exists()


def local_keepalive(failures):
    from openai import AsyncOpenAI

    connections = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            connections.append(self.client_address)
            content = '{"value":"bad"}' if len(connections) == 1 else '{"value":1}'
            body = (
                "data: "
                + json.dumps(
                    {
                        "id": "local",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "local",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": content},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                )
                + "\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever)
    worker.start()

    class HttpProvider(Provider):
        def __init__(self, case, name):
            super().__init__(case, name)
            self.client = AsyncOpenAI(
                api_key="local-only",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                max_retries=0,
            )

        async def answer(self, history):
            await super().answer(history)
            async with await self.client.chat.completions.create(
                model="local",
                messages=[{"role": "user", "content": "local job"}],
                stream=True,
            ) as response:
                return "".join(
                    [chunk.choices[0].delta.content or "" async for chunk in response]
                )

        async def close(self):
            await super().close()
            await self.client.close()
            assert self.client.is_closed()

    try:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                "os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            ),
        ):
            case = Case(
                directory,
                failures,
                named=True,
                schema=SCHEMA,
                responses=["ignored", "ignored"],
                provider_factory=HttpProvider,
            ).run()
            assert case.caught is None and case.return_value == '{"value":1}'
            assert len(case.requests) == 2 and len(connections) == 2
            assert connections[0] == connections[1]
            assert all(provider.close_count == 1 for provider in case.providers)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()


def main():
    failures = []
    unraisables = []
    previous = sys.unraisablehook
    checks = (
        schema_outcomes,
        file_preparation,
        failure_paths,
        selection_false,
        cleanup_and_scope,
        empty_response_retries,
        local_keepalive,
    )
    try:
        sys.unraisablehook = unraisables.append
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ResourceWarning)
            for check in checks:
                check(failures)
                gc.collect()
                print(f"{check.__name__}: PASS")
            assert not failures, failures
            assert not unraisables, unraisables
            assert not [
                item for item in captured if issubclass(item.category, ResourceWarning)
            ]
    finally:
        service = sys.modules.get("AgentCrew.modules.mcpclient.service")
        if service is not None:
            service.mcp_log_io.close()
        sys.unraisablehook = previous
    print(f"Job lifecycle: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
