from __future__ import annotations

from typing import TYPE_CHECKING

from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.service_manager import ServiceManager

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class ModelCommands:
    """Handles model-related slash commands."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler

    async def handle_model(self, command: str) -> tuple[bool, bool]:
        """
        Handle the /model command to switch models or list available models.

        Returns:
            Tuple of (exit_flag, clear_flag)
        """
        model_id = command[7:].strip()
        registry = ModelRegistry.get_instance()
        llm_manager = ServiceManager.get_instance()

        if not model_id:
            models_by_provider = {}
            for provider in registry.get_providers():
                models = registry.get_models_by_provider(provider)
                if models:
                    models_by_provider[provider] = []
                    for model in models:
                        current = (
                            registry.current_model
                            and registry.current_model.id == model.id
                        )
                        models_by_provider[provider].append(
                            {
                                "id": f"{model.provider}/{model.id}",
                                "name": model.name,
                                "description": model.description,
                                "capabilities": model.capabilities,
                                "current": current,
                            }
                        )

            self.message_handler.bus.emit_sync(
                AppEvents.MODELS_LISTED, models_by_provider=models_by_provider
            )
            return False, True

        if registry.set_current_model(model_id):
            model = registry.get_current_model()
            if model:
                llm_manager.set_model_for_llm(model)

                new_llm_service = llm_manager.get_service_for_model(model)

                await self.message_handler.agent_manager.update_llm_service_async(
                    new_llm_service
                )

                try:
                    GlobalConfig().set_last_used_model(model_id, model.provider)
                except Exception as e:
                    print(f"Warning: Failed to save last used model: {e}")

                self.message_handler.bus.emit_sync(
                    AppEvents.MODEL_CHANGED,
                    id=model.id,
                    name=model.name,
                    provider=model.provider,
                )
            else:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR, message="Failed to switch model."
                )
        else:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Unknown model: {model_id}"
            )

        return False, True
