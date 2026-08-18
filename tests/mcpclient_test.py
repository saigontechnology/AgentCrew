import asyncio
import base64
import json
import time
from types import SimpleNamespace

import httpx2
import pytest
from mcp import MCPError
from mcp.client.auth import AuthorizationCodeResult
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import (
    BlobResourceContents,
    ImageContent,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    Prompt,
    ReadResourceResult,
    Resource,
    ResourceLink,
    TextContent,
    TextResourceContents,
    Tool,
)

from AgentCrew.modules.agents.context_manager import AgentContextManager
from AgentCrew.modules.mcpclient.auth import (
    FileTokenStorage,
    InlineTokenStorage,
    OAuthCallbackServer,
)
from AgentCrew.modules.mcpclient.config import (
    MCPConfigManager,
    MCPOAuthOverrideConfig,
    MCPServerConfig,
)
from AgentCrew.modules.mcpclient.service import MCPService, _MCPSessionScope


class FakeTokenStorage:
    def __init__(self, tokens=None, client_info=None):
        self.tokens = tokens
        self.client_info = client_info
        self.set_tokens_calls = []
        self.set_client_info_calls = []

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens
        self.set_tokens_calls.append(tokens)

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info
        self.set_client_info_calls.append(client_info)


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "mcp_servers.json"


@pytest.fixture
def valid_client_info_dict():
    return {
        "client_id": "clientid",
        "redirect_uris": ["http://localhost:14142/callback"],
    }


@pytest.fixture
def valid_tokens_dict():
    return {
        "access_token": "access_token",
        "token_type": "bearer",
        "refresh_token": "refresh_token",
        "expires_at": int((time.time() + 120) * 1000),
    }


