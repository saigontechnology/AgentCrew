"""Credential-free console/runtime lifecycle checks; run with uv run python."""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import importlib
import inspect
import json
import queue
import sys
import tempfile
import threading
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

InteractiveAsyncRuntime = importlib.import_module(
    "AgentCrew.modules.runtime"
).InteractiveAsyncRuntime


def runtime_mechanics():
    from AgentCrew.modules.events.event_bus import EventBus

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    errors = []

    async def configure():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, data: errors.append(data)
        )

    runtime.run(configure())
    try:
        bus = EventBus()
        values = []

        def callback(**data):
            values.append(runtime.run(asyncio.sleep(0, result="nested")))

        bus.on("validation", callback)
        runtime.run(bus.emit("validation"))
        assert values == ["nested"]
        caller = threading.Thread(
            target=lambda: values.append(runtime.run(asyncio.sleep(0, result="idle"))),
            daemon=True,
        )
        caller.start()
        caller.join(3)
        assert not caller.is_alive() and values[-1] == "idle"

        async def reject_owner():
            coro = asyncio.sleep(0)
            try:
                runtime.run(coro)
            except RuntimeError:
                assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
            else:
                raise AssertionError("Owner-thread blocking call was accepted")

        runtime.run(reject_owner())
        entered, unwound = threading.Event(), threading.Event()

        async def wait_for_cancel():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                unwound.set()

        future = runtime.submit(wait_for_cancel())
        assert entered.wait(3)
        future.cancel()
        try:
            future.result(3)
        except concurrent.futures.CancelledError:
            pass
        else:
            raise AssertionError("Operation did not cancel")
        assert unwound.wait(3)
        bad = asyncio.sleep(0)
        unopened = InteractiveAsyncRuntime()
        try:
            unopened.submit(bad)
        except RuntimeError:
            assert inspect.getcoroutinestate(bad) == inspect.CORO_CLOSED
        else:
            raise AssertionError("Unstarted runtime accepted work")
        assert not errors, errors
    finally:
        runtime.shutdown()
    assert not runtime.is_alive() and runtime.state == "closed"


def shutdown_paths():
    for error in (ValueError("finalizer"), TimeoutError("provider close")):
        runtime = InteractiveAsyncRuntime()
        runtime.start()

        async def fail(error=error):
            raise error

        try:
            runtime.shutdown(fail())
        except type(error) as caught:
            assert type(caught) is type(error) and caught.args == error.args
        else:
            raise AssertionError("Finalizer failure was hidden")
        assert not runtime.is_alive()
    with patch("asyncio.new_event_loop", side_effect=OSError("startup")):
        runtime = InteractiveAsyncRuntime()
        try:
            runtime.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Startup failure was hidden")
        assert not runtime.is_alive()

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    started, finished = threading.Event(), threading.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:

            async def cleanup():
                await asyncio.sleep(0.02)
                finished.set()

            asyncio.create_task(cleanup())

    future = runtime.submit(operation())
    assert started.wait(3)

    async def finalize():
        assert finished.is_set(), "Provider closed before background stream cleanup"

    runtime.shutdown(finalize())
    assert future.cancelled() and finished.is_set() and not runtime.is_alive()

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    release = asyncio.Event()
    entered = threading.Event()
    cleanup_calls = []

    async def resist_cancel():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    async def finalize_retry():
        cleanup_calls.append(asyncio.get_running_loop())

    runtime.submit(resist_cancel())
    assert entered.wait(3)
    try:
        runtime.shutdown(finalize_retry(), timeout=0.02)
    except TimeoutError:
        assert runtime.is_alive() and runtime.state == "closing"
        assert not cleanup_calls
    else:
        raise AssertionError("Shutdown closed clients before cancellation finished")
    runtime.loop.call_soon_threadsafe(release.set)
    owner = runtime.loop
    runtime.shutdown(finalize_retry())
    assert cleanup_calls == [owner] and not runtime.is_alive()

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    completed = threading.Event()

    async def interrupted_finalizer():
        await asyncio.sleep(0.02)
        completed.set()

    with patch.object(
        concurrent.futures.Future, "result", side_effect=KeyboardInterrupt
    ):
        try:
            runtime.shutdown(interrupted_finalizer())
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("Caller interruption was hidden")
    runtime._thread.join(3)
    assert completed.is_set() and not runtime.is_alive()


