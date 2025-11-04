from AgentCrew.modules.llm.model_registry import ModelRegistry
from .service import CustomLLMService
import os
from dotenv import load_dotenv
from loguru import logger
from typing import Dict, Any, Optional, List
from datetime import datetime
import json
from uuid import uuid4


class GithubCopilotService(CustomLLMService):
    def __init__(
        self, api_key: Optional[str] = None, provider_name: str = "github_copilot"
    ):
        if api_key is None:
            load_dotenv()
            api_key = os.getenv("GITHUB_COPILOT_API_KEY")
            if not api_key:
                raise ValueError(
                    "GITHUB_COPILOT_API_KEY not found in environment variables"
                )
        super().__init__(
            api_key=api_key,
            base_url="https://api.githubcopilot.com",
            provider_name=provider_name,
            extra_headers={
                "Copilot-Integration-Id": "vscode-chat",
                "Editor-Plugin-Version": "CopilotChat.nvim/*",
                "Editor-Version": "Neovim/0.9.0",
            },
        )
        self.model = "gpt-4.1"
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        self._is_thinking = False
        # self._interaction_id = None
        logger.info("Initialized Github Copilot Service")

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
            if self.extra_headers:
                headers.update(self.extra_headers)
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

    def _convert_internal_format(self, messages: List[Dict[str, Any]]):
        for msg in messages:
            msg.pop("agent", None)
            if "tool_calls" in msg and msg.get("tool_calls", []):
                for tool_call in msg["tool_calls"]:
                    tool_call["function"] = {}
                    tool_call["function"]["name"] = tool_call.pop("name", "")
                    tool_call["function"]["arguments"] = json.dumps(
                        tool_call.pop("arguments", {})
                    )
            if msg.get("role") == "tool":
                # Special treatment for GitHub Copilot GPT-4.1 model
                # At the the time of writing, GitHub Copilot GPT-4.1 model cannot read tool results with array content
                msg.pop("tool_name", None)
                if isinstance(msg.get("content", ""), List):
                    if self._is_github_provider() and self.model != "gpt-4.1":
                        # OpenAI format for tool responses
                        parsed_tool_result = []
                        for tool_content in msg["content"]:
                            if tool_content.get("type", "text") == "image_url":
                                if "vision" in ModelRegistry.get_model_capabilities(
                                    f"{self._provider_name}/{self.model}"
                                ):
                                    parsed_tool_result.append(tool_content)
                            else:
                                parsed_tool_result.append(tool_content)
                        msg["content"] = parsed_tool_result
                    else:
                        parsed_tool_result = []
                        for tool_content in msg["content"]:
                            # Skipping vision/image tool results for Groq
                            # if res.get("type", "text") == "image_url":
                            #     if "vision" in ModelRegistry.get_model_capabilities(self.model):
                            #         parsed_tool_result.append(res)
                            # else:
                            if tool_content.get("type", "text") == "text":
                                parsed_tool_result.append(tool_content.get("text", ""))
                        msg["content"] = (
                            "\n".join(parsed_tool_result) if parsed_tool_result else ""
                        )

        return messages

    async def process_message(self, prompt: str, temperature: float = 0) -> str:
        if self._is_github_provider():
            self.base_url = self.base_url.rstrip("/")
            self._github_copilot_token_to_open_ai_key(self.api_key)
            if self.extra_headers:
                self.extra_headers["X-Initiator"] = "user"
                self.extra_headers["X-Request-Id"] = str(uuid4())
        return await super().process_message(prompt, temperature)

    async def stream_assistant_response(self, messages):
        """Stream the assistant's response with tool support."""

        if self._is_github_provider():
            self.base_url = self.base_url.rstrip("/")
            self._github_copilot_token_to_open_ai_key(self.api_key)
            # if len([m for m in messages if m.get("role") == "assistant"]) == 0:
            #     self._interaction_id = str(uuid4())
            if self.extra_headers:
                self.extra_headers["X-Initiator"] = (
                    "user"
                    if messages[-1].get("role", "assistant") == "user"
                    else "agent"
                )
                self.extra_headers["X-Request-Id"] = str(uuid4())
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
                        self.extra_headers["Copilot-Vision-Request"] = "true"

                # if self._interaction_id:
                #     self.extra_headers["X-Interaction-Id"] = self._interaction_id
            # Special handling for GitHub Copilot GPT-4.1 model
            # TODO: Find a better way to handle this
            if self.model == "gpt-4.1":
                for m in messages:
                    if m.get("role") == "tool" and isinstance(m.get("content"), list):
                        parsed_content = []
                        for content in m.get("content", []):
                            if content.get("type", "text") == "text":
                                parsed_content.append(content.get("text", ""))
                        m["content"] = "\n".join(parsed_content)
        return await super().stream_assistant_response(messages)
