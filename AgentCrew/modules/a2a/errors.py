"""
A2A-specific error code helpers for proper error handling.

This module provides convenience functions for creating A2A-specific errors
with contextual data according to the A2A protocol v0.3.0 specification.

Error Codes (Section 8.2):
- -32001: TaskNotFoundError
- -32002: TaskNotCancelableError
- -32003: PushNotificationNotSupportedError
- -32004: UnsupportedOperationError
- -32005: ContentTypeNotSupportedError
- -32006: InvalidAgentResponseError
- -32007: AuthenticatedExtendedCardNotConfiguredError
"""

from a2a.types import (
    AuthenticatedExtendedCardNotConfiguredError,
    ContentTypeNotSupportedError,
    InvalidAgentResponseError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)


class A2AError:
    @staticmethod
    def task_not_found(task_id: str) -> TaskNotFoundError:
        error = TaskNotFoundError()
        error.data = {"task_id": task_id}
        return error

    @staticmethod
    def task_not_cancelable(task_id: str, current_state: str) -> TaskNotCancelableError:
        error = TaskNotCancelableError()
        error.data = {"task_id": task_id, "state": current_state}
        return error

    @staticmethod
    def push_notification_not_supported() -> PushNotificationNotSupportedError:
        return PushNotificationNotSupportedError()

    @staticmethod
    def unsupported_operation(operation: str) -> UnsupportedOperationError:
        error = UnsupportedOperationError()
        error.data = {"operation": operation}
        return error

    @staticmethod
    def content_type_not_supported(
        mime_type: str, supported_types: list[str] | None = None
    ) -> ContentTypeNotSupportedError:
        error = ContentTypeNotSupportedError()
        error.data = {"mime_type": mime_type}
        if supported_types:
            error.data["supported_types"] = supported_types
        return error

    @staticmethod
    def invalid_agent_response(details: str) -> InvalidAgentResponseError:
        error = InvalidAgentResponseError()
        error.data = {"details": details}
        return error

    @staticmethod
    def authenticated_extended_card_not_configured() -> (
        AuthenticatedExtendedCardNotConfiguredError
    ):
        return AuthenticatedExtendedCardNotConfiguredError()
