from typing import Dict, Optional, Callable
from AgentCrew.modules.llm.base import BaseLLMService
import os


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

        self.services: Dict[str, BaseLLMService] = {}

        # Lazy import factories - only import when called
        self.service_factories: Dict[str, Callable[[], BaseLLMService]] = {
            "claude": self._create_anthropic_service,
            "groq": self._create_groq_service,
            "openai": self._create_openai_service,
            "google": self._create_google_service,
            "deepinfra": self._create_deepinfra_service,
            "github_copilot": self._create_github_copilot_service,
            "copilot_response": self._create_copilot_response_service,
        }

        # Store details for custom providers
        self.custom_provider_details: Dict[str, Dict] = {}
        self._load_custom_provider_configs()

    # Lazy import factory methods
    def _create_anthropic_service(self) -> BaseLLMService:
        """Lazy import and create Anthropic service."""
        if os.getenv("ANTHROPIC_API_KEY"):
            from AgentCrew.modules.anthropic import AnthropicService

            return AnthropicService()
        raise RuntimeError("API key for Anthropic not found.")

    def _create_groq_service(self) -> BaseLLMService:
        """Lazy import and create Groq service."""
        if os.getenv("GROQ_API_KEY"):
            from AgentCrew.modules.groq import GroqService

            return GroqService()
        raise RuntimeError("API key for Groq not found.")

    def _create_openai_service(self) -> BaseLLMService:
        """Lazy import and create OpenAI service."""
        if os.getenv("OPENAI_API_KEY"):
            from AgentCrew.modules.openai import OpenAIResponseService

            return OpenAIResponseService()
        raise RuntimeError("API key for OpenAI not found.")

    def _create_google_service(self) -> BaseLLMService:
        """Lazy import and create Google AI service."""
        if os.getenv("GEMINI_API_KEY"):
            from AgentCrew.modules.google import GoogleAINativeService

            return GoogleAINativeService()
        raise RuntimeError("API key for Google AI not found.")

    def _create_deepinfra_service(self) -> BaseLLMService:
        """Lazy import and create DeepInfra service."""
        if os.getenv("DEEPINFRA_API_KEY"):
            from AgentCrew.modules.custom_llm import DeepInfraService

            return DeepInfraService()
        raise RuntimeError("API key for DeepInfra not found.")

    def _create_github_copilot_service(
        self, api_key: Optional[str] = None, provider_name: str = "github_copilot"
    ) -> BaseLLMService:
        """Lazy import and create GitHub Copilot service."""
        if os.getenv("GITHUB_COPILOT_API_KEY"):
            from AgentCrew.modules.custom_llm import GithubCopilotService

            return GithubCopilotService(api_key=api_key, provider_name=provider_name)
        raise RuntimeError("API key for GitHub Copilot not found.")

    def _create_copilot_response_service(
        self, api_key: Optional[str] = None, provider_name: str = "copilot_response"
    ) -> BaseLLMService:
        """Lazy import and create Copilot Response service."""
        if os.getenv("GITHUB_COPILOT_API_KEY"):
            from AgentCrew.modules.custom_llm import GithubCopilotResponseService

            return GithubCopilotResponseService(
                api_key=api_key, provider_name=provider_name
            )
        raise RuntimeError("API key for GitHub Copilot not found.")

    def _create_custom_llm_service(
        self,
        base_url: str,
        api_key: str,
        provider_name: str,
        extra_headers: Optional[Dict] = None,
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
        from AgentCrew.modules.config import ConfigManagement

        try:
            config_manager = ConfigManagement()
            custom_providers = config_manager.read_custom_llm_providers_config()
            for provider_config in custom_providers:
                name = provider_config.get("name")
                # We are interested in 'openai_compatible' type for CustomLLMService
                if name and provider_config.get("type") == "openai_compatible":
                    if not provider_config.get("api_base_url"):
                        print(
                            f"Warning: Custom provider '{name}' is missing 'api_base_url' and will be skipped."
                        )
                        continue
                    self.custom_provider_details[name] = {
                        "api_base_url": provider_config.get("api_base_url"),
                        "api_key": provider_config.get("api_key", ""),
                        "extra_headers": provider_config.get("extra_headers", {}),
                    }
        except Exception as e:
            print(f"Error loading custom LLM provider configurations for services: {e}")

    def initialize_standalone_service(self, provider: str) -> BaseLLMService:
        """
        Initializes and returns a new service instance for the specified provider.
        This does not cache the service instance in self.services.
        """
        if provider in self.custom_provider_details:
            details = self.custom_provider_details[provider]
            api_key = details.get("api_key", "")
            extra_headers = details.get("extra_headers", None)

            if not details.get("api_base_url"):
                raise ValueError(
                    f"Missing api_base_url for custom provider: {provider}"
                )

            if (
                details.get("api_base_url", "")
                .rstrip("/")
                .endswith(".githubcopilot.com")
            ):
                # Special case for GitHub Copilot compatible providers
                return self._create_github_copilot_service(
                    api_key=api_key, provider_name=provider
                )
            else:
                return self._create_custom_llm_service(
                    base_url=details["api_base_url"],
                    api_key=api_key,
                    provider_name=provider,
                    extra_headers=extra_headers,
                )
        elif provider in self.service_factories:
            return self.service_factories[provider]()
        else:
            known_providers = list(self.service_factories.keys()) + list(
                self.custom_provider_details.keys()
            )
            raise ValueError(
                f"Unknown provider: {provider}. Available providers: {', '.join(sorted(list(set(known_providers))))}"
            )

    def get_service(self, provider: str) -> BaseLLMService:
        """
        Get or create a service instance for the specified provider.
        Caches the instance for subsequent calls.

        Args:
            provider: The provider name

        Returns:
            An instance of the appropriate LLM service
        """
        if provider in self.services:
            return self.services[provider]

        service_instance: Optional[BaseLLMService] = None

        if provider in self.custom_provider_details:
            details = self.custom_provider_details[provider]
            api_key = details.get("api_key", "")
            extra_headers = details.get("extra_headers", None)

            if not details.get("api_base_url"):
                raise RuntimeError(
                    f"Configuration error: Missing api_base_url for custom provider {provider}"
                )

            try:
                if (
                    details.get("api_base_url", "")
                    .rstrip("/")
                    .endswith(".githubcopilot.com")
                ):
                    # Special case for GitHub Copilot compatible providers
                    service_instance = self._create_github_copilot_service(
                        api_key=api_key, provider_name=provider
                    )
                else:
                    service_instance = self._create_custom_llm_service(
                        base_url=details["api_base_url"],
                        api_key=api_key,
                        provider_name=provider,
                        extra_headers=extra_headers,
                    )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize custom provider service '{provider}': {str(e)}"
                )

        elif provider in self.service_factories:
            try:
                service_instance = self.service_factories[provider]()
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize built-in '{provider}' service: {str(e)}"
                )

        if service_instance:
            self.services[provider] = service_instance
            return service_instance
        else:
            known_providers = list(self.service_factories.keys()) + list(
                self.custom_provider_details.keys()
            )
            raise ValueError(
                f"Unknown provider: {provider}. Available providers: {', '.join(sorted(list(set(known_providers))))}"
            )

    def set_model(self, provider: str, model_id: str) -> bool:
        """
        Set the model for a specific provider.

        Args:
            provider: The provider name
            model_id: The model ID to use

        Returns:
            True if successful, False otherwise
        """
        service = self.get_service(provider)
        if hasattr(service, "model"):
            service.model = model_id
            return True
        return False
