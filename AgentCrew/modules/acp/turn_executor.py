from __future__ import annotations

from typing import TYPE_CHECKING, Any

from AgentCrew.modules.acp.session_state import AcpSessionState
from AgentCrew.modules.acp.tools.permission_broker import AcpPermissionBroker
from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.tools.parallel_executor import (
    execute_tools_in_parallel,
    is_sequential_tool,
)

if TYPE_CHECKING:
    from acp import Client

    from AgentCrew.modules.acp.client_communication import ClientCommunication
    from AgentCrew.modules.acp.tool_manager import AcpToolManager
    from AgentCrew.modules.agents import LocalAgent


class TurnExecutor:
    def __init__(
        self,
        client_comm: ClientCommunication,
        tool_manager: AcpToolManager,
    ):
        self._client_comm = client_comm
        self._tool_manager = tool_manager

    async def run_turn(self, session_id: str, state: AcpSessionState, conn: Any):
        if state.permission_broker is None and conn is not None:
            state.permission_broker = AcpPermissionBroker(
                conn=conn,
                session_id=session_id,
            )
        agent = await self._get_agent(state.agent_name)
        await self._tool_manager.ensure_tools_for_session(session_id, state)

        retried_count = 0
        await self._run_turn_with_retry(session_id, state, conn, agent, retried_count)
        await self._send_usage_update(session_id, state, agent)

    async def _run_turn_with_retry(
        self,
        session_id: str,
        state: AcpSessionState,
        conn: Any,
        agent: LocalAgent,
        retried_count: int,
    ):
        current_response = ""
        thinking_content = ""
        thinking_signature = ""
        tool_uses: list[dict[str, Any]] = []
        token_usage = None

        def process_result(_tool_uses, _token_usage):
            nonlocal tool_uses, token_usage
            tool_uses = _tool_uses
            if _token_usage:
                if token_usage:
                    token_usage = token_usage.merge(_token_usage)
                else:
                    token_usage = _token_usage

        try:
            async for (
                response_message,
                chunk_text,
                thinking_chunk,
            ) in agent.process_messages(
                state.history,
                callback=process_result,
            ):
                if state.cancelled:
                    return
                if response_message:
                    current_response = response_message
                if chunk_text:
                    await self._client_comm.send_agent_message(session_id, chunk_text)
                if thinking_chunk:
                    think_text_chunk, signature = thinking_chunk
                    if think_text_chunk:
                        thinking_content += think_text_chunk
                        await self._client_comm.send_thought_chunk(
                            session_id, think_text_chunk
                        )
                    if signature:
                        thinking_signature += signature

            thinking_data = (
                (thinking_content, thinking_signature) if thinking_content else None
            )

            assistant_message = agent.format_message(
                MessageType.Assistant,
                {
                    "message": current_response,
                    "thinking": thinking_data,
                    "tool_uses": tool_uses,
                },
            )
            if assistant_message:
                state.history.append(assistant_message)

            if token_usage:
                state.token_usage = state.token_usage.merge(token_usage)

            if tool_uses:
                await self.execute_tools(session_id, state, agent, tool_uses)
                if not state.cancelled and state.pending_ask_tool is None:
                    await self.run_turn(session_id, state, conn)
                return

            user_message = agent._extract_last_user_message_for_memory(state.history)
            agent.store_memory_if_available(
                user_message,
                state.history,
                current_response,
                session_id=session_id,
            )
        except Exception as e:
            from openai import APIError

            if isinstance(e, APIError):
                if (
                    e.code == "model_max_prompt_tokens_exceeded"
                    or e.code == "context_length_exceeded"
                    or e.message.find("This endpoint's maximum context length is") >= 0
                    or e.message.find(
                        "Your input exceeds the context window of this model."
                    )
                    >= 0
                ) and retried_count < 5:
                    from AgentCrew.modules.agents import LocalAgent as _LocalAgent
                    from AgentCrew.modules.llm.model_registry import ModelRegistry

                    if isinstance(agent, _LocalAgent):
                        max_token = ModelRegistry.get_model_limit(agent.get_model())
                        agent.input_tokens_usage = max_token
                        retried_count += 1
                        return await self._run_turn_with_retry(
                            session_id, state, conn, agent, retried_count
                        )
                elif (
                    isinstance(e, APIError)
                    and str(e) == "InternalServerError"
                    and retried_count < 5
                ):
                    retried_count += 1
                    return await self._run_turn_with_retry(
                        session_id, state, conn, agent, retried_count
                    )

            raise

    async def _send_usage_update(
        self,
        session_id: str,
        state: AcpSessionState,
        agent: LocalAgent,
    ):
        from AgentCrew.modules.llm.model_registry import ModelRegistry

        try:
            context_size = ModelRegistry.get_model_limit(agent.get_model())
        except Exception:
            context_size = 0
        context_used = state.token_usage.total_input_tokens
        session_cost = agent.calculate_usage_cost(
            state.token_usage.input_tokens,
            state.token_usage.output_tokens,
            state.token_usage.cached_tokens,
        )
        await self._client_comm.send_usage_update(
            session_id,
            state.token_usage,
            session_cost,
            context_size,
            context_used,
        )

    async def execute_tools(
        self,
        session_id: str,
        state: AcpSessionState,
        agent: LocalAgent,
        tool_uses: list[dict[str, Any]],
    ):
        parallel_buffer: list[dict[str, Any]] = []
        started_tool_ids: set[str] = set()

        async def send_tool_started_once(tool_use: dict[str, Any]):
            tool_id = tool_use.get("id", "")
            if tool_id in started_tool_ids:
                return
            started_tool_ids.add(tool_id)
            await self._client_comm.send_tool_started(session_id, tool_use)

        async def flush_parallel():
            nonlocal parallel_buffer
            if not parallel_buffer:
                return
            for tool_use in parallel_buffer:
                await send_tool_started_once(tool_use)
            results = await execute_tools_in_parallel(
                parallel_buffer, agent.execute_tool_call
            )
            for result in results:
                await self.append_tool_result(
                    session_id,
                    state,
                    agent,
                    result.tool_use,
                    result.result,
                    result.is_error,
                )
            parallel_buffer = []

        for tool_use in tool_uses:
            if state.cancelled:
                return
            if is_sequential_tool(tool_use["name"]):
                await flush_parallel()
                await send_tool_started_once(tool_use)
                if tool_use.get("name") == "ask":
                    await self.handle_ask_tool(session_id, state, agent, tool_use)
                    return
                if state.permission_broker:
                    permission_outcome = (
                        await state.permission_broker.request_permission(tool_use)
                    )
                    if permission_outcome == "reject":
                        await self.append_tool_result(
                            session_id,
                            state,
                            agent,
                            tool_use,
                            "Tool execution rejected by user.",
                            is_error=True,
                            is_rejected=True,
                        )
                        continue
                try:
                    tool_result = await agent.execute_tool_call(tool_use)
                    await self.append_tool_result(
                        session_id, state, agent, tool_use, tool_result
                    )
                except CancelOperation:
                    await self.append_tool_result(
                        session_id,
                        state,
                        agent,
                        tool_use,
                        "Tool execution cancelled by a hook",
                        True,
                        True,
                    )
                except Exception as e:
                    await self.append_tool_result(
                        session_id, state, agent, tool_use, str(e), True
                    )
            else:
                permission_result = "allow_once"
                if state.permission_broker:
                    await send_tool_started_once(tool_use)
                    permission_result = (
                        await state.permission_broker.request_permission(tool_use)
                    )
                if permission_result == "reject":
                    await self.append_tool_result(
                        session_id,
                        state,
                        agent,
                        tool_use,
                        "Tool execution rejected by user.",
                        is_error=True,
                        is_rejected=True,
                    )
                else:
                    parallel_buffer.append(tool_use)

        await flush_parallel()

    async def handle_ask_tool(
        self,
        session_id: str,
        state: AcpSessionState,
        agent: LocalAgent,
        tool_use: dict[str, Any],
    ):
        tool_input = tool_use.get("input", {})
        questions = tool_input.get("questions", [])
        if not questions or not isinstance(questions, list):
            await self.append_tool_result(
                session_id,
                state,
                agent,
                tool_use,
                "Ask tool requires a non-empty `questions` array.",
                is_error=True,
            )
            return

        # Validate each question
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                await self.append_tool_result(
                    session_id,
                    state,
                    agent,
                    tool_use,
                    f"Question at index {i} must be an object.",
                    is_error=True,
                )
                return
            q_text = str(q.get("question", "")).strip()
            if not q_text:
                await self.append_tool_result(
                    session_id,
                    state,
                    agent,
                    tool_use,
                    f"Question at index {i} has no question text.",
                    is_error=True,
                )
                return
            answers = q.get("guided_answers", [])
            if not isinstance(answers, list):
                await self.append_tool_result(
                    session_id,
                    state,
                    agent,
                    tool_use,
                    f"Question at index {i} guided_answers must be a list.",
                    is_error=True,
                )
                return

        await self._client_comm.send_ask_request(session_id, questions)
        state.pending_ask_tool = {
            "tool_use": tool_use,
            "questions": questions,
        }

    async def resume_pending_ask(
        self,
        session_id: str,
        state: AcpSessionState,
        answer: str,
    ) -> bool:
        pending_ask = state.pending_ask_tool
        if pending_ask is None:
            return False
        tool_use = pending_ask.get("tool_use")
        if not isinstance(tool_use, dict):
            state.pending_ask_tool = None
            return False
        questions = pending_ask.get("questions", [])
        agent = await self._get_agent(state.agent_name)

        # Parse multi-answer response. Expected format:
        # q0: <answer1>
        # q1: <answer2>
        import re as _re

        parsed: dict[str, str] = {}
        for line in answer.split("\n"):
            line = line.strip()
            m = _re.match(r"^q(\d+):\s*(.*)", line)
            if m:
                parsed[m.group(1)] = m.group(2)

        # Build structured result
        answer_lines = []
        for i, q in enumerate(questions):
            idx_str = str(i)
            ans = parsed.get(idx_str, "(skipped)")
            answer_lines.append(f"- {q.get('question', '?')}: {ans}")

        result = "Answers:\n" + "\n".join(answer_lines)

        await self.append_tool_result(
            session_id,
            state,
            agent,
            tool_use,
            result,
        )
        state.pending_ask_tool = None
        return True

    async def append_tool_result(
        self,
        session_id: str,
        state: AcpSessionState,
        agent: LocalAgent,
        tool_use: dict[str, Any],
        tool_result: Any,
        is_error: bool = False,
        is_rejected: bool = False,
    ):
        result_message = agent.format_message(
            MessageType.ToolResult,
            {
                "tool_use": tool_use,
                "tool_result": tool_result,
                "is_error": is_error,
                "is_rejected": is_rejected,
            },
        )
        if result_message:
            state.history.append(result_message)
        await self._client_comm.send_tool_completed(
            session_id, tool_use, tool_result, is_error
        )

    async def release_active_terminals(
        self, session_id: str, state: AcpSessionState, conn: Client
    ):
        from AgentCrew.modules.acp.tools.terminal import (
            _kill_terminal,
            _release_terminal,
        )

        from .tools.context import AcpSessionContext

        for terminal_id in list(state.tool_state.acp_active_terminals.values()):
            ctx = AcpSessionContext(
                conn=conn,
                session_id=session_id,
                client_capabilities=self._tool_manager.client_capabilities,
                active_terminals=state.tool_state.acp_active_terminals,
            )
            await _release_terminal(ctx, terminal_id)
            await _kill_terminal(ctx, terminal_id)
        state.tool_state.acp_active_terminals.clear()

    async def _get_agent(self, agent_name: str) -> Any:
        from AgentCrew.modules.agents import AgentManager

        agent_manager = AgentManager.get_instance()
        agent = agent_manager.get_local_agent(agent_name)
        if agent is None:
            raise ValueError(f"Agent '{agent_name}' not found")
        if not agent.is_active:
            await agent.activate_async()
        return agent