def local_keepalive():
    import httpx2
    from openai import AsyncOpenAI

    connections = []
    failures = []
    unraisables = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            connections.append(self.client_address)
            chunk = {
                "id": "local-stream",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "local",
                "choices": [
                    {"index": 0, "delta": {"content": "hello"}, "finish_reason": None}
                ],
            }
            body = ("data: " + json.dumps(chunk) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    class Client(httpx2.AsyncClient):
        owner = None
        close_count = 0

        async def aclose(self):
            assert asyncio.get_running_loop() is self.owner, "Wrong-loop close"
            self.close_count += 1
            assert self.close_count == 1, "Duplicate client close"
            await super().aclose()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    runtime = InteractiveAsyncRuntime()
    runtime.start()
    old_hook = sys.unraisablehook
    sys.unraisablehook = unraisables.append
    sdk = None
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ResourceWarning)

            async def create():
                loop = asyncio.get_running_loop()
                loop.set_exception_handler(lambda loop, data: failures.append(data))
                transport = Client(trust_env=False)
                transport.owner = loop
                sdk = AsyncOpenAI(
                    api_key="local-only",
                    max_retries=0,
                    http_client=transport,
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                )
                return sdk, transport

            sdk, transport = runtime.run(create())

            async def turn(sdk=sdk, owner=runtime.loop):
                assert asyncio.get_running_loop() is owner
                stream = await sdk.chat.completions.create(
                    model="local",
                    messages=[{"role": "user", "content": "hello"}],
                    stream=True,
                )
                async with stream:
                    return "".join(
                        [chunk.choices[0].delta.content or "" async for chunk in stream]
                    )

            assert runtime.run(turn()) == "hello"
            assert runtime.run(turn()) == "hello"
            assert len(connections) == 2 and connections[0] == connections[1], (
                connections
            )
            runtime.shutdown(sdk.close())
            assert transport.close_count == 1 and transport.is_closed
            sdk = transport = None
            runtime = InteractiveAsyncRuntime()
            runtime.start()

            async def create_default():
                asyncio.get_running_loop().set_exception_handler(
                    lambda loop, data: failures.append(data)
                )
                return AsyncOpenAI(
                    api_key="local-only",
                    max_retries=0,
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                )

            with patch.dict(
                "os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            ):
                sdk = runtime.run(create_default())
                assert runtime.run(turn(sdk=sdk, owner=runtime.loop)) == "hello"
                assert runtime.run(turn(sdk=sdk, owner=runtime.loop)) == "hello"
                assert len(connections) == 4 and connections[2] == connections[3], (
                    connections
                )
                runtime.shutdown(sdk.close())
                assert sdk.is_closed()
            sdk = turn = None
            gc.collect()
            assert not failures, failures
            assert not unraisables, unraisables
            assert not [
                w for w in captured if issubclass(w.category, ResourceWarning)
            ], captured
    finally:
        if runtime.state == "running":
            runtime.shutdown(sdk.close() if sdk is not None else None)
        sys.unraisablehook = old_hook
        server.shutdown()
        server.server_close()
        thread.join(3)
    assert not runtime.is_alive() and not thread.is_alive()


def owned_clients():
    from AgentCrew.modules.agents.local_agent import LocalAgent
    from AgentCrew.modules.llm.service_manager import ServiceManager
    from AgentCrew.setup import ApplicationSetup

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    loops = []

    class Provider:
        count = 0

        async def close(self):
            loops.append(asyncio.get_running_loop())
            self.count += 1

    base, analysis, agent_client, template = (Provider() for _ in range(4))
    manager = ServiceManager()
    manager.services = {"template": template}
    setup = ApplicationSetup.__new__(ApplicationSetup)
    setup._standalone_llm_services = [base, analysis, base, agent_client]
    setup.services = {}
    first, second = LocalAgent.__new__(LocalAgent), LocalAgent.__new__(LocalAgent)
    first.llm, second.llm = agent_client, agent_client
    setup.agent_manager = SimpleNamespace(agents={"first": first, "second": second})

    async def cleanup():
        await setup._close_dedicated_llm_services()
        await setup._close_app_owned_standalone_llms()
        await setup._close_app_owned_standalone_llms()

    owner = runtime.loop
    with patch.object(ServiceManager, "get_instance", return_value=manager):
        runtime.shutdown(cleanup())
    assert [base.count, analysis.count, agent_client.count, template.count] == [
        1,
        1,
        1,
        0,
    ]
    assert loops == [owner] * 3


def console_paths():
    from AgentCrew.modules.chat.message.handler import MessageHandler
    from AgentCrew.modules.console.console_ui import ConsoleUI
    from AgentCrew.modules.events.event_bus import EventBus
    from AgentCrew.modules.llm.token_usage import TokenUsage

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    owner = runtime.loop
    ui = ConsoleUI.__new__(ConsoleUI)
    ui.runtime, ui._turn_lock, ui._closing = runtime, asyncio.Lock(), threading.Event()
    ui.message_handler = Mock()
    handler = ui.message_handler
    handler.agent.history = [{"role": "user", "content": "test"}]
    events = []

    async def input_message(text):
        assert asyncio.get_running_loop() is owner
        events.append(("input", text))
        await asyncio.sleep(0.01)
        return False, False

    async def response():
        assert asyncio.get_running_loop() is owner
        events.append(("response", None))
        await asyncio.sleep(0)
        return "answer", TokenUsage()

    handler.process_user_input = input_message
    handler.get_assistant_response = response
    ui.console = Mock()
    ui.display_handlers = Mock()
    ui.ui_effects = Mock()
    ui.input_handler = Mock()
    ui.input_handler._input_queue = queue.Queue()
    ui._token_usage = TokenUsage()
    ui.session_cost, ui._total_cost = 0, 0
    for name in (
        "_set_terminal_title",
        "print_logo",
        "print_welcome_message",
        "_register_subscriptions",
        "start_loading_animation",
        "stop_loading_animation",
        "_calculate_token_usage",
        "display_token_usage",
    ):
        setattr(ui, name, Mock())
    ui.get_user_input = Mock(side_effect=["one", "two", "/exit"])
    try:
        with patch("signal.getsignal", return_value=lambda *args: None):
            try:
                ui.start()
            except SystemExit as exc:
                assert exc.code == 0
        assert events == [
            ("input", "one"),
            ("response", None),
            ("input", "two"),
            ("response", None),
        ]
        ui._closing.clear()
        ui._voice_auto_inflight = True
        voice = threading.Thread(
            target=ui._process_voice_activation, args=("voice",), daemon=True
        )
        voice.start()
        typed = threading.Thread(
            target=lambda: ui.run_exclusive(lambda: ui._process_turn("typed"))
        )
        typed.start()
        voice.join(3)
        typed.join(3)
        assert not voice.is_alive() and not typed.is_alive()
        assert [item[0] for item in events[-4:]] == [
            "input",
            "response",
            "input",
            "response",
        ]
        assert {events[-4][1], events[-2][1]} == {"voice", "typed"}

        async def submit_review(action, summary=None):
            assert asyncio.get_running_loop() is owner
            events.append(("review", action))

        handler.submit_pending_evolution_review = submit_review
        bus = EventBus()
        for choice in ("accept", "edit", "decline"):
            ui.input_handler.get_choice_input.return_value = choice
            ui.input_handler.get_prompt_input.return_value = "edited"
            subscription = bus.on("evolution", ui._on_evolution_summary)
            ui.run_exclusive(
                lambda: bus.emit("evolution", user_editable_summary="original")
            )
            bus.off(subscription)
        assert [event[1] for event in events if event[0] == "review"] == [
            "accept",
            "edit",
            "decline",
        ]

        async def load(conversation_id):
            assert asyncio.get_running_loop() is owner
            return [conversation_id]

        actual = MessageHandler.__new__(MessageHandler)
        actual.tool_manager = Mock()
        actual.conversation_manager = SimpleNamespace(load_conversation_async=load)
        ui.message_handler = actual
        assert ui.load_conversation("history") == ["history"]
        actual.tool_manager.reset_approved_tools.assert_called_once_with()
    finally:
        runtime.shutdown()


def app_failure_paths():
    from AgentCrew.app import AgentCrewApplication
    from AgentCrew.modules.config.global_config import GlobalConfig
    from AgentCrew.modules.mcpclient import MCPSessionManager

    for stage in ("normal", "plugin", "setup"):
        fail_startup = stage == "plugin"
        app = AgentCrewApplication.__new__(AgentCrewApplication)
        app.setup = Mock()
        setup = app.setup
        setup.resolve_runtime_model.return_value = SimpleNamespace(provider="local")
        setup.setup_services.return_value = {"memory": None, "context_persistent": None}
        if stage == "setup":
            setup.setup_services.side_effect = ValueError("setup failure")
        order = []

        async def activate(order=order, fail_startup=fail_startup):
            order.append(("activate", asyncio.get_running_loop()))
            if fail_startup:
                raise ValueError("plugin activation failure")

        async def deactivate(order=order):
            order.append(("shutdown", asyncio.get_running_loop()))

        setup.initialize_plugins, setup.shutdown = activate, deactivate
        with (
            patch("AgentCrew.modules.console.ConsoleUI") as console,
            patch("AgentCrew.modules.chat.MessageHandler"),
            patch.object(GlobalConfig, "read", return_value={}),
            patch.object(MCPSessionManager, "get_instance") as mcp,
            patch("AgentCrew.app.logger.exception") as log,
            patch("click.echo"),
        ):
            app.run_console(provider="local")
        expected = ["shutdown"] if stage == "setup" else ["activate", "shutdown"]
        assert [name for name, loop in order] == expected
        assert len({id(loop) for name, loop in order}) == 1
        assert log.call_count == (stage != "normal")
        assert not app.interactive_runtime.is_alive()
        assert setup.setup_services.call_args.kwargs["interactive"]
        if stage != "setup":
            assert console.call_args.kwargs["runtime"] is app.interactive_runtime
        mcp.return_value.cleanup.assert_called_once_with()


def onboarding_path():
    from AgentCrew.modules.onboarding.service import OnboardingService

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    seen = []
    service = OnboardingService.__new__(OnboardingService)
    service._run_async = runtime.run
    service.llm_service = SimpleNamespace(provider_name="local", model="local")
    service._print_status = Mock()
    service._print_success = Mock()
    service._save_agent = Mock(return_value=True)

    async def generate(name, description):
        seen.append(asyncio.get_running_loop())
        return SimpleNamespace(toml_definition="local", error=None, last_response=None)

    service._run_onboarding_chat = generate
    try:
        with patch("sys.stdin.isatty", return_value=True):
            assert service.create_agent("local", "local description")
        assert seen == [runtime.loop]
    finally:
        runtime.shutdown()


def xai_streams():
    from AgentCrew.modules.xai.response_service import XAISubscriptionResponseService

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    calls = []
    closed = []
    service = XAISubscriptionResponseService.__new__(XAISubscriptionResponseService)
    service._client_loop = None
    service._model = "local"
    service.reasoning_effort = None
    service.system_prompt = "local"
    service.tools = []
    service._model_definition = lambda model: SimpleNamespace(capabilities=[])
    service._convert_internal_format = lambda messages, **kwargs: messages

    async def token():
        pass

    class Stream:
        def __init__(self):
            self.events = iter([SimpleNamespace(type="response.completed")])

        def __aiter__(self):
            return self

        async def __anext__(self):
            event = next(self.events, None)
            if event is None:
                raise StopAsyncIteration
            return event

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def create(**kwargs):
        calls.append(asyncio.get_running_loop())
        return Stream()

    async def close():
        closed.append(asyncio.get_running_loop())

    service._ensure_valid_token = token
    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=close)
    service.client = client

    async def turn():
        async with await service.stream_assistant_response([]) as stream:
            return [event.type async for event in stream]

    try:
        assert runtime.run(turn()) == ["response.completed"]
        assert runtime.run(turn()) == ["response.completed"]
        other = InteractiveAsyncRuntime()
        other.start()
        try:
            try:
                other.run(turn())
            except RuntimeError as error:
                assert "different asyncio event loop" in str(error)
            else:
                raise AssertionError("xAI silently replaced a cross-loop client")
        finally:
            other.shutdown()
        assert service.client is client and calls == [runtime.loop] * 2
        owner = runtime.loop
        runtime.shutdown(service.close())
        assert closed == [owner]
    finally:
        if runtime.state == "running":
            runtime.shutdown(service.close())


