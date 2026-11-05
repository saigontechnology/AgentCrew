"""Credential-free checks for the shared Runner-owned host lifecycle."""

from __future__ import annotations

import asyncio
import gc
import importlib
import json
import sys
import threading
import types
import warnings
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AgentCrewApplication = importlib.import_module("AgentCrew.app").AgentCrewApplication
ApplicationSetup = importlib.import_module("AgentCrew.setup").ApplicationSetup


class LoopProvider:
    def __init__(self, name):
        self.name = name
        self.model = "local"
        self.provider_name = "openai"
        self.owner = None
        self.requests = 0
        self.close_count = 0

    async def request(self):
        loop = asyncio.get_running_loop()
        self.owner = self.owner or loop
        assert loop is self.owner
        assert not self.close_count
        self.requests += 1
        await asyncio.sleep(0)
        return self.name

    async def close(self):
        loop = asyncio.get_running_loop()
        assert self.owner is None or self.owner is loop
        assert not loop.is_closed()
        self.owner = loop
        self.close_count += 1
        assert self.close_count == 1


def application(setup):
    app = AgentCrewApplication.__new__(AgentCrewApplication)
    app.setup = setup
    return app


def helper_lifecycle():
    for failure_stage in (None, "configure", "operation", "cancel", "shutdown"):
        _helper_lifecycle_case(failure_stage)


def _helper_lifecycle_case(failure_stage):
    provider = LoopProvider("primary")
    events = []
    failures = []
    owner = None
    expected_error = ValueError(failure_stage)

    async def shutdown():
        assert asyncio.get_running_loop() is owner
        events.append("provider")
        await provider.close()
        if failure_stage == "shutdown":
            raise expected_error

    async def request():
        try:
            await asyncio.gather(provider.request(), provider.request())
            if failure_stage == "operation":
                raise expected_error
            if failure_stage == "cancel":
                raise asyncio.CancelledError
        finally:
            events.append("operation-unwound")

    def manager_cleanup():
        assert owner.is_closed()
        events.append("manager")

    app = application(SimpleNamespace(shutdown=shutdown))
    with patch(
        "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
        return_value=SimpleNamespace(cleanup=manager_cleanup),
    ):
        try:
            with app._host_lifecycle() as runner:
                owner = runner.get_loop()
                owner.set_exception_handler(
                    lambda loop, context: failures.append(context)
                )
                runner.run(provider.request())
                if failure_stage == "configure":
                    raise expected_error
                runner.run(request())
        except asyncio.CancelledError:
            assert failure_stage == "cancel"
        except ValueError as error:
            assert error is expected_error
        else:
            assert failure_stage is None
    assert owner.is_closed()
    assert provider.close_count == 1
    assert events[-2:] == ["provider", "manager"]
    if failure_stage != "configure":
        assert events[0] == "operation-unwound"
    assert not failures, failures


def drain_and_failures():
    provider = LoopProvider("primary")
    events = []
    operation_error = ValueError("operation failure")

    async def late_cleanup():
        await asyncio.sleep(0.01)
        events.append("late-cleanup")

    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            asyncio.create_task(late_cleanup())
            events.append("cancelled")
            raise RuntimeError("request cleanup failure")

    async def start():
        await provider.request()
        asyncio.create_task(background())
        await asyncio.sleep(0)

    async def shutdown():
        assert events == ["cancelled", "late-cleanup"]
        await provider.close()
        events.append("provider")
        raise RuntimeError("provider shutdown failure")

    with (
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance") as manager,
        patch("AgentCrew.app.logger.exception") as logged,
    ):
        app = application(SimpleNamespace(shutdown=shutdown))
        try:
            with app._host_lifecycle() as runner:
                owner = runner.get_loop()
                runner.run(start())
                raise operation_error
        except ValueError as error:
            assert error is operation_error
        else:
            raise AssertionError("Operation failure was hidden")
        logged.assert_called_once()
        manager.return_value.cleanup.assert_called_once()
    assert owner.is_closed()
    assert provider.close_count == 1
    assert events[-1] == "provider"

    async def failing_shutdown():
        raise RuntimeError("shutdown failure")

    with patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance") as manager:
        app = application(SimpleNamespace(shutdown=failing_shutdown))
        try:
            with app._host_lifecycle() as runner:
                owner = runner.get_loop()
        except RuntimeError as error:
            assert str(error) == "shutdown failure"
        else:
            raise AssertionError("Shutdown failure was hidden")
        assert owner.is_closed()
        manager.return_value.cleanup.assert_called_once()