class TestMCPConfig:
    def test_load_config_normalizes_oauth_override(
        self, config_path, valid_tokens_dict, valid_client_info_dict
    ):
        config_data = {
            "server1": {
                "name": "Test Server 1",
                "command": "python",
                "args": ["test_server.py"],
                "env": {"TEST_ENV": "value"},
                "enabledForAgents": ["Engineer"],
                "oauth": {
                    "tokens": valid_tokens_dict,
                    "client_info": valid_client_info_dict,
                },
            },
            "server2": {
                "name": "Test Server 2",
                "command": "node",
                "args": ["test_server.js"],
                "enabledForAgents": [],
            },
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        manager = MCPConfigManager(str(config_path))
        configs = manager.load_config()

        assert len(configs) == 2
        assert configs["server1"].name == "Test Server 1"
        assert configs["server1"].enabledForAgents == ["Engineer"]
        assert configs["server1"].oauth is not None
        assert configs["server1"].oauth.tokens is not None
        assert configs["server1"].oauth.tokens.access_token == "access_token"
        assert configs["server1"].oauth.tokens.refresh_token == "refresh_token"
        assert configs["server1"].oauth.tokens.expires_in is not None
        assert configs["server1"].oauth.tokens.expires_in > 0
        assert configs["server1"].oauth.client_info is not None
        assert configs["server1"].oauth.client_info.client_id == "clientid"
        assert (
            str(configs["server1"].oauth.client_info.redirect_uris[0])
            == "http://localhost:14142/callback"
        )
        assert configs["server2"].oauth is None

    def test_load_config_keeps_valid_oauth_section_when_other_section_invalid(
        self, config_path, valid_client_info_dict
    ):
        config_data = {
            "server1": {
                "name": "Test Server 1",
                "command": "python",
                "args": ["test_server.py"],
                "enabledForAgents": ["Engineer"],
                "oauth": {
                    "tokens": "invalid",
                    "client_info": valid_client_info_dict,
                },
            }
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        manager = MCPConfigManager(str(config_path))
        configs = manager.load_config()

        assert configs["server1"].oauth is not None
        assert configs["server1"].oauth.tokens is None
        assert configs["server1"].oauth.client_info is not None
        assert configs["server1"].oauth.client_info.client_id == "clientid"

    def test_get_enabled_servers_filters_by_enabled_for_agents(self, config_path):
        config_data = {
            "server1": {
                "name": "Test Server 1",
                "command": "python",
                "args": ["test_server.py"],
                "enabledForAgents": ["Engineer"],
            },
            "server2": {
                "name": "Test Server 2",
                "command": "node",
                "args": ["test_server.js"],
                "enabledForAgents": [],
            },
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        manager = MCPConfigManager(str(config_path))
        manager.load_config()

        enabled_servers = manager.get_enabled_servers()
        engineer_servers = manager.get_enabled_servers("Engineer")
        other_servers = manager.get_enabled_servers("Other")

        assert list(enabled_servers.keys()) == ["server1"]
        assert list(engineer_servers.keys()) == ["server1"]
        assert other_servers == {}


@pytest.mark.asyncio
class TestInlineTokenStorage:
    async def test_reads_config_override_before_base_storage(
        self, valid_tokens_dict, valid_client_info_dict
    ):
        base_tokens = OAuthToken.model_validate(
            {
                "access_token": "file_access_token",
                "token_type": "bearer",
                "refresh_token": "file_refresh_token",
                "expires_in": 3600,
            }
        )
        base_client_info = OAuthClientInformationFull.model_validate(
            {
                "client_id": "file-client-id",
                "redirect_uris": ["http://localhost:3000/callback"],
            }
        )
        base_storage = FakeTokenStorage(
            tokens=base_tokens,
            client_info=base_client_info,
        )
        override_storage = InlineTokenStorage(
            base_storage=base_storage,
            tokens_override=OAuthToken.model_validate(
                {
                    **valid_tokens_dict,
                    "expires_in": 1800,
                }
            ),
            client_info_override=OAuthClientInformationFull.model_validate(
                valid_client_info_dict
            ),
        )

        tokens = await override_storage.get_tokens()
        client_info = await override_storage.get_client_info()

        assert tokens.access_token == "access_token"
        assert client_info.client_id == "clientid"

    async def test_falls_back_per_section_when_override_missing(self):
        base_tokens = OAuthToken.model_validate(
            {
                "access_token": "file_access_token",
                "token_type": "bearer",
                "refresh_token": "file_refresh_token",
                "expires_in": 3600,
            }
        )
        base_client_info = OAuthClientInformationFull.model_validate(
            {
                "client_id": "file-client-id",
                "redirect_uris": ["http://localhost:3000/callback"],
            }
        )
        base_storage = FakeTokenStorage(
            tokens=base_tokens,
            client_info=base_client_info,
        )
        override_storage = InlineTokenStorage(
            base_storage=base_storage,
            tokens_override=None,
            client_info_override=OAuthClientInformationFull.model_validate(
                {
                    "client_id": "config-client-id",
                    "redirect_uris": ["http://localhost:14142/callback"],
                }
            ),
        )

        tokens = await override_storage.get_tokens()
        client_info = await override_storage.get_client_info()

        assert tokens.access_token == "file_access_token"
        assert client_info.client_id == "config-client-id"

    async def test_runtime_writes_override_future_reads_and_delegate_persistence(self):
        base_storage = FakeTokenStorage()
        override_storage = InlineTokenStorage(
            base_storage=base_storage,
            tokens_override=OAuthToken.model_validate(
                {
                    "access_token": "initial_access_token",
                    "token_type": "bearer",
                    "refresh_token": "initial_refresh_token",
                    "expires_in": 120,
                }
            ),
            client_info_override=OAuthClientInformationFull.model_validate(
                {
                    "client_id": "initial-client-id",
                    "redirect_uris": ["http://localhost:14142/callback"],
                }
            ),
        )
        new_tokens = OAuthToken.model_validate(
            {
                "access_token": "new_access_token",
                "token_type": "bearer",
                "refresh_token": "new_refresh_token",
                "expires_in": 7200,
            }
        )
        new_client_info = OAuthClientInformationFull.model_validate(
            {
                "client_id": "new-client-id",
                "redirect_uris": ["http://localhost:15151/callback"],
            }
        )

        await override_storage.set_tokens(new_tokens)
        await override_storage.set_client_info(new_client_info)

        assert (await override_storage.get_tokens()).access_token == "new_access_token"
        assert (await override_storage.get_client_info()).client_id == "new-client-id"
        assert base_storage.set_tokens_calls == [new_tokens]
        assert base_storage.set_client_info_calls == [new_client_info]


class TestFileTokenStorage:
    def _write_token_file(self, tmp_path, server_name, data):
        token_file = tmp_path / "tokens" / f"{server_name}.json"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(json.dumps(data), encoding="utf-8")
        return token_file

    def test_loads_legacy_client_info_without_issuer(self, tmp_path, monkeypatch):
        """v1-era stored client info (no issuer) must still load and stay usable."""
        monkeypatch.setenv("AGENTCREW_PERSISTENCE_DIR", str(tmp_path))
        self._write_token_file(
            tmp_path,
            "server1",
            {
                "tokens": {
                    "access_token": "legacy_token",
                    "token_type": "bearer",
                    "refresh_token": "refresh_token",
                    "expires_in": 3600,
                },
                "client_info": {
                    "client_id": "legacy-client",
                    "redirect_uris": ["http://localhost:14142/callback"],
                },
            },
        )
        storage = FileTokenStorage("server1")

        async def run():
            tokens = await storage.get_tokens()
            client_info = await storage.get_client_info()
            assert tokens.access_token == "legacy_token"
            assert client_info.client_id == "legacy-client"
            assert client_info.issuer is None

        asyncio.run(run())

    def test_loads_issuer_bearing_client_info(self, tmp_path, monkeypatch):
        """Newly persisted issuer-bound client info round-trips."""
        monkeypatch.setenv("AGENTCREW_PERSISTENCE_DIR", str(tmp_path))
        self._write_token_file(
            tmp_path,
            "server1",
            {
                "tokens": None,
                "client_info": {
                    "client_id": "new-client",
                    "redirect_uris": ["http://localhost:14142/callback"],
                    "issuer": "https://as.example.com",
                },
            },
        )
        storage = FileTokenStorage("server1")

        async def run():
            client_info = await storage.get_client_info()
            assert client_info.client_id == "new-client"
            assert client_info.issuer == "https://as.example.com"

        asyncio.run(run())

    def test_corrupt_token_file_loads_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTCREW_PERSISTENCE_DIR", str(tmp_path))
        (tmp_path / "tokens").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tokens" / "server1.json").write_text("{not json", encoding="utf-8")
        storage = FileTokenStorage("server1")

        async def run():
            assert await storage.get_tokens() is None
            assert await storage.get_client_info() is None

        asyncio.run(run())


class TestOAuthCallback:
    def test_wait_for_callback_returns_authorization_code_result_with_iss(self):
        server = OAuthCallbackServer(host="localhost", port=0)
        server.set_result("code123", "state456", None, "https://as.example.com")

        result = asyncio.run(server.wait_for_callback(timeout=5))

        assert isinstance(result, AuthorizationCodeResult)
        assert result.code == "code123"
        assert result.state == "state456"
        assert result.iss == "https://as.example.com"

    def test_wait_for_callback_raises_on_error_result(self):
        server = OAuthCallbackServer(host="localhost", port=0)
        server.set_result(None, None, "access_denied")

        with pytest.raises(RuntimeError, match="access_denied"):
            asyncio.run(server.wait_for_callback(timeout=5))

    def test_wait_for_callback_times_out(self):
        server = OAuthCallbackServer(host="localhost", port=0)

        with pytest.raises(TimeoutError, match="authorization timeout"):
            asyncio.run(server.wait_for_callback(timeout=0.5))


class FakeResourceSession:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.read_resource_calls = []

    async def read_resource(self, uri):
        self.read_resource_calls.append(str(uri))
        if self.error:
            raise self.error
        return self.result


class FakeFileHandler:
    def __init__(self, result=None):
        self.result = result
        self.processed_paths = []

    async def async_process_file(self, file_path):
        self.processed_paths.append(file_path)
        return self.result


class FakeListResourcesSession:
    def __init__(self, pages):
        self.pages = pages
        self.list_resources_calls = []

    async def list_resources(self, cursor=None, params=None):
        effective_cursor = cursor or getattr(params, "cursor", None)
        self.list_resources_calls.append(effective_cursor)
        return self.pages[effective_cursor or "first"]


class FakeDiscoverySession:
    def __init__(self, tools=None, resources_pages=None, prompts=None):
        self.tools = tools or []
        self.resources_pages = resources_pages or {}
        self.prompts = prompts or []

    async def list_tools(self, params=None):
        return ListToolsResult(tools=self.tools)

    async def list_resources(self, cursor=None, params=None):
        effective_cursor = cursor or getattr(params, "cursor", None)
        return self.resources_pages[effective_cursor or "first"]

    async def list_prompts(self, params=None):
        return ListPromptsResult(prompts=self.prompts)


class RecordingScope:
    def __init__(self):
        self.closed = 0

    async def aclose(self, primary_error=None):
        self.closed += 1


def _stub_create_session(session, scope):
    async def _create(server_config):
        return session, scope

    return _create


class FakeTransportCtx:
    """Async context manager double for an MCP transport."""

    def __init__(self, exit_error=None, exit_cancel=False):
        self.entered = 0
        self.exited = 0
        self.exit_error = exit_error
        self.exit_cancel = exit_cancel

    async def __aenter__(self):
        self.entered += 1
        return (None, None)

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        if self.exit_cancel:
            raise asyncio.CancelledError()
        if self.exit_error:
            raise self.exit_error


class FakeSession:
    """ClientSession double recording lifecycle and optional failures."""

    def __init__(
        self,
        initialize_error=None,
        call_tool_error=None,
        call_tool_result=None,
    ):
        self.entered = 0
        self.exited = 0
        self.initialize_calls = 0
        self.initialize_hang = False
        self.initialize_error = initialize_error
        self.call_tool_error = call_tool_error
        self.call_tool_result = call_tool_result
        self.call_tool_hang = False
        self.exit_cancel = False
        self.protocol_version = "2025-11-25"

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        if self.exit_cancel:
            raise asyncio.CancelledError()

    async def initialize(self):
        self.initialize_calls += 1
        if self.initialize_hang:
            await asyncio.Event().wait()
        if self.initialize_error:
            raise self.initialize_error

    async def call_tool(self, name, arguments=None, **kwargs):
        if self.call_tool_hang:
            await asyncio.Event().wait()
        if self.call_tool_error:
            raise self.call_tool_error
        return self.call_tool_result


class FakeHttpClient:
    """httpx2.AsyncClient double recording enter/close exactly once."""

    def __init__(self):
        self.entered = 0
        self.closed = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aclose(self):
        self.closed += 1


async def _noop_sleep(*args, **kwargs):
    return None


class TestMCPService:
    def test_build_token_storage_returns_base_storage_without_oauth(self):
        service = MCPService()
        base_storage = FakeTokenStorage()
        server_config = MCPServerConfig(
            name="server1",
            command="python",
            args=["test_server.py"],
            enabledForAgents=["Engineer"],
        )

        service._get_or_create_token_storage = lambda _server_name: base_storage

        token_storage = service._build_token_storage(server_config)

        assert token_storage is base_storage

    def test_build_token_storage_wraps_base_storage_with_oauth_override(
        self, valid_client_info_dict
    ):
        service = MCPService()
        base_storage = FakeTokenStorage()
        server_config = MCPServerConfig(
            name="server1",
            command="python",
            args=["test_server.py"],
            enabledForAgents=["Engineer"],
            oauth=MCPOAuthOverrideConfig(
                client_info=OAuthClientInformationFull.model_validate(
                    valid_client_info_dict
                )
            ),
        )

        service._get_or_create_token_storage = lambda _server_name: base_storage

        token_storage = service._build_token_storage(server_config)

        assert isinstance(token_storage, InlineTokenStorage)
        assert token_storage.base_storage is base_storage

    @pytest.mark.asyncio
    async def test_discover_server_tools_lists_tools_resources_and_prompts(self):
        service = MCPService()
        session = FakeDiscoverySession(
            tools=[
                Tool(
                    name="read",
                    description="Read a file",
                    input_schema={"type": "object"},
                )
            ],
            resources_pages={
                "first": ListResourcesResult(
                    resources=[
                        Resource(
                            uri="file:///tmp/guide.md",
                            name="guide.md",
                            description="Guide",
                            mime_type="text/markdown",
                        )
                    ],
                    next_cursor="page2",
                ),
                "page2": ListResourcesResult(
                    resources=[
                        Resource(
                            uri="file:///tmp/other.md",
                            name="other.md",
                            mime_type="text/markdown",
                        )
                    ]
                ),
            },
            prompts=[Prompt(name="summarize", description="Summarize the guide")],
        )
        scope = RecordingScope()
        service._create_session = _stub_create_session(session, scope)
        server_config = MCPServerConfig(
            name="docs",
            command="python",
            args=["server.py"],
            enabledForAgents=["Engineer"],
        )

        tools = await service.discover_server_tools(server_config, "Engineer")

        assert [tool.name for tool in tools] == ["read"]
        assert "read" in service.tools_cache["docs"]
        assert service.server_resources["docs"] == [
            {
                "uri": "file:///tmp/guide.md",
                "name": "guide.md",
                "description": "Guide",
                "mimeType": "text/markdown",
            },
            {
                "uri": "file:///tmp/other.md",
                "name": "other.md",
                "mimeType": "text/markdown",
            },
        ]
        assert [prompt.name for prompt in service.server_prompts["docs"]] == [
            "summarize"
        ]
        assert scope.closed == 1

    @pytest.mark.asyncio
    async def test_get_resource_handler_uses_string_uri_and_closes_session(self):
        service = MCPService()
        service.server_resources["docs"] = [
            {
                "uri": "file:///tmp/guide.md",
                "name": "guide.md",
                "mimeType": "text/markdown",
            }
        ]
        service._get_server_config = lambda server_name: MCPServerConfig(
            name="docs",
            command="python",
            args=["server.py"],
            enabledForAgents=["Engineer"],
        )
        session = FakeResourceSession(
            ReadResourceResult(
                contents=[
                    TextResourceContents(
                        uri="file:///tmp/guide.md",
                        mime_type="text/plain",
                        text="guide",
                    )
                ]
            )
        )
        scope = RecordingScope()
        service._create_session = _stub_create_session(session, scope)
        captured = {}

        async def fake_format(resource_link, active_session):
            captured["uri"] = resource_link.uri
            captured["mime_type"] = resource_link.mime_type
            captured["session"] = active_session
            return [{"type": "text", "text": str(resource_link.uri)}]

        service._format_resource_link_async = fake_format

        handler = service._create_get_resource_handler("docs", "docs")()
        result = await handler("file:///tmp/guide.md")

        assert result == [{"type": "text", "text": "file:///tmp/guide.md"}]
        assert captured["uri"] == "file:///tmp/guide.md"
        assert isinstance(captured["uri"], str)
        assert captured["mime_type"] == "text/markdown"
        assert captured["session"] is session
        assert scope.closed == 1

    def test_mcp_resources_prompt_lists_server_scoped_resource_tools(self):
        agent = SimpleNamespace(
            services={},
            name="Engineer",
            mcp_resources={
                "docs": [
                    {
                        "uri": "file:///tmp/guide.md",
                        "name": "guide.md",
                        "description": "Developer guide",
                        "mimeType": "text/markdown",
                    }
                ]
            },
        )
        context = AgentContextManager(agent)

        prompt = context._build_mcp_resources_prompt()

        assert "## MCP Resources" in prompt
        assert "Get resource tool: `docs__get_resource`" in prompt
        assert "file:///tmp/guide.md" in prompt
        assert "Developer guide" in prompt

    @pytest.mark.asyncio
    async def test_list_all_resources_paginates_with_next_cursor(self):
        service = MCPService()
        session = FakeListResourcesSession(
            {
                "first": ListResourcesResult(
                    resources=[
                        Resource(
                            uri="file:///a.txt",
                            name="a",
                            mime_type="text/plain",
                        )
                    ],
                    next_cursor="page2",
                ),
                "page2": ListResourcesResult(
                    resources=[
                        Resource(
                            uri="file:///b.txt",
                            name="b",
                            mime_type="text/plain",
                        )
                    ]
                ),
            }
        )

        resources = await service._list_all_resources(session)

        assert [resource.name for resource in resources] == ["a", "b"]
        assert session.list_resources_calls == [None, "page2"]

    def test_format_resource_for_agent_keeps_mime_type_output_key(self):
        service = MCPService()

        data = service._format_resource_for_agent(
            Resource(
                uri="file:///guide.md",
                name="guide.md",
                mime_type="text/markdown",
            )
        )

        assert data == {
            "uri": "file:///guide.md",
            "name": "guide.md",
            "mimeType": "text/markdown",
        }

    @pytest.mark.asyncio
    async def test_format_contents_async_keeps_text_and_image_behavior(self):
        service = MCPService()

        formatted = await service._format_contents_async(
            [
                TextContent(type="text", text="hello"),
                ImageContent(type="image", data="abc", mime_type="image/png"),
            ]
        )

        assert formatted == [
            {"type": "text", "text": "hello"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc"},
            },
        ]

    @pytest.mark.asyncio
    async def test_format_contents_async_resource_link_reads_and_processes_text_resource(
        self,
    ):
        service = MCPService()
        processed_output = {"type": "text", "text": "processed content"}
        fake_file_handler = FakeFileHandler(result=processed_output)
        service._get_file_handler = lambda: fake_file_handler
        resource_link = ResourceLink(
            type="resource_link",
            uri="file:///tmp/example.txt",
            name="example.txt",
            mime_type="text/plain",
        )
        session = FakeResourceSession(
            ReadResourceResult(
                contents=[
                    TextResourceContents(
                        uri="file:///tmp/example.txt",
                        mime_type="text/plain",
                        text="resource text",
                    )
                ]
            )
        )

        formatted = await service._format_contents_async([resource_link], session)

        assert session.read_resource_calls == ["file:///tmp/example.txt"]
        assert formatted == [processed_output]
        assert len(fake_file_handler.processed_paths) == 1
        assert fake_file_handler.processed_paths[0].endswith(".txt")

    @pytest.mark.asyncio
    async def test_format_contents_async_resource_link_falls_back_for_unsupported_mime(
        self,
    ):
        service = MCPService()
        service._get_file_handler = lambda: FakeFileHandler(result=None)
        resource_link = ResourceLink(
            type="resource_link",
            uri="file:///tmp/data.bin",
            name="data.bin",
            mime_type="application/octet-stream",
        )
        session = FakeResourceSession(
            ReadResourceResult(
                contents=[
                    BlobResourceContents(
                        uri="file:///tmp/data.bin",
                        mime_type="application/octet-stream",
                        blob=base64.b64encode(b"binary").decode("utf-8"),
                    )
                ]
            )
        )

        formatted = await service._format_contents_async([resource_link], session)

        assert formatted[0]["type"] == "text"
        assert "MCP resource link could not be processed" in formatted[0]["text"]
        assert "file:///tmp/data.bin" in formatted[0]["text"]
        assert "application/octet-stream" in formatted[0]["text"]

    @pytest.mark.asyncio
    async def test_format_contents_async_resource_link_image_falls_back_to_image_format(
        self,
    ):
        service = MCPService()
        service._get_file_handler = lambda: FakeFileHandler(result=None)
        image_data = base64.b64encode(b"image-bytes").decode("utf-8")
        resource_link = ResourceLink(
            type="resource_link",
            uri="file:///tmp/image.png",
            name="image.png",
            mime_type="image/png",
        )
        session = FakeResourceSession(
            ReadResourceResult(
                contents=[
                    BlobResourceContents(
                        uri="file:///tmp/image.png",
                        mime_type="image/png",
                        blob=image_data,
                    )
                ]
            )
        )

        formatted = await service._format_contents_async([resource_link], session)

        assert formatted == [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_data}"},
            }
        ]

    @pytest.mark.asyncio
    async def test_format_contents_async_resource_link_read_failure_falls_back(self):
        service = MCPService()
        resource_link = ResourceLink(
            type="resource_link",
            uri="file:///tmp/fail.txt",
            name="fail.txt",
            mime_type="text/plain",
        )
        session = FakeResourceSession(error=RuntimeError("read failed"))

        formatted = await service._format_contents_async([resource_link], session)

        assert session.read_resource_calls == ["file:///tmp/fail.txt"]
        assert formatted[0]["type"] == "text"
        assert "read failed" in formatted[0]["text"]


class TestMCPSessionLifecycle:
    def _stdio_config(self):
        return MCPServerConfig(
            name="server1",
            command="python",
            args=["server.py"],
            enabledForAgents=["Engineer"],
        )

    def _stub_stdio_build(self, monkeypatch, transport, session):
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.stdio_client",
            lambda *args, **kwargs: transport,
        )
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.ClientSession",
            lambda *args, **kwargs: session,
        )

    @pytest.mark.asyncio
    async def test_success_path_closes_resources_exactly_once(self, monkeypatch):
        service = MCPService()
        transport = FakeTransportCtx()
        session = FakeSession()
        self._stub_stdio_build(monkeypatch, transport, session)

        created_session, scope = await service._create_session(self._stdio_config())

        assert created_session is session
        assert session.entered == 1
        assert transport.entered == 1
        assert session.initialize_calls == 1

        await service._close_session(created_session, scope)
        await service._close_session(created_session, scope)

        assert session.exited == 1
        assert transport.exited == 1

    @pytest.mark.asyncio
    async def test_initialization_failure_propagates_cleans_up_no_leaked_tasks(
        self, monkeypatch
    ):
        service = MCPService()
        transport = FakeTransportCtx()
        session = FakeSession(initialize_error=RuntimeError("init failed"))
        self._stub_stdio_build(monkeypatch, transport, session)
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

        with pytest.raises(RuntimeError, match="init failed"):
            await service._create_session(self._stdio_config())

        assert session.entered == 1
        assert session.exited == 1
        assert transport.entered == 1
        assert transport.exited == 1
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        assert pending == []

    @pytest.mark.asyncio
    async def test_teardown_failure_does_not_mask_primary_error(self, monkeypatch):
        """Regression: an OAuth registration failure used to surface as an AnyIO
        cancel-scope RuntimeError from teardown and leave A2A callers awaiting
        indefinitely. The primary error must propagate and cleanup must finish.
        """
        service = MCPService()
        transport = FakeTransportCtx(
            exit_error=RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            )
        )
        session = FakeSession(initialize_error=RuntimeError("registration failed: 307"))
        self._stub_stdio_build(monkeypatch, transport, session)
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

        with pytest.raises(RuntimeError, match="registration failed: 307"):
            await service._create_session(self._stdio_config())

        assert session.exited == 1
        assert transport.exited == 1
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        assert pending == []

    @pytest.mark.asyncio
    async def test_cancellation_during_initialize_propagates_and_cleans_up(
        self, monkeypatch
    ):
        service = MCPService()
        transport = FakeTransportCtx()
        session = FakeSession()
        session.initialize_hang = True
        self._stub_stdio_build(monkeypatch, transport, session)

        async def scenario():
            task = asyncio.create_task(service._create_session(self._stdio_config()))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await scenario()

        assert session.exited == 1
        assert transport.exited == 1

    @pytest.mark.asyncio
    async def test_call_tool_stateless_converts_mcp_error_to_error_result(
        self, monkeypatch
    ):
        service = MCPService()
        session = FakeSession(
            call_tool_error=MCPError(-32001, "Request 'tools/call' timed out")
        )
        scope = RecordingScope()
        service._create_session = _stub_create_session(session, scope)

        result = await service.call_tool_stateless(self._stdio_config(), "tool1", {})

        assert result["status"] == "error"
        assert "timed out" in result["content"]
        assert scope.closed == 1

    @pytest.mark.asyncio
    async def test_streamable_session_builds_httpx2_client_and_closes_it(
        self,
        monkeypatch,
    ):
        service = MCPService()
        transport = FakeTransportCtx()
        session = FakeSession()
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.streamable_http_client",
            lambda *args, **kwargs: transport,
        )
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.ClientSession",
            lambda *args, **kwargs: session,
        )
        service._build_token_storage = lambda server_config: FakeTokenStorage()
        server_config = MCPServerConfig(
            name="remote",
            command="",
            args=[],
            enabledForAgents=["Engineer"],
            streaming_server=True,
            url="https://mcp.example.com/mcp",
        )

        created_session, scope = await service._create_session(server_config)

        assert created_session is session
        assert isinstance(scope.http_client, httpx2.AsyncClient)
        assert not scope.http_client.is_closed
        http_client = scope.http_client

        await service._close_session(created_session, scope)

        assert http_client.is_closed
        assert scope.http_client is None
        assert session.exited == 1
        assert transport.exited == 1


