from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod


class BaseMemoryService(ABC):
    """Service for storing and retrieving conversation memory."""

    @property
    def session_id(self) -> str:
        """Get the provider name for this service."""
        return getattr(self, "_session_id", "")

    @session_id.setter
    def session_id(self, value: str):
        """Set the provider name for this service."""
        self._session_id = value

    @property
    def loaded_conversation(self) -> bool:
        """Get the provider name for this service."""
        return getattr(self, "_load_conversation", False)

    @loaded_conversation.setter
    def loaded_conversation(self, value: bool):
        """Set the provider name for this service."""
        self._load_conversation = value

    @abstractmethod
    def store_conversation(
        self, user_message: str, assistant_response: str, agent_name: str = "None"
    ) -> List[str]:
        """
        Store a conversation exchange in memory.

        Args:
            user_message: The user's message
            assistant_response: The assistant's response

        Returns:
            List of memory IDs created
        """
        pass

    @abstractmethod
    async def need_generate_user_context(self, user_input) -> bool:
        pass

    @abstractmethod
    def clear_conversation_context(self):
        pass

    @abstractmethod
    def load_conversation_context(self, session_id: str):
        pass

    @abstractmethod
    def generate_user_context(self, user_input: str, agent_name: str = "None") -> str:
        """
        Generate context based on user input by retrieving relevant memories.

        Args:
            user_input: The current user message to generate context for

        Returns:
            Formatted string containing relevant context from past conversations
        """
        pass

    @abstractmethod
    def retrieve_memory(
        self,
        keywords: str,
        from_date: Optional[int] = None,
        to_date: Optional[int] = None,
        agent_name: str = "None",
    ) -> str:
        """
        Retrieve relevant memories based on keywords.

        Args:
            keywords: Keywords to search for
            from_date: Optional start date (timestamp) to filter memories
            to_date: Optional end date (timestamp) to filter memories

        Returns:
            Formatted string of relevant memories
        """
        pass

    @abstractmethod
    def cleanup_old_memories(self, months: int = 1) -> int:
        """
        Remove memories older than the specified number of months.

        Args:
            months: Number of months to keep

        Returns:
            Number of memories removed
        """
        pass

    @abstractmethod
    def forget_topic(
        self,
        topic: str,
        from_date: Optional[int] = None,
        to_date: Optional[int] = None,
        agent_name: str = "None",
    ) -> Dict[str, Any]:
        """
        Remove memories related to a specific topic based on keyword search.

        Args:
            topic: Keywords describing the topic to forget

        Returns:
            Dict with success status and information about the operation
        """
        pass

    @abstractmethod
    def forget_ids(self, ids: List[str], agent_name: str = "None") -> Dict[str, Any]:
        """
        Remove memories using list of id.

        Args:
            ids: list of IDs to remove

        Returns:
            Dict with success status and information about the operation
        """
        pass
