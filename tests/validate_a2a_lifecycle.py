"""Credential-free A2A lifecycle and representative v1 protocol validation."""

import asyncio
import gc
import importlib
import sys
import warnings
from inspect import isawaitable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

host_checks = importlib.import_module("validate_host_lifecycle")
server_module = importlib.import_module("AgentCrew.modules.a2a.server")


def agent_manager(provider):
    from AgentCrew.modules.agents.local_agent import LocalAgent

    agents = {}
    for name in ("alpha", "beta"):
        agent = LocalAgent.__new__(LocalAgent)
        agent.name = name
        agent.description = name
        agent.tool_definitions = {"echo": {"name": "echo", "description": "Echo"}}
        agent.llm = provider
        agents[name] = agent
    return SimpleNamespace(agents=agents, get_agent=agents.get)


def observe_closes(server, events):
    for label, resources, method in (
        ("handler", server._handlers, "aclose"),
        ("session", server._session_stores, "close"),
        ("task", server._task_stores, "close"),
    ):
        for index, resource in enumerate(resources):
            original = getattr(resource, method, lambda: None)
            key = f"{label}:{index}"

            async def close(original=original, key=key):
                result = original()
                if isawaitable(result):
                    await result
                events.append((key, asyncio.get_running_loop()))

            setattr(resource, method, close)


def assert_cleanup(events, owner):
    names = [name for name, _ in events]
    assert len(names) == len(set(names)), names
    assert all(loop is owner for _, loop in events)
    assert names[-1] == "provider", names
    assert all(name.startswith("handler") for name in names[:2]), names
    assert any(name.startswith("session") for name in names)
    assert any(name.startswith("task") for name in names)


