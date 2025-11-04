from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from AgentCrew.modules.agents.base import MessageType
from loguru import logger
import tempfile
import os

from a2a.types import (
    CancelTaskResponse,
    JSONRPCError,
    GetTaskResponse,
    GetTaskSuccessResponse,
    JSONRPCErrorResponse,
    SendMessageResponse,
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    CancelTaskSuccessResponse,
    SetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigResponse,
    SendMessageSuccessResponse,
    Task,
    TaskStatus,
    TaskState,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    TaskNotFoundError,
    TaskNotCancelableError,
)

from AgentCrew.modules.agents import LocalAgent
from .adapters import (
    convert_a2a_message_to_agent,
    convert_agent_response_to_a2a_artifact,
    convert_agent_message_to_a2a,
)
from .common.server.task_manager import TaskManager

if TYPE_CHECKING:
    from typing import Any, AsyncIterable, Dict, Optional, Union
    from AgentCrew.modules.agents import AgentManager
    from a2a.types import (
        CancelTaskRequest,
        GetTaskPushNotificationConfigRequest,
        GetTaskRequest,
        SendMessageRequest,
        SendStreamingMessageRequest,
        SetTaskPushNotificationConfigRequest,
        TaskResubscriptionRequest,
        JSONRPCResponse,
    )


