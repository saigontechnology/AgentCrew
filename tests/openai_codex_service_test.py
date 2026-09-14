from types import SimpleNamespace
from typing import Any

import pytest

from AgentCrew.modules.agents.message_metadata import (
    attach_stream_metadata,
    get_responses_provider_state,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai_codex.service import OpenAICodexService

CODEX_ENCRYPTED = "gAAAAABcodex-encrypted-reasoning-blob=="


class TestOpenAICodexService:
    def test_codex_service_tier_defaults_to_default(self, monkeypatch):
        monkeypatch.delenv("AGENTCREW_FAST_CODEX", raising=False)

        assert OpenAICodexService._codex_service_tier() == "default"

    def test_codex_service_tier_uses_priority_when_fast_codex_enabled(
        self, monkeypatch
    ):
        monkeypatch.setenv("AGENTCREW_FAST_CODEX", "1")

        assert OpenAICodexService._codex_service_tier() == "priority"

    def test_codex_service_tier_ignores_other_values(self, monkeypatch):
        monkeypatch.setenv("AGENTCREW_FAST_CODEX", "true")

        assert OpenAICodexService._codex_service_tier() == "default"


def _make_codex_service(captured: dict[str, Any] | None = None) -> OpenAICodexService:
    """Build a Codex service with stubbed OAuth and capture client."""
    svc = OpenAICodexService.__new__(OpenAICodexService)
    svc._oauth = SimpleNamespace(
        get_valid_access_token=lambda: "token",
        account_id="account-1",
    )

    async def fake_create(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return "stream"

    svc.client = SimpleNamespace(
        api_key="token",
        responses=SimpleNamespace(create=fake_create),
    )
    svc._provider_name = "openai_codex"
    svc.model = "gpt-5.4"
    svc.tools = []
    svc.structured_output = None
    svc.reasoning_effort = "medium"
    svc.system_prompt = ""
    svc._extra_headers = None
    return svc


class TestCodexRequestConfiguration:
    @pytest.mark.asyncio
    async def test_stream_preserves_store_false_and_encrypted_include(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            ModelRegistry, "get_model_capabilities", lambda model_id: ["thinking"]
        )
        monkeypatch.setattr(
            ModelRegistry, "get_model_sample_params", lambda model_id: None
        )
        captured: dict[str, Any] = {}
        svc = _make_codex_service(captured)

        await svc.stream_assistant_response([{"role": "user", "content": "hi"}])

        assert captured["store"] is False
        assert captured["include"] == ["reasoning.encrypted_content"]
        assert captured["reasoning"] == {
            "effort": "medium",
            "summary": "auto",
        }


class TestCodexInheritedCaptureAndReplay:
    def test_capture_and_replay_via_inherited_logic(self):
        svc = _make_codex_service()
        state = svc.create_stream_state()
        assert state["provider"] == "openai_codex"

        item = SimpleNamespace(
            id="rs_codex",
            type="reasoning",
            summary=[SimpleNamespace(type="summary_text", text="codex step")],
            content=None,
            encrypted_content=CODEX_ENCRYPTED,
            status="completed",
        )
        svc.process_stream_chunk(
            SimpleNamespace(
                type="response.output_item.done", item=item, output_index=0
            ),
            "",
            [],
            state,
        )
        assert state["reasoning_items"][0]["encrypted_content"] == CODEX_ENCRYPTED

        message: dict[str, Any] = {
            "role": "assistant",
            "content": [{"type": "text", "text": "answer"}],
        }
        attach_stream_metadata(message, state)
        attached_state = get_responses_provider_state(message)
        assert attached_state is not None
        assert attached_state["provider"] == "openai_codex"

        history = [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            message,
        ]
        converted = svc._convert_internal_format(history)
        reasoning_items = [m for m in converted if m.get("type") == "reasoning"]
        assert len(reasoning_items) == 1
        assert reasoning_items[0]["encrypted_content"] == CODEX_ENCRYPTED
