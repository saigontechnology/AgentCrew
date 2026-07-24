from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_SESSION_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass
class AcpStoredSession:
    session_id: str
    cwd: str
    agent_name: str
    history: list[dict[str, Any]] = field(default_factory=list)
    title: str | None = None
    model_id: str | None = None
    thought_level: str | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: _utc_now())
    updated_at: str = field(default_factory=lambda: _utc_now())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcpStoredSession:
        now = _utc_now()
        raw_token_usage = data.get("token_usage") or {}
        return cls(
            session_id=str(data.get("session_id", "")),
            cwd=str(data.get("cwd", "")),
            agent_name=str(data.get("agent_name", "")),
            history=list(data.get("history") or []),
            title=data.get("title"),
            model_id=data.get("model_id"),
            thought_level=data.get("thought_level"),
            token_usage=raw_token_usage,
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AcpSessionStore:
    def __init__(self, base_dir: str | None = None):
        configured_dir = base_dir or os.getenv(
            "AGENTCREW_ACP_SESSION_DIR", ".agentcrew/acp_sessions"
        )
        self.base_dir = Path(configured_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    def _session_path(self, session_id: str) -> Path:
        return self.base_dir / f"{self._safe_session_id(session_id)}.json"

    def _safe_session_id(self, session_id: str) -> str:
        safe_id = _SAFE_SESSION_RE.sub("_", session_id).strip("._")
        return safe_id or "session"

    async def save_session(
        self,
        session_id: str,
        cwd: str,
        agent_name: str,
        history: list[dict[str, Any]],
        title: str | None = None,
        model_id: str | None = None,
        thought_level: str | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> AcpStoredSession:
        async with self.lock:
            existing = await self._read_session_unlocked(session_id)
            now = _utc_now()
            stored = AcpStoredSession(
                session_id=session_id,
                cwd=cwd,
                agent_name=agent_name,
                history=[dict(message) for message in history],
                title=title,
                model_id=model_id,
                thought_level=thought_level,
                token_usage=token_usage or {},
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            self._session_path(session_id).write_text(
                json.dumps(stored.to_dict(), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            return stored

    async def load_session(self, session_id: str) -> AcpStoredSession | None:
        async with self.lock:
            return await self._read_session_unlocked(session_id)

    async def list_sessions(self, cwd: str | None = None) -> list[AcpStoredSession]:
        normalized_cwd = os.path.abspath(os.path.expanduser(cwd)) if cwd else None
        async with self.lock:
            sessions: list[AcpStoredSession] = []
            for path in self.base_dir.glob("*.json"):
                try:
                    stored = AcpStoredSession.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                if normalized_cwd and stored.cwd != normalized_cwd:
                    continue
                sessions.append(stored)
            return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    async def delete_session(self, session_id: str) -> None:
        async with self.lock:
            path = self._session_path(session_id)
            if path.exists():
                path.unlink()

    async def fork_session(
        self,
        source_session_id: str,
        new_session_id: str,
        cwd: str | None = None,
        agent_name: str | None = None,
        title: str | None = None,
        model_id: str | None = None,
        thought_level: str | None = None,
    ) -> AcpStoredSession | None:
        async with self.lock:
            source = await self._read_session_unlocked(source_session_id)
            if source is None:
                return None
            now = _utc_now()
            forked = AcpStoredSession(
                session_id=new_session_id,
                cwd=cwd or source.cwd,
                agent_name=agent_name or source.agent_name,
                history=[dict(message) for message in source.history],
                title=title or source.title,
                model_id=model_id or source.model_id,
                thought_level=thought_level or source.thought_level,
                token_usage={},
                created_at=now,
                updated_at=now,
            )
            self._session_path(new_session_id).write_text(
                json.dumps(forked.to_dict(), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            return forked

    async def _read_session_unlocked(self, session_id: str) -> AcpStoredSession | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return AcpStoredSession.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