class TestScopeTeardownCancellation:
    """Cancellation-safe teardown: every acquired resource closes in strict
    reverse order even when an exit raises CancelledError, ownership fields
    are cleared, primary errors are never masked, and repeated close stays
    idempotent after a failed/cancelled exit."""

    def _stdio_config(self):
        return MCPServerConfig(
            name="server1",
            command="python",
            args=["server.py"],
            enabledForAgents=["Engineer"],
        )

    async def _enter_all(self, scope, session, transport, http_client):
        await scope.enter_http_client(http_client)
        await scope.enter_transport(transport)
        await scope.enter_session(session)

    @pytest.mark.asyncio
    async def test_session_exit_cancel_still_closes_transport_and_http_client(self):
        scope = _MCPSessionScope("server1")
        session = FakeSession()
        session.exit_cancel = True
        transport = FakeTransportCtx()
        http_client = FakeHttpClient()
        await self._enter_all(scope, session, transport, http_client)

        with pytest.raises(asyncio.CancelledError):
            await scope.aclose()

        assert session.exited == 1
        assert transport.exited == 1
        assert http_client.closed == 1
        assert scope.session is None
        assert scope.transport_ctx is None
        assert scope.http_client is None

    @pytest.mark.asyncio
    async def test_transport_exit_cancel_still_closes_http_client(self):
        scope = _MCPSessionScope("server1")
        session = FakeSession()
        transport = FakeTransportCtx(exit_cancel=True)
        http_client = FakeHttpClient()
        await self._enter_all(scope, session, transport, http_client)

        with pytest.raises(asyncio.CancelledError):
            await scope.aclose()

        assert session.exited == 1
        assert transport.exited == 1
        assert http_client.closed == 1
        assert scope.session is None
        assert scope.transport_ctx is None
        assert scope.http_client is None

    @pytest.mark.asyncio
    async def test_aclose_with_primary_error_swallows_teardown_cancellation(self):
        scope = _MCPSessionScope("server1")
        session = FakeSession()
        session.exit_cancel = True
        transport = FakeTransportCtx(exit_cancel=True)
        http_client = FakeHttpClient()
        await self._enter_all(scope, session, transport, http_client)

        await scope.aclose(primary_error=RuntimeError("registration failed: 307"))

        assert session.exited == 1
        assert transport.exited == 1
        assert http_client.closed == 1
        assert scope.session is None
        assert scope.transport_ctx is None
        assert scope.http_client is None

    @pytest.mark.asyncio
    async def test_aclose_idempotent_after_cancelled_exit(self):
        scope = _MCPSessionScope("server1")
        session = FakeSession()
        session.exit_cancel = True
        transport = FakeTransportCtx()
        http_client = FakeHttpClient()
        await self._enter_all(scope, session, transport, http_client)

        with pytest.raises(asyncio.CancelledError):
            await scope.aclose()
        await scope.aclose()

        assert session.exited == 1
        assert transport.exited == 1
        assert http_client.closed == 1
        assert scope.session is None
        assert scope.transport_ctx is None
        assert scope.http_client is None

    @pytest.mark.asyncio
    async def test_primary_oauth_error_not_masked_by_teardown_cancellation(
        self, monkeypatch
    ):
        """Historical regression: an OAuth registration failure followed by a
        cancelled teardown must surface the primary error, never CancelledError,
        with full cleanup and no leaked tasks."""
        service = MCPService()
        transport = FakeTransportCtx(exit_cancel=True)
        session = FakeSession(initialize_error=RuntimeError("registration failed: 307"))
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.stdio_client",
            lambda *args, **kwargs: transport,
        )
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.ClientSession",
            lambda *args, **kwargs: session,
        )
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

        with pytest.raises(RuntimeError, match="registration failed: 307"):
            await service._create_session(self._stdio_config())

        assert session.exited == 1
        assert transport.exited == 1
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        assert pending == []

    @pytest.mark.asyncio
    async def test_external_cancellation_propagates_after_full_cleanup(
        self, monkeypatch
    ):
        """Externally initiated cancellation during a tool call still
        propagates after the finally-path cleanup completes."""
        service = MCPService()
        transport = FakeTransportCtx()
        session = FakeSession()
        session.call_tool_hang = True
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.stdio_client",
            lambda *args, **kwargs: transport,
        )
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.service.ClientSession",
            lambda *args, **kwargs: session,
        )

        async def scenario():
            task = asyncio.create_task(
                service.call_tool_stateless(self._stdio_config(), "tool1", {})
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await scenario()

        assert session.exited == 1
        assert transport.exited == 1

    @pytest.mark.asyncio
    async def test_close_session_does_not_mask_primary_error_in_finally_path(self):
        """A teardown CancelledError must not mask the primary tool error when
        closing from a finally block."""
        service = MCPService()
        session = FakeSession(call_tool_error=RuntimeError("tool failed"))
        transport = FakeTransportCtx(exit_cancel=True)
        scope = _MCPSessionScope("server1")
        await scope.enter_session(session)
        await scope.enter_transport(transport)

        async def fake_create(server_config):
            return session, scope

        service._create_session = fake_create

        result = await service.call_tool_stateless(self._stdio_config(), "tool1", {})

        assert result["status"] == "error"
        assert "tool failed" in result["content"]
        assert session.exited == 1
        assert transport.exited == 1
        assert scope.session is None
        assert scope.transport_ctx is None

    @pytest.mark.asyncio
    async def test_tool_handler_failure_terminates_within_bound_no_pending_tasks(
        self,
    ):
        """Bounded regression: an MCP auth/transport failure must not leave the
        A2A-facing tool execution awaiting indefinitely, and a teardown failure
        must not mask the primary error (historical: OAuthRegistrationError
        followed by AnyIO cancel-scope RuntimeError and a hanging A2A task)."""
        service = MCPService()
        session = FakeSession(call_tool_error=RuntimeError("registration failed: 307"))
        transport = FakeTransportCtx(
            exit_error=RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            )
        )
        scope = _MCPSessionScope("server1")
        await scope.enter_session(session)
        await scope.enter_transport(transport)

        async def fake_create(server_config):
            return session, scope

        service._create_session = fake_create
        handler = service._create_stateless_tool_handler(
            self._stdio_config(), "tool1"
        )()

        result = await asyncio.wait_for(handler(x=1), timeout=5)

        assert result == [
            {
                "type": "text",
                "text": "Error calling MCP tool 'tool1': registration failed: 307",
            }
        ]
        assert session.exited == 1
        assert transport.exited == 1
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        assert pending == []

    @pytest.mark.asyncio
    async def test_tool_handler_teardown_cancellation_still_terminates(self):
        """A CancelledError from teardown after a handled tool error terminates
        within the bound with full cleanup and no leaked tasks (no hang)."""
        service = MCPService()
        session = FakeSession(call_tool_error=RuntimeError("tool failed"))
        transport = FakeTransportCtx(exit_cancel=True)
        scope = _MCPSessionScope("server1")
        await scope.enter_session(session)
        await scope.enter_transport(transport)

        async def fake_create(server_config):
            return session, scope

        service._create_session = fake_create
        handler = service._create_stateless_tool_handler(
            self._stdio_config(), "tool1"
        )()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handler(x=1), timeout=5)

        assert session.exited == 1
        assert transport.exited == 1
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        assert pending == []
