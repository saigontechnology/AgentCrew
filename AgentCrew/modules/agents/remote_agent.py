from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from loguru import logger
from pydantic import ValidationError
from AgentCrew.modules.a2a.adapters import (
    convert_agent_message_to_a2a,
)

from .base import BaseAgent, MessageType
from AgentCrew.modules.a2a.common.client import A2ACardResolver, A2AClient
from a2a.types import (
    MessageSendParams,
    TaskStatusUpdateEvent,
    JSONRPCErrorResponse,
    TaskArtifactUpdateEvent,
    TextPart,
)

if TYPE_CHECKING:
    from typing import Callable, Any, Union


class RemoteAgent(BaseAgent):
    def __init__(
        self, name: str, agent_url: str, headers: dict[str, str] | None = None
    ):
        self.card_resolver = A2ACardResolver(agent_url)
        self.agent_card = self.card_resolver.get_agent_card()
        self.client = A2AClient(self.agent_card, timeout=600, headers=headers)
        super().__init__(name, self.agent_card.description)
        self.current_task_id = None
        self.headers = headers or {}

    def activate(self) -> bool:
        """
        Activate this agent by registering all tools with the LLM service.

        Returns:
            True if activation was successful, False otherwise
        """
        self.is_active = True
        return True

    def deactivate(self) -> bool:
        """
        Deactivate this agent by clearing all tools from the LLM service.

        Returns:
            True if deactivation was successful, False otherwise
        """
        self.is_active = False
        return True

    def append_message(self, messages: Union[dict, list[dict]]):
        if isinstance(messages, dict):
            self.history.append(messages)
        else:
            self.history.extend(messages)

    @property
    def clean_history(self):
        return self.history
        # return MessageTransformer.standardize_messages(
        #     self.history, "a2a_remote", self.name
        # )

    def get_provider(self) -> str:
        return (
            self.agent_card.provider.organization
            if self.agent_card.provider
            else "a2a_remote"
        )

    def get_model(self) -> str:
        return self.get_provider() + "-" + self.agent_card.version

    def is_streaming(self) -> bool:
        return True

    def format_message(
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if message_type == MessageType.Assistant:
            return {
                "role": "assistant",
                "content": [{"type": "text", "text": message_data.get("message", "")}],
            }
        elif message_type == MessageType.ToolResult:
            return {
                "role": "tool",
                "tool_call_id": message_data.get("tool_use", {"id": ""})["id"],
                "content": message_data.get("tool_result", ""),
            }
        elif message_type == MessageType.FileContent:
            return None

    async def execute_tool_call(self, tool_use: dict) -> Any:
        return None

    def configure_think(self, think_setting):
        pass

    def calculate_usage_cost(
        self, input_tokens, output_tokens, cached_tokens=0
    ) -> float:
        return 0.0

    async def process_messages(
        self,
        messages: list[dict[str, Any]] | None = None,
        callback: Callable | None = None,
    ):
        if not self.client or not self.agent_card:
            raise ValidationError(
                f"RemoteAgent '{self.name}' not properly initialized."
            )
        if not messages:
            messages = self.history

        if not self.current_task_id:
            self.current_task_id = str(uuid4())

        last_user_message = messages[-1]

        a2a_message = convert_agent_message_to_a2a(last_user_message)
        a2a_message.task_id = self.current_task_id

        a2a_payload = MessageSendParams(
            metadata={"id": str(uuid4())},
            message=a2a_message,
        )

        full_response_text = ""
        max_retries = 3
        retry_count = 0
        is_resubscribe = False

        while retry_count <= max_retries:
            try:
                if is_resubscribe and self.current_task_id:
                    logger.info(
                        f"Resubscribing to task {self.current_task_id} "
                        f"(attempt {retry_count})"
                    )
                    stream = self.client.resubscribe_to_task(self.current_task_id)
                else:
                    stream = self.client.send_message_streaming(a2a_payload)

                async for stream_response in stream:
                    if isinstance(stream_response.root, JSONRPCErrorResponse):
                        raise Exception(
                            f"Remote agent stream error: "
                            f"{stream_response.root.error.code} - "
                            f"{stream_response.root.error.message}"
                        )

                    if stream_response.root.result:
                        event = stream_response.root.result
                        current_content_chunk_text = ""
                        current_thinking_chunk_text = ""

                        if isinstance(event, TaskArtifactUpdateEvent):
                            self.current_task_id = event.task_id
                            for part in event.artifact.parts:
                                if isinstance(part.root, TextPart):
                                    current_content_chunk_text += part.root.text
                            if current_content_chunk_text:
                                full_response_text += current_content_chunk_text
                                yield (
                                    full_response_text,
                                    current_content_chunk_text,
                                    None,
                                )

                        elif isinstance(event, TaskStatusUpdateEvent):
                            self.current_task_id = event.task_id
                            if event.status.message and event.status.message.parts:
                                for part in event.status.message.parts:
                                    if isinstance(part.root, TextPart):
                                        current_content_chunk_text += part.root.text
                                if current_thinking_chunk_text:
                                    yield (
                                        full_response_text,
                                        None,
                                        (current_thinking_chunk_text, None),
                                    )

                break

            except (
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.ReadTimeout,
                ConnectionError,
                httpx.ConnectError,
            ) as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(
                        f"Failed to reconnect after {max_retries} attempts: {e}"
                    )
                    raise
                wait_time = min(2**retry_count, 30)
                logger.warning(
                    f"Stream connection lost: {e}. "
                    f"Retrying in {wait_time}s (attempt {retry_count}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                is_resubscribe = True
