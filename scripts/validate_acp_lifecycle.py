"""Credential-free ACP owner-loop and session-cleanup validation."""

import asyncio
import gc
import importlib
import json
import sys
import tempfile
import threading
import warnings
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

agent_module = importlib.import_module("AgentCrew.modules.acp.agent")
AgentCrewAcpAgent = agent_module.AgentCrewAcpAgent
LocalAgent = importlib.import_module("AgentCrew.modules.agents.local_agent").LocalAgent
AcpSessionStore = importlib.import_module(
    "AgentCrew.modules.acp.session_store"
).AcpSessionStore
context_module = importlib.import_module("AgentCrew.modules.acp.tools.context")
current_inference_scope = importlib.import_module(
    "AgentCrew.modules.agents.tool_result_summary"
).current_inference_scope
host_checks = importlib.import_module("validate_host_lifecycle")
LoopProvider = host_checks.LoopProvider
application = host_checks.application


class Connection:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def session_update(self, session_id, update):
        self.events.append(("update", session_id, asyncio.get_running_loop()))

    async def release_terminal(self, session_id, terminal_id):
        self.events.append(
            ("release", session_id, terminal_id, asyncio.get_running_loop())
        )
        if self.closed:
            raise RuntimeError("Client disconnected")

    async def kill_terminal(self, session_id, terminal_id):
        self.events.append(
            ("kill", session_id, terminal_id, asyncio.get_running_loop())
        )
        if self.closed:
            raise RuntimeError("Client disconnected")


class McpService:
    def __init__(self, events):
        self.events = events
        self._acp_server_configs = {}
        self.active = set()
        self.started = asyncio.Event()
        self.block = False

    @staticmethod
    def _get_server_id_format(name, agent_name):
        return f"{agent_name}:{name}"

    async def register_tools_for_agent(self, config, agent_name):
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        self.active.add((config.name, agent_name))
        self.events.append(
            ("mcp-start", config.name, agent_name, asyncio.get_running_loop())
        )

    async def deregister_server_tools(self, name, agent_name):
        self.active.discard((name, agent_name))
        self.events.append(("mcp-close", name, agent_name, asyncio.get_running_loop()))


