"""Centralized helpers for AgentCrew-internal message metadata.

The ``_metadata`` envelope (schema version 1) currently stores only opaque
provider continuation state for the OpenAI Responses API. Existing AgentCrew
custom history fields (``agent``, ``tool_name``, ``is_rejected``, ...) remain
top-level and are intentionally NOT migrated in this slice.

Security contract:

- ``encrypted_content`` is opaque provider state. Never log, print, or dump it.
- Use :func:`safe_reasoning_diagnostics` to obtain non-secret facts (item IDs,
  presence, count, encrypted length) for debugging and tests.
- Use :func:`redact_message_for_debug` before dumping messages for debugging.

Persistence contract:

- Provider conversion must operate on copies and must never pop ``_metadata``
  from the persisted in-memory history object.
"""

import copy
from typing import Any

METADATA_FIELD = "_metadata"
METADATA_VERSION = 1
PROVIDER_STATE_OPENAI_RESPONSES = "openai_responses"
REASONING_ITEM_TYPE = "reasoning"


def build_responses_metadata(
    provider: str, model: str, output_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the ``_metadata`` envelope holding OpenAI Responses continuation state.

    Args:
        provider: Provider/service identity that produced the items (e.g.
            ``openai_codex``).
        model: Model ID used for the request that produced the items.
        output_items: Completed Responses output items (reasoning items with
            ``encrypted_content``).

    Returns:
        The ``_metadata`` dict to attach to an assistant history message.
    """
    return {
        "version": METADATA_VERSION,
        "provider_state": {
            PROVIDER_STATE_OPENAI_RESPONSES: {
                "provider": provider,
                "model": model,
                "output_items": output_items,
            }
        },
    }


def attach_stream_metadata(
    message: dict[str, Any] | None, stream_state: dict[str, Any] | None
) -> None:
    """Attach captured provider stream state to an assistant message.

    No-op when the stream captured no reasoning items or the message is empty.
    """
    if not message or not stream_state:
        return
    reasoning_items = stream_state.get("reasoning_items") or []
    if not reasoning_items:
        return
    message[METADATA_FIELD] = build_responses_metadata(
        provider=stream_state.get("provider", ""),
        model=stream_state.get("model", ""),
        output_items=reasoning_items,
    )


def get_responses_provider_state(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``openai_responses`` provider state of a message, or ``None``."""
    metadata = message.get(METADATA_FIELD)
    if not isinstance(metadata, dict):
        return None
    provider_state = metadata.get("provider_state")
    if not isinstance(provider_state, dict):
        return None
    state = provider_state.get(PROVIDER_STATE_OPENAI_RESPONSES)
    return state if isinstance(state, dict) else None


def get_reasoning_output_items(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return completed reasoning output items stored in provider state."""
    if not state:
        return []
    items = state.get("output_items")
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict) and item.get("type") == REASONING_ITEM_TYPE
    ]


def is_compatible_responses_state(
    state: dict[str, Any] | None, provider: str, model: str
) -> bool:
    """Check whether stored Responses state may be replayed by this service.

    Conservative on purpose: the state must come from the same provider/service
    identity and a compatible model identity/family. On mismatch, callers must
    ignore the provider state and fall back to canonical visible history.
    """
    if not state:
        return False
    stored_provider = state.get("provider")
    stored_model = state.get("model")
    if not stored_provider or not stored_model:
        return False
    if stored_provider != provider:
        return False
    return _models_compatible(str(stored_model), str(model))


def _models_compatible(stored_model: str, current_model: str) -> bool:
    """Model compatibility: exact match or the same model family prefix.

    The family is the model ID without a trailing variant suffix, e.g.
    ``gpt-5.1-codex`` and ``gpt-5.1`` share the family ``gpt-5.1``.
    """
    if stored_model == current_model:
        return True
    return _model_family(stored_model) == _model_family(current_model)


def _model_family(model: str) -> str:
    parts = model.split("-")
    if len(parts) > 2:
        return "-".join(parts[:2])
    return model


def strip_internal_metadata(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deep copies of messages with ``_metadata`` removed.

    Providers that mutate message dicts in place must use this (or an
    equivalent copy) so the persisted history keeps its metadata.
    """
    stripped = copy.deepcopy(messages)
    for message in stripped:
        if isinstance(message, dict):
            message.pop(METADATA_FIELD, None)
    return stripped


def redact_message_for_debug(message: dict[str, Any]) -> dict[str, Any]:
    """Return a debug-safe deep copy with encrypted payloads redacted."""
    redacted = copy.deepcopy(message)
    state = get_responses_provider_state(redacted)
    if state is None:
        return redacted
    for item in state.get("output_items", []) or []:
        if isinstance(item, dict) and item.get("encrypted_content"):
            item["encrypted_content"] = "<redacted>"
    return redacted


def safe_reasoning_diagnostics(message: dict[str, Any]) -> dict[str, Any]:
    """Return non-secret diagnostic facts about stored Responses reasoning state.

    Reports item IDs, count, and encrypted payload lengths only — never the
    encrypted payloads themselves.
    """
    state = get_responses_provider_state(message)
    if state is None:
        return {"has_responses_state": False}
    items = get_reasoning_output_items(state)
    return {
        "has_responses_state": True,
        "provider": state.get("provider"),
        "model": state.get("model"),
        "reasoning_item_count": len(items),
        "reasoning_item_ids": [item.get("id") for item in items],
        "encrypted_content_present": [
            bool(item.get("encrypted_content")) for item in items
        ],
        "encrypted_lengths": [
            len(item.get("encrypted_content") or "") for item in items
        ],
    }
