from abc import ABC, abstractmethod
from typing import Any


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
        self,
        user_message: str,
        assistant_messages: list[str],
        agent_name: str = "None",
        session_id: str | None = None,
    ) -> list[str]:
        """
        Store a conversation exchange in memory.

        Args:
            user_message: The user's message
            assistant_messages: The assistant messages for the turn

        Returns:
            list of memory IDs created
        """

    @abstractmethod
    def clear_conversation_context(self):
        pass

    @abstractmethod
    def load_conversation_context(self, session_id: str, agent_name: str = "None"):
        pass

    @abstractmethod
    def retrieve_memory(
        self,
        keywords: str,
        from_date: int | None = None,
        to_date: int | None = None,
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

    @abstractmethod
    def list_memory_headers(
        self,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "None",
    ) -> list[str]:
        """
        list all memory IDs within an optional date range.

        Args:
            from_date: Optional start date (timestamp) to filter memories
            to_date: Optional end date (timestamp) to filter memories

        Returns:
            list of memory IDs
        """

    @abstractmethod
    def cleanup_old_memories(self, months: int = 1) -> int:
        """
        Remove memories older than the specified number of months.

        Args:
            months: Number of months to keep

        Returns:
            Number of memories removed
        """

    @abstractmethod
    def forget_topic(
        self,
        topic: str,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "None",
    ) -> dict[str, Any]:
        """
        Remove memories related to a specific topic based on keyword search.

        Args:
            topic: Keywords describing the topic to forget

        Returns:
            dict with success status and information about the operation
        """

    @abstractmethod
    def forget_ids(self, ids: list[str], agent_name: str = "None") -> dict[str, Any]:
        """
        Remove memories using list of id.

        Args:
            ids: list of IDs to remove

        Returns:
            dict with success status and information about the operation
        """

    @abstractmethod
    def delete_by_conversation_id(self, conversation_id: str) -> dict[str, Any]:
        """
        Delete all memories associated with a specific conversation ID.

        Args:
            conversation_id: The conversation ID (session_id) to delete memories for

        Returns:
            dict with success status and count of deleted memories
        """

    @abstractmethod
    def get_agent_memory_corpus(
        self,
        agent_name: str,
        max_items: int = 100,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return a structured memory corpus for an agent.

        Args:
            agent_name: Agent name to scope memories to.
            max_items: Maximum number of memories to return.
            exclude_session_id: Optional session id to exclude from results.

        Returns:
            list of memory records containing id, document, and metadata.
        """

    @abstractmethod
    def mark_memories_evolved(
        self,
        memory_ids: list[str],
        agent_name: str,
    ) -> int:
        """
        Mark memory records as consumed by a prompt evolution.

        Args:
            memory_ids: list of memory IDs to mark.
            agent_name: Agent name the memories belong to.

        Returns:
            Number of memories successfully marked.
        """
