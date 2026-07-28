import importlib
import os

from loguru import logger

from AgentCrew.modules.config.global_config import GlobalConfig

from .types import Model

# Mapping of provider/service names to their required API key environment variables.
PROVIDER_API_KEY_MAP: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_response": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "crofai": "CROFAI_API_KEY",
    "together": "TOGETHER_API_KEY",
    "opencode_go": "OPENCODE_API_KEY",
    "opencode_anthropic": "OPENCODE_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "github_copilot": "GITHUB_COPILOT_API_KEY",
    "copilot_response": "GITHUB_COPILOT_API_KEY",
    "commandcode": "COMMAND_CODE_API_KEY",
    "commandcode_anthropic": "COMMAND_CODE_API_KEY",
}

# Mapping of provider name → (module_path, variable_name) for lazy-loading
# model definitions.  Only providers whose API key is available are loaded
# during initialization; others are loaded on demand when first requested.
PROVIDER_MODEL_MODULES: dict[str, tuple[str, str]] = {
    "claude": ("AgentCrew.modules.llm.model_definitions.anthropic", "ANTHROPIC_MODELS"),
    "openai": ("AgentCrew.modules.llm.model_definitions.openai", "OPENAI_MODELS"),
    "openai_codex": (
        "AgentCrew.modules.llm.model_definitions.openai_codex",
        "OPENAI_CODEX_MODELS",
    ),
    "google": ("AgentCrew.modules.llm.model_definitions.google", "GOOGLE_MODELS"),
    "deepinfra": (
        "AgentCrew.modules.llm.model_definitions.deepinfra",
        "DEEPINFRA_MODELS",
    ),
    "crofai": ("AgentCrew.modules.llm.model_definitions.crofai", "CROFAI_MODELS"),
    "together": ("AgentCrew.modules.llm.model_definitions.together", "TOGETHER_MODELS"),
    "opencode_go": (
        "AgentCrew.modules.llm.model_definitions.opencode",
        "OPENCODE_GO_MODELS",
    ),
    "fireworks": (
        "AgentCrew.modules.llm.model_definitions.fireworks",
        "FIREWORKS_MODELS",
    ),
    "github_copilot": (
        "AgentCrew.modules.llm.model_definitions.github_copilot",
        "GITHUB_COPILOT_MODELS",
    ),
    "commandcode": (
        "AgentCrew.modules.llm.model_definitions.commandcode",
        "COMMANDCODE_MODELS",
    ),
}


