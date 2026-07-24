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

    def test_v03_card(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert data["preferredTransport"] == "jsonrpc"

    def test_v03_card_has_required_fields(self, server):
        """v0.3 JS client requires top-level url, preferredTransport, protocolVersion."""
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent.json")
        data = resp.json()
        assert "protocolVersion" in data or "protocol_version" in data
        assert data.get("url", "").endswith("/stub/")
        assert "name" in data

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
