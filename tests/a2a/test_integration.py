"""
Integration tests for the A2A v1 server with SDK route factories.

Tests: server lifecycle, card endpoints, JSON-RPC, streaming, task lookup,
cancel, input-required resume, durable stores.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import uvicorn
from a2a.client import create_client
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.timestamp_pb2 import Timestamp
from starlette.applications import Starlette

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class DummyLocalAgent:
    """Minimal agent stub for integration testing."""

    name = "test_agent"
    description = "Test agent for integration tests"
    input_tokens_usage = 0
    output_tokens_usage = 0

    def __init__(self):
        self.history: list[dict[str, Any]] = []
        self.tool_definitions: dict[str, Any] = {}

    def format_message(self, msg_type, data):
        if hasattr(msg_type, "value"):
            msg_type = msg_type.value if hasattr(msg_type, "value") else str(msg_type)
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
        yield "Hello from dummy agent!", "Hello from dummy agent!", None

    def get_model(self):
        return "test-model"

    def is_streaming(self):
        return True


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _start_server(app, host="127.0.0.1", port=0, wait_path="/test_agent/"):
    """Start uvicorn on a random port, return (url, server_task)."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # Wait for server to start
    url = f"http://{host}:{port}"
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{url}{wait_path}", timeout=2)
                if r.status_code < 500:
                    return url, server_task
        except (httpx.ConnectError, httpx.RemoteProtocolError):
            await asyncio.sleep(0.1)
    raise RuntimeError("Server did not start")


# ---------------------------------------------------------------------------
# Test a basic server with SDK route factories
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_card():
    return AgentCard(
        name="test_agent",
        description="Test agent",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="http://127.0.0.1:0/test_agent/",
            ),
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )


class TestServerBasics:
    """Basic server integration tests."""

    @pytest.mark.asyncio
    async def test_v1_card_endpoint(self, agent_card, tmp_path):
        """Verify v1 AgentCard is served at the SDK route."""
        from a2a.server.agent_execution import AgentExecutor
        from a2a.server.request_handlers.default_request_handler_v2 import (
            DefaultRequestHandlerV2,
        )
        from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

        class SimpleExecutor(AgentExecutor):
            async def execute(self, context, event_queue):
                task = Task(
                    id=context.task_id or "t1",
                    context_id=context.context_id or "c1",
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
                await event_queue.enqueue_event(task)
                ts = Timestamp()
                ts.FromDatetime(datetime.now(UTC))
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.context_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_COMPLETED, timestamp=ts
                        ),
                    )
                )

            async def cancel(self, context, event_queue):
                pass

        store = InMemoryTaskStore()
        handler = DefaultRequestHandlerV2(
            agent_executor=SimpleExecutor(),
            task_store=store,
            agent_card=agent_card,
        )

        routes = []
        routes.extend(create_agent_card_routes(agent_card))
        routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))

        app = Starlette(routes=routes)
        url, server_task = await _start_server(app)

        try:
            # Fetch v1 card
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{url}/.well-known/agent-card.json")
                assert r.status_code == 200
                data = r.json()
                assert "supportedInterfaces" in data
                assert data["name"] == "test_agent"

            # Legacy v0.3 endpoint must be absent
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{url}/.well-known/agent.json")
                assert r.status_code == 404
        finally:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, RuntimeError):
                pass

    @pytest.mark.asyncio
    async def test_v1_send_message(self, agent_card):
        """Send a message via JSON-RPC and get a response."""
        from datetime import datetime

        from a2a.server.agent_execution import AgentExecutor
        from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

        class EchoExecutor(AgentExecutor):
            async def execute(self, context, event_queue):
                task_id = context.task_id or "echo-task"
                ctx_id = context.context_id or "echo-ctx"
                task = Task(
                    id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
                await event_queue.enqueue_event(task)
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=ctx_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_WORKING, timestamp=Timestamp()
                        ),
                    )
                )
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=task_id,
                        context_id=ctx_id,
                        artifact=Artifact(
                            artifact_id="answer_echo-task",
                            parts=[Part(text="Echo response")],
                        ),
                    )
                )
                ts = Timestamp()
                ts.FromDatetime(datetime.now(UTC))
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=ctx_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_COMPLETED, timestamp=ts
                        ),
                    )
                )

            async def cancel(self, context, event_queue):
                pass

        store = InMemoryTaskStore()
        handler = DefaultRequestHandlerV2(
            agent_executor=EchoExecutor(),
            task_store=store,
            agent_card=agent_card,
        )

        routes = create_agent_card_routes(agent_card)
        routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))

        app = Starlette(routes=routes)
        url, server_task = await _start_server(app)

        try:
            async with httpx.AsyncClient() as c:
                req = {
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                            "messageId": "test-msg-1",
                        },
                        "configuration": {},
                    },
                }
                r = await c.post(
                    url,
                    json=req,
                    timeout=10,
                    headers={"A2A-Version": "1.0"},
                )
                assert r.status_code == 200
                data = r.json()
                # May be a streaming response — check for result.task or result.message
                assert "result" in data
                # The response format depends on SDK version
                if "task" in data["result"]:
                    assert data["result"]["task"]["status"]["state"] in (
                        "TASK_STATE_COMPLETED",
                        "TASK_STATE_WORKING",
                    )
        finally:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, RuntimeError):
                pass