def stub_module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


def setup_ownership():
    for interactive, fail_startup in ((False, False), (True, False), (False, True)):
        _setup_ownership_case(interactive, fail_startup)


def _setup_ownership_case(interactive, fail_startup):
    from AgentCrew.modules.llm.model_selection import (
        ModelSelection,
        ModelSelectionSource,
        RuntimeModelInput,
    )
    from AgentCrew.modules.llm.service_manager import ServiceManager

    primary = LoopProvider("primary")
    memory = LoopProvider("memory")
    summary = LoopProvider("summary")
    analysis = LoopProvider("analysis")
    template = LoopProvider("template")
    manager = ServiceManager.__new__(ServiceManager)
    manager.services = {"cached": template}
    manager._closed_services = set()
    manager._pending_closes = set()
    manager.get_service_for_selection = Mock(return_value=primary)
    manager.initialize_standalone_service = Mock(
        side_effect=[memory, summary, analysis]
    )
    config = Mock()
    config.get_last_used_model.return_value = None
    config.get_last_used_provider.return_value = None
    config.read.return_value = {"auto_context_shrink": True}
    registry = Mock()
    registry.get_current_model.return_value = None
    registry.get_model.return_value = None
    agents = SimpleNamespace(
        agents={},
        close_all_remote_agents=AsyncMock(),
        close_all_cursor_agents=AsyncMock(),
    )
    owner = None

    def close_summary():
        with asyncio.Runner() as worker:
            worker.run(summary.request())
            worker.run(summary.close())
        assert summary.owner is not owner
        return True

    summary_service = SimpleNamespace(close=close_summary)
    clipboard = Mock()
    if fail_startup:
        clipboard.side_effect = ValueError("startup after owned client creation")
    stubs = {
        "AgentCrew.modules.memory.chroma_service": stub_module(
            "AgentCrew.modules.memory.chroma_service", ChromaMemoryService=Mock()
        ),
        "AgentCrew.modules.memory.context_persistent": stub_module(
            "AgentCrew.modules.memory.context_persistent",
            ContextPersistenceService=Mock(),
        ),
    }
    optional = (
        ("clipboard", "ClipboardService", clipboard),
        ("web_search", "TavilySearchService", Mock()),
        ("code_analysis", "CodeAnalysisService", Mock()),
        ("browser_automation", "BrowserAutomationService", Mock()),
        ("file_editing", "FileEditingService", Mock()),
        ("command_execution", "CommandExecutionService", Mock()),
        ("skills", "SkillsService", Mock()),
        ("image_generation.gemini_service", "GeminiImageGenerationService", Mock()),
    )
    for suffix, name, constructor in optional:
        module_name = f"AgentCrew.modules.{suffix}"
        stubs[module_name] = stub_module(module_name, **{name: constructor})
    selection = ModelSelection(
        source=ModelSelectionSource.DEFAULT,
        provider="openai",
        model_id="local",
    )
    setup = ApplicationSetup.__new__(ApplicationSetup)
    setup.services = None
    setup.agent_manager = None
    setup._standalone_llm_services = []
    setup.shutdown_plugins = AsyncMock()
    app = application(setup)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, stubs))
        stack.enter_context(patch("AgentCrew.setup.GlobalConfig", return_value=config))
        stack.enter_context(
            patch("AgentCrew.setup.resolve_model_selection", return_value=selection)
        )
        stack.enter_context(
            patch("AgentCrew.setup.default_reasoning_for_service", return_value=None)
        )
        stack.enter_context(patch("AgentCrew.setup.apply_reasoning_to_service"))
        stack.enter_context(
            patch(
                "AgentCrew.modules.llm.service_manager.ServiceManager.get_instance",
                return_value=manager,
            )
        )
        stack.enter_context(
            patch(
                "AgentCrew.modules.llm.model_registry.ModelRegistry.get_instance",
                return_value=registry,
            )
        )
        stack.enter_context(
            patch(
                "AgentCrew.modules.agents.AgentManager.get_instance",
                return_value=agents,
            )
        )
        stack.enter_context(
            patch(
                "AgentCrew.modules.agents.tool_result_summary.ToolResultSummaryService",
                return_value=summary_service,
            )
        )
        stack.enter_context(
            patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance")
        )
        try:
            with app._host_lifecycle() as runner:
                owner = runner.get_loop()
                services = setup.setup_services(
                    RuntimeModelInput(
                        provider="openai",
                        explicit_provider=True,
                        explicit_model_id=None,
                        detected_model_id=None,
                    ),
                    interactive=interactive,
                    application_owned_llms=not interactive,
                    run_async=runner.run,
                )
                assert setup._run_async == runner.run
                runner.run(primary.request())
                runner.run(analysis.request())
                assert services["llm"] is primary
                setup._track_standalone_llm(primary)
        except ValueError as error:
            assert fail_startup and str(error) == "startup after owned client creation"
        else:
            assert not fail_startup
        assert manager.get_service_for_selection.call_args.kwargs["standalone"] is True
        assert owner.is_closed()
        assert primary.owner is owner and primary.close_count == 1
        assert analysis.close_count == int(not fail_startup)
        assert summary.close_count == 1
        assert template.close_count == memory.close_count == 0


