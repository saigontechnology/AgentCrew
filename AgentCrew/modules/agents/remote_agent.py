from __future__ import annotations

from typing import Dict, TYPE_CHECKING
from uuid import uuid4

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
    from typing import Callable, Any, List, Optional, Tuple, Union


class RemoteAgent(BaseAgent):
    def __init__(
        self, name: str, agent_url: str, headers: Optional[Dict[str, str]] = None
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

    def append_message(self, messages: Union[Dict, List[Dict]]):
        if isinstance(messages, Dict):
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
        self, message_type: MessageType, message_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if message_type == MessageType.Assistant:
            return {
                "role": "assistant",
                "content": [{"type": "text", "text": message_data.get("message", "")}],
            }
        elif message_type == MessageType.Thinking:
            return None
        elif message_type == MessageType.ToolResult:
            return {
                "role": "tool",
                "tool_call_id": message_data.get("tool_use", {"id": ""})["id"],
                "content": message_data.get("tool_result", ""),
            }
        elif message_type == MessageType.FileContent:
            return None

    def execute_tool_call(self, tool_name: str, tool_input: Dict) -> Any:
        return None

    def configure_think(self, think_setting):
        pass

    def calculate_usage_cost(self, input_tokens, output_tokens) -> float:
        return 0.0

    async def process_messages(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        callback: Optional[Callable] = None,
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
            # acceptedOutputModes can be set here if needed, e.g., based on agent_card.defaultOutputModes
            # For now, relying on server defaults or agent's capability.
        )

        full_response_text = ""

        async for stream_response in self.client.send_message_streaming(a2a_payload):
            if isinstance(stream_response.root, JSONRPCErrorResponse):
                raise Exception(
                    f"Remote agent stream error: {stream_response.root.error.code} - {stream_response.root.error.message}"
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

        # After the loop, the generator stops. If full_response_text is empty,
        # it signifies no textual content was streamed as artifacts.

    def get_process_result(self) -> Tuple:
        """
        @DEPRECATED: Use the callback in process_messages instead.
        """
        return ([], 0, 0)
