"""Focused tests for the OpenAI Responses reasoning-history slice.

Covers:

- capture of completed encrypted reasoning items from
  ``response.output_item.done`` (never from ``response.output_item.added``)
- ``_metadata`` storage: exact encrypted value, copy/serialization survival
- replay of stored reasoning items into the next Responses request, exactly
  once and in correct order, without duplicating the canonical assistant item
- reasoning summary deltas streamed as AgentCrew thinking chunks
- summary fallback from the completed item when no deltas were received
- provider/model mismatch fallback to canonical visible history
- safe debugging: diagnostics never contain the encrypted payload
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest
from loguru import logger

from AgentCrew.modules.agents.message_metadata import (
    METADATA_FIELD,
    attach_stream_metadata,
    build_responses_metadata,
    get_reasoning_output_items,
    get_responses_provider_state,
    redact_message_for_debug,
    safe_reasoning_diagnostics,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai.response_service import OpenAIResponseService

ENCRYPTED = "gAAAAABencrypted-reasoning-blob-payload=="


def _make_service(
    provider: str = "openai", model: str = "gpt-5.4"
) -> OpenAIResponseService:
    svc = OpenAIResponseService.__new__(OpenAIResponseService)
    svc._provider_name = provider
    svc.model = model
    svc.tools = []
    svc.structured_output = None
    svc.reasoning_effort = "medium"
    svc.system_prompt = ""
    svc._extra_headers = None
    return svc


def _event(event_type: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **kwargs)


def _summary_part(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="summary_text", text=text)


def _reasoning_item(
    item_id: str = "rs_1",
    encrypted_content: str | None = ENCRYPTED,
    summary: list | None = None,
    content: list | None = None,
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        type="reasoning",
        summary=list(summary or []),
        content=content,
        encrypted_content=encrypted_content,
        status=status,
    )


def _reasoning_item_dict(item_id: str = "rs_1") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "step"}],
        "status": "completed",
        "encrypted_content": ENCRYPTED,
    }


def _completed_event() -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=None,
    )
    return _event("response.completed", response=SimpleNamespace(usage=usage))


# ---------------------------------------------------------------------------
# A. capture from response.output_item.done
# ---------------------------------------------------------------------------


class TestCaptureCompletedReasoning:
    def test_completed_item_captured_with_all_fields(self):
        svc = _make_service()
        state = svc.create_stream_state()
        item = _reasoning_item(summary=[_summary_part("step")])

        chunk = _event("response.output_item.done", item=item, output_index=0)
        svc.process_stream_chunk(chunk, "", [], state)

        assert len(state["reasoning_items"]) == 1
        stored = state["reasoning_items"][0]
        assert stored["id"] == "rs_1"
        assert stored["type"] == "reasoning"
        assert stored["summary"] == [{"type": "summary_text", "text": "step"}]
        assert stored["status"] == "completed"
        assert stored["encrypted_content"] == ENCRYPTED

    def test_capture_uses_model_dump_when_available(self):
        svc = _make_service()
        state = svc.create_stream_state()

        class _DumpItem:
            type = "reasoning"
            id = "rs_dump"
            encrypted_content = ENCRYPTED

            def model_dump(self, mode=None, exclude_none=None):
                return {
                    "id": "rs_dump",
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "dumped"}],
                    "status": "completed",
                    "encrypted_content": ENCRYPTED,
                }

        chunk = _event("response.output_item.done", item=_DumpItem(), output_index=0)
        svc.process_stream_chunk(chunk, "", [], state)

        assert state["reasoning_items"][0]["id"] == "rs_dump"
        assert state["reasoning_items"][0]["summary"] == [
            {"type": "summary_text", "text": "dumped"}
        ]
        assert state["reasoning_items"][0]["encrypted_content"] == ENCRYPTED

    def test_multiple_reasoning_items_captured_in_output_order(self):
        svc = _make_service()
        state = svc.create_stream_state()
        for idx, item_id in enumerate(["rs_a", "rs_b"]):
            chunk = _event(
                "response.output_item.done",
                item=_reasoning_item(item_id=item_id),
                output_index=idx,
            )
            svc.process_stream_chunk(chunk, "", [], state)

        assert [item["id"] for item in state["reasoning_items"]] == ["rs_a", "rs_b"]


# ---------------------------------------------------------------------------
# D. incomplete added items and items without encrypted_content are not stored
# ---------------------------------------------------------------------------


class TestCaptureExclusionRules:
    def test_output_item_added_is_not_captured(self):
        svc = _make_service()
        state = svc.create_stream_state()
        item = _reasoning_item(summary=[_summary_part("partial")])

        chunk = _event("response.output_item.added", item=item, output_index=0)
        svc.process_stream_chunk(chunk, "", [], state)

        assert state["reasoning_items"] == []

    def test_item_without_encrypted_content_is_not_captured(self):
        svc = _make_service()
        state = svc.create_stream_state()
        item = _reasoning_item(item_id="rs_plain", encrypted_content=None)

        chunk = _event("response.output_item.done", item=item, output_index=0)
        svc.process_stream_chunk(chunk, "", [], state)

        assert state["reasoning_items"] == []


# ---------------------------------------------------------------------------
# B. _metadata storage: exact value, copy/serialization survival
# ---------------------------------------------------------------------------


class TestMetadataStorage:
    def test_metadata_stores_encrypted_value_exactly(self):
        svc = _make_service()
        state = svc.create_stream_state()
        svc.process_stream_chunk(
            _event(
                "response.output_item.done",
                item=_reasoning_item(summary=[_summary_part("step")]),
                output_index=0,
            ),
            "",
            [],
            state,
        )

        message: dict[str, Any] = {
            "role": "assistant",
            "content": [{"type": "text", "text": "answer"}],
        }
        attach_stream_metadata(message, state)

        stored = get_responses_provider_state(message)
        assert stored is not None
        assert stored == {
            "provider": "openai",
            "model": "gpt-5.4",
            "output_items": state["reasoning_items"],
        }
        assert stored["output_items"][0]["encrypted_content"] == ENCRYPTED

    def test_metadata_survives_deepcopy_and_json_roundtrip(self):
        svc = _make_service()
        state = svc.create_stream_state()
        svc.process_stream_chunk(
            _event(
                "response.output_item.done",
                item=_reasoning_item(),
                output_index=0,
            ),
            "",
            [],
            state,
        )
        message: dict[str, Any] = {"role": "assistant", "content": []}
        attach_stream_metadata(message, state)

        copied = copy.deepcopy(message)
        copied_state = get_responses_provider_state(copied)
        assert copied_state is not None
        assert copied_state["output_items"][0]["encrypted_content"] == ENCRYPTED

        as_json = json.loads(json.dumps(message))
        json_state = get_responses_provider_state(as_json)
        assert json_state is not None
        assert json_state["output_items"][0]["encrypted_content"] == ENCRYPTED

        # attach must not pop the metadata from the persisted message
        assert METADATA_FIELD in message

    def test_attach_is_noop_without_state(self):
        message: dict[str, Any] = {"role": "assistant", "content": []}
        attach_stream_metadata(message, None)
        attach_stream_metadata(message, {"reasoning_items": []})
        assert METADATA_FIELD not in message


# ---------------------------------------------------------------------------
# C. replay: exactly once, correct order, no duplication
# ---------------------------------------------------------------------------


class TestReplay:
    def test_replay_replays_reasoning_once_in_order(self):
        svc = _make_service()
        history = [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {
                "role": "assistant",
                "agent": "Engineer",
                "content": [
                    {"type": "thinking", "thinking": "step"},
                    {"type": "text", "text": "answer"},
                ],
                METADATA_FIELD: build_responses_metadata(
                    "openai", "gpt-5.4", [_reasoning_item_dict()]
                ),
            },
            {"role": "user", "content": [{"type": "text", "text": "more"}]},
        ]
        original = copy.deepcopy(history)

        converted = svc._convert_internal_format(copy.deepcopy(history))

        reasoning_items = [m for m in converted if m.get("type") == "reasoning"]
        assert len(reasoning_items) == 1
        assert reasoning_items[0]["encrypted_content"] == ENCRYPTED
        assert converted.index(reasoning_items[0]) == 1

        assistant = converted[2]
        assert assistant["role"] == "assistant"
        # canonical thinking block suppressed — the summary lives in the
        # replayed reasoning item
        assert all(part.get("type") != "thinking" for part in assistant["content"])
        assert assistant["content"] == [{"type": "output_text", "text": "answer"}]
        assert converted[3]["role"] == "user"

        # persisted history untouched (conversion must operate on copies)
        assert history == original
        assert METADATA_FIELD in history[1]
        assert history[1]["content"][0] == {"type": "thinking", "thinking": "step"}

    def test_replay_ordering_with_function_call(self):
        svc = _make_service()
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "step"},
                    {"type": "text", "text": "calling tool"},
                ],
                "tool_calls": [
                    {"id": "call_1", "name": "web_search", "arguments": {"q": "x"}}
                ],
                METADATA_FIELD: build_responses_metadata(
                    "openai", "gpt-5.4", [_reasoning_item_dict()]
                ),
            }
        ]

        converted = svc._convert_internal_format(copy.deepcopy(history))

        assert len(converted) == 3
        assert converted[0].get("type") == "reasoning"
        assert converted[1].get("role") == "assistant"
        assert converted[2].get("type") == "function_call"
        function_call = converted[2]
        assert function_call["call_id"] == "call_1"
        assert function_call["name"] == "web_search"
        assert json.loads(function_call["arguments"]) == {"q": "x"}
        assert all(part.get("type") != "thinking" for part in converted[1]["content"])


# ---------------------------------------------------------------------------
# H. provider/model mismatch falls back to canonical history
# ---------------------------------------------------------------------------


class TestCompatibility:
    def test_provider_mismatch_not_replayed(self):
        codex_state = build_responses_metadata(
            "openai_codex", "gpt-5.4", [_reasoning_item_dict()]
        )
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "step"},
                    {"type": "text", "text": "answer"},
                ],
                METADATA_FIELD: codex_state,
            }
        ]

        converted = _make_service(provider="openai")._convert_internal_format(history)

        assert not any(m.get("type") == "reasoning" for m in converted)
        # fallback: canonical thinking converted to visible output_text
        assert converted[0]["content"][0]["text"] == "<think>step</think>"

    def test_model_mismatch_not_replayed(self):
        state = build_responses_metadata("openai", "gpt-5.4", [_reasoning_item_dict()])
        history = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
                METADATA_FIELD: state,
            }
        ]

        converted = _make_service(model="gpt-5.5")._convert_internal_format(history)

        assert not any(m.get("type") == "reasoning" for m in converted)

    def test_same_family_variant_replayed(self):
        state = build_responses_metadata("openai", "gpt-5.4", [_reasoning_item_dict()])
        history = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
                METADATA_FIELD: state,
            }
        ]

        converted = _make_service(model="gpt-5.4-codex")._convert_internal_format(
            history
        )

        assert [m for m in converted if m.get("type") == "reasoning"]

    def test_state_without_identity_not_replayed(self):
        state = {"output_items": [_reasoning_item_dict()]}
        history = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
                METADATA_FIELD: {
                    "version": 1,
                    "provider_state": {"openai_responses": state},
                },
            }
        ]

        converted = _make_service()._convert_internal_format(history)

        assert not any(m.get("type") == "reasoning" for m in converted)


# ---------------------------------------------------------------------------
# E/F. summary deltas as thinking, fallback, duplication prevention
# ---------------------------------------------------------------------------


class TestSummaryThinking:
    def test_summary_delta_streams_as_thinking(self):
        svc = _make_service()
        state = svc.create_stream_state()

        chunk = _event("response.reasoning_summary_text.delta", delta="step one")
        _, _, _, _, thinking = svc.process_stream_chunk(chunk, "", [], state)

        assert thinking == ("step one", None)
        assert state["summary_deltas_received"] is True

    def test_completed_summary_not_repeated_after_deltas(self):
        svc = _make_service()
        state = svc.create_stream_state()
        svc.process_stream_chunk(
            _event("response.reasoning_summary_text.delta", delta="step one"),
            "",
            [],
            state,
        )

        item = _reasoning_item(summary=[_summary_part("step one")])
        chunk = _event("response.output_item.done", item=item, output_index=0)
        _, _, _, _, thinking = svc.process_stream_chunk(chunk, "", [], state)

        assert thinking is None

    def test_summary_fallback_when_no_deltas_received(self):
        svc = _make_service()
        state = svc.create_stream_state()
        item = _reasoning_item(summary=[_summary_part("fallback summary")])

        chunk = _event("response.output_item.done", item=item, output_index=0)
        _, _, _, _, thinking = svc.process_stream_chunk(chunk, "", [], state)

        assert thinking == ("fallback summary", None)

    def test_visible_content_takes_precedence_over_summary(self):
        svc = _make_service()
        state = svc.create_stream_state()
        item = _reasoning_item(
            summary=[_summary_part("summary text")],
            content=[SimpleNamespace(type="output_text", text="visible reasoning")],
        )

        chunk = _event("response.output_item.done", item=item, output_index=0)
        _, _, _, _, thinking = svc.process_stream_chunk(chunk, "", [], state)

        assert thinking == ("visible reasoning", None)


# ---------------------------------------------------------------------------
# I. safe debugging — encrypted payload never in diagnostics or logs
# ---------------------------------------------------------------------------


class TestSafeDebugging:
    def test_diagnostics_report_only_safe_facts(self):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            METADATA_FIELD: build_responses_metadata(
                "openai", "gpt-5.4", [_reasoning_item_dict()]
            ),
        }

        diagnostics = safe_reasoning_diagnostics(message)

        assert diagnostics == {
            "has_responses_state": True,
            "provider": "openai",
            "model": "gpt-5.4",
            "reasoning_item_count": 1,
            "reasoning_item_ids": ["rs_1"],
            "encrypted_content_present": [True],
            "encrypted_lengths": [len(ENCRYPTED)],
        }
        assert ENCRYPTED not in json.dumps(diagnostics)

    def test_diagnostics_without_state(self):
        diagnostics = safe_reasoning_diagnostics({"role": "assistant", "content": []})
        assert diagnostics == {"has_responses_state": False}

    def test_redact_message_for_debug(self):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            METADATA_FIELD: build_responses_metadata(
                "openai", "gpt-5.4", [_reasoning_item_dict()]
            ),
        }

        redacted = redact_message_for_debug(message)

        redacted_state = get_responses_provider_state(redacted)
        assert redacted_state is not None
        assert redacted_state["output_items"][0]["encrypted_content"] == "<redacted>"
        # original message untouched
        original_state = get_responses_provider_state(message)
        assert original_state is not None
        assert original_state["output_items"][0]["encrypted_content"] == ENCRYPTED
        assert ENCRYPTED not in json.dumps(redacted)

    def test_capture_debug_log_reports_safe_facts_only(self):
        svc = _make_service()
        state = svc.create_stream_state()
        records: list[str] = []
        handler_id = logger.add(records.append, level="DEBUG")
        try:
            svc.process_stream_chunk(
                _event(
                    "response.output_item.done",
                    item=_reasoning_item(summary=[_summary_part("step")]),
                    output_index=0,
                ),
                "",
                [],
                state,
            )
        finally:
            logger.remove(handler_id)

        log_text = "\n".join(records)
        assert ENCRYPTED not in log_text
        assert "rs_1" in log_text
        assert str(len(ENCRYPTED)) in log_text


# ---------------------------------------------------------------------------
# Base-service request configuration (reasoning summary request)
# ---------------------------------------------------------------------------


class TestBaseServiceRequestConfig:
    @pytest.mark.asyncio
    async def test_stream_request_includes_summary_auto(self, monkeypatch):
        monkeypatch.setattr(
            ModelRegistry,
            "get_model_capabilities",
            lambda model_id: ["thinking"],
        )
        monkeypatch.setattr(
            ModelRegistry,
            "get_model_sample_params",
            lambda model_id: None,
        )
        captured: dict[str, Any] = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return "stream"

        svc = _make_service()
        svc.client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))

        await svc.stream_assistant_response([{"role": "user", "content": "hi"}])

        assert captured["reasoning"] == {"effort": "medium", "summary": "auto"}

    def test_create_stream_state_carries_provider_identity(self):
        svc = _make_service(provider="openai", model="gpt-5.4")
        state = svc.create_stream_state()
        assert state["provider"] == "openai"
        assert state["model"] == "gpt-5.4"
        assert state["reasoning_items"] == []
        assert state["summary_deltas_received"] is False

    def test_get_reasoning_output_items_filters_by_type(self):
        state = {
            "output_items": [
                _reasoning_item_dict(),
                {"id": "msg_1", "type": "message"},
            ]
        }
        items = get_reasoning_output_items(state)
        assert [item["id"] for item in items] == ["rs_1"]
