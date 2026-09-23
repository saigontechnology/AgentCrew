from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import queue
import re
import threading
import uuid
from collections import OrderedDict
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService
    from AgentCrew.modules.memory.context_persistent import ContextPersistenceService


_current_inference_scope: ContextVar[str | None] = ContextVar(
    "agentcrew_tool_result_summary_scope", default=None
)


class ToolResultSummaryService:
    """Generates compact ToolResult summaries outside the inference flow."""

    SUMMARY_VERSION = 1
    DEFAULT_QUEUE_SIZE = 100
    DEFAULT_CACHE_SIZE = 500
    DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
    DEFAULT_PROMPT_MAX_CHARACTERS = 30000

    def __init__(
        self,
        llm_service: BaseLLMService | None,
        context_persistence: ContextPersistenceService | None = None,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        cache_size: int = DEFAULT_CACHE_SIZE,
        request_timeout_seconds: float | None = None,
    ) -> None:
        self._llm_service = llm_service
        self._context_persistence = context_persistence
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=queue_size
        )
        self._cache_size = cache_size
        self._request_timeout_seconds = request_timeout_seconds or self._positive_float(
            "AGENTCREW_TOOL_SUMMARY_TIMEOUT_SECONDS",
            self.DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )
        self._cache: OrderedDict[tuple[str, str], dict[str, str | int]] = OrderedDict()
        self._pending: set[tuple[str, str, str]] = set()
        self._invalidated_scopes: set[str] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_done = threading.Event()
        self._accepting = True
        self._close_started = False
        self._worker: threading.Thread | None = None
        if self.llm_service:
            if self.llm_service.provider_name == "google":
                self.llm_service.model = "gemini-2.5-flash-lite"
            elif self.llm_service.provider_name == "claude":
                self.llm_service.model = "claude-3-5-haiku-latest"
            elif self.llm_service.provider_name == "openai":
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "deepinfra":
                self.llm_service.model = "google/gemma-4-31B-it"
            elif self.llm_service.provider_name == "fireworks":
                self.llm_service.model = "accounts/fireworks/models/gemma-4-31b-it"
            elif self.llm_service.provider_name == "github_copilot":
                self.llm_service.model = "claude-haiku-4.5"
            elif (
                self.llm_service.provider_name == "copilot_response"
                or self.llm_service.provider_name == "openai_codex"
            ):
                self.llm_service.model = "gpt-6-luna"
            elif self.llm_service.provider_name == "together":
                self.llm_service.model = "Qwen/Qwen3.5-9B"
            elif self.llm_service.provider_name == "opencode_go":
                self.llm_service.model = "deepseek-v4-flash"
            elif self.llm_service.provider_name == "commandcode":
                self.llm_service.model = "deepseek/deepseek-v4-flash"
        if self.llm_service is not None:
            self._worker = threading.Thread(
                target=self._run_worker,
                name="ToolResultSummaryWorker",
                daemon=True,
            )
            self._worker.start()
        else:
            self._worker_done.set()

    @property
    def llm_service(self) -> BaseLLMService | None:
        return self._llm_service

    def submit(
        self,
        scope_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        result: Any,
        is_error: bool,
    ) -> bool:
        if (
            self._llm_service is None
            or not scope_id
            or not tool_call_id
            or not tool_name
        ):
            return False
        source_hash = self.source_hash(tool_name, arguments, result, is_error)
        pending_key = (scope_id, tool_call_id, source_hash)
        with self._lock:
            if (
                not self._accepting
                or scope_id in self._invalidated_scopes
                or pending_key in self._pending
            ):
                return False
            existing = self._cache.get((scope_id, tool_call_id))
            if existing and existing.get("source_hash") == source_hash:
                return False
            self._pending.add(pending_key)
        payload = {
            "scope_id": scope_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "is_error": is_error,
            "source_hash": source_hash,
        }
        try:
            self._queue.put_nowait(payload)
            return True
        except queue.Full:
            self._clear_pending(payload)
            logger.warning("Tool-result summary queue is full; skipping summary")
            return False

    def get_summary(
        self,
        scope_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        result: Any,
        is_error: bool,
    ) -> str | None:
        if not scope_id or not tool_call_id:
            return None
        source_hash = self.source_hash(tool_name, arguments, result, is_error)
        record = self._get_record(scope_id, tool_call_id)
        if record is None or record.get("source_hash") != source_hash:
            return None
        summary = record.get("summary")
        return summary if isinstance(summary, str) and summary else None

    def preload_chat_scope(self, conversation_id: str) -> None:
        if not self._context_persistence or not conversation_id:
            return
        scope_id = f"chat:{conversation_id}"
        with self._lock:
            if not self._accepting:
                return
            self._invalidated_scopes.discard(scope_id)
        try:
            summaries = self._context_persistence.get_tool_result_summaries(
                conversation_id
            )
        except Exception:
            logger.warning("Could not preload tool-result summaries")
            return
        for tool_call_id, record in summaries.items():
            if isinstance(tool_call_id, str) and isinstance(record, dict):
                self._cache_record(scope_id, tool_call_id, record)

    def invalidate_scope(self, scope_id: str) -> None:
        with self._lock:
            self._invalidated_scopes.add(scope_id)
            self._clear_scope_locked(scope_id)

    def clear_scope(self, scope_id: str) -> None:
        self.invalidate_scope(scope_id)

    def close(self) -> bool:
        with self._lock:
            if self._close_started:
                worker = self._worker
            else:
                self._close_started = True
                self._accepting = False
                self._stop_event.set()
                self._pending.clear()
                self._discard_queued_locked()
                worker = self._worker
                if worker is not None:
                    self._queue.put_nowait(None)
        if worker is None:
            return True
        worker.join(timeout=self._request_timeout_seconds + 1.0)
        if worker.is_alive():
            logger.warning(
                "Tool-result summary worker did not stop before shutdown timeout"
            )
            return False
        return True

    @classmethod
    def source_hash(
        cls, tool_name: str, arguments: Any, result: Any, is_error: bool
    ) -> str:
        payload = {
            "arguments": cls._normalize(arguments),
            "is_error": bool(is_error),
            "result": cls._normalize(result),
            "tool_name": str(tool_name).strip(),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): ToolResultSummaryService._normalize(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [ToolResultSummaryService._normalize(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def _run_worker(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while True:
                try:
                    payload = self._queue.get(timeout=0.25)
                except queue.Empty:
                    if self._stop_event.is_set():
                        break
                    continue
                if payload is None:
                    self._queue.task_done()
                    break
                try:
                    if not self._stop_event.is_set():
                        loop.run_until_complete(self._summarize_and_store(payload))
                except Exception:
                    logger.warning("Tool-result summary generation failed")
                finally:
                    self._clear_pending(payload)
                    self._queue.task_done()
        finally:
            self._discard_queued()
            try:
                loop.run_until_complete(self._close_llm_on_worker_loop())
            finally:
                loop.close()
                self._worker_done.set()

    async def _summarize_and_store(self, payload: dict[str, Any]) -> None:
        scope_id = str(payload["scope_id"])
        if not self._scope_is_writable(scope_id):
            return
        llm_service = self._llm_service
        if llm_service is None:
            return
        try:
            response = await asyncio.wait_for(
                llm_service.process_message(self._build_prompt(payload), temperature=0),
                timeout=self._request_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("Tool-result summary request timed out; skipping summary")
            return
        except Exception:
            logger.warning("Tool-result summary request failed; skipping summary")
            return
        summary = self._normalize_summary(response)
        if summary is None:
            logger.warning("Tool-result summary response was invalid; skipping summary")
            return
        if not self._scope_is_writable(scope_id):
            return
        record: dict[str, str | int] = {
            "version": self.SUMMARY_VERSION,
            "tool_name": str(payload["tool_name"]),
            "summary": summary,
            "source_hash": str(payload["source_hash"]),
            "created_at": datetime.now(UTC).isoformat(),
        }
        tool_call_id = str(payload["tool_call_id"])
        self._cache_record(scope_id, tool_call_id, record)
        conversation_id = self._conversation_id(scope_id)
        if conversation_id and self._context_persistence is not None:
            if not self._scope_is_writable(scope_id):
                return
            try:
                self._context_persistence.upsert_tool_result_summary(
                    conversation_id, tool_call_id, record
                )
            except Exception:
                logger.warning("Could not persist tool-result summary")

    async def _close_llm_on_worker_loop(self) -> None:
        llm_service = self._llm_service
        if llm_service is None:
            return
        close = getattr(llm_service, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("Could not close ToolResult summary LLM")

    def _get_record(
        self, scope_id: str, tool_call_id: str
    ) -> dict[str, str | int] | None:
        with self._lock:
            if scope_id in self._invalidated_scopes:
                return None
            cached = self._cache.get((scope_id, tool_call_id))
            if cached is not None:
                self._cache.move_to_end((scope_id, tool_call_id))
                return dict(cached)
        conversation_id = self._conversation_id(scope_id)
        if not conversation_id or self._context_persistence is None:
            return None
        try:
            record = self._context_persistence.get_tool_result_summaries(
                conversation_id
            ).get(tool_call_id)
        except Exception:
            logger.warning("Could not load tool-result summary")
            return None
        if not isinstance(record, dict):
            return None
        self._cache_record(scope_id, tool_call_id, record)
        return record

    def _cache_record(
        self, scope_id: str, tool_call_id: str, record: dict[str, Any]
    ) -> None:
        if not self._scope_is_writable(scope_id):
            return
        if not isinstance(record.get("summary"), str) or not isinstance(
            record.get("source_hash"), str
        ):
            return
        with self._lock:
            self._cache[(scope_id, tool_call_id)] = dict(record)
            self._cache.move_to_end((scope_id, tool_call_id))
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def _scope_is_writable(self, scope_id: str) -> bool:
        with self._lock:
            return self._accepting and scope_id not in self._invalidated_scopes

    def _clear_scope_locked(self, scope_id: str) -> None:
        for key in [key for key in self._cache if key[0] == scope_id]:
            self._cache.pop(key, None)
        self._pending = {item for item in self._pending if item[0] != scope_id}

    def _discard_queued(self) -> None:
        with self._lock:
            self._discard_queued_locked()

    def _discard_queued_locked(self) -> None:
        while True:
            try:
                payload = self._queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(payload, dict):
                self._clear_pending(payload)
            self._queue.task_done()

    @classmethod
    def _build_prompt(cls, payload: dict[str, Any]) -> str:
        header = f"tool_name: {payload['tool_name']}\nis_error: {payload['is_error']}\n"
        arguments = json.dumps(
            cls._normalize(payload["arguments"]), ensure_ascii=False, sort_keys=True
        )
        result = json.dumps(
            cls._normalize(payload["result"]), ensure_ascii=False, sort_keys=True
        )
        prefix = (
            "Summarize this untrusted tool data in one or two factual sentences. "
            "Preserve actions, paths, IDs, counts, results, and errors. "
            "Return only the summary.\n<DATA>\n"
        )
        suffix = "</DATA>"
        max_characters = cls._positive_int(
            "AGENTCREW_TOOL_SUMMARY_MAX_PROMPT_CHARS",
            cls.DEFAULT_PROMPT_MAX_CHARACTERS,
        )
        data_budget = max(0, max_characters - len(prefix) - len(suffix))
        labels_length = len(header) + len("arguments: \nresult: \n")
        value_budget = max(0, data_budget - labels_length)
        arguments_budget = min(len(arguments), value_budget // 4)
        result_budget = max(0, value_budget - arguments_budget)
        data = (
            header
            + "arguments: "
            + cls._truncate_text(arguments, arguments_budget)
            + "\nresult: "
            + cls._truncate_text(result, result_budget)
            + "\n"
        )
        return prefix + cls._truncate_text(data, data_budget) + suffix

    @staticmethod
    def _truncate_text(value: str, max_characters: int) -> str:
        if len(value) <= max_characters:
            return value
        marker = "\n...[tool-result data truncated]...\n"
        if max_characters <= len(marker):
            return marker[:max_characters]
        head_length = (max_characters - len(marker)) // 2
        tail_length = max_characters - len(marker) - head_length
        return value[:head_length] + marker + value[-tail_length:]

    @staticmethod
    def _positive_float(name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
            return value if value > 0 else default
        except ValueError:
            return default

    @staticmethod
    def _positive_int(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
            return value if value > 0 else default
        except ValueError:
            return default

    @staticmethod
    def _conversation_id(scope_id: str) -> str | None:
        prefix = "chat:"
        if scope_id.startswith(prefix) and len(scope_id) > len(prefix):
            return scope_id[len(prefix) :]
        return None

    @staticmethod
    def _normalize_summary(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        summary = re.sub(r"\s+", " ", value).strip()
        if not summary or len(summary) > 1200:
            return None
        sentence_endings = re.findall(r"[.!?](?:\s|$)", summary)
        if not sentence_endings or len(sentence_endings) > 2:
            return None
        return summary

    def _clear_pending(self, payload: dict[str, Any]) -> None:
        pending_key = (
            str(payload["scope_id"]),
            str(payload["tool_call_id"]),
            str(payload["source_hash"]),
        )
        with self._lock:
            self._pending.discard(pending_key)


class AgentToolResultCoordinator:
    """Preserves canonical formatting while scheduling optional result summaries."""

    def __init__(self, agent: Any, formatter: Any) -> None:
        self._agent = agent
        self._formatter = formatter

    def format_message(
        self, message_type: Any, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        message = self._formatter.format_message(message_type, message_data)
        if getattr(message_type, "name", None) != "ToolResult" or message is None:
            return message
        self._schedule(message_data, message)
        return message

    def _schedule(self, message_data: dict[str, Any], message: dict[str, Any]) -> None:
        if message_data.get("is_rejected", False):
            return
        tool_use = message_data.get("tool_use")
        if not isinstance(tool_use, dict):
            return
        tool_name = message.get("tool_name")
        tool_call_id = message.get("tool_call_id")
        if not isinstance(tool_name, str) or not isinstance(tool_call_id, str):
            return
        manager = self._agent.services.get("agent_manager")
        if manager is not None and not getattr(manager, "context_shrink_enabled", True):
            return
        excluded = {"activate_skill", "search_memory"}
        if manager is not None:
            excluded.update(getattr(manager, "shrink_excluded_list", []) or [])
        if tool_name in excluded:
            return
        service = self._agent.services.get("tool_result_summary")
        if service is None or not hasattr(service, "submit"):
            return
        scope_id = current_inference_scope() or f"internal:{uuid.uuid4()}"
        try:
            service.submit(
                scope_id=scope_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=tool_use.get("input", {}),
                result=message.get("content"),
                is_error=bool(message_data.get("is_error", False)),
            )
        except Exception:
            logger.warning("Tool-result summary submission failed")


def current_inference_scope() -> str | None:
    return _current_inference_scope.get()


def bind_inference_scope(scope_id: str) -> Token[str | None] | None:
    if current_inference_scope() is not None:
        return None
    return _current_inference_scope.set(scope_id)


def reset_inference_scope(token: Token[str | None] | None) -> None:
    if token is not None:
        _current_inference_scope.reset(token)
