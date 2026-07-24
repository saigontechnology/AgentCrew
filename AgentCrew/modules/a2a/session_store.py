"""
AgentCrew session stores and durable SDK TaskStore adapters.

Separation of concerns:
- AgentCrewSessionStore — LLM history + pending tool state (AgentCrew owned)
- SDK TaskStore adapters (create_task_store) — protocol task persistence
- All stores accept an `agent_namespace` for multi-agent isolation.
- Owner isolation uses a single `default` owner key when no auth context.
"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task
from google.protobuf.json_format import MessageToDict, ParseDict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_name(name: str) -> str:
    """Sanitize a name for use in filenames/keys."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _owner_key(context: ServerCallContext | None) -> str:
    """Resolve owner from context. Default to 'default'."""
    if context and hasattr(context, "tenant") and context.tenant:
        return _safe_name(context.tenant)
    return "default"


def _read_json(path: str) -> Any:
    """Synchronous helper: read and parse JSON from *path*."""
    with open(path) as f:
        return json.load(f)


def _remove_sync(path: str) -> None:
    """Synchronous helper: remove file if it exists."""
    if os.path.exists(path):
        os.remove(path)


def atomic_write(path: str, data: Any) -> None:
    """Atomic file write using temporary file + rename."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# AgentCrewSessionStore — AgentCrew-owned execution state
# ---------------------------------------------------------------------------


class AgentCrewSessionStore(ABC):
    """Abstract store for AgentCrew execution state (history + pending tools)."""

    @abstractmethod
    async def get_history(self, context_id: str) -> list[dict[str, Any]]: ...
    @abstractmethod
    async def append_history(
        self, context_id: str, message: dict[str, Any]
    ) -> None: ...
    @abstractmethod
    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None: ...
    @abstractmethod
    async def get_pending_tools(self, task_id: str) -> dict | None: ...
    @abstractmethod
    async def clear_pending_tools(self, task_id: str) -> None: ...
    @abstractmethod
    async def cleanup(self, task_id: str, context_id: str) -> None: ...
    async def close(self) -> None:
        pass


class InMemorySessionStore(AgentCrewSessionStore):
    """In-memory implementation. State lost on restart. Agent-isolated."""

    def __init__(self, agent_namespace: str = "") -> None:
        self._agent = _safe_name(agent_namespace) if agent_namespace else ""
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._pending: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _hk(self, cid: str) -> str:
        return f"{self._agent}:{cid}" if self._agent else cid

    def _pk(self, tid: str) -> str:
        return f"{self._agent}:{tid}" if self._agent else tid

    async def get_history(self, context_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._histories.get(self._hk(context_id), []))

    async def append_history(self, context_id: str, message: dict[str, Any]) -> None:
        k = self._hk(context_id)
        async with self._lock:
            if k not in self._histories:
                self._histories[k] = []
            self._histories[k].append(message)

    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        async with self._lock:
            self._pending[self._pk(task_id)] = {
                "ask_tool_use": ask_tool_use,
                "remaining_tools": remaining_tools,
            }

    async def get_pending_tools(self, task_id: str) -> dict | None:
        async with self._lock:
            return self._pending.get(self._pk(task_id))

    async def clear_pending_tools(self, task_id: str) -> None:
        async with self._lock:
            self._pending.pop(self._pk(task_id), None)

    async def cleanup(self, task_id: str, context_id: str) -> None:
        async with self._lock:
            self._pending.pop(self._pk(task_id), None)
            self._histories.pop(self._hk(context_id), None)


class FileSessionStore(AgentCrewSessionStore):
    """File-based. Survives restart. Agent-namespaced directory. Per-file lock."""

    def __init__(
        self, base_dir: str = ".agentcrew/a2a_v1", agent_namespace: str = ""
    ) -> None:
        ns = _safe_name(agent_namespace) if agent_namespace else ""
        self.base_dir = os.path.join(base_dir, ns) if ns else base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self._file_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, path: str) -> asyncio.Lock:
        if path not in self._file_locks:
            self._file_locks[path] = asyncio.Lock()
        return self._file_locks[path]

    def _history_path(self, context_id: str) -> str:
        return os.path.join(self.base_dir, f"history_{_safe_name(context_id)}.json")

    def _pending_path(self, task_id: str) -> str:
        return os.path.join(self.base_dir, f"pending_{_safe_name(task_id)}.json")

    async def get_history(self, context_id: str) -> list[dict[str, Any]]:
        path = self._history_path(context_id)
        async with self._lock_for(path):
            if not os.path.exists(path):
                return []
            return await asyncio.to_thread(_read_json, path)

    async def append_history(self, context_id: str, message: dict[str, Any]) -> None:
        path = self._history_path(context_id)
        async with self._lock_for(path):
            history = []
            if os.path.exists(path):
                history = await asyncio.to_thread(_read_json, path)
            history.append(message)
            await asyncio.to_thread(atomic_write, path, history)

    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        path = self._pending_path(task_id)
        data = {"ask_tool_use": ask_tool_use, "remaining_tools": remaining_tools}
        async with self._lock_for(path):
            await asyncio.to_thread(atomic_write, path, data)

    async def get_pending_tools(self, task_id: str) -> dict | None:
        path = self._pending_path(task_id)
        async with self._lock_for(path):
            if not os.path.exists(path):
                return None
            return await asyncio.to_thread(_read_json, path)

    async def clear_pending_tools(self, task_id: str) -> None:
        path = self._pending_path(task_id)
        async with self._lock_for(path):
            await asyncio.to_thread(_remove_sync, path)

    async def cleanup(self, task_id: str, context_id: str) -> None:
        await self.clear_pending_tools(task_id)
        hpath = self._history_path(context_id)
        async with self._lock_for(hpath):
            await asyncio.to_thread(_remove_sync, hpath)


class RedisSessionStore(AgentCrewSessionStore):
    """Redis-backed session store. Survives restart. Agent-namespaced prefix."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        agent_namespace: str = "",
        **kwargs: Any,
    ) -> None:
        self._redis_url = redis_url
        ns = _safe_name(agent_namespace) if agent_namespace else ""
        self._prefix = f"a2a_v1_sesh_{ns}" if ns else "a2a_v1_sesh"
        self._ttl = kwargs.get("ttl", 3600)
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _hk(self, cid: str) -> str:
        return f"{self._prefix}:history:{_safe_name(cid)}"

    def _pk(self, tid: str) -> str:
        return f"{self._prefix}:pending:{_safe_name(tid)}"

    async def get_history(self, context_id: str) -> list[dict[str, Any]]:
        r = await self._get_redis()
        raw = await r.get(self._hk(context_id))
        return json.loads(raw) if raw else []

    async def append_history(self, context_id: str, message: dict[str, Any]) -> None:
        r = await self._get_redis()
        k = self._hk(context_id)
        raw = await r.get(k)
        history = json.loads(raw) if raw else []
        history.append(message)
        await r.setex(k, self._ttl, json.dumps(history))

    async def save_pending_tools(
        self, task_id: str, ask_tool_use: dict, remaining_tools: list
    ) -> None:
        r = await self._get_redis()
        data = {"ask_tool_use": ask_tool_use, "remaining_tools": remaining_tools}
        await r.setex(self._pk(task_id), self._ttl, json.dumps(data))

    async def get_pending_tools(self, task_id: str) -> dict | None:
        r = await self._get_redis()
        raw = await r.get(self._pk(task_id))
        return json.loads(raw) if raw else None

    async def clear_pending_tools(self, task_id: str) -> None:
        r = await self._get_redis()
        await r.delete(self._pk(task_id))

    async def cleanup(self, task_id: str, context_id: str) -> None:
        r = await self._get_redis()
        await r.delete(self._pk(task_id))
        await r.delete(self._hk(context_id))

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


