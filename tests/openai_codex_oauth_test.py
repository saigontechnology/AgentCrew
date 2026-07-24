import base64
import json
import time
from datetime import datetime

from AgentCrew.modules.openai_codex.oauth import OpenAICodexOAuth


def _jwt_with_exp(exp_seconds: int) -> str:
    header = (
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}')
        .rstrip(b"=")
        .decode("ascii")
    )
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp_seconds}).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{payload}.signature"


class TestOpenAICodexOAuth:
    def test_loads_legacy_nested_file(self, tmp_path):
        token_path = tmp_path / "auth.json"
        expires = int((time.time() + 3600) * 1000)
        token_path.write_text(
            json.dumps(
                {
                    "openai-codex": {
                        "type": "oauth",
                        "access": "legacy-access",
                        "refresh": "legacy-refresh",
                        "expires": expires,
                    }
                }
            ),
            encoding="utf-8",
        )

        oauth = OpenAICodexOAuth(token_path=str(token_path))

        assert oauth.access_token == "legacy-access"
        assert oauth._tokens["refresh"] == "legacy-refresh"
        assert oauth._tokens["expires"] == expires
        assert oauth.has_valid_tokens is True

    def test_loads_legacy_flat_file(self, tmp_path):
        token_path = tmp_path / "auth.json"
        expires = int((time.time() + 3600) * 1000)
        token_path.write_text(
            json.dumps(
                {
                    "type": "oauth",
                    "access": "flat-access",
                    "refresh": "flat-refresh",
                    "expires": expires,
                }
            ),
            encoding="utf-8",
        )

        oauth = OpenAICodexOAuth(token_path=str(token_path))

        assert oauth.access_token == "flat-access"
        assert oauth._tokens["refresh"] == "flat-refresh"
        assert oauth._tokens["expires"] == expires
        assert oauth.has_valid_tokens is True

    def test_loads_codex_cli_style_file(self, tmp_path):
        token_path = tmp_path / "auth.json"
        exp_seconds = int(time.time()) + 3600
        access_token = _jwt_with_exp(exp_seconds)
        token_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "last_refresh": 1712505600000,
                    "tokens": {
                        "access_token": access_token,
                        "refresh_token": "cli-refresh",
                        "id_token": "cli-id-token",
                        "account_id": "account-123",
                    },
                }
            ),
            encoding="utf-8",
        )

        oauth = OpenAICodexOAuth(token_path=str(token_path))

        assert oauth.access_token == access_token
        assert oauth._tokens["refresh"] == "cli-refresh"
        assert oauth._tokens["id_token"] == "cli-id-token"
        assert oauth._tokens["account_id"] == "account-123"
        assert oauth._tokens["auth_mode"] == "chatgpt"
        assert oauth._tokens["last_refresh"] == "2024-04-07T12:00:00Z"
        assert oauth._tokens["expires"] == exp_seconds * 1000
        assert oauth.has_valid_tokens is True

    def test_save_writes_codex_cli_style_shape(self, tmp_path):
        token_path = tmp_path / "auth.json"
        oauth = OpenAICodexOAuth(token_path=str(token_path))
        oauth._tokens = {
            "type": "oauth",
            "access": "new-access",
            "refresh": "new-refresh",
            "expires": int((time.time() + 3600) * 1000),
            "id_token": "new-id-token",
            "account_id": "account-456",
        }

        oauth._save_tokens()

        data = json.loads(token_path.read_text(encoding="utf-8"))
        assert data["auth_mode"] == "chatgpt"
        assert isinstance(data["last_refresh"], str)
        datetime.fromisoformat(data["last_refresh"])
        assert data["tokens"]["access_token"] == "new-access"
        assert data["tokens"]["refresh_token"] == "new-refresh"
        assert data["tokens"]["id_token"] == "new-id-token"
        assert data["tokens"]["account_id"] == "account-456"
        assert "openai-codex" not in data

    def test_save_migrates_legacy_nested_file_and_preserves_unrelated_keys(
        self, tmp_path
    ):
        token_path = tmp_path / "auth.json"
        token_path.write_text(
            json.dumps(
                {
                    "openai-codex": {
                        "type": "oauth",
                        "access": "legacy-access",
                        "refresh": "legacy-refresh",
                        "expires": int((time.time() + 3600) * 1000),
                    },
                    "custom": {"enabled": True},
                    "tokens": {
                        "custom_token_metadata": "keep-me",
                    },
                }
            ),
            encoding="utf-8",
        )

        oauth = OpenAICodexOAuth(token_path=str(token_path))
        oauth._tokens["access"] = "migrated-access"
        oauth._tokens["refresh"] = "migrated-refresh"
        oauth._save_tokens()

        data = json.loads(token_path.read_text(encoding="utf-8"))
        assert "openai-codex" not in data
        assert data["custom"] == {"enabled": True}
        assert data["auth_mode"] == "chatgpt"
        assert isinstance(data["last_refresh"], str)
        datetime.fromisoformat(data["last_refresh"])
        assert data["tokens"]["access_token"] == "migrated-access"
        assert data["tokens"]["refresh_token"] == "migrated-refresh"
        assert data["tokens"]["custom_token_metadata"] == "keep-me"

    def test_loads_codex_cli_file_with_legacy_numeric_last_refresh(self, tmp_path):
        token_path = tmp_path / "auth.json"
        exp_seconds = int(time.time()) + 3600
        access_token = _jwt_with_exp(exp_seconds)
        token_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "last_refresh": 1712505600000,
                    "tokens": {
                        "access_token": access_token,
                        "refresh_token": "cli-refresh",
                    },
                }
            ),
            encoding="utf-8",
        )

        oauth = OpenAICodexOAuth(token_path=str(token_path))

        assert oauth._tokens["last_refresh"] == "2024-04-07T12:00:00Z"
        assert oauth._tokens["expires"] == exp_seconds * 1000

    def test_loads_codex_cli_file_with_expires_in_fallback(self, tmp_path):
        token_path = tmp_path / "auth.json"
        token_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "access_token": "not-a-jwt",
                        "refresh_token": "cli-refresh",
                    },
                }
            ),
            encoding="utf-8",
        )

        oauth = OpenAICodexOAuth(token_path=str(token_path))

        assert oauth.access_token == "not-a-jwt"
        assert oauth._tokens["refresh"] == "cli-refresh"
        assert "expires" not in oauth._tokens
        assert oauth.has_valid_tokens is True
