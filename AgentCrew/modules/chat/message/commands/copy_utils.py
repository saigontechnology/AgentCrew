"""
Shared utilities for the /copy command.

Provides turn-boundary-based candidate selection and text extraction
used by command execution, console completers, and GUI completers.
"""

from __future__ import annotations

from typing import Any


def get_copyable_assistants(
    messages: list[dict[str, Any]],
    conversation_turns: list[Any],
) -> list[dict[str, Any]]:
    """
    Return assistant messages eligible for /copy, ordered oldest to newest.

    For each conversation turn:
      - The turn *starts* at its recorded user-message index.
      - The turn *ends* before the next turn's start (or at the end of messages).
      - The **last** assistant message within that range is selected.

    This approach is more robust than adjacency checks (``i + 1 in indices``)
    because it correctly handles:
      - Turns without any assistant response (no candidate produced).
      - Loaded / synthetic history where message indices may not be contiguous.
      - Tool / tool_result messages inserted between assistant blocks.
    """
    if not conversation_turns or not messages:
        return []

    candidates: list[dict[str, Any]] = []
    for idx, turn in enumerate(conversation_turns):
        turn_start = turn.message_index
        turn_end = (
            conversation_turns[idx + 1].message_index
            if idx + 1 < len(conversation_turns)
            else len(messages)
        )

        last_assistant: dict[str, Any] | None = None
        for i in range(turn_start, turn_end):
            msg = messages[i]
            if msg.get("role") == "assistant":
                last_assistant = msg

        if last_assistant is not None:
            candidates.append(last_assistant)

    return candidates


def extract_assistant_text(msg: dict[str, Any]) -> str:
    """
    Extract all text content from an assistant message.

    Handles both plain-string content and list-of-content-blocks format.
    Concatenates **all** text blocks in order (not just the first one).
    Returns empty string if no text content is found.
    """
    content = msg.get("content", "")
    if isinstance(content, list):
        text = "".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    else:
        text = str(content) if content else ""
    return text


def extract_assistant_text_preview(
    msg: dict[str, Any],
    max_len: int = 50,
) -> str:
    """
    Extract a whitespace-normalised, truncated preview from an assistant message.
    """
    text = extract_assistant_text(msg)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text