class AgentTaskManager(TaskManager):
    """Manages tasks for a specific agent"""

    def __init__(self, agent_name: str, agent_manager: AgentManager):
        self.agent_name = agent_name
        self.agent_manager = agent_manager
        self.tasks: Dict[str, Task] = {}
        self.task_history: Dict[str, list[Dict[str, Any]]] = {}
        self.streaming_tasks: Dict[str, asyncio.Queue] = {}
        self.file_handler = None

        self.agent = self.agent_manager.get_agent(self.agent_name)
        if self.agent is None or not isinstance(self.agent, LocalAgent):
            raise ValueError(f"Agent {agent_name} not found or is not a LocalAgent")

        self.memory_service = self.agent.services["memory"]

    async def on_send_message(
        self, request: SendMessageRequest | SendStreamingMessageRequest
    ) -> SendMessageResponse:
        """
        Handle message/send request for this agent.

        Args:
            request: The message request

        Returns:
            JSON-RPC response with task result
        """
        if not self.agent or not isinstance(self.agent, LocalAgent):
            return SendMessageResponse(
                root=JSONRPCErrorResponse(
                    id=request.id,
                    error=JSONRPCError(
                        code=-32001, message=f"Agent {self.agent_name} not found"
                    ),
                )
            )

        # Generate task ID from message
        task_id = (
            request.params.message.task_id
            or f"task_{request.params.message.message_id}"
        )

        if task_id not in self.tasks:
            # Create task with initial state
            task = Task(
                id=task_id,
                context_id=request.params.message.context_id or f"ctx_{task_id}",
                status=TaskStatus(
                    state=TaskState.working, timestamp=datetime.now().isoformat()
                ),
            )
            self.tasks[task.id] = task

        task = self.tasks[task_id]
        if task_id not in self.task_history:
            self.task_history[task_id] = []

        # Convert A2A message to SwissKnife format
        message = convert_a2a_message_to_agent(request.params.message)
        if next(
            (m for m in message.get("content", []) if m.get("type", "text") == "file"),
            None,
        ):
            from AgentCrew.modules.chat.file_handler import FileHandler

            new_parts = []
            if self.file_handler is None:
                self.file_handler = FileHandler()
            for part in message.get("content", []):
                if part.get("type") == "file":
                    temp_file = os.path.join(tempfile.gettempdir(), part["file_name"])
                    with open(temp_file, "wb") as f:
                        f.write(part["file_data"])
                    file_part = self.file_handler.process_file(temp_file)
                    if not file_part:
                        file_part = self.agent.format_message(
                            MessageType.FileContent, {"file_uri": temp_file}
                        )
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

        self.task_history[task_id].append(message)

        # Process with agent (non-blocking)
        asyncio.create_task(self._process_agent_task(self.agent, task))

        # Return initial task state
        return SendMessageResponse(
            root=SendMessageSuccessResponse(id=request.id, result=task)
        )

    async def on_send_message_streaming(
        self, request: SendStreamingMessageRequest
    ) -> Union[AsyncIterable[SendStreamingMessageResponse], JSONRPCResponse]:
        """
        Handle message/stream request for this agent.

        Args:
            request: The message request

        Yields:
            JSON-RPC responses with task updates
        """
        # Generate task ID from message
        task_id = (
            request.params.message.task_id
            or f"task_{request.params.message.message_id}"
        )

        # Create streaming queue
        queue = asyncio.Queue()
        self.streaming_tasks[task_id] = queue

        try:
            # Start the task
            response = await self.on_send_message(request)

            # If there was an error, yield it and stop
            if isinstance(response.root, JSONRPCErrorResponse):
                yield SendStreamingMessageResponse(root=response.root)
                return

            # Yield events from the queue
            while True:
                event = await queue.get()
                if event is None:  # End of stream
                    break
                yield SendStreamingMessageResponse(
                    root=SendStreamingMessageSuccessResponse(
                        id=request.id, result=event
                    )
                )

        finally:
            # Clean up
            self.streaming_tasks.pop(task_id, None)

    async def _process_agent_task(self, agent: LocalAgent, task: Task):
        """
        Process a task with the agent (background task).

        Args:
            agent: The agent to process the task
            message: The message to process
            task: The task object to update
        """
        try:
            artifacts = []
            if task.id not in self.task_history:
                raise ValueError("Task history is not existed")

            input_tokens = 0
            output_tokens = 0

            async def _process_task():
                # Process with agent

                # Create artifacts from response
                current_response = ""
                response_message = ""
                thinking_content = ""
                thinking_signature = ""
                tool_uses = []

                def process_result(_tool_uses, _input_tokens, _output_tokens):
                    nonlocal tool_uses, input_tokens, output_tokens
                    tool_uses = _tool_uses
                    input_tokens += _input_tokens
                    output_tokens += _output_tokens

                async for (
                    response_message,
                    chunk_text,
                    thinking_chunk,
                ) in agent.process_messages(
                    self.task_history[task.id], callback=process_result
                ):
                    # Update current response
                    if response_message:
                        current_response = response_message

                    # Update task status
                    task.status.state = TaskState.working
                    task.status.timestamp = datetime.now().isoformat()

                    # If this is a streaming task, send updates
                    if task.id in self.streaming_tasks:
                        queue = self.streaming_tasks[task.id]

                        # Send thinking update if available
                        if thinking_chunk:
                            think_text_chunk, signature = thinking_chunk
                            if think_text_chunk:
                                thinking_content += think_text_chunk
                                await queue.put(
                                    TaskStatusUpdateEvent(
                                        task_id=task.id,
                                        context_id=task.context_id,
                                        status=TaskStatus(
                                            state=TaskState.working,
                                            message=convert_agent_message_to_a2a(
                                                {
                                                    "role": "agent",
                                                    "content": think_text_chunk,
                                                },
                                                f"msg_thinking_{hash(think_text_chunk)}",
                                            ),
                                        ),
                                        final=False,
                                    )
                                )
                            if signature:
                                thinking_signature += signature

                        # Send chunk update
                        if chunk_text:
                            artifact = convert_agent_response_to_a2a_artifact(
                                chunk_text,
                                artifact_id=f"artifact_{task.id}_{len(artifacts)}",
                            )
                            await queue.put(
                                TaskArtifactUpdateEvent(
                                    task_id=task.id,
                                    context_id=task.context_id,
                                    artifact=artifact,
                                )
                            )

                if tool_uses and len(tool_uses) > 0:
                    if task.id in self.streaming_tasks:
                        queue = self.streaming_tasks[task.id]
                        artifact = convert_agent_response_to_a2a_artifact(
                            "",
                            artifact_id=f"artifact_{task.id}_{len(artifacts)}",
                            tool_uses=tool_uses,
                        )
                        await queue.put(
                            TaskArtifactUpdateEvent(
                                task_id=task.id,
                                context_id=task.context_id,
                                artifact=artifact,
                            )
                        )
                        # prevent the execute_tool_call take the control of event loop before queue has been process
                        await asyncio.sleep(0.7)

                    # Add thinking content as a separate message if available
                    thinking_data = (
                        (thinking_content, thinking_signature)
                        if thinking_content
                        else None
                    )
                    thinking_message = agent.format_message(
                        MessageType.Thinking, {"thinking": thinking_data}
                    )
                    if thinking_message:
                        self.task_history[task.id].append(thinking_message)

                    # Format assistant message with the response and tool uses
                    assistant_message = agent.format_message(
                        MessageType.Assistant,
                        {
                            "message": response_message,
                            "tool_uses": [
                                t for t in tool_uses if t["name"] != "transfer"
                            ],
                        },
                    )
                    if assistant_message:
                        self.task_history[task.id].append(assistant_message)

                    # Process each tool use
                    for tool_use in tool_uses:
                        try:
                            tool_result = await agent.execute_tool_call(
                                tool_use["name"],
                                tool_use["input"],
                            )

                            tool_result_message = agent.format_message(
                                MessageType.ToolResult,
                                {"tool_use": tool_use, "tool_result": tool_result},
                            )
                            if tool_result_message:
                                self.task_history[task.id].append(tool_result_message)

                        except Exception as e:
                            error_message = agent.format_message(
                                MessageType.ToolResult,
                                {
                                    "tool_use": tool_use,
                                    "tool_result": str(e),
                                    "is_error": True,
                                },
                            )
                            if error_message:
                                self.task_history[task.id].append(error_message)

                    return await _process_task()
                return current_response

            current_response = await _process_task()
            if current_response.strip():
                assistant_message = agent.format_message(
                    MessageType.Assistant,
                    {
                        "message": current_response,
                    },
                )
                if assistant_message:
                    self.task_history[task.id].append(assistant_message)
                user_message = (
                    self.task_history[task.id][0]
                    .get("content", [{}])[0]
                    .get("text", "")
                )
                self.memory_service.store_conversation(
                    user_message, current_response, self.agent_name
                )

            # Create artifact from final response
            artifact = convert_agent_response_to_a2a_artifact(
                current_response, artifact_id=f"artifact_{task.id}_final"
            )
            artifacts.append(artifact)

            # Update task with final state
            task.status.state = TaskState.completed
            task.status.timestamp = datetime.now().isoformat()
            task.artifacts = artifacts

            # If this is a streaming task, send final update
            if task.id in self.streaming_tasks:
                queue = self.streaming_tasks[task.id]

                # Send final status
                await queue.put(
                    TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.context_id,
                        status=task.status,
                        final=True,
                    )
                )

                # Mark queue as done
                await queue.put(None)

        except Exception as e:
            logger.error(str(e))
            # Handle errors
            task.status.state = TaskState.failed
            task.status.timestamp = datetime.now().isoformat()

            # If this is a streaming task, send error
            if task.id in self.streaming_tasks:
                queue = self.streaming_tasks[task.id]
                await queue.put(
                    TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.context_id,
                        status=task.status,
                        final=True,
                    )
                )
                await queue.put(None)

    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        """
        Handle tasks/get request for this agent.

        Args:
            request: The task request

        Returns:
            JSON-RPC response with task result
        """
        task_id = request.params.id
        if task_id not in self.tasks:
            return GetTaskResponse(
                root=JSONRPCErrorResponse(id=request.id, error=TaskNotFoundError())
            )

        return GetTaskResponse(
            root=GetTaskSuccessResponse(id=request.id, result=self.tasks[task_id])
        )

    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        """
        Handle tasks/cancel request for this agent.

        Args:
            request: The task request

        Returns:
            JSON-RPC response with task result
        """
        task_id = request.params.id
        if task_id not in self.tasks:
            return CancelTaskResponse(
                root=JSONRPCErrorResponse(id=request.id, error=TaskNotFoundError())
            )

        task = self.tasks[task_id]

        # Check if task can be canceled
        if task.status.state in [
            TaskState.completed,
            TaskState.failed,
            TaskState.canceled,
        ]:
            return CancelTaskResponse(
                root=JSONRPCErrorResponse(id=request.id, error=TaskNotCancelableError())
            )

        # Update task status
        task.status.state = TaskState.canceled
        task.status.timestamp = datetime.now().isoformat()

        # If this is a streaming task, send cancellation
        if task_id in self.streaming_tasks:
            queue = self.streaming_tasks[task_id]
            await queue.put(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=task.context_id,
                    status=task.status,
                    final=True,
                )
            )
            await queue.put(None)

        return CancelTaskResponse(
            root=CancelTaskSuccessResponse(id=request.id, result=task)
        )

    async def on_set_task_push_notification(
        self, request: SetTaskPushNotificationConfigRequest
    ) -> SetTaskPushNotificationConfigResponse:
        raise NotImplementedError("")

    async def on_get_task_push_notification(
        self, request: GetTaskPushNotificationConfigRequest
    ) -> GetTaskPushNotificationConfigResponse:
        raise NotImplementedError("")

    async def on_resubscribe_to_task(
        self, request: TaskResubscriptionRequest
    ) -> Union[AsyncIterable[SendStreamingMessageResponse], JSONRPCResponse]:
        raise NotImplementedError("")

    # Legacy methods for backward compatibility
    async def on_send_task(self, request: SendMessageRequest) -> SendMessageResponse:
        """Legacy method - delegates to on_send_message"""
        return await self.on_send_message(request)

    async def on_send_task_subscribe(
        self, request: SendStreamingMessageRequest
    ) -> Union[AsyncIterable[SendStreamingMessageResponse], JSONRPCResponse]:
        """Legacy method - delegates to on_send_message_streaming"""
        return await self.on_send_message_streaming(request)


class MultiAgentTaskManager:
    """Manages tasks for multiple agents"""

    def __init__(self, agent_manager: AgentManager):
        self.agent_manager = agent_manager
        self.agent_task_managers: Dict[str, AgentTaskManager] = {}

        # Initialize task managers for all agents
        for agent_name in agent_manager.agents:
            self.agent_task_managers[agent_name] = AgentTaskManager(
                agent_name, agent_manager
            )

    def get_task_manager(self, agent_name: str) -> Optional[AgentTaskManager]:
        """
        Get the task manager for a specific agent.

        Args:
            agent_name: Name of the agent

        Returns:
            The task manager if found, None otherwise
        """
        return self.agent_task_managers.get(agent_name)
