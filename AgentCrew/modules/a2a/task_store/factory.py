from __future__ import annotations

from .base import TaskStore
from .file import FileTaskStore
from .memory import InMemoryTaskStore
from .redis import RedisTaskStore


def create_task_store(store_type: str = "memory", **kwargs) -> TaskStore:
    if store_type == "memory":
        return InMemoryTaskStore()
    elif store_type == "file":
        base_dir = kwargs.get("base_dir", ".agentcrew/a2a_tasks")
        return FileTaskStore(base_dir=base_dir)
    elif store_type == "redis":
        redis_url = kwargs.get("redis_url", "redis://localhost:6379")
        prefix = kwargs.get("prefix", "a2a_task")
        ttl = kwargs.get("ttl", 3600)
        return RedisTaskStore(redis_url=redis_url, prefix=prefix, ttl=ttl)
    else:
        raise ValueError(
            f"Unknown store type: {store_type}. Use 'memory', 'file', or 'redis'."
        )
