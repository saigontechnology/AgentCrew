"""Full-text search support for the console conversation browser."""

from __future__ import annotations

import re
from array import array
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

_INTERNAL_CONTENT_PREFIXES = (
    "Memories related to the user request:",
    "Content of ",
)


@dataclass(frozen=True)
class SearchFragment:
    """A user-visible piece of searchable conversation text."""

    role: str
    text: str
    normalized_text: str = field(init=False, repr=False, compare=False)
    _extra_normalized_offsets: array[int] | None = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        normalized_text = self.text.casefold()
        object.__setattr__(self, "normalized_text", normalized_text)

        if len(normalized_text) == len(self.text):
            return

        extra_normalized_offsets = array("I")
        normalized_offset = 0
        for char in self.text:
            folded_length = len(char.casefold())
            extra_normalized_offsets.extend(
                range(normalized_offset + 1, normalized_offset + folded_length)
            )
            normalized_offset += folded_length

        object.__setattr__(
            self,
            "_extra_normalized_offsets",
            extra_normalized_offsets,
        )

    def find_span(self, normalized_query: str) -> tuple[int, int] | None:
        """Find a normalized query and map its span back to the original text."""
        normalized_start = self.normalized_text.find(normalized_query)
        if normalized_start < 0:
            return None

        normalized_end = normalized_start + len(normalized_query)
        if self._extra_normalized_offsets is None:
            return normalized_start, normalized_end

        original_start = normalized_start - bisect_right(
            self._extra_normalized_offsets,
            normalized_start,
        )
        last_normalized_offset = normalized_end - 1
        original_end = (
            last_normalized_offset
            - bisect_right(
                self._extra_normalized_offsets,
                last_normalized_offset,
            )
            + 1
        )
        return original_start, original_end


@dataclass(frozen=True)
class SearchSnippet:
    """Display-ready context surrounding a search match."""

    before: str
    matched: str
    after: str
    has_leading_ellipsis: bool
    has_trailing_ellipsis: bool


@dataclass(frozen=True)
class SearchMatch:
    """The first title or message match for a conversation."""

    source: str
    text: str
    start: int
    end: int
    role: str | None = None

    def snippet(self, context_chars: int = 60) -> SearchSnippet:
        """Return compact display text around the matched range."""
        context_start = max(0, self.start - context_chars)
        context_end = min(len(self.text), self.end + context_chars)

        before = _collapse_whitespace(self.text[context_start : self.start])
        matched = _collapse_whitespace(self.text[self.start : self.end])
        after = _collapse_whitespace(self.text[self.end : context_end])

        return SearchSnippet(
            before=before,
            matched=matched,
            after=after,
            has_leading_ellipsis=context_start > 0,
            has_trailing_ellipsis=context_end < len(self.text),
        )


def _is_searchable_text(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith(_INTERNAL_CONTENT_PREFIXES)


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _find_casefold_span(text: str, normalized_query: str) -> tuple[int, int] | None:
    """Map a casefolded match back to offsets in the original text."""
    normalized_chars: list[str] = []
    original_offsets: list[int] = []
    for original_offset, char in enumerate(text):
        folded = char.casefold()
        normalized_chars.append(folded)
        original_offsets.extend([original_offset] * len(folded))

    normalized_text = "".join(normalized_chars)
    normalized_start = normalized_text.find(normalized_query)
    if normalized_start < 0:
        return None

    normalized_end = normalized_start + len(normalized_query)
    original_start = original_offsets[normalized_start]
    original_end = original_offsets[normalized_end - 1] + 1
    return original_start, original_end


def iter_searchable_message_fragments(message: Any) -> Iterable[SearchFragment]:
    """Yield searchable fragments with their originating chat role."""
    if not isinstance(message, dict) or message.get("role") not in {
        "user",
        "assistant",
    }:
        return

    role = message["role"]
    content = message.get("content", "")
    if isinstance(content, str):
        if _is_searchable_text(content):
            yield SearchFragment(role=role, text=content)
        return

    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text", "")
        if isinstance(text, str) and _is_searchable_text(text):
            yield SearchFragment(role=role, text=text)


class ConversationSearchIndex:
    """
    Session-local full-text index for persisted conversations.

    Conversation histories are loaded lazily and normalized once. Subsequent queries search the in-memory index without repeating filesystem reads.
    """

    def __init__(
        self,
        get_conversation_history: (
            Callable[[str], list[dict[str, Any]] | None] | None
        ) = None,
    ) -> None:
        self._get_conversation_history = get_conversation_history
        self._message_fragments: dict[str, list[SearchFragment]] = {}
        self._matches: dict[str, SearchMatch] = {}

    def clear(self) -> None:
        """Discard all indexed conversation content."""
        self._message_fragments.clear()
        self._matches.clear()

    def remove(self, conversation_ids: Iterable[str]) -> None:
        """Remove conversations from the in-memory index."""
        for conversation_id in conversation_ids:
            self._message_fragments.pop(conversation_id, None)
            self._matches.pop(conversation_id, None)

    def get_match(self, conversation_id: str) -> SearchMatch | None:
        """Return the match produced by the most recent filter operation."""
        return self._matches.get(conversation_id)

    def filter(
        self,
        conversations: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        """
        Return conversations whose title/visible messages match the user query.
        """
        normalized_query = query.casefold()
        self._matches.clear()
        if not normalized_query:
            return list(conversations)

        matches: list[dict[str, Any]] = []
        for conversation in conversations:
            conversation_id = conversation.get("id")
            if not isinstance(conversation_id, str) or not conversation_id:
                continue

            title = conversation.get("title", "")
            title_span = (
                _find_casefold_span(title, normalized_query)
                if isinstance(title, str)
                else None
            )
            if title_span is not None:
                matches.append(conversation)
                self._matches[conversation_id] = SearchMatch(
                    source="title",
                    text=title,
                    start=title_span[0],
                    end=title_span[1],
                )
                continue

            for fragment in self._get_message_fragments(conversation_id):
                message_span = fragment.find_span(normalized_query)
                if message_span is None:
                    continue
                matches.append(conversation)
                self._matches[conversation_id] = SearchMatch(
                    source="message",
                    role=fragment.role,
                    text=fragment.text,
                    start=message_span[0],
                    end=message_span[1],
                )
                break

        return matches

    def _get_message_fragments(self, conversation_id: str) -> list[SearchFragment]:
        if conversation_id in self._message_fragments:
            return self._message_fragments[conversation_id]

        fragments: list[SearchFragment] = []
        if self._get_conversation_history is not None:
            try:
                history = self._get_conversation_history(conversation_id)
                if isinstance(history, list):
                    for message in history:
                        fragments.extend(iter_searchable_message_fragments(message))
            except Exception as exc:
                logger.warning(
                    "Error indexing conversation '{}' for search: {}",
                    conversation_id,
                    exc,
                )

        self._message_fragments[conversation_id] = fragments
        return fragments
