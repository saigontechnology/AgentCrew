import asyncio
import copy
import uuid
from collections.abc import Callable
from typing import Any

from loguru import logger

from AgentCrew.modules.agents import AgentManager, run_agent_loop
from AgentCrew.modules.events import AppEvents, EventBus

DELEGATE_CLEANUP_TIMEOUT = 1.0
DELEGATE_PROGRESS_TIMEOUT = 0.25


class _DelegateProgressPublisher:
    """Publish one delegate's progress in order without blocking its execution."""

    def __init__(
        self, bus: EventBus, run_id: str, agent_name: str, timeout: float
    ) -> None:
        self.bus = bus
        self.run_id = run_id
        self.agent_name = agent_name
        self.timeout = timeout
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.finished = False

    def enqueue(self, kind: str, text: str) -> None:
        if self.finished:
            return
        self.queue.put_nowait((kind, text))
        if kind == "finish":
            self.finished = True
        if self.task is None:
            self.task = asyncio.create_task(self._publish())
            self.task.add_done_callback(_log_background_progress)

    async def _publish(self) -> None:
        while True:
            kind, text = await self.queue.get()
            try:
                dispatch = asyncio.create_task(
                    self.bus.emit(
                        AppEvents.DELEGATE_PROGRESS,
                        run_id=self.run_id,
                        agent_name=self.agent_name,
                        kind=kind,
                        text=text,
                    )
                )
                try:
                    done, _ = await asyncio.wait({dispatch}, timeout=self.timeout)
                    if not done:
                        dispatch.add_done_callback(_log_background_progress)
                        dispatch.cancel()
                        logger.warning(
                            "Delegate progress %s dispatch timed out after %s seconds",
                            kind,
                            self.timeout,
                        )
                    elif dispatch.cancelled():
                        logger.warning(
                            "Delegate progress %s dispatch was cancelled", kind
                        )
                    elif error := dispatch.exception():
                        logger.warning(
                            "Delegate progress %s dispatch failed: %s", kind, error
                        )
                except asyncio.CancelledError:
                    dispatch.add_done_callback(_log_background_progress)
                    dispatch.cancel()
                    raise
            except Exception:
                logger.exception("Delegate progress %s publication failed", kind)
            finally:
                self.queue.task_done()
            if kind == "finish":
                return


def _log_background_progress(task: asyncio.Task) -> None:
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.warning("Delegate progress task failed: %s", error)


def _log_background_cleanup(task: asyncio.Task) -> None:
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.warning("Delegate cleanup task failed: %s", error)


async def _bounded_delegate_cleanup(label: str, operation: Any) -> None:
    """Give each owned cleanup operation at most one second."""
    task = asyncio.create_task(operation)
    done, _ = await asyncio.wait({task}, timeout=DELEGATE_CLEANUP_TIMEOUT)
    if not done:
        task.add_done_callback(_log_background_cleanup)
        task.cancel()
        logger.warning(
            "Delegate %s cleanup timed out after %s seconds",
            label,
            DELEGATE_CLEANUP_TIMEOUT,
        )
    elif task.cancelled():
        logger.warning("Delegate %s cleanup was cancelled", label)
    elif error := task.exception():
        logger.warning("Delegate %s cleanup failed: %s", label, error)


