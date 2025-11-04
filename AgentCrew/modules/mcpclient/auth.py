import asyncio
import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread, Lock
from urllib.parse import parse_qs, urlparse
from typing import Optional, Dict
from pydantic import AnyUrl
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from loguru import logger


class FileTokenStorage(TokenStorage):
    """File-based token storage implementation using AGENTCREW_PERSISTENCE_DIR."""

    def __init__(self, server_name: str):
        """
        Initialize file-based token storage.

        Args:
            server_name: Name of the MCP server (used for filename)
        """
        self.server_name = server_name
        self._tokens: OAuthToken | None = None
        self._client_info: OAuthClientInformationFull | None = None
        self._tokens_loaded = False
        self._client_info_loaded = False

        # Determine base persistence directory
        env_dir = os.getenv("AGENTCREW_PERSISTENCE_DIR")
        if env_dir:
            base_dir = os.path.abspath(os.path.expanduser(env_dir))
        else:
            base_dir = os.path.abspath(os.path.expanduser("./persistents"))

        # Create tokens directory path
        self.tokens_dir = Path(base_dir) / "tokens"
        self.token_file = self.tokens_dir / f"{server_name}.json"

        # Ensure the tokens directory exists
        self._ensure_dir()

        logger.info(
            f"FileTokenStorage initialized for server '{server_name}' at: {self.token_file}"
        )

    def _ensure_dir(self):
        """Ensure the tokens directory exists."""
        try:
            self.tokens_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create tokens directory {self.tokens_dir}: {e}")
            raise

    async def get_tokens(self) -> OAuthToken | None:
        """Get stored tokens from file."""
        if not self._tokens_loaded:
            self._load_from_file()
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Store tokens to file."""
        self._tokens = tokens
        self._save_to_file()
        logger.info(f"Tokens saved for server '{self.server_name}'")

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Get stored client information from file."""
        if not self._client_info_loaded:
            self._load_from_file()
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Store client information to file."""
        self._client_info = client_info
        self._save_to_file()
        logger.info(f"Client info saved for server '{self.server_name}'")

    def _load_from_file(self):
        """Load tokens and client info from file."""
        if not self.token_file.exists():
            logger.info(f"No existing token file found for server '{self.server_name}'")
            self._tokens = None
            self._client_info = None
            self._tokens_loaded = True
            self._client_info_loaded = True
            return

        try:
            with open(self.token_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Load tokens if present
            if "tokens" in data and data["tokens"]:
                self._tokens = OAuthToken.model_validate_json(data["tokens"])
            else:
                self._tokens = None

            # Load client info if present
            if "client_info" in data and data["client_info"]:
                self._client_info = OAuthClientInformationFull.model_validate_json(
                    data["client_info"]
                )
            else:
                self._client_info = None

            self._tokens_loaded = True
            self._client_info_loaded = True
            logger.info(f"Tokens loaded from file for server '{self.server_name}'")

        except Exception as e:
            logger.error(f"Failed to load tokens from {self.token_file}: {e}")
            self._tokens = None
            self._client_info = None
            self._tokens_loaded = True
            self._client_info_loaded = True

    def _save_to_file(self):
        """Save tokens and client info to file."""
        try:
            data = {
                "tokens": self._tokens.model_dump_json() if self._tokens else None,
                "client_info": self._client_info.model_dump_json()
                if self._client_info
                else None,
            }

            # Write to temporary file first, then rename for atomicity
            temp_file = self.token_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Atomic rename
            temp_file.replace(self.token_file)
            logger.info(f"Tokens saved to file for server '{self.server_name}'")

        except Exception as e:
            logger.error(f"Failed to save tokens to {self.token_file}: {e}")
            raise


class OAuthCallbackServer:
    """
    Manages OAuth callback server lifecycle and state.

    This class encapsulates all state needed for the OAuth callback flow,
    eliminating the need for global variables.
    """

    def __init__(self, host: str = "localhost", port: int = 3000):
        self.host = host
        self.port = port
        self.result: Dict[str, Optional[str]] = {}
        self.result_lock = Lock()
        self.server: Optional[HTTPServer] = None
        self.server_thread: Optional[Thread] = None

    def get_callback_url(self) -> str:
        """Get the callback URL for this server."""
        return f"http://{self.host}:{self.port}/callback"

    def set_result(
        self, code: Optional[str], state: Optional[str], error: Optional[str]
    ):
        """
        Thread-safe method to set the callback result.

        Args:
            code: Authorization code from OAuth provider
            state: State parameter from OAuth provider
            error: Error message if authorization failed
        """
        with self.result_lock:
            self.result = {"code": code, "state": state, "error": error}
            logger.info(
                f"OAuth callback result set: error={error}, has_code={code is not None}"
            )

    def get_result(self) -> Dict[str, Optional[str]]:
        """
        Thread-safe method to get the callback result.

        Returns:
            Dictionary with code, state, and error keys
        """
        with self.result_lock:
            return self.result.copy()

    def has_result(self) -> bool:
        """Check if a result has been received."""
        with self.result_lock:
            return bool(self.result)

    def create_handler_class(self):
        """
        Create a request handler class bound to this server instance.

        Returns:
            Request handler class that can access this server's state
        """
        callback_server = self

        class OAuthCallbackHandler(BaseHTTPRequestHandler):
            """HTTP request handler for OAuth callback."""

            def log_message(self, format, *args):
                """Suppress HTTP server logs."""
                pass

            def do_GET(self):
                """Handle GET request from OAuth callback."""
                parsed_url = urlparse(self.path)
                params = parse_qs(parsed_url.query)

                code = params.get("code", [None])[0]
                state = params.get("state", [None])[0]
                error = params.get("error", [None])[0]

                callback_server.set_result(code, state, error)

                if error:
                    self._send_error_response(error)
                elif code:
                    self._send_success_response()
                else:
                    self._send_no_code_response()

            def _send_error_response(self, error: str):
                """Send error response to browser."""
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"""
                    <html>
                    <head><title>OAuth Error</title></head>
                    <body>
                        <h1>Authorization Failed</h1>
                        <p>Error: {error}</p>
                        <p>You can close this window now.</p>
                    </body>
                    </html>
                    """.encode()
                )

            def _send_success_response(self):
                """Send success response to browser."""
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"""
                    <html>
                    <head><title>OAuth Success</title></head>
                    <body>
                        <h1>Authorization Successful</h1>
                        <p>You can close this window now and return to the application.</p>
                        <script>window.close();</script>
                    </body>
                    </html>
                    """
                )

            def _send_no_code_response(self):
                """Send response when no code is received."""
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"""
                    <html>
                    <head><title>OAuth Error</title></head>
                    <body>
                        <h1>Authorization Failed</h1>
                        <p>No authorization code received.</p>
                        <p>You can close this window now.</p>
                    </body>
                    </html>
                    """
                )

        return OAuthCallbackHandler

    def start(self):
        """Start the HTTP server in a background thread."""
        logger.info(f"Starting OAuth callback server on {self.host}:{self.port}")

        handler_class = self.create_handler_class()
        self.server = HTTPServer((self.host, self.port), handler_class)

        self.server_thread = Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        logger.info("OAuth callback server started")

    def stop(self):
        """Stop the HTTP server and wait for the thread to finish."""
        if self.server:
            logger.info("Shutting down OAuth callback server...")
            self.server.shutdown()
            self.server.server_close()

        if self.server_thread:
            self.server_thread.join(timeout=2.0)

        logger.info("OAuth callback server stopped")

    async def wait_for_callback(self, timeout: float = 300) -> tuple[str, str | None]:
        """
        Wait for OAuth callback to be received.

        Args:
            timeout: Maximum time to wait in seconds (default: 5 minutes)

        Returns:
            tuple: (authorization_code, state)

        Raises:
            Exception: If callback fails or times out
        """
        poll_interval = 0.3  # 100ms
        elapsed = 0

        while elapsed < timeout:
            if self.has_result():
                break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if not self.has_result():
            logger.error("OAuth callback timeout - no response received")
            raise Exception(
                f"OAuth authorization timeout. Please complete authorization within {timeout / 60:.1f} minutes."
            )

        result = self.get_result()

        if result.get("error"):
            error = result.get("error")
            raise Exception(f"OAuth authorization failed: {error}")

        code = result.get("code")
        state = result.get("state")

        if not code:
            raise Exception("No authorization code received from OAuth callback")

        logger.info("OAuth callback successful, authorization code received")
        return code, state


class OAuthClientResolver:
    def __init__(self, port: Optional[int] = 14142):
        self.port = port if port else 14142

    async def handle_redirect(self, auth_url: str) -> None:
        """
        Handle OAuth redirect by opening the authorization URL in the user's browser.

        Args:
            auth_url: The OAuth authorization URL to open
        """
        logger.info(f"Opening OAuth authorization URL: {auth_url}")
        print(
            f"🔐 Opening browser for OAuth authorization...\nIf the browser doesn't open automatically, visit: {auth_url}\n"
        )

        # Open URL in the default browser
        try:
            webbrowser.open(auth_url)
        except Exception as e:
            logger.error(f"Failed to open browser: {e}")
            print(
                "⚠️ Failed to open browser automatically. Please visit the URL manually."
            )

    async def handle_callback(self) -> tuple[str, str | None]:
        """
        Handle OAuth callback by starting a temporary local HTTP server.

        This function creates a callback server, waits for the OAuth callback,
        and returns the authorization code and state.

        Returns:
            tuple: (authorization_code, state)

        Raises:
            Exception: If the callback fails or times out
        """
        # Create callback server instance
        callback_server = OAuthCallbackServer(host="localhost", port=self.port)

        logger.info(
            f"⏳ Waiting for OAuth callback on {callback_server.get_callback_url()}..."
        )

        try:
            callback_server.start()

            code, state = await callback_server.wait_for_callback(timeout=600)

            print("✅ Authorization successful!\n")
            return code, state

        except Exception as e:
            logger.error(f"OAuth callback failed: {e}")
            raise
        finally:
            callback_server.stop()

    def get_oauth_client_provider(
        self, mcp_url: str, tokens_storage: TokenStorage
    ) -> OAuthClientProvider:
        """
        Create and configure an OAuth client provider for MCP.

        Args:
            mcp_url: The MCP server URL

        Returns:
            OAuthClientProvider: Configured OAuth provider
        """
        oauth_auth = OAuthClientProvider(
            server_url=mcp_url,
            client_metadata=OAuthClientMetadata(
                client_name="AgentCrew MCP Client",
                redirect_uris=[AnyUrl(f"http://localhost:{self.port}/callback")],
                token_endpoint_auth_method="client_secret_post",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope="user",
            ),
            storage=tokens_storage,
            redirect_handler=self.handle_redirect,
            callback_handler=self.handle_callback,
        )
        return oauth_auth
