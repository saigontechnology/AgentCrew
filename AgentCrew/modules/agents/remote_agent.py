"""
RemoteAgent using the A2A v1 SDK client.

Key design:
- Uses a2a.client.create_client() with ClientConfig for auth.
- Keeps task_id empty for new conversations (server assigns it).
- Tracks context_id for conversation continuity.
- Reconnects via client.subscribe(SubscribeToTaskRequest).
- Aggregates artifacts from initial Task snapshots on reconnect.
- Supports input-required continuation (attaches existing task_id).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_message_text
from a2a.types.a2a_pb2 import (
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskState,
)
from loguru import logger

from .base import BaseAgent, MessageType

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class ArtifactAccumulator:
    """Append-aware artifact aggregation for dedup-free live events and reconnect reconciliation.

    Live append events: always append the delta (no content-based dedup).
    Live replacement events (append=false): establish new baseline.
    Reconnect snapshots: compare accumulated text vs snapshot total; emit suffix only.
    """

    def __init__(self) -> None:
        self._text_by_id: dict[str, str] = {}
        self._event_count_by_id: dict[
            str, int
        ] = {}  # tracks append event count for dedup
        self.phase: str = "idle"

    def record_text(self, artifact_id: str, text: str) -> None:
        """Append text unconditionally for a live append event."""
        if artifact_id not in self._text_by_id:
            self._text_by_id[artifact_id] = ""
        self._text_by_id[artifact_id] += text

    def replace_text(self, artifact_id: str, text: str) -> None:
        """Replace baseline for a non-append (replacement/snapshot) event."""
        self._text_by_id[artifact_id] = text

    def get_total_text(self, artifact_id: str) -> str:
        """Get the total accumulated text for an artifact."""
        return self._text_by_id.get(artifact_id, "")

    def on_task_snapshot(self, task: Any) -> dict[str, str]:
        """Process a Task snapshot's artifacts (from subscribe/getTask).
        Returns artifact_id -> text suffix that is new since we last saw.
        Only emits content that extends what we've already accumulated.
        """
        result = {}
        for art in task.artifacts:
            aid = art.artifact_id
            art_text = ""
            for p in art.parts:
                if p.HasField("text"):
                    art_text += p.text
            if not art_text:
                continue
            existing = self._text_by_id.get(aid, "")
            if art_text == existing:
                # Already fully consumed — nothing new
                continue
            if art_text.startswith(existing):
                unconsumed = art_text[len(existing) :]
            else:
                # Accumulated text diverged from snapshot; use full snapshot
                unconsumed = art_text
            if unconsumed:
                result[aid] = unconsumed
                self._text_by_id[aid] = art_text
        return result

    def reset(self) -> None:
        self._text_by_id.clear()
        self._event_count_by_id.clear()
        self.phase = "idle"


class RemoteAgent(BaseAgent):
    """Agent that proxies to a remote A2A v1 server."""

    def __init__(
        self,
        name: str,
        agent_url: str,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(name, "")
        self.agent_url = agent_url.rstrip("/")
        self.headers = headers or {}
        self._client = None
        self._client_own_httpx = None
        self.current_task_id: str | None = None
        self.current_context_id: str | None = None
        self._state = ArtifactAccumulator()

    async def _ensure_client(self):
        """Lazy async initialization — creates the SDK client once."""
        if self._client is not None:
            return self._client

        httpx_client = httpx.AsyncClient(
            headers=self.headers,
            timeout=httpx.Timeout(600.0),
        )
        self._client_own_httpx = httpx_client
        config = ClientConfig(httpx_client=httpx_client)
        self._client = await create_client(self.agent_url, client_config=config)
        return self._client

    async def close(self):
        """Close client resources. Safe to call multiple times."""
        if self._client:
            await self._client.close()
            self._client = None
        if self._client_own_httpx:
            await self._client_own_httpx.aclose()
            self._client_own_httpx = None

    def activate(self) -> bool:
        self.is_active = True
        return True

    def deactivate(self) -> bool:
        self.is_active = False
        return True

    def append_message(self, messages: dict | list[dict]):
        if isinstance(messages, dict):
            self.history.append(messages)
        else:
            self.history.extend(messages)

    @property
    def clean_history(self):
        return self.history

    def get_provider(self) -> str:
        return "a2a_remote"

    def get_model(self) -> str:
        return "a2a_remote-remote"

    def is_streaming(self) -> bool:
        return True

    def format_message(
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if message_type == MessageType.Assistant:
            return {
                "role": "assistant",
                "content": [{"type": "text", "text": message_data.get("message", "")}],
            }
        elif message_type == MessageType.ToolResult:
            return {
                "role": "tool",
                "tool_call_id": message_data.get("tool_use", {"id": ""})["id"],
                "content": message_data.get("tool_result", ""),
            }
        elif message_type == MessageType.FileContent:
            return None

    async def execute_tool_call(self, tool_use: dict) -> Any:
        return None

    def configure_think(self, think_setting):
        pass

    def calculate_usage_cost(
        self, input_tokens, output_tokens, cached_tokens=0
    ) -> float:
        return 0.0

    def _extract_artifact_text(self, art: Any) -> str:
        """Extract text from an artifact's parts."""
        texts = []
        for p in art.parts:
            if p.HasField("text"):
                texts.append(p.text)
        return "".join(texts)

    def _extract_status_text(self, status_event: Any) -> str:
        """Extract text from a status update event's message."""
        if status_event.status.HasField("message"):
            return get_message_text(status_event.status.message)
        return ""

    def _handle_task_chunk(
        self,
        chunk: Any,
        full_response_text: list[str],
        is_reconnect: bool,
    ) -> tuple[str, str | None, Any] | None:
        """Process a chunk.HasField('task') — initial or current task snapshot.
        On reconnect (subscribe), process accumulated artifacts from the Task snapshot.
        """
        t = chunk.task
        was_new = self.current_task_id is None
        self.current_task_id = t.id
        self.current_context_id = t.context_id

        # On reconnect: extract unconsumed text from snapshot artifacts
        if is_reconnect or was_new:
            new_artifacts = self._state.on_task_snapshot(t)
            for aid, text in new_artifacts.items():
                if text:
                    full_response_text.append(text)
                    return ("".join(full_response_text), text, None)

        content_chunk = ""
        if t.status.HasField("message"):
            content_chunk = get_message_text(t.status.message)
        if content_chunk:
            full_response_text.append(content_chunk)
            return ("".join(full_response_text), content_chunk, None)
        return None

    def _handle_artifact_chunk(
        self,
        chunk: Any,
        full_response_text: list[str],
    ) -> tuple[str, str | None, Any]:
        art = chunk.artifact_update.artifact
        aid = art.artifact_id
        art_text = self._extract_artifact_text(art)

        if not art_text:
            return ("".join(full_response_text), None, None)

        is_append = chunk.artifact_update.append

        if is_append:
            # Append event: always append the delta, no content-based dedup
            # This preserves legitimate repeated content like "ha"+"ha"="haha"
            self._state.record_text(aid, art_text)
            full_response_text.append(art_text)
            return ("".join(full_response_text), art_text, None)
        else:
            # Replacement/snapshot event: compare against accumulated text
            existing = self._state.get_total_text(aid)
            if art_text == existing:
                # Already fully consumed
                return ("".join(full_response_text), None, None)
            if art_text.startswith(existing):
                unconsumed = art_text[len(existing) :]
            else:
                unconsumed = art_text
            if unconsumed:
                self._state.replace_text(aid, art_text)
                full_response_text.append(unconsumed)
                return ("".join(full_response_text), unconsumed, None)
        return ("".join(full_response_text), None, None)

    async def _handle_status_chunk(
        self,
        chunk: Any,
        full_response_text: list[str],
    ) -> AsyncIterator[tuple[str, str | None, Any]]:
        status_event = chunk.status_update
        state = status_event.status.state

        # Update client state
        if state == TaskState.TASK_STATE_INPUT_REQUIRED:
            self._state.phase = "input_required"
        elif state in (
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_FAILED,
        ):
            self._state.phase = "terminal"
        else:
            self._state.phase = "working"

        msg_text = self._extract_status_text(status_event)
        if msg_text:
            yield ("".join(full_response_text), None, (msg_text, None))

    async def process_messages(
        self,
        messages: list[dict[str, Any]] | None = None,
        callback: Callable | None = None,
    ):
        client = await self._ensure_client()
        if not messages:
            messages = self.history

        last_user_message = messages[-1]
        from AgentCrew.modules.a2a.adapters import convert_agent_message_to_a2a

        a2a_message = convert_agent_message_to_a2a(last_user_message)

        # Preserve context_id for multi-turn conversation continuity
        # Only attach task_id for input_required continuation
        if self._state.phase == "input_required" and self.current_task_id:
            a2a_message.task_id = self.current_task_id
        if self.current_context_id:
            a2a_message.context_id = self.current_context_id
        else:
            ctx = f"ctx_{uuid4().hex}"
            a2a_message.context_id = ctx
            self.current_context_id = ctx

        config = SendMessageConfiguration()
        request = SendMessageRequest(
            message=a2a_message,
            configuration=config,
        )

        full_response_text: list[str] = []
        max_retries = 3
        retry_count = 0
        is_reconnect = False

        while retry_count <= max_retries:
            try:
                if is_reconnect and self.current_task_id:
                    logger.info(
                        f"Resubscribing to task {self.current_task_id} "
                        f"(attempt {retry_count})"
                    )
                    stream = client.subscribe(
                        SubscribeToTaskRequest(id=self.current_task_id)
                    )
                else:
                    stream = client.send_message(request)

                async for chunk in stream:
                    if chunk.HasField("task"):
                        result = self._handle_task_chunk(
                            chunk, full_response_text, is_reconnect
                        )
                        if result:
                            yield result

                    elif chunk.HasField("artifact_update"):
                        result = self._handle_artifact_chunk(chunk, full_response_text)
                        if result[1] is not None or result[2] is not None:
                            yield result

                    elif chunk.HasField("status_update"):
                        async for result in self._handle_status_chunk(
                            chunk, full_response_text
                        ):
                            yield result

                    elif chunk.HasField("message"):
                        msg = chunk.message
                        msg_text = get_message_text(msg)
                        if msg_text:
                            full_response_text.append(msg_text)
                            yield ("".join(full_response_text), msg_text, None)

                # Stream ended naturally
                break

            except (
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.CloseError,
            ) as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(
                        f"Failed to reconnect after {max_retries} attempts: {e}"
                    )
                    raise
                wait_time = min(2**retry_count, 30)
                logger.warning(
                    f"Stream connection lost: {e}. "
                    f"Retrying in {wait_time}s (attempt {retry_count}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                is_reconnect = True