class Fixture:
    def __init__(self, directory, provider=None):
        self.events = []
        self.provider = provider or LoopProvider("dedicated")
        self.conn = Connection(self.events)
        self.mcp = McpService(self.events)
        self.mcp_manager = SimpleNamespace(
            initialized=True, mcp_service=self.mcp, cleanup=Mock()
        )
        self.locals = {}
        for name in ("alpha", "beta"):
            local = LocalAgent.__new__(LocalAgent)
            local.name = name
            local.description = name
            local.llm = self.provider
            local.services = {"mcp_client": self.mcp}
            local.tool_definitions = {"read_file": {"name": "read_file"}}
            local.tool_handlers = {"read_file": Mock()}
            local.resync_tools_to_llm = Mock()

            def register(definition, handler, service_instance=None, target=local):
                spec = definition()
                target.tool_definitions[spec["name"]] = spec
                target.tool_handlers[spec["name"]] = handler

            local.register_tool = register
            self.locals[name] = local
        self.manager = SimpleNamespace(
            agents=self.locals,
            get_agent=self.locals.get,
            get_local_agent=self.locals.get,
            get_current_agent=lambda: self.locals["alpha"],
            get_current_agent_name=lambda: "alpha",
            select_agent=Mock(),
            close_all_remote_agents=AsyncMock(),
            close_all_cursor_agents=AsyncMock(),
        )
        self.agent = AgentCrewAcpAgent(
            self.manager,
            default_agent_name="alpha",
            session_store=AcpSessionStore(base_dir=directory),
        )
        self.agent.on_connect(self.conn)
        self.agent._model_controller = SimpleNamespace(
            resolve_agent_name=lambda name=None: name or "alpha",
            current_agent_model_id=lambda name: "local/test",
            default_thought_level_for_model=lambda model: "none",
            validated_thought_level_for_model=lambda model, level: level or "none",
            apply_session_thought_level_to_agent=AsyncMock(),
            apply_session_model_to_agent=AsyncMock(),
            build_modes=lambda name: None,
            build_config_options=lambda *args: [],
        )
        self.agent._tool_manager.update_capabilities(
            SimpleNamespace(
                fs=SimpleNamespace(read_text_file=True, write_text_file=False),
                terminal=True,
            )
        )
        restore = self.agent._tool_manager.restore_builtin_tools

        def restore_tools(state):
            self.events.append(("restore", id(state), asyncio.get_running_loop()))
            restore(state)

        self.agent._tool_manager.restore_builtin_tools = restore_tools
        self.started = asyncio.Queue()
        self.body = None

        async def turn(session_id, state, conn):
            context = context_module._current_acp_session.get()
            assert context is not None and context.session_id == session_id
            assert context.conn is conn
            assert current_inference_scope() == f"acp:{session_id}"
            self.events.append(("turn-start", session_id, asyncio.get_running_loop()))
            self.started.put_nowait(session_id)
            try:
                if self.body is not None:
                    await self.body(session_id, state)
                else:
                    await self.provider.request()
                    state.history.append({"role": "assistant", "content": "hello"})
                return "end_turn"
            finally:
                self.events.append(("turn-end", session_id, asyncio.get_running_loop()))

        self.agent._turn_executor.run_turn = turn

    async def new(self):
        response = await self.agent.new_session(
            cwd="/tmp",
            additional_directories=[],
            mcp_servers=[
                {
                    "type": "http",
                    "name": "local-tools",
                    "url": "http://127.0.0.1:9/mcp",
                    "headers": [],
                }
            ],
        )
        return response.session_id

    async def prompt(self, session_id):
        return await self.agent.prompt(session_id, [{"type": "text", "text": "hello"}])

    def assert_closed(self):
        assert self.agent._closing and self.agent._close_task.done()
        assert not self.agent._sessions and not self.agent._session_locks
        assert not self.agent._session_requests and not self.agent._session_closes
        assert self.agent._conn is None and self.agent._client_comm.conn is None
        assert not self.mcp.active and not self.mcp._acp_server_configs
        assert context_module._current_acp_session.get() is None
        assert current_inference_scope() is None


async def bounded(operation):
    task = asyncio.create_task(operation)
    done, _ = await asyncio.wait({task}, timeout=4)
    if not done:
        task.cancel()
        raise AssertionError("ACP lifecycle operation exceeded its deadline")
    return task.result()


def run_case(body, failures):
    with tempfile.TemporaryDirectory() as directory, asyncio.Runner() as runner:
        runner.get_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        fixture = Fixture(directory)

        async def exercise():
            try:
                await body(fixture)
            finally:
                try:
                    await fixture.agent.aclose()
                finally:
                    await fixture.provider.close()

        with patch(
            "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
            return_value=fixture.mcp_manager,
        ):
            runner.run(bounded(exercise()))
        fixture.assert_closed()
        assert fixture.provider.close_count == 1


def session_graph(failures):
    async def exercise(fixture):
        agent = fixture.agent
        first = await fixture.new()
        assert (await fixture.prompt(first)).stop_reason == "end_turn"
        stored = await agent.session_store.load_session(first)
        assert stored is not None and len(stored.history) == 2
        await agent.close_session(first)
        count = len(fixture.events)
        await agent.close_session(first)
        assert len(fixture.events) == count
        await agent.load_session(cwd="/tmp", session_id=first, mcp_servers=[])
        resumed = await agent.resume_session(
            cwd="/tmp", session_id=first, mcp_servers=[]
        )
        assert first in agent._sessions and resumed.config_options == []
        forked = await agent.fork_session(cwd="/tmp", session_id=first, mcp_servers=[])
        assert forked.session_id != first
        second = await fixture.new()
        assert second not in (first, forked.session_id)
        assert set(agent._sessions) == {first, forked.session_id, second}
        await asyncio.gather(agent.aclose(), agent.aclose())
        count = len(fixture.events)
        await agent.aclose()
        assert len(fixture.events) == count
        fixture.assert_closed()
        await agent.cancel("already-closed")
        fixture.assert_closed()
        for session_id in (first, forked.session_id, second):
            assert await agent.session_store.load_session(session_id) is not None
        for call in (
            agent.new_session(cwd="/tmp"),
            agent.load_session(cwd="/tmp", session_id=first),
            agent.resume_session(cwd="/tmp", session_id=first),
            agent.fork_session(cwd="/tmp", session_id=first),
            fixture.prompt(first),
        ):
            try:
                await call
            except agent_module.RequestError:
                pass
            else:
                raise AssertionError("Closed ACP agent admitted session work")

    run_case(exercise, failures)