class _ParentClarificationSession:
    """Private, tool-free source-agent context for one delegated run."""

    def __init__(self, source_agent: Any, delegation_message: str) -> None:
        self.source_agent = source_agent
        self.messages = copy.deepcopy(getattr(source_agent, "history", []))
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.get("role") == "assistant":
                calls = message.get("tool_calls") or []
                if calls:
                    call_ids = {call.get("id") for call in calls}
                    completed_ids = {
                        item.get("tool_call_id")
                        for item in self.messages[index + 1 :]
                        if item.get("role") == "tool"
                    }
                    if not call_ids <= completed_ids:
                        del self.messages[index:]
                break
        self.delegation_message = delegation_message
        self.llm = None
        self.lock = asyncio.Lock()

    async def answer(self, questions: list[dict[str, Any]]) -> str:
        """Ask the source agent's isolated model and preserve completed clarifications."""
        from AgentCrew.modules.llm.model_registry import ModelRegistry
        from AgentCrew.modules.llm.service_manager import ServiceManager

        async with self.lock:
            if self.llm is None:
                source = self.source_agent
                if not source or not getattr(source, "llm", None):
                    raise ValueError(
                        "Source agent LLM is unavailable for clarification"
                    )
                model = ModelRegistry.get_instance().get_model(source.get_model())
                service_name = (
                    model.resolved_service_name() if model else source.llm.provider_name
                )
                self.llm = ServiceManager.get_instance().initialize_standalone_service(
                    service_name
                )
                self.llm.model = source.llm.model
                self.llm.temperature = (
                    source.temperature if source.temperature is not None else 0.4
                )
                system_prompt = (
                    f"<Name>{source.name}</Name>\n"
                    f"<Description>{source.description}</Description>\n"
                    f"<Instructions>\n{source.get_system_prompt()}\n</Instructions>"
                )
                if source.custom_system_prompt:
                    system_prompt += f"\n\n{source.custom_system_prompt}"
                system_prompt += (
                    "\n\nAnswer the delegated agent's clarification using only the completed "
                    "source conversation and delegation task/context. Do not use tools, "
                    "delegate, or ask the user. If information is insufficient, say so explicitly."
                )
                self.llm.set_system_prompt(system_prompt)

            request = (
                f"Delegation task and context:\n{self.delegation_message}\n\n"
                f"Questions from delegated agent:\n{questions!r}\n\n"
                "Answer each question directly. If the available context is insufficient, "
                "say so explicitly; do not ask the user."
            )
            message = {"role": "user", "content": [{"type": "text", "text": request}]}
            response = await self.llm.process_message([*self.messages, message])
            if not isinstance(response, str) or not response.strip():
                raise ValueError("Source agent returned no clarification")
            self.messages.extend(
                [
                    message,
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": response}],
                    },
                ]
            )
            return response

    async def close(self) -> None:
        """Close the privately owned LLM, if a clarification was requested."""
        if self.llm is not None:
            await self.llm.close()


