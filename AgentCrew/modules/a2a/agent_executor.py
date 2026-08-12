"""AgentCrewA2AExecutor — SDK AgentExecutor adapter for AgentCrew.

Key design:
- Task-scoped cancellation (not shared _cancel_event).
- Proper artifact append/last_chunk semantics across recursive LLM/tool rounds.
- Stable artifact IDs per task with answer state passed through recursion.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
from AgentCrew.modules.llm.token_usage import TokenUsage

from .exceptions import TaskCanceledException
from .session_store import AgentCrewSessionStore, _owner_key

if TYPE_CHECKING:
    from AgentCrew.modules.utils.file_handler import FileHandler

# Re-export for compatibility with tests that import from this module
from .artifact_stream import AnswerArtifactState, _TurnChunkBuffer
from .tool_executor import ToolCallResult


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
        self._file_handler = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancel_lock = asyncio.Lock()
        self._attachment_processor = None
        self._tool_executor = None

    @property
    def _agent_namespace(self) -> str:
        """Agent namespace used to keep pending/task state isolated per agent."""
        name = getattr(self.agent, "name", "") or ""
        return name if isinstance(name, str) else ""

    def _get_attachment_processor(self):
        if self._attachment_processor is None:
            from .attachment_processor import AttachmentProcessor

            self._attachment_processor = AttachmentProcessor()
        return self._attachment_processor

    def _get_tool_executor(self):
        if self._tool_executor is None:
            from .tool_executor import ToolExecutor

            self._tool_executor = ToolExecutor(
                session_store=self.session_store,
                agent_namespace=self._agent_namespace,
                now_timestamp_factory=self._now_timestamp,
            )
        return self._tool_executor

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
        owner = _owner_key(context.call_context)

        cancel_event = await self._get_cancel_event(task_id)
        cancel_event.clear()

        try:
            history, pending = await asyncio.gather(
                self.session_store.get_history(context_id, owner),
                self.session_store.get_pending_tools(
                    task_id, owner, self._agent_namespace
                ),
            )

            if pending:
                await self._resume_from_input_required(
                    context, event_queue, task_id, context_id, history, pending
                )
                return

            # New message
            user_message = self._convert_message_to_agent(context)
            if user_message:
                user_message = await self._process_attachments(user_message)
                history.append(user_message)
                await self.session_store.append_history(context_id, user_message, owner)

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

    def _get_file_handler(self) -> FileHandler:
        if self._file_handler is None:
            from AgentCrew.modules.utils.file_handler import FileHandler

            self._file_handler = FileHandler()
        return self._file_handler

    def _build_answer_state(
        self,
        context: RequestContext,
        task_id: str,
    ) -> AnswerArtifactState:
        """Build AnswerArtifactState from persisted task snapshot.

        Checks context.current_task.artifacts to find the latest answer
        artifact across turns (turn 0 uses ``answer_{task_id}``, later turns
        use ``answer_{task_id}_turn_{n}``). Sets emitted=True only when an
        artifact is found; otherwise leaves it False so the next chunk
        creates it.
        """
        artifact_id = f"answer_{task_id}"
        state = AnswerArtifactState(artifact_id=artifact_id)

        task = context.current_task
        if task is None:
            return state

        turn_prefix = f"answer_{task_id}_turn_"
        latest_artifact: Artifact | None = None
        latest_turn = -1

        for artifact in task.artifacts:
            artifact_turn = 0
            if artifact.artifact_id == artifact_id:
                artifact_turn = 0
            elif artifact.artifact_id.startswith(turn_prefix):
                suffix = artifact.artifact_id[len(turn_prefix) :]
                if not suffix.isdigit():
                    continue
                artifact_turn = int(suffix)
            else:
                continue
            if artifact_turn > latest_turn:
                latest_turn = artifact_turn
                latest_artifact = artifact

        if latest_artifact is not None:
            state.artifact_id = latest_artifact.artifact_id
            state.turn = latest_turn
            state.emitted = True
            state.accumulated_text = "".join(
                part.text for part in latest_artifact.parts if part.HasField("text")
            )

        return state

    def _convert_message_to_agent(
        self, context: RequestContext
    ) -> dict[str, Any] | None:
        from .adapters import convert_a2a_message_to_agent

        msg = context.message
        if not msg:
            return None
        return convert_a2a_message_to_agent(msg)

    async def _process_attachments(
        self, user_message: dict[str, Any]
    ) -> dict[str, Any]:
        """Process any file or file_uri content items through FileHandler.

        Replaces inline file payloads and file URIs with their FileHandler-processed
        equivalents (e.g. Docling markdown, optimized images). Text-only messages pass
        through unchanged. Processing failures preserve the original content item and
        emit a warning, consistent with existing FileHandler contract.
        """
        processor = self._get_attachment_processor()
        return await processor.process_attachments(user_message)

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
        owner = _owner_key(context.call_context)
        current_response = ""
        tool_uses: list[dict[str, Any]] = []

        def process_result(_tool_uses, _token_usage):
            nonlocal tool_uses, token_usage
            tool_uses = _tool_uses
            if token_usage:
                token_usage = token_usage.merge(_token_usage)
            else:
                token_usage = _token_usage

        # Per-turn chunk accumulator: token chunks are buffered and flushed
        # every 100ms as one batch event, so the SDK task-store persistence
        # runs once per batch instead of once per token.
        buffer = _TurnChunkBuffer(event_queue, task_id, context_id, answer_state)
        flush_task = asyncio.create_task(buffer.flush_loop())
        working_emitted = False

        async def stop_flush_task(suppress_errors: bool = False) -> None:
            """Stop the turn's timer task and observe its outcome.

            Always awaits the task, even when already done, so a completed
            timer-flush exception is never silently discarded. On critical
            paths the only suppressed error is the CancelledError from our
            own cancel; other exceptions propagate so a failed background
            flush fails the request instead of leaving a false COMPLETED.
            """
            if not flush_task.done():
                flush_task.cancel()
            try:
                await flush_task
            except asyncio.CancelledError:
                return
            except Exception as e:
                if suppress_errors:
                    logger.warning(f"Timer flush task for task {task_id} failed: {e}")
                else:
                    raise

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

                # Working status — emitted once per turn, on the first chunk
                if chunk_text:
                    if not working_emitted:
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
                        working_emitted = True
                    buffer.add_answer(chunk_text)

                # Thinking chunks — separate artifact ID with append tracking
                if thinking_chunk:
                    think_text, _ = thinking_chunk
                    if think_text:
                        if not working_emitted:
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
                            working_emitted = True
                        think_state = getattr(answer_state, "_think_state", None)
                        if think_state is None:
                            if answer_state.turn > 0:
                                think_artifact_id = (
                                    f"thinking_{task_id}_turn_{answer_state.turn}"
                                )
                            else:
                                think_artifact_id = f"thinking_{task_id}"
                            think_state = AnswerArtifactState(
                                artifact_id=think_artifact_id
                            )
                            answer_state._think_state = think_state
                        buffer.add_thinking(think_state, think_text)

            if cancel_event.is_set():
                raise TaskCanceledException(
                    f"Task {task_id} was canceled after streaming"
                )

            # Deliver all buffered chunks and stop this turn's timer before
            # tool execution, the INPUT_REQUIRED return, the next-turn
            # recursion, or finalization.
            await buffer.flush()
            await stop_flush_task()

            if tool_uses:
                from .adapters import convert_agent_response_to_a2a_artifact

                tool_artifact_id = f"tool_{task_id}"
                if answer_state.turn > 0:
                    tool_artifact_id = f"tool_{task_id}_turn_{answer_state.turn}"
                tool_artifact = convert_agent_response_to_a2a_artifact(
                    "",
                    artifact_id=tool_artifact_id,
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
                        context_id, assistant_message, owner
                    )
                    history.append(assistant_message)

                result = await self._execute_tool_calls(
                    agent,
                    task_id,
                    context_id,
                    tool_uses,
                    history,
                    event_queue,
                    owner,
                )

                if result == ToolCallResult.INPUT_REQUIRED:
                    return "", token_usage

                # Recurse with a fresh state for the next turn so each LLM/tool
                # round streams into its own per-turn artifact
                next_turn = answer_state.turn + 1
                next_answer_state = AnswerArtifactState(
                    artifact_id=f"answer_{task_id}_turn_{next_turn}",
                    turn=next_turn,
                )
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
                    next_answer_state,
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

            if (
                isinstance(e, APIError)
                and (
                    hasattr(e, "code")
                    and e.code
                    in (
                        "model_max_prompt_tokens_exceeded",
                        "context_length_exceeded",
                    )
                    or "context window" in str(e)
                )
                and retried_count[0] < 5
            ):
                from AgentCrew.modules.agents import LocalAgent as _LocalAgent
                from AgentCrew.modules.llm.model_registry import ModelRegistry

                if isinstance(agent, _LocalAgent):
                    max_token = ModelRegistry.get_model_limit(agent.get_model())
                    agent.input_tokens_usage = max_token
                    retried_count[0] += 1
                    # Flush and stop this turn's timer before the retry so the
                    # already-generated text stays consistent and the retry
                    # starts with a clean buffer and no overlapping timers.
                    await buffer.flush()
                    await stop_flush_task()
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
        finally:
            await stop_flush_task(suppress_errors=True)

    async def _execute_tool_calls(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
        owner: str,
    ) -> str:
        """Execute tool calls - compatibility wrapper for tests."""
        tool_executor = self._get_tool_executor()
        return await tool_executor.execute_tool_calls(
            agent,
            task_id,
            context_id,
            tool_uses,
            history,
            event_queue,
            owner,
            self._get_cancel_event,
        )

    async def _execute_single_tool(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_use: dict[str, Any],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
        owner: str,
    ) -> str:
        """Execute a single tool - compatibility wrapper for tests."""
        # For compatibility with tests that monkeypatch _handle_ask_tool,
        # call it directly when the tool is 'ask' and the method has been patched.
        if tool_use.get("name") == "ask":
            # Check if _handle_ask_tool is a method (not the original implementation)
            # by seeing if it's different from the class method
            original_method = type(self).__dict__.get("_handle_ask_tool")
            instance_method = self.__dict__.get("_handle_ask_tool")
            if instance_method is not None and instance_method is not original_method:
                # Method has been patched (e.g., in tests)
                # First validate the tool use
                error_text = agent.validate_tool_use(tool_use)
                if error_text is not None:
                    # Validation failed - record error and return CONTINUE
                    error_message = agent.format_message(
                        MessageType.ToolResult,
                        {
                            "tool_use": tool_use,
                            "tool_result": error_text,
                            "is_error": True,
                        },
                    )
                    if error_message:
                        await self.session_store.append_history(
                            context_id, error_message, owner
                        )
                        history.append(error_message)
                    return ToolCallResult.CONTINUE
                return await instance_method(
                    agent, task_id, context_id, tool_use, history, event_queue, owner
                )

        tool_executor = self._get_tool_executor()
        return await tool_executor.execute_single_tool(
            agent,
            task_id,
            context_id,
            tool_use,
            history,
            event_queue,
            owner,
            self._get_cancel_event,
        )

    async def _flush_parallel(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
        owner: str,
    ) -> None:
        """Flush parallel tools - compatibility wrapper for tests."""
        tool_executor = self._get_tool_executor()
        await tool_executor.flush_parallel(
            agent,
            task_id,
            context_id,
            tool_uses,
            history,
            owner,
        )

    async def _handle_ask_tool(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_use: dict[str, Any],
        history: list[dict[str, Any]],
        event_queue: EventQueue,
        owner: str,
    ) -> str:
        """Handle ask tool - compatibility wrapper for tests."""
        tool_executor = self._get_tool_executor()
        return await tool_executor.handle_ask_tool(
            agent,
            task_id,
            context_id,
            tool_use,
            history,
            event_queue,
            owner,
        )

    async def _resume_from_input_required(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        history: list[dict[str, Any]],
        pending: dict,
    ) -> None:
        owner = _owner_key(context.call_context)
        user_response = get_message_text(context.message) if context.message else ""

        ask_tool_use = pending["ask_tool_use"]
        remaining_tools = pending["remaining_tools"]

        tool_result_msg = self.agent.format_message(
            MessageType.ToolResult,
            {"tool_use": ask_tool_use, "tool_result": user_response},
        )
        if tool_result_msg:
            await self.session_store.append_history(context_id, tool_result_msg, owner)
            history.append(tool_result_msg)

        answer_state = self._build_answer_state(context, task_id)

        if remaining_tools:
            for remaining_tool in remaining_tools:
                result = await self._execute_single_tool(
                    self.agent,
                    task_id,
                    context_id,
                    remaining_tool,
                    history,
                    event_queue,
                    owner,
                )
                if result == ToolCallResult.INPUT_REQUIRED:
                    return

        await self.session_store.clear_pending_tools(
            task_id, owner, self._agent_namespace
        )
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
        owner = _owner_key(context.call_context)
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
                await self.session_store.append_history(
                    context_id, assistant_message, owner
                )
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
