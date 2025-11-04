from __future__ import annotations

import asyncio

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING

from .utils import new_not_implemented_error
from a2a.types import (
    CancelTaskResponse,
    GetTaskPushNotificationConfigResponse,
    GetTaskResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    SendStreamingMessageSuccessResponse,
    InternalError,
    JSONRPCError,
    GetTaskSuccessResponse,
    JSONRPCErrorResponse,
    JSONRPCResponse,
    SendMessageResponse,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigResponse,
    Task,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


from loguru import logger

if TYPE_CHECKING:
    from a2a.types import (
        Artifact,
        CancelTaskRequest,
        GetTaskPushNotificationConfigRequest,
        GetTaskRequest,
        MessageSendParams,
        PushNotificationConfig,
        SendMessageRequest,
        SendStreamingMessageRequest,
        SetTaskPushNotificationConfigRequest,
        TaskIdParams,
        TaskQueryParams,
        TaskResubscriptionRequest,
    )


class TaskManager(ABC):
    @abstractmethod
    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        pass

    @abstractmethod
    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        pass

    @abstractmethod
    async def on_send_message(self, request: SendMessageRequest) -> SendMessageResponse:
        pass

    @abstractmethod
    async def on_send_message_streaming(
        self, request: SendStreamingMessageRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        pass

    @abstractmethod
    async def on_set_task_push_notification(
        self, request: SetTaskPushNotificationConfigRequest
    ) -> SetTaskPushNotificationConfigResponse:
        pass

    @abstractmethod
    async def on_get_task_push_notification(
        self, request: GetTaskPushNotificationConfigRequest
    ) -> GetTaskPushNotificationConfigResponse:
        pass

    @abstractmethod
    async def on_resubscribe_to_task(
        self, request: TaskResubscriptionRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        pass

    # Legacy methods for backward compatibility
    async def on_send_task(self, request: SendMessageRequest) -> SendMessageResponse:
        """Legacy method - delegates to on_send_message"""
        return await self.on_send_message(request)

    async def on_send_task_subscribe(
        self, request: SendStreamingMessageRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        """Legacy method - delegates to on_send_message_streaming"""
        return await self.on_send_message_streaming(request)


class InMemoryTaskManager(TaskManager):
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.push_notification_infos: dict[str, PushNotificationConfig] = {}
        self.lock = asyncio.Lock()
        self.task_sse_subscribers: dict[str, list[asyncio.Queue]] = {}
        self.subscriber_lock = asyncio.Lock()

    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        logger.info(f"Getting task {request.params.id}")
        task_query_params: TaskQueryParams = request.params

        async with self.lock:
            task = self.tasks.get(task_query_params.id)
            if task is None:
                return GetTaskResponse(
                    root=JSONRPCErrorResponse(error=TaskNotFoundError(), id=request.id)
                )

            task_result = self.append_task_history(
                task, task_query_params.history_length
            )

        return GetTaskResponse(
            root=GetTaskSuccessResponse(result=task_result, id=request.id)
        )

    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        logger.info(f"Cancelling task {request.params.id}")
        task_id_params: TaskIdParams = request.params

        async with self.lock:
            task = self.tasks.get(task_id_params.id)
            if task is None:
                return CancelTaskResponse(
                    root=JSONRPCErrorResponse(error=TaskNotFoundError(), id=request.id)
                )

        return CancelTaskResponse(
            root=JSONRPCErrorResponse(error=TaskNotCancelableError(), id=request.id)
        )

    @abstractmethod
    async def on_send_message(self, request: SendMessageRequest) -> SendMessageResponse:
        pass

    @abstractmethod
    async def on_send_message_streaming(
        self, request: SendStreamingMessageRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        pass

    async def set_push_notification_info(
        self, task_id: str, notification_config: PushNotificationConfig
    ):
        async with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task not found for {task_id}")

            self.push_notification_infos[task_id] = notification_config

    async def get_push_notification_info(self, task_id: str) -> PushNotificationConfig:
        async with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task not found for {task_id}")

            return self.push_notification_infos[task_id]

    async def has_push_notification_info(self, task_id: str) -> bool:
        async with self.lock:
            return task_id in self.push_notification_infos

    async def on_set_task_push_notification(
        self, request: SetTaskPushNotificationConfigRequest
    ) -> SetTaskPushNotificationConfigResponse:
        logger.info(f"Setting task push notification {request.params.task_id}")
        task_notification_params: TaskPushNotificationConfig = request.params

        try:
            await self.set_push_notification_info(
                task_notification_params.task_id,
                task_notification_params.push_notification_config,
            )
        except Exception as e:
            logger.error(f"Error while setting push notification info: {e}")
            return SetTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=request.id,
                    error=InternalError(
                        message="An error occurred while setting push notification info"
                    ),
                )
            )

        return SetTaskPushNotificationConfigResponse(
            root=SetTaskPushNotificationConfigSuccessResponse(
                id=request.id, result=task_notification_params
            )
        )

    async def on_get_task_push_notification(
        self, request: GetTaskPushNotificationConfigRequest
    ) -> GetTaskPushNotificationConfigResponse:
        logger.info(f"Getting task push notification {request.params.id}")

        task_id = request.params.id

        try:
            notification_info = await self.get_push_notification_info(task_id)
        except Exception as e:
            logger.error(f"Error while getting push notification info: {e}")
            return GetTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=request.id,
                    error=InternalError(
                        message="An error occurred while getting push notification info"
                    ),
                )
            )

        return GetTaskPushNotificationConfigResponse(
            root=GetTaskPushNotificationConfigSuccessResponse(
                id=request.id,
                result=TaskPushNotificationConfig(
                    task_id=task_id, push_notification_config=notification_info
                ),
            )
        )

    async def upsert_task(self, message_send_params: MessageSendParams) -> Task:
        logger.info(
            f"Upserting task from message {message_send_params.message.message_id}"
        )
        async with self.lock:
            # Use taskId from message or generate one
            task_id = (
                message_send_params.message.task_id
                or f"task_{message_send_params.message.message_id}"
            )

            task = self.tasks.get(task_id)
            if task is None:
                task = Task(
                    id=task_id,
                    context_id=message_send_params.message.context_id
                    or f"ctx_{task_id}",
                    status=TaskStatus(state=TaskState.submitted),
                    history=[message_send_params.message],
                )
                self.tasks[task_id] = task
            else:
                if task.history is None:
                    task.history = []
                task.history.append(message_send_params.message)

            return task

    async def on_resubscribe_to_task(
        self, request: TaskResubscriptionRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        return new_not_implemented_error(request.id)

    async def update_store(
        self, task_id: str, status: TaskStatus, artifacts: list[Artifact]
    ) -> Task:
        async with self.lock:
            try:
                task = self.tasks[task_id]
            except KeyError:
                logger.error(f"Task {task_id} not found for updating the task")
                raise ValueError(f"Task {task_id} not found")

            task.status = status

            if status.message is not None:
                if task.history is None:
                    task.history = []
                task.history.append(status.message)

            if artifacts is not None:
                if task.artifacts is None:
                    task.artifacts = []
                task.artifacts.extend(artifacts)

            return task

    def append_task_history(self, task: Task, historyLength: int | None):
        new_task = task.model_copy()
        if historyLength is not None and historyLength > 0:
            if new_task.history:
                new_task.history = new_task.history[-historyLength:]
        else:
            new_task.history = []

        return new_task

    async def setup_sse_consumer(self, task_id: str, is_resubscribe: bool = False):
        async with self.subscriber_lock:
            if task_id not in self.task_sse_subscribers:
                if is_resubscribe:
                    raise ValueError("Task not found for resubscription")
                self.task_sse_subscribers[task_id] = []

            sse_event_queue = asyncio.Queue(maxsize=0)  # <=0 is unlimited
            self.task_sse_subscribers[task_id].append(sse_event_queue)
            return sse_event_queue

    async def enqueue_events_for_sse(self, task_id, task_update_event):
        async with self.subscriber_lock:
            if task_id not in self.task_sse_subscribers:
                return

            current_subscribers = self.task_sse_subscribers[task_id]
            for subscriber in current_subscribers:
                await subscriber.put(task_update_event)

    async def dequeue_events_for_sse(
        self, request_id, task_id, sse_event_queue: asyncio.Queue
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        try:
            while True:
                event = await sse_event_queue.get()
                if isinstance(event, JSONRPCError):
                    yield SendStreamingMessageResponse(
                        root=JSONRPCErrorResponse(id=request_id, error=event)
                    )
                    break

                yield SendStreamingMessageResponse(
                    root=SendStreamingMessageSuccessResponse(
                        id=request_id, result=event
                    )
                )
                if isinstance(event, TaskStatusUpdateEvent) and event.final:
                    break
        finally:
            async with self.subscriber_lock:
                if task_id in self.task_sse_subscribers:
                    self.task_sse_subscribers[task_id].remove(sse_event_queue)