def protocol_and_lifespan(failures):
    import httpx2
    from a2a.server.context import ServerCallContext
    from a2a.types.a2a_pb2 import (
        Artifact,
        Part,
        Task,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TaskStatusUpdateEvent,
    )

    provider = host_checks.LoopProvider("a2a")
    events = []
    active = {}
    blocked = []
    cancelled = []
    task_states = {}

    class Executor:
        def __init__(self, agent, session_store):
            self.agent = agent
            self.session_store = session_store

        async def execute(self, context, event_queue):
            task_id = context.task_id
            context_id = context.context_id
            text = "".join(part.text for part in context.message.parts)
            cancel_event = asyncio.Event()
            active[task_id] = cancel_event
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            try:
                await provider.request()
                await asyncio.sleep(0.01)
                if text == "block":
                    blocked.append(task_id)
                    await cancel_event.wait()
                    state = TaskState.TASK_STATE_CANCELED
                elif text == "ask":
                    await self.session_store.save_pending_tools(
                        task_id,
                        {"id": "ask", "name": "ask", "input": {}},
                        [],
                        agent_namespace=self.agent.name,
                    )
                    state = TaskState.TASK_STATE_INPUT_REQUIRED
                else:
                    await self.session_store.clear_pending_tools(
                        task_id, agent_namespace=self.agent.name
                    )
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            task_id=task_id,
                            context_id=context_id,
                            artifact=Artifact(
                                artifact_id="answer", parts=[Part(text=text)]
                            ),
                            append=False,
                            last_chunk=True,
                        )
                    )
                    state = TaskState.TASK_STATE_COMPLETED
                task_states[task_id] = state
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(state=state),
                    )
                )
            except asyncio.CancelledError:
                cancelled.append(task_id)
                raise
            finally:
                active.pop(task_id, None)

        async def cancel(self, context, event_queue):
            active[context.task_id].set()

    server = None

    async def shutdown():
        assert server is not None and not active
        assert not server._handlers and not server._session_stores
        assert not server._task_stores
        await provider.close()
        events.append(("provider", asyncio.get_running_loop()))

    app = host_checks.application(SimpleNamespace(shutdown=shutdown))
    with (
        patch.object(server_module, "AgentCrewA2AExecutor", Executor),
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance"),
        app._host_lifecycle() as runner,
    ):
        owner = runner.get_loop()
        owner.set_exception_handler(lambda loop, context: failures.append(context))
        server = server_module.A2AServer(agent_manager(provider), api_key="local-key")
        observe_closes(server, events)
        task_store = server._task_stores[0]
        store_context = ServerCallContext()
        runner.run(
            task_store.save(
                Task(
                    id="stale",
                    context_id="stale-context",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                ),
                store_context,
            )
        )

        async def exercise():
            async with server.app.router.lifespan_context(server.app):
                stale = await task_store.get("stale", store_context)
                assert stale.status.state == TaskState.TASK_STATE_FAILED
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=server.app),
                    base_url="http://local",
                    trust_env=False,
                    timeout=3,
                ) as client:
                    unauthorized = await client.post("/alpha/", json={})
                    assert unauthorized.status_code == 401
                    client.headers["Authorization"] = "Bearer local-key"
                    client.headers["A2A-Version"] = "1.0"

                    async def send(text, task_id="", context_id="", immediate=False):
                        response = await client.post(
                            "/alpha/",
                            json={
                                "jsonrpc": "2.0",
                                "id": text,
                                "method": "SendMessage",
                                "params": {
                                    "message": {
                                        "messageId": text,
                                        "role": "ROLE_USER",
                                        "parts": [{"text": text}],
                                        "taskId": task_id,
                                        "contextId": context_id,
                                    },
                                    "configuration": {"returnImmediately": immediate},
                                },
                            },
                        )
                        assert response.status_code == 200, response.text
                        result = response.json()
                        assert "error" not in result, result
                        return result["result"]

                    first, second = await asyncio.gather(send("one"), send("two"))
                    first = first.get("task", first)
                    second = second.get("task", second)
                    assert first["id"] != second["id"]
                    assert first["status"]["state"] == "TASK_STATE_COMPLETED"
                    assert first["artifacts"][0]["parts"][0]["text"] == "one"
                    subscription = await client.post(
                        "/alpha/",
                        json={
                            "jsonrpc": "2.0",
                            "id": "subscribe",
                            "method": "SubscribeToTask",
                            "params": {"id": first["id"]},
                        },
                    )
                    assert subscription.status_code == 200, subscription.text
                    assert "TASK_STATE_COMPLETED" in subscription.text
                    asked = await send("ask")
                    asked = asked.get("task", asked)
                    assert asked["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
                    assert await server.session_store.get_pending_tools(
                        asked["id"], agent_namespace="alpha"
                    )
                    resumed = await send("resume", asked["id"], asked["contextId"])
                    resumed = resumed.get("task", resumed)
                    assert resumed["id"] == asked["id"]
                    assert resumed["contextId"] == asked["contextId"]
                    assert resumed["status"]["state"] == "TASK_STATE_COMPLETED"
                    assert not await server.session_store.get_pending_tools(
                        asked["id"], agent_namespace="alpha"
                    )
                    running = await send("block", immediate=True)
                    running = running.get("task", running)
                    for _ in range(100):
                        if running["id"] in blocked:
                            break
                        await asyncio.sleep(0.001)
                    assert running["id"] in active
                assert active
            assert not active and running["id"] in cancelled
            await server.aclose()

        runner.run(asyncio.wait_for(exercise(), 10))
    assert provider.close_count == 1
    assert_cleanup(events, owner)
    assert provider.owner is owner and provider.requests == 5


def resource_failures(failures):
    provider = host_checks.LoopProvider("cleanup")
    events = []
    error = ValueError("handler cleanup failure")
    server = None

    async def shutdown():
        assert not server._task_stores and not server._session_stores
        await provider.close()
        events.append(("provider", asyncio.get_running_loop()))

    app = host_checks.application(SimpleNamespace(shutdown=shutdown))
    with (
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance"),
        app._host_lifecycle() as runner,
    ):
        owner = runner.get_loop()
        owner.set_exception_handler(lambda loop, context: failures.append(context))
        server = server_module.A2AServer(agent_manager(provider), api_key="local-key")
        observe_closes(server, events)
        original = server._handlers[0].aclose

        async def failed_close():
            await original()
            raise error

        server._handlers[0].aclose = failed_close
        with patch.object(server_module.logger, "opt") as logged:
            for _ in range(2):
                try:
                    runner.run(server.aclose())
                except ValueError as caught:
                    assert caught is error
                else:
                    raise AssertionError("A2A cleanup failure was hidden")
            logged.assert_called_once_with(exception=error)
            logged.return_value.error.assert_called_once()
        plain = server_module.A2AServer(agent_manager(provider), api_key="local-key")
        runner.run(plain.aclose())
        runner.run(plain.aclose())
        assert not plain._task_stores and not plain._handlers
    assert_cleanup(events, owner)


def live_serve_cancellation(failures):
    for during_startup in (False, True):
        _live_serve_cancellation_case(failures, during_startup)


def _live_serve_cancellation_case(failures, during_startup):
    import uvicorn

    provider = host_checks.LoopProvider("live")
    events = []
    server = None
    captured = []
    real_server = uvicorn.Server
    real_config = uvicorn.Config

    def configure(*args, **kwargs):
        kwargs.update(log_level="error", log_config=None, timeout_graceful_shutdown=1)
        return real_config(*args, **kwargs)

    def construct(config):
        instance = real_server(config)
        captured.append(instance)
        return instance

    async def shutdown():
        assert not server._handlers and not server._session_stores
        assert captured[0].lifespan.shutdown_event.is_set()
        assert all(sock.fileno() == -1 for sock in sockets)
        assert not any(listener.is_serving() for listener in captured[0].servers)
        await provider.close()
        events.append(("provider", asyncio.get_running_loop()))

    sockets = []
    app = host_checks.application(SimpleNamespace(shutdown=shutdown))
    with (
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance"),
        patch.object(uvicorn, "Config", side_effect=configure),
        patch.object(uvicorn, "Server", side_effect=construct),
        app._host_lifecycle() as runner,
    ):
        owner = runner.get_loop()
        owner.set_exception_handler(lambda loop, context: failures.append(context))
        server = server_module.A2AServer(
            agent_manager(provider), host="127.0.0.1", port=0, api_key="local-key"
        )
        observe_closes(server, events)

        async def exercise():
            entered = asyncio.Event()
            release = asyncio.Event()
            reconcile = server._reconcile_stale_tasks

            async def reconcile_after_release():
                entered.set()
                await release.wait()
                await reconcile()

            server._reconcile_stale_tasks = reconcile_after_release
            if not during_startup:
                release.set()
            serving = asyncio.create_task(server.serve())
            await asyncio.wait_for(entered.wait(), 3)
            if during_startup:
                assert not captured[0].started
                serving.cancel()
                release.set()
            else:
                for _ in range(1000):
                    if captured[0].started:
                        break
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.001)
                assert captured[0].started
                sockets.extend(
                    sock
                    for listener in captured[0].servers
                    for sock in listener.sockets
                )
                serving.cancel()
            await provider.request()
            try:
                await serving
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("A2A cancellation was hidden")
            await server.aclose()

        runner.run(asyncio.wait_for(exercise(), 10))
    assert_cleanup(events, owner)


