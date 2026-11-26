"""Read-only MCP /stats regressions using fictional in-memory runtime state."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from AgentCrew.modules.agents import LocalAgent
from AgentCrew.modules.chat.message.commands.utility_commands import UtilityCommands
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import ConversationUsage, TokenUsage
from AgentCrew.modules.mcpclient.config import MCPConfigManager, MCPServerConfig
from AgentCrew.modules.mcpclient.manager import MCPSessionManager
from AgentCrew.modules.mcpclient.service import MCPService


def server_config(name, *, enabled=True):
    return MCPServerConfig(
        name=name,
        command="unused",
        args=[],
        enabledForAgents=["Engineer"] if enabled else [],
    )


@pytest.fixture(autouse=True)
def forbid_external_state(monkeypatch):
    guards = []
    for target in (
        "socket.socket.connect",
        "socket.create_connection",
        "subprocess.Popen",
    ):
        guard = Mock(side_effect=AssertionError(f"Unexpected external call: {target}"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    for owner, name in (
        (MCPSessionManager, "get_instance"),
        (MCPSessionManager, "initialize"),
        (MCPConfigManager, "load_config"),
    ):
        guard = Mock(side_effect=AssertionError(f"Unexpected lifecycle call: {name}"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    yield
    for guard in guards:
        guard.assert_not_called()


@pytest.fixture
def runtime():
    config_manager = SimpleNamespace(configs={})
    service = MCPService()
    service._config_manager = config_manager
    service._create_session = AsyncMock(side_effect=AssertionError("No connections"))
    service._close_session = AsyncMock()
    service.discover_server_tools = AsyncMock(side_effect=AssertionError("No discovery"))
    manager = SimpleNamespace(mcp_service=service, config_manager=config_manager)
    handler = SimpleNamespace(
        mcp_manager=manager,
        agent_manager=SimpleNamespace(agents={}, cursor_services={}),
        agent=SimpleNamespace(name="Engineer", services={}, llm=None),
        memory_service=None,
        bus=Mock(),
    )
    yield SimpleNamespace(
        service=service,
        configs=config_manager.configs,
        handler=handler,
        commands=UtilityCommands(handler),
    )
    service._create_session.assert_not_called()
    service._close_session.assert_not_called()
    service.discover_server_tools.assert_not_called()


def report(runtime):
    runtime.handler.bus.reset_mock()
    result = runtime.commands.handle_stats("/stats")
    assert result.handled
    assert result.clear_flag
    runtime.handler.bus.emit_sync.assert_called_once()
    args, kwargs = runtime.handler.bus.emit_sync.call_args
    assert args == (AppEvents.SYSTEM_MESSAGE,)
    return kwargs["message"]


def test_mixed_statuses_include_all_runtime_sources_without_mutation(runtime):
    runtime.configs.update(
        {
            "file-id": server_config("Filesystem"),
            "waiting-id": server_config("Waiting"),
            "disabled-id": server_config("Disabled", enabled=False),
            "empty-id": server_config("Empty"),
        }
    )
    service = runtime.service
    service.tools_cache.update({"Filesystem": {"read": None}, "Empty": {}, "Disabled": {}})
    service._acp_server_configs["acp-id"] = server_config("ACP server")
    service.server_prompts["Cached prompts"] = []
    service.server_resources["Cached resources"] = []
    expected = {
        "ACP server": "not discovered",
        "Cached prompts": "discovered (on-demand)",
        "Cached resources": "discovered (on-demand)",
        "Disabled": "disabled",
        "Empty": "discovered (on-demand)",
        "Filesystem": "discovered (on-demand)",
        "Waiting": "not discovered",
    }
    observed = (
        runtime.configs,
        service._acp_server_configs,
        service.tools_cache,
        service.server_prompts,
        service.server_resources,
    )
    before = deepcopy(observed)

    assert service.get_server_statuses() == expected
    assert list(service.get_server_statuses()) == sorted(expected)
    text = report(runtime)
    assert text.endswith(
        "MCP servers:\n"
        + "\n".join(f"  {name}: {status}" for name, status in expected.items())
    )
    assert observed == before
    assert "file-id" not in text


def test_empty_runtime_preserves_existing_stats_and_reports_no_servers(runtime):
    prefix = runtime.commands._format_llm_services({}) + "\n\n"
    prefix += runtime.commands._format_stats_message({}, "Engineer")

    assert runtime.service.get_server_statuses() == {}
    assert report(runtime) == prefix + "\n\nMCP servers:\n  No MCP servers configured."
    assert "Current conversation total:" in prefix
    assert "0 in | 0 out | 0 cached | 0 cache-write | 0 total" in prefix
    assert "Cost: $0.0000" in prefix


def test_populated_conversation_stats_remain_unchanged(runtime, monkeypatch):
    registry = SimpleNamespace(
        get_model=lambda _: SimpleNamespace(max_context_token=1000)
    )
    monkeypatch.setattr(ModelRegistry, "get_instance", lambda: registry)
    monkeypatch.setattr(ModelRegistry, "_instance", None)
    monkeypatch.setenv("AGENTCREW_DEFAULT_MAX_CONTEXT", "1000")
    agent = LocalAgent.__new__(LocalAgent)
    agent.name = "Engineer"
    agent.llm = SimpleNamespace(provider_name="example", model="test-model")
    agent.services = {}
    agent.token_usage = TokenUsage(total_input_tokens=250)
    agent.conversation_usage = ConversationUsage(
        input_tokens=100,
        output_tokens=50,
        cached_tokens=20,
        cache_creation_tokens=5,
        total_input_tokens=125,
        cost=0.0123,
    )
    runtime.handler.agent = agent
    runtime.handler.agent_manager.agents = {agent.name: agent}
    runtime.configs["example"] = server_config("Example")
    prefix = runtime.commands._format_llm_services({}, primary=agent.llm)
    prefix += "\n\n" + runtime.commands._format_stats_message({agent.name: agent}, agent.name)

    assert report(runtime) == prefix + "\n\nMCP servers:\n  Example: not discovered"
    assert "* Engineer (current)" in prefix
    assert "100 in | 50 out | 20 cached | 5 cache-write | 175 total" in prefix
    assert "Cost: $0.0123" in prefix
    assert "75% left (1,000 limit | 250 occupied)" in prefix


def test_snapshot_reflects_runtime_changes_without_retaining_status(runtime):
    runtime.configs["name"] = server_config("Live")
    assert "  Live: not discovered" in report(runtime)

    runtime.service.tools_cache["Live"] = {}
    assert "  Live: discovered (on-demand)" in report(runtime)

    runtime.configs["name"].enabledForAgents.clear()
    assert "  Live: disabled" in report(runtime)


def test_service_without_config_manager_still_lists_cached_and_acp_servers(runtime):
    runtime.service._config_manager = None
    runtime.service.tools_cache["Cached"] = {}
    runtime.service._acp_server_configs["ACP"] = server_config("ACP")

    assert runtime.service.get_server_statuses() == {
        "ACP": "not discovered",
        "Cached": "discovered (on-demand)",
    }


@pytest.mark.parametrize("missing", ["manager", "service", "attribute"])
def test_missing_runtime_status_is_graceful(runtime, missing):
    if missing == "manager":
        runtime.handler.mcp_manager = None
    elif missing == "service":
        runtime.handler.mcp_manager.mcp_service = None
    else:
        del runtime.handler.mcp_manager

    text = report(runtime)
    assert text.endswith("MCP servers:\n  Status unavailable.")
    assert "LLM-backed service roles" in text
    assert "Current conversation total:" in text


def test_snapshot_error_preserves_stats_and_does_not_expose_details(runtime):
    runtime.service.get_server_statuses = Mock(
        side_effect=RuntimeError("private configuration details")
    )

    text = report(runtime)
    assert text.endswith("MCP servers:\n  Status unavailable.")
    assert "private configuration details" not in text
    assert "Current conversation total:" in text
    runtime.service.get_server_statuses.assert_called_once_with()


@pytest.mark.parametrize("succeeds", [True, False])
def test_real_discovery_cache_distinguishes_empty_success_from_failure(succeeds):
    service = MCPService()
    config = server_config("Example")
    service._config_manager = SimpleNamespace(configs={"example-id": config})
    session = SimpleNamespace(
        list_tools=AsyncMock(return_value=SimpleNamespace(tools=[])),
        list_prompts=AsyncMock(return_value=SimpleNamespace(prompts=[])),
    )
    service._create_session = AsyncMock(return_value=(session, object()))
    service._close_session = AsyncMock()
    service._list_all_resources = AsyncMock(return_value=[])
    if not succeeds:
        service._create_session.side_effect = RuntimeError("Synthetic discovery failure")

    assert asyncio.run(service.discover_server_tools(config, "Engineer")) == []
    assert ("Example" in service.tools_cache) is succeeds
    calls = service._create_session.call_count
    assert service.get_server_statuses() == {
        "Example": "discovered (on-demand)" if succeeds else "not discovered"
    }
    assert service._create_session.call_count == calls
    assert service._close_session.call_count == int(succeeds)
