from __future__ import annotations

import asyncio


class TaskCancellationManager:
    def __init__(self) -> None:
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.background_tasks: dict[str, asyncio.Task] = {}

    def register(self, task_id: str, bg_task: asyncio.Task) -> None:
        cancel_event = asyncio.Event()
        self.cancel_events[task_id] = cancel_event
        self.background_tasks[task_id] = bg_task

    def signal_cancel(self, task_id: str) -> None:
        if task_id in self.cancel_events:
            self.cancel_events[task_id].set()
        if task_id in self.background_tasks:
            self.background_tasks[task_id].cancel()

    def is_canceled(self, task_id: str) -> bool:
        event = self.cancel_events.get(task_id)
        return event is not None and event.is_set()

    def cleanup(self, task_id: str) -> None:
        self.cancel_events.pop(task_id, None)
        self.background_tasks.pop(task_id, None)
