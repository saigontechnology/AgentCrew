from __future__ import annotations

import asyncio
from datetime import datetime
import os
import copy
from typing import TYPE_CHECKING, Literal, cast

from .base import BaseAgent, MessageType
from AgentCrew.modules.llm.token_usage import TokenUsage
from loguru import logger

if TYPE_CHECKING:
    from AgentCrew.modules.llm import BaseLLMService
    from typing import Any, Callable, Union


def normalize_voice_enabled(value) -> Literal["enabled", "disabled"]:
    if value in (True, "enabled", "full", "partial"):
        return "enabled"
    return "disabled"


class LocalAgent(BaseAgent):
    """Base class for all specialized agents."""

    def __init__(
        self,
        name: str,
        description: str,
        llm_service: BaseLLMService | None,
        services: dict[str, Any],
        tools: list[str],
        temperature: float | None = None,
        is_remoting_mode: bool = False,
        voice_enabled: Literal["enabled", "disabled"] = "disabled",
        voice_id: str | None = None,
    ):
        """
        Initialize a new agent.

        Args:
            name: The name of the agent
            description: A description of the agent's capabilities
            llm_service: The LLM service to use for this agent
            services: Dictionary of available services
            voice_enabled: Whether voice features are enabled for this agent
            voice_id: Voice ID to use for text-to-speech
        """
        super().__init__(name, description)
        self.llm = llm_service
        self.temperature = temperature
        self.services = services
        self.tools: list[str] = tools  # list of tool names that the agent needs
        self.system_prompt = None
        self.custom_system_prompt = None
        self.tool_prompts = []
        self.mcp_resources: dict[str, list[dict[str, Any]]] = {}
        self.is_remoting_mode: bool = is_remoting_mode
        self.pinned_model_id: str | None = None
        self.token_usage = TokenUsage()
        self.voice_enabled: Literal["enabled", "disabled"] = normalize_voice_enabled(
            voice_enabled
        )
        self.voice_id: str | None = voice_id

        self.tool_definitions = {}  # {tool_name: (definition_func, handler_factory, service_instance)}
        self.registered_tools = (
            set()
        )  # Set of tool names that are registered with the LLM
        self._defer_tool_registration = False
        self.mcps_loading = []

        from AgentCrew.modules.agents.manager import AgentMode

        self._colaboration_mode = AgentMode.NONE

        from .tool_registrar import AgentToolRegistrar
        from .context_manager import AgentContextManager

        self._tool_registrar = AgentToolRegistrar(self)
        self._context_manager = AgentContextManager(self)

    @property
    def input_tokens_usage(self) -> int:
        return self.token_usage.input_tokens

    @input_tokens_usage.setter
    def input_tokens_usage(self, value: int):
        self.token_usage = TokenUsage(
            input_tokens=value,
            output_tokens=self.token_usage.output_tokens,
            cached_tokens=self.token_usage.cached_tokens,
            cache_creation_tokens=self.token_usage.cache_creation_tokens,
            total_input_tokens=self.token_usage.total_input_tokens,
        )

    @property
    def output_tokens_usage(self) -> int:
        return self.token_usage.output_tokens

    @output_tokens_usage.setter
    def output_tokens_usage(self, value: int):
        self.token_usage = TokenUsage(
            input_tokens=self.token_usage.input_tokens,
            output_tokens=value,
            cached_tokens=self.token_usage.cached_tokens,
            cache_creation_tokens=self.token_usage.cache_creation_tokens,
        )

    def _extract_tool_name(self, tool_def: Any) -> str:
        """Extract tool name from definition regardless of format."""
        from AgentCrew.modules.tools.utils import extract_tool_name

        return extract_tool_name(tool_def)

    def append_message(self, messages: Union[dict, list[dict]]):
        copy_messages = copy.deepcopy(messages)
        if isinstance(copy_messages, list):
            self.history.extend(copy_messages)
        else:
            self.history.append(copy_messages)

    def register_tools(self):
        """Register tools for this agent using the services dictionary."""
        self._tool_registrar.register_tools()

    def register_tool(self, definition_func, handler_factory, service_instance=None):
        """Register a tool with this agent."""
        self._tool_registrar.register_tool(
            definition_func, handler_factory, service_instance
        )

    def set_system_prompt(self, prompt: str):
        """
        Set the system prompt for this agent.

        Args:
            prompt: The system prompt
        """
        self.system_prompt = prompt

    def _parse_system_prompt(self, prompt: str) -> str:
        """
        Parse the system prompt to ensure it is in the correct format.

        Args:
            prompt: The system prompt
        """
        return (
            prompt.replace("{current_date}", datetime.today().strftime("%A, %d %B %Y"))
            .replace("{cwd}", os.getcwd())
            .replace("{current_agent_name}", self.name)
            .replace("{current_agent_description}", self.description)
        )

    def set_custom_system_prompt(self, prompt: str):
        """
        Set the system prompt for this agent.

        Args:
            prompt: The system prompt
        """
        self.custom_system_prompt = prompt

    def get_system_prompt(self) -> str:
        """
        Get the system prompt for this agent.

        Returns:
            The system prompt
        """
        return self.system_prompt or ""

    def activate(self):
        """
        Activate this agent by registering all tools with the LLM service.

        Returns:
            True if activation was successful, False otherwise
        """
        if not self.llm:
            return False

        if self.is_active:
            return True  # Already active

        self.register_tools()

        # Start MCP discovery in background (non-blocking) to reduce activation time
        if not self.is_remoting_mode:
            from AgentCrew.modules.mcpclient import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            if mcp_manager.initialized:
                mcp_manager.discover_mcps_for_agent_background(self.name)

        system_prompt = (
            f"<Name>{self.name}</Name>\n"
            f"<Description>{self.description}</Description>\n"
            f"<Instructions>\n{self.get_system_prompt()}\n</Instructions>"
        )
        if self.custom_system_prompt:
            system_prompt = f"{system_prompt}\n\n{self.custom_system_prompt}"
        if self.tool_prompts:
            system_prompt = f"{system_prompt}\n\n{'\n\n'.join(self.tool_prompts)}"

        self.llm.set_system_prompt(self._parse_system_prompt(system_prompt))
        self.llm.temperature = self.temperature if self.temperature is not None else 0.4
        self._defer_tool_registration = True
        self.is_active = True
        return True

    def deactivate(self):
        """
        Deactivate this agent by clearing all tools from the LLM service.

        Returns:
            True if deactivation was successful, False otherwise
        """
        if not self.llm:
            return False

        self._clear_tools_from_llm()
        self.tool_definitions = {}
        self.tool_prompts = []
        self.mcp_resources = {}
        self.is_active = False
        self.mcps_loading = []
        self._defer_tool_registration = False
        # Deregister MCP tools (no server shutdown needed — just remove definitions)
        if not self.is_remoting_mode:
            from AgentCrew.modules.mcpclient import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            if mcp_manager.initialized:
                mcp_manager.deregister_tools_for_agent_sync(self.name)
        return True

    def _register_tools_with_llm(self):
        """Register all of this agent's tools with the LLM service."""
        self._tool_registrar.sync_to_llm()

    def _clear_tools_from_llm(self):
        """Clear all tools from the LLM service."""
        self._tool_registrar._clear_from_llm()

    def resync_tools_to_llm(self):
        self._register_tools_with_llm()

    @property
    def clean_history(self):
        return self.history

    def get_provider(self) -> str:
        return self.llm.provider_name if self.llm else ""

    def is_streaming(self) -> bool:
        return self.llm.is_stream if self.llm else False

    def _format_tool_result(
        self,
        tool_use: dict,
        tool_result: Any,
        is_error: bool = False,
        is_rejected: bool = False,
    ) -> dict[str, Any]:
        """
        Format a tool result for OpenAI API.

        Args:
            tool_use: The tool use details
            tool_result: The result from the tool execution
            is_error: Whether the result is an error

        Returns:
            A formatted message for tool response
        """
        # OpenAI format for tool responses
        message = {
            "role": "tool",
            "agent": self.name,
            "tool_call_id": tool_use["id"],
            "tool_name": tool_use["name"],
            "content": tool_result,
        }

        # Add error indication if needed
        if is_error:
            message["content"] = f"ERROR: {str(message['content'])}"
        if is_rejected:
            message["content"] = f"DENIED: {str(message['content'])}"
            message["is_rejected"] = True

        return message

    def _format_assistant_message(
        self,
        assistant_response: str,
        thinking_data: tuple[str, str] | None = None,
        tool_uses: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Format the assistant's response into the appropriate message format for the LLM provider.

        Args:
            assistant_response (str): The text response from the assistant
            tool_use (dict, optional): Tool use information if a tool was used

        Returns:
            dict[str, Any]: A properly formatted message to append to the messages list
        """
        assistant_message = {
            "agent": self.name,
            "role": "assistant",
            "content": [{"type": "text", "text": assistant_response}],
        }
        if thinking_data:
            thinking_content, thinking_signature = thinking_data
            thinking_block = {"type": "thinking", "thinking": thinking_content}
            # Add signature if available
            if thinking_signature:
                thinking_block["signature"] = thinking_signature
            assistant_message["content"].insert(0, thinking_block)
        valid_tool_uses = [
            tool_use
            for tool_use in (tool_uses or [])
            if tool_use.get("id") and tool_use.get("name")
        ]
        if valid_tool_uses:
            assistant_message["tool_calls"] = [
                {
                    "id": tool_use["id"],
                    "name": tool_use["name"],
                    "arguments": tool_use["input"],
                    "type": tool_use.get("type", "tool_call"),
                }
                for tool_use in valid_tool_uses
            ]
        return assistant_message

    def format_message(
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if message_type == MessageType.Assistant:
            return self._format_assistant_message(
                message_data.get("message", ""),
                message_data.get("thinking", None),
                message_data.get("tool_uses", None),
            )
        elif message_type == MessageType.ToolResult:
            return self._format_tool_result(
                message_data.get("tool_use", {}),
                message_data.get("tool_result", ""),
                message_data.get("is_error", False),
                message_data.get("is_rejected", False),
            )
        elif message_type == MessageType.FileContent:
            return (
                self.llm.process_file_for_message(message_data.get("file_uri", ""))
                if self.llm
                else message_data
            )

    def configure_think(self, think_setting):
        if self.llm:
            self.llm.set_think(think_setting)

    async def execute_tool_call(self, tool_use: dict) -> Any:
        from AgentCrew.modules.events.hook_payloads import (
            ToolExecuteContext,
            ToolExecuteResult,
        )
        from AgentCrew.modules.events.hooks import (
            HookRegistry,
            HookPoints,
            CancelOperation,
        )

        hooks = HookRegistry.get_instance()
        tool_name = tool_use["name"]
        tool_input = tool_use.get("input", {})

        # Run before hooks — can cancel or modify the tool call
        before_payload: ToolExecuteContext = {
            "agent_name": self.name,
            "tool_id": tool_use.get("id", ""),
            "tool_use": copy.deepcopy(tool_use),
            "requested_tool_name": tool_name,
            "requested_tool_input": copy.deepcopy(tool_input),
            "tool_name": tool_name,
            "tool_input": copy.deepcopy(tool_input),
        }
        context_result = await hooks.run_before(
            HookPoints.TOOL_EXECUTE,
            **before_payload,
        )

        if context_result is None:
            raise CancelOperation(
                f"Tool {tool_name} was cancelled by a tool.execute hook"
            )

        context = cast(ToolExecuteContext, context_result)
        resolved_name = context.get("tool_name", tool_name)
        resolved_input = context.get("tool_input", tool_input)

        result = (
            await self.llm.execute_tool(resolved_name, resolved_input)
            if self.llm
            else None
        )

        # Run after hooks — can modify the result
        after_context: ToolExecuteContext = {
            "agent_name": self.name,
            "tool_id": tool_use.get("id", ""),
            "tool_use": copy.deepcopy(tool_use),
            "requested_tool_name": tool_name,
            "requested_tool_input": copy.deepcopy(tool_input),
            "tool_name": resolved_name,
            "tool_input": copy.deepcopy(resolved_input),
            "resolved_tool_name": resolved_name,
            "resolved_tool_input": copy.deepcopy(resolved_input),
        }
        result_envelope: ToolExecuteResult = {
            "tool_result": result,
            "is_error": False,
        }
        modified = await hooks.run_after(
            HookPoints.TOOL_EXECUTE,
            result=result_envelope,
            **after_context,
        )

        if isinstance(modified, dict):
            return modified.get("tool_result", result)
        return modified

    def calculate_usage_cost(
        self, input_tokens, output_tokens, cached_tokens=0
    ) -> float:
        return (
            self.llm.calculate_cost(input_tokens, output_tokens, cached_tokens)
            if self.llm
            else 0
        )

    def get_model(self) -> str:
        return f"{self.llm.provider_name}/{self.llm.model}" if self.llm else ""

    def update_llm_service(self, new_llm_service: BaseLLMService) -> bool:
        """
        Update the LLM service used by this agent.

        Args:
            new_llm_service: The new LLM service to use

        Returns:
            True if the update was successful, False otherwise
        """
        was_active = self.is_active

        # Deactivate with the current LLM if active
        if was_active:
            self.deactivate()

        # Update the LLM service
        self.llm = new_llm_service

        # Reactivate with the new LLM if it was active before
        if was_active:
            self.activate()

        return True

    def _get_directory_structure(self) -> str:
        return self._context_manager._get_directory_structure()

    def _enhance_agent_context_messages(
        self, final_messages: list[dict[str, Any]]
    ) -> None:
        self._context_manager.enhance_messages(final_messages)

    def _filter_invalid_tool_uses(
        self, tool_uses: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        filtered_tool_uses = []
        for tool_use in tool_uses:
            if isinstance(tool_use.get("name"), str) and bool(
                tool_use.get("name", "").strip()
            ):
                filtered_tool_uses.append(tool_use)
            elif tool_use.get("id") or tool_use.get("args_json"):
                logger.warning(
                    "Dropping malformed parsed tool call without a usable name"
                )
        return filtered_tool_uses

    def _clean_shrinkable_tool_result(
        self, final_messages: list[dict[str, Any]]
    ) -> None:
        self._context_manager.shrink_tool_results(final_messages)

    def _extract_last_user_message_for_memory(self, messages: list[dict]) -> str:
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content", [])
            if isinstance(content, str):
                normalized = content.strip()
                if normalized:
                    return normalized
                continue
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    normalized = part.strip()
                    if normalized:
                        text_parts.append(normalized)
                elif isinstance(part, dict) and part.get("type") == "text":
                    normalized = str(part.get("text", "")).strip()
                    if normalized:
                        text_parts.append(normalized)
            if text_parts:
                return " ".join(text_parts)
        return ""

    def _extract_assistant_messages_for_memory(
        self, messages: list[dict], current_response: str = ""
    ) -> list[str]:
        assistant_messages: list[str] = []
        last_user_idx = -1
        # Map tool_call_id -> ask question for pairing across parallel calls
        ask_questions_by_id: dict[str, str] = {}

        for index, message in enumerate(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                last_user_idx = index

        for message in messages[last_user_idx + 1 :]:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "assistant":
                content = message.get("content", "")
                if isinstance(content, str):
                    normalized = content.strip()
                    if normalized:
                        assistant_messages.append(normalized)

                # Index ask tool questions by their tool call id
                for tc in message.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("name") == "ask":
                        tc_id = tc.get("id")
                        if not tc_id:
                            continue
                        arguments = tc.get("arguments", {})
                        if isinstance(arguments, dict):
                            q = arguments.get("question", "")
                        elif isinstance(arguments, str):
                            import json

                            try:
                                q = json.loads(arguments).get("question", "")
                            except json.JSONDecodeError:
                                q = ""
                        else:
                            q = ""
                        if q:
                            ask_questions_by_id[tc_id] = q

            elif role == "tool":
                # Capture tool rejection reasons (user feedback)
                if message.get("is_rejected"):
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        assistant_messages.append(
                            f"[Tool rejected: {message.get('tool_name', 'unknown')}] "
                            f"{content.strip()}"
                        )
                # Capture ask tool answers paired with the question by tool_call_id
                elif message.get("tool_name") == "ask":
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        tc_id = message.get("tool_call_id")
                        question = (
                            ask_questions_by_id.pop(tc_id, None) if tc_id else None
                        )
                        if question:
                            assistant_messages.append(
                                f"[User answered: {content.strip()} | Question was: {question}]"
                            )
                        else:
                            assistant_messages.append(
                                f"[User answered: {content.strip()}]"
                            )

        normalized_current_response = current_response.strip()
        if normalized_current_response and (
            not assistant_messages
            or assistant_messages[-1] != normalized_current_response
        ):
            assistant_messages.append(normalized_current_response)
        return assistant_messages

    def store_memory_if_available(
        self,
        user_message: str,
        messages: list[dict],
        current_response: str,
        session_id: str | None = None,
    ) -> None:
        from AgentCrew.modules.memory.base_service import BaseMemoryService

        memory_service = self.services.get("memory")
        if not memory_service or not isinstance(memory_service, BaseMemoryService):
            return
        assistant_messages = self._extract_assistant_messages_for_memory(
            messages, current_response
        )
        try:
            memory_service.store_conversation(
                user_message,
                assistant_messages,
                self.name,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"Failed to store conversation in memory: {e}")

    def _refresh_agent_skills(self) -> None:
        """Refresh skills from disk and update LLM tool definitions if needed.

        Handles all state transitions:
        - No skills, tool present -> remove tool, re-sync to LLM
        - Skills appear -> register tool, re-sync to LLM
        - Skills changed (names added/removed) -> re-register tool with current
          skill list, re-sync to LLM
        - No changes in state -> no-op
        """
        skills_service = self.services.get("skills")
        if not skills_service:
            return

        changed = skills_service.refresh()
        has_skills = skills_service.has_skills()
        has_tool = "activate_skill" in self.tool_definitions

        # Quick exit when nothing changed
        if has_skills == has_tool and not changed:
            return

        # State changed — update tool definitions
        if has_tool:
            del self.tool_definitions["activate_skill"]

        if has_skills:
            from AgentCrew.modules.skills.tool import register as register_skills

            register_skills(skills_service, self)

        # Re-sync to LLM only when already past deferred sync
        if not self._defer_tool_registration:
            self._register_tools_with_llm()

    async def process_messages(
        self,
        messages: list[dict[str, Any]] | None = None,
        callback: Callable | None = None,
    ):
        """
        Process messages using this agent.

        Args:
            messages: The messages to process

        Returns:
            The processed messages with the agent's response
        """
        if not self.llm:
            return

        self._refresh_agent_skills()

        if self._defer_tool_registration:
            while len(self.mcps_loading) > 0:
                await asyncio.sleep(0.2)
            self._register_tools_with_llm()
            self._defer_tool_registration = False

        assistant_response = ""
        _tool_uses = []
        _token_usage = TokenUsage()
        preparing_messages = messages or self.history

        from AgentCrew.modules.events.hooks import (
            HookRegistry,
            HookPoints,
        )
        from AgentCrew.modules.events.hook_payloads import (
            ContextBuildContext,
            ContextBuildResult,
        )

        _hooks = HookRegistry.get_instance()

        # ── context.build before hook ────────────────────────────────
        _system_prompt = self.llm.get_system_prompt() if self.llm else ""
        _before_payload: ContextBuildContext = {
            "system_prompt": _system_prompt,
            "messages": copy.deepcopy(preparing_messages),
        }
        _before_ctx = await _hooks.run_before(
            HookPoints.CONTEXT_BUILD,
            **_before_payload,
        )
        if _before_ctx is None:
            logger.info("context.build cancelled by before hook — aborting turn")
            return
        # Apply before-hook mutations
        if "messages" in _before_ctx:
            preparing_messages = _before_ctx["messages"]
        _before_sp = _before_ctx.get("system_prompt", _system_prompt)
        if _before_sp != _system_prompt:
            self.llm.set_system_prompt(_before_sp)
            _system_prompt = _before_sp
        # ─────────────────────────────────────────────────────────────

        self._clean_shrinkable_tool_result(preparing_messages)
        enhancing_messages = messages[:] if messages else self.history[:]
        self._enhance_agent_context_messages(enhancing_messages)
        from AgentCrew.modules.utils import VisionPreprocessingUtils

        final_messages = copy.deepcopy(enhancing_messages)

        await VisionPreprocessingUtils.preprocess_messages(
            final_messages,
            self.llm,
        )

        # ── context.build after hook ─────────────────────────────────
        _after_envelope: ContextBuildResult = {
            "messages": final_messages,
            "system_prompt": _system_prompt,
        }
        _modified = await _hooks.run_after(
            HookPoints.CONTEXT_BUILD,
            result=_after_envelope,
        )
        if isinstance(_modified, dict):
            final_messages = _modified.get("messages", final_messages)
            _after_sp = _modified.get("system_prompt", _system_prompt)
            if _after_sp != _system_prompt:
                self.llm.set_system_prompt(_after_sp)
                _system_prompt = _after_sp
        # ─────────────────────────────────────────────────────────────

        try:
            async with await self.llm.stream_assistant_response(
                final_messages
            ) as stream:
                async for chunk in stream:
                    (
                        assistant_response,
                        tool_uses,
                        chunk_token_usage,
                        chunk_text,
                        thinking_chunk,
                    ) = self.llm.process_stream_chunk(
                        chunk, assistant_response, _tool_uses
                    )
                    yield (assistant_response, chunk_text, thinking_chunk)

                    if tool_uses:
                        _tool_uses = tool_uses
                    _token_usage = _token_usage.merge(chunk_token_usage)

            self.token_usage = _token_usage
            if callback:
                callback(
                    self._filter_invalid_tool_uses(_tool_uses),
                    _token_usage,
                )
            else:
                self.tool_uses = _tool_uses

        except GeneratorExit as e:
            logger.warning(f"Stream processing interrupted: {e}")
            return
        except Exception as e:
            logger.error(f"Error during message processing: {e}")
            logger.debug(f"Final messages at error time: {final_messages}")
            raise e
