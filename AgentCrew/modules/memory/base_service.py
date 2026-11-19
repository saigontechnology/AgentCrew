from abc import ABC, abstractmethod
from datetime import datetime, time
from typing import Any

from .types import (
    CandidateSource,
    ConfidenceBand,
    MatchReason,
    MemoryQuery,
    MemorySearchHit,
    MemorySearchResult,
)


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

    def get_memopad_stats(
        self, agent_name: str | None = None, project_key: str | None = None
    ) -> dict[str, Any]:
        """Optional backend capability; collection is SQLite-only for now."""
        raise NotImplementedError(
            "Memopad collection/statistics require the SQLite memory backend."
        )

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

    def search_structured(self, query: MemoryQuery) -> MemorySearchResult:
        """Structured search over the legacy string contract.

        Services that only implement ``retrieve_memory`` still serve the
        expanded tool: the recalled text is wrapped as one legacy hit. ID
        restriction, kind hints and structured match reasons require a
        backend-specific override.
        """
        from_date = (
            int(datetime.combine(query.from_date, time.min).timestamp())
            if query.from_date is not None
            else None
        )
        to_date = (
            int(datetime.combine(query.to_date, time.min).timestamp()) + 86_399
            if query.to_date is not None
            else None
        )
        text = self.retrieve_memory(query.query, from_date, to_date, query.agent_name)
        if (
            not text
            or not text.strip()
            or text.strip() == "No relevant memories found."
        ):
            return MemorySearchResult()
        hit = MemorySearchHit(
            memory_id="legacy",
            unit_id="string",
            date=(
                query.from_date.isoformat()
                if query.from_date is not None
                else "unknown"
            ),
            cwd=None,
            kind="memory",
            headline=None,
            excerpt=text,
            reasons=(MatchReason(CandidateSource.FTS, 0, "legacy string adapter"),),
            confidence=ConfidenceBand.MEDIUM,
            score=0.0,
        )
        return MemorySearchResult((hit,))

    def get_memory_documents(
        self,
        memory_ids: list[str],
        agent_name: str = "None",
    ) -> dict[str, str]:
        """Return full documents for known memory IDs, scoped to one agent."""
        wanted = set(memory_ids)
        documents: dict[str, str] = {}
        if not wanted:
            return documents
        for record in self.get_agent_memory_corpus(agent_name, max_items=500):
            memory_id = record.get("id")
            if memory_id in wanted:
                documents[str(memory_id)] = str(record.get("document", ""))
        return documents
