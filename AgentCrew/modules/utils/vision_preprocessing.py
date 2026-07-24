from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loguru import logger

from AgentCrew.modules.llm.model_registry import ModelRegistry

VISION_CACHE_PATH_ENV = "AGENTCREW_VISION_CACHE_PATH"
DEFAULT_VISION_CACHE_PATH = "~/.AgentCrew/cache/visions"
VISION_DESCRIPTION_PROMPT = """Describe the image in as much useful detail as possible in around 400 words single paragraph:

Include:
- main subject
- scene and environment
- visible objects, their positions and relationships
- colors, layout, style, and notable visual details
- any people, actions, emotions, or gestures
- all visible text, labels, signs, code, UI text, tables, charts, or handwriting
- any uncertainty or ambiguity

Return only the description. Do not mention that you are an AI model."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_hex(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def sha256_label(value: bytes | str) -> str:
    return f"sha256:{sha256_hex(value)}"


def parse_data_url(url: str) -> tuple[str | None, bytes] | None:
    if not url.startswith("data:"):
        return None
    header, separator, payload = url.partition(",")
    if separator != ",":
        return None
    metadata = header[5:]
    parts = metadata.split(";") if metadata else []
    mime_type = parts[0] if parts and "/" in parts[0] else None
    is_base64 = any(part.lower() == "base64" for part in parts)
    try:
        if is_base64:
            return mime_type, base64.b64decode(payload, validate=True)
        return mime_type, payload.encode("utf-8")
    except (binascii.Error, ValueError) as exc:
        logger.warning(
            f"Could not decode data URL image for vision preprocessing: {exc}"
        )
        return None


def normalize_remote_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, parsed.path, query, ""))


def fingerprint_image_url(url: str) -> dict[str, str | None]:
    data = parse_data_url(url)
    if data:
        mime_type, image_bytes = data
        return {
            "image_fingerprint": sha256_label(image_bytes),
            "image_source_type": "data_url",
            "image_mime_type": mime_type,
        }

    normalized_url = normalize_remote_url(url)
    return {
        "image_fingerprint": sha256_label(normalized_url),
        "image_source_type": "remote_url",
        "image_mime_type": None,
    }


def build_vision_cache_key(
    *,
    image_fingerprint: str,
    provider: str,
    vision_model: str,
) -> str:
    key_payload = {
        "image_fingerprint": image_fingerprint,
        "provider": provider,
        "vision_model": vision_model,
    }
    serialized = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
    return sha256_label(serialized)


class VisionDescriptionCache:
    def __init__(
        self, base_path: str | Path | None = None, disabled: bool | None = None
    ):
        env_disabled = (
            os.getenv("AGENTCREW_VISION_CACHE_DISABLED", "").lower() == "true"
        )
        self.disabled = env_disabled if disabled is None else disabled
        configured_path = (
            base_path or os.getenv("VISION_CACHE_PATH_ENV") or DEFAULT_VISION_CACHE_PATH
        )
        self.index_path = Path(configured_path).expanduser()

    def get(self, cache_key: str) -> dict[str, Any] | None:
        if self.disabled:
            return None
        path = self._entry_path(cache_key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                entry = json.load(f)
            if not isinstance(entry, dict) or entry.get("cache_key") != cache_key:
                return None
            entry["last_accessed_at"] = utc_now_iso()
            self._write_entry(path, entry)
            return entry
        except Exception as exc:
            logger.warning(f"Ignoring invalid vision description cache entry: {exc}")
            return None

    def set(self, cache_key: str, entry: dict[str, Any]) -> None:
        if self.disabled:
            return
        now = utc_now_iso()
        entry = dict(entry)
        entry["cache_key"] = cache_key
        entry.setdefault("created_at", now)
        entry["last_accessed_at"] = now
        self._write_entry(self._entry_path(cache_key), entry)

    def delete(self, cache_key: str) -> None:
        if self.disabled:
            return
        try:
            self._entry_path(cache_key).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"Failed to delete vision description cache entry: {exc}")

    def clear_all(self) -> None:
        if self.disabled or not self.index_path.exists():
            return
        for path in self.index_path.glob("*/*.json"):
            try:
                path.unlink()
            except Exception as exc:
                logger.warning(
                    f"Failed to delete vision description cache entry: {exc}"
                )

    def clear_for_model(self, vision_model: str) -> None:
        self._clear_matching(lambda entry: entry.get("vision_model") == vision_model)

    def clear_for_image(self, image_fingerprint: str) -> None:
        self._clear_matching(
            lambda entry: entry.get("image_fingerprint") == image_fingerprint
        )

    def _clear_matching(self, predicate) -> None:
        if self.disabled or not self.index_path.exists():
            return
        for path in self.index_path.glob("*/*.json"):
            try:
                with path.open("r", encoding="utf-8") as f:
                    entry = json.load(f)
                if isinstance(entry, dict) and predicate(entry):
                    path.unlink()
            except Exception as exc:
                logger.warning(
                    f"Failed to inspect vision description cache entry: {exc}"
                )

    def _entry_path(self, cache_key: str) -> Path:
        key = cache_key.split(":", 1)[-1]
        return self.index_path / key[:2] / f"{key}.json"

    def _write_entry(self, path: Path, entry: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path.replace(path)


class VisionPreprocessingUtils:
    @staticmethod
    async def preprocess_messages(
        messages: list[dict[str, Any]],
        llm_service: Any,
        cache: VisionDescriptionCache | None = None,
    ):
        main_model_id = f"{llm_service.provider_name}/{llm_service.model}"
        registry = ModelRegistry.get_instance()
        main_model = registry.get_model(main_model_id)
        if not main_model:
            return
        if "vision" in main_model.capabilities:
            return
        vision_model_id = main_model.vision_model
        if not vision_model_id:
            return
        vision_model = VisionPreprocessingUtils._resolve_vision_model(
            main_model.provider, vision_model_id
        )
        if not vision_model:
            return
        if "vision" not in vision_model.capabilities:
            logger.warning(
                f"Vision preprocessing skipped because {vision_model.provider}/{vision_model.id} has no vision capability"
            )
            return

        effective_cache = cache or VisionDescriptionCache()
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            replacement_content = []
            for part in content:
                if VisionPreprocessingUtils._is_image_url_part(part):
                    replacement_content.append(
                        await VisionPreprocessingUtils._image_part_to_text(
                            part,
                            main_model.provider,
                            vision_model,
                            llm_service,
                            effective_cache,
                        )
                    )
                else:
                    replacement_content.append(part)
            message["content"] = replacement_content

    @staticmethod
    def _resolve_vision_model(provider: str, vision_model_id: str) -> Any | None:
        registry = ModelRegistry.get_instance()
        model = registry.get_model(f"{provider}/{vision_model_id}")
        if not model:
            logger.warning(
                f"Vision preprocessing skipped because configured vision model was not found: {provider}/{vision_model_id}"
            )
            return None
        return model

    @staticmethod
    def _is_image_url_part(part: Any) -> bool:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            return False
        image_url = part.get("image_url")
        return isinstance(image_url, dict) and isinstance(image_url.get("url"), str)

    @staticmethod
    async def _image_part_to_text(
        part: dict[str, Any],
        provider: str,
        vision_model: Any,
        llm_service: Any,
        cache: VisionDescriptionCache,
    ) -> dict[str, str]:
        url = part["image_url"]["url"]
        fingerprint = fingerprint_image_url(url)
        cache_key = build_vision_cache_key(
            image_fingerprint=str(fingerprint["image_fingerprint"]),
            provider=provider,
            vision_model=vision_model.id,
        )
        cached = cache.get(cache_key)
        if cached and isinstance(cached.get("description"), str):
            description = cached["description"]
        else:
            description = await VisionPreprocessingUtils._describe_image(
                part, vision_model, llm_service
            )
            if description.strip():
                cache.set(
                    cache_key,
                    {
                        "image_fingerprint": fingerprint["image_fingerprint"],
                        "image_source_type": fingerprint["image_source_type"],
                        "image_mime_type": fingerprint["image_mime_type"],
                        "provider": provider,
                        "vision_model": vision_model.id,
                        "description": description,
                    },
                )
        return {
            "type": "text",
            "text": f"[Image description generated by {provider}/{vision_model.id}]\n{description}",
        }

    @staticmethod
    async def _describe_image(
        image_part: dict[str, Any], vision_model: Any, llm_service: Any
    ) -> str:
        vision_messages = [
            {
                "role": "user",
                "content": [
                    image_part,
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_DESCRIPTION_PROMPT},
                ],
            },
        ]
        return (
            await llm_service.process_message(
                vision_messages,
                temperature=0.7,
                model_id=vision_model.id,
            )
        ).strip()
