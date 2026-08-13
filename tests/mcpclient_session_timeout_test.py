"""Unit tests for the bounded MCP session-setup timeout.

Covers the two guarantees that keep a broken MCP server from hanging an A2A
task forever:

- an OAuth registration failure (e.g. a 307 redirect to a sign-in page) is
  never retried and surfaces as the standard error tool text block;
- a session-establishment hang raises ``TimeoutError`` instead of blocking
  and never pins the per-server OAuth lock.
"""

import asyncio

import pytest
from mcp.client.auth.exceptions import OAuthRegistrationError

from AgentCrew.modules.mcpclient.config import MCPServerConfig
from AgentCrew.modules.mcpclient.service import MCPService


class FakeCtx:
    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return (object(), object())

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False


class FakeSession:
    def __init__(self, initialize_impl):
        self.initialize_impl = initialize_impl
        self.initialize_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def initialize(self):
        self.initialize_calls += 1
        await self.initialize_impl()


class FakeOAuthResolver:
    def __init__(self, port):
        self.port = port

    def get_oauth_client_provider(self, url, token_storage):
        return None


class FakeTokenStorage:
    async def get_client_info(self):
        return None


def _server_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="oauth_broken",
        command="python",
        args=["server.py"],
        url="https://example.com/mcp/sse",
        streaming_server=True,
        enabledForAgents=["Engineer"],
    )


@pytest.fixture
def patched_service(monkeypatch):
    monkeypatch.setattr(
        "AgentCrew.modules.mcpclient.service.sse_client",
        lambda *args, **kwargs: FakeCtx(),
    )
    monkeypatch.setattr(
        "AgentCrew.modules.mcpclient.service.OAuthClientResolver",
        FakeOAuthResolver,
    )
    monkeypatch.setattr(
        "AgentCrew.modules.mcpclient.service.MCP_SESSION_SETUP_TIMEOUT", 0.1
    )
    service = MCPService()
    monkeypatch.setattr(
        service, "_build_token_storage", lambda config: FakeTokenStorage()
    )
    return service


@pytest.mark.asyncio
async def test_oauth_registration_error_is_not_retried_and_returns_error_text(
    monkeypatch, patched_service
):
    async def fail_registration():
        raise OAuthRegistrationError(
            "Registration failed: 307 /api/auth/signin?callbackUrl=%2Fregister"
        )

    fake_session = FakeSession(fail_registration)
    monkeypatch.setattr(
        "AgentCrew.modules.mcpclient.service.ClientSession",
        lambda *args, **kwargs: fake_session,
    )
    config = _server_config()

    handler = patched_service._create_stateless_tool_handler(config, "search_tool")()
    result = await handler(query="term")

    assert fake_session.initialize_calls == 1
    assert result[0]["type"] == "text"
    assert "Error calling MCP tool 'search_tool'" in result[0]["text"]
    assert "Registration failed: 307" in result[0]["text"]


@pytest.mark.asyncio
async def test_hanging_initialize_raises_timeout_and_releases_oauth_lock(
    monkeypatch, patched_service
):
    never = asyncio.Event()

    async def hang():
        await never.wait()

    fake_session = FakeSession(hang)
    monkeypatch.setattr(
        "AgentCrew.modules.mcpclient.service.ClientSession",
        lambda *args, **kwargs: fake_session,
    )
    config = _server_config()
    lock = patched_service._get_oauth_lock(config.name)

    with pytest.raises(TimeoutError):
        await patched_service._create_session(config)

    assert not lock.locked()


@pytest.mark.asyncio
async def test_hanging_initialize_stateless_handler_returns_error_text(
    monkeypatch, patched_service
):
    never = asyncio.Event()

    async def hang():
        await never.wait()

    fake_session = FakeSession(hang)
    monkeypatch.setattr(
        "AgentCrew.modules.mcpclient.service.ClientSession",
        lambda *args, **kwargs: fake_session,
    )
    config = _server_config()

    handler = patched_service._create_stateless_tool_handler(config, "search_tool")()
    result = await handler(query="term")

    assert result[0]["type"] == "text"
    assert "Error calling MCP tool 'search_tool'" in result[0]["text"]
    assert "timed out" in result[0]["text"]
