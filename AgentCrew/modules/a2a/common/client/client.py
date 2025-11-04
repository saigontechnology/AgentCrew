from __future__ import annotations

import json

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx

from httpx_sse import aconnect_sse

from a2a.types import (
    A2ARequest,
    CancelTaskResponse,
    GetTaskPushNotificationConfigResponse,
    GetTaskResponse,
    SendMessageResponse,
    TaskPushNotificationConfig,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigResponse,
)

if TYPE_CHECKING:
    from typing import Any, Dict, Optional
    from httpx._types import TimeoutTypes
    from a2a.types import (
        AgentCard,
        CancelTaskRequest,
        GetTaskPushNotificationConfigRequest,
        GetTaskRequest,
        MessageSendParams,
        SendMessageRequest,
        SendStreamingMessageRequest,
        SetTaskPushNotificationConfigRequest,
        TaskIdParams,
        TaskQueryParams,
    )


class A2AClient:
    def __init__(
        self,
        agent_card: AgentCard,
        url: Optional[str] = None,
        timeout: TimeoutTypes = 60.0,
        headers: Optional[Dict[str, str]] = None,
    ):
        if agent_card:
            self.url = agent_card.url
        ##Override URL if provided
        elif url:
            self.url = url
        else:
            raise ValueError("Must provide either agent_card or url")
        self.timeout = timeout
        self.headers = headers or {}

    async def send_message(self, payload: MessageSendParams) -> SendMessageResponse:
        """Send a message using the new A2A v0.2 message/send method"""
        request = SendMessageRequest(id=str(uuid4()), params=payload)
        result = await self._send_request(A2ARequest(root=request))
        return SendMessageResponse.model_validate(result)

    async def send_message_streaming(
        self, payload: MessageSendParams
    ) -> AsyncGenerator[SendStreamingMessageResponse]:
        """Send a streaming message using the new A2A v0.2 message/stream method"""
        request = SendStreamingMessageRequest(id=str(uuid4()), params=payload)
        # Merge custom headers with default headers
        request_headers = {"Content-Type": "application/json", **self.headers}

        async with httpx.AsyncClient(timeout=None) as client:
            async with aconnect_sse(
                client,
                "POST",
                self.url,
                json=request.model_dump(),
                headers=request_headers,
            ) as event_source:
                try:
                    async for sse in event_source.aiter_sse():
                        yield SendStreamingMessageResponse.model_validate(
                            json.loads(sse.data)
                        )
                except json.JSONDecodeError as e:
                    raise httpx.DecodingError(str(e)) from e

    async def _send_request(self, request: A2ARequest) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            try:
                # Merge custom headers with default headers
                request_headers = {"Content-Type": "application/json", **self.headers}

                # Image generation could take time, adding timeout
                response = await client.post(
                    self.url,
                    json=request.root.model_dump(),
                    timeout=self.timeout,
                    headers=request_headers,
                )
                response.raise_for_status()
                return response.json()
            except json.JSONDecodeError as e:
                raise httpx.DecodingError(str(e)) from e

    async def get_task(self, payload: TaskQueryParams) -> GetTaskResponse:
        request = GetTaskRequest(id=str(uuid4()), params=payload)
        result = await self._send_request(A2ARequest(root=request))
        return GetTaskResponse.model_validate(result)

    async def cancel_task(self, payload: TaskIdParams) -> CancelTaskResponse:
        request = CancelTaskRequest(id=str(uuid4()), params=payload)
        result = await self._send_request(A2ARequest(root=request))
        return CancelTaskResponse.model_validate(result)

    async def set_task_push_notification_config(
        self, payload: dict[str, Any]
    ) -> SetTaskPushNotificationConfigResponse:
        request = SetTaskPushNotificationConfigRequest(
            id=str(uuid4()), params=TaskPushNotificationConfig(**payload)
        )
        result = await self._send_request(A2ARequest(root=request))
        return SetTaskPushNotificationConfigResponse.model_validate(result)

    async def get_task_push_notification_config(
        self, payload: dict[str, Any]
    ) -> GetTaskPushNotificationConfigResponse:
        request = GetTaskPushNotificationConfigRequest(
            id=str(uuid4()), params=TaskIdParams(**payload)
        )
        result = await self._send_request(A2ARequest(root=request))
        return GetTaskPushNotificationConfigResponse.model_validate(result)