def active_shutdown(failures):
    async def exercise(fixture):
        session_id = await fixture.new()
        state = fixture.agent._sessions[session_id]

        async def blocked(current_id, current_state):
            terminal_id = f"terminal-{asyncio.current_task().get_name()}"
            current_state.tool_state.acp_active_terminals[terminal_id] = terminal_id
            await asyncio.Event().wait()

        fixture.body = blocked
        prompt = asyncio.create_task(fixture.prompt(session_id))
        assert await fixture.started.get() == session_id
        await fixture.agent.cancel(session_id)
        assert (await prompt).stop_reason == "cancelled"
        assert state.current_task is None and state.cancelled
        fixture.body = None
        assert (await fixture.prompt(session_id)).stop_reason == "end_turn"
        assert not state.cancelled
        while not fixture.started.empty():
            fixture.started.get_nowait()
        fixture.body = blocked
        prompts = [asyncio.create_task(fixture.prompt(session_id)) for _ in range(2)]
        for _ in prompts:
            assert await fixture.started.get() == session_id
        await asyncio.gather(fixture.agent.aclose(), fixture.agent.aclose())
        assert all([(await task).stop_reason == "cancelled" for task in prompts])
        ends = [
            index
            for index, event in enumerate(fixture.events)
            if event[0] == "turn-end"
        ]
        restores = [
            index for index, event in enumerate(fixture.events) if event[0] == "restore"
        ]
        assert max(ends) < min(restores)
        releases = Counter(
            event[2] for event in fixture.events if event[0] == "release"
        )
        kills = Counter(event[2] for event in fixture.events if event[0] == "kill")
        assert releases == kills and all(count == 1 for count in releases.values())
        fixture.assert_closed()

    run_case(exercise, failures)


def explicit_close(failures):
    async def exercise(fixture):
        session_id = await fixture.new()

        async def blocked(current_id, state):
            state.tool_state.acp_active_terminals["explicit-terminal"] = (
                "explicit-terminal"
            )
            await asyncio.Event().wait()

        fixture.body = blocked
        prompt = asyncio.create_task(fixture.prompt(session_id))
        await fixture.started.get()
        releasing = asyncio.Event()
        release_gate = asyncio.Event()
        original_release = fixture.conn.release_terminal

        async def release_terminal(**kwargs):
            releasing.set()
            await release_gate.wait()
            await original_release(**kwargs)

        fixture.conn.release_terminal = release_terminal
        first_close = asyncio.create_task(fixture.agent.close_session(session_id))
        second_close = asyncio.create_task(fixture.agent.close_session(session_id))
        await releasing.wait()
        first_close.cancel()
        await asyncio.sleep(0)
        assert not first_close.done()
        release_gate.set()
        outcomes = await asyncio.gather(
            first_close, second_close, return_exceptions=True
        )
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert not isinstance(outcomes[1], BaseException)
        assert (await prompt).stop_reason == "cancelled"
        assert session_id not in fixture.agent._sessions
        assert session_id not in fixture.agent._session_locks
        assert fixture.agent._conn is fixture.conn
        assert sum(event[0] == "release" for event in fixture.events) == 1
        assert sum(event[0] == "kill" for event in fixture.events) == 1
        assert sum(event[0] == "restore" for event in fixture.events) == 1
        second = await fixture.new()
        async with fixture.agent._session_request(second):
            for call in (
                fixture.agent.aclose(),
                fixture.agent.close_session(second),
            ):
                try:
                    await call
                except (RuntimeError, agent_module.RequestError):
                    pass
                else:
                    raise AssertionError("ACP cleanup admitted a self-wait deadlock")
        assert not fixture.agent._closing

    run_case(exercise, failures)


