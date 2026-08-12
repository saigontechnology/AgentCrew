"""Tool-call orchestration for A2A executor.

This module handles tool execution, parallel flushing, and ask tool handling
with preserved compatibility for executor wrapper methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.tools.parallel_executor import (
    ToolResult,
    execute_tools_in_parallel,
    is_sequential_tool,
)

if TYPE_CHECKING:
    from a2a.server.events import EventQueue

    from AgentCrew.modules.a2a.session_store import AgentCrewSessionStore
    from AgentCrew.modules.agents import LocalAgent


class ToolCallResult:
    CONTINUE = "continue"
    INPUT_REQUIRED = "input_required"


class ToolExecutor:
    """Orchestrates tool execution for A2A executor.

    Handles sequential vs parallel tool execution, validation, CancelOperation
    handling, pending-tool persistence, ask INPUT_REQUIRED events, history
    ordering, and owner/agent namespace isolation.
    """

    def __init__(
        self,
        session_store: AgentCrewSessionStore,
        agent_namespace: str,
        now_timestamp_factory: Any,
    ) -> None:
        self._session_store = session_store
        self._agent_namespace = agent_namespace
        self._now_timestamp = now_timestamp_factory

    async def execute_tool_calls(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
        owner: str,
        cancel_event_getter: Any,
    ) -> str:
        """Execute tool calls with sequential/parallel ordering."""
        cancel_event = await cancel_event_getter(task_id)
        parallel_buffer: list[dict[str, Any]] = []

        for i, tool_use in enumerate(tool_uses):
            if cancel_event.is_set():
                from AgentCrew.modules.a2a.exceptions import TaskCanceledException

                raise TaskCanceledException(f"Task {task_id} was canceled")

            tool_name = tool_use.get("name")
            if not tool_name:
                continue

            if is_sequential_tool(tool_name):
                if parallel_buffer:
                    await self.flush_parallel(
                        agent,
                        task_id,
                        context_id,
                        parallel_buffer,
                        history,
                        owner,
                    )
                    parallel_buffer = []
                result = await self.execute_single_tool(
                    agent,
                    task_id,
                    context_id,
                    tool_use,
                    history,
                    event_queue,
                    owner,
                    cancel_event_getter,
                )
                if result == ToolCallResult.INPUT_REQUIRED:
                    remaining = tool_uses[i + 1 :]
                    await self._session_store.save_pending_tools(
                        task_id,
                        tool_use,
                        remaining,
                        owner,
                        self._agent_namespace,
                    )
                    return ToolCallResult.INPUT_REQUIRED
            else:
                parallel_buffer.append(tool_use)

        if parallel_buffer:
            await self.flush_parallel(
                agent,
                task_id,
                context_id,
                parallel_buffer,
                history,
                owner,
            )

        return ToolCallResult.CONTINUE

    async def execute_single_tool(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_use: dict[str, Any],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
        owner: str,
        cancel_event_getter: Any,
    ) -> str:
        """Execute a single tool with validation and error handling."""
        tool_name = tool_use["name"]

        # --- Validate every tool once before any dispatch -----------------
        error_text = agent.validate_tool_use(tool_use)
        if error_text is not None:
            error_message = agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": error_text,
                    "is_error": True,
                },
            )
            if error_message:
                await self._session_store.append_history(
                    context_id, error_message, owner
                )
                history.append(error_message)
            return ToolCallResult.CONTINUE

        if tool_name == "ask":
            return await self.handle_ask_tool(
                agent,
                task_id,
                context_id,
                tool_use,
                history,
                event_queue,
                owner,
            )

        try:
            tool_result = await agent.execute_tool_call(tool_use)
            tool_result_message = agent.format_message(
                MessageType.ToolResult,
                {"tool_use": tool_use, "tool_result": tool_result},
            )
            if tool_result_message:
                await self._session_store.append_history(
                    context_id, tool_result_message, owner
                )
                history.append(tool_result_message)
        except CancelOperation:
            cancelled_message = agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": "Tool execution cancelled by a hook",
                    "is_error": True,
                    "is_rejected": True,
                },
            )
            if cancelled_message:
                await self._session_store.append_history(
                    context_id, cancelled_message, owner
                )
                history.append(cancelled_message)
        except Exception as e:
            error_message = agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": str(e),
                    "is_error": True,
                },
            )
            if error_message:
                await self._session_store.append_history(
                    context_id, error_message, owner
                )
                history.append(error_message)
        return ToolCallResult.CONTINUE

    async def flush_parallel(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
        owner: str,
    ) -> None:
        """Flush parallel tool calls with validation and result recording."""
        if not tool_uses:
            return

        # Validate each parallel tool; collect valid for execution, record errors
        pre_results: list[ToolResult | None] = [None] * len(tool_uses)
        valid_indices: list[int] = []
        for i, tu in enumerate(tool_uses):
            err = agent.validate_tool_use(tu)
            if err is not None:
                pre_results[i] = ToolResult(
                    tool_use=tu, result=err, is_error=True, was_executed=False
                )
            else:
                valid_indices.append(i)

        valid_tools = [tool_uses[i] for i in valid_indices]
        exec_results = await execute_tools_in_parallel(
            valid_tools, agent.execute_tool_call
        )

        # Interleave results in original order
        for result_idx, orig_idx in enumerate(valid_indices):
            pre_results[orig_idx] = exec_results[result_idx]

        for r in pre_results:
            if r is None:
                continue
            msg = agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": r.tool_use,
                    "tool_result": r.result,
                    "is_error": r.is_error,
                },
            )
            if msg:
                await self._session_store.append_history(context_id, msg, owner)
                history.append(msg)

    async def handle_ask_tool(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_use: dict[str, Any],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
        owner: str,
    ) -> str:
        """Handle ask tool with pending tool persistence and INPUT_REQUIRED event."""
        from .adapters import create_ask_message

        questions = tool_use["input"].get("questions", [])
        if not questions or not isinstance(questions, list):
            questions = []

        ask_msg = create_ask_message(questions)
        await self._session_store.save_pending_tools(
            task_id, tool_use, [], owner, self._agent_namespace
        )

        ts = self._now_timestamp()
        from a2a.types.a2a_pb2 import TaskState, TaskStatus, TaskStatusUpdateEvent

        status = TaskStatus(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            timestamp=ts,
        )
        if ask_msg:
            status.message.CopyFrom(ask_msg)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=status,
            )
        )
        return ToolCallResult.INPUT_REQUIRED
