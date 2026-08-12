"""
A2A v1 protocol server using SDK route factories.

Key design:
- Per-agent mounts with SDK route factories (agent card + JSON-RPC).
- Proper ASGI lifespan: stores close, handlers drain via aclose().
- Durable TaskStore (memory/file/redis) wired via --store-type.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from a2a.server.agent_execution.active_task import TERMINAL_TASK_STATES
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.request_handlers.request_handler import (
    validate,
    validate_request_params,
)
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.types.a2a_pb2 import (
    ListTasksRequest,
    SubscribeToTaskRequest,
    TaskState,
)
from a2a.utils.errors import TaskNotFoundError
from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount, Route

from AgentCrew.modules.agents import AgentManager, LocalAgent

from .agent_executor import AgentCrewA2AExecutor
from .common.server.auth_middleware import AuthMiddleware
from .registry import AgentRegistry
from .session_store import create_session_store, create_task_store

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from a2a.server.events import Event


class AgentCrewRequestHandlerV2(DefaultRequestHandlerV2):
    """A2A v1 request handler that guards resubscribe against stale tasks.

    The SDK ``DefaultRequestHandlerV2.on_subscribe_to_task`` hangs when the
    persisted task is non-terminal but no live ``ActiveTask`` producer exists
    (server restart, or an agent execution that died mid-stream): it spawns an
    idle producer that waits forever on the request queue, so the resubscribe
    never yields another event. This subclass terminates such resubscribes by
    marking the orphaned task FAILED, while still delegating to the SDK for
    live tasks and returning snapshots for terminal tasks.
    """

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        "Streaming is not supported by the agent",
    )
    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        task_id = params.id

        task = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        # Terminal tasks are resubscribed from the snapshot directly. Note
        # SubscribeToTaskRequest carries no history_length field, so the full
        # task (including artifacts/history) is returned as-is.
        if task.status.state in TERMINAL_TASK_STATES:
            yield task
            return

        # Non-terminal task: keep streaming only while a live ActiveTask is
        # still registered. The lookup can race a concurrent sendMessage that
        # has not registered its producer yet; acceptable for a single client.
        active_task = await self._active_task_registry.get(task_id)
        if active_task is not None:
            async for event in super().on_subscribe_to_task(params, context):
                yield event
            return

        # Orphaned non-terminal task: no producer is running and none will
        # resume it. Mark FAILED and persist so this resubscribe terminates
        # instead of hanging on an idle SDK producer.
        task.status.state = TaskState.TASK_STATE_FAILED
        task.status.message.parts.add(text="Task execution was interrupted.")
        await self.task_store.save(task, context)
        yield task


class A2AServer:
    """A2A v1 server that exposes multiple agents using SDK route factories."""

    def __init__(
        self,
        agent_manager: AgentManager,
        host: str = "0.0.0.0",
        port: int = 41241,
        base_url: str | None = None,
        api_key: str | None = None,
        store_type: str = "memory",
        store_options: dict | None = None,
    ):
        logger.info(f"Initializing A2A v1 server with host={host}, port={port}")
        self.agent_manager = agent_manager
        self.host = host
        self.port = port
        self.base_url = base_url or f"http://{host}:{port}"
        self.api_key = api_key or os.getenv("A2A_SERVER_API_KEY", "")
        logger.debug(f"Using base URL: {self.base_url}")

        self.exposed_url = os.getenv("A2A_SERVER_EXPOSED_URL", self.base_url)
        self.agent_registry = AgentRegistry(agent_manager, self.exposed_url)
        self.store_type = store_type
        self.store_options = store_options or {}
        # Track created resources for lifecycle shutdown
        self._handlers: list[DefaultRequestHandlerV2] = []
        self._session_stores: list = []
        self._task_stores: list = []

        # One shared session store for all agents: conversation history is
        # keyed by {owner}:{context_id} (no agent namespace) so a conversation
        # can continue across agents. Per-agent task stores keep task and
        # pending execution state isolated by agent namespace + owner.
        shared_opts = dict(self.store_options)
        shared_opts.pop("agent_namespace", None)
        self.session_store = create_session_store(self.store_type, **shared_opts)
        self._session_stores.append(self.session_store)

        self.app = self._create_app()

    def _create_app(self) -> Starlette:
        """Create the Starlette application with per-agent SDK routes."""
        logger.debug("Creating A2A v1 Starlette application")

        routes: list[BaseRoute] = [
            Route("/agents", self._list_agents, methods=["GET"]),
        ]

        for agent_name in self.agent_manager.agents:
            logger.debug(f"Creating v1 routes for agent: {agent_name}")
            agent_routes = self._build_agent_routes(agent_name)
            routes.append(
                Mount(
                    f"/{agent_name}",
                    routes=agent_routes,
                    middleware=[Middleware(AuthMiddleware, api_key=self.api_key)]
                    if self.api_key
                    else [],
                )
            )

        @asynccontextmanager
        async def lifespan(app):
            await self._reconcile_stale_tasks()
            try:
                yield
            finally:
                logger.info("A2A v1 server shutting down — draining handlers.")
                for h in self._handlers:
                    try:
                        await h.aclose()
                    except Exception:
                        logger.exception("Handler aclose failed")
                for s in self._session_stores:
                    try:
                        await s.close()
                    except Exception:
                        logger.warning("Failed to close session store during shutdown")
                for ts in self._task_stores:
                    try:
                        await ts.close()
                    except Exception:
                        logger.warning("Failed to close task store during shutdown")
                logger.info("A2A v1 server stopped.")

        return Starlette(routes=routes, lifespan=lifespan)

    def _build_agent_routes(self, agent_name: str) -> list[BaseRoute]:
        """Build SDK route factories for a single agent."""
        agent = self.agent_manager.get_agent(agent_name)
        if not agent or not isinstance(agent, LocalAgent):
            logger.warning(f"Agent {agent_name} not found or not a LocalAgent")
            return []

        card = self.agent_registry.get_agent_card(agent_name)
        if not card:
            logger.warning(f"No agent card for {agent_name}")
            return []

        # Create stores: protocol TaskStore stays agent-namespaced and
        # owner-scoped; the session store is shared across all agents.
        store_opts = dict(self.store_options)
        store_opts["agent_namespace"] = agent_name
        task_store = create_task_store(self.store_type, **store_opts)

        self._task_stores.append(task_store)

        executor = AgentCrewA2AExecutor(agent, self.session_store)
        handler = AgentCrewRequestHandlerV2(
            agent_executor=executor,
            task_store=task_store,
            agent_card=card,
        )
        self._handlers.append(handler)

        # Standard v1 card route (SDK factory)
        agent_routes: list[BaseRoute] = []
        agent_routes.extend(create_agent_card_routes(card))

        # JSON-RPC route — v1 only
        agent_routes.extend(
            create_jsonrpc_routes(
                handler,
                rpc_url="/",
            )
        )

        return agent_routes

    async def _reconcile_stale_tasks(self) -> None:
        """Mark persisted non-terminal tasks as FAILED after a server restart.

        Tasks left in a non-terminal state (container killed mid-stream or a
        stalled provider stream) have no live producer once the server
        restarts. Marking them FAILED makes a later resubscribe terminate
        instead of hanging. Best-effort per store so one failure does not
        block startup.
        """
        context = ServerCallContext()
        for task_store in self._task_stores:
            try:
                page = await task_store.list(ListTasksRequest(), context)
            except Exception:
                logger.warning(
                    "Failed to list tasks for stale-task reconciliation",
                    exc_info=True,
                )
                continue
            for task in page.tasks:
                if task.status.state in TERMINAL_TASK_STATES:
                    continue
                try:
                    task.status.state = TaskState.TASK_STATE_FAILED
                    task.status.message.parts.add(
                        text="Task execution was interrupted by server restart."
                    )
                    await task_store.save(task, context)
                except Exception:
                    logger.warning(
                        f"Failed to reconcile stale task {task.id}",
                        exc_info=True,
                    )

    async def _list_agents(self, request: Request):
        """List all available agents."""
        agents = self.agent_registry.list_agents()
        return JSONResponse([agent.model_dump(mode="json") for agent in agents])

    def start(self):
        """Start the A2A server.

        ``nest_asyncio`` is no longer applied. ``uvicorn.run()`` is safe
        because Python 3.12+'s native ``asyncio.run()`` supports the
        ``loop_factory`` kwarg that uvicorn passes internally.
        """
        import uvicorn

        logger.info(f"Starting A2A v1 server on {self.host}:{self.port}")
        logger.info(f"Available agents: {list(self.agent_manager.agents.keys())}")
        uvicorn.run(self.app, host=self.host, port=self.port)
