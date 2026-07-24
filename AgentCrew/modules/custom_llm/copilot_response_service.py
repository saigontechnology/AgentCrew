import os
from datetime import datetime
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from loguru import logger

from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai import OpenAIResponseService


class GithubCopilotResponseService(OpenAIResponseService):
    def __init__(
        self, api_key: str | None = None, provider_name: str = "copilot_response"
    ):
        if api_key is None:
            load_dotenv()
            api_key = os.getenv("GITHUB_COPILOT_API_KEY")
            if not api_key:
                logger.error(
                    "GITHUB_COPILOT_API_KEY not found in environment variables"
                )
        super().__init__(
            api_key=api_key,
            base_url="https://api.githubcopilot.com",
        )
        self._provider_name = provider_name
        self._extra_headers = {
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Plugin-Version": "CopilotChat.nvim/*",
            "Editor-Version": "Neovim/0.9.0",
        }

        self.model = "gpt-5.4"
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        self._is_thinking = False
        # self._interaction_id = None
        logger.info("Initialized Copilot Response Service")

    def _github_copilot_token_to_open_ai_key(self, copilot_api_key):
        """
        Convert GitHub Copilot token to OpenAI key format.

        Args:
            copilot_api_key: The GitHub Copilot token

        Returns:
            Updated OpenAI compatible token
        """
        openai_api_key = self.client.api_key

        if openai_api_key.startswith("ghu") or int(
            dict(x.split("=") for x in openai_api_key.split(";"))["exp"]
        ) < int(datetime.now().timestamp()):
            import requests

            headers = {
                "Authorization": f"Bearer {copilot_api_key}",
                "Content-Type": "application/json",
            }
            if self._extra_headers:
                headers.update(self._extra_headers)
            res = requests.get(
                "https://api.github.com/copilot_internal/v2/token", headers=headers
            )
            self.client.api_key = res.json()["token"]

    def _is_github_provider(self):
        if self.base_url:
            from urllib.parse import urlparse

            parsed_url = urlparse(self.base_url)
            host = parsed_url.hostname
            if host and host.endswith(".githubcopilot.com"):
                return True
        return False

    async def get_usage(self) -> dict:
        if not self.api_key or not str(self.api_key).startswith("gh"):
            return {
                "supported": False,
                "provider": self.provider_name,
                "model": self.model,
                "message": "Usage not supported for this provider",
                "limits": [],
            }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "https://api.github.com/copilot_internal/user",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()

        from AgentCrew.modules.custom_llm.github_copilot_service import (
            GithubCopilotService,
        )

        limits = GithubCopilotService._extract_usage_limits(payload)
        message = None
        if not limits:
            message = "Usage data returned but no known limit windows could be parsed. Raw provider payload is available in logs."
            logger.debug(f"Unparsed GitHub Copilot usage payload: {payload}")

        return {
            "supported": True,
            "provider": self.provider_name,
            "model": self.model,
            "plan_type": payload.get("copilot_plan")
            or payload.get("plan_type")
            or payload.get("planType"),
            "message": message,
            "limits": limits,
            "credits": None,
            "raw": payload,
        }

    async def process_message(
        self,
        prompt: str | list,
        temperature: float = 0,
        model_id: str | None = None,
    ) -> str:
        if self._is_github_provider():
            self.base_url = self.base_url.rstrip("/")
            self._github_copilot_token_to_open_ai_key(self.api_key)
            if self._extra_headers:
                self._extra_headers["X-Initiator"] = "user"
                self._extra_headers["X-Request-Id"] = str(uuid4())
        return await super().process_message(prompt, temperature, model_id)

    async def stream_assistant_response(self, messages):
        """Stream the assistant's response with tool support."""

        if self._is_github_provider():
            self.base_url = self.base_url.rstrip("/")
            self._github_copilot_token_to_open_ai_key(self.api_key)
            if self._extra_headers:
                self._extra_headers["X-Initiator"] = (
                    "user"
                    if messages[-1].get("role", "assistant") == "user"
                    else "agent"
                )
                self._extra_headers["X-Request-Id"] = str(uuid4())
                if (
                    len(
                        [
                            m
                            for m in messages
                            if isinstance(m.get("content", ""), list)
                            and len(
                                [
                                    n
                                    for n in m.get("content", [])
                                    if n.get("type", "text") == "image_url"
                                ]
                            )
                            > 0
                        ]
                    )
                    > 0
                ):
                    if "vision" in ModelRegistry.get_model_capabilities(
                        f"{self._provider_name}/{self.model}"
                    ):
                        self._extra_headers["Copilot-Vision-Request"] = "true"

        return await super().stream_assistant_response(messages)
