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
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.tools.parallel_executor import (
    ToolResult,
    execute_tools_in_parallel,
    is_sequential_tool,
)

from .exceptions import TaskCanceledException
from .session_store import AgentCrewSessionStore, _owner_key

if TYPE_CHECKING:
    from AgentCrew.modules.utils.file_handler import FileHandler


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
        self._file_handler = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancel_lock = asyncio.Lock()

    @property
    def _agent_namespace(self) -> str:
        """Agent namespace used to keep pending/task state isolated per agent."""
        name = getattr(self.agent, "name", "") or ""
        return name if isinstance(name, str) else ""

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

        Checks context.current_task.artifacts to determine if an answer
        artifact already exists. Sets emitted=True only when the artifact
        is found; otherwise leaves it False so the next chunk creates it.
        """
        artifact_id = f"answer_{task_id}"
        state = AnswerArtifactState(artifact_id=artifact_id)

        task = context.current_task
        if task is None:
            return state

        for artifact in task.artifacts:
            if artifact.artifact_id == artifact_id:
                state.emitted = True
                state.accumulated_text = "".join(
                    part.text for part in artifact.parts if part.HasField("text")
                )
                break

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

        content = user_message.get("content")
        if not isinstance(content, list) or not content:
            return user_message

        has_attachments = any(
            item.get("type") in ("file", "file_uri")
            for item in content
            if isinstance(item, dict)
        )
        if not has_attachments:
            return user_message

        processed_content: list[dict[str, Any]] = []

        for item in content:
            if not isinstance(item, dict):
                processed_content.append(item)
                continue

            item_type = item.get("type")

            if item_type == "file":
                result = await self._process_raw_file_item(item)
                if result is not None:
                    processed_content.append(result)
                else:
                    processed_content.append(item)

            elif item_type == "file_uri":
                result = await self._process_file_uri_item(item)
                if result is not None:
                    processed_content.append(result)
                else:
                    processed_content.append(item)

            else:
                processed_content.append(item)

        user_message["content"] = processed_content
        return user_message

    async def _process_raw_file_item(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Process a 'file' content item (raw bytes payload) through FileHandler.

        Writes bytes to a temporary file with an appropriate extension so FileHandler
        can validate and process it normally.
        """
        import os
        import tempfile
        from pathlib import Path

        from loguru import logger

        file_data = item.get("file_data")
        if not file_data:
            return None

        file_name = item.get("file_name") or "attachment"
        file_handler = self._get_file_handler()

        suffix = Path(file_name).suffix or ".bin"
        tmp_path: str | None = None

        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            result = await file_handler.async_process_file(tmp_path)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(
                f"Failed to process file attachment '{file_name}' via FileHandler: {e!s}"
            )
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return None

    async def _process_file_uri_item(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Process a 'file_uri' content item through FileHandler.

        Resolves the URI to a local file path:
        - file:// URIs are converted to local paths.
        - HTTP/HTTPS URIs are downloaded to a temporary file.
        - Bare paths are used as-is.
        """
        import os
        import tempfile
        from pathlib import Path
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        from loguru import logger

        uri = item.get("uri", "")
        if not uri:
            return None

        parsed = urlparse(uri)
        tmp_path: str | None = None
        should_cleanup = False
        file_handler = self._get_file_handler()

        try:
            if parsed.scheme in ("file", ""):
                local_path = (
                    url2pathname(unquote(parsed.path))
                    if parsed.scheme == "file"
                    else uri
                )
                result = await file_handler.async_process_file(local_path)
                return result

            if parsed.scheme in ("http", "https"):
                import httpx

                file_name = item.get("file_name") or "download"
                suffix = Path(file_name).suffix if Path(file_name).suffix else ".bin"

                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    should_cleanup = True

                import asyncio

                with httpx.Client(follow_redirects=True, timeout=30) as client:
                    resp = client.get(uri)
                    resp.raise_for_status()

                content_bytes = resp.content

                def _write_tmp(path: str, data: bytes) -> None:
                    with open(path, "wb") as f:
                        f.write(data)

                await asyncio.to_thread(_write_tmp, tmp_path, content_bytes)

                result = await file_handler.async_process_file(tmp_path)
                return result

            logger.warning(
                f"Unsupported URI scheme '{parsed.scheme}' for file URI: {uri}"
            )
        except Exception as e:
            logger.warning(f"Failed to process file URI '{uri}' via FileHandler: {e!s}")
        finally:
            if should_cleanup and tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return None

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
        owner: str,
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
                        agent, task_id, context_id, parallel_buffer, history, owner
                    )
                    parallel_buffer = []
                result = await self._execute_single_tool(
                    agent,
                    task_id,
                    context_id,
                    tool_use,
                    history,
                    event_queue,
                    owner,
                )
                if result == ToolCallResult.INPUT_REQUIRED:
                    remaining = tool_uses[i + 1 :]
                    await self.session_store.save_pending_tools(
                        task_id, tool_use, remaining, owner, self._agent_namespace
                    )
                    return ToolCallResult.INPUT_REQUIRED
            else:
                parallel_buffer.append(tool_use)

        if parallel_buffer:
            await self._flush_parallel(
                agent, task_id, context_id, parallel_buffer, history, owner
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
        owner: str,
    ) -> str:
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
                await self.session_store.append_history(
                    context_id, error_message, owner
                )
                history.append(error_message)
            return ToolCallResult.CONTINUE

        if tool_name == "ask":
            return await self._handle_ask_tool(
                agent, task_id, context_id, tool_use, history, event_queue, owner
            )

        try:
            tool_result = await agent.execute_tool_call(tool_use)
            tool_result_message = agent.format_message(
                MessageType.ToolResult,
                {"tool_use": tool_use, "tool_result": tool_result},
            )
            if tool_result_message:
                await self.session_store.append_history(
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
                await self.session_store.append_history(
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
                await self.session_store.append_history(
                    context_id, error_message, owner
                )
                history.append(error_message)
        return ToolCallResult.CONTINUE

    async def _flush_parallel(
        self,
        agent: LocalAgent,
        task_id: str,
        context_id: str,
        tool_uses: list[dict[str, Any]],
        history: list[dict[str, Any]],
        owner: str,
    ) -> None:
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
                await self.session_store.append_history(context_id, msg, owner)
                history.append(msg)

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
        from .adapters import create_ask_message

        questions = tool_use["input"].get("questions", [])
        if not questions or not isinstance(questions, list):
            questions = []

        ask_msg = create_ask_message(questions)
        await self.session_store.save_pending_tools(
            task_id, tool_use, [], owner, self._agent_namespace
        )

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
