from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from loguru import logger

from .prompt_builder import MetaPromptBuilder
from .providers.base import BaseImageProvider, ImageGenerationResult


class ImageGenerationService:
    """Service for generating images from structured meta prompts.

    Implements priority-based provider fallback:
    1. OpenAI gpt-image-2
    2. Google Gemini gemini-3.1-flash-image
    3. DeepInfra FLUX-2-klein-9b
    """

    DEFAULT_OUTPUT_DIR = ".agentcrew/images"

    def __init__(self, output_dir: str | None = None):
        self._prompt_builder = MetaPromptBuilder()
        self._output_dir = output_dir or self.DEFAULT_OUTPUT_DIR
        self._providers: list[BaseImageProvider] = []
        self._initialize_providers()

    def _initialize_providers(self) -> None:
        """Initialize providers in priority order."""
        from .providers.deepinfra_provider import DeepInfraImageProvider
        from .providers.gemini_provider import GeminiImageProvider
        from .providers.openai_provider import OpenAIImageProvider

        self._providers = [
            GeminiImageProvider(),
            OpenAIImageProvider(),
            DeepInfraImageProvider(),
        ]

    def has_any_provider(self) -> bool:
        """Check if at least one provider is available."""
        return any(p.is_available() for p in self._providers)

    async def generate_image(
        self,
        meta_prompt: dict[str, Any],
        size: str = "1024x1024",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Generate an image from a structured meta prompt.

        Args:
            meta_prompt: The structured JSON meta prompt
            size: Image dimensions (e.g., "1024x1024")
            output_dir: Override output directory

        Returns:
            dict with keys: success, file_path, provider, model,
                           prompt (revised if available), error
        """
        valid, error = self._prompt_builder.validate(meta_prompt)
        if not valid:
            return {"success": False, "error": f"Invalid meta prompt: {error}"}

        prompt = self._prompt_builder.build(meta_prompt)
        if not prompt:
            return {
                "success": False,
                "error": "Failed to build prompt from meta prompt",
            }

        reference_images = meta_prompt.get("images")

        last_error: str | None = None
        for provider in self._providers:
            if not provider.is_available():
                continue

            try:
                result = await provider.generate(
                    prompt=prompt,
                    size=size,
                    reference_images=reference_images,
                )

                file_path = self._save_image(result, output_dir)

                return {
                    "success": True,
                    "file_path": file_path,
                    "provider": result.provider,
                    "model": result.model,
                    "prompt": result.revised_prompt or prompt,
                    "size": size,
                }

            except Exception as e:
                logger.warning(
                    f"Image generation failed with "
                    f"{provider.name}/{provider.model_id}: {e}"
                )
                last_error = str(e)
                continue

        return {
            "success": False,
            "error": (
                f"All image generation providers failed. Last error: {last_error}"
            ),
        }

    def _save_image(
        self,
        result: ImageGenerationResult,
        output_dir: str | None = None,
    ) -> str:
        """Save generated image to disk and return the file path."""
        save_dir = Path(output_dir or self._output_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        ext = result.mime_type.lstrip("image/") or "png"
        filename = f"generated_{result.provider}_{timestamp}.{ext}"
        file_path = save_dir / filename

        if result.image_data:
            file_path.write_bytes(result.image_data)
        elif result.base64_data:
            file_path.write_bytes(base64.b64decode(result.base64_data))
        else:
            raise RuntimeError("No image data available to save")

        return str(file_path.resolve())
