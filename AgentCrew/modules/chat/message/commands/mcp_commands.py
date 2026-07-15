from __future__ import annotations

from AgentCrew.modules.events import AppEvents
from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler
    from AgentCrew.modules.mcpclient import MCPService


class MCPCommands:
    """Handles MCP-related slash commands."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler

    async def handle_mcp(self, command: str) -> Tuple[bool, bool]:
        """
        Handle the /mcp command: list prompts or fetch a specific prompt content.

        Returns:
            Tuple of (exit_flag, clear_flag)
        """
        parts = command.strip().split()
        mcp_service: MCPService | None = self.message_handler.mcp_manager.mcp_service
        # /mcp with no args: list all prompts
        if len(parts) == 1:
            prompts = []
            if mcp_service:
                for server_id, prompt_list in mcp_service.server_prompts.items():
                    for prompt in prompt_list:
                        prompt_name = prompt.name
                        if prompt_name:
                            prompts.append(f"{server_id}/{prompt_name}")
            msg = (
                "Available MCP prompts:\n" + "\n".join(prompts)
                if prompts
                else "No MCP prompts found."
            )
            await self.message_handler.bus.emit(AppEvents.SYSTEM_MESSAGE, message=msg)
            return False, True
        # /mcp <server_id.prompt_name>: fetch and show the prompt
        elif len(parts) == 2:
            full_name = parts[1]
            if "/" not in full_name:
                await self.message_handler.bus.emit(
                    AppEvents.ERROR,
                    message="Please use format: /mcp server_id/prompt_name",
                )
                return False, True
            server_id, prompt_name = full_name.split("/", 1)
            try:
                prompt = await mcp_service.get_prompt(server_id, prompt_name)
                prompt_content = prompt.get("content", [])
                if len(prompt_content) > 0:
                    prompt_text = prompt_content[0].content.text
                    await self.message_handler.bus.emit(
                        AppEvents.MCP_PROMPT, name=prompt_name, content=f"{prompt_text}"
                    )
                else:
                    await self.message_handler.bus.emit(
                        AppEvents.ERROR,
                        message=f"Prompt {server_id}.{prompt_name} not found.",
                    )
            except Exception as e:
                await self.message_handler.bus.emit(
                    AppEvents.ERROR, message=f"Error fetching prompt: {str(e)}"
                )
            return False, True
        else:
            await self.message_handler.bus.emit(
                AppEvents.ERROR, message="Usage: /mcp [server_id.prompt_name]"
            )
            return False, True
