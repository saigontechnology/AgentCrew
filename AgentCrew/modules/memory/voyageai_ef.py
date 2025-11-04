from __future__ import annotations

from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.api.types import Space
from typing import cast, TYPE_CHECKING
from chromadb.utils.embedding_functions.schemas import validate_config_schema
import os

if TYPE_CHECKING:
    from typing import Optional, List, Dict, Any


class VoyageEmbeddingFunction(EmbeddingFunction[Documents]):
    """To use this EmbeddingFunction, you must have the google.generativeai Python package installed and have a Google API key."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "voyage-3.5-lite",
        api_key_env_var: str = "CHROMA_VOYAGE_API_KEY",
    ):
        """
        Initialize the GoogleGenerativeAiEmbeddingFunction.

        Args:
            api_key_env_var (str, optional): Environment variable name that contains your API key for the Google Generative AI API.
                Defaults to "CHROMA_GOOGLE_GENAI_API_KEY".
            model_name (str, optional): The name of the model to use for text embeddings.
                Defaults to "models/embedding-001".
            task_type (str, optional): The task type for the embeddings.
                Use "RETRIEVAL_DOCUMENT" for embedding documents and "RETRIEVAL_QUERY" for embedding queries.
                Defaults to "RETRIEVAL_DOCUMENT".
        """
        try:
            from voyageai.client import Client
        except ImportError:
            raise ValueError(
                "The Google Generative AI python package is not installed. Please install it with `pip install google-generativeai`"
            )

        self.api_key_env_var = api_key_env_var
        self.api_key = api_key or os.getenv(api_key_env_var)
        if not self.api_key:
            raise ValueError(f"The {api_key_env_var} environment variable is not set.")

        self.model_name = model_name

        self._provider_client = Client(api_key=self.api_key, timeout=60)

    def __call__(self, input: Documents) -> Embeddings:
        """
        Generate embeddings for the given documents.

        Args:
            input: Documents or images to generate embeddings for.

        Returns:
            Embeddings for the documents.
        """
        # Google Generative AI only works with text documents
        if not all(isinstance(item, str) for item in input):
            raise ValueError(
                "Google Generative AI only supports text documents, not images"
            )

        embedding_result = self._provider_client.embed(
            input,
            model=self.model_name,
        )
        # Convert to the expected Embeddings type (List[Vector])
        return cast(Embeddings, embedding_result.embeddings)

    @staticmethod
    def name() -> str:
        return "voyageai"

    def default_space(self) -> Space:
        return "cosine"

    def supported_spaces(self) -> List[Space]:
        return ["cosine", "l2", "ip"]

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "EmbeddingFunction[Documents]":
        api_key_env_var = config.get("api_key_env_var")
        model_name = config.get("model_name")
        task_type = config.get("task_type")

        if api_key_env_var is None or model_name is None or task_type is None:
            assert False, "This code should not be reached"

        return VoyageEmbeddingFunction(
            api_key_env_var=api_key_env_var,
            model_name=model_name,
        )

    def get_config(self) -> Dict[str, Any]:
        return {
            "api_key_env_var": self.api_key_env_var,
            "model_name": self.model_name,
        }

    def validate_config_update(
        self, old_config: Dict[str, Any], new_config: Dict[str, Any]
    ) -> None:
        if "model_name" in new_config:
            raise ValueError(
                "The model name cannot be changed after the embedding function has been initialized."
            )
        if "task_type" in new_config:
            raise ValueError(
                "The task type cannot be changed after the embedding function has been initialized."
            )

    @staticmethod
    def validate_config(config: Dict[str, Any]) -> None:
        """
        Validate the configuration using the JSON schema.

        Args:
            config: Configuration to validate

        Raises:
            ValidationError: If the configuration does not match the schema
        """
        validate_config_schema(config, "google_generative_ai")
