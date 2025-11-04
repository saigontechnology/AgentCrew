from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request
from typing import Optional
from loguru import logger


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware for A2A agent routes.
    Validates Bearer token in Authorization header.
    """

    def __init__(self, app, api_key: Optional[str] = None):
        super().__init__(app)
        self.api_key = api_key or "default-api-key"  # You can configure this
        logger.debug("AuthMiddleware initialized.")

    async def dispatch(self, request: Request, call_next):
        """
        Process the request and validate authentication.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or endpoint to call

        Returns:
            Response from the next handler or authentication error
        """
        # Get Authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return JSONResponse(
                {
                    "error": {
                        "code": -32001,
                        "message": "Authentication required",
                        "data": {"detail": "Missing Authorization header"},
                    }
                },
                status_code=401,
            )

        # Check Bearer token format
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {
                    "error": {
                        "code": -32001,
                        "message": "Authentication failed",
                        "data": {
                            "detail": "Authorization header must start with 'Bearer '"
                        },
                    }
                },
                status_code=401,
            )

        # Extract and validate API key
        token = auth_header[7:]  # Remove "Bearer " prefix
        if token != self.api_key:
            return JSONResponse(
                {
                    "error": {
                        "code": -32001,
                        "message": "Authentication failed",
                        "data": {"detail": "Invalid API key"},
                    }
                },
                status_code=401,
            )

        logger.debug("Authentication successful")
        # Authentication successful, proceed to next handler
        response = await call_next(request)
        return response
