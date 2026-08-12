"""
AgentCrew session stores and durable SDK TaskStore adapters.

Separation of concerns:
- AgentCrewSessionStore — LLM history + pending tool state (AgentCrew owned)
- SDK TaskStore adapters (create_task_store) — protocol task persistence

Identity rules:
- Conversation history is keyed by ``{owner}:{context_id}`` with NO agent
  namespace, so a conversation can continue across agents.
- Pending tool state is keyed by ``{agent}:{owner}:{task_id}`` and protocol
  tasks by ``{agent}:{owner}:{task_id}`` — both stay agent-namespaced.
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
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task, TaskState
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


def _safe_owner(owner: str) -> str:
    """Sanitize an owner/tenant for use in keys. Empty falls back to 'default'."""
    return _safe_name(owner) if owner else "default"


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


_TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
}


def _task_to_jsonl_line(task: Task) -> str:
    """Serialize a Task as one compact JSONL line (camelCase keys)."""
    d = MessageToDict(
        task,
        preserving_proto_field_name=False,
        always_print_fields_with_no_presence=True,
    )
    return json.dumps(d, separators=(",", ":")) + "\n"


def _parse_task_line(line: str) -> Task | None:
    """Parse a single JSONL line into a Task; None when torn/invalid."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        task = Task()
        ParseDict(d, task)
        return task
    except Exception:
        return None


def _is_single_json_object(content: str) -> bool:
    """True when *content* is a single JSON object (legacy task file)."""
    try:
        return isinstance(json.loads(content), dict)
    except (json.JSONDecodeError, ValueError):
        return False


def _inspect_task_file(path: str) -> tuple[Task | None, int]:
    """Read *path* once; return the latest task and its JSONL line count.

    A count of ``-1`` marks a legacy single-object JSON file that still needs
    migration to JSONL on the next save. Torn/invalid JSONL lines are
    ignored when counting so a crash mid-append cannot poison steady-state
    appends.
    """
    try:
        content = _read_content(path)
    except OSError:
        return None, 0
    if not content.strip():
        return None, 0
    if _is_single_json_object(content) and "\n" in content.strip("\n"):
        try:
            task = Task()
            ParseDict(json.loads(content), task)
            return task, -1
        except Exception:
            return None, -1
    last_task = None
    count = 0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        task = _parse_task_line(line)
        if task is not None:
            last_task = task
            count += 1
    return last_task, count


def _read_content(path: str) -> str:
    """Synchronous helper: read raw file content."""
    with open(path) as f:
        return f.read()


def _append_jsonl(path: str, line: str) -> None:
    """Append one JSONL line to *path*, preserving a JSONL record boundary.

    Inspects only the final byte and inserts a newline when a non-empty file
    does not end with one, so a torn trailing fragment left by a crash
    mid-append cannot merge with the next snapshot into one invalid line.
    """
    with open(path, "a+") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size > 0:
            f.seek(size - 1)
            if f.read(1) != "\n":
                f.write("\n")
        f.write(line)


