from typing import Dict, List, Optional
from .types import Model
from .constants import AVAILABLE_MODELS
from loguru import logger
from AgentCrew.modules.config import ConfigManagement


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

        self.models: Dict[str, Model] = {}
        self.current_model: Optional[Model] = None
        self._initialize_models()

    @classmethod
    def get_model_capabilities(cls, mode_id):
        registry = ModelRegistry.get_instance()
        model = registry.get_model(mode_id)
        if not model:
            logger.warning(f"Model not found in registry: {mode_id}")
            return []
        return model.capabilities

    def _load_custom_models_from_config(self):
        """Loads models from custom LLM provider configurations and registers them."""
        try:
            config_manager = ConfigManagement()
            custom_providers_config = config_manager.read_custom_llm_providers_config()

            for provider_config in custom_providers_config:
                provider_name = provider_config.get("name")
                for model_data_dict in provider_config.get("available_models", []):
                    try:
                        if provider_name:
                            model_data_dict["provider"] = provider_name
                        else:
                            print(
                                f"Warning: Skipping model due to missing provider name in config: ID '{model_data_dict.get('id', 'N/A')}'"
                            )
                            continue
                        model = Model(**model_data_dict)
                        self.register_model(model)
                    except Exception as e:
                        print(
                            f"Error loading custom model '{model_data_dict.get('id')}' for provider '{provider_name}': {e}"
                        )
        except Exception as e:
            print(f"Error loading custom LLM providers configuration for models: {e}")

    def _initialize_models(self):
        """Initialize the registry with default and custom models."""
        # Load and register built-in models
        for model in AVAILABLE_MODELS:
            self.register_model(model)

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

    def get_model(self, model_id: str) -> Optional[Model]:
        """
        Get a model by ID.

        Args:
            model_id: The model ID

        Returns:
            The model if found, None otherwise
        """
        return self.models.get(model_id)

    def get_models_by_provider(self, provider: str) -> List[Model]:
        """
        Get all models for a specific provider.

        Args:
            provider: The provider name

        Returns:
            List of models for the provider
        """
        return [model for model in self.models.values() if model.provider == provider]

    def set_current_model(self, model_id: str) -> bool:
        """
        Set the current model by ID.

        Args:
            model_id: The model ID

        Returns:
            True if successful, False otherwise
        """
        model = self.get_model(model_id)
        if model:
            self.current_model = model
            return True
        logger.warning("Model with ID '%s' not found in registry.", model_id)
        return False

    def get_current_model(self) -> Optional[Model]:
        """
        Get the current model.

        Returns:
            The current model if set, None otherwise
        """
        return self.current_model

    def get_providers(self) -> List[str]:
        """
        Get all unique provider names from the registered models.

        Returns:
            A list of unique provider names.
        """
        providers = set()
        for model in self.models.values():
            providers.add(model.provider)
        return list(providers)
