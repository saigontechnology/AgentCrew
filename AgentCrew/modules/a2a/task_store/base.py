from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)


class TaskStore(ABC):
    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None:
        pass

    @abstractmethod
    async def save_task(self, task: Task) -> None:
        pass

    @abstractmethod
    async def delete_task(self, task_id: str) -> None:
        pass

    @abstractmethod
    async def has_task(self, task_id: str) -> bool:
        pass

    @abstractmethod
    async def get_task_history(self, context_id: str) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def save_task_history(
        self, context_id: str, history: list[dict[str, Any]]
    ) -> None:
        pass

    @abstractmethod
    async def append_task_history_message(
        self, context_id: str, message: dict[str, Any]
    ) -> None:
        pass

    @abstractmethod
    async def has_task_history(self, context_id: str) -> bool:
        pass

    @abstractmethod
    async def get_task_events(
        self, task_id: str
    ) -> list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        pass

    @abstractmethod
    async def append_task_event(
        self,
        task_id: str,
        event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        pass

    @abstractmethod
    async def append_task_events(
        self,
        task_id: str,
        events: Sequence[TaskStatusUpdateEvent | TaskArtifactUpdateEvent],
    ) -> None:
        pass

    @abstractmethod
    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        pass

    @abstractmethod
    async def get_pending_tools(self, task_id: str) -> dict | None:
        pass

    @abstractmethod
    async def clear_pending_tools(self, task_id: str) -> None:
        pass

    @abstractmethod
    async def cleanup_task(self, task_id: str) -> None:
        pass

    async def list_task_ids(self) -> list[str]:
        """Return all task IDs currently in the store. Override in implementations."""
        return []

    async def close(self) -> None:
        """Release any resources held by the store (e.g. connection pools).
        Default is a no-op; override in stores that manage connections.
        """

    @staticmethod
    def deserialize_events(
        raw_events: list[dict[str, Any]],
    ) -> list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        events: list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent] = []
        for raw in raw_events:
            if "artifact" in raw:
                events.append(TaskArtifactUpdateEvent.model_validate(raw))
            else:
                events.append(TaskStatusUpdateEvent.model_validate(raw))
        return events
