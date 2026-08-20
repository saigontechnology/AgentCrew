from collections.abc import Callable
from typing import Any

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.tools.parallel_executor import (
    execute_tools_in_parallel,
    is_sequential_tool,
)


async def run_agent_loop(
    agent: LocalAgent,
    history: list[dict[str, Any]],
    *,
    tool_filter: Callable[[dict[str, Any]], bool] | None = None,
    prior_token_usage: TokenUsage | None = None,
    request_usage_callback: Callable[[TokenUsage], None] | None = None,
) -> tuple[str, TokenUsage]:
    """Run a full agent loop, returning the final response and merged usage.

    Args:
        agent: The agent to execute the loop for.
        history: The conversation history for the loop.
        tool_filter: Optional filter applied to tool calls before execution.
        prior_token_usage: Usage merged from earlier segments of the same loop.
        request_usage_callback: Optional hook invoked with each raw completed
            LLM request's TokenUsage before it is merged into the returned
            control-flow usage. Used by local-agent delegation to attribute
            consumption to the registered target agent.
    """
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

    async for (
        response_message,
        chunk_text,
        thinking_chunk,
    ) in agent.process_messages(history, callback=process_result):
        if response_message:
            current_response = response_message
        if thinking_chunk:
            think_text_chunk, signature = thinking_chunk
            if think_text_chunk:
                thinking_content += think_text_chunk
            if signature:
                thinking_signature += signature

    if not tool_uses:
        assistant_message = agent.format_message(
            MessageType.Assistant, {"message": current_response}
        )
        if assistant_message:
            history.append(assistant_message)

        user_message = agent.extract_last_user_message_for_memory(history)
        agent.store_memory_if_available(user_message, history, current_response)
        # Prevent agent loop exit with empty response
        if current_response.strip() == "":
            return await run_agent_loop(
                agent,
                history,
                tool_filter=tool_filter,
                prior_token_usage=token_usage,
                request_usage_callback=request_usage_callback,
            )
        return current_response, token_usage

    if tool_filter:
        filtered = [t for t in tool_uses if tool_filter(t)]
    else:
        filtered = tool_uses

    if not filtered:
        assistant_message = agent.format_message(
            MessageType.Assistant, {"message": current_response}
        )
        if assistant_message:
            history.append(assistant_message)

        user_message = agent.extract_last_user_message_for_memory(history)
        agent.store_memory_if_available(user_message, history, current_response)
        return current_response, token_usage

    thinking_data = (thinking_content, thinking_signature) if thinking_content else None

    assistant_message = agent.format_message(
        MessageType.Assistant,
        {"message": current_response, "thinking": thinking_data, "tool_uses": filtered},
    )
    if assistant_message:
        history.append(assistant_message)

    parallel_buffer: list[dict[str, Any]] = []

    for tool_use in filtered:
        # --- Validate each tool exactly once before dispatch --------------
        error_text = agent.validate_tool_use(tool_use)
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

    return await run_agent_loop(
        agent,
        history,
        tool_filter=tool_filter,
        prior_token_usage=token_usage,
        request_usage_callback=request_usage_callback,
    )
