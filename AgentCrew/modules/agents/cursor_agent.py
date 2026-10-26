"""Local Cursor SDK agent.

Cursor owns its run and transcript. AgentCrew owns routing through custom
tools: ``transfer`` hands the conversation back, ``delegate`` returns text
without changing the current agent, and memory tools are opt-in.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

from AgentCrew.modules.agents.agent_response_stream import AgentResponseStream
from AgentCrew.modules.agents.base import BaseAgent, MessageType
from AgentCrew.modules.config.agents_config import (
    DEFAULT_CURSOR_OPTIMIZE_FOR,
    validate_cursor_optimize_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from AgentCrew.modules.agents.manager import AgentManager

DEFAULT_MODEL_ID = "composer-2.5"
TRANSFER_ACCEPTANCE = "Accepted. Stop now. Do not continue."
_TERMINAL_RUN_STATUSES = frozenset({"finished", "error", "cancelled", "expired"})
_cwd_lock = threading.Lock()
_cwd_owners: dict[str, str] = {}


class CursorAgentError(RuntimeError):
    """Raised when a Cursor agent cannot be constructed from config."""


def _load_cursor_sdk():
    """Import the optional Cursor SDK. Never call this at module import time."""
    try:
        import cursor_sdk
    except ImportError as exc:
        raise ImportError(
            "cursor-sdk is not installed. Install it with: uv sync --extra cursor"
        ) from exc
    return cursor_sdk


def cursor_sdk_status() -> str | None:
    """Return a user-facing setup error, or None when the SDK and key exist."""
    if not os.environ.get("CURSOR_API_KEY"):
        return (
            "Cursor agent requires CURSOR_API_KEY in the environment. "
            "The key is not read from agents.toml."
        )
    try:
        _load_cursor_sdk()
    except ImportError as exc:
        return str(exc)
    return None


def _resolve_cwd(cwd: str | None) -> str:
    raw = cwd or "."
    return os.path.abspath(os.path.expanduser(raw))


def _content_text(message: Mapping[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type", "text") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(part for part in parts if part)


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_text(message)
    if messages:
        return _content_text(messages[-1])
    return ""


def _claim_cwd(cwd: str, owner: str) -> str | None:
    """Claim cwd for owner. Return the blocking owner, or None on success."""
    with _cwd_lock:
        current = _cwd_owners.get(cwd)
        if current and current != owner:
            return current
        _cwd_owners[cwd] = owner
        return None


def _release_cwd(cwd: str, owner: str) -> None:
    with _cwd_lock:
        if _cwd_owners.get(cwd) == owner:
            del _cwd_owners[cwd]


def _run_coroutine(factory: Callable[[], Any]) -> Any:
    """Run async work from a custom-tool callback.

    The Cursor SDK invokes sync custom tools on its loopback HTTP thread, not
    the AgentCrew event-loop thread. ``asyncio.run`` is safe there. If this is
    ever called on a running loop thread, the work moves to a worker thread so
    it cannot deadlock the loop.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running.is_running():
        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                box["result"] = asyncio.run(factory())
            except BaseException as exc:
                box["error"] = exc

        thread = threading.Thread(target=worker, name="cursor-delegate", daemon=True)
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box.get("result")
    return asyncio.run(factory())


def create_cursor_agent_from_definition(
    agent_def: Mapping[str, Any], services: dict[str, Any] | None = None
) -> CursorAgent:
    name = str(agent_def.get("name") or "").strip()
    if not name:
        raise CursorAgentError("Cursor agent name is required")
    return CursorAgent(
        name=name,
        description=str(agent_def.get("description") or ""),
        model_id=str(agent_def.get("model_id") or DEFAULT_MODEL_ID),
        cwd=str(agent_def.get("cwd") or "."),
        tools=agent_def.get("tools") or [],
        services=services,
        optimize_for=agent_def.get("optimize_for", DEFAULT_CURSOR_OPTIMIZE_FOR),
    )


def upsert_cursor_agent(
    agent_manager: AgentManager,
    agent_def: Mapping[str, Any],
    services: dict[str, Any] | None = None,
) -> bool:
    """Register or update a Cursor agent without constructing an LLM service."""
    name = str(agent_def.get("name") or "").strip()
    try:
        existing = agent_manager.agents.get(name)
        if isinstance(existing, CursorAgent):
            existing.apply_config(agent_def, services=services)
            return True
        if existing is not None and hasattr(existing, "release_llm"):
            existing.release_llm()  # type: ignore
        agent_manager.register_agent(
            create_cursor_agent_from_definition(agent_def, services)
        )
        status = cursor_sdk_status()
        if status:
            logger.warning(
                f"Cursor agent '{name}' is registered but not ready: {status}"
            )
        return True
    except Exception as exc:
        logger.warning(
            f"Cannot set up Cursor agent '{name or '<unnamed>'}', skipping: {exc}"
        )
        return False


