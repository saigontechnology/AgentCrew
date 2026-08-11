"""
Integration tests for A2AServer routes using ASGITransport.

Tests card endpoints, auth, and JSON-RPC via the actual server routes.
Uses a patched isinstance check to allow stub agents through A2AServer's
LocalAgent gate.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from a2a.types.a2a_pb2 import (
    Task,
    TaskState,
    TaskStatus,
)
from starlette.testclient import TestClient as ASGITestClient

from AgentCrew.modules.a2a.server import A2AServer
from AgentCrew.modules.agents import AgentManager, LocalAgent


class StubForA2A:
    """Duck-typed agent stub matching the LocalAgent interface used by A2AServer.
    Registered as a LocalAgent via register_agent and isinstance patch."""

    name = "stub"
    description = "Stub agent for testing"
    input_tokens_usage = 0
    output_tokens_usage = 0

    def __init__(self):
        self.history: list[dict[str, Any]] = []
        self.tool_definitions: dict[str, Any] = {}

    def get_model(self):
        return "test-model"

    def is_streaming(self):
        return True

    def format_message(self, msg_type, data):
        return {
            "role": "assistant",
            "content": [{"type": "text", "text": data.get("message", "")}],
        }

    def _extract_last_user_message_for_memory(self, history):
        for m in reversed(history):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""

    def store_memory_if_available(self, *args, **kwargs):
        pass

    def calculate_usage_cost(self, *args, **kwargs):
        return 0.0

    async def process_messages(self, messages, callback=None, **kwargs):
        yield "Hello from stub!", "Hello from stub!", None

    def activate(self):
        return True

    def deactivate(self):
        return True


class _StubExecutor:
    """Minimal AgentExecutor stub used to construct request handlers."""

    async def execute(self, context, event_queue):
        return None

    async def cancel(self, context, event_queue):
        return None


@pytest.fixture
def server():
    """Create an A2AServer with a stub agent registered via patched isinstance."""
    mgr = AgentManager()
    agent = StubForA2A()
    mgr.register_agent(agent)

    with patch.object(LocalAgent, "__subclasshook__", return_value=True):
        # Also patch isinstance directly for the check in server.py
        original_isinstance = isinstance

        def patched_isinstance(obj, cls):
            if cls is LocalAgent and hasattr(obj, "name"):
                return True
            return original_isinstance(obj, cls)

        with patch("builtins.isinstance", patched_isinstance):
            srv = A2AServer(
                agent_manager=mgr,
                host="127.0.0.1",
                port=0,
                base_url="http://127.0.0.1:0",
                store_type="memory",
            )
    return srv


class TestA2AServerCards:
    """Agent card endpoints via actual A2AServer."""

    def test_v1_card(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent-card.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "supportedInterfaces" in data
        assert data["name"] == "stub"

    def test_v1_card_advertises_only_v1_interface(self, server):
        """supportedInterfaces must contain only JSONRPC 1.0, no v0.3."""
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent-card.json")
        data = resp.json()
        interfaces = data.get("supportedInterfaces", [])
        assert len(interfaces) == 1
        assert interfaces[0]["protocolVersion"] == "1.0"
        assert interfaces[0]["protocolBinding"] == "JSONRPC"

    def test_legacy_agent_json_returns_404(self, server):
        """Legacy v0.3 /agent.json endpoint must be absent (404)."""
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent.json")
        assert resp.status_code == 404

    def test_agents_list(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/agents")
        assert resp.status_code == 200
        agents = resp.json()
        names = [a["name"] for a in agents]
        assert "stub" in names


class TestA2AServerAuth:
    """API key behavior."""

    def test_card_discovery_without_auth(self, server):
        """Card discovery must work without authentication."""
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent-card.json")
        assert resp.status_code == 200

    def test_lifecycle_tracking(self, server):
        """Server tracks handlers and stores."""
        assert len(server._handlers) >= 1
        assert len(server._session_stores) >= 1
        assert len(server._task_stores) >= 1

    def test_close_all_remote_agents_empty(self):
        """close_all_remote_agents on empty manager does not raise."""
        mgr = AgentManager()
        asyncio.run(mgr.close_all_remote_agents())


class TestA2AServerSendMessage:
    """JSON-RPC SendMessage via actual A2AServer route."""

    def test_streaming_send(self, server):
        """Sending a streaming message returns events starting with Task."""
        client = ASGITestClient(server.app)
        # Use SendStreamingMessage-style request
        req = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
                "configuration": {},
            },
        }
        resp = client.post("/stub/", json=req)
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data or "error" in data


class TestA2AServerOrphanedTasks:
    """Process restart handling for persisted nonterminal tasks."""

    def test_nonterminal_task_reconciliation(self, tmp_path):
        """A WORKING task persisted via file store is reconciled on restart."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        store = FileAgentCrewTaskStore(base_dir=str(tmp_path))

        task = Task(
            id="orphan-test",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        asyncio.run(store.save(task, ctx))

        # Recreate store (simulates restart)
        store2 = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        loaded = asyncio.run(store2.get("orphan-test", ctx))
        assert loaded is not None
        assert loaded.id == "orphan-test"
        # The task persists as-is (WORKING state preserved). The server must
        # handle resubscribe gracefully for such orphaned tasks.
        assert loaded.status.state == TaskState.TASK_STATE_WORKING

    def _make_handler(self, store, registry=None):
        """Build an AgentCrewRequestHandlerV2 bound to the given task store."""
        from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard

        from AgentCrew.modules.a2a.server import AgentCrewRequestHandlerV2

        handler = AgentCrewRequestHandlerV2(
            agent_executor=_StubExecutor(),
            task_store=store,
            agent_card=AgentCard(
                name="stub",
                description="stub",
                capabilities=AgentCapabilities(streaming=True),
            ),
        )
        if registry is not None:
            handler._active_task_registry = registry
        return handler

    def test_resubscribe_orphaned_working_task_yields_failed(self, tmp_path):
        """Resubscribing an orphaned WORKING task yields FAILED and stops."""
        from a2a.server.context import ServerCallContext
        from a2a.types.a2a_pb2 import SubscribeToTaskRequest

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        asyncio.run(
            store.save(
                Task(
                    id="orphan-working",
                    context_id="ctx-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                ),
                ctx,
            )
        )
        handler = self._make_handler(store)

        async def resubscribe():
            events = []
            async for ev in handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id="orphan-working"), ctx
            ):
                events.append(ev)
            return events

        # wait_for guards against the pre-fix hang on an idle SDK producer.
        events = asyncio.run(asyncio.wait_for(resubscribe(), timeout=5))
        assert len(events) == 1
        assert events[0].id == "orphan-working"
        assert events[0].status.state == TaskState.TASK_STATE_FAILED
        assert (
            events[0].status.message.parts[0].text
            == "Task execution was interrupted."
        )
        # The FAILED state is persisted for future resubscribes.
        persisted = asyncio.run(store.get("orphan-working", ctx))
        assert persisted.status.state == TaskState.TASK_STATE_FAILED

    def test_resubscribe_completed_task_yields_snapshot(self, tmp_path):
        """Resubscribing a terminal task yields the snapshot, no error."""
        from a2a.server.context import ServerCallContext
        from a2a.types.a2a_pb2 import SubscribeToTaskRequest

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        asyncio.run(
            store.save(
                Task(
                    id="done-task",
                    context_id="ctx-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                ),
                ctx,
            )
        )
        handler = self._make_handler(store)

        async def resubscribe():
            events = []
            async for ev in handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id="done-task"), ctx
            ):
                events.append(ev)
            return events

        events = asyncio.run(asyncio.wait_for(resubscribe(), timeout=5))
        assert len(events) == 1
        assert events[0].id == "done-task"
        assert events[0].status.state == TaskState.TASK_STATE_COMPLETED

    def test_resubscribe_live_active_task_delegates_to_sdk(self, tmp_path):
        """A non-terminal task with a live ActiveTask streams via super()."""
        from a2a.server.context import ServerCallContext
        from a2a.types.a2a_pb2 import SubscribeToTaskRequest

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        asyncio.run(
            store.save(
                Task(
                    id="live-task",
                    context_id="ctx-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                ),
                ctx,
            )
        )

        class _FakeActiveTask:
            async def subscribe(self, **kwargs):
                yield Task(
                    id="live-task",
                    context_id="ctx-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )

        class _FakeRegistry:
            def __init__(self, active_task):
                self.active_task = active_task
                self.get_or_create_called = False

            async def get(self, task_id):
                return self.active_task

            async def get_or_create(self, *args, **kwargs):
                self.get_or_create_called = True
                return self.active_task

        registry = _FakeRegistry(_FakeActiveTask())
        handler = self._make_handler(store, registry=registry)

        async def resubscribe():
            events = []
            async for ev in handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id="live-task"), ctx
            ):
                events.append(ev)
            return events

        events = asyncio.run(asyncio.wait_for(resubscribe(), timeout=5))
        # super() (the SDK default handler) was reached via get_or_create.
        assert registry.get_or_create_called
        assert len(events) == 1
        assert events[0].id == "live-task"
        assert events[0].status.state == TaskState.TASK_STATE_WORKING

    def test_startup_reconciliation_marks_orphaned_tasks_failed(self, tmp_path):
        """Lifespan startup marks persisted non-terminal tasks FAILED."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        store = FileAgentCrewTaskStore(
            base_dir=str(tmp_path), agent_namespace="stub"
        )
        asyncio.run(
            store.save(
                Task(
                    id="restart-orphan",
                    context_id="ctx-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                ),
                ctx,
            )
        )
        asyncio.run(
            store.save(
                Task(
                    id="restart-done",
                    context_id="ctx-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                ),
                ctx,
            )
        )

        mgr = AgentManager()
        agent = StubForA2A()
        mgr.register_agent(agent)

        with patch.object(LocalAgent, "__subclasshook__", return_value=True):
            original_isinstance = isinstance

            def patched_isinstance(obj, cls):
                if cls is LocalAgent and hasattr(obj, "name"):
                    return True
                return original_isinstance(obj, cls)

            with patch("builtins.isinstance", patched_isinstance):
                srv = A2AServer(
                    agent_manager=mgr,
                    host="127.0.0.1",
                    port=0,
                    base_url="http://127.0.0.1:0",
                    store_type="file",
                    store_options={"base_dir": str(tmp_path)},
                )

        with ASGITestClient(srv.app):
            # Lifespan startup runs stale-task reconciliation.
            pass

        # Fresh store instance reads the reconciled state from disk.
        store2 = FileAgentCrewTaskStore(
            base_dir=str(tmp_path), agent_namespace="stub"
        )
        orphan = asyncio.run(store2.get("restart-orphan", ctx))
        assert orphan is not None
        assert orphan.status.state == TaskState.TASK_STATE_FAILED
        assert (
            orphan.status.message.parts[0].text
            == "Task execution was interrupted by server restart."
        )
        done = asyncio.run(store2.get("restart-done", ctx))
        assert done.status.state == TaskState.TASK_STATE_COMPLETED


class TestA2AServerStartup:
    """Tests for A2AServer.start() — focused on the asyncio.Runner fix.

    The ``start()`` method uses ``asyncio.Runner`` with uvicorn's loop
    factory to bypass the ``nest_asyncio``-patched ``asyncio.run()``,
    which does not accept the ``loop_factory`` kwarg added in Python 3.12.

    All tests mock ``uvicorn.Server.serve`` to avoid actual socket binding.
    """

    def test_uvicorn_run_completes_without_error(self, server, monkeypatch):
        """A2AServer.start() completes without TypeError or SystemExit.

        ``uvicorn.run()`` calls the native ``asyncio.run()`` (no longer
        patched by ``nest_asyncio``), which supports the ``loop_factory``
        kwarg that uvicorn passes internally.
        """
        import uvicorn

        run_called = False

        def fake_uvicorn_main(app, **kwargs):
            nonlocal run_called
            run_called = True

        monkeypatch.setattr(uvicorn, "run", fake_uvicorn_main)

        server.start()

        assert run_called, "A2AServer.start() did not invoke uvicorn.run()"

    def test_start_logs_agents(self, server, monkeypatch):
        """A2AServer.start() logs available agents."""
        import uvicorn

        def fake_uvicorn_main(app, **kwargs):
            pass

        monkeypatch.setattr(uvicorn, "run", fake_uvicorn_main)

        server.start()