def local_keepalive():
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
                "id": "local",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "local",
                "choices": [
                    {"index": 0, "delta": {"content": "hello"}, "finish_reason": None}
                ],
            }
            body = f"data: {json.dumps(chunk)}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    previous_hook = sys.unraisablehook
    sys.unraisablehook = unraisables.append
    sdk = None
    client_close = None
    try:
        with (
            warnings.catch_warnings(record=True) as caught,
            patch.dict(
                "os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            ),
            patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance"),
        ):
            warnings.simplefilter("always", ResourceWarning)
            sdk = AsyncOpenAI(
                api_key="local-only",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                max_retries=0,
            )
            client_close = AsyncMock(wraps=sdk._client.aclose)
            owner = None

            async def turn():
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

            async def shutdown():
                assert asyncio.get_running_loop() is owner
                await sdk.close()

            app = application(SimpleNamespace(shutdown=shutdown))
            with patch.object(sdk._client, "aclose", client_close):
                with app._host_lifecycle() as runner:
                    owner = runner.get_loop()
                    owner.set_exception_handler(
                        lambda loop, context: failures.append(context)
                    )
                    assert runner.run(turn()) == "hello"
                    assert runner.run(turn()) == "hello"
                assert owner.is_closed() and sdk.is_closed()
                client_close.assert_awaited_once()
            assert len(connections) == 2 and connections[0] == connections[1]
            sdk = None
            client_close = None
            gc.collect()
            assert not failures, failures
            assert not unraisables, unraisables
            assert not [
                item for item in caught if issubclass(item.category, ResourceWarning)
            ]
    finally:
        leaked = sdk is not None and not sdk.is_closed()
        try:
            server.shutdown()
            server.server_close()
            thread.join(3)
        finally:
            sys.unraisablehook = previous_hook
        assert not leaked, "Harness left an SDK client open"
    assert not thread.is_alive()


def main():
    checks = (helper_lifecycle, drain_and_failures, setup_ownership, local_keepalive)
    for check in checks:
        check()
        print(f"{check.__name__}: PASS")
    print(f"host lifecycle foundation: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
