from __future__ import annotations

import mimetypes
import os

from openai import AsyncOpenAI

from .base import BaseImageProvider, ImageGenerationResult

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class OpenAIImageProvider(BaseImageProvider):
    """OpenAI gpt-image-2 image generation provider.

    Authentication priority (matching Codex CLI approach):
    1. ChatGPT subscription OAuth token (from ~/.codex/auth.json)
       - Routes through Codex backend (CODEX_BASE_URL) because the
         OAuth JWT scopes (openid, profile, email, offline_access,
         api.connectors.*) don't include api.model.images.request
         required by the standard images API.
    2. OPENAI_API_KEY environment variable / config
       - Uses standard OpenAI images API.

    When using the subscription token, sends ChatGPT-Account-ID header
    for proper routing, matching Codex CLI's BearerAuthProvider behavior.
    """

    def __init__(self):
        self._auth_source: str | None = None
        self._client: AsyncOpenAI | None = None
        self._default_headers: dict[str, str] | None = None
        self._init_auth()

    def _init_auth(self):
        # Priority 1: ChatGPT subscription OAuth token (Codex CLI approach)
        # Must route through CODEX_BASE_URL because the OAuth JWT doesn't
        # have api.model.images.request scope for the standard images API.
        try:
            from AgentCrew.modules.openai_codex.oauth import OpenAICodexOAuth

            auth = OpenAICodexOAuth.get_auth()
            if auth and auth.get("access_token"):
                default_headers = (
                    {
                        "ChatGPT-Account-ID": auth.get("account_id", ""),
                    }
                    if auth.get("account_id")
                    else None
                )
                self._client = AsyncOpenAI(
                    api_key=auth["access_token"],
                    base_url=CODEX_BASE_URL,
                    default_headers=default_headers,
                )
                self._default_headers = default_headers
                self._auth_source = "chatgpt_subscription"
                return
        except ImportError:
            pass
        except Exception:
            pass

        # Priority 2: Standard OpenAI API key (standard images API)
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self._client = AsyncOpenAI(api_key=api_key)
            self._auth_source = "api_key"

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model_id(self) -> str:
        return "gpt-image-2"

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

        if reference_images:
            return await self._generate_with_reference(prompt, size, reference_images)
        return await self._generate_text_only(prompt, size)

    async def _generate_text_only(
        self, prompt: str, size: str
    ) -> ImageGenerationResult:
        if not self._client:
            raise ValueError("Image Generation provider is not available")

        response = await self._client.images.generate(
            model=self.model_id,
            prompt=prompt,
            output_format="webp",
            size=size,
        )
        if not response or not response.data:
            raise SystemError("Image generated failed.")

        image_b64 = response.data[0].b64_json
        revised_prompt = getattr(response.data[0], "revised_prompt", None)

        return ImageGenerationResult(
            base64_data=image_b64,
            mime_type="image/webp",
            provider=self.name,
            model=self.model_id,
            revised_prompt=revised_prompt,
        )

    async def _generate_with_reference(
        self,
        prompt: str,
        size: str,
        reference_images: list[str],
    ) -> ImageGenerationResult:
        if not self._client:
            raise ValueError("Image Generation provider is not available")
        image_file = reference_images[0]
        mime_type, _ = mimetypes.guess_type(image_file)
        if not mime_type:
            mime_type = "image/png"

        with open(image_file, "rb") as f:
            image_data = f.read()

        response = await self._client.images.edit(
            model=self.model_id,
            image=image_data,
            output_format="webp",
            prompt=prompt,
            response_format="b64_json",
        )

        if not response or not response.data:
            raise SystemError("Image generated failed.")

        image_b64 = response.data[0].b64_json
        revised_prompt = getattr(response.data[0], "revised_prompt", None)

        return ImageGenerationResult(
            base64_data=image_b64,
            mime_type="image/webp",
            provider=self.name,
            model=self.model_id,
            revised_prompt=revised_prompt,
        )