def startup_race(failures):
    async def exercise(fixture):
        fixture.mcp.block = asyncio.Event()
        creating = asyncio.create_task(fixture.new())
        await fixture.mcp.started.wait()
        await bounded(fixture.agent.aclose())
        try:
            await creating
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("In-flight session creation survived shutdown")
        fixture.assert_closed()

    run_case(exercise, failures)


def close_failure(failures):
    async def exercise(fixture):
        first, second = await fixture.new(), await fixture.new()
        bad_state = fixture.agent._sessions[first]
        restore = fixture.agent._tool_manager.restore_builtin_tools
        error = ValueError("restore failure")

        def fail_once(state):
            restore(state)
            if state is bad_state:
                raise error

        fixture.agent._tool_manager.restore_builtin_tools = fail_once
        for attempt in range(2):
            count = len(fixture.events)
            try:
                await bounded(fixture.agent.aclose())
            except BaseExceptionGroup as caught:
                assert "cleanup failed" in str(caught.exceptions[0])
            else:
                raise AssertionError("Session cleanup failure was hidden")
            if attempt:
                assert len(fixture.events) == count
        assert len([event for event in fixture.events if event[0] == "restore"]) == 2
        assert len([event for event in fixture.events if event[0] == "mcp-close"]) == 2
        assert (
            first not in fixture.agent._sessions
            and second not in fixture.agent._sessions
        )
        fixture.assert_closed()

    with tempfile.TemporaryDirectory() as directory, asyncio.Runner() as runner:
        runner.get_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        fixture = Fixture(directory)
        try:
            with patch(
                "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
                return_value=fixture.mcp_manager,
            ):
                runner.run(bounded(exercise(fixture)))
        finally:
            runner.run(fixture.provider.close())


def protocol_wrapper(failures):
    for stage in (
        "return",
        "eof",
        "startup",
        "failure",
        "cancel",
        "failure-and-cleanup",
    ):
        _protocol_wrapper_case(stage, failures)


def _protocol_wrapper_case(stage, failures):
    with tempfile.TemporaryDirectory() as directory, asyncio.Runner() as runner:
        owner = runner.get_loop()
        owner.set_exception_handler(lambda loop, context: failures.append(context))
        fixture = Fixture(directory)
        error = ValueError(f"protocol {stage}")

        async def protocol(agent, *, use_unstable_protocol):
            assert agent is fixture.agent and use_unstable_protocol is True
            assert asyncio.get_running_loop() is owner
            if stage == "startup":
                raise error
            session_id = await fixture.new()
            assert (await fixture.prompt(session_id)).stop_reason == "end_turn"
            if stage == "eof":
                agent._sessions[session_id].tool_state.acp_active_terminals[
                    "eof-terminal"
                ] = "eof-terminal"
                fixture.conn.closed = True
            if stage == "failure-and-cleanup":
                fixture.agent._tool_manager.restore_builtin_tools = Mock(
                    side_effect=RuntimeError("cleanup")
                )
            if stage in ("failure", "failure-and-cleanup"):
                raise error
            if stage == "cancel":
                raise asyncio.CancelledError()

        with (
            patch.object(agent_module, "AgentCrewAcpAgent", return_value=fixture.agent),
            patch("acp.run_agent", side_effect=protocol),
            patch(
                "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
                return_value=fixture.mcp_manager,
            ),
            patch.object(agent_module.logger, "exception") as logged,
        ):
            try:
                runner.run(agent_module.run_acp_agent(fixture.manager, "alpha"))
            except ValueError as caught:
                assert (
                    stage in ("startup", "failure", "failure-and-cleanup")
                    and caught is error
                )
            except asyncio.CancelledError:
                assert stage == "cancel"
            else:
                assert stage in ("return", "eof")
            assert logged.call_count == int(stage == "failure-and-cleanup")
        fixture.assert_closed()
        runner.run(fixture.provider.close())
        assert fixture.provider.close_count == 1 and fixture.provider.owner is owner


