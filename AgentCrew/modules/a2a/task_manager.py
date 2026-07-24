from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from a2a.types import (
    CancelTaskResponse,
    CancelTaskSuccessResponse,
    GetTaskPushNotificationConfigResponse,
    GetTaskResponse,
    GetTaskSuccessResponse,
    JSONRPCError,
    JSONRPCErrorResponse,
    SendMessageResponse,
    SendMessageSuccessResponse,
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    SetTaskPushNotificationConfigResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from AgentCrew.modules.agents import LocalAgent
from AgentCrew.modules.agents.base import MessageType

from .adapters import convert_a2a_message_to_agent
from .common.server.task_manager import TaskManager
from .errors import A2AError
from .task_cancellation import TaskCancellationManager
from .task_execution import TaskExecutionEngine, ToolCallResult
from .task_interaction import TaskInteractionHandler
from .task_store import TaskStore
from .task_streaming import TaskStreamingManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterable
    from typing import Any

    from a2a.types import (
        CancelTaskRequest,
        GetTaskPushNotificationConfigRequest,
        GetTaskRequest,
        JSONRPCResponse,
        SendMessageRequest,
        SendStreamingMessageRequest,
        SetTaskPushNotificationConfigRequest,
        TaskNotCancelableError,
        TaskResubscriptionRequest,
    )

    from AgentCrew.modules.agents import AgentManager


TERMINAL_STATES = {TaskState.completed, TaskState.canceled, TaskState.failed}
INPUT_REQUIRED_STATES = {TaskState.input_required}


class AgentTaskManager(TaskManager):
    def __init__(self, agent_name: str, agent_manager: AgentManager, store: TaskStore):
        self.agent_name = agent_name
        self.agent_manager = agent_manager
        self.store = store
        self.file_handler = None

        self.agent = self.agent_manager.get_agent(self.agent_name)
        if self.agent is None or not isinstance(self.agent, LocalAgent):
            raise ValueError(f"Agent {agent_name} not found or is not a LocalAgent")

        self.cancellation = TaskCancellationManager()
        self.streaming = TaskStreamingManager(store)
        self.interaction = TaskInteractionHandler()
        self.execution = TaskExecutionEngine(
            store,
            self.streaming,
            self.cancellation,
            self.interaction,
        )

    def _is_terminal_state(self, state: TaskState) -> bool:
        return state in TERMINAL_STATES

    def _validate_task_not_terminal(
        self, task: Task, operation: str
    ) -> TaskNotCancelableError | None:
        if self._is_terminal_state(task.status.state):
            return A2AError.task_not_cancelable(task.id, task.status.state.value)
        return None

    def _extract_text_from_message(self, message: dict[str, Any]) -> str:
        content = message.get("content", [])
        if isinstance(content, str):
            return content
        text_parts = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        return " ".join(text_parts)

    async def on_send_message(
        self, request: SendMessageRequest | SendStreamingMessageRequest
    ) -> SendMessageResponse:
        if not self.agent or not isinstance(self.agent, LocalAgent):
            return SendMessageResponse(
                root=JSONRPCErrorResponse(
                    id=request.id,
                    error=JSONRPCError(
                        code=-32001, message=f"Agent {self.agent_name} not found"
                    ),
                )
            )

        task_id = (
            request.params.message.task_id
            or f"task_{request.params.message.message_id}"
        )

        existing_task = await self.store.get_task(task_id)
        if existing_task:
            error = self._validate_task_not_terminal(existing_task, "send message")
            if error:
                return SendMessageResponse(
                    root=JSONRPCErrorResponse(id=request.id, error=error)
                )

            if existing_task.status.state in INPUT_REQUIRED_STATES:
                message = convert_a2a_message_to_agent(request.params.message)
                user_response = self._extract_text_from_message(message)

                pending = await self.store.get_pending_tools(task_id)
                if pending:
                    ask_tool_use = pending["ask_tool_use"]
                    remaining_tools = pending["remaining_tools"]

                    tool_result = user_response
                    tool_result_message = self.agent.format_message(
                        MessageType.ToolResult,
                        {"tool_use": ask_tool_use, "tool_result": tool_result},
                    )
                    if tool_result_message:
                        await self.store.append_task_history_message(
                            existing_task.context_id, tool_result_message
                        )

                    if remaining_tools:
                        task_history = await self.store.get_task_history(
                            existing_task.context_id
                        )
                        for remaining_tool in remaining_tools:
                            result = await self.execution._execute_single_tool(
                                self.agent, existing_task, remaining_tool, task_history
                            )
                            if result == ToolCallResult.INPUT_REQUIRED:
                                new_remaining = remaining_tools[
                                    remaining_tools.index(remaining_tool) + 1 :
                                ]
                                await self.store.save_pending_tools(
                                    task_id, remaining_tool, new_remaining
                                )
                                return SendMessageResponse(
                                    root=SendMessageSuccessResponse(
                                        id=request.id, result=existing_task
                                    )
                                )

                    await self.store.clear_pending_tools(task_id)

                existing_task.status.state = TaskState.working
                existing_task.status.timestamp = datetime.now().isoformat()
                existing_task.status.message = None
                await self.store.save_task(existing_task)

                bg_task = asyncio.create_task(
                    self.execution.run(self.agent, existing_task)
                )
                self.cancellation.register(task_id, bg_task)

                return SendMessageResponse(
                    root=SendMessageSuccessResponse(id=request.id, result=existing_task)
                )

        task = existing_task
        if not task:
            task = Task(
                id=task_id,
                context_id=request.params.message.context_id or f"ctx_{task_id}",
                status=TaskStatus(
                    state=TaskState.working, timestamp=datetime.now().isoformat()
                ),
            )
            await self.store.save_task(task)

        if not await self.store.has_task_history(task.context_id):
            await self.store.save_task_history(task.context_id, [])

        message = convert_a2a_message_to_agent(request.params.message)
        if next(
            (m for m in message.get("content", []) if m.get("type", "text") == "file"),
            None,
        ):
            from AgentCrew.modules.utils.file_handler import FileHandler

            new_parts = []
            if self.file_handler is None:
                self.file_handler = FileHandler()
            for part in message.get("content", []):
                if part.get("type") == "file":
                    temp_file = os.path.join(tempfile.gettempdir(), part["file_name"])
                    with open(temp_file, "wb") as f:
                        f.write(part["file_data"])
                    file_part = await self.file_handler.async_process_file(temp_file)
                    if file_part:
                        new_parts.append(file_part)
                    else:
                        new_parts.append(
                            {
                                "type": "text",
                                "text": f"[Unsupported file: {part['file_name']}]",
                            }
                        )
                else:
                    new_parts.append(part)
            message["content"] = new_parts

        await self.store.append_task_history_message(task.context_id, message)

        bg_task = asyncio.create_task(self.execution.run(self.agent, task))
        self.cancellation.register(task_id, bg_task)

        return SendMessageResponse(
            root=SendMessageSuccessResponse(id=request.id, result=task)
        )

    async def on_send_message_streaming(
        self, request: SendStreamingMessageRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        task_id = (
            request.params.message.task_id
            or f"task_{request.params.message.message_id}"
        )

        queue = self.streaming.enable_streaming(task_id)

        try:
            response = await self.on_send_message(request)

            if isinstance(response.root, JSONRPCErrorResponse):
                yield SendStreamingMessageResponse(root=response.root)
                return

            while True:
                event = await queue.get()
                if event is None:
                    break
                yield SendStreamingMessageResponse(
                    root=SendStreamingMessageSuccessResponse(
                        id=request.id, result=event
                    )
                )
        finally:
            self.streaming.remove(task_id)

    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        task_id = request.params.id
        task = await self.store.get_task(task_id)
        if not task:
            return GetTaskResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=A2AError.task_not_found(task_id)
                )
            )
        return GetTaskResponse(root=GetTaskSuccessResponse(id=request.id, result=task))

    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        task_id = request.params.id
        task = await self.store.get_task(task_id)
        if not task:
            return CancelTaskResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=A2AError.task_not_found(task_id)
                )
            )

        error = self._validate_task_not_terminal(task, "cancel")
        if error:
            return CancelTaskResponse(
                root=JSONRPCErrorResponse(id=request.id, error=error)
            )

        self.cancellation.signal_cancel(task_id)

        task.status.state = TaskState.canceled
        task.status.timestamp = datetime.now().isoformat()
        await self.store.save_task(task)

        await self.streaming.signal_cancel(task_id, task)

        return CancelTaskResponse(
            root=CancelTaskSuccessResponse(id=request.id, result=task)
        )

    async def on_set_task_push_notification(
        self, request: SetTaskPushNotificationConfigRequest
    ) -> SetTaskPushNotificationConfigResponse:
        return SetTaskPushNotificationConfigResponse(
            root=JSONRPCErrorResponse(
                id=request.id, error=A2AError.push_notification_not_supported()
            )
        )

    async def on_get_task_push_notification(
        self, request: GetTaskPushNotificationConfigRequest
    ) -> GetTaskPushNotificationConfigResponse:
        return GetTaskPushNotificationConfigResponse(
            root=JSONRPCErrorResponse(
                id=request.id, error=A2AError.push_notification_not_supported()
            )
        )

    async def on_resubscribe_to_task(
        self, request: TaskResubscriptionRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        task_id = request.params.id

        task = await self.store.get_task(task_id)
        if not task:
            yield SendStreamingMessageResponse(
                root=JSONRPCErrorResponse(
                    id=request.id, error=A2AError.task_not_found(task_id)
                )
            )
            return

        await self.streaming.flush_task_events(task_id)
        stored_events = await self.store.get_task_events(task_id)
        for event in stored_events:
            yield SendStreamingMessageResponse(
                root=SendStreamingMessageSuccessResponse(id=request.id, result=event)
            )

        if self._is_terminal_state(task.status.state):
            return

        if task.status.state in INPUT_REQUIRED_STATES:
            return

        if not self.streaming.is_streaming_enabled(task_id):
            yield SendStreamingMessageResponse(
                root=JSONRPCErrorResponse(
                    id=request.id,
                    error=A2AError.unsupported_operation(
                        "Task was not created with streaming enabled"
                    ),
                )
            )
            return

        resubscribe_key = f"{task_id}_resub_{request.id}"
        queue = self.streaming.register_subscriber(resubscribe_key)

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break

                yield SendStreamingMessageResponse(
                    root=SendStreamingMessageSuccessResponse(
                        id=request.id, result=event
                    )
                )

                if isinstance(event, TaskStatusUpdateEvent):
                    if self._is_terminal_state(event.status.state):
                        break
        finally:
            self.streaming.remove_subscriber(resubscribe_key)

    async def on_send_task(self, request: SendMessageRequest) -> SendMessageResponse:
        return await self.on_send_message(request)

    def _is_expired(self, timestamp: str, retention: timedelta) -> bool:
        try:
            ts = datetime.fromisoformat(timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return datetime.now(UTC) - ts > retention
        except (ValueError, TypeError):
            return True

    async def cleanup_terminal_tasks(
        self, retention: timedelta = timedelta(hours=24)
    ) -> None:
        task_ids = await self.store.list_task_ids()
        for task_id in task_ids:
            task = await self.store.get_task(task_id)
            if task and self._is_terminal_state(task.status.state):
                if self._is_expired(task.status.timestamp or "", retention):
                    await self.store.cleanup_task(task_id)
                    await self.streaming.cleanup(task_id)

    async def on_send_task_subscribe(
        self, request: SendStreamingMessageRequest
    ) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse:
        return await self.on_send_message_streaming(request)


class MultiAgentTaskManager:
    def __init__(
        self,
        agent_manager: AgentManager,
        store_type: str = "memory",
        store_options: dict[str, Any] | None = None,
    ):
        from .task_store import create_task_store

        self.agent_manager = agent_manager
        self.agent_task_managers: dict[str, AgentTaskManager] = {}

        for agent_name in agent_manager.agents:
            store = create_task_store(store_type, **(store_options or {}))
            self.agent_task_managers[agent_name] = AgentTaskManager(
                agent_name, agent_manager, store
            )

    def get_task_manager(self, agent_name: str) -> AgentTaskManager | None:
        return self.agent_task_managers.get(agent_name)

    async def initialize(self) -> None:
        for manager in self.agent_task_managers.values():
            await manager.cleanup_terminal_tasks()

    async def close(self) -> None:
        for manager in self.agent_task_managers.values():
            await manager.store.close()