class CursorAgent(BaseAgent):
    """AgentCrew agent whose turns run on the local Cursor SDK runtime."""

    def __init__(
        self,
        name: str,
        description: str,
        model_id: str = DEFAULT_MODEL_ID,
        cwd: str = ".",
        tools: list[str] | None = None,
        services: dict[str, Any] | None = None,
        optimize_for: str = DEFAULT_CURSOR_OPTIMIZE_FOR,
    ) -> None:
        super().__init__(name, description)
        self.tools = list(tools or [])
        self.services = services if services is not None else {}
        from .memory_coordinator import AgentMemoryCoordinator

        self._memory_coordinator = AgentMemoryCoordinator(self)
        self.model_id = model_id or DEFAULT_MODEL_ID
        self.optimize_for = validate_cursor_optimize_for(optimize_for)
        self.cwd = _resolve_cwd(cwd)
        self._cwd_owner = f"cursor:{id(self)}"
        self._cwd_claimed = False
        self._sdk_agent: Any = None
        self._sdk_agent_id: str | None = None
        self._active_run: Any = None
        self._sdk_lock = threading.Lock()
        self._pending_transfer: dict[str, Any] | None = None
        self._transfer_emitted = False
        self._announced_cwd = False

    def apply_config(
        self, agent_def: Mapping[str, Any], services: dict[str, Any] | None = None
    ) -> None:
        """Update routing fields and tool configuration without connecting to Cursor."""
        optimize_for = validate_cursor_optimize_for(
            agent_def.get("optimize_for", DEFAULT_CURSOR_OPTIMIZE_FOR)
        )
        self.description = str(agent_def.get("description") or "")
        model_id = str(agent_def.get("model_id") or DEFAULT_MODEL_ID)
        cwd = _resolve_cwd(str(agent_def.get("cwd") or "."))
        tools = list(agent_def.get("tools") or [])
        changed = (
            (model_id == "auto-smart" and optimize_for != self.optimize_for)
            or model_id != self.model_id
            or cwd != self.cwd
            or tools != self.tools
            or (services is not None and services is not self.services)
        )
        self.model_id = model_id
        self.optimize_for = optimize_for
        self.tools = tools
        if changed:
            self.dispose_sdk_agent()
        self.cwd = cwd
        if services is not None:
            self.services = services

    def activate(self) -> bool:
        self.is_active = True
        self._announced_cwd = False
        logger.info(f"Cursor agent '{self.name}' cwd: {self.cwd}")
        return True

    def deactivate(self) -> bool:
        self.is_active = False
        return True

    def append_message(self, messages: dict | list[dict]) -> None:
        if isinstance(messages, dict):
            self.history.append(messages)
        else:
            self.history.extend(messages)

    @property
    def clean_history(self) -> list:
        return self.history

    def store_memory_if_available(
        self,
        user_message: str,
        messages: list[dict],
        current_response: str,
        session_id: str | None = None,
    ) -> None:
        """Queue completed Cursor turns only when memory is configured."""
        if "memory" not in self.tools:
            return
        self._memory_coordinator.store_memory_if_available(
            user_message, messages, current_response, session_id
        )

    def get_provider(self) -> str:
        return "cursor"

    def get_model(self) -> str:
        return self.model_id

    def is_streaming(self) -> bool:
        return True

    def format_message(
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if message_type == MessageType.Assistant:
            return {
                "agent": self.name,
                "role": "assistant",
                "content": [{"type": "text", "text": message_data.get("message", "")}],
            }
        if message_type == MessageType.ToolResult:
            tool_use = message_data.get("tool_use", {"id": ""})
            return {
                "role": "tool",
                "agent": self.name,
                "tool_call_id": tool_use.get("id", ""),
                "content": message_data.get("tool_result", ""),
            }
        return None

    async def execute_tool_call(self, tool_use: dict) -> Any:
        """Cursor runs its own tools. AgentCrew only executes synthetic transfer."""
        if tool_use.get("name") != "transfer":
            return None
        from AgentCrew.modules.agents.tools.transfer import get_transfer_tool_handler

        handler = get_transfer_tool_handler(self._manager())
        return await handler(**(tool_use.get("input") or {}))

    def validate_tool_use(self, tool_use: dict[str, Any]) -> str | None:
        if tool_use.get("name") == "transfer":
            return None
        name = tool_use.get("name", "")
        return f"Error: Tool '{name}' is not available on this Cursor agent."

    def configure_think(self, think_setting) -> None:
        return None

    def calculate_usage_cost(
        self, input_tokens, output_tokens, cached_tokens=0
    ) -> float:
        return 0.0

    def dispose_sdk_agent(self) -> None:
        """Drop the SDK agent so the next chat cannot reuse its transcript."""
        with self._sdk_lock:
            run = self._active_run
            agent = self._sdk_agent
            self._active_run = None
            self._sdk_agent = None
            self._sdk_agent_id = None
            claimed = self._cwd_claimed
            self._cwd_claimed = False
        self._cancel_run(run)
        if agent is not None:
            close = getattr(agent, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    logger.warning(f"Failed to close Cursor agent '{self.name}': {exc}")
        if claimed:
            _release_cwd(self.cwd, self._cwd_owner)

    def process_messages(
        self,
        messages: list[dict[str, Any]] | None = None,
        callback: Callable | None = None,
    ) -> AgentResponseStream:
        return AgentResponseStream(
            agent=self,
            state_factory=lambda: None,
            iterator_factory=lambda _state: self._stream_messages(messages, callback),
        )

    async def _stream_messages(
        self,
        messages: list[dict[str, Any]] | None,
        callback: Callable | None,
    ):
        self._pending_transfer = None
        self._transfer_emitted = False
        history = messages if messages is not None else self.history
        user_text = _latest_user_text(history)
        status = cursor_sdk_status()
        if status:
            yield (status, status, None)
            return
        if not user_text.strip():
            error = "No user message to send to the Cursor agent."
            yield (error, error, None)
            return

        holder: dict[str, Any] = {"run": None, "thread": None}
        response = ""
        try:
            if not self._announced_cwd:
                self._announced_cwd = True
                yield ("", None, (f"Cursor agent cwd: {self.cwd}", None))
            async for message in self._drive_sdk_run(
                self._task_text(user_text), holder
            ):
                run = holder.get("run")
                if self._pending_transfer is not None:
                    self._cancel_run(run)
                    break
                delta, thinking = self._translate_message(message)
                if delta:
                    response += delta
                if delta or thinking:
                    yield (response, delta, thinking)
                if self._pending_transfer is not None:
                    self._cancel_run(run)
                    break
        except Exception as exc:
            logger.exception(f"Cursor agent '{self.name}' failed")
            error = f"Cursor agent error: {exc}"
            combined = f"{response}\n{error}".strip() if response else error
            yield (combined, error, None)
        finally:
            self._emit_transfer(callback)
            await asyncio.to_thread(self._cancel_run, holder.get("run"))
            thread = holder.get("thread")
            if thread is not None:
                await asyncio.to_thread(thread.join, 30)
            with self._sdk_lock:
                if self._active_run is holder.get("run"):
                    self._active_run = None

    def _task_text(self, user_text: str) -> str:
        context: list[str] = []
        memory = self.services.get("memory") if "memory" in self.tools else None
        persistence = self.services.get("context_persistent")
        if memory is not None:
            from AgentCrew.modules.memory.tool import (
                adaptive_instruction_prompt,
                memory_instruction_prompt,
            )

            context.append(memory_instruction_prompt())
            if persistence is not None:
                context.append(adaptive_instruction_prompt())
            try:
                headers = memory.list_memory_headers(agent_name=self.name)
                if headers:
                    context.append(
                        f"## Recent conversation memory headlines:\n- {'\n - '.join(headers)}\n---\nIf the user request related to any recent memories, call search_memory before responding."
                    )
            except Exception as exc:
                logger.warning(
                    f"Failed to list Cursor memory headlines for '{self.name}': {exc}"
                )
        if persistence is not None:
            from AgentCrew.modules.agents.context_manager import (
                build_persisted_behaviors,
            )

            try:
                behaviors = build_persisted_behaviors(persistence, self.name)
                if behaviors:
                    context.append(behaviors)
            except Exception as exc:
                logger.warning(
                    f"Failed to load Cursor behaviors for '{self.name}': {exc}"
                )
        plan = (
            "Generate then execute the plan to fulfill user task below, the plan must stay inside <agent_evaluation> tags:\n"
            "  - Plan out the tool call strategy for this task: which tools you should call, in what order, and what inputs each needs."
            f"  - The purpose of the planning is finding the optimal way to gather necessary information and organize steps to be taken to accomplished the task..\n\n{user_text}"
        )
        return "\n\n".join([*context, plan])

    def _sdk_model_selection(self, sdk: Any) -> Any:
        if self.model_id != "auto-smart":
            return self.model_id
        return sdk.ModelSelection(
            id="auto-smart",
            params=[
                sdk.ModelParameterValue(
                    id="optimize_for",
                    value=validate_cursor_optimize_for(self.optimize_for),
                )
            ],
        )

    def _ensure_sdk_agent(self) -> Any:
        with self._sdk_lock:
            if self._sdk_agent is not None:
                return self._sdk_agent
        blocker = _claim_cwd(self.cwd, self._cwd_owner)
        if blocker:
            raise CursorAgentError(
                f"Another Cursor agent is already active in {self.cwd}."
            )
        self._cwd_claimed = True
        try:
            sdk = _load_cursor_sdk()
            agent = sdk.Agent.create(
                name=self.name,
                model=self._sdk_model_selection(sdk),
                api_key=os.environ.get("CURSOR_API_KEY"),
                local=sdk.LocalAgentOptions(
                    cwd=self.cwd,
                    setting_sources=["project", "user"],
                    custom_tools=self._custom_tools(sdk),
                ),
            )
        except Exception:
            self._cwd_claimed = False
            _release_cwd(self.cwd, self._cwd_owner)
            raise
        with self._sdk_lock:
            self._sdk_agent = agent
            self._sdk_agent_id = str(getattr(agent, "agent_id", "") or "")
        return agent

    def _custom_tools(self, sdk: Any) -> dict[str, Any]:
        names = ", ".join(self._other_agent_names()) or "(none)"
        tools = {
            "transfer": sdk.CustomTool(
                description=(
                    "Hand the conversation back to another AgentCrew agent. "
                    "Call this only when the requested work is done; after it "
                    "returns, stop and do not continue editing. "
                    f"Available agents: {names}."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "target_agent": {
                            "type": "string",
                            "description": "AgentCrew agent that should become current.",
                        },
                        "task_description": {
                            "type": "string",
                            "description": "What the target agent should do next.",
                        },
                        "post_action": {
                            "type": "string",
                            "description": "Optional follow-up for the target agent.",
                        },
                    },
                    "required": ["target_agent", "task_description"],
                },
                execute=self._execute_transfer,
            ),
            "delegate": sdk.CustomTool(
                description=(
                    "Ask another AgentCrew agent to do a task and return text. "
                    "Do not use this to hand off the conversation. You remain "
                    "the current agent. "
                    f"Available agents: {names}."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "target_agent": {"type": "string"},
                        "task_description": {"type": "string"},
                        "context": {"type": "string"},
                    },
                    "required": ["target_agent", "task_description"],
                },
                execute=self._execute_delegate,
            ),
        }
        unsupported = set(self.tools) - {"memory"}
        if unsupported:
            logger.warning(
                f"Unsupported Cursor tools for '{self.name}': {sorted(unsupported)}"
            )
        memory = self.services.get("memory") if "memory" in self.tools else None
        if memory is None:
            tools.update(self._mcp_custom_tools(sdk))
            return tools

        from AgentCrew.modules.memory.tool import (
            MemoryToolContext,
            get_learn_behavior_tool_definition,
            get_learn_behavior_tool_handler,
            get_memory_forget_tool_definition,
            get_memory_forget_tool_handler,
            get_memory_retrieve_tool_definition,
            get_memory_retrieve_tool_handler,
        )

        persistence = self.services.get("context_persistent")
        context = MemoryToolContext(
            agent_name=self.name,
            memory_service=memory,
            persistence_service=persistence,
        )
        pairs: list[tuple[Callable, Callable]] = [
            (get_memory_retrieve_tool_definition, get_memory_retrieve_tool_handler),
            (get_memory_forget_tool_definition, get_memory_forget_tool_handler),
        ]
        if persistence is not None:
            pairs.append(
                (get_learn_behavior_tool_definition, get_learn_behavior_tool_handler)
            )
        for definition_factory, handler_factory in pairs:
            function = definition_factory()["function"]
            handler = handler_factory(context)

            def execute(
                args: Mapping[str, Any], _context: Any = None, *, _handler=handler
            ) -> str:
                return _run_coroutine(lambda: _handler(**(args or {})))

            tools[function["name"]] = sdk.CustomTool(
                description=function["description"],
                input_schema=function["parameters"],
                execute=execute,
            )
        tools.update(self._mcp_custom_tools(sdk))
        return tools

    def _mcp_custom_tools(self, sdk: Any) -> dict[str, Any]:
        """Load assigned MCP tools without registering local LLM tools."""
        try:
            from AgentCrew.modules.mcpclient.cursor_adapter import cursor_mcp_tools

            return cursor_mcp_tools(sdk, self.name, _run_coroutine)
        except Exception:
            logger.warning("Cursor MCP setup failed for agent '{}'", self.name)
            return {}

    def _execute_transfer(self, args: Mapping[str, Any], _context: Any = None) -> str:
        if self._pending_transfer is not None:
            return TRANSFER_ACCEPTANCE
        target = str((args or {}).get("target_agent") or "").strip()
        task = str((args or {}).get("task_description") or "").strip()
        post_action = str((args or {}).get("post_action") or "").strip()
        if not target or not task:
            return "Error: transfer requires target_agent and task_description."
        if target == self.name:
            return "Error: Cannot transfer to the current Cursor agent."
        manager = self._manager()
        if target not in manager.agents:
            available = ", ".join(sorted(manager.agents))
            return f"Error: Agent '{target}' not found. Available agents: {available}"
        recorded = {
            "target_agent": target,
            "task_description": task,
            "id": f"cursor-transfer-{uuid4()}",
        }
        if post_action:
            recorded["post_action"] = post_action
        self._pending_transfer = recorded
        self._cancel_run(self._active_run)
        return TRANSFER_ACCEPTANCE

    def _execute_delegate(self, args: Mapping[str, Any], _context: Any = None) -> str:
        target_name = str((args or {}).get("target_agent") or "").strip()
        task = str((args or {}).get("task_description") or "").strip()
        context = str((args or {}).get("context") or "")
        if not target_name or not task:
            return "Error: delegate requires target_agent and task_description."
        if target_name == self.name:
            return "Error: Cannot delegate to yourself."
        manager = self._manager()
        current = manager.current_agent
        if target_name not in manager.agents:
            available = ", ".join(sorted(manager.agents))
            return (
                f"Error: Agent '{target_name}' not found. Available agents: {available}"
            )
        target = manager.agents[target_name]
        try:
            if _is_local_agent(target):
                result = _run_coroutine(
                    lambda: _delegate_to_local(self.name, target, task, context)
                )
            elif _is_remote_agent(target):
                result = _run_coroutine(
                    lambda: _delegate_to_remote(self.name, target, task, context)
                )
            elif isinstance(target, CursorAgent):
                result = _delegate_to_cursor(self.name, target, task, context)
            else:
                result = (
                    f"Error: Agent '{target_name}' cannot accept Cursor delegation."
                )
        except Exception as exc:
            logger.exception(f"Cursor delegate to '{target_name}' failed")
            result = f"Error: Delegate to '{target_name}' failed: {exc}"
        if manager.current_agent is not current:
            manager.current_agent = current
        return result

    def _emit_transfer(self, callback: Callable | None) -> None:
        pending = self._pending_transfer
        if pending is None or self._transfer_emitted:
            return
        self._transfer_emitted = True
        tool_input = {
            "target_agent": pending["target_agent"],
            "task_description": pending["task_description"],
        }
        if pending.get("post_action"):
            tool_input["post_action"] = pending["post_action"]
        tool_use = {
            "id": pending["id"],
            "name": "transfer",
            "input": tool_input,
        }
        if callback is None:
            return
        from AgentCrew.modules.llm.token_usage import TokenUsage

        callback([tool_use], TokenUsage())

    def _other_agent_names(self) -> list[str]:
        return sorted(name for name in self._manager().agents if name != self.name)

    def _manager(self) -> AgentManager:
        from AgentCrew.modules.agents.manager import AgentManager

        return AgentManager.get_instance()

    async def _drive_sdk_run(self, prompt: str, holder: dict[str, Any]):
        """Send and stream on one worker thread.

        The SDK invokes custom tools on the thread that consumes ``stream()``.
        That must not be the running event-loop thread: delegate waits on its
        own loop, and ``asyncio.run`` there would deadlock.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        def enqueue(item: Any) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

        def worker() -> None:
            try:
                agent = self._ensure_sdk_agent()
                run = agent.send(prompt)
                holder["run"] = run
                with self._sdk_lock:
                    self._active_run = run
                for message in run.stream():
                    enqueue(message)
            except Exception as exc:
                enqueue(exc)
            finally:
                enqueue(sentinel)

        thread = threading.Thread(
            target=worker,
            name=f"cursor-stream-{self.name}",
            daemon=True,
        )
        holder["thread"] = thread
        thread.start()
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def _translate_message(
        self, message: Any
    ) -> tuple[str | None, tuple[str, None] | None]:
        message_type = _message_type(message)
        if message_type in {"assistant", "text-delta"}:
            texts = _assistant_texts(message)
            if not texts and message_type == "text-delta":
                text = _field(message, "text")
                texts = [text] if text else []
            if texts:
                return "".join(texts), None
            return None, None
        if message_type in {"thinking", "thinking-delta"}:
            text = _field(message, "text")
            if text:
                return None, (text, None)
            return None, None
        if message_type in {"tool_call", "tool-call-started", "tool-call-completed"}:
            name = _field(message, "name") or _nested_name(message)
            if name in {"transfer", "delegate"}:
                return None, None
            status = _field(message, "status") or message_type
            if name:
                return None, (f"Cursor tool {name}: {status}", None)
            return None, None
        if message_type == "status":
            text = _field(message, "message") or _field(message, "status")
            if text:
                return None, (text, None)
        return None, None

    @staticmethod
    def _cancel_run(run: Any) -> None:
        if run is None:
            return
        status = str(getattr(run, "status", "") or "")
        if status in _TERMINAL_RUN_STATUSES:
            return
        cancel = getattr(run, "cancel", None)
        if cancel is None:
            return
        try:
            cancel()
        except Exception as exc:
            logger.debug(f"Cursor run cancel skipped: {exc}")


def _message_type(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("type") or "")
    return str(getattr(message, "type", "") or "")


def _field(message: Any, name: str) -> str:
    if isinstance(message, dict):
        value = message.get(name, "")
    else:
        value = getattr(message, name, "")
    return str(value or "")


def _nested_name(message: Any) -> str:
    tool_call = (
        message.get("tool_call")
        if isinstance(message, dict)
        else getattr(message, "tool_call", None)
    )
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


def _assistant_texts(message: Any) -> list[str]:
    if isinstance(message, dict):
        inner = message.get("message", message)
        content = inner.get("content") if isinstance(inner, dict) else None
        if content is None and message.get("text"):
            return [str(message["text"])]
    else:
        inner = getattr(message, "message", None)
        content = getattr(inner, "content", None) if inner is not None else None
        if content is None and getattr(message, "text", None):
            return [str(message.text)]
    texts: list[str] = []
    for block in content or ():
        if isinstance(block, dict):
            text = block.get("text")
            block_type = block.get("type", "text")
        else:
            text = getattr(block, "text", None)
            block_type = getattr(block, "type", "text")
        if text and block_type in {"text", None}:
            texts.append(str(text))
    return texts


def _is_local_agent(agent: Any) -> bool:
    from AgentCrew.modules.agents.local_agent import LocalAgent

    return isinstance(agent, LocalAgent)


def _is_remote_agent(agent: Any) -> bool:
    from AgentCrew.modules.agents.remote_agent import RemoteAgent

    return isinstance(agent, RemoteAgent)


def _delegation_message(source: str, task: str, context: str) -> str:
    message = f"## Delegated Task from {source}\n\n{task}\n"
    if context:
        message += f"\n## Context\n{context}\n"
    message += (
        "\n## Instructions\n"
        "Complete the task above. Provide a thorough, complete response. "
        "Do not ask the parent for clarification; continue from the given task."
    )
    return message


async def _refuse_parent_clarification(_questions: list[dict[str, Any]]) -> str:
    return "Continue from the given task. Do not ask the parent for clarification."


async def _delegate_to_local(source: str, target: Any, task: str, context: str) -> str:
    from AgentCrew.modules.agents.agent_runner import run_agent_loop
    from AgentCrew.modules.agents.local_agent import LocalAgent
    from AgentCrew.modules.llm.model_registry import ModelRegistry
    from AgentCrew.modules.llm.service_manager import ServiceManager

    if target.llm is None:
        return f"Error: Agent '{target.name}' has no LLM service."
    registry = ModelRegistry.get_instance()
    model = registry.get_model(target.get_model())
    service_name = model.resolved_service_name() if model else target.llm.provider_name
    fresh_llm = ServiceManager.get_instance().initialize_standalone_service(
        service_name
    )
    fresh_llm.model = target.llm.model
    clone = LocalAgent(
        name=target.name,
        description=target.description,
        llm_service=fresh_llm,
        services=target.services,
        tools=target.tools,
        temperature=target.temperature,
        excluded_tools={"delegate", "transfer"},
        ask_responder=_refuse_parent_clarification,
    )
    clone.set_system_prompt(target.system_prompt or "")
    if target.custom_system_prompt:
        clone.set_custom_system_prompt(target.custom_system_prompt)
    try:
        clone.activate()
        response, _usage = await run_agent_loop(
            agent=clone,
            history=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _delegation_message(source, task, context),
                        }
                    ],
                }
            ],
            request_usage_callback=getattr(target, "record_conversation_usage", None),
        )
        return f"## Result from {target.name}:\n\n{response}"
    finally:
        try:
            clone.deactivate()
        finally:
            close = getattr(fresh_llm, "close", None)
            if close is not None:
                closed = close()
                if asyncio.iscoroutine(closed):
                    await closed


async def _delegate_to_remote(source: str, target: Any, task: str, context: str) -> str:
    from AgentCrew.modules.agents.remote_agent import RemoteAgent

    prompt = _delegation_message(source, task, context)
    one_shot = RemoteAgent(
        target.name,
        target.agent_url,
        headers=dict(getattr(target, "headers", {}) or {}),
    )
    stream = one_shot.process_messages(
        [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    )
    latest = ""
    try:
        async for item in stream:
            if isinstance(item, tuple) and item and item[0]:
                latest = str(item[0])
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
        await one_shot.close()
    if not latest.strip():
        return f"Error: Remote agent '{target.name}' returned no text."
    return f"## Result from {target.name}:\n\n{latest}"


def _delegate_to_cursor(
    source: str,
    target: CursorAgent,
    task: str,
    context: str,
    cancel_event: threading.Event | None = None,
    run_holder: dict[str, Any] | None = None,
) -> str:
    owner = f"cursor-oneshot:{uuid4()}"
    blocker = _claim_cwd(target.cwd, owner)
    if blocker:
        return (
            f"Error: Another Cursor agent is already active in {target.cwd}. "
            "Refusing to start a second Cursor agent in that directory."
        )
    agent = None
    try:
        sdk = _load_cursor_sdk()
        agent = sdk.Agent.create(
            name=f"{target.name}-oneshot",
            model=target._sdk_model_selection(sdk),
            api_key=os.environ.get("CURSOR_API_KEY"),
            local=sdk.LocalAgentOptions(
                cwd=target.cwd,
                setting_sources=["project", "user"],
                custom_tools=target._mcp_custom_tools(sdk),
            ),
        )
        if cancel_event is not None and cancel_event.is_set():
            return f"Error: Cursor delegation to '{target.name}' cancelled."
        run = agent.send(_delegation_message(source, task, context))
        if run_holder is not None:
            run_holder["run"] = run
        chunks: list[str] = []
        try:
            stream = getattr(run, "stream", None)
            if stream is not None:
                for message in stream():
                    if cancel_event is not None and cancel_event.is_set():
                        return f"Error: Cursor delegation to '{target.name}' cancelled."
                    delta, _thinking = target._translate_message(message)
                    if delta:
                        chunks.append(delta)
            text = "".join(chunks).strip()
            if not text:
                if cancel_event is not None and cancel_event.is_set():
                    return f"Error: Cursor delegation to '{target.name}' cancelled."
                result = run.wait()
                text = str(
                    getattr(result, "result", "") or getattr(run, "result", "") or ""
                ).strip()
        finally:
            CursorAgent._cancel_run(run)
        if not text:
            return f"Error: Cursor agent '{target.name}' returned no text."
        return f"## Result from {target.name}:\n\n{text}"
    finally:
        if agent is not None:
            close = getattr(agent, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    logger.warning(f"Failed to close one-shot Cursor agent: {exc}")
        _release_cwd(target.cwd, owner)
