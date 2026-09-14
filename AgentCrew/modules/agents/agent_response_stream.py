"""Stream-owned lifecycle for agent message processing.

``AgentResponseStream`` is the object returned by ``agent.process_messages(...)``.
It owns the request-local provider stream state so generic callers never
create or thread provider state themselves. Callers:

- iterate the stream with ``async for``;
- close it on cancellation with ``await stream.aclose()``;
- finalize assistant messages with ``stream.format_assistant_message(...)``,
  which delegates to the agent's message formatter and attaches any captured
  provider continuation state.

Each stream instance owns exactly one private state container, created lazily
on first iteration. State is never stored on the agent or LLM service, so
concurrent streams on the same agent stay isolated.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.message_metadata import attach_stream_metadata

if TYPE_CHECKING:
    from AgentCrew.modules.agents.base import BaseAgent


class AgentResponseStream:
    """Async iterator over agent stream items with owned provider state."""

    def __init__(
        self,
        agent: BaseAgent,
        state_factory: Callable[[], dict[str, Any] | None],
        iterator_factory: Callable[[dict[str, Any] | None], Any],
    ) -> None:
        self._agent = agent
        self._state_factory = state_factory
        self._iterator_factory = iterator_factory
        self._stream_state: dict[str, Any] | None = None
        self._iterator: Any = None
        self._closed = False

    async def _start(self) -> None:
        """Create the provider stream state and iterator exactly once."""
        if self._iterator is None:
            self._stream_state = self._state_factory()
            self._iterator = self._iterator_factory(self._stream_state)

    def __aiter__(self) -> AgentResponseStream:
        return self

    async def __anext__(self) -> tuple[str, str | None, tuple | None]:
        if self._closed:
            raise StopAsyncIteration
        await self._start()
        try:
            return await self._iterator.__anext__()
        except StopAsyncIteration:
            self._closed = True
            raise

    async def aclose(self) -> None:
        """Close the stream, safe before first iteration and after completion."""
        if self._closed:
            return
        self._closed = True
        if self._iterator is not None:
            aclose = getattr(self._iterator, "aclose", None)
            if aclose is not None:
                await aclose()

    def format_assistant_message(
        self,
        response: str,
        thinking: tuple[str, str | None] | None = None,
        tool_uses: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Format an assistant message and attach captured stream metadata.

        Delegates to the agent's message formatter and attaches any provider
        continuation state captured during iteration. No-op for streams
        without captured state (for example remote agents).
        """
        data: dict[str, Any] = {"message": response}
        if thinking is not None:
            data["thinking"] = thinking
        if tool_uses is not None:
            data["tool_uses"] = tool_uses
        message = self._agent.format_message(MessageType.Assistant, data)
        if message is not None:
            attach_stream_metadata(message, self._stream_state)
        return message