def create_session_store(
    store_type: str = "memory", **options: Any
) -> AgentCrewSessionStore:
    """Factory for session stores (AgentCrew-owned state)."""
    if store_type == "file":
        return FileSessionStore(**options)
    if store_type == "redis":
        return RedisSessionStore(**options)
    return InMemorySessionStore(**options)


# ---------------------------------------------------------------------------
# AgentCrew TaskStore adapters — durable protocol task persistence
# Implements a2a.server.tasks.task_store.TaskStore using ProtoJSON.
# All stores accept agent_namespace for isolation and use _owner_key for auth.
# ---------------------------------------------------------------------------


class InMemoryAgentCrewTaskStore(TaskStore):
    """In-memory SDK TaskStore — tasks lost on restart. Owner-keyed + agent-isolated."""

    def __init__(self, agent_namespace: str = "") -> None:
        ns = _safe_name(agent_namespace) if agent_namespace else ""
        self._prefix = f"{ns}:" if ns else ""
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()

    def _key(self, task_id: str, owner: str) -> str:
        return f"{self._prefix}{owner}:{task_id}"

    async def save(self, task: Task, context: ServerCallContext) -> None:
        owner = _owner_key(context)
        k = self._key(task.id, owner)
        async with self._lock:
            t = Task()
            t.CopyFrom(task)
            self._tasks[k] = t

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        owner = _owner_key(context)
        k = self._key(task_id, owner)
        async with self._lock:
            t = self._tasks.get(k)
            if t is None:
                return None
            task = Task()
            task.CopyFrom(t)
            return task

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        owner = _owner_key(context)
        prefix = self._prefix + owner + ":"
        async with self._lock:
            tasks = [t for k, t in self._tasks.items() if k.startswith(prefix)]
        return ListTasksResponse(
            tasks=tasks, total_size=len(tasks), page_size=len(tasks)
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        owner = _owner_key(context)
        k = self._key(task_id, owner)
        async with self._lock:
            self._tasks.pop(k, None)


class FileAgentCrewTaskStore(TaskStore):
    """File-backed SDK TaskStore — tasks survive restart. Agent-namespaced subdir."""

    def __init__(
        self, base_dir: str = ".agentcrew/a2a_v1", agent_namespace: str = ""
    ) -> None:
        ns = _safe_name(agent_namespace) if agent_namespace else ""
        self._base_dir = (
            os.path.join(base_dir, ns, "tasks")
            if ns
            else os.path.join(base_dir, "tasks")
        )
        os.makedirs(self._base_dir, exist_ok=True)
        self._lock = asyncio.Lock()
        self._cache: dict[str, Task] = {}

    def _task_path(self, task_id: str, owner: str) -> str:
        return os.path.join(self._base_dir, f"task_{owner}_{_safe_name(task_id)}.json")

    async def save(self, task: Task, context: ServerCallContext) -> None:
        owner = _owner_key(context)
        path = self._task_path(task.id, owner)
        d = MessageToDict(
            task,
            preserving_proto_field_name=False,
            always_print_fields_with_no_presence=True,
        )
        async with self._lock:
            await asyncio.to_thread(atomic_write, path, d)
            t = Task()
            t.CopyFrom(task)
            self._cache[f"{owner}:{task.id}"] = t

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        owner = _owner_key(context)
        ck = f"{owner}:{task_id}"
        async with self._lock:
            if ck in self._cache:
                t = Task()
                t.CopyFrom(self._cache[ck])
                return t
        path = self._task_path(task_id, owner)
        if not os.path.exists(path):
            return None
        d = await asyncio.to_thread(_read_json, path)
        task = Task()
        ParseDict(d, task)
        async with self._lock:
            t = Task()
            t.CopyFrom(task)
            self._cache[ck] = t
        return task

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        owner = _owner_key(context)
        async with self._lock:
            # Populate cache from disk if needed
            tasks = []
            for fname in os.listdir(self._base_dir):
                if fname.startswith(f"task_{owner}_") and fname.endswith(".json"):
                    ck = f"{owner}:{fname[len(f'task_{owner}_') : -5]}"
                    if ck not in self._cache:
                        fpath = os.path.join(self._base_dir, fname)
                        d = await asyncio.to_thread(_read_json, fpath)
                        task = Task()
                        ParseDict(d, task)
                        self._cache[ck] = task
                    tasks.append(self._cache[ck])
        return ListTasksResponse(
            tasks=tasks, total_size=len(tasks), page_size=len(tasks)
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        owner = _owner_key(context)
        path = self._task_path(task_id, owner)
        async with self._lock:
            self._cache.pop(f"{owner}:{task_id}", None)
        await asyncio.to_thread(_remove_sync, path)


class RedisAgentCrewTaskStore(TaskStore):
    """Redis-backed SDK TaskStore — tasks survive restart. Agent-namespaced."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        agent_namespace: str = "",
        **kwargs: Any,
    ) -> None:
        self._redis_url = redis_url
        ns = _safe_name(agent_namespace) if agent_namespace else ""
        self._prefix = f"a2a_v1_task_{ns}" if ns else "a2a_v1_task"
        self._ttl = kwargs.get("ttl", 3600)
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _key(self, task_id: str, owner: str) -> str:
        return f"{self._prefix}:{owner}:{_safe_name(task_id)}"

    async def save(self, task: Task, context: ServerCallContext) -> None:
        r = await self._get_redis()
        owner = _owner_key(context)
        d = MessageToDict(
            task,
            preserving_proto_field_name=False,
            always_print_fields_with_no_presence=True,
        )
        await r.setex(self._key(task.id, owner), self._ttl, json.dumps(d))

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        r = await self._get_redis()
        owner = _owner_key(context)
        raw = await r.get(self._key(task_id, owner))
        if not raw:
            return None
        d = json.loads(raw)
        task = Task()
        ParseDict(d, task)
        return task

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        r = await self._get_redis()
        owner = _owner_key(context)
        pattern = f"{self._prefix}:{owner}:*"
        cursor = 0
        keys = []
        while True:
            cursor, batch = await r.scan(cursor=cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        tasks = []
        for k in keys:
            raw = await r.get(k)
            if raw:
                d = json.loads(raw)
                task = Task()
                ParseDict(d, task)
                tasks.append(task)
        return ListTasksResponse(
            tasks=tasks, total_size=len(tasks), page_size=len(tasks)
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        r = await self._get_redis()
        owner = _owner_key(context)
        await r.delete(self._key(task_id, owner))

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


def create_task_store(store_type: str = "memory", **options: Any) -> TaskStore:
    """Factory for SDK TaskStore (protocol task persistence)."""
    if store_type == "redis":
        return RedisAgentCrewTaskStore(**options)
    if store_type == "file":
        return FileAgentCrewTaskStore(**options)
    return InMemoryAgentCrewTaskStore(**options)