def live_bind_failure(failures):
    import socket

    import uvicorn

    provider = host_checks.LoopProvider("bind-failure")
    events = []

    async def shutdown():
        await provider.close()
        events.append(("provider", asyncio.get_running_loop()))

    app = host_checks.application(SimpleNamespace(shutdown=shutdown))
    with (
        socket.socket() as occupied,
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance"),
    ):
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        with app._host_lifecycle() as runner:
            owner = runner.get_loop()
            owner.set_exception_handler(lambda loop, context: failures.append(context))
            runner.run(provider.request())
            server = server_module.A2AServer(
                agent_manager(provider),
                host="127.0.0.1",
                port=port,
                api_key="local-key",
            )
            observe_closes(server, events)
            config = uvicorn.Config(
                server.app,
                host="127.0.0.1",
                port=port,
                lifespan="on",
                log_config=None,
                log_level="error",
            )
            with patch("uvicorn.Config", return_value=config):
                try:
                    runner.run(server.serve())
                except SystemExit as error:
                    assert error.code == 1
                else:
                    raise AssertionError("Port conflict did not preserve SystemExit")
        assert_cleanup(events, owner)
        assert owner.is_closed() and provider.close_count == 1


def startup_failures(failures):
    for stage in ("configuration", "lifespan"):
        _startup_failure_case(stage, failures)


