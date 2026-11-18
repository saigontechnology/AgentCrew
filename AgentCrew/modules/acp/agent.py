from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from acp import Agent, RequestError
from loguru import logger

if TYPE_CHECKING:
    from acp import Client

    from AgentCrew.modules.agents import AgentManager

from AgentCrew.modules.acp.client_communication import ClientCommunication
from AgentCrew.modules.acp.mcp_orchestrator import McpOrchestrator
from AgentCrew.modules.acp.model_controller import ModelController
from AgentCrew.modules.acp.session_lifecycle import SessionLifecycle
from AgentCrew.modules.acp.session_state import AcpSessionState
from AgentCrew.modules.acp.session_store import AcpSessionStore
from AgentCrew.modules.acp.tool_manager import AcpToolManager
from AgentCrew.modules.acp.tools.context import (
    AcpSessionContext,
    _current_acp_session,
)
from AgentCrew.modules.acp.turn_executor import TurnExecutor
from AgentCrew.modules.llm.token_usage import TokenUsage


class AgentCrewAcpAgent(Agent):
    def __init__(
        self,
        agent_manager: AgentManager,
        default_agent_name: str | None = None,
        session_store: AcpSessionStore | None = None,
    ):
        self.agent_manager: AgentManager = agent_manager
        self.default_agent_name: str | None = default_agent_name
        self.session_store: AcpSessionStore = session_store or AcpSessionStore()
        self._conn: Client | None = None
        self._client_comm = ClientCommunication()
        self._mcp_orchestrator = McpOrchestrator(agent_manager)
        self._tool_manager = AcpToolManager(agent_manager)
        self._sessions: dict[str, AcpSessionState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_requests: dict[asyncio.Task, str | None] = {}
        self._session_closes: dict[str, asyncio.Task] = {}
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._model_controller = ModelController(
            agent_manager=agent_manager,
            tool_manager=self._tool_manager,
            mcp_orchestrator=self._mcp_orchestrator,
        )
        self._turn_executor = TurnExecutor(
            client_comm=self._client_comm,
            tool_manager=self._tool_manager,
        )
        self._session_lifecycle = SessionLifecycle(
            session_store=self.session_store,
            client_comm=self._client_comm,
        )

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    @asynccontextmanager
    async def _session_request(self, session_id: str | None = None):
        if self._closing:
            raise RequestError.invalid_request({"reason": "ACP agent is closing"})
        closing = self._session_closes.get(session_id)
        if closing is not None:
            if not closing.done():
                raise RequestError.resource_not_found(f"session:{session_id}")
            self._session_closes.pop(session_id)
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("ACP session requests require an asyncio task")
        registered = task in self._session_requests
        previous = self._session_requests.get(task)
        self._session_requests[task] = session_id
        try:
            yield
        finally:
            if registered:
                self._session_requests[task] = previous
            else:
                self._session_requests.pop(task, None)

    @staticmethod
    def _consume_close_result(task: asyncio.Task):
        if not task.cancelled():
            task.exception()

    @staticmethod
    async def _wait_for_close(task: asyncio.Task):
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)
            except BaseException:
                logger.exception("ACP cleanup failed while its caller was cancelled")
            raise

    async def _cancel_requests(self, session_id: str | None = None):
        session_ids = tuple(
            current_id
            for current_id in self._sessions
            if session_id is None or current_id == session_id
        )
        current = asyncio.current_task()
        tasks = {
            task
            for task, request_session in self._session_requests.items()
            if task is not current
            and (session_id is None or request_session == session_id)
        }
        for current_id in session_ids:
            state = self._sessions[current_id]
            state.cancelled = True
            if state.current_task is not None and state.current_task is not current:
                tasks.add(state.current_task)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [
            result
            for result in results
            if isinstance(result, BaseException)
            and not isinstance(result, asyncio.CancelledError)
        ]
        for current_id in session_ids:
            try:
                await self.cancel(current_id)
            except BaseException as error:
                failures.append(error)
        return failures

    async def aclose(self):
        if asyncio.current_task() in self._session_requests:
            raise RuntimeError(
                "ACP shutdown must run outside an active session request"
            )
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
            self._close_task.add_done_callback(self._consume_close_result)
        await self._wait_for_close(self._close_task)

    async def _close(self):
        failures = []
        try:
            try:
                failures.extend(await self._cancel_requests())
            except BaseException as error:
                failures.append(error)
            session_ids = tuple(dict.fromkeys((*self._sessions, *self._session_closes)))
            for session_id in session_ids:
                try:
                    await self.close_session(session_id)
                except BaseException as error:
                    failures.append(error)
        finally:
            self._conn = None
            self._client_comm.conn = None
            self._sessions.clear()
            self._session_locks.clear()
            self._session_closes.clear()
        if failures:
            raise BaseExceptionGroup("ACP session shutdown failed", failures)

    def on_connect(self, conn: Client):
        if self._closing:
            raise RequestError.invalid_request({"reason": "ACP agent is closing"})
        self._conn = conn
        self._client_comm.conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities=None,
        client_info=None,
        **kwargs,
    ):
        logger.debug(
            f"initialize:  {protocol_version}, {client_info}, {client_capabilities}, {kwargs}"
        )
        self._tool_manager.update_capabilities(client_capabilities)
        from acp import PROTOCOL_VERSION
        from acp.schema import (
            AgentCapabilities,
            AuthMethodAgent,
            EnvVarAuthMethod,
            Implementation,
            InitializeResponse,
            McpCapabilities,
            PromptCapabilities,
            SessionCapabilities,
            SessionCloseCapabilities,
            SessionListCapabilities,
            SessionResumeCapabilities,
            TerminalAuthMethod,
        )

        import AgentCrew

        auth_methods: list[AuthMethodAgent | TerminalAuthMethod | EnvVarAuthMethod] = [
            AuthMethodAgent(
                id="agentcrew-auth",
                name="Agentcrew Api keys Auththenticate",
                description="Set API Keys in config to use Agentcrew",
            )
        ]

        client_meta = (
            client_capabilities.field_meta if client_capabilities is not None else None
        )
        if client_meta and client_meta.get("terminal-auth") is True:
            auth_methods.append(
                TerminalAuthMethod(
                    type="terminal",
                    id="chatgpt-codex",
                    name="ChatGPT (Codex)",
                    description="Authenticate with ChatGPT subscription (Plus/Pro) for Codex API access",
                    args=["chatgpt-auth"],
                ),
            )

        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=McpCapabilities(http=True, sse=True),
                prompt_capabilities=PromptCapabilities(embedded_context=True),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            agent_info=Implementation(
                name="agentcrew",
                title="AgentCrew",
                version=getattr(AgentCrew, "__version__", "0.0.0"),
            ),
            auth_methods=auth_methods,
        )

    async def authenticate(self, method_id: str, **kwargs):
        from acp.schema import AuthenticateResponse

        return AuthenticateResponse()

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs,
    ):
        async with self._session_request():
            logger.debug(f"new session:  {cwd}, {additional_directories}, {kwargs}")
            session_id = f"agentcrew-{uuid.uuid4().hex}"
            agent_name = self._model_controller.resolve_agent_name(
                self.default_agent_name
            )
            model_id = self._model_controller.current_agent_model_id(agent_name)
            state = AcpSessionState(
                cwd=os.path.abspath(os.path.expanduser(cwd)),
                agent_name=agent_name,
                model_id=model_id,
                thought_level=self._model_controller.default_thought_level_for_model(
                    model_id
                ),
            )
            self._sessions[session_id] = state
            await self._model_controller.apply_session_thought_level_to_agent(state)
            await self._mcp_orchestrator.setup_session_mcp_servers(
                session_id, state, mcp_servers
            )
            await self._session_lifecycle._persist_session(session_id, state)
            from acp.schema import NewSessionResponse

            logger.debug("complete new session")
            return NewSessionResponse(
                session_id=session_id,
                modes=self._model_controller.build_modes(agent_name),
                config_options=self._model_controller.build_config_options(
                    agent_name, state.model_id, state.thought_level
                ),
            )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs,
    ):
        async with self._session_request(session_id):
            logger.debug(
                f"load session: {session_id}, {cwd}, {additional_directories}, {kwargs}"
            )
            stored = await self.session_store.load_session(session_id)
            if stored is None:
                raise RequestError.resource_not_found(f"session:{session_id}")
            agent_name = self._model_controller.resolve_agent_name(stored.agent_name)
            loaded_history = [dict(message) for message in stored.history]
            state = AcpSessionState(
                cwd=os.path.abspath(os.path.expanduser(cwd)),
                agent_name=agent_name,
                history=loaded_history,
                title=stored.title,
                updated_at=stored.updated_at,
                model_id=stored.model_id,
                thought_level=stored.thought_level,
                token_usage=TokenUsage(**stored.token_usage)
                if stored.token_usage
                else TokenUsage(),
            )
            self._sessions[session_id] = state
            await self._model_controller.apply_session_model_to_agent(state)
            await self._mcp_orchestrator.setup_session_mcp_servers(
                session_id, state, mcp_servers
            )
            await self._session_lifecycle._persist_session(session_id, state)
            await self._session_lifecycle._replay_history_to_client(
                session_id, loaded_history
            )
            from acp.schema import LoadSessionResponse

            return LoadSessionResponse(
                modes=self._model_controller.build_modes(agent_name),
                config_options=self._model_controller.build_config_options(
                    agent_name, state.model_id, state.thought_level
                ),
            )

    async def close_session(self, session_id: str, **kwargs):
        logger.debug(f"close session: {session_id}, {kwargs}")
        from acp.schema import CloseSessionResponse

        current = asyncio.current_task()
        if (
            current in self._session_requests
            and self._session_requests[current] == session_id
        ):
            raise RequestError.invalid_request(
                {"reason": "Cannot close the current ACP session request"}
            )
        task = self._session_closes.get(session_id)
        if task is None and session_id in self._sessions:
            task = asyncio.create_task(self._close_session(session_id))
            task.add_done_callback(self._consume_close_result)
            self._session_closes[session_id] = task
        if task is not None:
            await self._wait_for_close(task)
        return CloseSessionResponse()

    async def _close_session(self, session_id: str):
        failures = []
        try:
            failures.extend(await self._cancel_requests(session_id))
        except BaseException as error:
            failures.append(error)
        async with self._get_session_lock(session_id):
            state = self._sessions.get(session_id)
            try:
                if state is not None:
                    try:
                        if self._conn:
                            await self._turn_executor.release_active_terminals(
                                session_id, state, self._conn
                            )
                    except BaseException as error:
                        failures.append(error)
                    try:
                        self._tool_manager.restore_builtin_tools(state)
                    except BaseException as error:
                        failures.append(error)
                    try:
                        await self._mcp_orchestrator.cleanup_session_mcp_servers(
                            state, clear_configs=True
                        )
                    except BaseException as error:
                        failures.append(error)
            finally:
                self._sessions.pop(session_id, None)
                self._session_locks.pop(session_id, None)
        if failures:
            raise BaseExceptionGroup(
                f"ACP session {session_id} cleanup failed", failures
            )

    async def set_session_mode(self, session_id: str, mode_id: str, **kwargs):
        async with self._session_request(session_id):
            logger.debug(f"set session mode: {mode_id}, {session_id}, {kwargs}")
            from acp.schema import SetSessionModeResponse

            state = self._sessions.get(session_id)
            if state is None:
                raise RequestError.resource_not_found(f"session:{session_id}")
            await self._model_controller.switch_session_agent(
                state, self._model_controller.resolve_agent_name(mode_id)
            )
            await self._session_lifecycle._persist_session(session_id, state)
            await self._client_comm.send_current_mode_update(session_id, state)
            return SetSessionModeResponse()

    async def list_sessions(
        self,
        cwd: str | None = None,
        cursor: str | None = None,
        **kwargs,
    ):
        logger.debug(f"list sessions: {cursor}, {cwd}, {kwargs}")
        from acp.schema import ListSessionsResponse, SessionInfo

        normalized_cwd = os.path.abspath(os.path.expanduser(cwd)) if cwd else None
        sessions_by_id = {}
        for stored in await self.session_store.list_sessions(cwd=normalized_cwd):
            sessions_by_id[stored.session_id] = SessionInfo(
                cwd=stored.cwd,
                session_id=stored.session_id,
                title=stored.title or stored.agent_name,
                updated_at=stored.updated_at,
            )
        for session_id, state in self._sessions.items():
            if normalized_cwd and state.cwd != normalized_cwd:
                continue
            sessions_by_id[session_id] = SessionInfo(
                cwd=state.cwd,
                session_id=session_id,
                title=state.title or state.agent_name,
                updated_at=state.updated_at,
            )
        return ListSessionsResponse(sessions=list(sessions_by_id.values()))

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs,
    ):

        async with self._session_request(session_id):
            logger.debug(
                f"resume session: {session_id}, {cwd}, {additional_directories}, {kwargs}"
            )
            state = self._sessions.get(session_id)
            if state is None:
                stored = await self.session_store.load_session(session_id)
                if stored is None:
                    raise RequestError.resource_not_found(f"session:{session_id}")
                agent_name = self._model_controller.resolve_agent_name(
                    stored.agent_name
                )
                state = AcpSessionState(
                    cwd=os.path.abspath(os.path.expanduser(cwd)),
                    agent_name=agent_name,
                    history=[dict(message) for message in stored.history],
                    title=stored.title,
                    updated_at=stored.updated_at,
                    model_id=stored.model_id,
                    thought_level=stored.thought_level,
                    token_usage=TokenUsage(**stored.token_usage)
                    if stored.token_usage
                    else TokenUsage(),
                )
                self._sessions[session_id] = state
            else:
                state.cwd = os.path.abspath(os.path.expanduser(cwd))
            await self._model_controller.apply_session_model_to_agent(state)
            await self._mcp_orchestrator.setup_session_mcp_servers(
                session_id, state, mcp_servers
            )
            await self._session_lifecycle._persist_session(session_id, state)
            from acp.schema import ResumeSessionResponse

            return ResumeSessionResponse(
                modes=self._model_controller.build_modes(state.agent_name),
                config_options=self._model_controller.build_config_options(
                    state.agent_name, state.model_id, state.thought_level
                ),
            )

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs,
    ):
        async with self._session_request(session_id):
            logger.debug(
                f"fork session: {session_id}, {cwd}, {additional_directories}, {kwargs}"
            )
            source = self._sessions.get(session_id)
            stored = (
                None if source else await self.session_store.load_session(session_id)
            )
            new_session_id = f"agentcrew-{uuid.uuid4().hex}"
            if source is not None:
                agent_name = source.agent_name
                history = [dict(message) for message in source.history]
                title = source.title
                model_id = source.model_id
                thought_level = source.thought_level
            elif stored is not None:
                agent_name = self._model_controller.resolve_agent_name(
                    stored.agent_name
                )
                history = [dict(message) for message in stored.history]
                title = stored.title
                model_id = stored.model_id
                thought_level = stored.thought_level
            else:
                agent_name = self._model_controller.resolve_agent_name(
                    self.default_agent_name
                )
                history = []
                title = None
                model_id = self._model_controller.current_agent_model_id(agent_name)
                thought_level = self._model_controller.default_thought_level_for_model(
                    model_id
                )
            forked_state = AcpSessionState(
                cwd=os.path.abspath(os.path.expanduser(cwd)),
                agent_name=agent_name,
                history=history,
                title=title,
                model_id=model_id,
                thought_level=thought_level,
            )
            self._sessions[new_session_id] = forked_state
            await self._model_controller.apply_session_model_to_agent(forked_state)
            await self._session_lifecycle._persist_session(new_session_id, forked_state)
            from acp.schema import ForkSessionResponse

            return ForkSessionResponse(
                session_id=new_session_id,
                modes=self._model_controller.build_modes(agent_name),
                config_options=self._model_controller.build_config_options(
                    agent_name, forked_state.model_id, forked_state.thought_level
                ),
            )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs,
    ):

        async with self._session_request(session_id):
            logger.debug(f"Set Config Option: {session_id}, {config_id}, {kwargs}")
            state = self._sessions.get(session_id)
            if state is None:
                raise RequestError.resource_not_found(f"session:{session_id}")
            if not isinstance(value, str):
                raise RequestError.invalid_params(
                    {
                        "configId": config_id,
                        "value": value,
                        "reason": "Config option value must be a string.",
                    }
                )
            if config_id == "mode":
                next_agent_name = self._model_controller.resolve_local_agent_mode(value)
                await self._model_controller.switch_session_agent(
                    state, next_agent_name
                )
                await self._client_comm.send_current_mode_update(session_id, state)
            elif config_id == "model":
                await self._model_controller.switch_session_model(state, value)
            elif config_id == "thought_level":
                await self._model_controller.switch_session_thought_level(state, value)
            else:
                raise RequestError.invalid_params(
                    {"configId": config_id, "reason": "Unsupported ACP config option."}
                )
            await self._session_lifecycle._persist_session(session_id, state)
            from acp.schema import SetSessionConfigOptionResponse

            return SetSessionConfigOptionResponse(
                config_options=self._model_controller.build_config_options(
                    state.agent_name, state.model_id, state.thought_level
                ),
            )

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        message_id: str | None = None,
        **kwargs,
    ):

        from AgentCrew.modules.agents.tool_result_summary import (
            bind_inference_scope,
            reset_inference_scope,
        )
        from AgentCrew.modules.memory.resources import seed_resource_data

        async with self._session_request(session_id):
            scope_token = bind_inference_scope(f"acp:{session_id}")
            state = None
            try:
                logger.debug(f"Prompt: {session_id}, {prompt}, {message_id}, {kwargs}")
                from .message_extraction import prompt_to_text

                # Serialize concurrent prompt calls for the same session
                async with self._get_session_lock(session_id):
                    state = self._sessions.get(session_id)
                    if state is None:
                        raise RequestError.resource_not_found(f"session:{session_id}")

                    seed_resource_data(state.pending_resources)
                    user_text = prompt_to_text(prompt)
                    if user_text.strip() and state.pending_ask_tool is not None:
                        handled = await self._turn_executor.resume_pending_ask(
                            session_id,
                            state,
                            user_text,
                        )
                        if handled:
                            user_text = ""
                    if user_text.strip():
                        state.history.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": user_text}],
                            }
                        )
                        if not state.title:
                            state.title = user_text[:80].split("\n")[0].strip()
                            await self._client_comm.send_session_info_update(
                                session_id, state
                            )

                    state.cancelled = False
                    state.current_task = asyncio.current_task()

                # Release the lock during run_turn so cancel() can interrupt
                ctx = AcpSessionContext(
                    conn=self._conn,
                    session_id=session_id,
                    client_capabilities=self._tool_manager.client_capabilities,
                    active_terminals=state.tool_state.acp_active_terminals,
                )
                token = _current_acp_session.set(ctx)

                from acp.schema import PromptResponse

                try:
                    from AgentCrew.modules.memory.resources import capture_attachment

                    for index, block in enumerate(prompt):
                        if getattr(block, "type", None) == "resource":
                            resource = getattr(block, "resource", None)
                            if resource is not None:
                                capture_attachment(
                                    str(resource.uri)
                                    if getattr(resource, "uri", None)
                                    else None,
                                    scope=f"acp:{session_id}",
                                    identifier=f"{message_id or uuid.uuid4()}/{index}",
                                )
                    await self._turn_executor.run_turn(session_id, state, self._conn)
                    return PromptResponse(
                        stop_reason="cancelled" if state.cancelled else "end_turn",
                    )
                except asyncio.CancelledError:
                    state.cancelled = True
                    return PromptResponse(
                        stop_reason="cancelled",
                    )
                except Exception as e:
                    logger.exception("ACP prompt failed")
                    await self._client_comm.send_agent_message(
                        session_id, f"AgentCrew ACP error: {e}"
                    )
                    return PromptResponse(
                        stop_reason="refusal",
                    )
                finally:
                    _current_acp_session.reset(token)
                    # Re-acquire lock to persist — safe because run_turn has returned
                    async with self._get_session_lock(session_id):
                        state.current_task = None
                        await self._session_lifecycle._persist_session(
                            session_id, state
                        )
            finally:
                reset_inference_scope(scope_token)

    async def cancel(self, session_id: str, **kwargs):
        logger.debug(f"Cancel: {session_id}, {kwargs}")
        if session_id not in self._sessions:
            return
        async with self._get_session_lock(session_id):
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.cancelled = True
            if self._conn:
                await self._turn_executor.release_active_terminals(
                    session_id, state, self._conn
                )
            if (
                state.current_task
                and not state.current_task.done()
                and not state.current_task.cancelling()
            ):
                state.current_task.cancel()

    async def ext_method(self, method: str, params: dict[str, Any]):
        logger.debug(f"Extension method: {method}, {params}")
        return {"error": f"Unsupported extension method: {method}"}

    async def ext_notification(self, method: str, params: dict[str, Any]):
        logger.debug(f"Ignoring unsupported ACP extension notification: {method}")


async def run_acp_agent(
    agent_manager: AgentManager, default_agent_name: str | None = None
):
    from acp import run_agent

    agent = AgentCrewAcpAgent(agent_manager, default_agent_name)
    primary_error = None
    try:
        await run_agent(agent, use_unstable_protocol=True)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            await agent.aclose()
        except BaseException:
            if primary_error is None:
                raise
            logger.exception("ACP cleanup failed while preserving protocol failure")
