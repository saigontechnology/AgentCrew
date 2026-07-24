from __future__ import annotations

import asyncio
import mimetypes
import os

from loguru import logger

from .base import BaseImageProvider, ImageGenerationResult


def _read_file_sync(path: str) -> bytes:
    """Synchronous helper: read file as bytes."""
    with open(path, "rb") as f:
        return f.read()


class GeminiImageProvider(BaseImageProvider):
    """Google Gemini image generation provider using gemini-3.1-flash-image."""

    def __init__(self):
        self._api_key = os.getenv("GEMINI_API_KEY")
        self._client = None
        if self._api_key:
            try:
                from google import genai
                from google.genai import types

                self._client = genai.Client(api_key=self._api_key)
                self._types = types
            except ImportError:
                logger.warning(
                    "google-genai package not installed; "
                    "Gemini image generation will not be available."
                )

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_id(self) -> str:
        return "gemini-3.1-flash-image"

    def is_available(self) -> bool:
        return bool(self._client)

    def supports_reference_images(self) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        reference_images: list[str] | None = None,
    ) -> ImageGenerationResult:
        if not self._client:
            raise RuntimeError("Gemini API key not configured or client not available")

        contents = []

        if reference_images:
            for img_path in reference_images:
                mime_type, _ = mimetypes.guess_type(img_path)
                if not mime_type:
                    mime_type = "image/png"
                img_data = await asyncio.to_thread(_read_file_sync, img_path)
                contents.append(
                    self._types.Part.from_bytes(data=img_data, mime_type=mime_type)
                )

        contents.append(prompt)

        config = self._types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=self._types.ImageConfig(
                image_output_options=self._types.ImageConfigImageOutputOptions(
                    mime_type="image/webp"
                )
            ),
        )

        response = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=contents,
            config=config,
        )

        image_data = None
        mime_type = "image/png"
        if not response or not response.candidates:
            raise SystemError("Image generated failed.")

        parts = []

        if response.candidates[0].content and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts

        for part in parts:
            if hasattr(part, "inline_data") and part.inline_data:
                image_data = part.inline_data.data
                mime_type = getattr(part.inline_data, "mime_type", None) or "image/webp"
                break

        if not image_data:
            raise RuntimeError("Gemini did not return an image in the response")

        return ImageGenerationResult(
            image_data=image_data,
            mime_type=mime_type,
            provider=self.name,
            model=self.model_id,
        )
