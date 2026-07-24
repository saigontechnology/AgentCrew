import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from loguru import logger

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_ENDPOINT = "https://auth.openai.com/oauth/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
SCOPES = "openid profile email offline_access"
DEFAULT_TOKEN_PATH = os.path.expanduser("~/.codex/auth.json")
CALLBACK_HOST = "127.0.0.1"
CALLBACK_REDIRECT_HOST = "localhost"
CALLBACK_PORT_RANGE = (1455, 1475)
LEGACY_TOP_LEVEL_KEYS = {
    "openai-codex",
    "access",
    "refresh",
    "expires",
    "type",
    "id_token",
    "account_id",
}


def _generate_pkce_pair():
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _current_time_ms() -> int:
    return int(time.time() * 1000)


def _current_time_rfc3339() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_refresh_time(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        return (
            datetime.fromtimestamp(value / 1000, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            datetime.fromisoformat(stripped)
            return stripped.replace("+00:00", "Z")
        except ValueError:
            return None

    return None


def _decode_base64url_json(value: str) -> dict[str, Any] | None:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
        if isinstance(payload, dict):
            return payload
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def _derive_expires_from_access_token(access_token: str) -> int | None:
    if not isinstance(access_token, str):
        return None

    token_parts = access_token.split(".")
    if len(token_parts) != 3:
        return None

    payload = _decode_base64url_json(token_parts[1])
    if not payload:
        return None

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp > 0:
        return int(exp * 1000)

    return None


def _extract_account_id(data: dict[str, Any]) -> str | None:
    account_id = data.get("account_id")
    if isinstance(account_id, str) and account_id:
        return account_id

    account = data.get("account")
    if isinstance(account, dict):
        nested_account_id = account.get("id") or account.get("account_id")
        if isinstance(nested_account_id, str) and nested_account_id:
            return nested_account_id

    return None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None
    error: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path != "/auth/callback":
            self._send_response(
                "<html><body><h2>Waiting for authentication...</h2></body></html>"
            )
            return

        if "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            self._send_response(
                "<html><body><h2>Authentication successful!</h2>"
                "<p>You can close this window and return to AgentCrew.</p></body></html>"
            )
        elif "error" in params:
            _OAuthCallbackHandler.error = params.get(
                "error_description", params["error"]
            )[0]
            self._send_response(
                f"<html><body><h2>Authentication failed</h2>"
                f"<p>{_OAuthCallbackHandler.error}</p></body></html>"
            )
        else:
            self._send_response(
                "<html><body><h2>Unexpected response</h2></body></html>"
            )

        self.server.shutdown_event.set()

    def _send_response(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


class _ShutdownHTTPServer(HTTPServer):
    def __init__(self, *args, **kwargs):
        self.shutdown_event = Event()
        super().__init__(*args, **kwargs)


class OpenAICodexOAuth:
    def __init__(self, token_path: str | None = None):
        self.token_path = token_path or DEFAULT_TOKEN_PATH
        self._tokens: dict[str, Any] | None = None
        self._load_tokens()

    def _normalize_tokens(
        self,
        raw_tokens: dict[str, Any],
        *,
        auth_mode: Any | None = None,
        last_refresh: Any | None = None,
    ) -> dict[str, Any] | None:
        access_token = raw_tokens.get("access") or raw_tokens.get("access_token")
        refresh_token = (
            raw_tokens.get("refresh") or raw_tokens.get("refresh_token") or ""
        )

        if not access_token and not refresh_token:
            return None

        expires = raw_tokens.get("expires")
        if not isinstance(expires, (int, float)) or expires <= 0:
            expires = raw_tokens.get("expires_at")
        if not isinstance(expires, (int, float)) or expires <= 0:
            expires_in = raw_tokens.get("expires_in")
            if isinstance(expires_in, (int, float)) and expires_in > 0:
                expires = _current_time_ms() + int(expires_in * 1000)
        if not isinstance(expires, (int, float)) or expires <= 0:
            expires = _derive_expires_from_access_token(access_token)

        normalized = {
            "type": raw_tokens.get("type") or "oauth",
            "access": access_token,
            "refresh": refresh_token,
        }

        if isinstance(expires, (int, float)) and expires > 0:
            normalized["expires"] = int(expires)

        id_token = raw_tokens.get("id_token")
        if isinstance(id_token, str) and id_token:
            normalized["id_token"] = id_token

        account_id = raw_tokens.get("account_id")
        if isinstance(account_id, str) and account_id:
            normalized["account_id"] = account_id

        if isinstance(auth_mode, str) and auth_mode:
            normalized["auth_mode"] = auth_mode

        normalized_last_refresh = _normalize_refresh_time(last_refresh)
        if normalized_last_refresh:
            normalized["last_refresh"] = normalized_last_refresh

        return normalized

    def _load_tokens(self):
        if not os.path.exists(self.token_path):
            return

        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load OAuth tokens from {self.token_path}: {e}")
            self._tokens = None
            return

        if not isinstance(data, dict):
            self._tokens = None
            return

        if isinstance(data.get("tokens"), dict):
            self._tokens = self._normalize_tokens(
                data["tokens"],
                auth_mode=data.get("auth_mode"),
                last_refresh=data.get("last_refresh"),
            )
            if self._tokens:
                return

        legacy_nested = data.get("openai-codex")
        if isinstance(legacy_nested, dict):
            self._tokens = self._normalize_tokens(legacy_nested)
            if self._tokens:
                return

        if data.get("access") or data.get("refresh"):
            self._tokens = self._normalize_tokens(data)
            if self._tokens:
                return

        self._tokens = None

    def _save_tokens(self):
        if not self._tokens:
            return

        dir_path = os.path.dirname(self.token_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        existing: dict[str, Any] = {}
        if os.path.exists(self.token_path):
            try:
                with open(self.token_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError):
                existing = {}

        current_time_rfc3339 = _current_time_rfc3339()
        self._tokens["last_refresh"] = current_time_rfc3339
        self._tokens["auth_mode"] = "chatgpt"

        top_level = {
            key: value
            for key, value in existing.items()
            if key not in LEGACY_TOP_LEVEL_KEYS
            and key not in {"auth_mode", "last_refresh", "tokens"}
        }
        top_level["auth_mode"] = "chatgpt"
        top_level["last_refresh"] = current_time_rfc3339

        existing_tokens = existing.get("tokens")
        tokens_payload = (
            dict(existing_tokens) if isinstance(existing_tokens, dict) else {}
        )

        access_token = self._tokens.get("access")
        if isinstance(access_token, str) and access_token:
            tokens_payload["access_token"] = access_token

        refresh_token = self._tokens.get("refresh")
        if isinstance(refresh_token, str) and refresh_token:
            tokens_payload["refresh_token"] = refresh_token

        id_token = self._tokens.get("id_token")
        if isinstance(id_token, str) and id_token:
            tokens_payload["id_token"] = id_token

        account_id = self._tokens.get("account_id")
        if isinstance(account_id, str) and account_id:
            tokens_payload["account_id"] = account_id

        top_level["tokens"] = tokens_payload

        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(top_level, f, indent=2)
        os.chmod(self.token_path, 0o600)

    @property
    def has_valid_tokens(self) -> bool:
        if not self._tokens:
            return False
        expires = self._tokens.get("expires", 0)
        if isinstance(expires, (int, float)) and expires > 0:
            return time.time() * 1000 < expires - 60_000
        return bool(self._tokens.get("access"))

    @property
    def access_token(self) -> str | None:
        if self._tokens:
            return self._tokens.get("access")
        return None

    @property
    def account_id(self) -> str | None:
        """Return the ChatGPT account ID from stored tokens, if available."""
        if self._tokens:
            return self._tokens.get("account_id")
        return None

    def get_valid_access_token(self) -> str | None:
        if self.has_valid_tokens:
            return self.access_token
        if self._tokens and self._tokens.get("refresh") and self._refresh_token():
            return self.access_token
        return None

    def _update_tokens_from_response(self, data: dict[str, Any]):
        expires_in = data.get("expires_in", 3600)
        self._tokens = {
            "type": "oauth",
            "access": data["access_token"],
            "refresh": data.get("refresh_token")
            or (self._tokens or {}).get("refresh", ""),
            "expires": int((time.time() + expires_in) * 1000),
            "auth_mode": "chatgpt",
            "last_refresh": _current_time_rfc3339(),
        }

        id_token = data.get("id_token")
        if isinstance(id_token, str) and id_token:
            self._tokens["id_token"] = id_token
        elif self._tokens.get("id_token"):
            self._tokens["id_token"] = self._tokens["id_token"]

        account_id = _extract_account_id(data) or (self._tokens or {}).get("account_id")
        if isinstance(account_id, str) and account_id:
            self._tokens["account_id"] = account_id

    def _refresh_token(self) -> bool:
        if not self._tokens or not self._tokens.get("refresh"):
            return False
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "refresh_token": self._tokens["refresh"],
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code != 200:
                    logger.error(
                        f"Token refresh failed: {resp.status_code} {resp.text}"
                    )
                    return False
                data = resp.json()
                previous_tokens = dict(self._tokens)
                self._update_tokens_from_response(data)
                if "id_token" not in self._tokens and previous_tokens.get("id_token"):
                    self._tokens["id_token"] = previous_tokens["id_token"]
                if "account_id" not in self._tokens and previous_tokens.get(
                    "account_id"
                ):
                    self._tokens["account_id"] = previous_tokens["account_id"]
                self._save_tokens()
                logger.info("OAuth token refreshed successfully")
                return True
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return False

    @classmethod
    def is_available(cls) -> bool:
        """Check whether the user has valid Codex OAuth tokens on disk."""
        try:
            return cls().has_valid_tokens
        except Exception:
            return False

    @classmethod
    def get_auth(cls) -> dict[str, str] | None:
        """Return {access_token, account_id} dict if valid OAuth tokens exist."""
        try:
            oauth = cls()
            token = oauth.get_valid_access_token()
            if token:
                return {
                    "access_token": token,
                    "account_id": oauth.account_id or "",
                }
        except Exception:
            return None
        return None

    def login(self) -> bool:
        code_verifier, code_challenge = _generate_pkce_pair()

        server = None
        callback_port = None
        for port in range(CALLBACK_PORT_RANGE[0], CALLBACK_PORT_RANGE[1]):
            try:
                server = _ShutdownHTTPServer(
                    (CALLBACK_HOST, port), _OAuthCallbackHandler
                )
                callback_port = port
                break
            except OSError:
                continue

        if not server or callback_port is None:
            logger.error("Could not find available port for OAuth callback")
            return False

        redirect_uri = f"http://{CALLBACK_REDIRECT_HOST}:{callback_port}/auth/callback"

        _OAuthCallbackHandler.auth_code = None
        _OAuthCallbackHandler.error = None

        state = secrets.token_urlsafe(32)

        auth_params = urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": state,
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
            }
        )
        auth_url = f"{AUTH_ENDPOINT}?{auth_params}"

        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        logger.info("Opening browser for OpenAI authentication...")
        webbrowser.open(auth_url)

        server.shutdown_event.wait(timeout=300)
        server.shutdown()

        if _OAuthCallbackHandler.error:
            logger.error(f"OAuth error: {_OAuthCallbackHandler.error}")
            return False

        if not _OAuthCallbackHandler.auth_code:
            logger.error("OAuth flow timed out or no authorization code received")
            return False

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": "authorization_code",
                        "client_id": CLIENT_ID,
                        "code": _OAuthCallbackHandler.auth_code,
                        "redirect_uri": redirect_uri,
                        "code_verifier": code_verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code != 200:
                    logger.error(
                        f"Token exchange failed: {resp.status_code} {resp.text}"
                    )
                    return False
                data = resp.json()
                self._update_tokens_from_response(data)
                self._save_tokens()
                logger.info("OpenAI Codex OAuth authentication successful")
                return True
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            return False
