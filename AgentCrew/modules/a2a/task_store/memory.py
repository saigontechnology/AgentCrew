from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

from .base import TaskStore


class InMemoryTaskStore(TaskStore):
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.task_history: dict[str, list[dict[str, Any]]] = {}
        self.task_events: dict[
            str, list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]
        ] = defaultdict(list)
        self.pending_tools: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def get_task(self, task_id: str) -> Task | None:
        async with self.lock:
            return self.tasks.get(task_id)

    async def save_task(self, task: Task) -> None:
        async with self.lock:
            self.tasks[task.id] = task

    async def delete_task(self, task_id: str) -> None:
        async with self.lock:
            self.tasks.pop(task_id, None)

    async def has_task(self, task_id: str) -> bool:
        async with self.lock:
            return task_id in self.tasks

    async def get_task_history(self, context_id: str) -> list[dict[str, Any]]:
        async with self.lock:
            return self.task_history.get(context_id, [])

    async def save_task_history(
        self, context_id: str, history: list[dict[str, Any]]
    ) -> None:
        async with self.lock:
            self.task_history[context_id] = history

    async def append_task_history_message(
        self, context_id: str, message: dict[str, Any]
    ) -> None:
        async with self.lock:
            if context_id not in self.task_history:
                self.task_history[context_id] = []
            self.task_history[context_id].append(message)

    async def has_task_history(self, context_id: str) -> bool:
        async with self.lock:
            return context_id in self.task_history

    async def get_task_events(
        self, task_id: str
    ) -> list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        async with self.lock:
            return list(self.task_events.get(task_id, []))

    async def append_task_events(
        self,
        task_id: str,
        events: Sequence[TaskStatusUpdateEvent | TaskArtifactUpdateEvent],
    ) -> None:
        if not events:
            return
        async with self.lock:
            self.task_events[task_id].extend(events)

    async def append_task_event(
        self,
        task_id: str,
        event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        await self.append_task_events(task_id, [event])

    async def cleanup_task(self, task_id: str) -> None:
        async with self.lock:
            self.tasks.pop(task_id, None)
            self.task_events.pop(task_id, None)
            self.pending_tools.pop(task_id, None)

    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        async with self.lock:
            self.pending_tools[task_id] = {
                "ask_tool_use": ask_tool_use,
                "remaining_tools": remaining_tools,
            }

    async def get_pending_tools(self, task_id: str) -> dict | None:
        async with self.lock:
            return self.pending_tools.get(task_id)

    async def clear_pending_tools(self, task_id: str) -> None:
        async with self.lock:
            self.pending_tools.pop(task_id, None)

    async def list_task_ids(self) -> list:
        async with self.lock:
            return list(self.tasks.keys())
