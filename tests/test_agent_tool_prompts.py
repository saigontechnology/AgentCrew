"""Shared LocalAgent tool guidance with provider-neutral, offline stubs."""

from types import SimpleNamespace

import pytest

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.manager import AgentMode
from AgentCrew.modules.agents.tools.delegate import get_delegate_tool_definition
from AgentCrew.modules.mcpclient import MCPSessionManager
from AgentCrew.modules.prompts.constants import TOOL_USE_GUIDANCE


class RecordingLLM:
    def __init__(self):
        self.system_prompt = "Initial provider prompt."
        self.tools = []

    def set_system_prompt(self, prompt):
        self.system_prompt = prompt

    def get_system_prompt(self):
        return self.system_prompt

    def clear_tools(self):
        self.tools.clear()

    def register_tool(self, definition, handler):
        self.tools.append(definition)


@pytest.fixture(autouse=True)
def disable_mcp_discovery(monkeypatch):
    monkeypatch.setattr(
        MCPSessionManager,
        "get_instance",
        lambda: SimpleNamespace(initialized=False),
    )


def make_agent(mode=AgentMode.TRANSFER, excluded_tools=None, with_manager=True):
    manager = SimpleNamespace(
        agent_mode=mode,
        agents={"source": object(), "target": object()},
        one_turn_process=True,
        get_agents_list_prompt=lambda: "agents-list-marker",
        get_transfer_system_prompt=lambda: "transfer-prompt-marker",
        get_delegate_system_prompt=lambda: "delegate-prompt-marker",
    )
    agent = LocalAgent(
        "source",
        "test agent",
        RecordingLLM(),
        {"agent_manager": manager} if with_manager else {},
        [],
        excluded_tools=excluded_tools,
    )
    agent.set_system_prompt("Original instructions.")
    agent.set_custom_system_prompt("Custom instructions.")
    return agent


def assert_guidance_once(agent):
    assert agent.tool_prompts.count(TOOL_USE_GUIDANCE) == 1
    assert agent.llm.system_prompt.count(TOOL_USE_GUIDANCE) == 1
    assert agent.llm.system_prompt.count("<Tool_Use_Guidance>") == 1
    assert "Original instructions." in agent.llm.system_prompt
    assert "Custom instructions." in agent.llm.system_prompt
    assert agent.llm.tools


@pytest.mark.parametrize("mode", [AgentMode.TRANSFER, AgentMode.DELEGATE])
def test_sync_activation_assembles_shared_guidance_exactly_once(mode):
    agent = make_agent(mode)
    agent.register_tools()
    agent.register_tools()

    assert agent.activate()
    assert_guidance_once(agent)
    assert agent.activate()
    agent.register_tools()
    agent._tool_registrar.sync_to_llm()
    assert_guidance_once(agent)

    assert agent.deactivate()
    assert agent.tool_prompts == []
    assert agent.activate()
    assert_guidance_once(agent)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [AgentMode.TRANSFER, AgentMode.DELEGATE])
async def test_async_activation_assembles_shared_guidance_exactly_once(mode):
    agent = make_agent(mode)
    agent.register_tools()
    agent.register_tools()

    assert await agent.activate_async()
    assert_guidance_once(agent)
    assert await agent.activate_async()
    agent.register_tools()
    await agent._sync_mcp_tools_after_discovery()
    assert_guidance_once(agent)

    assert await agent.deactivate_async()
    assert agent.tool_prompts == []
    assert await agent.activate_async()
    assert_guidance_once(agent)


@pytest.mark.asyncio
@pytest.mark.parametrize("async_activation", [False, True])
@pytest.mark.parametrize("all_excluded", [False, True])
async def test_toolless_or_all_excluded_agent_has_no_guidance(
    async_activation, all_excluded
):
    agent = make_agent(
        excluded_tools={"transfer", "delegate", "ask"} if all_excluded else None,
        with_manager=all_excluded,
    )
    agent.register_tools()
    agent.register_tools()

    if async_activation:
        assert await agent.activate_async()
    else:
        assert agent.activate()
    await agent._sync_mcp_tools_after_discovery()

    assert agent.tool_definitions == {}
    assert agent.tool_prompts == []
    assert agent.llm.tools == []
    assert "<Tool_Use_Guidance>" not in agent.llm.system_prompt


def test_only_excluded_individual_tool_adds_no_guidance():
    agent = make_agent(with_manager=False, excluded_tools={"delegate"})
    agent.register_tool(get_delegate_tool_definition, lambda: None)

    assert agent.activate()

    assert agent.tool_definitions == {}
    assert agent.tool_prompts == []
    assert "<Tool_Use_Guidance>" not in agent.llm.system_prompt


def test_direct_registration_gets_guidance_without_builtin_service():
    agent = make_agent(with_manager=False)
    agent.register_tool(get_delegate_tool_definition, lambda: None)
    agent.register_tool(get_delegate_tool_definition, lambda: None)

    assert agent.activate()

    assert_guidance_once(agent)


@pytest.mark.asyncio
async def test_late_discovered_tool_adds_guidance_to_active_prompt_once():
    agent = make_agent(with_manager=False)
    assert await agent.activate_async()
    assert "<Tool_Use_Guidance>" not in agent.llm.system_prompt
    original_prompt = agent.llm.system_prompt

    agent.register_tool(get_delegate_tool_definition, lambda: None)
    await agent._sync_mcp_tools_after_discovery()
    agent._tool_registrar.sync_to_llm()

    assert agent.llm.system_prompt == f"{original_prompt}\n\n{TOOL_USE_GUIDANCE}"
    assert_guidance_once(agent)


def test_guidance_remains_conditional_during_tools_disabled_finalization():
    guidance = " ".join(TOOL_USE_GUIDANCE.split())
    assert "When tools are available" in guidance
    assert "same response" in guidance
    assert "do not stop after the sentence" in guidance
    assert "blindly repeat an identical call" in guidance
