"""
A2A v1 protocol server using SDK route factories.

Key design:
- Per-agent mounts with SDK route factories (agent card + JSON-RPC).
- Proper ASGI lifespan: stores close, handlers drain via aclose().
- Durable TaskStore (memory/file/redis) wired via --store-type.
- v0.3 card routes at well-known paths for JS client compatibility.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from google.protobuf.json_format import MessageToDict
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

        agent_url = f"{self.exposed_url}/{agent_name}/"
        card = self.agent_registry.get_agent_card(agent_name)
        if not card:
            logger.warning(f"No agent card for {agent_name}")
            return []

        # Create stores: both protocol TaskStore and AgentCrew session store
        # Pass agent_namespace for per-agent isolation
        store_opts = dict(self.store_options)
        store_opts["agent_namespace"] = agent_name
        session_store = create_session_store(self.store_type, **store_opts)
        task_store = create_task_store(self.store_type, **store_opts)

        self._session_stores.append(session_store)
        self._task_stores.append(task_store)

        executor = AgentCrewA2AExecutor(agent, session_store)
        handler = DefaultRequestHandlerV2(
            agent_executor=executor,
            task_store=task_store,
            agent_card=card,
        )
        self._handlers.append(handler)

        from starlette.routing import Route as StarRoute

        # Standard v1 card route (SDK factory)
        agent_routes: list[BaseRoute] = []
        agent_routes.extend(create_agent_card_routes(card))

        # v0.3 compatible card at well-known paths
        async def v03_card(request: Request):
            """Return a v0.3-compatible card with top-level url/preferredTransport.
            Required by @a2a-js/sdk 0.3.x which cannot parse supportedInterfaces.
            """
            d = MessageToDict(card)
            v03 = {
                "protocolVersion": "0.3.0",
                "name": d.get("name", ""),
                "description": d.get("description", ""),
                "url": agent_url,
                "preferredTransport": "jsonrpc",
                "capabilities": d.get("capabilities", {}),
                "skills": d.get("skills", []),
                "defaultInputModes": d.get("defaultInputModes", ["text/plain"]),
                "defaultOutputModes": d.get("defaultOutputModes", ["text/plain"]),
            }
            if "provider" in d:
                v03["provider"] = d["provider"]
            if "version" in d:
                v03["version"] = d["version"]
            return JSONResponse(v03)

        # v0.3 compatible card ONLY at /.well-known/agent.json (not agent-card.json)
        # The v1 SDK route handles /.well-known/agent-card.json above.
        agent_routes.append(
            StarRoute("/.well-known/agent.json", v03_card, methods=["GET"])
        )

        # JSON-RPC route with v0.3 compat
        agent_routes.extend(
            create_jsonrpc_routes(
                handler,
                rpc_url="/",
                enable_v0_3_compat=True,
            )
        )

        return agent_routes

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
