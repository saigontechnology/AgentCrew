from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from AgentCrew.modules.llm.base import BaseLLMService
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.model_selection import (
    ModelSelection,
    ModelSelectionSource,
)

if TYPE_CHECKING:
    from AgentCrew.modules.llm.types import Model


class ServiceManager:
    """Singleton manager for LLM service instances with lazy loading."""

    _instance = None

    @classmethod
    def get_instance(cls):
        """Get the singleton instance of ServiceManager."""
        if cls._instance is None:
            cls._instance = ServiceManager()
        return cls._instance

    def __init__(self):
        """Initialize the service manager with empty service instances."""
        if ServiceManager._instance is not None:
            raise RuntimeError(
                "ServiceManager is a singleton. Use get_instance() instead."
            )

        self.services: dict[str, BaseLLMService] = {}

        # Pending async close tasks retained for draining at shutdown, and a
        # dedup set so each owned dedicated service is closed exactly once.
        self._pending_closes: set[asyncio.Task] = set()
        self._closed_services: set[BaseLLMService] = set()

        # Lazy import factories keyed by service implementation name.
        # A single vendor (e.g. openai) may expose multiple service families.
        self.service_factories: dict[str, Callable[[], BaseLLMService]] = {
            "claude": self._create_anthropic_service,
            "openai": self._create_openai_service,
            "openai_response": self._create_openai_response_service,
            "openai_codex": self._create_openai_codex_service,
            "google": self._create_google_service,
            "deepinfra": self._create_deepinfra_service,
            "crofai": self._create_crofai_service,
            "together": self._create_together_service,
            "opencode_go": self._create_opencode_go_service,
            "opencode_anthropic": self._create_opencode_anthropic_service,
            "github_copilot": self._create_github_copilot_service,
            "copilot_response": self._create_copilot_response_service,
            "fireworks": self._create_fireworks_service,
            "commandcode": self._create_commandcode_service,
            "commandcode_anthropic": self._create_commandcode_anthropic_service,
        }

        # Store details for custom providers
        self.custom_provider_details: dict[str, dict] = {}
        self._load_custom_provider_configs()

    # Lazy import factory methods
    def _create_anthropic_service(self) -> BaseLLMService:
        """Lazy import and create Anthropic service."""
        from AgentCrew.modules.anthropic import AnthropicService

        return AnthropicService()

    def _create_opencode_anthropic_service(self) -> BaseLLMService:
        if not os.getenv("OPENCODE_API_KEY"):
            logger.error("API key for OpenCode not found.")
        from AgentCrew.modules.anthropic import AnthropicService

        llm = AnthropicService(
            os.getenv("OPENCODE_API_KEY", ""),
            "https://opencode.ai/zen/go",
            provider_name="opencode_go",
        )
        llm.model = "minimax-m2.7"
        return llm

    def _create_openai_service(self) -> BaseLLMService:
        """Lazy import and create OpenAI Chat Completions service."""
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("API key for OpenAI not found.")
        from AgentCrew.modules.openai.service import OpenAIService

        return OpenAIService()

    def _create_openai_response_service(self) -> BaseLLMService:
        """Lazy import and create OpenAI Response API service."""
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("API key for OpenAI not found.")
        from AgentCrew.modules.openai import OpenAIResponseService

        return OpenAIResponseService()

    def _create_openai_codex_service(self) -> BaseLLMService:
        """Lazy import and create OpenAI Codex service using ChatGPT subscription OAuth."""
        from AgentCrew.modules.openai_codex import OpenAICodexService

        return OpenAICodexService()

    def _create_google_service(self) -> BaseLLMService:
        """Lazy import and create Google AI service."""
        if not os.getenv("GEMINI_API_KEY"):
            logger.error("API key for Google AI not found.")
        from AgentCrew.modules.google import GoogleAINativeService

        return GoogleAINativeService()

    def _create_deepinfra_service(self) -> BaseLLMService:
        """Lazy import and create DeepInfra service."""
        if not os.getenv("DEEPINFRA_API_KEY"):
            logger.error("API key for DeepInfra not found.")
        from AgentCrew.modules.custom_llm import DeepInfraService

        return DeepInfraService()

    def _create_crofai_service(self) -> BaseLLMService:
        """Lazy import and create CrofAI service."""
        if not os.getenv("CROFAI_API_KEY"):
            logger.error("API key for CrofAI not found.")
        from AgentCrew.modules.custom_llm import CrofAIService

        return CrofAIService()

    def _create_together_service(self) -> BaseLLMService:
        """Lazy import and create Together service."""
        if not os.getenv("TOGETHER_API_KEY"):
            logger.error("API key for Together not found.")
        from AgentCrew.modules.together import TogetherAIService

        return TogetherAIService()

    def _create_opencode_go_service(self) -> BaseLLMService:
        """Lazy import and create OpenCode Go service."""
        from AgentCrew.modules.custom_llm import OpenCodeService

        api_key = os.getenv("OPENCODE_API_KEY", "")
        if not api_key:
            logger.error("API key for OpenCode Go not found.")
        llm = OpenCodeService(
            base_url="https://opencode.ai/zen/go/v1",
            api_key=api_key,
            provider_name="opencode_go",
        )
        llm.model = "kimi-k2.6"
        return llm

    def _create_github_copilot_service(
        self, api_key: str | None = None, provider_name: str = "github_copilot"
    ) -> BaseLLMService:
        """Lazy import and create GitHub Copilot service."""
        if not os.getenv("GITHUB_COPILOT_API_KEY"):
            logger.error("API key for GitHub Copilot not found.")
        from AgentCrew.modules.custom_llm import GithubCopilotService

        return GithubCopilotService(api_key=api_key, provider_name=provider_name)

    def _create_copilot_response_service(
        self, api_key: str | None = None, provider_name: str = "github_copilot"
    ) -> BaseLLMService:
        """Lazy import and create Copilot Response service."""
        if not os.getenv("GITHUB_COPILOT_API_KEY"):
            logger.error("API key for GitHub Copilot not found.")
        from AgentCrew.modules.custom_llm import GithubCopilotResponseService

        return GithubCopilotResponseService(
            api_key=api_key, provider_name=provider_name
        )

    def _create_fireworks_service(self) -> BaseLLMService:
        """Lazy import and create Fireworks AI service."""
        if not os.getenv("FIREWORKS_API_KEY"):
            logger.error("API key for Fireworks not found.")
        from AgentCrew.modules.custom_llm import FireworksService

        return FireworksService()

    def _create_commandcode_service(self) -> BaseLLMService:
        """Lazy import and create CommandCode service (OpenAI-compatible endpoint)."""
        api_key = os.getenv("COMMAND_CODE_API_KEY")
        if not api_key:
            logger.error("COMMAND_CODE_API_KEY not found in environment variables.")
        from AgentCrew.modules.custom_llm import CommandCodeService

        return CommandCodeService()

    def _create_commandcode_anthropic_service(self) -> BaseLLMService:
        """Lazy import and create CommandCode Anthropic service (Anthropic Messages endpoint)."""
        api_key = os.getenv("COMMAND_CODE_API_KEY", "")
        if not api_key:
            logger.error("COMMAND_CODE_API_KEY not found in environment variables.")
        from AgentCrew.modules.anthropic import AnthropicService

        return AnthropicService(
            api_key=api_key,
            base_url="https://api.commandcode.ai/provider",
            provider_name="commandcode",
        )

    def _create_custom_llm_service(
        self,
        base_url: str,
        api_key: str,
        provider_name: str,
        extra_headers: dict | None = None,
    ) -> BaseLLMService:
        """Lazy import and create Custom LLM service."""
        from AgentCrew.modules.custom_llm import CustomLLMService

        return CustomLLMService(
            base_url=base_url,
            api_key=api_key,
            provider_name=provider_name,
            extra_headers=extra_headers,
        )

    def _load_custom_provider_configs(self):
        """Loads configurations for custom LLM providers."""
        from AgentCrew.modules.config.global_config import GlobalConfig

        try:
            custom_providers = GlobalConfig().read_custom_llm_providers_config()
            for provider_config in custom_providers:
                name = provider_config.get("name")
                # We are interested in 'openai_compatible' type for CustomLLMService
                if name and provider_config.get("type") == "openai_compatible":
                    if not provider_config.get("api_base_url"):
                        logger.warning(
                            f"Custom provider '{name}' is missing 'api_base_url' and will be skipped."
                        )
                        continue
                    self.custom_provider_details[name] = {
                        "api_base_url": provider_config.get("api_base_url"),
                        "api_key": provider_config.get("api_key", ""),
                        "extra_headers": provider_config.get("extra_headers", {}),
                    }
        except Exception as e:
            logger.warning(
                f"Error loading custom LLM provider configurations for services: {e}"
            )

    def initialize_standalone_service(self, service_name: str) -> BaseLLMService:
        """
        Initializes and returns a new service instance for the specified service name.
        This does not cache the service instance in self.services.
        """
        if service_name in self.custom_provider_details:
            details = self.custom_provider_details[service_name]
            api_key = details.get("api_key", "")
            extra_headers = details.get("extra_headers", None)

            if not details.get("api_base_url"):
                raise ValueError(
                    f"Missing api_base_url for custom provider: {service_name}"
                )

            if (
                details.get("api_base_url", "")
                .rstrip("/")
                .endswith(".githubcopilot.com")
            ):
                # Special case for GitHub Copilot compatible providers
                return self._create_github_copilot_service(
                    api_key=api_key, provider_name=service_name
                )
            else:
                return self._create_custom_llm_service(
                    base_url=details["api_base_url"],
                    api_key=api_key,
                    provider_name=service_name,
                    extra_headers=extra_headers,
                )
        elif service_name in self.service_factories:
            return self.service_factories[service_name]()
        else:
            known = list(self.service_factories.keys()) + list(
                self.custom_provider_details.keys()
            )
            raise ValueError(
                f"Unknown service: {service_name}. Available services: {', '.join(sorted(set(known)))}"
            )

    def initialize_standalone_service_for_model(self, model: Model) -> BaseLLMService:
        """Initialize a standalone service for the given model."""
        return self.initialize_standalone_service(model.resolved_service_name())

    def get_service(
        self, service_name: str, provider_name: str | None = None
    ) -> BaseLLMService:
        """
        Get or create a service instance for the specified service name.
        Caches the instance for subsequent calls.

        Args:
            service_name: The service implementation name (e.g. "openai", "openai_response")

        Returns:
            An instance of the appropriate LLM service
        """
        if service_name in self.services:
            return self.services[service_name]

        service_instance: BaseLLMService | None = None

        if service_name in self.custom_provider_details:
            details = self.custom_provider_details[service_name]
            api_key = details.get("api_key", "")
            extra_headers = details.get("extra_headers", None)

            if not details.get("api_base_url"):
                raise RuntimeError(
                    f"Configuration error: Missing api_base_url for custom provider {service_name}"
                )

            try:
                if (
                    details.get("api_base_url", "")
                    .rstrip("/")
                    .endswith(".githubcopilot.com")
                ):
                    # Special case for GitHub Copilot compatible providers
                    service_instance = self._create_github_copilot_service(
                        api_key=api_key, provider_name=service_name
                    )
                else:
                    service_instance = self._create_custom_llm_service(
                        base_url=details["api_base_url"],
                        api_key=api_key,
                        provider_name=provider_name or service_name,
                        extra_headers=extra_headers,
                    )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize custom provider service '{service_name}': {e!s}"
                )

        elif service_name in self.service_factories:
            try:
                service_instance = self.service_factories[service_name]()
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize built-in '{service_name}' service: {e!s}"
                )

        if service_instance:
            self.services[service_name] = service_instance
            return service_instance
        else:
            known = list(self.service_factories.keys()) + list(
                self.custom_provider_details.keys()
            )
            raise ValueError(
                f"Unknown service: {service_name}. Available services: {', '.join(sorted(set(known)))}"
            )

    def get_service_for_model(self, model: Model) -> BaseLLMService:
        """
        Get or create a service instance for the given model,
        using the model's declared service_name.
        """
        return self.get_service(model.resolved_service_name(), model.provider)

    def get_service_for_provider(self, provider: str) -> BaseLLMService:
        """
        Get or create a service instance for the given provider name,
        by resolving the provider's default model and using its service_name.
        This preserves backward compatibility when only a provider string is known.
        """
        registry = ModelRegistry.get_instance()
        models = registry.get_models_by_provider(provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            return self.get_service_for_model(default_model)
        # Fallback: treat provider as a direct service name
        return self.get_service(provider)

    def set_model_for_llm(self, model: Model):
        """Set the model on the service instance declared by the given model."""
        service = self.get_service_for_model(model)
        service.model = model.id
        self.apply_model_defaults(service, model)

    def apply_model_defaults(self, service: BaseLLMService, model: Model) -> None:
        service.model = model.id
        if not model or not hasattr(service, "reasoning_effort"):
            return

        if model.default_reasoning is not None:
            service.reasoning_effort = model.default_reasoning

    def clone_service(self, service: BaseLLMService) -> BaseLLMService:
        """Create a dedicated uncached service matching ``service``'s current model.

        Used to isolate per-agent reasoning so one agent's ``/think`` or config
        effort never mutates a service shared with another agent. Registered
        models resolve through their declared service family; raw/unregistered
        models fall back to the provider service with the raw model id.
        """
        provider = service.provider_name
        model_id = f"{provider}/{service.model}"
        model = ModelRegistry.get_instance().get_model(model_id)
        if model is not None:
            new_service = self.initialize_standalone_service_for_model(model)
            self.apply_model_defaults(new_service, model)
            return new_service
        new_service = self.initialize_standalone_service(provider)
        new_service.model = service.model
        return new_service

    def get_service_for_selection(
        self,
        selection: ModelSelection,
        *,
        standalone: bool = False,
    ) -> BaseLLMService:
        """Bind a model selection to a service instance.

        Registered models use their resolved service family; unregistered
        explicit/environment models fall back to the provider service with the
        raw model id; otherwise the provider default is used. When
        ``standalone`` is set an uncached service is created (A2A).
        """
        registry = ModelRegistry.get_instance()
        provider = selection.provider
        model_id = selection.model_id

        if model_id:
            model = registry.get_model(model_id)
            if model:
                registry.set_current_model(model_id)
                if standalone:
                    service = self.initialize_standalone_service_for_model(model)
                else:
                    service = self.get_service_for_model(model)
                self.apply_model_defaults(service, model)
                return service
            if selection.source in (
                ModelSelectionSource.RUNTIME_ARGS,
                ModelSelectionSource.ENVIRONMENT,
            ):
                if standalone:
                    service = self.initialize_standalone_service(provider)
                else:
                    service = self.get_service_for_provider(provider)
                if selection.relative_model_id:
                    service.model = selection.relative_model_id
                return service

        if standalone:
            service = self.initialize_standalone_service(provider)
            models = registry.get_models_by_provider(provider)
            if models:
                default_model = next((m for m in models if m.default), models[0])
                self.apply_model_defaults(service, default_model)
            return service
        models = registry.get_models_by_provider(provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            registry.set_current_model(f"{default_model.provider}/{default_model.id}")
            service = self.get_service_for_model(default_model)
            self.apply_model_defaults(service, default_model)
            return service
        return self.get_service(provider)

    def close_service(self, service: BaseLLMService | None) -> None:
        """Close an owned dedicated LLM service exactly once.

        Safe from sync and async contexts: with a running event loop the close
        is scheduled and tracked for draining at shutdown; otherwise it runs
        inline. Never closes ServiceManager-cached services or services that
        were already closed.
        """
        if service is None:
            return
        if service in self.services.values():
            return  # cached — ServiceManager owns it
        if service in self._closed_services:
            return  # already closed
        self._closed_services.add(service)
        close = getattr(service, "close", None)
        if close is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._close_sync(service)
        else:
            task = asyncio.create_task(self._close_async(service))
            self._pending_closes.add(task)
            task.add_done_callback(self._pending_closes.discard)

    def _close_sync(self, service: BaseLLMService) -> None:
        """Close a service inline when no event loop is running."""
        try:
            asyncio.run(service.close())
        except Exception as e:
            logger.warning(f"Failed to close LLM service {service!r}: {e}")

    async def _close_async(self, service: BaseLLMService) -> None:
        """Close a service as a tracked task on the running loop."""
        try:
            await service.close()
        except Exception as e:
            logger.warning(f"Failed to close LLM service {service!r}: {e}")

    async def drain_pending_closes(self) -> None:
        """Await and clear all scheduled close tasks (called at shutdown)."""
        pending = list(self._pending_closes)
        self._pending_closes.clear()
        if not pending:
            return
        results = await asyncio.gather(*pending, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"LLM service close failed during drain: {result}")