def _write_jsonl_single(path: str, line: str) -> None:
    """Atomically rewrite *path* containing only *line* (tmp + replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(line)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# AgentCrewSessionStore — AgentCrew-owned execution state
# ---------------------------------------------------------------------------


class AgentCrewSessionStore(ABC):
    """Abstract store for AgentCrew execution state (history + pending tools).

    Identity rules:
    - History is keyed by ``{owner}:{context_id}`` and intentionally has NO
      agent namespace so a conversation can continue across agents.
    - Pending tool state is keyed by ``{agent}:{owner}:{task_id}`` and stays
      isolated per agent and per owner.
    """

    @abstractmethod
    async def get_history(
        self, context_id: str, owner: str = "default"
    ) -> list[dict[str, Any]]: ...
    @abstractmethod
    async def append_history(
        self, context_id: str, message: dict[str, Any], owner: str = "default"
    ) -> None: ...
    @abstractmethod
    async def save_pending_tools(
        self,
        task_id: str,
        ask_tool_use: dict,
        remaining_tools: list,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None: ...
    @abstractmethod
    async def get_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> dict | None: ...
    @abstractmethod
    async def clear_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> None: ...
    @abstractmethod
    async def cleanup(
        self,
        task_id: str,
        context_id: str,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None: ...
    async def close(self) -> None:
        pass


class InMemorySessionStore(AgentCrewSessionStore):
    """In-memory implementation. State lost on restart.

    History is shared across agents for the same owner + context; pending tool
    state is isolated by agent namespace + owner + task.
    """

    def __init__(self, agent_namespace: str = "") -> None:
        self._agent = _safe_name(agent_namespace) if agent_namespace else ""
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._pending: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _hk(self, cid: str, owner: str) -> str:
        return f"{_safe_owner(owner)}:{cid}"

    def _pk(self, tid: str, owner: str, agent_namespace: str = "") -> str:
        ns = _safe_name(agent_namespace) if agent_namespace else self._agent
        key = f"{_safe_owner(owner)}:{tid}"
        return f"{ns}:{key}" if ns else key

    async def get_history(
        self, context_id: str, owner: str = "default"
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._histories.get(self._hk(context_id, owner), []))

    async def append_history(
        self, context_id: str, message: dict[str, Any], owner: str = "default"
    ) -> None:
        k = self._hk(context_id, owner)
        async with self._lock:
            if k not in self._histories:
                self._histories[k] = []
            self._histories[k].append(message)

    async def save_pending_tools(
        self,
        task_id: str,
        ask_tool_use: dict,
        remaining_tools: list,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None:
        async with self._lock:
            self._pending[self._pk(task_id, owner, agent_namespace)] = {
                "ask_tool_use": ask_tool_use,
                "remaining_tools": remaining_tools,
            }

    async def get_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> dict | None:
        async with self._lock:
            return self._pending.get(self._pk(task_id, owner, agent_namespace))

    async def clear_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> None:
        async with self._lock:
            self._pending.pop(self._pk(task_id, owner, agent_namespace), None)

    async def cleanup(
        self,
        task_id: str,
        context_id: str,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None:
        async with self._lock:
            self._pending.pop(self._pk(task_id, owner, agent_namespace), None)
            self._histories.pop(self._hk(context_id, owner), None)


class FileSessionStore(AgentCrewSessionStore):
    """File-based. Survives restart. Per-file lock.

    History files live directly in the shared ``base_dir`` keyed by
    owner + context so agents/containers sharing the same directory continue a
    conversation. Pending tool state lives in an agent-namespaced subdirectory.
    """

    def __init__(
        self, base_dir: str = ".agentcrew/a2a_v1", agent_namespace: str = ""
    ) -> None:
        self._agent = _safe_name(agent_namespace) if agent_namespace else ""
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self._file_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, path: str) -> asyncio.Lock:
        if path not in self._file_locks:
            self._file_locks[path] = asyncio.Lock()
        return self._file_locks[path]

    def _history_path(self, context_id: str, owner: str) -> str:
        return os.path.join(
            self.base_dir,
            f"history_{_safe_owner(owner)}_{_safe_name(context_id)}.json",
        )

    def _pending_dir(self, agent_namespace: str = "") -> str:
        ns = _safe_name(agent_namespace) if agent_namespace else self._agent
        d = os.path.join(self.base_dir, ns) if ns else self.base_dir
        os.makedirs(d, exist_ok=True)
        return d

    def _pending_path(self, task_id: str, owner: str, agent_namespace: str = "") -> str:
        return os.path.join(
            self._pending_dir(agent_namespace),
            f"pending_{_safe_owner(owner)}_{_safe_name(task_id)}.json",
        )

    async def get_history(
        self, context_id: str, owner: str = "default"
    ) -> list[dict[str, Any]]:
        path = self._history_path(context_id, owner)
        async with self._lock_for(path):
            if not os.path.exists(path):
                return []
            return await asyncio.to_thread(_read_json, path)

    async def append_history(
        self, context_id: str, message: dict[str, Any], owner: str = "default"
    ) -> None:
        path = self._history_path(context_id, owner)
        async with self._lock_for(path):
            history = []
            if os.path.exists(path):
                history = await asyncio.to_thread(_read_json, path)
            history.append(message)
            await asyncio.to_thread(atomic_write, path, history)

    async def save_pending_tools(
        self,
        task_id: str,
        ask_tool_use: dict,
        remaining_tools: list,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None:
        path = self._pending_path(task_id, owner, agent_namespace)
        data = {"ask_tool_use": ask_tool_use, "remaining_tools": remaining_tools}
        async with self._lock_for(path):
            await asyncio.to_thread(atomic_write, path, data)

    async def get_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> dict | None:
        path = self._pending_path(task_id, owner, agent_namespace)
        async with self._lock_for(path):
            if not os.path.exists(path):
                return None
            return await asyncio.to_thread(_read_json, path)

    async def clear_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> None:
        path = self._pending_path(task_id, owner, agent_namespace)
        async with self._lock_for(path):
            await asyncio.to_thread(_remove_sync, path)

    async def cleanup(
        self,
        task_id: str,
        context_id: str,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None:
        await self.clear_pending_tools(task_id, owner, agent_namespace)
        hpath = self._history_path(context_id, owner)
        async with self._lock_for(hpath):
            await asyncio.to_thread(_remove_sync, hpath)


class RedisSessionStore(AgentCrewSessionStore):
    """Redis-backed session store. Survives restart. Agent-namespaced prefix.

    History uses a shared prefix keyed by owner + context; pending tool state
    uses an agent-namespaced prefix.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        agent_namespace: str = "",
        **kwargs: Any,
    ) -> None:
        self._redis_url = redis_url
        ns = _safe_name(agent_namespace) if agent_namespace else ""
        self._agent = ns
        self._prefix = f"a2a_v1_sesh_{ns}" if ns else "a2a_v1_sesh"
        self._history_prefix = "a2a_v1_sesh"
        self._ttl = kwargs.get("ttl", 3600)
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _hk(self, cid: str, owner: str) -> str:
        return f"{self._history_prefix}:history:{_safe_owner(owner)}:{_safe_name(cid)}"

    def _pk(self, tid: str, owner: str, agent_namespace: str = "") -> str:
        ns = _safe_name(agent_namespace) if agent_namespace else self._agent
        prefix = f"a2a_v1_sesh_{ns}" if ns else "a2a_v1_sesh"
        return f"{prefix}:pending:{_safe_owner(owner)}:{_safe_name(tid)}"

    async def get_history(
        self, context_id: str, owner: str = "default"
    ) -> list[dict[str, Any]]:
        r = await self._get_redis()
        raw = await r.get(self._hk(context_id, owner))
        return json.loads(raw) if raw else []

    async def append_history(
        self, context_id: str, message: dict[str, Any], owner: str = "default"
    ) -> None:
        r = await self._get_redis()
        k = self._hk(context_id, owner)
        raw = await r.get(k)
        history = json.loads(raw) if raw else []
        history.append(message)
        await r.setex(k, self._ttl, json.dumps(history))

    async def save_pending_tools(
        self,
        task_id: str,
        ask_tool_use: dict,
        remaining_tools: list,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None:
        r = await self._get_redis()
        data = {"ask_tool_use": ask_tool_use, "remaining_tools": remaining_tools}
        await r.setex(
            self._pk(task_id, owner, agent_namespace), self._ttl, json.dumps(data)
        )

    async def get_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> dict | None:
        r = await self._get_redis()
        raw = await r.get(self._pk(task_id, owner, agent_namespace))
        return json.loads(raw) if raw else None

    async def clear_pending_tools(
        self, task_id: str, owner: str = "default", agent_namespace: str = ""
    ) -> None:
        r = await self._get_redis()
        await r.delete(self._pk(task_id, owner, agent_namespace))

    async def cleanup(
        self,
        task_id: str,
        context_id: str,
        owner: str = "default",
        agent_namespace: str = "",
    ) -> None:
        r = await self._get_redis()
        await r.delete(self._pk(task_id, owner, agent_namespace))
        await r.delete(self._hk(context_id, owner))

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
    """File-backed SDK TaskStore — tasks survive restart. Agent-namespaced subdir.

    Tasks are persisted as JSONL: every ``save()`` appends one compact JSON
    snapshot line, and reads return the LAST valid line (tolerating a torn
    trailing line). Legacy single-object JSON files written by earlier
    versions are still readable and migrated on the next save. Files are
    compacted atomically back to a single line once the tracked snapshot
    count reaches ``_JSONL_COMPACTION_THRESHOLD`` or the saved task reaches
    a terminal state. Steady-state saves append without rereading prior
    snapshots; ``MessageToDict`` and ``Task.CopyFrom`` still scale with the
    current task size.
    """

    _JSONL_COMPACTION_THRESHOLD = 100

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
        self._file_locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, Task] = {}
        # Per-path JSONL snapshot line count; -1 marks a legacy file pending
        # migration. An absent key means the file has not been inspected yet.
        self._line_counts: dict[str, int] = {}

    def _lock_for(self, path: str) -> asyncio.Lock:
        if path not in self._file_locks:
            self._file_locks[path] = asyncio.Lock()
        return self._file_locks[path]

    def _task_path(self, task_id: str, owner: str) -> str:
        return os.path.join(self._base_dir, f"task_{owner}_{_safe_name(task_id)}.json")

    async def _line_count_for(self, path: str) -> int:
        """Tracked JSONL line count for *path*, initializing from disk once.

        Must be called while holding the per-path lock. Legacy files are
        reported as ``-1`` and migrated by the next ``save()``.
        """
        count = self._line_counts.get(path)
        if count is None:
            if os.path.exists(path):
                _, count = await asyncio.to_thread(_inspect_task_file, path)
            else:
                count = 0
            self._line_counts[path] = count
        return count

    async def save(self, task: Task, context: ServerCallContext) -> None:
        owner = _owner_key(context)
        path = self._task_path(task.id, owner)
        line = _task_to_jsonl_line(task)
        is_terminal = task.status.state in _TERMINAL_TASK_STATES
        async with self._lock_for(path):
            count = await self._line_count_for(path)
            if count == -1 or is_terminal or count >= self._JSONL_COMPACTION_THRESHOLD:
                await asyncio.to_thread(_write_jsonl_single, path, line)
                self._line_counts[path] = 1
            else:
                await asyncio.to_thread(_append_jsonl, path, line)
                self._line_counts[path] = count + 1
            t = Task()
            t.CopyFrom(task)
            self._cache[f"{owner}:{task.id}"] = t

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        owner = _owner_key(context)
        ck = f"{owner}:{task_id}"
        path = self._task_path(task_id, owner)
        async with self._lock_for(path):
            if ck in self._cache:
                t = Task()
                t.CopyFrom(self._cache[ck])
                return t
            if not os.path.exists(path):
                return None
            task, count = await asyncio.to_thread(_inspect_task_file, path)
            if task is None:
                return None
            self._line_counts[path] = count
            cached = Task()
            cached.CopyFrom(task)
            self._cache[ck] = cached
            result = Task()
            result.CopyFrom(cached)
            return result

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        owner = _owner_key(context)
        tasks = []
        for fname in await asyncio.to_thread(os.listdir, self._base_dir):
            if fname.startswith(f"task_{owner}_") and fname.endswith(".json"):
                ck = f"{owner}:{fname[len(f'task_{owner}_') : -5]}"
                path = os.path.join(self._base_dir, fname)
                async with self._lock_for(path):
                    if ck in self._cache:
                        t = Task()
                        t.CopyFrom(self._cache[ck])
                        tasks.append(t)
                        continue
                    if not os.path.exists(path):
                        continue
                    task, count = await asyncio.to_thread(_inspect_task_file, path)
                    if task is None:
                        continue
                    self._line_counts[path] = count
                    cached = Task()
                    cached.CopyFrom(task)
                    self._cache[ck] = cached
                    t = Task()
                    t.CopyFrom(cached)
                    tasks.append(t)
        return ListTasksResponse(
            tasks=tasks, total_size=len(tasks), page_size=len(tasks)
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        owner = _owner_key(context)
        path = self._task_path(task_id, owner)
        async with self._lock_for(path):
            self._cache.pop(f"{owner}:{task_id}", None)
            self._line_counts.pop(path, None)
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