def sdk_eof(failures):
    async def exercise(fixture):
        sdk_run_agent = importlib.import_module("acp").run_agent
        reader = asyncio.StreamReader()
        reader.feed_eof()

        stream_protocol = asyncio.StreamReaderProtocol(reader)

        class Transport(asyncio.Transport):
            closed = False

            def write(self, data):
                pass

            def is_closing(self):
                return self.closed

            def close(self):
                if not self.closed:
                    self.closed = True
                    stream_protocol.connection_lost(None)

        transport = Transport()
        stream_protocol.connection_made(transport)
        writer = asyncio.StreamWriter(
            transport, stream_protocol, reader, asyncio.get_running_loop()
        )

        async def protocol(agent, **kwargs):
            await sdk_run_agent(
                agent,
                input_stream=writer,
                output_stream=reader,
                **kwargs,
            )

        try:
            with (
                patch.object(
                    agent_module, "AgentCrewAcpAgent", return_value=fixture.agent
                ),
                patch("acp.run_agent", side_effect=protocol),
            ):
                await agent_module.run_acp_agent(fixture.manager)
            fixture.assert_closed()
        finally:
            writer.close()
            await writer.wait_closed()
        assert writer.is_closing()

    run_case(exercise, failures)


def owned_setup(fixture, primary, cached):
    from AgentCrew.modules.llm.service_manager import ServiceManager
    from AgentCrew.setup import ApplicationSetup

    setup = ApplicationSetup.__new__(ApplicationSetup)
    setup._plugins_initialized = False
    setup.plugin_manager = SimpleNamespace(unload_all=AsyncMock())
    setup._standalone_llm_services = [primary, primary]
    setup.services = {"llm": primary, "tool_result_summary": None}
    setup.agent_manager = fixture.manager
    manager = ServiceManager.__new__(ServiceManager)
    manager.services = {"cached": cached}
    manager._closed_services = set()
    manager._pending_closes = set()
    return setup, manager


def application_entry(failures):
    for stage in (
        "return",
        "model",
        "services",
        "agents",
        "constructor",
        "protocol",
        "cancel",
    ):
        _application_entry_case(stage, failures)


def _application_entry_case(stage, failures):
    from AgentCrew.modules.llm.service_manager import ServiceManager

    with tempfile.TemporaryDirectory() as directory:
        fixture = Fixture(directory)
        primary = LoopProvider("primary")
        cached = LoopProvider("cached")
        setup, service_manager = owned_setup(fixture, primary, cached)
        callback = None

        async def setup_request(provider):
            asyncio.get_running_loop().set_exception_handler(
                lambda loop, context: failures.append(context)
            )
            await provider.request()

        def configure(runtime_model, **kwargs):
            nonlocal callback
            assert kwargs["application_owned_llms"] is True
            callback = kwargs["run_async"]
            callback(setup_request(primary))
            if stage == "services":
                raise ValueError("service startup")
            return setup.services

        def configure_agents(services, config_uri, **kwargs):
            assert kwargs["runtime_model"].provider == "local"
            callback(setup_request(fixture.provider))
            if stage == "agents":
                raise ValueError("agent startup")
            setup.agent_manager = fixture.manager

        setup.resolve_runtime_model = Mock(
            return_value=SimpleNamespace(provider="local"),
            side_effect=ValueError("model") if stage == "model" else None,
        )
        setup.setup_services = Mock(side_effect=configure)
        setup.setup_agents = Mock(side_effect=configure_agents)
        setup.restore_last_agent = Mock()

        def construct(agent_manager, default_agent_name):
            assert agent_manager is fixture.manager and default_agent_name == "beta"
            if stage == "constructor":
                raise ValueError("constructor")
            return fixture.agent

        async def protocol(agent, *, use_unstable_protocol):
            assert use_unstable_protocol
            session_id = await fixture.new()
            assert (await fixture.prompt(session_id)).stop_reason == "end_turn"
            if stage == "protocol":
                raise ValueError("protocol")
            if stage == "cancel":
                raise asyncio.CancelledError()

        shutdown = setup.shutdown

        async def checked_shutdown():
            if stage in ("return", "protocol", "cancel"):
                fixture.assert_closed()
            fixture.events.append(("provider-shutdown", asyncio.get_running_loop()))
            await shutdown()

        setup.shutdown = checked_shutdown
        with (
            patch.object(ServiceManager, "get_instance", return_value=service_manager),
            patch.object(agent_module, "AgentCrewAcpAgent", side_effect=construct),
            patch("acp.run_agent", side_effect=protocol),
            patch("AgentCrew.app.logger.exception") as logged,
            patch(
                "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
                return_value=fixture.mcp_manager,
            ) as mcp,
            patch("AgentCrew.app.click.echo"),
        ):
            try:
                application(setup).run_acp(provider="local", agent="beta")
            except asyncio.CancelledError:
                assert stage == "cancel"
            assert logged.call_count == int(stage not in ("return", "cancel")), (
                stage,
                logged.call_args_list,
            )
            mcp.return_value.cleanup.assert_called_once()
        assert primary.close_count == fixture.provider.close_count == 1
        assert cached.close_count == 0
        assert primary.owner is fixture.provider.owner and primary.owner.is_closed()


