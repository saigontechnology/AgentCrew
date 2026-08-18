import os
from typing import Any

import httpx2
from dotenv import load_dotenv
from loguru import logger

from .service import CustomLLMService


class CrofAIService(CustomLLMService):
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("CROFAI_API_KEY")
        base_url = os.getenv("CROFAI_BASE_URL", "https://crof.ai/v1")
        if not api_key:
            logger.error("CROFAI_API_KEY not found in environment variables")
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider_name="crofai",
        )
        self.model = "deepseek-v3.2"
        logger.info("Initialized CrofAI Service")

    @staticmethod
    def _usage_api_url(base_url: str) -> str:
        origin = base_url.split("/v1", 1)[0].rstrip("/")
        return f"{origin}/usage_api/"

    async def get_usage(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "supported": False,
                "provider": self.provider_name,
                "model": self.model,
                "message": "Usage not supported without CROFAI_API_KEY",
                "limits": [],
            }

        usage_url = self._usage_api_url(self.base_url or "https://crof.ai/v1")
        try:
            async with httpx2.AsyncClient(timeout=30) as client:
                response = await client.get(
                    usage_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.debug(f"CrofAI usage retrieval failed: {exc}")
            return {
                "supported": False,
                "provider": self.provider_name,
                "model": self.model,
                "message": f"Failed to retrieve CrofAI usage: {exc}",
                "limits": [],
            }

        usable_requests = payload.get("usable_requests")
        limits = []
        if usable_requests is not None:
            limits.append(
                {
                    "name": "daily requests",
                    "used_percent": None,
                    "remaining_percent": None,
                    "window_seconds": 86400,
                    "reset_at": None,
                    "reset_after_seconds": None,
                    "remaining": usable_requests,
                    "entitlement": None,
                    "unlimited": False,
                }
            )

        return {
            "supported": True,
            "provider": self.provider_name,
            "model": self.model,
            "message": None,
            "limits": limits,
            "credits": {"balance": payload.get("credits")},
            "raw": payload,
        }
