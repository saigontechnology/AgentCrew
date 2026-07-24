from __future__ import annotations

import asyncio
import base64
import os
import random
import tempfile
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from loguru import logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    BlobResourceContents,
    ImageContent,
    PaginatedRequestParams,
    Resource,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from AgentCrew.modules import FileLogIO
from AgentCrew.modules.agents import AgentManager, LocalAgent
from AgentCrew.modules.utils.file_handler import optimize_image_data_uri

from .auth import FileTokenStorage, InlineTokenStorage, OAuthClientResolver

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from mcp.types import ContentBlock, Prompt, Tool

    from AgentCrew.modules.utils.file_handler import FileHandler

    from .config import MCPConfigManager, MCPServerConfig

# Initialize the logger
mcp_log_io = FileLogIO("mcpclient_agentcrew")


class MCPService:
    """Stateless service for interacting with Model Context Protocol (MCP) servers.

    Each tool call creates a temporary MCP session, executes the tool, and tears
    down the connection immediately. No persistent event loop thread, no
    keepalive workers, no eager server startup. Tool definitions are discovered
    once (brief connect → list → disconnect) and cached so the LLM knows what
    tools exist, but handlers are lazy — each invocation creates a fresh
    connection.
    """

    def __init__(self):
        """Initialize the stateless MCP service."""
        self.tools_cache: dict[str, dict[str, Tool]] = {}
        self.server_prompts: dict[str, list[Prompt]] = {}
        self.server_resources: dict[str, list[dict[str, Any]]] = {}
        self.tokens_storage_cache: dict[str, FileTokenStorage] = {}
        self.file_handler: FileHandler | None = None
        self._config_manager: MCPConfigManager | None = None
        self._acp_server_configs: dict[str, MCPServerConfig] = {}
        self._oauth_locks: dict[str, asyncio.Lock] = {}

    def _get_file_handler(self) -> FileHandler:
        if self.file_handler is None:
            from AgentCrew.modules.utils.file_handler import FileHandler

            self.file_handler = FileHandler()
        return self.file_handler

    def _get_server_config(
        self, server_name: str, agent_name: str | None = None
    ) -> MCPServerConfig | None:
        """Look up a server config by name from the config manager or ACP cache."""
        if self._config_manager is not None:
            for config in self._config_manager.configs.values():
                if config.name == server_name and (
                    agent_name is None or agent_name in config.enabledForAgents
                ):
                    return config
        for config in self._acp_server_configs.values():
            if config.name == server_name:
                return config
        return None

    def _get_oauth_lock(self, server_name: str) -> asyncio.Lock:
        """Get or create a per-server asyncio.Lock for serializing OAuth flows."""
        if server_name not in self._oauth_locks:
            self._oauth_locks[server_name] = asyncio.Lock()
        return self._oauth_locks[server_name]

    async def _create_session(
        self, server_config: MCPServerConfig
    ) -> tuple[ClientSession, Any]:
        """Create a temporary MCP client session.

        For streaming (HTTP/SSE) servers, session creation is serialized per
        server via an asyncio.Lock. This prevents concurrent OAuth flows from
        conflicting on the same callback port: the first call triggers OAuth
        and saves the token, while subsequent calls wait for the lock, then
        find a valid token and skip OAuth entirely.

        Returns ``(session, ctx)`` where ``ctx`` is the transport context
        manager. The caller must call :meth:`_close_session` when done.
        """
        if server_config.streaming_server:
            lock = self._get_oauth_lock(server_config.name)
            async with lock:
                return await self._create_session_impl(server_config)
        return await self._create_session_impl(server_config)

    async def _create_session_impl(
        self, server_config: MCPServerConfig
    ) -> tuple[ClientSession, Any]:
        """Internal session creation (called under per-server OAuth lock for streaming servers)."""
        if server_config.streaming_server:
            headers = server_config.headers or {}
            token_storage = self._build_token_storage(server_config)
            client_info = await token_storage.get_client_info()
            if client_info and client_info.redirect_uris:
                port = client_info.redirect_uris[0].port
            else:
                port = random.randint(14100, 14200)
            oauth_resolver = OAuthClientResolver(port=port)

            if server_config.url.endswith("/sse"):
                ctx = sse_client(
                    server_config.url,
                    headers=headers,
                    auth=oauth_resolver.get_oauth_client_provider(
                        server_config.url, token_storage
                    ),
                    sse_read_timeout=60 * 60 * 24,
                )
            else:
                from httpx import AsyncClient, Timeout

                ctx = streamable_http_client(
                    server_config.url,
                    http_client=AsyncClient(
                        headers=headers,
                        auth=oauth_resolver.get_oauth_client_provider(
                            server_config.url, token_storage
                        ),
                        timeout=Timeout(60, read=60 * 60 * 24),
                    ),
                )
        else:
            server_params = StdioServerParameters(
                command=server_config.command,
                args=server_config.args,
                env=server_config.env,
            )
            ctx = stdio_client(server_params, errlog=mcp_log_io)

        stream_context = await ctx.__aenter__()
        read_stream, write_stream = stream_context[0], stream_context[1]
        session = ClientSession(read_stream, write_stream)
        try:
            await session.__aenter__()
        except Exception:
            await ctx.__aexit__(None, None, None)
            raise

        retried = 0
        while retried < 3:
            try:
                await session.initialize()
                return session, ctx
            except Exception:
                retried += 1
                if retried >= 3:
                    await session.__aexit__(None, None, None)
                    await ctx.__aexit__(None, None, None)
                    raise
                logger.warning(
                    f"MCPService: Retrying connection to '{server_config.name}' "
                    f"(attempt {retried + 1}/3)"
                )
                await asyncio.sleep(retried * 2)
        return session, ctx

    async def _close_session(self, session: ClientSession, ctx: Any) -> None:
        """Close a temporary session and its transport context."""
        try:
            await session.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"MCPService: Error closing session: {e}")
        try:
            await ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"MCPService: Error closing transport: {e}")

    async def discover_server_tools(
        self, server_config: MCPServerConfig, agent_name: str
    ) -> list[Tool]:
        """One-time discovery: connect, list tools + resources + prompts, cache, disconnect.

        Briefly connects to the server, discovers all tools, resources, and
        prompts, caches them, and disconnects. Does **not** register tools on
        agents — the caller does that separately via
        :meth:`register_tools_for_agent`.
        """
        try:
            session, ctx = await self._create_session(server_config)
            try:
                response = await session.list_tools()
                self.tools_cache[server_config.name] = {
                    tool.name: tool for tool in response.tools
                }

                try:
                    resources = await self._list_all_resources(session)
                    self.server_resources[server_config.name] = [
                        self._format_resource_for_agent(r) for r in resources
                    ]
                except Exception:
                    logger.debug(
                        f"MCPService: No resources from '{server_config.name}'"
                    )

                try:
                    prompts_response = await session.list_prompts()
                    self.server_prompts[server_config.name] = prompts_response.prompts
                except Exception:
                    logger.debug(f"MCPService: No prompts from '{server_config.name}'")

                logger.info(
                    f"MCPService: Discovery complete for '{server_config.name}': "
                    f"{len(response.tools)} tools, "
                    f"{len(self.server_resources.get(server_config.name, []))} resources, "
                    f"{len(self.server_prompts.get(server_config.name, []))} prompts"
                )
                return response.tools
            finally:
                await self._close_session(session, ctx)
        except Exception as e:
            logger.exception(
                f"MCPService: Discovery failed for '{server_config.name}': {e}"
            )
            return []

    async def register_tools_for_agent(
        self, server_config: MCPServerConfig, agent_name: str
    ) -> None:
        """Discover tools and register definitions + lazy handlers on the agent.

        Uses cached tool definitions when available to avoid repeated MCP
        discovery calls on re-activation. Tracks loading state in
        ``agent.mcps_loading`` so callers can defer tool registration until
        discovery completes.
        """
        combined_server_id = self._get_server_id_format(server_config.name, agent_name)

        agent_manager = AgentManager.get_instance()
        agent = agent_manager.get_local_agent(agent_name)
        if not agent:
            return

        is_cached = server_config.name in self.tools_cache

        if not is_cached:
            agent.mcps_loading.append(combined_server_id)

        try:
            if is_cached:
                tools = list(self.tools_cache[server_config.name].values())
                logger.info(
                    f"MCPService: Using cached tool definitions for "
                    f"'{server_config.name}' ({len(tools)} tools)"
                )
            else:
                tools = await self.discover_server_tools(server_config, agent_name)

            if not tools:
                logger.warning(
                    f"MCPService: No tools discovered for '{server_config.name}'"
                )
                return

            filtered_tools = self._filter_tools_for_registration(
                tools, server_config.includeTools, server_config.name
            )

            if self.server_resources.get(server_config.name):
                agent.mcp_resources[server_config.name] = self.server_resources[
                    server_config.name
                ]
                self._register_get_resource_tool(
                    agent, combined_server_id, server_config.name
                )

            for tool in filtered_tools:

                def tool_definition_factory(
                    tool_info=tool, srv_name=server_config.name
                ):
                    def get_definition():
                        return self._format_tool_definition(tool_info, srv_name)

                    return get_definition

                handler_factory = self._create_stateless_tool_handler(
                    server_config, tool.name
                )
                agent.register_tool(tool_definition_factory(), handler_factory, self)

            logger.info(
                f"MCPService: Registered {len(filtered_tools)} tools for "
                f"'{server_config.name}' on agent '{agent_name}'"
            )
        finally:
            if combined_server_id in agent.mcps_loading:
                agent.mcps_loading.remove(combined_server_id)

    def _create_stateless_tool_handler(
        self, server_config: MCPServerConfig, tool_name: str
    ) -> Callable:
        """Create a lazy tool handler that connects, executes, and disconnects per call."""

        def handler_factory(service_instance=None):
            async def actual_tool_executor(
                **params,
            ) -> list[dict[str, Any]]:
                session = None
                ctx = None
                try:
                    session, ctx = await self._create_session(server_config)
                    result = await session.call_tool(tool_name, params)
                    return await self._format_contents_async(result.content, session)
                except Exception as e:
                    logger.error(
                        f"MCPService: Stateless tool call failed for "
                        f"'{tool_name}' on '{server_config.name}': {e}"
                    )
                    return [
                        {
                            "type": "text",
                            "text": f"Error calling MCP tool '{tool_name}': {e!s}",
                        }
                    ]
                finally:
                    if session and ctx:
                        await self._close_session(session, ctx)

            return actual_tool_executor

        return handler_factory

    async def call_tool_stateless(
        self,
        server_config: MCPServerConfig,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Stateless tool call for direct API usage: connect, call, disconnect."""
        try:
            session, ctx = await self._create_session(server_config)
            try:
                result = await session.call_tool(tool_name, tool_args)
                return {
                    "content": await self._format_contents_async(
                        result.content, session
                    ),
                    "status": "success",
                }
            finally:
                await self._close_session(session, ctx)
        except Exception as e:
            return {"content": f"Error: {e!s}", "status": "error"}

    async def get_prompt_stateless(
        self,
        server_config: MCPServerConfig,
        prompt_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stateless prompt fetch: connect, get prompt, disconnect."""
        try:
            session, ctx = await self._create_session(server_config)
            try:
                result = await session.get_prompt(prompt_name, arguments)
                return {"content": result.messages, "status": "success"}
            finally:
                await self._close_session(session, ctx)
        except Exception as e:
            return {"content": f"Error: {e!s}", "status": "error"}

    def _get_server_id_format(
        self, server_name: str, agent_name: str | None = None
    ) -> str:
        """Format server ID with optional agent name prefix."""
        return f"{agent_name}__{server_name}" if agent_name else server_name

    def _target_agent_names(
        self, agent_name: str | None, server_config: MCPServerConfig
    ) -> list[str]:
        if agent_name:
            return [agent_name]
        return list(server_config.enabledForAgents)

    async def _list_all_resources(self, session: ClientSession) -> list[Resource]:
        resources: list[Resource] = []
        cursor = None
        for _ in range(50):
            response = await session.list_resources(
                params=PaginatedRequestParams(cursor=cursor)
            )
            resources.extend(response.resources)
            cursor = response.nextCursor
            if not cursor:
                return resources
        return resources

    def _format_resource_for_agent(self, resource: Resource) -> dict[str, Any]:
        data = {"uri": str(resource.uri), "name": resource.name}
        if resource.title:
            data["title"] = resource.title
        if resource.description:
            data["description"] = resource.description
        if resource.mimeType:
            data["mimeType"] = resource.mimeType
        return data

    def _get_resource_tool_definition(self, server_name: str) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": f"{server_name}__get_resource",
                "description": f"Fetch the content of an available MCP resource from server '{server_name}' by URI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": "The exact MCP resource URI to fetch.",
                        }
                    },
                    "required": ["uri"],
                },
            },
        }

    def _register_get_resource_tool(
        self, agent: LocalAgent, combined_server_id: str, server_name: str
    ) -> None:
        agent.register_tool(
            lambda: self._get_resource_tool_definition(server_name),
            self._create_get_resource_handler(combined_server_id, server_name),
            self,
        )

    def _create_get_resource_handler(
        self, combined_server_id: str, server_name: str
    ) -> Callable:
        """Create a lazy resource handler that connects on-demand per call."""

        def handler_factory(service_instance=None):
            async def get_resource(uri: str) -> list[dict[str, Any]]:
                from pydantic import AnyUrl

                resource_uri = uri.strip() if uri else ""
                if not resource_uri:
                    raise ValueError("Resource URI is required")

                resource = next(
                    (
                        res
                        for res in self.server_resources.get(server_name, [])
                        if res.get("uri") == resource_uri
                    ),
                    None,
                )
                if not resource:
                    raise ValueError(
                        f"Resource URI is not available on MCP server '{server_name}': {resource_uri}"
                    )

                server_config = self._get_server_config(server_name)
                if not server_config:
                    raise ValueError(
                        f"Cannot find config for MCP server '{server_name}'"
                    )

                resource_link = ResourceLink(
                    type="resource_link",
                    uri=AnyUrl(resource_uri),
                    name=resource.get("name", resource_uri),
                    description=resource.get("description"),
                    mimeType=resource.get("mimeType"),
                )

                session = None
                ctx = None
                try:
                    session, ctx = await self._create_session(server_config)
                    return await self._format_resource_link_async(
                        resource_link, session
                    )
                finally:
                    if session and ctx:
                        await self._close_session(session, ctx)

            return get_resource

        return handler_factory

    def _get_or_create_token_storage(self, server_name: str) -> FileTokenStorage:
        """
        Get or create a FileTokenStorage instance for a specific server.

        Args:
            server_name: Name of the MCP server

        Returns:
            FileTokenStorage instance for the server
        """
        if server_name not in self.tokens_storage_cache:
            self.tokens_storage_cache[server_name] = FileTokenStorage(server_name)
            logger.info(
                f"MCPService: Created new FileTokenStorage for server '{server_name}'"
            )
        return self.tokens_storage_cache[server_name]

    def _build_token_storage(self, server_config: MCPServerConfig):
        """Build effective token storage using cached file storage plus optional config overrides."""
        base_storage = self._get_or_create_token_storage(server_config.name)
        oauth_override = getattr(server_config, "oauth", None)
        if not oauth_override:
            return base_storage

        return InlineTokenStorage(
            base_storage=base_storage,
            tokens_override=oauth_override.tokens,
            client_info_override=oauth_override.client_info,
        )

    def _filter_tools_for_registration(
        self,
        tools: list[Tool],
        include_tools: list[str] | None,
        combined_server_id: str,
    ) -> list[Tool]:
        if not include_tools:
            return tools

        tool_map = {tool.name: tool for tool in tools}
        filtered_tools = [tool_map[name] for name in include_tools if name in tool_map]
        unmatched_tools = [name for name in include_tools if name not in tool_map]

        if unmatched_tools:
            logger.warning(
                f"MCPService: MCP server '{combined_server_id}' includeTools contains unknown tools: {', '.join(unmatched_tools)}"
            )

        if not filtered_tools:
            logger.warning(
                f"MCPService: MCP server '{combined_server_id}' matched 0 tools from includeTools filter. No tools will be registered."
            )

        return filtered_tools

    def _remove_agent_server_tool_definitions(
        self, local_agent: LocalAgent, server_name: str
    ) -> None:
        namespaced_prefix = f"{server_name}__"
        tool_names_to_remove = [
            tool_name
            for tool_name in local_agent.tool_definitions
            if tool_name.startswith(namespaced_prefix)
        ]

        if not tool_names_to_remove:
            return

        if local_agent.is_active:
            local_agent._clear_tools_from_llm()

        for tool_name in tool_names_to_remove:
            local_agent.tool_definitions.pop(tool_name, None)
        local_agent.mcp_resources.pop(server_name, None)

        if local_agent.is_active:
            local_agent.resync_tools_to_llm()

    async def deregister_server_tools(
        self, server_name: str, agent_name: str | None = None
    ):
        agent_manager = AgentManager.get_instance()
        if agent_name:
            local_agent = agent_manager.get_local_agent(agent_name)
            if not local_agent:
                return
            if server_name in self.tools_cache:
                self._remove_agent_server_tool_definitions(local_agent, server_name)
        else:
            for current_agent_name in agent_manager.agents:
                local_agent = agent_manager.get_local_agent(current_agent_name)
                if not local_agent:
                    continue

                if server_name in self.tools_cache:
                    self._remove_agent_server_tool_definitions(local_agent, server_name)

    def _format_tool_definition(self, tool: Tool, server_id: str) -> dict[str, Any]:
        """
        Format a tool definition for the tool registry.

        Args:
            tool: Tool information from the server
            server_id: ID of the server the tool belongs to
            provider: LLM provider to format for (if None, uses default format)

        Returns:
            Formatted tool definition
        """
        # Create namespaced tool name
        namespaced_name = f"{server_id}__{tool.name}"

        import json

        from jsonref import replace_refs

        merged_inputSchema_string = json.dumps(
            replace_refs(
                tool.inputSchema,
                merge_props=True,
                jsonschema=True,
            ),
            indent=2,
        )
        merged_inputSchema = json.loads(merged_inputSchema_string)
        if "$defs" in merged_inputSchema:
            del merged_inputSchema["$defs"]

        # Format for different providers
        return {
            "type": "function",
            "function": {
                "name": namespaced_name,
                "description": tool.description,
                "parameters": merged_inputSchema,
            },
        }

    async def list_tools(self, server_id: str | None = None) -> list[dict[str, Any]]:
        """List tools from cache (populated during one-time discovery).

        Args:
            server_id: Optional ID of the server to list tools from.
                If None, lists tools from all cached servers.

        Returns:
            List of tools with their metadata.
        """
        if server_id is not None:
            if server_id not in self.tools_cache:
                return []
            return [
                {
                    "name": f"{server_id}.{tool.name}",
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in self.tools_cache[server_id].values()
            ]
        else:
            all_tools = []
            for srv_id in list(self.tools_cache.keys()):
                all_tools.extend(await self.list_tools(srv_id))
            return all_tools

    async def get_prompt(
        self,
        server_id: str,
        prompt_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get a prompt from an MCP server (stateless: connect, fetch, disconnect).

        Args:
            server_id: Name of the server to get the prompt from
            prompt_name: Name of the prompt to retrieve
            arguments: Optional arguments for the prompt

        Returns:
            Dict with ``content`` and ``status`` keys.
        """
        server_config = self._get_server_config(server_id)
        if not server_config:
            return {
                "content": f"Server '{server_id}' not found in config",
                "status": "error",
            }

        try:
            session, ctx = await self._create_session(server_config)
            try:
                result = await session.get_prompt(prompt_name, arguments)
                return {"content": result.messages, "status": "success"}
            finally:
                await self._close_session(session, ctx)
        except Exception as e:
            logger.error(
                f"Error retrieving prompt '{prompt_name}' from server '{server_id}': {e!s}"
            )
            return {
                "content": f"Error retrieving prompt '{prompt_name}' on server '{server_id}': {e!s}",
                "status": "error",
            }

    async def _format_contents_async(
        self,
        content: list[ContentBlock],
        session: ClientSession | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        for c in content:
            if isinstance(c, TextContent):
                result.append(
                    {
                        "type": "text",
                        "text": c.text,
                    }
                )
            elif isinstance(c, ImageContent):
                data_uri = optimize_image_data_uri(f"data:{c.mimeType};base64,{c.data}")
                result.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    }
                )
            elif isinstance(c, ResourceLink):
                result.extend(await self._format_resource_link_async(c, session))

        return result

    async def _format_resource_link_async(
        self, resource_link: ResourceLink, session: ClientSession | None
    ) -> list[dict[str, Any]]:
        """Format a resource link using the active session (no cross-loop bridge)."""
        if session is None:
            return [
                self._resource_link_fallback(resource_link, "No MCP session available")
            ]

        try:
            resource_result = await session.read_resource(resource_link.uri)
        except Exception as e:
            return [self._resource_link_fallback(resource_link, str(e))]

        return await self._format_resource_result(resource_link, resource_result)

    async def _format_resource_result(
        self, resource_link: ResourceLink, resource_result
    ) -> list[dict[str, Any]]:
        formatted = []
        for resource_content in resource_result.contents:
            error_str = None
            try:
                processed = await self._process_resource_content(
                    resource_link, resource_content
                )
            except Exception as e:
                processed = None
                error_str = str(e)

            if processed:
                formatted.append(processed)
            else:
                image_fallback = self._resource_image_fallback(
                    resource_link, resource_content
                )
                formatted.append(
                    image_fallback
                    or self._resource_link_fallback(
                        resource_link,
                        error_str
                        or f"Unsupported MIME type: {self._resource_mime_type(resource_link, resource_content)}",
                    )
                )
        return formatted

    async def _process_resource_content(
        self,
        resource_link: ResourceLink,
        resource_content: TextResourceContents | BlobResourceContents,
    ) -> dict[str, Any] | None:
        suffix = self._resource_temp_suffix(resource_link, resource_content)
        temp_path = None

        try:
            if isinstance(resource_content, TextResourceContents):
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=suffix, delete=False
                ) as temp_file:
                    temp_file.write(resource_content.text)
                    temp_path = temp_file.name
            elif isinstance(resource_content, BlobResourceContents):
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=suffix, delete=False
                ) as temp_file:
                    temp_file.write(base64.b64decode(resource_content.blob))
                    temp_path = temp_file.name

            return await self._get_file_handler().async_process_file(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _resource_temp_suffix(
        self,
        resource_link: ResourceLink,
        resource_content: TextResourceContents | BlobResourceContents,
    ) -> str:
        uri_path = unquote(urlparse(str(resource_link.uri)).path)
        _, ext = os.path.splitext(uri_path)

        if not ext:
            name = getattr(resource_link, "name", "")
            _, ext = os.path.splitext(name)

        if not ext:
            mime_type = self._resource_mime_type(resource_link, resource_content)
            if mime_type.startswith("text/"):
                ext = ".txt"

        return ext

    def _resource_mime_type(
        self,
        resource_link: ResourceLink,
        resource_content: TextResourceContents | BlobResourceContents,
    ) -> str:
        return (
            getattr(resource_content, "mimeType", None)
            or getattr(resource_link, "mimeType", None)
            or "unknown"
        )

    def _resource_image_fallback(
        self,
        resource_link: ResourceLink,
        resource_content: TextResourceContents | BlobResourceContents,
    ) -> dict[str, Any] | None:
        mime_type = self._resource_mime_type(resource_link, resource_content)
        if not mime_type.startswith("image/") or not isinstance(
            resource_content, BlobResourceContents
        ):
            return None

        data_uri = optimize_image_data_uri(
            f"data:{mime_type};base64,{resource_content.blob}"
        )
        return {
            "type": "image_url",
            "image_url": {"url": data_uri},
        }

    def _resource_link_fallback(
        self, resource_link: ResourceLink, reason: str
    ) -> dict[str, Any]:
        name = getattr(resource_link, "name", "resource")
        uri = str(getattr(resource_link, "uri", ""))
        mime_type = getattr(resource_link, "mimeType", None) or "unknown"
        return {
            "type": "text",
            "text": (
                f"MCP resource link could not be processed: {name}\n"
                f"URI: {uri}\n"
                f"MIME type: {mime_type}\n"
                f"Reason: {reason}"
            ),
        }

    async def call_tool(
        self, server_id: str, tool_name: str, tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a tool on an MCP server (stateless).

        Args:
            server_id: Name of the server to call the tool on
            tool_name: Name of the tool to call
            tool_args: Arguments to pass to the tool

        Returns:
            Dict containing the tool's response.
        """
        server_config = self._get_server_config(server_id)
        if not server_config:
            return {
                "content": f"Server '{server_id}' not found in config",
                "status": "error",
            }
        return await self.call_tool_stateless(server_config, tool_name, tool_args)