def local_keepalive(failures):
    from openai import AsyncOpenAI

    from AgentCrew.modules.llm.service_manager import ServiceManager

    connections = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            connections.append(self.client_address)
            body = (
                "data: "
                + json.dumps(
                    {
                        "id": "local",
                        "object": "chat.completion.chunk",
                        "model": "local",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "hello"},
                                "finish_reason": None,
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    sdk = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(directory)
            primary = LoopProvider("primary")
            cached = LoopProvider("cached")
            setup, manager = owned_setup(fixture, primary, cached)

            class Provider:
                model = "local"
                provider_name = "local"

                def __init__(self):
                    self.owner = None
                    self.close_count = 0
                    self.requests = 0

                async def request(self):
                    loop = asyncio.get_running_loop()
                    self.owner = self.owner or loop
                    assert loop is self.owner
                    self.requests += 1
                    async with await sdk.chat.completions.create(
                        model="local",
                        messages=[{"role": "user", "content": "hi"}],
                        stream=True,
                    ) as stream:
                        return "".join(
                            [
                                chunk.choices[0].delta.content or ""
                                async for chunk in stream
                            ]
                        )

                async def close(self):
                    assert asyncio.get_running_loop() is self.owner
                    self.close_count += 1
                    assert self.close_count == 1
                    assert fixture.agent._conn is None
                    await sdk.close()

            provider = Provider()
            fixture.provider = provider
            for local in fixture.locals.values():
                local.llm = provider

            async def body(session_id, state):
                text = await provider.request()
                assert text == "hello"
                state.history.append({"role": "assistant", "content": text})

            fixture.body = body
            with (
                patch.object(ServiceManager, "get_instance", return_value=manager),
                patch(
                    "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
                    return_value=fixture.mcp_manager,
                ),
                patch.dict(
                    "os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
                ),
                application(setup)._host_lifecycle() as runner,
            ):
                owner = runner.get_loop()
                owner.set_exception_handler(
                    lambda loop, context: failures.append(context)
                )
                sdk = AsyncOpenAI(
                    api_key="local-only",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    max_retries=0,
                )

                async def protocol():
                    session_id = await fixture.new()
                    for _ in range(2):
                        assert (
                            await fixture.prompt(session_id)
                        ).stop_reason == "end_turn"
                    await fixture.agent.aclose()

                runner.run(protocol())
            assert provider.close_count == 1 and provider.requests == 2
            assert provider.owner is owner and owner.is_closed() and sdk.is_closed()
            assert primary.close_count == 1 and cached.close_count == 0
            assert len(connections) == 2 and connections[0] == connections[1]
            sdk = None
            gc.collect()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()


def main():
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    failures, unraisables = [], []
    previous = sys.unraisablehook
    sys.unraisablehook = unraisables.append
    checks = (
        session_graph,
        active_shutdown,
        explicit_close,
        startup_race,
        close_failure,
        protocol_wrapper,
        sdk_eof,
        application_entry,
        local_keepalive,
    )
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ResourceWarning)
            for check in checks:
                check(failures)
                print(f"{check.__name__}: PASS")
            gc.collect()
            assert not failures, failures
            assert not unraisables, unraisables
            assert not [
                warning
                for warning in captured
                if issubclass(warning.category, ResourceWarning)
            ], captured
    finally:
        service = sys.modules.get("AgentCrew.modules.mcpclient.service")
        if service is not None:
            service.mcp_log_io.close()
        sys.unraisablehook = previous
    print(f"ACP lifecycle: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
