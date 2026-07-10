from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from acp import Client

from AgentCrew.modules.acp.tools.context import classify_tool_kind


class ClientCommunication:
    def __init__(self):
        self._conn: Client | None = None

    @property
    def conn(self) -> Client | None:
        return self._conn

    @conn.setter
    def conn(self, value: Client | None):
        self._conn = value

    async def send_agent_message(self, session_id: str, text: str):
        from acp import update_agent_message_text

        if self._conn is not None:
            await self._conn.session_update(session_id, update_agent_message_text(text))

    async def send_thought_chunk(self, session_id: str, text: str):
        from acp import text_block
        from acp.schema import AgentThoughtChunk

        if self._conn is not None and text.strip():
            await self._conn.session_update(
                session_id,
                AgentThoughtChunk(
                    session_update="agent_thought_chunk",
                    content=text_block(text),
                ),
            )

    async def send_ask_request(
        self,
        session_id: str,
        questions: list[dict[str, Any]],
    ):
        """Send one or more questions to the client.

        Args:
            session_id: The ACP session ID
            questions: list of {question, guided_answers} dicts
        """
        total = len(questions)
        lines = [f"Agent has {total} question{'s' if total > 1 else ''}:"]
        for i, q in enumerate(questions):
            q_text = q.get("question", "")
            answers = q.get("guided_answers", [])
            lines.append("")
            lines.append(f"{i + 1}. {q_text}")
            if answers:
                for j, ans in enumerate(answers, 1):
                    lines.append(f"   {j}) {ans}")
        lines.extend(["", "Reply with answers in the format:"])
        for i in range(total):
            lines.append(f"q{i}: <your answer for question {i + 1}>")
        lines.append("Answer all questions in one response.")
        await self.send_agent_message(session_id, "\n".join(lines))

    async def send_current_mode_update(self, session_id: str, state: Any):
        from acp.schema import CurrentModeUpdate

        if self._conn is not None:
            await self._conn.session_update(
                session_id,
                CurrentModeUpdate(
                    current_mode_id=state.agent_name,
                    session_update="current_mode_update",
                ),
            )

    async def send_session_info_update(self, session_id: str, state: Any):
        from acp.schema import SessionInfoUpdate

        if self._conn is not None:
            await self._conn.session_update(
                session_id,
                SessionInfoUpdate(
                    title=state.title,
                    session_update="session_info_update",
                ),
            )

    async def send_tool_started(self, session_id: str, tool_use: dict[str, Any]):
        from acp import start_tool_call

        if self._conn is not None:
            await self._conn.session_update(
                session_id,
                start_tool_call(
                    tool_use["id"],
                    self.tool_title(tool_use),
                    kind=classify_tool_kind(tool_use.get("name", "")),
                    status="in_progress",
                    raw_input=tool_use.get("input", {}),
                ),
            )

    async def send_tool_completed(
        self,
        session_id: str,
        tool_use: dict[str, Any],
        tool_result: Any,
        is_error: bool,
    ):
        from acp import text_block, tool_content
        from acp.schema import ToolCallProgress

        if self._conn is not None:
            await self._conn.session_update(
                session_id,
                ToolCallProgress(
                    tool_call_id=tool_use["id"],
                    session_update="tool_call_update",
                    status="failed" if is_error else "completed",
                    content=[tool_content(text_block(str(tool_result)))],
                    raw_output=tool_result,
                ),
            )

    async def send_usage_update(
        self,
        session_id: str,
        token_usage: Any,
        session_cost: float,
        context_size: int,
        context_used: int,
    ):
        from acp.schema import Cost, UsageUpdate

        if self._conn is not None:
            await self._conn.session_update(
                session_id,
                UsageUpdate(
                    session_update="usage_update",
                    cost=Cost(amount=session_cost, currency="USD"),
                    size=context_size,
                    used=context_used,
                    field_meta={
                        "input_tokens": token_usage.input_tokens,
                        "output_tokens": token_usage.output_tokens,
                        "cached_tokens": token_usage.cached_tokens,
                        "cache_creation_tokens": token_usage.cache_creation_tokens,
                        "total_input_tokens": token_usage.total_input_tokens,
                    },
                ),
            )

    @staticmethod
    def tool_title(tool_use: dict[str, Any]) -> str:
        return f"{tool_use.get('name', 'tool')}"