def session_mutations():
    from AgentCrew.modules.chat.message.commands.model_commands import ModelCommands
    from AgentCrew.modules.config.agents_config import AgentsConfig, AgentsFileConfig
    from AgentCrew.modules.config.global_config import GlobalConfig
    from AgentCrew.modules.console.command_handlers import CommandHandlers
    from AgentCrew.modules.console.console_ui import ConsoleUI
    from AgentCrew.modules.events import AppEvents
    from AgentCrew.modules.events.event_bus import EventBus
    from AgentCrew.modules.llm.model_registry import ModelRegistry
    from AgentCrew.modules.llm.service_manager import ServiceManager

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    ui = ConsoleUI.__new__(ConsoleUI)
    ui.runtime, ui._turn_lock, ui._closing = runtime, asyncio.Lock(), threading.Event()
    commands = CommandHandlers.__new__(CommandHandlers)
    commands._console_ui, commands.console, commands.open_file_in_editor = (
        ui,
        Mock(),
        Mock(),
    )
    loops = []

    async def reload_config(*args):
        loops.append(asyncio.get_running_loop())

    try:
        with patch.object(AgentsConfig, "reload_async", reload_config):
            commands.handle_edit_agent_command()
            commands.handle_edit_mcp_command()
            commands.handle_edit_config_command()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "agents.json"
            source.write_text(
                json.dumps({"agents": [{"name": "local", "description": "local"}]})
            )
            with (
                patch.object(AgentsConfig, "read", return_value=AgentsFileConfig()),
                patch.object(AgentsConfig, "write_async", reload_config),
            ):
                commands.handle_import_agent_command(str(source))
        assert loops == [runtime.loop] * 4
        bus = EventBus()
        changes = []
        bus.on(AppEvents.MODEL_CHANGED, lambda **data: changes.append(data["id"]))
        template = object()

        async def change_model(service, selection):
            assert service is template and selection.model_id == "local/new"
            loops.append(asyncio.get_running_loop())

        manager, registry = Mock(), Mock()
        manager.get_service_for_model.return_value = template
        registry.get_current_model.return_value = SimpleNamespace(
            id="new", name="new", provider="local"
        )
        handler = SimpleNamespace(
            bus=bus,
            agent_manager=SimpleNamespace(update_llm_service_async=change_model),
        )
        with (
            patch.object(ModelRegistry, "get_instance", return_value=registry),
            patch.object(ServiceManager, "get_instance", return_value=manager),
            patch.object(GlobalConfig, "set_last_used_model"),
        ):
            result = ui.run_exclusive(
                lambda: ModelCommands(handler).handle_model("/model local/new")
            )
        assert result == (False, True) and changes == ["new"]
        assert loops == [runtime.loop] * 5
    finally:
        runtime.shutdown()


