from __future__ import annotations

from typing import Any

from rich.console import Console

from AgentCrew.modules.console.conversation_browser.browser_ui import (
    ConversationBrowserUI,
)
from AgentCrew.modules.console.conversation_browser.search import (
    ConversationSearchIndex,
    SearchFragment,
    SearchMatch,
    iter_searchable_message_fragments,
)


def _conversation(conversation_id: str, title: str) -> dict[str, Any]:
    return {"id": conversation_id, "title": title, "timestamp": "2026-01-01"}


def test_filters_by_title_without_loading_matching_history() -> None:
    calls: list[str] = []

    def load_history(conversation_id: str) -> list[dict[str, Any]]:
        calls.append(conversation_id)
        return []

    index = ConversationSearchIndex(load_history)
    conversations = [_conversation("one", "Database design")]

    assert index.filter(conversations, "DATABASE") == conversations
    assert calls == []
    assert index.get_match("one") == SearchMatch(
        source="title",
        text="Database design",
        start=0,
        end=8,
    )


def test_filters_user_and_assistant_message_text() -> None:
    histories = {
        "one": [{"role": "user", "content": "Plan a release pipeline"}],
        "two": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "First block"},
                    {"type": "tool_use", "name": "ignored"},
                    {"type": "text", "text": "Kubernetes deployment"},
                ],
            }
        ],
    }
    index = ConversationSearchIndex(histories.get)
    conversations = [
        _conversation("one", "Release"),
        _conversation("two", "Infrastructure"),
    ]

    assert [c["id"] for c in index.filter(conversations, "pipeline")] == ["one"]
    assert [c["id"] for c in index.filter(conversations, "kubernetes")] == ["two"]
    assert index.get_match("two") == SearchMatch(
        source="message",
        role="assistant",
        text="Kubernetes deployment",
        start=0,
        end=10,
    )


def test_search_uses_unicode_casefold() -> None:
    index = ConversationSearchIndex(
        lambda _: [{"role": "assistant", "content": "Straße"}]
    )

    assert index.filter([_conversation("one", "Travel")], "STRASSE")
    assert index.get_match("one") == SearchMatch(
        source="message",
        role="assistant",
        text="Straße",
        start=0,
        end=6,
    )


def test_search_fragment_precomputes_normalized_text_and_sparse_offsets() -> None:
    ascii_fragment = SearchFragment(role="user", text="ALPHA beta")
    unicode_fragment = SearchFragment(role="assistant", text="ß Straße")

    assert ascii_fragment.normalized_text == "alpha beta"
    assert ascii_fragment._extra_normalized_offsets is None
    assert unicode_fragment.normalized_text == "ss strasse"
    assert unicode_fragment._extra_normalized_offsets is not None
    assert len(unicode_fragment._extra_normalized_offsets) == 2
    assert unicode_fragment.find_span("sse") == (6, 8)


def test_search_fragment_does_not_renormalize_text_for_each_query() -> None:
    class CountingText(str):
        casefold_calls = 0

        def casefold(self) -> str:
            self.casefold_calls += 1
            return super().casefold()

    text = CountingText("Alpha beta")
    fragment = SearchFragment(role="user", text=text)

    assert fragment.find_span("alpha") == (0, 5)
    assert fragment.find_span("beta") == (6, 10)
    assert fragment.find_span("missing") is None
    assert text.casefold_calls == 1


def test_match_snippet_collapses_whitespace_and_adds_ellipses() -> None:
    text = f"{'a' * 80}\nmatched\t{'b' * 80}"
    match = SearchMatch(
        source="message",
        role="user",
        text=text,
        start=81,
        end=88,
    )

    snippet = match.snippet(context_chars=20)

    assert snippet.before == "a" * 19 + " "
    assert snippet.matched == "matched"
    assert snippet.after == " " + "b" * 19
    assert snippet.has_leading_ellipsis is True
    assert snippet.has_trailing_ellipsis is True


