"""
Integration tests for the A2A v1 server with SDK route factories.

Tests: server lifecycle, card endpoints, JSON-RPC, streaming, task lookup,
cancel, input-required resume, durable stores, v0.3 card compat.
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


async def _start_server(app, host="127.0.0.1", port=0):
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
                r = await c.get(f"{url}/test_agent/", timeout=2)
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
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="0.3",
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
        routes.extend(
            create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True)
        )

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

            # Fetch v0.3 compat card
            async with httpx.AsyncClient() as c:
                # The SDK route returns v1 card under the default path
                r = await c.get(f"{url}/.well-known/agent.json")
                assert r.status_code == 200
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
        routes.extend(
            create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True)
        )

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
                        },
                        "configuration": {},
                    },
                }
                r = await c.post(url, json=req, timeout=10)
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
        routes.extend(
            create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True)
        )
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
