from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from typing import AsyncGenerator, Tuple, Dict, List, Optional, Any, Callable, Union


class MessageType(Enum):
    Assistant = 0
    Thinking = 1
    ToolResult = 2
    FileContent = 3


class BaseAgent(ABC):
    """Base class for all specialized agents."""

    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.history = []
        self.is_active = False
        self.shared_context_pool: Dict[str, List[int]] = {}

    @abstractmethod
    def activate(self) -> bool:
        """
        Activate this agent by registering all tools with the LLM service.

        Returns:
            True if activation was successful, False otherwise
        """
        pass

    @abstractmethod
    def deactivate(self) -> bool:
        """
        Deactivate this agent by clearing all tools from the LLM service.

        Returns:
            True if deactivation was successful, False otherwise
        """
        pass

    @abstractmethod
    def append_message(self, messages: Union[Dict, List[Dict]]):
        """Append a message or list of messages to the agent's history."""
        pass

    @property
    @abstractmethod
    def clean_history(self) -> List:
        pass

    @abstractmethod
    def get_provider(self) -> str:
        pass

    @abstractmethod
    def get_model(self) -> str:
        pass

    @abstractmethod
    def is_streaming(self) -> bool:
        pass

    @abstractmethod
    def format_message(
        self, message_type: MessageType, message_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def execute_tool_call(self, tool_name: str, tool_input: Dict) -> Any:
        pass

    @abstractmethod
    def configure_think(self, think_setting):
        pass

    @abstractmethod
    def calculate_usage_cost(self, input_tokens, output_tokens) -> float:
        pass

    @abstractmethod
    async def process_messages(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        callback: Optional[Callable] = None,
    ) -> AsyncGenerator:
        """
        Process messages using this agent.

        Args:
            messages: The messages to process

        Returns:
            The processed messages with the agent's response
        """
        yield

    @abstractmethod
    def get_process_result(self) -> Tuple:
        """
        @DEPRECATED: Use the callback in process_messages instead.
        """
        pass
