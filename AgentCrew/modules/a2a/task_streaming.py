from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from loguru import logger

if TYPE_CHECKING:
    from .task_store import TaskStore


class TaskStreamingManager:
    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.streaming_tasks: dict[str, asyncio.Queue] = {}
        self.streaming_enabled_tasks: set[str] = set()
        self.pending_events: dict[
            str, list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]
        ] = defaultdict(list)
        self.flush_tasks: dict[str, asyncio.Task] = {}
        self.flush_locks: dict[str, asyncio.Lock] = {}
        self.flush_interval_seconds = 0.75
        self.max_buffered_events_per_task = 25

    def enable_streaming(self, task_id: str) -> asyncio.Queue:
        self.streaming_enabled_tasks.add(task_id)
        queue: asyncio.Queue = asyncio.Queue()
        self.streaming_tasks[task_id] = queue
        return queue

    def is_streaming_enabled(self, task_id: str) -> bool:
        return task_id in self.streaming_enabled_tasks

    def _get_flush_lock(self, task_id: str) -> asyncio.Lock:
        lock = self.flush_locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self.flush_locks[task_id] = lock
        return lock

    def _cancel_flush_task(self, task_id: str) -> None:
        flush_task = self.flush_tasks.pop(task_id, None)
        if flush_task and not flush_task.done():
            flush_task.cancel()

    def _schedule_flush(self, task_id: str) -> None:
        flush_task = self.flush_tasks.get(task_id)
        if flush_task and not flush_task.done():
            return
        self.flush_tasks[task_id] = asyncio.create_task(
            self._flush_after_delay(task_id)
        )

    async def _flush_after_delay(self, task_id: str) -> None:
        try:
            await asyncio.sleep(self.flush_interval_seconds)
            await self.flush_task_events(task_id, cancel_scheduled_task=False)
        except asyncio.CancelledError:
            return
        finally:
            flush_task = self.flush_tasks.get(task_id)
            current_task = asyncio.current_task()
            if flush_task is current_task:
                self.flush_tasks.pop(task_id, None)

    async def flush_task_events(
        self, task_id: str, cancel_scheduled_task: bool = True
    ) -> None:
        if cancel_scheduled_task:
            self._cancel_flush_task(task_id)

        async with self._get_flush_lock(task_id):
            events = self.pending_events.get(task_id)
            if not events:
                return

            batch = list(events)
            self.pending_events[task_id].clear()
            await self.store.append_task_events(task_id, batch)

    async def record_and_emit_event(
        self,
        task_id: str,
        event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        self.pending_events[task_id].append(event)
        for key, queue in list(self.streaming_tasks.items()):
            if key.startswith(task_id):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(f"Queue full for {key}")
                except Exception as e:
                    logger.error(f"Error emitting event to {key}: {e}")

        if len(self.pending_events[task_id]) >= self.max_buffered_events_per_task:
            await self.flush_task_events(task_id)
        else:
            self._schedule_flush(task_id)

    async def signal_end(self, task_id: str) -> None:
        await self.flush_task_events(task_id)
        for key in list(self.streaming_tasks.keys()):
            if key.startswith(task_id):
                await self.streaming_tasks[key].put(None)

    async def signal_cancel(self, task_id: str, task: Task) -> None:
        canceled_status = TaskStatus(
            state=TaskState.canceled,
            timestamp=task.status.timestamp,
        )
        cancel_event = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=task.context_id,
            status=canceled_status,
            final=True,
        )
        await self.record_and_emit_event(task_id, cancel_event)
        await self.signal_end(task_id)

    def drain_nowait(self, task_id: str) -> None:
        for key in list(self.streaming_tasks.keys()):
            if key.startswith(task_id):
                try:
                    self.streaming_tasks[key].put_nowait(None)
                except Exception:
                    logger.debug("Failed to send drain signal to streaming queue")

    def remove(self, task_id: str) -> None:
        self.streaming_tasks.pop(task_id, None)

    def register_subscriber(self, key: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.streaming_tasks[key] = queue
        return queue

    def remove_subscriber(self, key: str) -> None:
        self.streaming_tasks.pop(key, None)

    async def cleanup(self, task_id: str) -> None:
        await self.flush_task_events(task_id)
        self.streaming_enabled_tasks.discard(task_id)
        self.streaming_tasks.pop(task_id, None)
        self._cancel_flush_task(task_id)
        self.pending_events.pop(task_id, None)
        self.flush_locks.pop(task_id, None)