def test_ignores_internal_non_text_and_non_chat_content() -> None:
    messages = [
        {"role": "system", "content": "system-secret"},
        {"role": "tool", "content": "tool-secret"},
        {"role": "user", "content": "Memories related to the user request: memory"},
        {"role": "user", "content": "Content of notes.txt: injected-file"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "input": {"query": "tool-query"}}],
        },
    ]
    index = ConversationSearchIndex(lambda _: messages)
    conversations = [_conversation("one", "Normal title")]

    for query in [
        "system-secret",
        "tool-secret",
        "memory",
        "injected-file",
        "tool-query",
    ]:
        assert index.filter(conversations, query) == []


def test_history_is_loaded_once_per_browser_session() -> None:
    calls = 0

    def load_history(_: str) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [{"role": "user", "content": "alpha beta"}]

    index = ConversationSearchIndex(load_history)
    conversations = [_conversation("one", "Unrelated")]

    assert index.filter(conversations, "alpha")
    assert index.filter(conversations, "beta")
    assert index.filter(conversations, "missing") == []
    assert calls == 1


def test_loader_failure_is_cached_and_title_search_still_works() -> None:
    calls = 0

    def load_history(_: str) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        raise OSError("unreadable")

    index = ConversationSearchIndex(load_history)
    conversations = [_conversation("one", "Readable title")]

    assert index.filter(conversations, "message") == []
    assert index.filter(conversations, "another") == []
    assert index.filter(conversations, "READABLE") == conversations
    assert calls == 1


def test_message_fragment_extractor_handles_malformed_content() -> None:
    def extract_text(message: Any) -> list[str]:
        return [
            fragment.text for fragment in iter_searchable_message_fragments(message)
        ]

    assert extract_text({"role": "user", "content": "visible"}) == ["visible"]
    assert extract_text(None) == []
    assert extract_text({"role": "user", "content": 42}) == []
    assert (
        extract_text(
            {
                "role": "assistant",
                "content": [None, {"type": "text", "text": 42}],
            }
        )
        == []
    )


def test_browser_ui_full_text_filter_and_filtered_delete() -> None:
    histories = {
        "one": [{"role": "user", "content": "alpha"}],
        "two": [{"role": "assistant", "content": "beta"}],
    }
    ui = ConversationBrowserUI(
        Console(width=120, height=40),
        get_conversation_history=histories.get,
    )
    ui.set_conversations(
        [_conversation("one", "First"), _conversation("two", "Second")]
    )

    ui.update_search_query("beta")
    assert [c["id"] for c in ui.conversations] == ["two"]

    ui.remove_conversations([0])
    ui.exit_search_mode(clear_filter=True)
    assert [c["id"] for c in ui.conversations] == ["one"]


def test_browser_ui_renders_role_context_and_highlight_for_message_match() -> None:
    ui = ConversationBrowserUI(
        Console(width=120, height=40),
        get_conversation_history=lambda _: [
            {
                "role": "assistant",
                "content": "Use PostgreSQL for durable storage",
            }
        ],
    )
    ui.set_conversations([_conversation("one", "Database advice")])

    ui.update_search_query("postgresql")
    match = ui._search_index.get_match("one")
    assert match is not None

    preview_lines = ui._create_search_match_preview(match)
    rendered_match = preview_lines[-1]

    assert rendered_match.plain == "Assistant: Use PostgreSQL for durable storage"
    highlighted_spans = [
        span
        for span in rendered_match.spans
        if str(span.style) == "bold black on yellow"
    ]
    assert len(highlighted_spans) == 1
    highlight = highlighted_spans[0]
    assert rendered_match.plain[highlight.start : highlight.end] == "PostgreSQL"


def test_browser_ui_renders_title_match_without_loading_history() -> None:
    calls = 0

    def load_history(_: str) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return []

    ui = ConversationBrowserUI(
        Console(width=120, height=40),
        get_conversation_history=load_history,
    )
    ui.set_conversations([_conversation("one", "Database advice")])

    ui.update_search_query("database")
    match = ui._search_index.get_match("one")

    assert match is not None
    assert ui._create_search_match_preview(match)[-1].plain == "Title: Database advice"
    assert calls == 0