class TestDurableTaskStore:
    """File-backed TaskStore survives restart."""

    @pytest.mark.asyncio
    async def test_in_memory_task_store(self):
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import InMemoryAgentCrewTaskStore

        store = InMemoryAgentCrewTaskStore()
        ctx = ServerCallContext()
        task = Task(
            id="mem-test",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        await store.save(task, ctx)
        loaded = await store.get("mem-test", ctx)
        assert loaded is not None
        assert loaded.id == "mem-test"

    @pytest.mark.asyncio
    async def test_file_store_restart(self, tmp_path):
        """Write a task, create new store, read it back."""
        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        from a2a.server.context import ServerCallContext

        ctx = ServerCallContext()
        task = Task(
            id="survive-test",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        await store.save(task, ctx)

        # Create a new store instance (simulates restart)
        store2 = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        loaded = await store2.get("survive-test", ctx)
        assert loaded is not None
        assert loaded.id == "survive-test"
        assert loaded.context_id == "ctx-1"
        assert loaded.status.state == TaskState.TASK_STATE_WORKING

    @pytest.mark.asyncio
    async def test_in_memory_store_roundtrip(self):
        """In-memory store round-trips (second variant)."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import InMemoryAgentCrewTaskStore

        store = InMemoryAgentCrewTaskStore()
        ctx = ServerCallContext()
        task = Task(
            id="mem-test",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        await store.save(task, ctx)
        loaded = await store.get("mem-test", ctx)
        assert loaded is not None
        assert loaded.id == "mem-test"


class TestRemoteAgentClient:
    """Test RemoteAgent's actual client calls against a running server."""

    @pytest.mark.asyncio
    async def test_client_send_and_get_task(self, agent_card):
        """Create a server, send a message with the SDK client, then get the task."""
        from datetime import datetime

        from a2a.server.agent_execution import AgentExecutor

        class EchoExecutor(AgentExecutor):
            async def execute(self, context, event_queue):
                tid = context.task_id or "echo"
                cid = context.context_id or "echo-ctx"
                task = Task(
                    id=tid,
                    context_id=cid,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
                await event_queue.enqueue_event(task)
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=tid,
                        context_id=cid,
                        artifact=Artifact(
                            artifact_id="answer_echo", parts=[Part(text="Hi!")]
                        ),
                    )
                )
                ts = Timestamp()
                ts.FromDatetime(datetime.now(UTC))
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=tid,
                        context_id=cid,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_COMPLETED, timestamp=ts
                        ),
                    )
                )

            async def cancel(self, context, event_queue):
                pass

        from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

        store = InMemoryTaskStore()
        handler = DefaultRequestHandlerV2(
            agent_executor=EchoExecutor(),
            task_store=store,
            agent_card=agent_card,
        )

        routes = create_agent_card_routes(agent_card)
        routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
        app = Starlette(routes=routes)
        url, server_task = await _start_server(app)

        try:
            client = await create_client(url)

            # Send a message
            msg = Message(role=Role.ROLE_USER, parts=[Part(text="hello")])
            config = SendMessageConfiguration()
            req = SendMessageRequest(message=msg, configuration=config)

            response_task_id = None
            async for chunk in client.send_message(req):
                if chunk.HasField("task"):
                    response_task_id = chunk.task.id
                elif (
                    chunk.HasField("artifact_update")
                    or chunk.HasField("status_update")
                    and (
                        chunk.status_update.status.state
                        == TaskState.TASK_STATE_COMPLETED
                    )
                ):
                    pass

            # Get the task
            if response_task_id:
                task = await client.get_task(GetTaskRequest(id=response_task_id))
                assert task is not None
                assert task.id == response_task_id

            await client.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, RuntimeError):
                pass


