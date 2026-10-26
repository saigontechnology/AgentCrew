"""Cursor MCP and local-to-Cursor delegation regression tests with fake SDKs."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from AgentCrew.modules.agents import cursor_agent
from AgentCrew.modules.agents.tools.delegate import get_delegate_tool_handler
from AgentCrew.modules.events import AppEvents, EventBus
from AgentCrew.modules.mcpclient.config import MCPServerConfig
from AgentCrew.modules.mcpclient.cursor_adapter import cursor_mcp_tools
from AgentCrew.modules.mcpclient.manager import MCPSessionManager
from AgentCrew.modules.mcpclient.service import MCPService


@pytest.fixture
def mcp_setup(monkeypatch):
    secret = "private-token-should-never-appear"
    configs = {
        name: MCPServerConfig(
            name=name,
            command="",
            args=[],
            enabledForAgents=agents,
            includeTools=allowed,
            headers={"Authorization": f"Bearer {secret}"},
        )
        for name, agents, allowed in (
            ("healthy", ["Cursor"], ["search", "unknown"]),
            ("restricted", ["Cursor"], ["missing"]),
            ("other", ["Other"], None),
            ("broken", ["Cursor"], None),
        )
    }
    service = MCPService()
    service.tools_cache = {
        name: {
            tool: SimpleNamespace(
                name=tool,
                description=f"Search safely {secret}",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
            for tool in ("search", "write")
        }
        for name in ("healthy", "restricted", "other")
    }

    async def discover(config, _agent):
        raise RuntimeError(secret)

    calls = []

    async def call(config, name, args):
        calls.append((config.name, name, args))
        return {"status": "success", "content": [{"type": "text", "text": "found"}]}

    monkeypatch.setattr(service, "discover_server_tools", discover)
    monkeypatch.setattr(service, "call_tool_stateless", call)
    config_manager = SimpleNamespace(
        load_config=lambda: None,
        get_enabled_servers=lambda name: {
            key: config
            for key, config in configs.items()
            if name in config.enabledForAgents
        },
    )
    manager = SimpleNamespace(
        initialized=True, config_manager=config_manager, mcp_service=service
    )
    monkeypatch.setattr(MCPSessionManager, "get_instance", lambda: manager)
    sdk = SimpleNamespace(CustomTool=lambda **kwargs: SimpleNamespace(**kwargs))
    return sdk, calls, secret


def test_cursor_mcp_assignment_filter_namespace_and_stateless_call(mcp_setup, caplog):
    sdk, calls, secret = mcp_setup
    tools = cursor_mcp_tools(sdk, "Cursor", cursor_agent._run_coroutine)
    assert set(tools) == {"healthy__search"}
    assert (
        tools["healthy__search"].input_schema["properties"]["query"]["type"] == "string"
    )
    result = json.loads(tools["healthy__search"].execute({"query": "hello"}, None))
    assert result == {
        "status": "success",
        "content": [{"type": "text", "text": "found"}],
    }
    assert calls == [("healthy", "search", {"query": "hello"})]
    assert secret not in tools["healthy__search"].description
    assert secret not in caplog.text


def test_cursor_mcp_result_redacts_configured_credentials(mcp_setup):
    sdk, _, secret = mcp_setup
    manager = MCPSessionManager.get_instance()

    async def call(_config, _name, _args):
        return {"status": "error", "content": secret}

    manager.mcp_service.call_tool_stateless = call
    tool = cursor_mcp_tools(sdk, "Cursor", cursor_agent._run_coroutine)[
        "healthy__search"
    ]
    result = tool.execute({}, None)
    assert secret not in result
    assert json.loads(result)["status"] == "error"

    quoted = 'private"quoted'
    manager.config_manager.get_enabled_servers("Cursor")["healthy"].headers = {
        "Authorization": quoted
    }

    async def quoted_call(_config, _name, _args):
        return {"status": "error", "content": [{"text": quoted}]}

    manager.mcp_service.call_tool_stateless = quoted_call
    result = cursor_mcp_tools(sdk, "Cursor", cursor_agent._run_coroutine)[
        "healthy__search"
    ].execute({}, None)
    assert quoted not in json.dumps(json.loads(result))
    assert json.loads(result)["content"][0]["text"] == "[redacted]"


def test_cursor_mcp_failure_of_one_tool_keeps_other_tools(mcp_setup):
    sdk, _, secret = mcp_setup
    original = sdk.CustomTool

    def custom_tool(**kwargs):
        if "bad schema" in kwargs["description"]:
            raise ValueError(secret)
        return original(**kwargs)

    sdk.CustomTool = custom_tool
    manager = MCPSessionManager.get_instance()
    manager.mcp_service.tools_cache["healthy"]["search"].description = "bad schema"
    manager.mcp_service.tools_cache["healthy"]["write"].description = "good tool"
    manager.config_manager.get_enabled_servers = lambda name: {
        "healthy": MCPServerConfig(
            name="healthy", command="", args=[], enabledForAgents=[name]
        )
    }
    tools = cursor_mcp_tools(sdk, "Cursor", cursor_agent._run_coroutine)
    assert set(tools) == {"healthy__write"}


def test_failed_server_keeps_routing_memory_and_healthy_tools(tmp_path, mcp_setup):
    sdk, _, _ = mcp_setup
    agent = cursor_agent.CursorAgent(
        "Cursor",
        "worker",
        cwd=str(tmp_path),
        tools=["memory"],
        services={"memory": SimpleNamespace()},
    )
    tools = agent._custom_tools(sdk)
    assert set(tools) == {
        "transfer",
        "delegate",
        "search_memory",
        "forget_memory_topic",
        "healthy__search",
    }


def test_cursor_agent_and_one_shot_keep_mcp_and_native_settings(
    tmp_path, monkeypatch, mcp_setup
):
    sdk, _, _ = mcp_setup
    created = []

    class FakeRun:
        def stream(self):
            yield SimpleNamespace(type="assistant", text="completed")

        def cancel(self):
            pass

    class FakeAgent:
        def send(self, _prompt):
            return FakeRun()

        def close(self):
            pass

    def create(**kwargs):
        created.append(kwargs)
        return FakeAgent()

    sdk.Agent = SimpleNamespace(create=create)
    sdk.LocalAgentOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setattr(cursor_agent, "_load_cursor_sdk", lambda: sdk)
    agent = cursor_agent.CursorAgent("Cursor", "worker", cwd=str(tmp_path))
    try:
        agent._ensure_sdk_agent()
        direct = created[-1]["local"]
        assert direct.setting_sources == ["project", "user"]
        assert set(direct.custom_tools) == {"transfer", "delegate", "healthy__search"}
    finally:
        agent.dispose_sdk_agent()
    assert cursor_agent._delegate_to_cursor(
        "Source", agent, "task", "context"
    ).startswith("## Result from Cursor:\n\ncompleted")
    delegated = created[-1]["local"]
    assert delegated.setting_sources == ["project", "user"]
    assert set(delegated.custom_tools) == {"healthy__search"}


@pytest.mark.asyncio
async def test_local_delegate_to_cursor_runs_off_loop_and_preserves_current_agent(
    tmp_path, monkeypatch
):
    source = SimpleNamespace(name="Source")
    target = cursor_agent.CursorAgent("Cursor", "worker", cwd=str(tmp_path))
    manager = SimpleNamespace(
        current_agent=source, agents={"Source": source, "Cursor": target}
    )
    main_thread = threading.get_ident()
    observed = []
    events = []
    finished = asyncio.Event()
    bus = EventBus()

    def record(**event):
        events.append(event)
        if event["kind"] == "finish":
            finished.set()

    bus.on(AppEvents.DELEGATE_PROGRESS, record)
    monkeypatch.setattr(EventBus, "get_instance", lambda: bus)

    def delegate(source_name, agent, task, context, cancelled, holder):
        observed.append((threading.get_ident(), source_name, agent, task, context))
        return "## Result from Cursor:\n\ndone"

    monkeypatch.setattr(cursor_agent, "_delegate_to_cursor", delegate)
    handler = get_delegate_tool_handler(manager)
    assert (await handler(target_agent="Cursor", task_description="task")) == (
        "## Result from Cursor:\n\ndone"
    )
    assert observed == [(observed[0][0], "Source", target, "task", "")]
    assert observed[0][0] != main_thread
    assert manager.current_agent is source
    await asyncio.wait_for(finished.wait(), 2)
    assert [event["kind"] for event in events] == ["start", "response", "finish"]
    with pytest.raises(ValueError, match="Cannot delegate to yourself"):
        await handler(target_agent="Source", task_description="task")
    with pytest.raises(ValueError, match="not found"):
        await handler(target_agent="Unknown", task_description="task")


@pytest.mark.asyncio
async def test_local_cursor_delegate_cancellation_closes_run_and_releases_cwd(
    tmp_path, monkeypatch
):
    started = threading.Event()
    stopped = threading.Event()
    created = []

    class Run:
        def __init__(self):
            self.cancelled = False

        def stream(self):
            started.set()
            stopped.wait(timeout=3)
            return iter(())

        def cancel(self):
            self.cancelled = True
            stopped.set()

        def wait(self):
            return SimpleNamespace(result="")

    class Agent:
        def __init__(self):
            self.run = Run()
            self.closed = False

        def send(self, _prompt):
            return self.run

        def close(self):
            self.closed = True

    def create(**_kwargs):
        agent = Agent()
        created.append(agent)
        return agent

    monkeypatch.setattr(
        cursor_agent,
        "_load_cursor_sdk",
        lambda: SimpleNamespace(
            Agent=SimpleNamespace(create=create),
            LocalAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    source = SimpleNamespace(name="Source")
    target = cursor_agent.CursorAgent("Cursor", "worker", cwd=str(tmp_path))
    manager = SimpleNamespace(
        current_agent=source, agents={"Source": source, "Cursor": target}
    )
    task = asyncio.create_task(
        get_delegate_tool_handler(manager)(
            target_agent="Cursor", task_description="task"
        )
    )
    assert await asyncio.to_thread(started.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert created[0].run.cancelled
    assert created[0].closed
    assert target.cwd not in cursor_agent._cwd_owners
    assert manager.current_agent is source


@pytest.mark.asyncio
async def test_local_cursor_delegate_conflict_and_failure_release_cwd(
    tmp_path, monkeypatch
):
    source = SimpleNamespace(name="Source")
    target = cursor_agent.CursorAgent("Cursor", "worker", cwd=str(tmp_path))
    manager = SimpleNamespace(
        current_agent=source, agents={"Source": source, "Cursor": target}
    )
    handler = get_delegate_tool_handler(manager)
    assert cursor_agent._claim_cwd(target.cwd, "another-run") is None
    try:
        response = await handler(target_agent="Cursor", task_description="task")
        assert "already active" in response
        assert cursor_agent._cwd_owners[target.cwd] == "another-run"
    finally:
        cursor_agent._release_cwd(target.cwd, "another-run")

    agent = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(
        cursor_agent,
        "_load_cursor_sdk",
        lambda: SimpleNamespace(
            Agent=SimpleNamespace(create=lambda **kwargs: agent),
            LocalAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )

    def send(_prompt):
        raise RuntimeError("SDK send failed")

    agent.send = send
    with pytest.raises(RuntimeError, match="SDK send failed"):
        await handler(target_agent="Cursor", task_description="task")
    assert target.cwd not in cursor_agent._cwd_owners
    assert manager.current_agent is source
