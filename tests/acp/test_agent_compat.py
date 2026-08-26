"""Tests pinning ``AgentCrewAcpAgent`` to the ``agent-client-protocol`` 0.12.x surface.

Covers:
- class instantiation with a minimal mocked agent manager
- presence of every method declared on the ``acp.Agent`` protocol
- ``initialize`` response construction (capabilities + auth methods)
- the removed ``session/set_model`` RPC is no longer exposed
- the real SDK agent router dispatches ``initialize`` end-to-end
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from acp import Agent, run_agent
from acp.schema import (
    AuthMethodAgent,
    ClientCapabilities,
    InitializeResponse,
    ListSessionsResponse,
    TerminalAuthMethod,
)

from AgentCrew.modules.acp.agent import AgentCrewAcpAgent

# Methods declared on the acp.Agent protocol in agent-client-protocol 0.12.1.
PROTOCOL_METHODS = [
    "initialize",
    "new_session",
    "load_session",
    "list_sessions",
    "set_session_mode",
    "set_config_option",
    "authenticate",
    "prompt",
    "fork_session",
    "resume_session",
    "close_session",
    "cancel",
    "ext_method",
    "ext_notification",
    "on_connect",
]


class _MockAgentManager:
    """Minimal stand-in for AgentManager used during ACP agent construction."""

    def __init__(self):
        self.agents: dict = {}

    def get_current_agent(self):
        return None

    def get_local_agent(self, name):
        return self.agents.get(name)


def _make_agent(agent_manager: Any = None, session_store=None):
    manager: Any = agent_manager or _MockAgentManager()
    return AgentCrewAcpAgent(manager, session_store=session_store)


class TestAgentInstantiation:
    def test_constructs_with_mock_manager(self):
        agent = _make_agent()
        assert agent.agent_manager is not None
        assert agent._conn is None

    def test_constructs_with_explicit_session_store(self):
        from AgentCrew.modules.acp.session_store import AcpSessionStore

        store = AcpSessionStore(base_dir="/tmp/agentcrew-acp-test-store")
        agent = _make_agent(session_store=store)
        assert agent.session_store is store


class TestProtocolInterface:
    @pytest.mark.parametrize("method", PROTOCOL_METHODS)
    def test_all_protocol_methods_present(self, method):
        assert callable(getattr(AgentCrewAcpAgent, method, None))

    def test_removed_set_session_model_not_exposed(self):
        # session/set_model RPC was removed from the protocol in 0.12.x.
        assert not hasattr(AgentCrewAcpAgent, "set_session_model")

    def test_run_agent_accepts_unstable_protocol_flag(self):
        params = inspect.signature(run_agent).parameters
        assert "use_unstable_protocol" in params


class TestOverrideSignatureCompatibility:
    """Pin parameter names/order against the acp.Agent protocol.

    Guards the Pyright reportIncompatibleMethodOverride fixes: the SDK
    dispatches agent methods with keyword arguments, so parameter order
    must match the protocol exactly.
    """

    @staticmethod
    def _param_names(func) -> list[str]:
        return [
            name
            for name, param in inspect.signature(func).parameters.items()
            if name != "self"
            and param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
        ]

    @pytest.mark.parametrize(
        "method",
        [
            "initialize",
            "new_session",
            "load_session",
            "list_sessions",
            "set_session_mode",
            "set_config_option",
            "authenticate",
            "prompt",
            "fork_session",
            "resume_session",
            "close_session",
            "cancel",
        ],
    )
    def test_parameter_order_matches_protocol(self, method):
        base_names = self._param_names(getattr(Agent, method))
        override_names = self._param_names(getattr(AgentCrewAcpAgent, method))
        # The override must accept every base parameter in the same order;
        # extra optional trailing parameters (e.g. prompt's message_id) are allowed.
        assert override_names[: len(base_names)] == base_names


class TestInitialize:
    def test_initialize_with_terminal_auth_meta(self):
        agent = _make_agent()
        caps = ClientCapabilities(field_meta={"terminal-auth": True})
        response = asyncio.run(
            agent.initialize(protocol_version=1, client_capabilities=caps)
        )
        assert isinstance(response, InitializeResponse)
        assert any(
            isinstance(m, AuthMethodAgent) for m in (response.auth_methods or [])
        )
        assert any(
            isinstance(m, TerminalAuthMethod) for m in (response.auth_methods or [])
        )
        assert response.model_dump(mode="json", by_alias=True, exclude_none=True)

    def test_initialize_without_terminal_auth_meta(self):
        agent = _make_agent()
        response = asyncio.run(
            agent.initialize(
                protocol_version=1, client_capabilities=ClientCapabilities()
            )
        )
        assert any(
            isinstance(m, AuthMethodAgent) for m in (response.auth_methods or [])
        )
        assert not any(
            isinstance(m, TerminalAuthMethod) for m in (response.auth_methods or [])
        )

    def test_initialize_with_none_capabilities(self):
        agent = _make_agent()
        response = asyncio.run(
            agent.initialize(protocol_version=1, client_capabilities=None)
        )
        assert isinstance(response, InitializeResponse)


class TestSdkRouterSmoke:
    """Exercise the real SDK agent router (used by run_agent) with initialize."""

    def test_router_dispatches_initialize(self):
        from acp.agent.router import build_agent_router

        agent = _make_agent()
        router = build_agent_router(agent, use_unstable_protocol=True)
        result = asyncio.run(
            router(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {"_meta": {"terminal-auth": True}},
                },
                is_notification=False,
            )
        )
        assert result.protocol_version == 1
        assert result.agent_info.name == "agentcrew"
        auth_types = {type(m).__name__ for m in (result.auth_methods or [])}
        assert "TerminalAuthMethod" in auth_types

    def test_router_dispatches_list_sessions_keyword_args(self, tmp_path):
        # list_sessions was reordered to (cwd, cursor) to match the protocol;
        # the SDK router dispatches via keyword args. Use an isolated store so
        # the assertion does not depend on leftover real sessions.
        from acp.agent.router import build_agent_router

        from AgentCrew.modules.acp.session_store import AcpSessionStore

        agent = _make_agent(
            session_store=AcpSessionStore(base_dir=str(tmp_path / "sessions"))
        )
        router = build_agent_router(agent, use_unstable_protocol=True)
        result = asyncio.run(
            router(
                "session/list",
                {"cwd": None, "cursor": None},
                is_notification=False,
            )
        )
        assert isinstance(result, ListSessionsResponse)
        assert result.sessions == []

    def test_router_dispatches_prompt_keyword_args(self):
        # prompt was reordered to (session_id, prompt); dispatching with a
        # missing session proves keyword dispatch reaches the method body.
        from acp import RequestError
        from acp.agent.router import build_agent_router

        agent = _make_agent()
        router = build_agent_router(agent, use_unstable_protocol=True)
        with pytest.raises(RequestError):
            asyncio.run(
                router(
                    "session/prompt",
                    {
                        "sessionId": "missing-session",
                        "prompt": [{"type": "text", "text": "hi"}],
                    },
                    is_notification=False,
                )
            )