def get_delegate_tool_definition() -> dict[str, Any]:
    tool_description = (
        "Delegates a task to a specialized agent for independent execution. "
        "The target agent completes the task and returns the result. "
        "To delegate to MULTIPLE agents in parallel, call this tool multiple "
        "times with the parallel tool calls. all delegations will execute concurrently."
    )

    tool_arguments = {
        "target_agent": {
            "type": "string",
            "description": (
                "The name of the agent to delegate to. "
                "Refer to <Agents_Pool> for options."
            ),
        },
        "task_description": {
            "type": "string",
            "description": (
                "A clear, actionable description of what the target agent "
                "should accomplish. Start with an action verb. Include "
                "specific deliverables and constraints."
            ),
        },
        "context": {
            "type": "string",
            "description": (
                "Optional. Relevant information the target agent needs. "
                "Include only what's necessary for the agent starts fresh."
            ),
        },
    }

    tool_required = ["target_agent", "task_description"]

    return {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_delegate_tool_handler(agent_manager: AgentManager) -> Callable:
    async def handler(**params) -> str:
        from AgentCrew.modules.agents.local_agent import LocalAgent
        from AgentCrew.modules.llm.service_manager import (
            ServiceManager as LLMServiceManager,
        )

        target_agent_name = params.get("target_agent")
        task_description = params.get("task_description")
        context = params.get("context", "")

        if not target_agent_name:
            raise ValueError("Error: No target agent specified")

        if not task_description:
            raise ValueError("Error: No task description specified for delegation")

        from_agent_name = (
            agent_manager.current_agent.name
            if agent_manager.current_agent
            else "Unknown"
        )

        if target_agent_name not in agent_manager.agents:
            available_agents = ", ".join(agent_manager.agents.keys())
            raise ValueError(
                f"Error: Agent '{target_agent_name}' not found. Available agents: {available_agents}"
            )

        if target_agent_name == from_agent_name:
            raise ValueError("Error: Cannot delegate to yourself")

        target_agent = agent_manager.agents[target_agent_name]
        from AgentCrew.modules.agents.cursor_agent import (
            CursorAgent,
            _delegate_to_cursor,
        )

        if isinstance(target_agent, CursorAgent):
            import threading

            cancelled = threading.Event()
            run_holder: dict[str, Any] = {}
            publisher = _DelegateProgressPublisher(
                EventBus.get_instance(),
                uuid.uuid4().hex,
                target_agent_name,
                DELEGATE_PROGRESS_TIMEOUT,
            )
            publisher.enqueue("start", task_description)
            worker = asyncio.create_task(
                asyncio.to_thread(
                    _delegate_to_cursor,
                    from_agent_name,
                    target_agent,
                    task_description,
                    context,
                    cancelled,
                    run_holder,
                )
            )
            try:
                result = await asyncio.shield(worker)
                publisher.enqueue("response", result)
                return result
            except asyncio.CancelledError:
                cancelled.set()
                await asyncio.to_thread(CursorAgent._cancel_run, run_holder.get("run"))
                await asyncio.shield(worker)
                raise
            finally:
                publisher.enqueue("finish", "")
        target_agent = agent_manager.get_local_agent(target_agent_name)
        if not target_agent:
            raise ValueError(
                f"Error: Could not retrieve local agent '{target_agent_name}'"
            )

        delegation_message = (
            f"## Delegated Task from {from_agent_name}\n\n{task_description}\n"
        )
        if context:
            delegation_message += f"\n## Context\n{context}\n"

        delegation_message += (
            "\n## Instructions\n"
            "Complete the task above. Provide a thorough, complete response."
        )

        parent_session = _ParentClarificationSession(
            agent_manager.current_agent, delegation_message
        )
        llm_manager = LLMServiceManager.get_instance()
        from AgentCrew.modules.llm.model_registry import ModelRegistry

        registry = ModelRegistry.get_instance()
        model = registry.get_model(target_agent.get_model())
        if not target_agent.llm:
            raise ValueError("LLM of Agent not found")

        service_name = (
            model.resolved_service_name() if model else target_agent.llm.provider_name
        )
        fresh_llm = llm_manager.initialize_standalone_service(service_name)
        fresh_llm.model = target_agent.llm.model

        clone = None
        run_id = uuid.uuid4().hex
        publisher = _DelegateProgressPublisher(
            EventBus.get_instance(),
            run_id,
            target_agent_name,
            DELEGATE_PROGRESS_TIMEOUT,
        )

        async def progress(kind: str, text: str) -> None:
            publisher.enqueue(kind, text)

        async def cleanup() -> None:
            try:
                operations = (
                    ("agent deactivation", clone.deactivate_async if clone else None),
                    (
                        "parent LLM close",
                        parent_session.close
                        if parent_session.llm is not None
                        else None,
                    ),
                    ("LLM close", fresh_llm.close),
                )
                for label, operation in operations:
                    if operation is not None:
                        try:
                            await _bounded_delegate_cleanup(label, operation())
                        except Exception:
                            logger.exception("Delegate %s cleanup failed", label)
            finally:
                publisher.enqueue("finish", "")

        try:
            clone = LocalAgent(
                name=target_agent.name,
                description=target_agent.description,
                llm_service=fresh_llm,
                services=target_agent.services,
                tools=target_agent.tools,
                temperature=target_agent.temperature,
                excluded_tools={"delegate", "transfer"},
                ask_responder=parent_session.answer,
            )
            clone.set_system_prompt(target_agent.system_prompt or "")
            if target_agent.custom_system_prompt:
                clone.set_custom_system_prompt(target_agent.custom_system_prompt)
            await progress("start", task_description)
            await clone.activate_async()
            delegate_history = [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": delegation_message}],
                }
            ]

            response, _ = await run_agent_loop(
                agent=clone,
                history=delegate_history,
                request_usage_callback=target_agent.record_conversation_usage,
                progress_callback=progress,
            )

            return f"## Result from {target_agent_name}:\n\n{response}"

        finally:
            cleanup_task = asyncio.create_task(cleanup())
            cleanup_task.add_done_callback(_log_background_cleanup)
            await asyncio.shield(cleanup_task)

    return handler


def delegate_tool_prompt(agent_manager: AgentManager) -> str:
    return agent_manager.get_delegate_system_prompt()


def register(agent_manager, agent=None):
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_delegate_tool_definition, get_delegate_tool_handler, agent_manager, agent
    )
