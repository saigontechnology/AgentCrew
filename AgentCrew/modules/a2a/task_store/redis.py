from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

from .base import TaskStore


class RedisTaskStore(TaskStore):
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        prefix: str = "a2a_task",
        ttl: int = 3600,
    ):
        self.prefix = prefix
        self.ttl = ttl
        self._redis = None
        self._redis_url = redis_url

    async def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    max_connections=20,
                    socket_keepalive=True,
                    health_check_interval=30,
                )
            except ImportError:
                raise ImportError(
                    "redis package is required for RedisTaskStore. "
                    "Install it with: uv add redis"
                )
        return self._redis

    async def close(self) -> None:
        """Close the Redis connection pool and release all connections."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _key(self, namespace: str, id: str) -> str:
        return f"{self.prefix}:{namespace}:{id}"

    async def get_task(self, task_id: str) -> Task | None:
        r = await self._get_redis()
        data = await r.get(self._key("task", task_id))
        if data is None:
            return None
        return Task.model_validate_json(data)

    async def save_task(self, task: Task) -> None:
        r = await self._get_redis()
        key = self._key("task", task.id)
        await r.set(key, task.model_dump_json(exclude_none=True), ex=self.ttl)

    async def delete_task(self, task_id: str) -> None:
        r = await self._get_redis()
        await r.delete(self._key("task", task_id))

    async def has_task(self, task_id: str) -> bool:
        r = await self._get_redis()
        return await r.exists(self._key("task", task_id)) > 0

    async def get_task_history(self, context_id: str) -> list[dict[str, Any]]:
        r = await self._get_redis()
        data = await r.get(self._key("history", context_id))
        if data is None:
            return []
        return json.loads(data)

    async def save_task_history(
        self, context_id: str, history: list[dict[str, Any]]
    ) -> None:
        r = await self._get_redis()
        key = self._key("history", context_id)
        await r.set(key, json.dumps(history, default=str), ex=self.ttl)

    async def append_task_history_message(
        self, context_id: str, message: dict[str, Any]
    ) -> None:
        r = await self._get_redis()
        key = self._key("history", context_id)
        data = await r.get(key)
        history = json.loads(data) if data else []
        history.append(message)
        await r.set(key, json.dumps(history, default=str), ex=self.ttl)

    async def has_task_history(self, context_id: str) -> bool:
        r = await self._get_redis()
        return await r.exists(self._key("history", context_id)) > 0

    async def get_task_events(
        self, task_id: str
    ) -> list[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        r = await self._get_redis()
        raw_events = [
            json.loads(item)
            for item in await r.lrange(self._key("events", task_id), 0, -1)
        ]
        return self.deserialize_events(raw_events)

    async def append_task_events(
        self,
        task_id: str,
        events_to_append: Sequence[TaskStatusUpdateEvent | TaskArtifactUpdateEvent],
    ) -> None:
        if not events_to_append:
            return
        r = await self._get_redis()
        key = self._key("events", task_id)
        payloads = [
            event.model_dump_json(exclude_none=True) for event in events_to_append
        ]
        async with r.pipeline(transaction=True) as pipe:
            await pipe.rpush(key, *payloads)
            await pipe.expire(key, self.ttl)
            await pipe.execute()

    async def append_task_event(
        self,
        task_id: str,
        event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        await self.append_task_events(task_id, [event])

    async def cleanup_task(self, task_id: str) -> None:
        r = await self._get_redis()
        await r.delete(
            self._key("task", task_id),
            self._key("events", task_id),
            self._key("pending", task_id),
        )

    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        r = await self._get_redis()
        key = self._key("pending", task_id)
        data = {
            "ask_tool_use": ask_tool_use,
            "remaining_tools": remaining_tools,
        }
        await r.set(key, json.dumps(data, default=str), ex=self.ttl)

    async def get_pending_tools(self, task_id: str) -> dict | None:
        r = await self._get_redis()
        data = await r.get(self._key("pending", task_id))
        if data is None:
            return None
        return json.loads(data)

    async def clear_pending_tools(self, task_id: str) -> None:
        r = await self._get_redis()
        await r.delete(self._key("pending", task_id))

    async def list_task_ids(self) -> list:
        r = await self._get_redis()
        prefix = self._key("task", "")
        task_ids = []
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match=f"{prefix}*", count=100)
            task_ids.extend(k[len(prefix) :] for k in keys)
            if cursor == 0:
                break
        return task_ids
