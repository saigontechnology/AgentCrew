from __future__ import annotations

from loguru import logger
from typing import TYPE_CHECKING
from mcp import ClientSession, StdioServerParameters
from mcp.types import ImageContent, TextContent
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client
from AgentCrew.modules.agents import LocalAgent, AgentManager
from AgentCrew.modules.tools.registry import ToolRegistry
from .auth import OAuthClientResolver, FileTokenStorage
import random
import asyncio
import threading
from AgentCrew.modules import FileLogIO

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional
    from mcp.types import ContentBlock, Prompt, Tool
    from .config import MCPServerConfig

# Initialize the logger
mcp_log_io = FileLogIO("mcpclient_agentcrew")


class MCPService:
    """Service for interacting with Model Context Protocol (MCP) servers."""

    def __init__(self):
        """Initialize the MCP service."""
        self.sessions: Dict[str, ClientSession] = {}
        self.connected_servers: Dict[str, bool] = {}
        self.tools_cache: Dict[str, Dict[str, Tool]] = {}
        self.loop = asyncio.new_event_loop()
        self._server_connection_tasks: Dict[str, asyncio.Task] = {}
        self._server_shutdown_events: Dict[str, asyncio.Event] = {}
        self.server_prompts: Dict[str, List[Prompt]] = {}
        self.tokens_storage_cache: Dict[str, FileTokenStorage] = {}

    async def _manage_single_connection(
        self, server_config: MCPServerConfig, agent_name: Optional[str] = None
    ):
        """Manages the lifecycle of a single MCP server connection."""
        server_name = server_config.name
        combined_server_id = self._get_server_id_format(server_name, agent_name)
        shutdown_event = asyncio.Event()
        self._server_shutdown_events[combined_server_id] = shutdown_event
        logger.info(f"MCPService: Starting connection management for {server_name}")

        try:
            if server_config.streaming_server:
                # Import here to avoid import errors if not available

                logger.info(
                    f"MCPService: Using streaming HTTP client for {server_name}"
                )
                # Prepare headers for the streamable HTTP client
                headers = server_config.headers if server_config.headers else {}

                # Backward compatible with SSE
                # Get or create token storage for this specific server
                token_storage = self._get_or_create_token_storage(server_name)
                client_info = await token_storage.get_client_info()
                if client_info and client_info.redirect_uris:
                    port = client_info.redirect_uris[0].port
                else:
                    port = random.randint(14100, 14200)

                oauth_resolver = OAuthClientResolver(port=port)

                if server_config.url.endswith("/sse"):
                    session_context = sse_client(
                        server_config.url,
                        headers=headers,
                        auth=oauth_resolver.get_oauth_client_provider(
                            server_config.url, token_storage
                        ),
                        sse_read_timeout=60 * 60 * 24,
                    )
                else:
                    session_context = streamablehttp_client(
                        server_config.url,
                        headers=headers,
                        auth=oauth_resolver.get_oauth_client_provider(
                            server_config.url, token_storage
                        ),
                        sse_read_timeout=60 * 60 * 24,
                    )

                async with session_context as stream_context:
                    logger.info(
                        f"MCPService: streamablehttp_client established for {server_name}"
                    )
                    read_stream = stream_context[0]
                    write_stream = stream_context[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        logger.info(
                            f"MCPService: ClientSession established for {server_name}"
                        )
                        server_info = await session.initialize()
                        self.sessions[combined_server_id] = session
                        self.connected_servers[combined_server_id] = True
                        logger.info(
                            f"MCPService: {server_name} connected. Registering tools..."
                        )

                        if agent_name:
                            await self.register_server_tools(
                                combined_server_id, server_name, agent_name
                            )
                        else:
                            for agent_name in server_config.enabledForAgents:
                                await self.register_server_tools(
                                    combined_server_id, server_name, agent_name
                                )

                        if server_info.capabilities.prompts:
                            prompts = await self.sessions[
                                combined_server_id
                            ].list_prompts()
                            self.server_prompts[server_name] = prompts.prompts

                        logger.info(
                            f"MCPService: {server_name} setup complete. Waiting for shutdown signal."
                        )
                        await shutdown_event.wait()
            else:
                # Original stdio client logic
                server_params = StdioServerParameters(
                    command=server_config.command,
                    args=server_config.args,
                    env=server_config.env,
                )

                async with stdio_client(server_params, errlog=mcp_log_io) as (
                    read_stream,
                    write_stream,
                ):
                    logger.info(
                        f"MCPService: stdio_client established for {server_name}"
                    )
                    async with ClientSession(read_stream, write_stream) as session:
                        logger.info(
                            f"MCPService: ClientSession established for {server_name}"
                        )
                        retried = 0
                        server_info = None
                        while retried < 3:
                            try:
                                server_info = await session.initialize()
                                break
                            except Exception:
                                print("Retrying MCP connection...")
                                await asyncio.sleep(retried * 2)
                                retried += 1

                        if not server_info:
                            raise Exception("Failed to initialize MCP session.")

                        self.sessions[combined_server_id] = session
                        self.connected_servers[combined_server_id] = (
                            True  # Mark as connected before tool registration
                        )
                        logger.info(
                            f"MCPService: {combined_server_id} connected. Registering tools..."
                        )

                        if agent_name:
                            await self.register_server_tools(
                                combined_server_id, server_name, agent_name
                            )
                        else:
                            for agent_name in server_config.enabledForAgents:
                                await self.register_server_tools(
                                    combined_server_id, server_name, agent_name
                                )

                        if server_info.capabilities.prompts:
                            prompts = await self.sessions[
                                combined_server_id
                            ].list_prompts()
                            self.server_prompts[server_name] = prompts.prompts

                        logger.info(
                            f"MCPService: {server_name} setup complete. Waiting for shutdown signal."
                        )
                        await shutdown_event.wait()

        except asyncio.CancelledError:
            logger.info(f"MCPService: Connection task for {server_name} was cancelled.")
        except Exception:
            logger.exception(
                f"MCPService: Error in connection management for '{server_name}'"
            )
            if agent_name:
                agent_manager = AgentManager.get_instance()
                agent = agent_manager.get_local_agent(agent_name)
                if agent and combined_server_id in agent.mcps_loading:
                    agent.mcps_loading.remove(combined_server_id)
        finally:
            logger.info(f"MCPService: Cleaning up connection for {server_name}.")
            self.sessions.pop(server_name, None)
            self.connected_servers.pop(server_name, False)
            self.tools_cache.pop(server_name, None)
            self._server_shutdown_events.pop(server_name, None)
            logger.info(f"MCPService: Cleanup for {server_name} complete.")

    def _get_server_id_format(
        self, server_name: str, agent_name: Optional[str] = None
    ) -> str:
        """Format server ID with optional agent name prefix."""
        return f"{agent_name}__{server_name}" if agent_name else server_name

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

    async def start_server_connection_management(
        self, server_config: MCPServerConfig, agent_name: Optional[str] = None
    ):
        """Starts and manages the connection for a single MCP server."""
        combined_server_id = self._get_server_id_format(server_config.name, agent_name)
        if (
            combined_server_id in self._server_connection_tasks
            and not self._server_connection_tasks[combined_server_id].done()
        ):
            logger.info(
                f"MCPService: Connection management for {combined_server_id} already in progress."
            )
            return

        if agent_name:
            agent_manager = AgentManager.get_instance()
            agent = agent_manager.get_local_agent(agent_name)
            if agent:
                agent.mcps_loading.append(combined_server_id)
        logger.info(
            f"MCPService: Creating task for _manage_single_connection for {combined_server_id}"
        )
        if self.loop.is_closed():
            logger.warning(
                "MCPService: Loop is closed, cannot create task for server connection."
            )
            return
        task = self.loop.create_task(
            self._manage_single_connection(server_config, agent_name)
        )
        self._server_connection_tasks[combined_server_id] = task

    async def shutdown_all_server_connections(self, agent_name: Optional[str] = None):
        """Signals all active server connections to shut down and waits for them."""
        logger.info("MCPService: Shutting down all server connections...")
        active_tasks = []
        for server_id, event in list(self._server_shutdown_events.items()):
            if agent_name and agent_name not in server_id:
                continue  # Skip servers not matching the agent name
            extracted_server_id = server_id.replace(
                f"{agent_name}__", ""
            )  # Remove prefix if present
            await self.deregister_server_tools(extracted_server_id, agent_name)
            logger.info(f"MCPService: Signaling shutdown for {server_id}")
            event.set()
            if server_id in self._server_connection_tasks:
                task = self._server_connection_tasks[server_id]
                if task and not task.done():
                    active_tasks.append(task)

        if active_tasks:
            logger.info(
                f"MCPService: Waiting for {len(active_tasks)} connection tasks to complete..."
            )
            await asyncio.gather(*active_tasks, return_exceptions=True)

        self._server_connection_tasks.clear()
        logger.info("MCPService: All server connections shut down process initiated.")

    async def shutdown_single_server_connection(self, server_id: str):
        """Signals a specific server connection to shut down and waits for it."""
        logger.info(f"MCPService: Shutting down connection for server {server_id}...")
        if server_id in self._server_shutdown_events:
            event = self._server_shutdown_events[server_id]
            event.set()
            logger.info(f"MCPService: Shutdown signal sent to {server_id}.")

            task = self._server_connection_tasks.get(server_id)
            if task and not task.done():
                logger.info(
                    f"MCPService: Waiting for connection task of {server_id} to complete..."
                )
                try:
                    await asyncio.wait_for(
                        task, timeout=10.0
                    )  # Wait for task to finish
                    logger.info(
                        f"MCPService: Connection task for {server_id} completed."
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"MCPService: Timeout waiting for {server_id} connection task to complete. It might be stuck."
                    )
                    task.cancel()  # Force cancel if it didn't finish
                except Exception as e:
                    logger.error(f"MCPService: Error waiting for {server_id} task: {e}")
            else:
                logger.info(
                    f"MCPService: No active task found for {server_id} or task already done."
                )
        else:
            logger.warning(
                f"MCPService: No shutdown event found for server {server_id}. It might not be running or already shut down."
            )

        # Clean up entries related to this server, though _manage_single_connection's finally should handle most
        self._server_connection_tasks.pop(server_id, None)
        logger.info(
            f"MCPService: Shutdown process for {server_id} initiated/completed."
        )

    async def register_server_tools(
        self, combined_server_id: str, server_name: str, agent_name: str
    ) -> None:
        """
        Register all tools from a connected server.

        Args:
            server_id: ID of the server to register tools from
        """

        if combined_server_id not in self.sessions or not self.connected_servers.get(
            combined_server_id
        ):
            logger.warning(
                f"Cannot register tools: Server '{combined_server_id}' is not connected"
            )
            return

        try:
            response = await self.sessions[combined_server_id].list_tools()

            # Cache tools
            self.tools_cache[combined_server_id] = {
                tool.name: tool for tool in response.tools
            }

            if agent_name:
                agent_manager = AgentManager.get_instance()
                registry = agent_manager.get_local_agent(agent_name)
            else:
                # Register each tool with the tool registry
                registry = ToolRegistry.get_instance()
            for tool in response.tools:
                # Create namespaced tool definition
                def tool_definition_factory(tool_info=tool, srv_id=server_name):
                    def get_definition(provider=None):
                        return self._format_tool_definition(tool_info, srv_id, provider)

                    return get_definition

                # Create tool handler
                handler_factory = self._create_tool_handler(
                    combined_server_id, tool.name
                )

                # Register the tool
                if registry:
                    registry.register_tool(
                        tool_definition_factory(), handler_factory, self
                    )
            if (
                isinstance(registry, LocalAgent)
                and combined_server_id in registry.mcps_loading
            ):
                registry.mcps_loading.remove(combined_server_id)

        except Exception:
            logger.exception(
                f"Error registering tools from server '{combined_server_id}'"
            )
            self.connected_servers[combined_server_id] = False

    async def deregister_server_tools(
        self, server_name: str, agent_name: Optional[str] = None
    ):
        agent_manager = AgentManager.get_instance()
        if agent_name:
            local_agent = agent_manager.get_local_agent(agent_name)
            if not local_agent:
                return
            combined_server_id = self._get_server_id_format(server_name, agent_name)
            if server_name in self.tools_cache:
                for tool_name in self.tools_cache[combined_server_id].keys():
                    if local_agent.is_active:
                        local_agent.deactivate()
                    if (
                        f"{server_name}_{tool_name}"
                        in local_agent.tool_definitions.keys()
                    ):
                        del local_agent.tool_definitions[f"{server_name}_{tool_name}"]
        else:
            for agent_name in agent_manager.agents.keys():
                local_agent = agent_manager.get_local_agent(agent_name)
                if not local_agent:
                    continue

                combined_server_id = self._get_server_id_format(server_name, agent_name)
                if combined_server_id in self.tools_cache:
                    for tool_name in self.tools_cache[combined_server_id].keys():
                        if local_agent.is_active:
                            local_agent.deactivate()
                        if (
                            f"{server_name}_{tool_name}"
                            in local_agent.tool_definitions.keys()
                        ):
                            del local_agent.tool_definitions[
                                f"{server_name}_{tool_name}"
                            ]

    def _format_tool_definition(
        self, tool: Tool, server_id: str, provider: Optional[str] = None
    ) -> Dict[str, Any]:
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
        namespaced_name = f"{server_id}_{tool.name}"

        from jsonref import replace_refs
        import json

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
        if provider == "claude":
            return {
                "name": namespaced_name,
                "description": tool.description,
                "input_schema": merged_inputSchema,
            }
        else:  # Default format (OpenAI-compatible)
            return {
                "type": "function",
                "function": {
                    "name": namespaced_name,
                    "description": tool.description,
                    "parameters": merged_inputSchema,
                },
            }

    def _run_async(self, coro):
        """Helper method to run coroutines in the service's event loop"""
        if self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def _create_tool_handler(self, server_id: str, tool_name: str) -> Callable:
        """
        Create an asynchronous handler function for a tool.

        Args:
            server_id: ID of the server the tool belongs to
            tool_name: Name of the tool

        Returns:
            Asynchronous handler function for the tool
        """

        # This is the factory that the ToolRegistry expects.
        # It should return the actual callable that will be invoked by the agent.
        def handler_factory(
            service_instance=None,
        ):  # service_instance will be self (MCPService)
            # This is the actual async handler the agent will await.
            def actual_tool_executor(
                **params,
            ) -> list[Dict[str, Any]]:
                if server_id not in self.sessions or not self.connected_servers.get(
                    server_id
                ):
                    raise Exception(
                        f"Cannot call tool: Server '{server_id}' is not connected"
                    )

                # The agent framework should be awaiting this coroutine.
                # The call to session.call_tool is already async.
                session = self.sessions[server_id]
                result = self._run_async(session.call_tool(tool_name, params))
                return self._format_contents(result.content)

            return actual_tool_executor  # Return the async function

        return handler_factory  # Return the factory

    async def list_tools(self, server_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all available tools from a connected MCP server or all servers.

        Args:
            server_id: Optional ID of the server to list tools from. If None, lists tools from all servers.

        Returns:
            List of tools with their metadata
        """
        if server_id is not None:
            if server_id not in self.sessions or not self.connected_servers.get(
                server_id
            ):
                return []

            try:
                response = await self.sessions[server_id].list_tools()
                self.tools_cache[server_id] = {
                    tool.name: tool for tool in response.tools
                }

                return [
                    {
                        "name": f"{server_id}.{tool.name}",
                        "description": tool.description,
                        "input_schema": tool.inputSchema,
                    }
                    for tool in response.tools
                ]
            except Exception:
                logger.exception(f"Error listing tools from server '{server_id}'")
                self.connected_servers[server_id] = False
                return []
        else:
            # List tools from all connected servers
            all_tools = []
            for srv_id in list(self.sessions.keys()):
                all_tools.extend(await self.list_tools(srv_id))
            return all_tools

    async def get_prompt(
        self,
        server_id: str,
        prompt_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Get a specific prompt from a connected MCP server.

        Args:
            server_id: ID of the server to get the prompt from
            prompt_name: Name of the prompt to retrieve

        Returns:
            Prompt object if found, None otherwise
        """

        if server_id not in self.sessions or not self.connected_servers.get(server_id):
            return {
                "content": f"Cannot call tool: Server '{server_id}' is not connected",
                "status": "error",
            }

        try:
            session = self.sessions[server_id]
            result = self._run_async(session.get_prompt(prompt_name, arguments))
            return {"content": result.messages, "status": "success"}
        except Exception as e:
            logger.error(
                f"Error retrieving prompt '{prompt_name}' from server '{server_id}': {str(e)}"
            )
            self.connected_servers[server_id] = False
            return {
                "content": f"Error calling tool '{prompt_name}' on server '{server_id}': {str(e)}",
                "status": "error",
            }

    def _format_contents(self, content: List[ContentBlock]) -> List[Dict[str, Any]]:
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
                result.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{c.mimeType};base64,{c.data}"},
                    }
                )

        return result

    async def call_tool(
        self, server_id: str, tool_name: str, tool_args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Call a tool on an MCP server.

        Args:
            server_id: ID of the server to call the tool on
            tool_name: Name of the tool to call
            tool_args: Arguments to pass to the tool

        Returns:
            Dict containing the tool's response
        """
        if server_id not in self.sessions or not self.connected_servers.get(server_id):
            return {
                "content": f"Cannot call tool: Server '{server_id}' is not connected",
                "status": "error",
            }

        if server_id not in self.tools_cache or tool_name not in self.tools_cache.get(
            server_id, {}
        ):
            # Refresh tools cache
            await self.list_tools(server_id)
            if (
                server_id not in self.tools_cache
                or tool_name not in self.tools_cache.get(server_id, {})
            ):
                return {
                    "content": f"Tool '{tool_name}' is not available on server '{server_id}'",
                    "status": "error",
                }

        try:
            result = await self.sessions[server_id].call_tool(tool_name, tool_args)
            return {
                "content": self._format_contents(result.content),
                "status": "success",
            }
        except Exception as e:
            self.connected_servers[server_id] = False
            return {
                "content": f"Error calling tool '{tool_name}' on server '{server_id}': {str(e)}",
                "status": "error",
            }

    def start(self):
        """Start the service's event loop in a separate thread"""

        def run_loop():
            logger.info("MCPService: Event loop thread started.")
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_forever()
            finally:
                logger.info("MCPService: Event loop stopping...")
                # This block executes when loop.stop() is called or run_forever() exits.
                # Attempt to cancel any remaining tasks.
                # shutdown_all_server_connections should ideally handle most task terminations gracefully.
                try:
                    all_tasks = asyncio.all_tasks(loop=self.loop)
                    # Exclude the current task if this finally block is run by a task on the loop
                    current_task = (
                        asyncio.current_task(loop=self.loop)
                        if self.loop.is_running()
                        else None
                    )  # Check if loop is running

                    tasks_to_cancel = [
                        t for t in all_tasks if t is not current_task and not t.done()
                    ]
                    if tasks_to_cancel:
                        logger.info(
                            f"MCPService: Cancelling {len(tasks_to_cancel)} outstanding tasks in event loop thread."
                        )
                        for task in tasks_to_cancel:
                            task.cancel()
                        # Give tasks a chance to process cancellation
                        # This needs to run on the loop, but run_forever has exited.
                        # We can run_until_complete for these specific tasks.
                        if self.loop.is_running():  # Should not be, but as a safeguard
                            self.loop.run_until_complete(
                                asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                            )
                        else:  # If loop not running, create a temporary runner for cleanup

                            async def gather_cancel_tasks():
                                await asyncio.gather(
                                    *tasks_to_cancel, return_exceptions=True
                                )

                            self.loop.run_until_complete(gather_cancel_tasks())

                except RuntimeError as e:
                    logger.error(
                        f"MCPService: Runtime error during task cancellation in run_loop finally: {e}"
                    )
                except Exception as e_final:
                    logger.error(
                        f"MCPService: General error during task cancellation in run_loop finally: {e_final}"
                    )

                if not self.loop.is_closed():
                    self.loop.close()
                logger.info("MCPService: Event loop thread stopped and loop closed.")

        self.loop_thread = threading.Thread(target=run_loop, daemon=True)
        self.loop_thread.start()

    def stop(self):
        """Stop the service's event loop"""
        logger.info("MCPService: Stopping event loop...")
        if hasattr(self, "loop") and self.loop and not self.loop.is_closed():
            if self.loop.is_running():
                logger.info("MCPService: Requesting event loop to stop.")
                self.loop.call_soon_threadsafe(self.loop.stop)
            # The finally block in run_loop should handle task cleanup and closing the loop.
        else:
            logger.info(
                "MCPService: Loop not available or already closed when stop() called."
            )

        if hasattr(self, "loop_thread") and self.loop_thread.is_alive():
            logger.info("MCPService: Waiting for event loop thread to join...")
            self.loop_thread.join(timeout=10)
            if self.loop_thread.is_alive():
                logger.warning(
                    "MCPService: Event loop thread did not join in time. Loop might be stuck or tasks not yielding."
                )
        else:
            logger.info(
                "MCPService: Loop thread not available or not alive when stop() called."
            )

        # Fallback: Ensure loop is closed if thread exited but loop wasn't closed by run_loop's finally
        if hasattr(self, "loop") and self.loop and not self.loop.is_closed():
            logger.warning(
                "MCPService: Loop was not closed by thread's run_loop, attempting to close now."
            )
            # This is a fallback, ideally run_loop's finally handles this.
            try:
                # Minimal cleanup if loop is in a weird state
                # Ensure all tasks are finished or cancelled before closing loop
                if (
                    not self.loop.is_running()
                ):  # If not running, we can try to run_until_complete for cleanup
                    tasks = [
                        t for t in asyncio.all_tasks(loop=self.loop) if not t.done()
                    ]
                    if tasks:
                        logger.info(
                            f"MCPService: Running {len(tasks)} pending tasks to completion before closing loop in stop()."
                        )

                        async def finalize_tasks():
                            await asyncio.gather(*tasks, return_exceptions=True)

                        self.loop.run_until_complete(finalize_tasks())
            except (
                RuntimeError
            ) as e:  # e.g. "cannot call run_until_complete() on a running loop"
                logger.error(
                    f"MCPService: Runtime error during final loop cleanup in stop(): {e}"
                )
            except Exception as e_final_stop:
                logger.error(
                    f"MCPService: General error during final loop cleanup in stop(): {e_final_stop}"
                )
            finally:
                if not self.loop.is_closed():  # Check again before closing
                    self.loop.close()
                    logger.info("MCPService: Loop closed in stop() fallback.")

        logger.info("MCPService: Stop process complete.")
