from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp import RequestError
from loguru import logger

from AgentCrew.modules.acp.session_state import AcpSessionState
from AgentCrew.modules.agents import LocalAgent

if TYPE_CHECKING:
    from AgentCrew.modules.acp.mcp_orchestrator import McpOrchestrator
    from AgentCrew.modules.acp.tool_manager import AcpToolManager
    from AgentCrew.modules.agents import AgentManager

ACP_THOUGHT_LEVELS = ("none", "minimal", "low", "medium", "high")
ACP_ANTHROPIC_THOUGHT_BUDGETS = {
    "none": "0",
    "minimal": "1024",
    "low": "2048",
    "medium": "4096",
    "high": "8192",
}


class ModelController:
    def __init__(
        self,
        agent_manager: AgentManager,
        tool_manager: AcpToolManager,
        mcp_orchestrator: McpOrchestrator,
    ):
        self._agent_manager = agent_manager
        self._tool_manager = tool_manager
        self._mcp_orchestrator = mcp_orchestrator

    def _get_agent(self, agent_name: str) -> LocalAgent:
        agent = self._agent_manager.get_local_agent(agent_name)
        if agent is None:
            raise ValueError(f"Local agent '{agent_name}' not found")
        return agent

    def resolve_agent_name(self, agent_name: str | None) -> str:
        if agent_name and agent_name in self._agent_manager.agents:
            return agent_name
        current_agent = self._agent_manager.get_current_agent()
        if current_agent is not None:
            return current_agent.name
        for name, agent in self._agent_manager.agents.items():
            if isinstance(agent, LocalAgent):
                self._agent_manager.select_agent(name)
                return name
        raise ValueError("No local agents are available for ACP")

    def resolve_local_agent_mode(self, mode_id: str) -> str:
        agent = self._agent_manager.agents.get(mode_id)
        if isinstance(agent, LocalAgent):
            return mode_id
        raise RequestError.invalid_params(
            {
                "configId": "mode",
                "value": mode_id,
                "reason": "Unknown ACP local agent mode.",
            }
        )

    async def switch_session_agent(self, state: AcpSessionState, next_agent_name: str):
        prev_agent_name = state.agent_name
        if next_agent_name != prev_agent_name:
            self._tool_manager.restore_builtin_tools(state)
            state.agent_name = next_agent_name
            state.tool_state.acp_tools_configured = False
            if state.permission_broker:
                state.permission_broker.always_allowed.clear()
                state.permission_broker.always_denied.clear()
            if state.acp_mcp_server_configs:
                await self._mcp_orchestrator.cleanup_session_mcp_servers(state)
                for config in state.acp_mcp_server_configs:
                    config.enabledForAgents = [next_agent_name]
                await self._mcp_orchestrator.start_session_mcp_configs(state)
        else:
            state.agent_name = next_agent_name
        await self.apply_session_model_to_agent(state)

    async def apply_session_model_to_agent(self, state: AcpSessionState):
        if not state.model_id:
            state.model_id = self.current_agent_model_id(state.agent_name)
            state.thought_level = self.validated_thought_level_for_model(
                state.model_id, state.thought_level
            )
            await self.apply_session_thought_level_to_agent(state)
            return
        try:
            await self.switch_session_model(state, state.model_id)
        except RequestError:
            logger.warning(
                f"Ignoring invalid persisted ACP model '{state.model_id}' for agent '{state.agent_name}'"
            )
            state.model_id = self.current_agent_model_id(state.agent_name)
            state.thought_level = self.validated_thought_level_for_model(
                state.model_id, state.thought_level
            )
            await self.apply_session_thought_level_to_agent(state)

    async def switch_session_model(self, state: AcpSessionState, model_id: str):
        from AgentCrew.modules.config.global_config import GlobalConfig
        from AgentCrew.modules.llm.model_registry import ModelRegistry
        from AgentCrew.modules.llm.service_manager import ServiceManager

        registry = ModelRegistry.get_instance()
        model = registry.get_model(model_id)
        if model is None:
            raise RequestError.invalid_params(
                {
                    "configId": "model",
                    "value": model_id,
                    "reason": "Unknown AgentCrew model.",
                }
            )

        manager = ServiceManager.get_instance()
        manager.set_model_for_llm(model)

        new_llm_service = manager.get_service_for_model(model)
        manager.apply_model_defaults(new_llm_service, model)
        self._get_agent(state.agent_name).update_llm_service(new_llm_service)
        state.model_id = model_id
        state.thought_level = self.validated_thought_level_for_model(
            model_id, state.thought_level
        )
        await self.apply_session_thought_level_to_agent(state)
        try:
            GlobalConfig().set_last_used_model(model_id, model.provider)
        except Exception:
            logger.warning("Failed to save ACP selected model", exc_info=True)

    def current_agent_model_id(self, agent_name: str) -> str | None:
        try:
            return self._get_agent(agent_name).get_model()
        except Exception:
            logger.warning(
                f"Failed to resolve current model for ACP agent '{agent_name}'"
            )
            return None

    async def apply_session_thought_level_to_agent(self, state: AcpSessionState):
        if not self.model_supports_thinking(state.model_id):
            state.thought_level = "none"
            return
        level = self.validated_thought_level_for_model(
            state.model_id, state.thought_level
        )
        try:
            await self.switch_session_thought_level(state, level)
        except RequestError:
            logger.warning(
                f"Ignoring invalid persisted ACP thought level '{state.thought_level}' for model '{state.model_id}'"
            )
            state.thought_level = "none"
            try:
                await self.switch_session_thought_level(state, "none")
            except RequestError:
                logger.warning("Failed to reset ACP thought level", exc_info=True)

    async def switch_session_thought_level(
        self, state: AcpSessionState, thought_level: str
    ):
        if thought_level not in ACP_THOUGHT_LEVELS:
            raise RequestError.invalid_params(
                {
                    "configId": "thought_level",
                    "value": thought_level,
                    "reason": "Unknown ACP thought level.",
                }
            )
        if not self.model_supports_thinking(state.model_id):
            state.thought_level = "none"
            raise RequestError.invalid_params(
                {
                    "configId": "thought_level",
                    "value": thought_level,
                    "reason": "The selected model does not support thinking.",
                }
            )
        agent = self._get_agent(state.agent_name)
        agent_value = self.thought_level_to_agent_value(agent, thought_level)
        try:
            applied = agent.llm.set_think(agent_value) if agent.llm else False
        except ValueError as e:
            raise RequestError.invalid_params(
                {
                    "configId": "thought_level",
                    "value": thought_level,
                    "reason": str(e),
                }
            ) from e
        if applied is False:
            raise RequestError.invalid_params(
                {
                    "configId": "thought_level",
                    "value": thought_level,
                    "reason": "The selected model rejected this thought level.",
                }
            )
        state.thought_level = thought_level

    def validated_thought_level_for_model(
        self, model_id: str | None, thought_level: str | None
    ) -> str:
        if not self.model_supports_thinking(model_id):
            return "none"
        if thought_level in ACP_THOUGHT_LEVELS:
            return thought_level
        return self.default_thought_level_for_model(model_id)

    def default_thought_level_for_model(self, model_id: str | None) -> str:
        if not self.model_supports_thinking(model_id):
            return "none"
        try:
            from AgentCrew.modules.llm.model_registry import ModelRegistry

            model = ModelRegistry.get_instance().get_model(model_id or "")
            default_reasoning = getattr(model, "default_reasoning", None)
            if default_reasoning in ACP_THOUGHT_LEVELS:
                return default_reasoning
        except Exception:
            logger.warning(
                f"Failed to resolve default ACP thought level for model '{model_id}'"
            )
        return "none"

    def model_supports_thinking(self, model_id: str | None) -> bool:
        if not model_id:
            return False
        try:
            from AgentCrew.modules.llm.model_registry import ModelRegistry

            model = ModelRegistry.get_instance().get_model(model_id)
            return bool(model and "thinking" in model.capabilities)
        except Exception:
            logger.warning(
                f"Failed to resolve ACP thinking capability for model '{model_id}'"
            )
            return False

    def thought_level_to_agent_value(
        self, agent: LocalAgent, thought_level: str
    ) -> str:
        provider_name = getattr(agent.llm, "provider_name", "")
        class_name = agent.llm.__class__.__name__.lower()
        if (
            provider_name in ("claude", "opencode_anthropic")
            or "anthropic" in class_name
        ):
            return ACP_ANTHROPIC_THOUGHT_BUDGETS[thought_level]
        return thought_level

    def build_config_options(
        self,
        current_agent_name: str,
        current_model_id: str | None = None,
        current_thought_level: str | None = None,
    ) -> list[Any]:
        from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

        from AgentCrew.modules.llm.model_registry import ModelRegistry

        config_options = []
        mode_options = [
            SessionConfigSelectOption(
                value=name,
                name=name,
                description=getattr(agent, "description", None),
            )
            for name, agent in self._agent_manager.agents.items()
            if isinstance(agent, LocalAgent)
        ]
        if mode_options:
            config_options.append(
                SessionConfigOptionSelect(
                    id="mode",
                    name="Agent",
                    description="Select the AgentCrew local agent for this ACP session.",
                    category="mode",
                    type="select",
                    current_value=current_agent_name,
                    options=mode_options,
                )
            )

        registry = ModelRegistry.get_instance()
        model_options = [
            SessionConfigSelectOption(
                value=full_model_id,
                name=f"{full_model_id} — {model.name}",
                description=model.description,
            )
            for full_model_id, model in sorted(registry.models.items())
        ]
        selected_model_id = current_model_id or self.current_agent_model_id(
            current_agent_name
        )
        if model_options and selected_model_id:
            config_options.append(
                SessionConfigOptionSelect(
                    id="model",
                    name="Model",
                    description="Select the LLM model for this AgentCrew ACP session.",
                    category="model",
                    type="select",
                    current_value=selected_model_id,
                    options=model_options,
                )
            )

        if self.model_supports_thinking(selected_model_id):
            thought_options = [
                SessionConfigSelectOption(value="none", name="Off"),
                SessionConfigSelectOption(value="minimal", name="Minimal"),
                SessionConfigSelectOption(value="low", name="Low"),
                SessionConfigSelectOption(value="medium", name="Medium"),
                SessionConfigSelectOption(value="high", name="High"),
            ]
            config_options.append(
                SessionConfigOptionSelect(
                    id="thought_level",
                    name="Thought Level",
                    description="Select the thinking level for this AgentCrew ACP session.",
                    category="thought_level",
                    type="select",
                    current_value=self.validated_thought_level_for_model(
                        selected_model_id, current_thought_level
                    ),
                    options=thought_options,
                )
            )
        return config_options

    def build_models(self, current_model_id: str):
        from acp.schema import ModelInfo, SessionModelState

        from AgentCrew.modules.llm.model_registry import ModelRegistry

        registry = ModelRegistry.get_instance()
        return SessionModelState(
            available_models=[
                ModelInfo(model_id=id, name=m.name) for id, m in registry.models.items()
            ],
            current_model_id=current_model_id,
        )

    def build_modes(self, current_agent_name: str):
        from acp.schema import SessionMode, SessionModeState

        modes = [
            SessionMode(
                id=name,
                name=name,
                description=getattr(agent, "description", None),
            )
            for name, agent in self._agent_manager.agents.items()
            if isinstance(agent, LocalAgent)
        ]
        if not modes:
            return None
        return SessionModeState(
            available_modes=modes, current_mode_id=current_agent_name
        )