class ModelRegistry:
    """Registry for available LLM models."""

    _instance = None

    @classmethod
    def get_instance(cls):
        """Get the singleton instance of ModelRegistry."""
        if cls._instance is None:
            cls._instance = ModelRegistry()
        return cls._instance

    def __init__(self):
        """Initialize the model registry with default models."""
        if ModelRegistry._instance is not None:
            raise RuntimeError(
                "ModelRegistry is a singleton. Use get_instance() instead."
            )

        self.models: dict[str, Model] = {}
        self.current_model: Model | None = None
        self._loaded_providers: set[str] = set()
        self._initialize_models()

    @classmethod
    def get_model_capabilities(cls, full_qualified_mode_id):
        registry = ModelRegistry.get_instance()
        model = registry.get_model(full_qualified_mode_id)
        if not model:
            logger.warning(f"Model not found in registry: {full_qualified_mode_id}")
            return ["tool_use", "stream"]
        return model.capabilities

    @classmethod
    def get_model_limit(cls, full_qualified_mode_id):
        registry = ModelRegistry.get_instance()
        model = registry.get_model(full_qualified_mode_id)
        if not model:
            logger.warning(f"Model not found in registry: {full_qualified_mode_id}")
            return 128_000
        return model.max_context_token

    @classmethod
    def get_model_sample_params(cls, full_qualified_mode_id):
        registry = ModelRegistry.get_instance()
        model = registry.get_model(full_qualified_mode_id)
        if not model:
            logger.warning(f"Model not found in registry: {full_qualified_mode_id}")
            return None
        return model.force_sample_params

    def _load_provider_models(self, provider: str) -> None:
        """Lazily load models for a specific provider if not already loaded.

        This allows runtime loading of models for providers whose API key
        was not available at startup (e.g. the user set it later).
        """
        if provider in self._loaded_providers:
            return

        module_info = PROVIDER_MODEL_MODULES.get(provider)
        if module_info is None:
            return  # Unknown or custom provider

        module_path, attr_name = module_info
        try:
            mod = importlib.import_module(module_path)
            models = getattr(mod, attr_name, [])
            for model in models:
                self.register_model(model)
            self._loaded_providers.add(provider)
        except Exception as e:
            logger.warning(
                f"Failed to lazily load models for provider '{provider}': {e}"
            )

    def _load_custom_models_from_config(self):
        """Loads models from custom LLM provider configurations and registers them."""
        try:
            custom_providers_config = GlobalConfig().read_custom_llm_providers_config()

            for provider_config in custom_providers_config:
                provider_name = provider_config.get("name")
                for model_data_dict in provider_config.get("available_models", []):
                    try:
                        if provider_name:
                            model_data_dict["provider"] = provider_name
                        else:
                            logger.warning(
                                f"Skipping model due to missing provider name in config: ID '{model_data_dict.get('id', 'N/A')}'"
                            )
                            continue
                        model = Model(**model_data_dict)
                        self.register_model(model)
                    except Exception as e:
                        logger.warning(
                            f"Error loading custom model '{model_data_dict.get('id')}' for provider '{provider_name}': {e}"
                        )
        except Exception as e:
            logger.warning(
                f"Error loading custom LLM providers configuration for models: {e}"
            )

    def _initialize_models(self):
        """Initialize the registry with models for providers whose API keys are available.

        Instead of importing all provider model modules upfront (which is expensive
        due to Pydantic model construction), this loads only the modules for
        providers whose required API key is present in the environment.
        """
        for provider, (module_path, attr_name) in PROVIDER_MODEL_MODULES.items():
            # Check if this provider's API key is available
            env_var = PROVIDER_API_KEY_MAP.get(provider)
            if env_var is not None and not os.getenv(env_var):
                logger.info(
                    f"Skipping provider '{provider}': API key {env_var} not set"
                )
                continue

            try:
                mod = importlib.import_module(module_path)
                models = getattr(mod, attr_name, [])
                for model in models:
                    self.register_model(model)
                self._loaded_providers.add(provider)
            except Exception as e:
                logger.warning(f"Failed to load models for provider '{provider}': {e}")

        # Load and register custom models from the configuration file
        self._load_custom_models_from_config()

        # Set the default model
        for model in self.models.values():
            if model.default:
                self.current_model = model
                break

    def register_model(self, model: Model):
        """
        Register a model in the registry.

        Args:
            model: The model to register
        """
        self.models[f"{model.provider}/{model.id}"] = model

    def get_model(self, model_id: str) -> Model | None:
        """
        Get a model by ID.

        Args:
            model_id: The model ID

        Returns:
            The model if found, None otherwise
        """
        return self.models.get(model_id)

    def get_models_by_provider(self, provider: str) -> list[Model]:
        """
        Get all models for a specific provider.

        Models are loaded lazily — if the provider's model module hasn't been
        imported yet (e.g. the API key was set after startup), it is loaded
        on demand.

        Args:
            provider: The provider name

        Returns:
            list of models for the provider
        """
        self._load_provider_models(provider)
        return [model for model in self.models.values() if model.provider == provider]

    def set_current_model(self, full_qualified_model_id: str) -> bool:
        """
        Set the current model by ID.

        Args:
            model_id: The model ID

        Returns:
            True if successful, False otherwise
        """
        model = self.get_model(full_qualified_model_id)
        if model:
            self.current_model = model
            return True
        logger.warning(
            "Model with ID '%s' not found in registry.", full_qualified_model_id
        )
        return False

    def get_current_model(self) -> Model | None:
        """
        Get the current model.

        Returns:
            The current model if set, None otherwise
        """
        return self.current_model

    def get_providers(self) -> list[str]:
        """
        Get all unique provider names from the registered models.

        Returns:
            A list of unique provider names.
        """
        providers = set()
        for model in self.models.values():
            providers.add(model.provider)
        return list(providers)
