"""Streaming state and chunk buffering for A2A executor.

This module contains the ``AnswerArtifactState`` and ``_TurnChunkBuffer``
classes that manage answer artifact state across recursive ``_process_task``
calls and time-based chunk accumulation for A2A streaming.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    TaskArtifactUpdateEvent,
)

if TYPE_CHECKING:
    from a2a.server.events import EventQueue


class AnswerArtifactState:
    """Tracks answer artifact state across recursive _process_task calls.

    Attributes:
        artifact_id: Stable artifact ID for the answer.
        turn: Zero-based turn counter; incremented on each new-turn recursion.
        emitted: Whether at least one chunk has been emitted.
        accumulated_text: Full accumulated text for deduplication purposes.
    """

    def __init__(self, artifact_id: str, turn: int = 0) -> None:
        self.artifact_id = artifact_id
        self.turn = turn
        self.emitted = False
        self.accumulated_text = ""
        self._think_state: AnswerArtifactState | None = None


class _TurnChunkBuffer:
    """Time-based (100ms) chunk accumulator for a single LLM turn.

    Owned by one ``_process_task`` invocation and never shared across turns:
    each turn creates its own buffer and background flush task. Chunks are
    accumulated and flushed as a single ``TaskArtifactUpdateEvent`` batch so
    the SDK task-store persistence runs once per ~100ms instead of once per
    token, which is what made A2A streaming slower than the interactive UI.
    """

    FLUSH_INTERVAL = 0.1

    def __init__(
        self,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        answer_state: AnswerArtifactState,
    ) -> None:
        self._event_queue = event_queue
        self._task_id = task_id
        self._context_id = context_id
        self._answer_state = answer_state
        self._answer_parts: list[str] = []
        self._think_state: AnswerArtifactState | None = None
        self._think_parts: list[str] = []
        self._flush_lock = asyncio.Lock()

    def add_answer(self, text: str) -> None:
        self._answer_parts.append(text)

    def add_thinking(self, think_state: AnswerArtifactState, text: str) -> None:
        self._think_state = think_state
        self._think_parts.append(text)

    async def flush_loop(self) -> None:
        """Background loop flushing buffered chunks every 100ms."""
        while True:
            await asyncio.sleep(self.FLUSH_INTERVAL)
            await self.flush()

    async def flush(self) -> None:
        """Emit buffered chunks as one batched artifact update each.

        Snapshot/clear and append-state updates are serialized under a
        per-buffer lock so overlapping timer/final flushes can never emit two
        ``append=False`` batches (the SDK treats them as artifact
        replacement) and never duplicate content.
        """
        async with self._flush_lock:
            answer_text = "".join(self._answer_parts)
            think_text = "".join(self._think_parts)
            self._answer_parts.clear()
            self._think_parts.clear()

            if answer_text:
                await self._event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=self._task_id,
                        context_id=self._context_id,
                        artifact=Artifact(
                            artifact_id=self._answer_state.artifact_id,
                            parts=[Part(text=answer_text)],
                        ),
                        append=self._answer_state.emitted,
                        last_chunk=False,
                    )
                )
                self._answer_state.emitted = True
                self._answer_state.accumulated_text += answer_text

            if think_text and self._think_state is not None:
                await self._event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=self._task_id,
                        context_id=self._context_id,
                        artifact=Artifact(
                            artifact_id=self._think_state.artifact_id,
                            parts=[Part(text=think_text)],
                        ),
                        append=self._think_state.emitted,
                        last_chunk=False,
                    )
                )
                self._think_state.emitted = True
