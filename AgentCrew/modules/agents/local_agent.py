from __future__ import annotations

import asyncio
import copy
import os
import threading
from concurrent.futures import Future
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from AgentCrew.modules.llm.token_usage import ConversationUsage, TokenUsage

from .base import BaseAgent, MessageType

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from AgentCrew.modules.llm import BaseLLMService
    from AgentCrew.modules.llm.model_selection import ModelSelection
    from AgentCrew.modules.llm.reasoning_selection import ReasoningSelection


def normalize_voice_enabled(value) -> Literal["enabled", "disabled"]:
    if value in (True, "enabled", "full", "partial"):
        return "enabled"
    return "disabled"


class LocalAgent(BaseAgent):
    """Base class for all specialized agents."""

    #: Bounded wait for background MCP discovery before the first LLM request.
    MCP_DISCOVERY_WAIT_SECONDS: float = 30.0

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
        self.model_selection: ModelSelection | None = None
        self.reasoning_selection: ReasoningSelection | None = None
        self.token_usage = TokenUsage()
        self.conversation_usage = ConversationUsage()
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
        self._mcp_discovery_future: Future | None = None
        self._mcp_registry_lock = threading.RLock()

        from AgentCrew.modules.agents.manager import AgentMode

        self._colaboration_mode = AgentMode.NONE

        from .context_manager import AgentContextManager
        from .llm_lifecycle import AgentLLMLifecycle
        from .memory_coordinator import AgentMemoryCoordinator
        from .message_formatter import AgentMessageFormatter
        from .tool_registrar import AgentToolRegistrar

        self._tool_registrar = AgentToolRegistrar(self)
        self._context_manager = AgentContextManager(self)
        self._llm_lifecycle = AgentLLMLifecycle(self)
        self._memory_coordinator = AgentMemoryCoordinator(self)
        self._message_formatter = AgentMessageFormatter(self)

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

    def append_message(self, messages: dict | list[dict]):
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
            prompt.replace("{current_date}", datetime.now(UTC).strftime("%A, %d %B %Y"))
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

        # Commit active state before starting background discovery so a fast
        # cached completion is not rejected by the active-agent check. The
        # agent-scoped lock serializes registry mutations with background MCP
        # registration.
        with self._mcp_registry_lock:
            self.is_active = True
            self.register_tools()
        # Sync built-in tools to the LLM immediately so the agent is usable
        # before MCP discovery finishes; MCP tools are final-synced later.
        self._tool_registrar.sync_to_llm()

        try:
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
            self.llm.temperature = (
                self.temperature if self.temperature is not None else 0.4
            )
            self._defer_tool_registration = True

            # Start MCP discovery in background (non-blocking) to reduce activation time
            from AgentCrew.modules.mcpclient import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            if mcp_manager.initialized:
                self._mcp_discovery_future = (
                    mcp_manager.discover_mcps_for_agent_background(self.name)
                )
            return True
        except Exception:
            # Restore a consistent inactive state if a later activation step
            # raises, so background workers cannot mutate a broken agent and a
            # retry starts from a clean registry.
            self._clear_local_state()
            raise

    def _clear_local_state(self):
        """Clear agent's local activation state without performing MCP deregistration.

        Shared helper between :meth:`deactivate` and :meth:`deactivate_async`
        so that state mutation logic is not duplicated.
        """
        self.clear_tools_from_llm()
        with self._mcp_registry_lock:
            self.tool_definitions = {}
            self.tool_prompts = []
            self.mcp_resources = {}
            self.is_active = False
            self.mcps_loading = []
            self._defer_tool_registration = False
        self._mcp_discovery_future = None

    def deactivate(self):
        """
        Deactivate this agent by clearing all tools from the LLM service.

        Returns:
            True if deactivation was successful, False otherwise
        """
        if not self.llm:
            return False

        self._clear_local_state()
        # Deregister MCP tools (sync-boundary adapter using asyncio.run)
        # Only called from genuine sync boundaries (setup, GUI, console).
        # Async callers (transfer, delegate, ACP, chat commands) use
        # deactivate_async() instead.
        from AgentCrew.modules.mcpclient import MCPSessionManager

        mcp_manager = MCPSessionManager.get_instance()
        if mcp_manager.initialized:
            import asyncio

            asyncio.run(mcp_manager.deregister_tools_for_agent(self.name))
        return True

    async def deactivate_async(self):
        """
        Async variant of :meth:`deactivate`.

        Awaits MCP deregistration natively. Async callers (transfer,
        delegate, ACP model switch, chat commands, conversation load)
        should use this method to avoid ``asyncio.run()``.

        Returns:
            True if deactivation was successful, False otherwise
        """
        if not self.llm:
            return False

        self._clear_local_state()
        # Deregister MCP tools natively — no sync wrapper needed
        from AgentCrew.modules.mcpclient import MCPSessionManager

        mcp_manager = MCPSessionManager.get_instance()
        if mcp_manager.initialized:
            await mcp_manager.deregister_tools_for_agent(self.name)
        return True

    async def activate_async(self):
        """
        Async variant of :meth:`activate`.

        Replicates the sync activation steps and starts durable
        manager-owned background MCP discovery (daemon thread with its own
        event loop). Built-in tools are synced to the LLM immediately so the
        agent is usable before discovery finishes; MCP tools are final-synced
        on the first message via :meth:`_sync_mcp_tools_after_discovery`.
        Async callers (transfer, delegate, ACP, chat commands, A2A turn
        executor/agent manager) use this method; the discovery work itself
        never depends on the caller's event loop.

        Returns:
            True if activation was successful, False otherwise
        """
        if not self.llm:
            return False

        if self.is_active:
            return True  # Already active

        # Commit active state before starting background discovery so a fast
        # cached completion is not rejected by the active-agent check. The
        # agent-scoped lock serializes registry mutations with background MCP
        # registration.
        with self._mcp_registry_lock:
            self.is_active = True
            self.register_tools()
        # Sync built-in tools to the LLM immediately so the agent is usable
        # before MCP discovery finishes; MCP tools are final-synced later.
        self._tool_registrar.sync_to_llm()

        try:
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
            self.llm.temperature = (
                self.temperature if self.temperature is not None else 0.4
            )
            self._defer_tool_registration = True

            # Start durable manager-owned background discovery (daemon thread
            # with its own event loop) instead of an asyncio.Task bound to the
            # caller's temporary loop, which CLI/GUI close after each command
            # and which would cancel the discovery work.
            from AgentCrew.modules.mcpclient import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            if mcp_manager.initialized:
                self._mcp_discovery_future = (
                    mcp_manager.discover_mcps_for_agent_background(self.name)
                )
            return True
        except Exception:
            # Restore a consistent inactive state if a later activation step
            # raises, so background workers cannot mutate a broken agent and a
            # retry starts from a clean registry.
            self._clear_local_state()
            raise

    def clear_tools_from_llm(self):
        """Clear all tools from the LLM service."""
        self._tool_registrar._clear_from_llm()

    def validate_tool_use(self, tool_use: dict[str, Any]) -> str | None:
        """Validate a *tool_use* against its registered schema.

        Returns ``None`` when the tool call is valid, or a formatted error
        string when validation fails.

        Unknown (unregistered) tools are also rejected here.
        """
        from AgentCrew.modules.tools.input_validation import (
            extract_tool_input_schema,
            format_unknown_tool_error_text,
            format_validation_error_text,
            validate_tool_input,
        )

        tool_name = tool_use.get("name", "")

        # --- Unknown tool --------------------------------------------------
        tool_def = self._tool_registrar.get_tool_definition(tool_name)
        if tool_def is None:
            return format_unknown_tool_error_text(tool_name)

        # --- Extract schema and validate -----------------------------------
        input_schema = extract_tool_input_schema(tool_def)
        tool_input = tool_use.get("input", {})

        result = validate_tool_input(tool_input, input_schema)
        if result.valid:
            return None

        return format_validation_error_text(tool_name, result.issues)

    def resync_tools_to_llm(self):
        self._tool_registrar.sync_to_llm()

    @property
    def clean_history(self):
        return self.history

    def get_provider(self) -> str:
        return self.llm.provider_name if self.llm else ""

    def is_streaming(self) -> bool:
        return self.llm.is_stream if self.llm else False

    def format_message(
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Route message formatting through the collaborator."""
        return self._message_formatter.format_message(message_type, message_data)

    def configure_think(self, think_setting):
        if self.llm:
            self.llm.set_think(think_setting)

    async def execute_tool_call(self, tool_use: dict) -> Any:
        from AgentCrew.modules.events.hook_payloads import (
            ToolExecuteContext,
            ToolExecuteResult,
        )
        from AgentCrew.modules.events.hooks import (
            CancelOperation,
            HookPoints,
            HookRegistry,
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

    def record_conversation_usage(self, token_usage: TokenUsage) -> None:
        """Fold one completed turn's usage into this agent's conversation tracker.

        Called exactly once per completed turn at turn finalization. Cost is
        computed here against the agent's current LLM pricing, so display code
        never needs to re-estimate prices. ``token_usage`` is the turn-level
        merged usage already accumulated by the message handler across any
        recursive tool-call requests.
        """
        cost = self.calculate_usage_cost(
            token_usage.input_tokens,
            token_usage.output_tokens,
            token_usage.cached_tokens,
        )
        self.conversation_usage.add(
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
            cached_tokens=token_usage.cached_tokens,
            cache_creation_tokens=token_usage.cache_creation_tokens,
            total_input_tokens=token_usage.total_input_tokens,
            cost=cost,
        )

    def reset_conversation_usage(self) -> None:
        """Reset this agent's conversation tracker (new/loaded conversation)."""
        self.conversation_usage = ConversationUsage()

    def get_model(self) -> str:
        return f"{self.llm.provider_name}/{self.llm.model}" if self.llm else ""

    @property
    def is_pinned(self) -> bool:
        """True when the agent keeps its service on global updates."""
        return bool(self.model_selection and self.model_selection.is_pinned)

    def release_llm(self) -> None:
        """Release this agent's owned LLM service when safe.

        Detaches the agent's reference to its LLM, then closes the service
        exactly once when it is a dedicated service that is not cached by
        ServiceManager and not referenced by any remaining agent. Used when
        the agent is deregistered (e.g. config reload removes an agent) so
        dedicated clients are not orphaned. Sync/async scheduling is handled
        by ``ServiceManager.close_service``.
        """
        self._llm_lifecycle.release_llm()

    def ensure_reasoning_isolated(self) -> None:
        """Ensure this agent owns a dedicated LLM before reasoning is mutated.

        Swaps a shared service for a dedicated uncached clone when necessary,
        preserving lifecycle (tools/system prompt) via deactivate/activate.
        """
        self._llm_lifecycle.ensure_reasoning_isolated()

    def reapply_reasoning(self) -> None:
        """Isolate this agent's LLM, then recompute reasoning.

        Used after a model/service switch or config reload: the agent first
        ensures it owns a dedicated service, then applies its effective
        reasoning so no other agent sharing the previous service is affected.
        """
        self._llm_lifecycle.reapply_reasoning()

    def update_llm_service(self, new_llm_service: BaseLLMService) -> bool:
        """
        Update the LLM service used by this agent.

        Args:
            new_llm_service: The new LLM service to use

        Returns:
            True if the update was successful, False otherwise
        """
        return self._llm_lifecycle.update_llm_service(new_llm_service)

    async def update_llm_service_async(self, new_llm_service: BaseLLMService) -> bool:
        """
        Async variant of :meth:`update_llm_service`.

        Uses :meth:`deactivate_async` and :meth:`activate_async` so that
        MCP deregistration is awaited natively rather than through a
        synchronous ``asyncio.run()`` bridge.

        Args:
            new_llm_service: The new LLM service to use

        Returns:
            True if the update was successful, False otherwise
        """
        return await self._llm_lifecycle.update_llm_service_async(new_llm_service)

    def extract_last_user_message_for_memory(self, messages: list[dict]) -> str:
        """Return the last non-empty user text for memory storage.

        Supports string content, list string parts, and ``{type:
        "text", text: ...}`` parts.
        """
        return self._memory_coordinator.extract_last_user_message_for_memory(messages)

    def store_memory_if_available(
        self,
        user_message: str,
        messages: list[dict],
        current_response: str,
        session_id: str | None = None,
    ) -> None:
        """Store conversation memory when a memory service is available.

        Failures are logged and swallowed so memory problems never break the
        current turn.
        """
        self._memory_coordinator.store_memory_if_available(
            user_message, messages, current_response, session_id
        )

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
            self._tool_registrar.sync_to_llm()

    async def _sync_mcp_tools_after_discovery(self) -> None:
        """Final-sync tools after background MCP discovery (bounded, fail-open).

        Built-in tools are already synced during activation; this only gates
        the final synchronization that also includes MCP definitions. The
        wait is shielded so a first-message timeout never cancels the
        underlying manager-owned discovery; on timeout preprocessing
        continues with the already-synced built-in tools and the deferred
        flag stays set so the next message retries. Discovery failure also
        unblocks preprocessing and performs one final synchronization before
        clearing the deferred/loading state.
        """
        future = self._mcp_discovery_future
        if future is not None and not future.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(future)),
                    timeout=self.MCP_DISCOVERY_WAIT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    f"LocalAgent: MCP discovery still loading for '{self.name}' "
                    f"after {self.MCP_DISCOVERY_WAIT_SECONDS}s; continuing with "
                    f"built-in tools"
                )
                return
            except Exception as e:
                logger.warning(
                    f"LocalAgent: MCP discovery failed for '{self.name}': {e}"
                )
        self._tool_registrar.sync_to_llm()
        self._defer_tool_registration = False

    async def pre_process_message(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """
        Run context.build hooks and pre-processing, returning the final
        message list ready for the LLM call.

        Returns ``None`` if a before-hook cancels the operation.
        """
        if not self.llm:
            return None

        self._refresh_agent_skills()

        if self._defer_tool_registration:
            await self._sync_mcp_tools_after_discovery()

        from AgentCrew.modules.events.hook_payloads import (
            ContextBuildContext,
            ContextBuildResult,
        )
        from AgentCrew.modules.events.hooks import (
            HookPoints,
            HookRegistry,
        )

        _hooks = HookRegistry.get_instance()

        # ── context.build before hook ────────────────────────────────
        _before_payload: ContextBuildContext = {
            "system_prompt": self.llm.get_system_prompt() if self.llm else "",
            "messages": messages,
        }
        _before_ctx = await _hooks.run_before(
            HookPoints.CONTEXT_BUILD,
            **_before_payload,
        )
        if _before_ctx is None:
            logger.info("context.build cancelled by before hook — aborting turn")
            return None
        if "messages" in _before_ctx:
            messages = _before_ctx["messages"]
        _before_sp = _before_ctx.get("system_prompt", "")
        _current_sp = self.llm.get_system_prompt() if self.llm else ""
        if _before_sp != _current_sp:
            self.llm.set_system_prompt(_before_sp)
        # ─────────────────────────────────────────────────────────────

        self._context_manager.shrink_tool_results(messages)
        enhancing_messages = messages[:]
        self._context_manager.enhance_messages(enhancing_messages)
        from AgentCrew.modules.utils import VisionPreprocessingUtils

        # ── context.build after hook ─────────────────────────────────
        _after_envelope: ContextBuildResult = {
            "messages": enhancing_messages,
            "system_prompt": _before_sp,
        }
        _modified = await _hooks.run_after(
            HookPoints.CONTEXT_BUILD,
            result=_after_envelope,
        )
        if isinstance(_modified, dict):
            enhancing_messages = _modified.get("messages", enhancing_messages)
        # ─────────────────────────────────────────────────────────────

        final_messages = copy.deepcopy(enhancing_messages)

        await VisionPreprocessingUtils.preprocess_messages(
            final_messages,
            self.llm,
        )

        return final_messages

    async def process_messages(
        self,
        messages: list[dict[str, Any]] | None = None,
        callback: Callable | None = None,
    ):
        """
        Process messages using this agent.

        Delegates pre-processing (context.build hooks, enhancement, vision)
        to :meth:`pre_process_message`, then runs ``agent.process``
        before/after hooks and streams the LLM response.

        Args:
            messages: The messages to process
            callback: Optional ``(tool_uses, token_usage) -> None``

        Yields:
            ``(assistant_response, chunk_text, thinking_chunk)`` tuples.
        """

        if not self.llm:
            return

        final_messages = await self.pre_process_message(messages or self.history)
        if final_messages is None:
            return

        assistant_response = ""
        _tool_uses: list[dict[str, Any]] = []
        _token_usage = TokenUsage()

        from AgentCrew.modules.events.hook_payloads import (
            AgentProcessContext,
            AgentProcessResult,
        )
        from AgentCrew.modules.events.hooks import (
            HookPoints,
            HookRegistry,
        )

        _hooks = HookRegistry.get_instance()

        # ── agent.process before hook ────────────────────────────
        _agent_process_provider = self.llm._provider_name if self.llm else ""
        _agent_process_model_id = self.llm.model if self.llm else ""
        _process_before_payload: AgentProcessContext = {
            "model_id": _agent_process_model_id,
            "messages": final_messages,
            "provider": _agent_process_provider,
        }
        _process_before_ctx = await _hooks.run_before(
            HookPoints.AGENT_PROCESS,
            **_process_before_payload,
        )
        if _process_before_ctx is None:
            logger.info("agent.process cancelled by before hook — aborting turn")
            return

        _modified_model_id = _process_before_ctx.get(
            "model_id", _agent_process_model_id
        )
        if "messages" in _process_before_ctx:
            final_messages = _process_before_ctx["messages"]

        _original_model = self.llm.model if self.llm else None
        if _modified_model_id != _original_model:
            self.llm.model = _modified_model_id
        # ─────────────────────────────────────────────────────────

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

            # ── agent.process after hook ────────────────────────────
            _process_after_envelope: AgentProcessResult = {
                "tool_uses": _tool_uses,
                "token_usage": _token_usage,
            }
            _modified_result = await _hooks.run_after(
                HookPoints.AGENT_PROCESS,
                result=_process_after_envelope,
                model_id=_modified_model_id,
                messages=final_messages,
            )
            if isinstance(_modified_result, dict):
                _tool_uses = _modified_result.get("tool_uses", _tool_uses)
                _token_usage = _modified_result.get("token_usage", _token_usage)
            # ─────────────────────────────────────────────────────

            # Restore original model_id if it was changed by hook
            if _original_model is not None and _modified_model_id != _original_model:
                self.llm.model = _original_model

            self.token_usage = _token_usage
            if callback:
                callback(
                    self._message_formatter.filter_invalid_tool_uses(_tool_uses),
                    _token_usage,
                )
            else:
                self.tool_uses = _tool_uses

        except GeneratorExit as e:
            logger.warning(f"Stream processing interrupted: {e}")
            return
        except Exception as e:
            # Restore original model_id on error
            if _original_model is not None and _modified_model_id != _original_model:
                self.llm.model = _original_model
            logger.error(f"Error during message processing: {e}")
            logger.debug(f"Final messages at error time: {final_messages}")
            raise
