"""
AgentCrewA2AExecutor — SDK AgentExecutor adapter for AgentCrew.

Key design:
- Task-scoped cancellation (not shared _cancel_event).
- Proper artifact append/last_chunk semantics across recursive LLM/tool rounds.
- Stable artifact IDs per task with answer state passed through recursion.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from a2a.helpers import get_message_text
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.timestamp_pb2 import Timestamp
from loguru import logger

from AgentCrew.modules.agents import LocalAgent
from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.tools.parallel_executor import (
    execute_tools_in_parallel,
    is_sequential_tool,
)

from .exceptions import TaskCanceledException
from .session_store import AgentCrewSessionStore


class ToolCallResult:
    CONTINUE = "continue"
    INPUT_REQUIRED = "input_required"


class AgentCrewA2AExecutor(AgentExecutor):
    """Adapts AgentCrew's async LLM/tool execution loop to the SDK AgentExecutor interface.

    Cancellation is task-scoped using a dict of asyncio.Event keyed by task_id,
    guarded by a lock for concurrent safety.
    Answer state is tracked via an AnswerArtifactState object passed through
    recursive _process_task calls so append semantics survive LLM/tool rounds.
    """

    def __init__(
        self,
        agent: LocalAgent,
        session_store: AgentCrewSessionStore,
    ) -> None:
        self.agent = agent
        self.session_store = session_store
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancel_lock = asyncio.Lock()

    async def _get_cancel_event(self, task_id: str) -> asyncio.Event:
        async with self._cancel_lock:
            if task_id not in self._cancel_events:
                self._cancel_events[task_id] = asyncio.Event()
            return self._cancel_events[task_id]

    async def _cleanup_cancel(self, task_id: str) -> None:
        async with self._cancel_lock:
            self._cancel_events.pop(task_id, None)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""

        cancel_event = await self._get_cancel_event(task_id)
        cancel_event.clear()

        try:
            history = await self.session_store.get_history(context_id)
            pending = await self.session_store.get_pending_tools(task_id)

            if pending:
                await self._resume_from_input_required(
                    context, event_queue, task_id, context_id, history, pending
                )
                return

            # New message
            user_message = self._convert_message_to_agent(context)
            if user_message:
                history.append(user_message)
                await self.session_store.append_history(context_id, user_message)

            # Enqueue initial Task (required by v1 streaming spec)
            initial_task = Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_SUBMITTED,
                    timestamp=self._now_timestamp(),
                ),
            )
            await event_queue.enqueue_event(initial_task)

            # Create answer artifact state — passed through recursion
            answer_state = AnswerArtifactState(artifact_id=f"answer_{task_id}")

            await self._run_agent_loop(
                context, event_queue, task_id, context_id, history, answer_state
            )
        except TaskCanceledException:
            logger.info(f"Task {task_id} canceled during processing")
        except asyncio.CancelledError:
            logger.info(f"Task {task_id} asyncio task cancelled externally")
            raise
        except Exception as e:
            logger.exception(f"Task {task_id} failed: {e}")
            if not cancel_event.is_set():
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_FAILED,
                            timestamp=self._now_timestamp(),
                        ),
                    )
                )
        finally:
            await self._cleanup_cancel(task_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        cancel_event = await self._get_cancel_event(task_id)
        cancel_event.set()

        ts = self._now_timestamp()
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context.context_id or "",
                status=TaskStatus(
                    state=TaskState.TASK_STATE_CANCELED,
                    timestamp=ts,
                ),
            )
        )
        logger.info(f"Task {task_id} canceled via executor")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _convert_message_to_agent(
        self, context: RequestContext
    ) -> dict[str, Any] | None:
        from .adapters import convert_a2a_message_to_agent

        msg = context.message
        if not msg:
            return None
        return convert_a2a_message_to_agent(msg)

    def _now_timestamp(self) -> Timestamp:
        ts = Timestamp()
        ts.FromDatetime(datetime.now(UTC))
        return ts

    async def _run_agent_loop(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        history: list[dict[str, Any]],
        answer_state: AnswerArtifactState,
    ) -> None:
        agent = self.agent
        artifacts: list[Any] = []
        token_usage = TokenUsage()
        retried_count = [0]
        cancel_event = await self._get_cancel_event(task_id)

        try:
            await self._process_task(
                context,
                event_queue,
                task_id,
                context_id,
                agent,
                history,
                artifacts,
                token_usage,
                retried_count,
                answer_state,
            )
        except TaskCanceledException:
            logger.info(f"Task {task_id} canceled during processing")
        except asyncio.CancelledError:
            logger.info(f"Task {task_id} asyncio task cancelled externally")
            raise
        except Exception as e:
            logger.exception(f"Task {task_id} failed: {e}")
            if not cancel_event.is_set():
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_FAILED,
                            timestamp=self._now_timestamp(),
                        ),
                    )
                )

    async def _process_task(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        agent: LocalAgent,
        history: list[dict[str, Any]],
        artifacts: list[Any],
        token_usage: TokenUsage,
        retried_count: list[int],
        answer_state: AnswerArtifactState,
    ) -> tuple[str, TokenUsage]:
        cancel_event = await self._get_cancel_event(task_id)
        current_response = ""
        tool_uses: list[dict[str, Any]] = []

        def process_result(_tool_uses, _token_usage):
            nonlocal tool_uses, token_usage
            tool_uses = _tool_uses
            if token_usage:
                token_usage = token_usage.merge(_token_usage)
            else:
                token_usage = _token_usage

        try:
            async for (
                response_message,
                chunk_text,
                thinking_chunk,
            ) in agent.process_messages(history, callback=process_result):
                if cancel_event.is_set():
                    raise TaskCanceledException(f"Task {task_id} was canceled")

                if response_message:
                    current_response = response_message

                # Working status
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_WORKING,
                            timestamp=self._now_timestamp(),
                        ),
                    )
                )

                # Answer chunks with proper append semantics
                if chunk_text:
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            task_id=task_id,
                            context_id=context_id,
                            artifact=Artifact(
                                artifact_id=answer_state.artifact_id,
                                parts=[Part(text=chunk_text)],
                            ),
                            append=answer_state.emitted,
                            last_chunk=False,
                        )
                    )
                    answer_state.emitted = True
                    answer_state.accumulated_text += chunk_text

                # Thinking chunks — separate artifact ID with append tracking
                if thinking_chunk:
                    think_text, _ = thinking_chunk
                    if think_text:
                        think_state = getattr(answer_state, "_think_state", None)
                        if think_state is None:
                            think_state = AnswerArtifactState(
                                artifact_id=f"thinking_{task_id}"
                            )
                            answer_state._think_state = think_state
                        await event_queue.enqueue_event(
                            TaskArtifactUpdateEvent(
                                task_id=task_id,
                                context_id=context_id,
                                artifact=Artifact(
                                    artifact_id=think_state.artifact_id,
                                    parts=[Part(text=think_text)],
                                ),
                                append=think_state.emitted,
                                last_chunk=False,
                            )
                        )
                        think_state.emitted = True

            if cancel_event.is_set():
                raise TaskCanceledException(
                    f"Task {task_id} was canceled after streaming"
                )

            if tool_uses:
                from .adapters import convert_agent_response_to_a2a_artifact

                tool_artifact = convert_agent_response_to_a2a_artifact(
                    "",
                    artifact_id=f"tool_{task_id}",
                    tool_uses=tool_uses,
                )
                if tool_artifact:
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            task_id=task_id,
                            context_id=context_id,
                            artifact=tool_artifact,
                        )
                    )

                assistant_message = agent.format_message(
                    MessageType.Assistant,
                    {
                        "message": current_response,
                        "tool_uses": [
                            t for t in tool_uses if t.get("name", "") != "transfer"
                        ],
                    },
                )
                if assistant_message:
                    await self.session_store.append_history(
                        context_id, assistant_message
                    )
                    history.append(assistant_message)

                result = await self._execute_tool_calls(
                    agent,
                    task_id,
                    context_id,
                    tool_uses,
                    history,
                    event_queue,
                )

                if result == ToolCallResult.INPUT_REQUIRED:
                    return "", token_usage

                # Recurse with same answer_state — append flag is preserved
                return await self._process_task(
                    context,
                    event_queue,
                    task_id,
                    context_id,
                    agent,
                    history,
                    artifacts,
                    token_usage,
                    retried_count,
                    answer_state,
                )

            # Finalize — mark last chunk
            await self._finalize_task(
                context,
                event_queue,
                task_id,
                context_id,
                agent,
                current_response,
                history,
                token_usage,
                answer_state,
            )
            return current_response, token_usage

        except Exception as e:
            if isinstance(e, TaskCanceledException):
                raise
            from openai import APIError

            if isinstance(e, APIError):
                if (
                    hasattr(e, "code")
                    and e.code
                    in (
                        "model_max_prompt_tokens_exceeded",
                        "context_length_exceeded",
                    )
                    or "context window" in str(e)
                ) and retried_count[0] < 5:
                    from AgentCrew.modules.agents import LocalAgent as _LocalAgent
                    from AgentCrew.modules.llm.model_registry import ModelRegistry

                    if isinstance(agent, _LocalAgent):
                        max_token = ModelRegistry.get_model_limit(agent.get_model())
                        agent.input_tokens_usage = max_token
                        retried_count[0] += 1
                        return await self._process_task(
                            context,
                            event_queue,
                            task_id,
                            context_id,
                            agent,
                            history,
                            artifacts,
                            token_usage,
                            retried_count,
                            answer_state,
                        )
            raise

    async def _execute_tool_calls(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
    ) -> str:
        cancel_event = await self._get_cancel_event(task_id)
        parallel_buffer: list[dict[str, Any]] = []

        for i, tool_use in enumerate(tool_uses):
            if cancel_event.is_set():
                raise TaskCanceledException(f"Task {task_id} was canceled")

            tool_name = tool_use.get("name")
            if not tool_name:
                continue

            if is_sequential_tool(tool_name):
                if parallel_buffer:
                    await self._flush_parallel(
                        agent, task_id, context_id, parallel_buffer, history
                    )
                    parallel_buffer = []
                result = await self._execute_single_tool(
                    agent,
                    task_id,
                    context_id,
                    tool_use,
                    history,
                    event_queue,
                )
                if result == ToolCallResult.INPUT_REQUIRED:
                    remaining = tool_uses[i + 1 :]
                    await self.session_store.save_pending_tools(
                        task_id, tool_use, remaining
                    )
                    return ToolCallResult.INPUT_REQUIRED
            else:
                parallel_buffer.append(tool_use)

        if parallel_buffer:
            await self._flush_parallel(
                agent, task_id, context_id, parallel_buffer, history
            )

        return ToolCallResult.CONTINUE

    async def _execute_single_tool(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_use: dict[str, Any],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
    ) -> str:
        tool_name = tool_use["name"]
        if tool_name == "ask":
            return await self._handle_ask_tool(
                agent, task_id, context_id, tool_use, history, event_queue
            )

        try:
            tool_result = await agent.execute_tool_call(tool_use)
            tool_result_message = agent.format_message(
                MessageType.ToolResult,
                {"tool_use": tool_use, "tool_result": tool_result},
            )
            if tool_result_message:
                await self.session_store.append_history(context_id, tool_result_message)
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
                await self.session_store.append_history(context_id, cancelled_message)
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
                await self.session_store.append_history(context_id, error_message)
                history.append(error_message)
        return ToolCallResult.CONTINUE

    async def _flush_parallel(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> None:
        results = await execute_tools_in_parallel(tool_uses, agent.execute_tool_call)
        for r in results:
            msg = agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": r.tool_use,
                    "tool_result": r.result,
                    "is_error": r.is_error,
                },
            )
            if msg:
                await self.session_store.append_history(context_id, msg)
                history.append(msg)

    async def _handle_ask_tool(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_use: dict[str, Any],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
    ) -> str:
        from .adapters import create_ask_message

        questions = tool_use["input"].get("questions", [])
        if not questions or not isinstance(questions, list):
            questions = []

        ask_msg = create_ask_message(questions)
        await self.session_store.save_pending_tools(task_id, tool_use, [])

        ts = self._now_timestamp()
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

    async def _resume_from_input_required(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        history: list[dict[str, Any]],
        pending: dict,
    ) -> None:
        user_response = get_message_text(context.message) if context.message else ""

        ask_tool_use = pending["ask_tool_use"]
        remaining_tools = pending["remaining_tools"]

        tool_result_msg = self.agent.format_message(
            MessageType.ToolResult,
            {"tool_use": ask_tool_use, "tool_result": user_response},
        )
        if tool_result_msg:
            await self.session_store.append_history(context_id, tool_result_msg)
            history.append(tool_result_msg)

        # Create answer state that continues from where we left off
        answer_state = AnswerArtifactState(artifact_id=f"answer_{task_id}")
        answer_state.emitted = (
            True  # answer may have prior chunks, so next is append=true
        )

        if remaining_tools:
            for remaining_tool in remaining_tools:
                result = await self._execute_single_tool(
                    self.agent,
                    task_id,
                    context_id,
                    remaining_tool,
                    history,
                    event_queue,
                )
                if result == ToolCallResult.INPUT_REQUIRED:
                    return

        await self.session_store.clear_pending_tools(task_id)
        await self._run_agent_loop(
            context,
            event_queue,
            task_id,
            context_id,
            history,
            answer_state,
        )

    async def _finalize_task(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        agent: LocalAgent,
        current_response: str,
        history: list[dict[str, Any]],
        token_usage: TokenUsage | None = None,
        answer_state: AnswerArtifactState | None = None,
    ) -> None:
        if token_usage is None:
            token_usage = TokenUsage()
        session_cost = agent.calculate_usage_cost(
            token_usage.input_tokens,
            token_usage.output_tokens,
            token_usage.cached_tokens,
        )

        if current_response.strip():
            assistant_message = agent.format_message(
                MessageType.Assistant, {"message": current_response}
            )
            if assistant_message:
                await self.session_store.append_history(context_id, assistant_message)
                history.append(assistant_message)

            user_msg = agent._extract_last_user_message_for_memory(history)
            agent.store_memory_if_available(
                user_msg,
                history,
                current_response,
                session_id=context_id,
            )

        if answer_state and answer_state.emitted:
            # Mark last chunk
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    artifact=Artifact(
                        artifact_id=answer_state.artifact_id,
                        parts=[Part(text="")],
                    ),
                    append=True,
                    last_chunk=True,
                )
            )
        elif current_response.strip():
            # No streaming chunks — emit final artifact
            from .adapters import convert_agent_response_to_a2a_artifact

            final = convert_agent_response_to_a2a_artifact(
                current_response,
                artifact_id=f"artifact_{task_id}_final",
            )
            if final:
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        artifact=final,
                    )
                )

        # Completed status
        ts = self._now_timestamp()
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_COMPLETED,
                    timestamp=ts,
                ),
                metadata={
                    "input_tokens": token_usage.input_tokens,
                    "output_tokens": token_usage.output_tokens,
                    "cached_tokens": token_usage.cached_tokens,
                    "cache_creation_tokens": token_usage.cache_creation_tokens,
                    "total_input_tokens": token_usage.total_input_tokens,
                    "total_tokens": token_usage.total_tokens,
                    "cost": session_cost,
                },
            )
        )


class AnswerArtifactState:
    """Tracks answer artifact state across recursive _process_task calls.

    Attributes:
        artifact_id: Stable artifact ID for the answer.
        emitted: Whether at least one chunk has been emitted.
        accumulated_text: Full accumulated text for deduplication purposes.
    """

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        self.emitted = False
        self.accumulated_text = ""
        self._think_state: AnswerArtifactState | None = None
