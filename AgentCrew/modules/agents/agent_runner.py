import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from loguru import logger

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.tool_loop_guard import (
    ToolLoopGuard,
)
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.tools.parallel_executor import (
    execute_tools_in_parallel,
    is_sequential_tool,
)

ProgressKind = Literal["thinking", "response", "tool"]
ProgressCallback = Callable[[ProgressKind, str], Awaitable[None]]

STREAM_CLOSE_TIMEOUT_SECONDS = 0.5
"""Maximum time allowed for closing a delegate's response stream."""

FIRST_RESPONSE_TIMEOUT_SECONDS = 300
MAX_EMPTY_RESPONSE_RETRIES = 5


async def _close_stream(stream, primary_error: BaseException | None) -> None:
    close_task = asyncio.create_task(stream.aclose())

    def report_late_failure(task: asyncio.Task) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.warning("Delegate stream close failed after timeout: {}", error)

    try:
        done, _ = await asyncio.wait({close_task}, timeout=STREAM_CLOSE_TIMEOUT_SECONDS)
        if not done:
            raise TimeoutError(
                f"Delegate stream close exceeded {STREAM_CLOSE_TIMEOUT_SECONDS}s"
            )
        await close_task
    except BaseException as error:
        if not close_task.done():
            close_task.add_done_callback(report_late_failure)
            close_task.cancel()
        if primary_error is None:
            raise
        logger.warning(
            "Delegate stream close failed during {}: {}",
            type(primary_error).__name__,
            error,
        )