class TestStoreIsolation:
    """Agent namespace and owner isolation tests."""

    @pytest.mark.asyncio
    async def test_agent_namespace_isolation(self, tmp_path):
        """Two agents with same task ID do not collide."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        s1 = FileAgentCrewTaskStore(base_dir=str(tmp_path), agent_namespace="agent1")
        s2 = FileAgentCrewTaskStore(base_dir=str(tmp_path), agent_namespace="agent2")

        task = Task(
            id="same-id",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        await s1.save(task, ctx)

        loaded = await s2.get("same-id", ctx)
        assert loaded is None, "Agent2 should not see agent1's task"

    @pytest.mark.asyncio
    async def test_owner_isolation_in_memory(self):
        """Owner key isolation in InMemoryAgentCrewTaskStore."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import InMemoryAgentCrewTaskStore

        store = InMemoryAgentCrewTaskStore()
        ctx_a = ServerCallContext()
        ctx_a.tenant = "user-a"
        ctx_b = ServerCallContext()
        ctx_b.tenant = "user-b"

        task = Task(
            id="owner-test",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        await store.save(task, ctx_a)

        loaded_a = await store.get("owner-test", ctx_a)
        assert loaded_a is not None
        loaded_b = await store.get("owner-test", ctx_b)
        assert loaded_b is None, "User-b should not see user-a's task"

    @pytest.mark.asyncio
    async def test_file_list_after_restart(self, tmp_path):
        """FileAgentCrewTaskStore.list() returns all tasks after restart."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        s1 = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        await s1.save(
            Task(
                id="t1",
                context_id="c1",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            ),
            ctx,
        )
        await s1.save(
            Task(
                id="t2",
                context_id="c2",
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            ),
            ctx,
        )

        # Restart
        s2 = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        from a2a.types.a2a_pb2 import ListTasksRequest

        result = await s2.list(ListTasksRequest(), ctx)
        ids = {t.id for t in result.tasks}
        assert "t1" in ids
        assert "t2" in ids

    @pytest.mark.asyncio
    async def test_file_deleted_task_not_in_list(self, tmp_path):
        """Deleted task does not appear in list."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore

        ctx = ServerCallContext()
        s = FileAgentCrewTaskStore(base_dir=str(tmp_path))
        await s.save(
            Task(
                id="del-me",
                context_id="c1",
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            ),
            ctx,
        )
        await s.delete("del-me", ctx)

        from a2a.types.a2a_pb2 import ListTasksRequest

        result = await s.list(ListTasksRequest(), ctx)
        assert "del-me" not in {t.id for t in result.tasks}

    @pytest.mark.asyncio
    async def test_redis_session_store(self):
        """RedisSessionStore can be constructed and configured."""
        from AgentCrew.modules.a2a.session_store import RedisSessionStore

        store = RedisSessionStore(
            redis_url="redis://localhost:6379", agent_namespace="test"
        )
        assert "test" in store._prefix
        assert "sesh" in store._prefix
        await store.close()

    @pytest.mark.asyncio
    async def test_accumulator_append_ha_ha(self):
        """Append "ha" + "ha" must produce "haha" (no false dedup)."""
        from AgentCrew.modules.agents.remote_agent import ArtifactAccumulator

        acc = ArtifactAccumulator()
        # Simulate append=true events: each delta is recorded unconditionally
        acc.record_text("a1", "ha")
        assert acc.get_total_text("a1") == "ha"
        acc.record_text("a1", "ha")
        # Second "ha" must NOT be dropped
        assert acc.get_total_text("a1") == "haha"

    @pytest.mark.asyncio
    async def test_accumulator_append_contained_text(self):
        """Append "a" after "cat" must produce "cata" (no false substring dedup)."""
        from AgentCrew.modules.agents.remote_agent import ArtifactAccumulator

        acc = ArtifactAccumulator()
        acc.record_text("a1", "cat")
        acc.record_text("a1", "a")
        assert acc.get_total_text("a1") == "cata"

    @pytest.mark.asyncio
    async def test_accumulator_snapshot_equal_to_accumulated(self):
        """Reconnect snapshot equal to accumulated text emits nothing."""
        from a2a.types.a2a_pb2 import Artifact, Part, Task, TaskState, TaskStatus

        from AgentCrew.modules.agents.remote_agent import ArtifactAccumulator

        acc = ArtifactAccumulator()
        acc.record_text("a1", "hello")

        task = Task(
            id="t1",
            context_id="c1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            artifacts=[Artifact(artifact_id="a1", parts=[Part(text="hello")])],
        )
        result = acc.on_task_snapshot(task)
        assert len(result) == 0, "No new text should be reported"

    @pytest.mark.asyncio
    async def test_accumulator_snapshot_extends_accumulated(self):
        """Reconnect snapshot with more text emits only the suffix."""
        from a2a.types.a2a_pb2 import Artifact, Part, Task, TaskState, TaskStatus

        from AgentCrew.modules.agents.remote_agent import ArtifactAccumulator

        acc = ArtifactAccumulator()
        acc.record_text("a1", "hel")

        task = Task(
            id="t1",
            context_id="c1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            artifacts=[Artifact(artifact_id="a1", parts=[Part(text="hello")])],
        )
        result = acc.on_task_snapshot(task)
        assert "a1" in result
        assert result["a1"] == "lo", f"Expected 'lo', got '{result['a1']}'"

    @pytest.mark.asyncio
    async def test_accumulator_reset(self):
        """Reset clears all state."""
        from AgentCrew.modules.agents.remote_agent import ArtifactAccumulator

        acc = ArtifactAccumulator()
        acc.record_text("a1", "data")
        assert acc.get_total_text("a1") == "data"
        acc.reset()
        assert acc.get_total_text("a1") == ""
        assert acc.phase == "idle"


class TestSharedHistory:
    """Owner-scoped conversation history shared across agents; tasks stay isolated."""

    @pytest.mark.asyncio
    async def test_file_history_shared_across_agent_stores(self, tmp_path):
        """Two agents (containers) on the same file store share owner-scoped history."""
        from AgentCrew.modules.a2a.session_store import FileSessionStore

        store_a = FileSessionStore(base_dir=str(tmp_path), agent_namespace="agent-a")
        store_b = FileSessionStore(base_dir=str(tmp_path), agent_namespace="agent-b")

        await store_a.append_history(
            "ctx-1", {"role": "user", "content": "hello"}, owner="owner-1"
        )

        history = await store_b.get_history("ctx-1", owner="owner-1")
        assert len(history) == 1
        assert history[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_history_owner_isolation(self, tmp_path):
        """Same context id under a different owner never shares history."""
        from AgentCrew.modules.a2a.session_store import FileSessionStore

        store = FileSessionStore(base_dir=str(tmp_path))
        await store.append_history(
            "ctx-1", {"role": "user", "content": "secret"}, owner="owner-1"
        )

        assert await store.get_history("ctx-1", owner="owner-1")
        assert await store.get_history("ctx-1", owner="owner-2") == []

        await store.append_history(
            "ctx-1", {"role": "user", "content": "other"}, owner="owner-2"
        )
        hist2 = await store.get_history("ctx-1", owner="owner-2")
        assert len(hist2) == 1
        assert hist2[0]["content"] == "other"

    @pytest.mark.asyncio
    async def test_missing_context_returns_empty_history(self):
        """A new/missing context returns an empty history without error."""
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        store = InMemorySessionStore()
        assert await store.get_history("brand-new-ctx", owner="owner-1") == []

    @pytest.mark.asyncio
    async def test_pending_tools_agent_and_owner_isolated(self):
        """Pending/in-flight tool state stays agent-namespaced and owner-scoped."""
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        store = InMemorySessionStore()
        await store.save_pending_tools(
            "task-1", {"name": "ask"}, [], owner="owner-1", agent_namespace="agent-a"
        )

        # Same task id under another agent is invisible
        assert (
            await store.get_pending_tools(
                "task-1", owner="owner-1", agent_namespace="agent-b"
            )
            is None
        )
        # Same task id under another owner is invisible
        assert (
            await store.get_pending_tools(
                "task-1", owner="owner-2", agent_namespace="agent-a"
            )
            is None
        )
        # Visible under the exact agent + owner scope
        assert (
            await store.get_pending_tools(
                "task-1", owner="owner-1", agent_namespace="agent-a"
            )
            is not None
        )
        # Clear under the wrong scope leaves it intact
        await store.clear_pending_tools(
            "task-1", owner="owner-2", agent_namespace="agent-b"
        )
        assert (
            await store.get_pending_tools(
                "task-1", owner="owner-1", agent_namespace="agent-a"
            )
            is not None
        )

    @pytest.mark.asyncio
    async def test_empty_tenant_uses_default_owner(self):
        """Callers omitting tenant map to the default owner; explicit tenants stay isolated."""
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.session_store import (
            InMemorySessionStore,
            _owner_key,
        )

        assert _owner_key(ServerCallContext()) == "default"

        store = InMemorySessionStore()
        await store.append_history("ctx-1", {"role": "user", "content": "hi"})
        # owner defaults to "default" for backward compatibility
        assert len(await store.get_history("ctx-1")) == 1
        assert len(await store.get_history("ctx-1", owner="default")) == 1
        # An explicit tenant must NOT see the default-owner history
        assert await store.get_history("ctx-1", owner="user-a") == []


class TestExecutorScope:
    """Task-scoped cancellation tests."""

    @pytest.mark.asyncio
    async def test_cancel_one_task_does_not_affect_another(self):
        """Cancelling task A does not affect task B."""
        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)
        # Simulate concurrent tasks
        e1 = await executor._get_cancel_event("task-a")
        e2 = await executor._get_cancel_event("task-b")
        assert not e1.is_set()
        assert not e2.is_set()
        # Cancel task-a
        ce = await executor._get_cancel_event("task-a")
        ce.set()
        assert e1.is_set()
        assert not e2.is_set(), "Task-b should NOT be cancelled"

        await executor._cleanup_cancel("task-a")
        await executor._cleanup_cancel("task-b")


class TestResumeFromInputRequired:
    """Regression tests for artifact append-before-create on resume."""

    @pytest.mark.asyncio
    async def test_resume_without_existing_artifact_uses_append_false(self):
        """When no answer artifact exists in current_task, first resumed chunk must use append=False."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import (
            AgentCrewA2AExecutor,
        )
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-no-artifact"
        context_id = "test-ctx-no-artifact"

        # Create a task with no artifacts
        persisted_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            artifacts=[],
        )

        # Create request context with the persisted task
        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=persisted_task,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        # Call _build_answer_state
        answer_state = executor._build_answer_state(ctx, task_id)

        # Assert: emitted must be False since no artifact exists
        assert answer_state.emitted is False, (
            "emitted should be False when no answer artifact exists in current_task"
        )
        assert answer_state.artifact_id == f"answer_{task_id}"
        assert answer_state.accumulated_text == ""

    @pytest.mark.asyncio
    async def test_resume_with_existing_artifact_uses_append_true(self):
        """When answer artifact exists in current_task, first resumed chunk must use append=True."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-with-artifact"
        context_id = "test-ctx-with-artifact"

        # Create a task with an existing answer artifact
        persisted_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            artifacts=[
                Artifact(
                    artifact_id=f"answer_{task_id}",
                    parts=[Part(text="Hello from before the ask")],
                )
            ],
        )

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=persisted_task,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        answer_state = executor._build_answer_state(ctx, task_id)

        # Assert: emitted must be True since artifact exists
        assert answer_state.emitted is True, (
            "emitted should be True when answer artifact exists in current_task"
        )
        assert answer_state.artifact_id == f"answer_{task_id}"
        assert answer_state.accumulated_text == "Hello from before the ask"

    @pytest.mark.asyncio
    async def test_resume_with_none_task_uses_append_false(self):
        """When current_task is None, emitted must default to False (safe protocol default)."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-none-task"
        context_id = "test-ctx-none-task"

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=None,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        answer_state = executor._build_answer_state(ctx, task_id)

        assert answer_state.emitted is False
        assert answer_state.accumulated_text == ""

    @pytest.mark.asyncio
    async def test_resume_with_multiple_parts_accumulates_text(self):
        """When answer artifact has multiple text parts, accumulated_text concatenates them."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-multi-parts"
        context_id = "test-ctx-multi-parts"

        persisted_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            artifacts=[
                Artifact(
                    artifact_id=f"answer_{task_id}",
                    parts=[
                        Part(text="First chunk "),
                        Part(text="Second chunk"),
                    ],
                )
            ],
        )

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=persisted_task,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        answer_state = executor._build_answer_state(ctx, task_id)

        assert answer_state.emitted is True
        assert answer_state.accumulated_text == "First chunk Second chunk"

    @pytest.mark.asyncio
    async def test_other_artifacts_do_not_trigger_emitted(self):
        """Artifacts with non-answer IDs must not set emitted=True for the answer artifact."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-other-artifacts"
        context_id = "test-ctx-other-artifacts"

        persisted_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            artifacts=[
                Artifact(
                    artifact_id=f"tool_{task_id}",
                    parts=[Part(text="tool result")],
                ),
                Artifact(
                    artifact_id=f"thinking_{task_id}",
                    parts=[Part(text="thinking")],
                ),
            ],
        )

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=persisted_task,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        answer_state = executor._build_answer_state(ctx, task_id)

        # Only answer artifact ID should count
        assert answer_state.emitted is False
        assert answer_state.accumulated_text == ""

    @pytest.mark.asyncio
    async def test_resume_with_turn_2_artifact_restores_state(self):
        """A persisted per-turn answer artifact restores artifact_id, turn, emitted and text."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-turn-2"
        context_id = "test-ctx-turn-2"

        persisted_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            artifacts=[
                Artifact(
                    artifact_id=f"answer_{task_id}_turn_2",
                    parts=[Part(text="Answer text from turn two")],
                )
            ],
        )

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=persisted_task,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        answer_state = executor._build_answer_state(ctx, task_id)

        assert answer_state.artifact_id == f"answer_{task_id}_turn_2"
        assert answer_state.turn == 2
        assert answer_state.emitted is True
        assert answer_state.accumulated_text == "Answer text from turn two"

    @pytest.mark.asyncio
    async def test_resume_picks_latest_turn_artifact(self):
        """With answer artifacts across turns present, the latest turn is restored."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        task_id = "test-task-latest-turn"
        context_id = "test-ctx-latest-turn"

        persisted_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            artifacts=[
                Artifact(
                    artifact_id=f"answer_{task_id}",
                    parts=[Part(text="turn zero text")],
                ),
                Artifact(
                    artifact_id=f"answer_{task_id}_turn_1",
                    parts=[Part(text="turn one text")],
                ),
                Artifact(
                    artifact_id=f"answer_{task_id}_turn_3",
                    parts=[Part(text="turn three text")],
                ),
                Artifact(
                    artifact_id=f"tool_{task_id}_turn_3",
                    parts=[Part(text="tool artifact")],
                ),
                Artifact(
                    artifact_id=f"answer_{task_id}_turn_notanumber",
                    parts=[Part(text="ignored")],
                ),
            ],
        )

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=persisted_task,
        )

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(agent=MagicMock(), session_store=store)

        answer_state = executor._build_answer_state(ctx, task_id)

        assert answer_state.artifact_id == f"answer_{task_id}_turn_3"
        assert answer_state.turn == 3
        assert answer_state.emitted is True
        assert answer_state.accumulated_text == "turn three text"


class TestMultiTurnArtifacts:
    """Per-turn artifact IDs for recursive _process_task calls."""

    @pytest.mark.asyncio
    async def test_tool_recursion_uses_per_turn_artifact_ids(self, monkeypatch):
        """Each new-turn recursion streams into its own per-turn answer/thinking/tool artifacts."""
        from a2a.server.agent_execution import RequestContext
        from a2a.server.context import ServerCallContext

        from AgentCrew.modules.a2a import agent_executor as executor_module
        from AgentCrew.modules.a2a.agent_executor import (
            AgentCrewA2AExecutor,
            AnswerArtifactState,
        )
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore
        from AgentCrew.modules.llm.token_usage import TokenUsage

        task_id = "multi-turn-task"
        context_id = "multi-turn-ctx"

        class MultiTurnStreamingAgent(DummyLocalAgent):
            """Streams two tool-use rounds, then a plain final round."""

            def __init__(self):
                super().__init__()
                self.process_calls = 0

            async def process_messages(self, messages, callback=None, **kwargs):
                self.process_calls += 1
                usage = TokenUsage(input_tokens=10, output_tokens=5)
                if self.process_calls < 3:
                    if callback:
                        callback(
                            [{"name": "dummy_tool", "input": {"query": "x"}}],
                            usage,
                        )
                    yield (
                        f"turn {self.process_calls} response",
                        f"turn {self.process_calls} chunk",
                        (f"thinking {self.process_calls}", "cursor"),
                    )
                else:
                    yield (
                        "final response",
                        "final chunk",
                        ("final thinking", "cursor"),
                    )

            def validate_tool_use(self, tool_use):
                return None

            async def execute_tool_call(self, tool_use):
                return "tool done"

        created_states: list[tuple[str, int]] = []
        original_state_cls = AnswerArtifactState

        class RecordingState(original_state_cls):
            def __init__(self, artifact_id, turn=0):
                created_states.append((artifact_id, turn))
                super().__init__(artifact_id, turn=turn)

        monkeypatch.setattr(executor_module, "AnswerArtifactState", RecordingState)

        store = InMemorySessionStore()
        executor = AgentCrewA2AExecutor(
            agent=MultiTurnStreamingAgent(), session_store=store
        )

        call_ctx = ServerCallContext()
        ctx = RequestContext(
            call_context=call_ctx,
            task_id=task_id,
            context_id=context_id,
            task=None,
        )

        events: list[Any] = []

        class RecordingQueue:
            async def enqueue_event(self, event):
                events.append(event)

        initial_state = executor_module.AnswerArtifactState(
            artifact_id=f"answer_{task_id}"
        )
        await executor._run_agent_loop(
            ctx, RecordingQueue(), task_id, context_id, [], initial_state
        )

        artifact_updates = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]
        artifact_ids = [u.artifact.artifact_id for u in artifact_updates]

        # Turn 0 keeps the legacy artifact IDs; later turns carry a turn suffix
        assert f"answer_{task_id}" in artifact_ids
        assert f"answer_{task_id}_turn_1" in artifact_ids
        assert f"answer_{task_id}_turn_2" in artifact_ids
        assert f"thinking_{task_id}" in artifact_ids
        assert f"thinking_{task_id}_turn_1" in artifact_ids
        assert f"thinking_{task_id}_turn_2" in artifact_ids
        assert f"tool_{task_id}" in artifact_ids
        assert f"tool_{task_id}_turn_1" in artifact_ids

        # Fresh per-turn answer states carry the incremented turn counter
        assert (f"answer_{task_id}", 0) in created_states
        assert (f"answer_{task_id}_turn_1", 1) in created_states
        assert (f"answer_{task_id}_turn_2", 2) in created_states

        # Each turn's chunks land only in that turn's artifact
        def text_of(artifact_id):
            return "".join(
                part.text
                for u in artifact_updates
                if u.artifact.artifact_id == artifact_id
                for part in u.artifact.parts
            )

        assert "turn 1 chunk" in text_of(f"answer_{task_id}")
        assert "turn 2 chunk" in text_of(f"answer_{task_id}_turn_1")
        assert "final chunk" in text_of(f"answer_{task_id}_turn_2")
        assert "turn 2 chunk" not in text_of(f"answer_{task_id}")

        # First chunk of each turn must create its artifact (append=False)
        def first_chunk(artifact_id):
            return next(
                u
                for u in artifact_updates
                if u.artifact.artifact_id == artifact_id and u.artifact.parts
            )

        assert first_chunk(f"answer_{task_id}").append is False
        assert first_chunk(f"answer_{task_id}_turn_1").append is False

        # Task must complete successfully (guards against silent loop failures)
        completed = any(
            isinstance(e, TaskStatusUpdateEvent)
            and e.status.state == TaskState.TASK_STATE_COMPLETED
            for e in events
        )
        assert completed


class TestAgentSwitchContinuity:
    """Cross-agent conversation continuity with agent-owned task isolation."""

    @pytest.mark.asyncio
    async def test_terminal_task_then_switch_agent_shares_history(self, tmp_path):
        """Terminal task under agent A, then a new message under agent B with the same
        tenant/context: agent B sees prior conversation messages but receives a new
        agent-B-owned task, and tasks stay isolated across agents.
        """
        from a2a.server.context import ServerCallContext
        from starlette.routing import Mount

        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
        from AgentCrew.modules.a2a.session_store import (
            FileAgentCrewTaskStore,
            FileSessionStore,
        )

        class CaptureAgent(DummyLocalAgent):
            def __init__(self, name):
                super().__init__()
                self.name = name
                self.captured: list[list[dict[str, Any]]] = []

            async def process_messages(self, messages, callback=None, **kwargs):
                self.captured.append(list(messages))
                yield f"reply from {self.name}", f"reply from {self.name}", None

        # One shared session store wired to both agents (as A2AServer does now)
        shared = FileSessionStore(base_dir=str(tmp_path))
        agents: dict[str, CaptureAgent] = {}
        task_stores: dict[str, FileAgentCrewTaskStore] = {}
        handlers: dict[str, DefaultRequestHandlerV2] = {}
        cards: dict[str, AgentCard] = {}
        for name in ("agent-a", "agent-b"):
            agents[name] = CaptureAgent(name)
            task_stores[name] = FileAgentCrewTaskStore(
                base_dir=str(tmp_path), agent_namespace=name
            )
            executor = AgentCrewA2AExecutor(agent=agents[name], session_store=shared)
            card = AgentCard(
                name=name,
                description=f"{name} test agent",
                version="1.0.0",
                supported_interfaces=[
                    AgentInterface(
                        protocol_binding="JSONRPC",
                        protocol_version="1.0",
                        url=f"http://127.0.0.1:0/{name}/",
                    ),
                ],
                capabilities=AgentCapabilities(streaming=True),
                default_input_modes=["text/plain"],
                default_output_modes=["text/plain"],
            )
            cards[name] = card
            handlers[name] = DefaultRequestHandlerV2(
                agent_executor=executor,
                task_store=task_stores[name],
                agent_card=card,
            )

        routes = []
        for name in ("agent-a", "agent-b"):
            routes.append(
                Mount(
                    f"/{name}",
                    routes=[
                        *create_agent_card_routes(cards[name]),
                        *create_jsonrpc_routes(handlers[name], rpc_url="/"),
                    ],
                )
            )
        app = Starlette(routes=routes)
        url, server_task = await _start_server(app, wait_path="/agent-a/")

        def send_payload(text: str, context_id: str) -> dict[str, Any]:
            return {
                "jsonrpc": "2.0",
                "id": "req-1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": text}],
                        "messageId": f"msg-{text}",
                        "contextId": context_id,
                    },
                    "configuration": {},
                    "tenant": "user-1",
                },
            }

        try:
            async with httpx.AsyncClient() as c:
                r1 = await c.post(
                    f"{url}/agent-a/",
                    json=send_payload("hello from user to agent-a", "ctx-1"),
                    timeout=15,
                    headers={"A2A-Version": "1.0"},
                )
                assert r1.status_code == 200
                task_a = r1.json()["result"]["task"]["id"]

                r2 = await c.post(
                    f"{url}/agent-b/",
                    json=send_payload("hello from user to agent-b", "ctx-1"),
                    timeout=15,
                    headers={"A2A-Version": "1.0"},
                )
                assert r2.status_code == 200
                task_b = r2.json()["result"]["task"]["id"]
        finally:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, RuntimeError):
                pass

        # A new message to agent B creates a new agent-B-owned task
        assert task_a != task_b

        # Agent B's executor received agent A's conversation history
        assert agents["agent-b"].captured, "agent-b should have processed messages"
        seen = agents["agent-b"].captured[0]
        user_texts = [
            part["text"]
            for m in seen
            if m.get("role") == "user"
            for part in m.get("content", [])
            if part.get("type") == "text"
        ]
        assert "hello from user to agent-a" in user_texts, (
            "agent-b should see agent-a's user message"
        )
        reply_texts = [
            part["text"]
            for m in seen
            if m.get("role") == "assistant"
            for part in m.get("content", [])
            if part.get("type") == "text"
        ]
        assert any("reply from agent-a" in t for t in reply_texts), (
            "agent-b should see agent-a's assistant reply"
        )

        # Shared history persisted under owner + context (no agent namespace)
        history = await shared.get_history("ctx-1", owner="user-1")
        hist_texts = [
            part["text"]
            for m in history
            if m.get("role") == "user"
            for part in m.get("content", [])
            if part.get("type") == "text"
        ]
        assert "hello from user to agent-a" in hist_texts
        assert "hello from user to agent-b" in hist_texts

        # Task isolation: agent B cannot see agent A's task, and vice versa
        ctx_user1 = ServerCallContext()
        ctx_user1.tenant = "user-1"
        store_b = FileAgentCrewTaskStore(
            base_dir=str(tmp_path), agent_namespace="agent-b"
        )
        assert await store_b.get(task_a, ctx_user1) is None, (
            "agent-b must not see agent-a's task"
        )
        assert await store_b.get(task_b, ctx_user1) is not None, (
            "agent-b must own its new task"
        )
        store_a = FileAgentCrewTaskStore(
            base_dir=str(tmp_path), agent_namespace="agent-a"
        )
        assert await store_a.get(task_b, ctx_user1) is None, (
            "agent-a must not see agent-b's task"
        )