def _startup_failure_case(stage, failures):
    provider = host_checks.LoopProvider(stage)
    events = []
    server = None
    error = ValueError(stage)

    async def shutdown():
        assert not server._handlers and not server._task_stores
        await provider.close()
        events.append(("provider", asyncio.get_running_loop()))

    app = host_checks.application(SimpleNamespace(shutdown=shutdown))
    with (
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance"),
        app._host_lifecycle() as runner,
    ):
        owner = runner.get_loop()
        owner.set_exception_handler(lambda loop, context: failures.append(context))
        server = server_module.A2AServer(agent_manager(provider), api_key="local-key")
        observe_closes(server, events)
        if stage == "configuration":
            with patch("uvicorn.Config", side_effect=error):
                try:
                    runner.run(server.serve())
                except ValueError as caught:
                    assert caught is error
                else:
                    raise AssertionError("Server startup failure was hidden")
        else:

            async def fail():
                raise error

            async def enter():
                async with server.app.router.lifespan_context(server.app):
                    raise AssertionError("Lifespan entered after reconciliation failed")

            with patch.object(server, "_reconcile_stale_tasks", side_effect=fail):
                try:
                    runner.run(enter())
                except ValueError as caught:
                    assert caught is error
                else:
                    raise AssertionError("Reconciliation failure was hidden")
        runner.run(server.aclose())
    assert_cleanup(events, owner)


def application_entry(failures):
    for stage in (None, "setup", "constructor", "serve"):
        _application_entry_case(stage, failures)


def _application_entry_case(stage, failures):
    from unittest.mock import Mock

    provider = host_checks.LoopProvider("entry")
    manager = agent_manager(provider)
    state = {}
    error = ValueError(stage)

    async def request():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        await provider.request()

    def configure(runtime_model, **kwargs):
        assert runtime_model.provider == "openai"
        assert kwargs["application_owned_llms"] is True
        state["run_async"] = kwargs["run_async"]
        state["run_async"](request())
        if stage == "setup":
            raise error
        return {"llm": provider}

    def agents(services, config_uri, **kwargs):
        assert services["llm"] is provider
        state["run_async"](request())

    async def shutdown():
        if "server" in state:
            assert state["server"].closed
        await provider.close()

    class Server:
        def __init__(self, **kwargs):
            assert kwargs["agent_manager"] is manager
            assert kwargs["api_key"] == "local-key"
            if stage == "constructor":
                raise error
            self.closed = False
            state["server"] = self

        async def serve(self):
            try:
                await asyncio.gather(provider.request(), provider.request())
                if stage == "serve":
                    raise error
            finally:
                self.closed = True

    setup = SimpleNamespace(
        load_api_keys_from_config=Mock(),
        resolve_runtime_model=Mock(return_value=SimpleNamespace(provider="openai")),
        setup_services=Mock(side_effect=configure),
        setup_agents=Mock(side_effect=agents),
        restore_last_agent=Mock(),
        agent_manager=manager,
        shutdown=shutdown,
    )
    app = host_checks.application(setup)
    with (
        patch.object(server_module, "A2AServer", Server),
        patch("AgentCrew.app.logger.exception") as logged,
        patch("AgentCrew.app.click.echo"),
        patch("AgentCrew.modules.mcpclient.MCPSessionManager.get_instance") as mcp,
    ):
        app.run_server(provider="openai", api_key="local-key")
        assert logged.call_count == int(stage is not None), (
            stage,
            logged.call_args_list,
        )
        mcp.return_value.cleanup.assert_called_once()
    assert provider.owner.is_closed() and provider.close_count == 1
    assert provider.requests == (
        1 if stage == "setup" else 2 if stage == "constructor" else 4
    )


def main():
    checks = (
        protocol_and_lifespan,
        resource_failures,
        live_serve_cancellation,
        live_bind_failure,
        startup_failures,
        application_entry,
    )
    failures = []
    unraisables = []
    previous_hook = sys.unraisablehook
    try:
        sys.unraisablehook = unraisables.append
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", ResourceWarning)
            for check in checks:
                check(failures)
                gc.collect()
                print(f"{check.__name__}: PASS")
            assert not failures, failures
            assert not unraisables, unraisables
            assert not [
                item for item in recorded if issubclass(item.category, ResourceWarning)
            ]
    finally:
        try:
            service = sys.modules.get("AgentCrew.modules.mcpclient.service")
            if service is not None:
                service.mcp_log_io.close()
        finally:
            sys.unraisablehook = previous_hook
    print(f"A2A lifecycle: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