async def _run_agent_loop(
    agent: LocalAgent,
    history: list[dict[str, Any]],
    *,
    prior_token_usage: TokenUsage | None = None,
    request_usage_callback: Callable[[TokenUsage], None] | None = None,
    progress_callback: ProgressCallback | None = None,
    first_response_timeout: float = FIRST_RESPONSE_TIMEOUT_SECONDS,
    empty_response_retries: int = 0,
    tool_loop_guard: ToolLoopGuard | None = None,
) -> tuple[str, TokenUsage]:
    """Run a full agent loop, returning the final response and merged usage.

    Args:
        agent: The agent to execute the loop for.
        history: The conversation history for the loop.
        prior_token_usage: Usage merged from earlier segments of the same loop.
        request_usage_callback: Optional hook invoked with each raw completed
            LLM request's TokenUsage before it is merged into the returned
            control-flow usage. Used by local-agent delegation to attribute
            consumption to the registered target agent.
        progress_callback: Optional async callback for incremental delegate output.
        first_response_timeout: Seconds to wait for the first item of each stream.
        empty_response_retries: Consecutive empty responses already retried.
    """
    tool_loop_guard = tool_loop_guard or ToolLoopGuard()
    current_response = ""
    thinking_content = ""
    thinking_signature = ""
    tool_uses: list[dict[str, Any]] = []

    token_usage = prior_token_usage or TokenUsage()

    def process_result(_tool_uses, _token_usage):
        nonlocal tool_uses, token_usage
        tool_uses = _tool_uses
        token_usage = token_usage.merge(_token_usage)
        if request_usage_callback is not None:
            request_usage_callback(_token_usage)

    tools_enabled = tool_loop_guard.start_request()
    stream = agent.process_messages(
        history, callback=process_result, tools_enabled=tools_enabled
    )

    try:
        iterator = aiter(stream)
        try:
            first_item = await asyncio.wait_for(
                anext(iterator), timeout=first_response_timeout
            )
        except TimeoutError as error:
            raise TimeoutError(
                f"Agent response stream yielded no first item within {first_response_timeout}s"
            ) from error
        except StopAsyncIteration:
            first_item = None

        while first_item is not None:
            response_message, chunk_text, thinking_chunk = first_item
            if response_message:
                current_response = response_message
            if chunk_text and progress_callback is not None:
                await progress_callback("response", chunk_text)
            if thinking_chunk:
                think_text_chunk, signature = thinking_chunk
                if think_text_chunk:
                    thinking_content += think_text_chunk
                    if progress_callback is not None:
                        await progress_callback("thinking", think_text_chunk)
                if signature:
                    thinking_signature += signature
            try:
                first_item = await anext(iterator)
            except StopAsyncIteration:
                break
    finally:
        await _close_stream(stream, sys.exc_info()[1])

    tool_loop_guard.validate_response(current_response, tool_uses)
    thinking_data = (thinking_content, thinking_signature) if thinking_content else None

    def append_assistant_message(assistant_message: dict[str, Any] | None) -> None:
        """Append a finalized assistant message to history."""
        if assistant_message:
            history.append(assistant_message)

    if not tool_uses:
        assistant_message = stream.format_assistant_message(
            current_response, thinking=thinking_data
        )
        append_assistant_message(assistant_message)

        user_message = agent.extract_last_user_message_for_memory(history)
        agent.store_memory_if_available(user_message, history, current_response)
        if current_response.strip() == "":
            if empty_response_retries >= MAX_EMPTY_RESPONSE_RETRIES:
                raise RuntimeError(
                    f"Agent returned an empty response after {MAX_EMPTY_RESPONSE_RETRIES} retries"
                )
            return await _run_agent_loop(
                agent,
                history,
                prior_token_usage=token_usage,
                request_usage_callback=request_usage_callback,
                progress_callback=progress_callback,
                first_response_timeout=first_response_timeout,
                empty_response_retries=empty_response_retries + 1,
                tool_loop_guard=tool_loop_guard,
            )
        return current_response, token_usage

    tool_uses, tool_round = tool_loop_guard.check_round(tool_uses).omit_blocked_calls(
        tool_uses
    )
    assistant_message = stream.format_assistant_message(
        current_response, thinking=thinking_data, tool_uses=tool_uses
    )
    tool_round.mark_assistant_message(assistant_message)
    append_assistant_message(assistant_message)

    parallel_buffer: list[dict[str, Any]] = []

    for index, tool_use in enumerate(tool_uses):
        if progress_callback is not None:
            await progress_callback("tool", tool_use["name"])
        # --- Validate each tool exactly once before dispatch --------------
        error_text = tool_round.errors[index] or agent.validate_tool_use(tool_use)
        if error_text is not None:
            # Flush buffered valid tools first to preserve order
            if parallel_buffer:
                results = await execute_tools_in_parallel(
                    parallel_buffer, agent.execute_tool_call
                )
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
                        history.append(msg)
                parallel_buffer = []
            error_msg = agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": error_text,
                    "is_error": True,
                },
            )
            if error_msg:
                history.append(error_msg)
            continue

        if is_sequential_tool(tool_use["name"]):
            if parallel_buffer:
                results = await execute_tools_in_parallel(
                    parallel_buffer, agent.execute_tool_call
                )
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
                        history.append(msg)
                parallel_buffer = []

            try:
                tool_result = await agent.execute_tool_call(tool_use)
            except CancelOperation:
                cancelled_msg = agent.format_message(
                    MessageType.ToolResult,
                    {
                        "tool_use": tool_use,
                        "tool_result": "Tool execution cancelled by a hook",
                        "is_error": True,
                        "is_rejected": True,
                    },
                )
                if cancelled_msg:
                    history.append(cancelled_msg)
                continue
            except Exception as e:
                tool_result = str(e)
                error_msg = agent.format_message(
                    MessageType.ToolResult,
                    {
                        "tool_use": tool_use,
                        "tool_result": tool_result,
                        "is_error": True,
                    },
                )
                if error_msg:
                    history.append(error_msg)
                continue

            result_msg = agent.format_message(
                MessageType.ToolResult,
                {"tool_use": tool_use, "tool_result": tool_result},
            )
            if result_msg:
                history.append(result_msg)
        else:
            parallel_buffer.append(tool_use)

    if parallel_buffer:
        results = await execute_tools_in_parallel(
            parallel_buffer, agent.execute_tool_call
        )
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
                history.append(msg)

    tool_round.raise_if_terminal()
    if recovery := tool_round.recovery_message(history):
        history.append(recovery)
    return await _run_agent_loop(
        agent,
        history,
        prior_token_usage=token_usage,
        request_usage_callback=request_usage_callback,
        progress_callback=progress_callback,
        first_response_timeout=first_response_timeout,
        tool_loop_guard=tool_loop_guard,
    )


async def run_agent_loop(
    agent: LocalAgent,
    history: list[dict[str, Any]],
    *,
    prior_token_usage: TokenUsage | None = None,
    request_usage_callback: Callable[[TokenUsage], None] | None = None,
    progress_callback: ProgressCallback | None = None,
    scope_id: str | None = None,
    first_response_timeout: float = FIRST_RESPONSE_TIMEOUT_SECONDS,
) -> tuple[str, TokenUsage]:
    from AgentCrew.modules.agents.tool_result_summary import (
        bind_inference_scope,
        reset_inference_scope,
    )

    token = bind_inference_scope(scope_id or f"job:{uuid.uuid4()}")
    try:
        return await _run_agent_loop(
            agent,
            history,
            prior_token_usage=prior_token_usage,
            request_usage_callback=request_usage_callback,
            progress_callback=progress_callback,
            first_response_timeout=first_response_timeout,
            tool_loop_guard=ToolLoopGuard(),
        )
    finally:
        reset_inference_scope(token)
