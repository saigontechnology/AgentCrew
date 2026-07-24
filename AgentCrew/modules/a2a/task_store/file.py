from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

from .base import TaskStore


class FileTaskStore(TaskStore):
    def __init__(self, base_dir: str = ".agentcrew/a2a_tasks"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "tasks").mkdir(exist_ok=True)
        (self.base_dir / "history").mkdir(exist_ok=True)
        (self.base_dir / "events").mkdir(exist_ok=True)
        self.lock = asyncio.Lock()

    def _task_path(self, task_id: str) -> Path:
        return self.base_dir / "tasks" / f"{task_id}.json"

    def _history_path(self, context_id: str) -> Path:
        return self.base_dir / "history" / f"{context_id}.json"

    def _events_path(self, task_id: str) -> Path:
        return self.base_dir / "events" / f"{task_id}.jsonl"

    def _pending_path(self, task_id: str) -> Path:
        return self.base_dir / "tasks" / f"{task_id}_pending.json"

    def _safe_filename(self, name: str) -> str:
        return name.replace("/", "_").replace("\\", "_").replace("..", "_")

    async def get_task(self, task_id: str) -> Task | None:
        async with self.lock:
            path = self._task_path(self._safe_filename(task_id))
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return Task.model_validate(data)

    async def save_task(self, task: Task) -> None:
        async with self.lock:
            path = self._task_path(self._safe_filename(task.id))
            path.write_text(task.model_dump_json(exclude_none=True))

    async def delete_task(self, task_id: str) -> None:
        async with self.lock:
            path = self._task_path(self._safe_filename(task_id))
            if path.exists():
                path.unlink()

    async def has_task(self, task_id: str) -> bool:
        async with self.lock:
            return self._task_path(self._safe_filename(task_id)).exists()

    async def get_task_history(self, context_id: str) -> list[dict[str, Any]]:
        async with self.lock:
            path = self._history_path(self._safe_filename(context_id))
            if not path.exists():
                return []
            return json.loads(path.read_text())

    async def save_task_history(
        self, context_id: str, history: list[dict[str, Any]]
    ) -> None:
        async with self.lock:
            path = self._history_path(self._safe_filename(context_id))
            path.write_text(json.dumps(history, default=str))

    async def append_task_history_message(
        self, context_id: str, message: dict[str, Any]
    ) -> None:
        async with self.lock:
            path = self._history_path(self._safe_filename(context_id))
            history = []
            if path.exists():
                history = json.loads(path.read_text())
            history.append(message)
            path.write_text(json.dumps(history, default=str))

    async def has_task_history(self, context_id: str) -> bool:
        async with self.lock:
            return self._history_path(self._safe_filename(context_id)).exists()

    async def get_task_events(
        self, task_id: str
    ) -> list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        async with self.lock:
            path = self._events_path(self._safe_filename(task_id))
            if not path.exists():
                return []
            raw_events = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
            return self.deserialize_events(raw_events)

    async def append_task_events(
        self,
        task_id: str,
        events_to_append: Sequence[TaskStatusUpdateEvent | TaskArtifactUpdateEvent],
    ) -> None:
        if not events_to_append:
            return

        async with self.lock:
            path = self._events_path(self._safe_filename(task_id))
            with path.open("a", encoding="utf-8") as f:
                for event in events_to_append:
                    f.write(event.model_dump_json(exclude_none=True))
                    f.write("\n")

    async def append_task_event(
        self,
        task_id: str,
        event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        await self.append_task_events(task_id, [event])

    async def cleanup_task(self, task_id: str) -> None:
        async with self.lock:
            safe_id = self._safe_filename(task_id)
            for path in [
                self._task_path(safe_id),
                self._events_path(safe_id),
                self._pending_path(safe_id),
            ]:
                if path.exists():
                    path.unlink()

    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        async with self.lock:
            path = self._pending_path(self._safe_filename(task_id))
            data = {
                "ask_tool_use": ask_tool_use,
                "remaining_tools": remaining_tools,
            }
            path.write_text(json.dumps(data, default=str))

    async def get_pending_tools(self, task_id: str) -> dict | None:
        async with self.lock:
            path = self._pending_path(self._safe_filename(task_id))
            if not path.exists():
                return None
            return json.loads(path.read_text())

    async def clear_pending_tools(self, task_id: str) -> None:
        async with self.lock:
            path = self._pending_path(self._safe_filename(task_id))
            if path.exists():
                path.unlink()

    async def list_task_ids(self) -> list[str]:
        async with self.lock:
            tasks_dir = self.base_dir / "tasks"
            if not tasks_dir.exists():
                return []
            return [
                p.stem
                for p in tasks_dir.glob("*.json")
                if not p.stem.endswith("_pending")
            ]
