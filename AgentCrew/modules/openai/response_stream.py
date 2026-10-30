"""Terminal validation for Responses API event streams."""

from typing import Any


class ResponsesStreamGuard:
    """Require a successful terminal event on natural stream exhaustion."""

    def __init__(self, stream: Any, provider: str):
        self._stream = stream
        self._provider = provider
        self._iterator = None
        self._completed = False
        self._closed = False
        self._exhausted = False
        self._entered = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed or self._exhausted:
            raise StopAsyncIteration
        if self._iterator is None:
            self._iterator = self._stream.__aiter__()
        try:
            event = await self._iterator.__anext__()
        except StopAsyncIteration:
            self._exhausted = True
            if not self._completed:
                raise RuntimeError(
                    f"{self._provider} Responses stream ended without completion; request was not replayed"
                ) from None
            raise
        event_type = getattr(event, "type", None)
        if event_type in ("response.failed", "response.incomplete", "error"):
            raise RuntimeError(
                f"{self._provider} Responses stream failed or incomplete; request was not replayed ({event_type})"
            )
        if event_type == "response.completed":
            self._completed = True
        return event

    async def __aenter__(self):
        enter = getattr(self._stream, "__aenter__", None)
        if enter is not None:
            entered_stream = await enter()
            self._entered = True
            if entered_stream is not None:
                self._iterator = entered_stream.__aiter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._closed:
            return False
        self._closed = True
        exit_stream = getattr(self._stream, "__aexit__", None)
        if self._entered and exit_stream is not None:
            return await exit_stream(exc_type, exc_val, exc_tb)
        await self._close_stream()
        return False

    async def _close_stream(self):
        close = getattr(self._stream, "aclose", None) or getattr(
            self._stream, "close", None
        )
        if close is not None:
            await close()

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        if self._entered and getattr(self._stream, "__aexit__", None) is not None:
            await self._stream.__aexit__(None, None, None)
        else:
            await self._close_stream()
