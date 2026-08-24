"""Tests for the embedded A2A server chat UI.

Covers the public root page, static assets under the reserved /_a2a-ui/
prefix, the public UI config endpoint, and auth gating of protected agent
routes when an API key is configured.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient as ASGITestClient

from AgentCrew.modules.a2a.server import A2AServer
from AgentCrew.modules.agents import AgentManager, LocalAgent


class StubForA2A:
    """Duck-typed agent stub matching the LocalAgent interface used by A2AServer."""

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


def _make_server(api_key: str | None = None, store_type: str = "memory"):
    """Build an A2AServer with a stub agent, bypassing the LocalAgent gate."""
    mgr = AgentManager()
    mgr.register_agent(StubForA2A())

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
            store_type=store_type,
            api_key=api_key,
        )
    return srv


@pytest.fixture
def server():
    return _make_server()


class TestA2AWebUIPublicRoutes:
    """Public UI routes must be reachable without authentication."""

    def test_root_serves_html(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "AgentCrew A2A Chat" in resp.text

    def test_stylesheet_served(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/_a2a-ui/styles.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]
        assert ":root" in resp.text

    def test_app_js_served(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/_a2a-ui/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "A2AClient" in resp.text

    def test_ui_modules_served(self, server):
        client = ASGITestClient(server.app)
        for module in ("db.js", "a2a.js", "ui.js"):
            resp = client.get(f"/_a2a-ui/{module}")
            assert resp.status_code == 200
            assert "javascript" in resp.headers["content-type"]

    def test_ui_config_no_auth_required(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/_a2a-ui/config")
        assert resp.status_code == 200
        assert resp.json() == {"authRequired": False}

    def test_ui_config_never_leaks_key(self, server):
        client = ASGITestClient(server.app)
        body = client.get("/_a2a-ui/config").text
        assert "secret" not in body
        assert "api_key" not in body

    def test_agents_route_still_public(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()]
        assert "stub" in names

    def test_agent_card_still_public(self, server):
        client = ASGITestClient(server.app)
        resp = client.get("/stub/.well-known/agent-card.json")
        assert resp.status_code == 200
        assert resp.json()["name"] == "stub"


class TestA2AWebUIAuth:
    """With a configured API key, public routes stay public and agent routes
    are protected."""

    @pytest.fixture
    def authed_server(self):
        return _make_server(api_key="secret-key")

    def test_ui_config_reports_auth_required(self, authed_server):
        client = ASGITestClient(authed_server.app)
        resp = client.get("/_a2a-ui/config")
        assert resp.status_code == 200
        assert resp.json() == {"authRequired": True}
        assert "secret-key" not in resp.text

    def test_root_stays_public_with_key(self, authed_server):
        client = ASGITestClient(authed_server.app)
        assert client.get("/").status_code == 200
        assert client.get("/_a2a-ui/styles.css").status_code == 200
        assert client.get("/_a2a-ui/app.js").status_code == 200
        assert client.get("/agents").status_code == 200

    def test_agent_card_requires_bearer(self, authed_server):
        client = ASGITestClient(authed_server.app)
        assert client.get("/stub/.well-known/agent-card.json").status_code == 401

    def test_agent_card_wrong_bearer_rejected(self, authed_server):
        client = ASGITestClient(authed_server.app)
        resp = client.get(
            "/stub/.well-known/agent-card.json",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_agent_card_correct_bearer_accepted(self, authed_server):
        client = ASGITestClient(authed_server.app)
        resp = client.get(
            "/stub/.well-known/agent-card.json",
            headers={"Authorization": "Bearer secret-key"},
        )
        assert resp.status_code == 200

    def test_rpc_requires_bearer(self, authed_server):
        client = ASGITestClient(authed_server.app)
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
        assert client.post("/stub/", json=req).status_code == 401
        ok = client.post(
            "/stub/",
            json=req,
            headers={"Authorization": "Bearer secret-key"},
        )
        assert ok.status_code == 200