def plugin_rollback():
    from AgentCrew.setup import ApplicationSetup

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    setup = ApplicationSetup.__new__(ApplicationSetup)
    setup._plugins_initialized = False
    loops = []

    async def load():
        loops.append(asyncio.get_running_loop())
        raise ValueError("partial plugin activation")

    async def unload():
        loops.append(asyncio.get_running_loop())

    setup.plugin_manager = SimpleNamespace(load_all=load, unload_all=unload)
    owner = runtime.loop
    try:
        runtime.run(setup.initialize_plugins())
    except ValueError:
        assert not setup._plugins_initialized
    else:
        raise AssertionError("Plugin activation error was hidden")
    finally:
        runtime.shutdown(setup.shutdown_plugins())
    assert loops == [owner] * 3 and not runtime.is_alive()


def cleanup_failure():
    from AgentCrew.modules.llm.service_manager import ServiceManager
    from AgentCrew.setup import ApplicationSetup

    runtime = InteractiveAsyncRuntime()
    runtime.start()
    closed = []

    async def close():
        closed.append(asyncio.get_running_loop())

    async def unload():
        pass

    def summary_close():
        raise ValueError("summary cleanup failure")

    client = SimpleNamespace(close=close)
    manager = Mock()
    pending = []

    def schedule(service):
        pending.append(asyncio.create_task(service.close()))

    async def drain():
        await asyncio.gather(*pending)

    manager.close_service, manager.drain_pending_closes = schedule, drain
    setup = ApplicationSetup.__new__(ApplicationSetup)
    setup.agent_manager = None
    setup.shutdown_plugins = unload
    setup.services = {"tool_result_summary": SimpleNamespace(close=summary_close)}
    setup._standalone_llm_services = [client]
    owner = runtime.loop
    with patch.object(ServiceManager, "get_instance", return_value=manager):
        try:
            runtime.shutdown(setup.shutdown())
        except ValueError as error:
            assert str(error) == "summary cleanup failure"
        else:
            raise AssertionError("Cleanup failure was hidden")
    assert closed == [owner] and not runtime.is_alive()


def main():
    checks = [
        runtime_mechanics,
        shutdown_paths,
        local_keepalive,
        owned_clients,
        console_paths,
        app_failure_paths,
        onboarding_path,
        xai_streams,
        session_mutations,
        plugin_rollback,
        cleanup_failure,
    ]
    for check in checks:
        check()
        print(f"{check.__name__}: PASS")
    print(f"interactive lifecycle: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
