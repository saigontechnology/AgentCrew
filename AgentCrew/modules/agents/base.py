from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from typing import Any

    from AgentCrew.modules.llm.base import BaseLLMService


class MessageType(Enum):
    Assistant = 0
    ToolResult = 2
    FileContent = 3


class BaseAgent(ABC):
    """Base class for all specialized agents."""

    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.history = []
        self.is_active = False
        self.shared_context_pool: dict[str, list[int]] = {}
        self.llm: BaseLLMService | None = None

    @abstractmethod
    def activate(self) -> bool:
        """
        Activate this agent by registering all tools with the LLM service.

        Returns:
            True if activation was successful, False otherwise
        """

    @abstractmethod
    def deactivate(self) -> bool:
        """
        Deactivate this agent by clearing all tools from the LLM service.

        Returns:
            True if deactivation was successful, False otherwise
        """

    async def activate_async(self) -> bool:
        """Async variant of :meth:`activate`.

        Default implementation delegates to :meth:`activate`.
        Subclasses with async lifecycle should override this.
        """
        return self.activate()

    async def deactivate_async(self) -> bool:
        """Async variant of :meth:`deactivate`.

        Default implementation delegates to :meth:`deactivate`.
        Subclasses with async lifecycle should override this.
        """
        return self.deactivate()

    @abstractmethod
    def append_message(self, messages: dict | list[dict]):
        """Append a message or list of messages to the agent's history."""

    @property
    @abstractmethod
    def clean_history(self) -> list:
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
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        pass

    @abstractmethod
    async def execute_tool_call(self, tool_use: dict) -> Any:
        pass

    @abstractmethod
    def configure_think(self, think_setting):
        pass

    @abstractmethod
    def calculate_usage_cost(
        self, input_tokens, output_tokens, cached_tokens=0
    ) -> float:
        pass

    @abstractmethod
    async def process_messages(
        self,
        messages: list[dict[str, Any]] | None = None,
        callback: Callable | None = None,
    ) -> AsyncGenerator:
        """
        Process messages using this agent.

        Args:
            messages: The messages to process

        Returns:
            The processed messages with the agent's response
        """
        yield
