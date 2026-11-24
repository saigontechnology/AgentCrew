"""Shared agent preprocessing, tool registration, and approval integration."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.tool_result_summary import AgentToolResultCoordinator
from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills.tool import (
    active_skill_results,
    get_activate_skill_tool_handler,
)
from verification.skills.test_skills import canonical_result, write_skill


@pytest.fixture
def agent(tmp_path):
    service = SkillsService(
        project_root=tmp_path, user_root=tmp_path / "home", trusted_project=True
    )
    llm = Mock()
    llm.get_system_prompt.return_value = "Base system prompt"
    llm._provider_name = "fake"
    llm.model = "fake-model"
    from AgentCrew.modules.agents import AgentMode

    manager = SimpleNamespace(
        agent_mode=AgentMode.NONE,
        context_shrink_enabled=True,
        shrink_excluded_list=[],
        one_turn_process=True,
    )
    return LocalAgent(
        "Verifier",
        "Verification",
        llm,
        {"skills": service, "agent_manager": manager},
        [],
    )


@pytest.mark.asyncio
async def test_real_preprocessing_evicts_only_inference_copy_under_shrink(
    agent, monkeypatch
):
    service = agent.services["skills"]
    for name in "abcd":
        write_skill(service.project_root, name, body=f"instructions for {name}")
    history = [{"role": "user", "content": [{"type": "text", "text": "task"}]}]
    stream = agent.process_messages(history)
    handler = get_activate_skill_tool_handler(service, agent=agent)
    for name in "abcd":
        canonical_result(
            agent._message_formatter, history, name, await handler(name=name)
        )
    original = copy.deepcopy(history)
    agent.input_tokens_usage = 2000
    monkeypatch.setenv("AGENTCREW_DEFAULT_MAX_CONTEXT", "1000")
    monkeypatch.setenv("AGENTCREW_CONTEXT_SHRINK_THRESHOLD", "0")
    hooks = SimpleNamespace(
        run_before=AsyncMock(side_effect=lambda _point, **payload: payload),
        run_after=AsyncMock(side_effect=lambda _point, result, **_kw: result),
    )
    with (
        patch(
            "AgentCrew.modules.events.hooks.HookRegistry.get_instance",
            return_value=hooks,
        ),
        patch(
            "AgentCrew.modules.utils.VisionPreprocessingUtils.preprocess_messages",
            new=AsyncMock(),
        ),
        patch.object(agent._context_manager, "enhance_messages"),
    ):
        inference = await agent.pre_process_message(history)
    await stream.aclose()
    assert history == original
    results = [message for message in inference if message.get("role") == "tool"]
    assert len(results) == 4
    assert "skill_content_evicted" in results[0]["content"]
    assert all(
        result["content"].startswith("<skill_content ") for result in results[1:]
    )
    assert list(active_skill_results(inference)[0]) == list("bcd")
    call_ids = [
        call["id"] for message in inference for call in message.get("tool_calls", [])
    ]
    assert call_ids == [result["tool_call_id"] for result in results]
    agent.llm.set_system_prompt.assert_not_called()


def test_background_summary_keeps_activation_result(agent):
    service = Mock()
    agent.services["tool_result_summary"] = service
    coordinator = AgentToolResultCoordinator(agent, agent._message_formatter)
    message = coordinator.format_message(
        MessageType.ToolResult,
        {
            "tool_use": {"id": "1", "name": "activate_skill", "input": {"name": "a"}},
            "tool_result": '<skill_content name="a">full body</skill_content>',
        },
    )
    assert message["role"] == "tool"
    service.submit.assert_not_called()


def test_shared_service_refresh_updates_each_agent_schema(agent):
    service = agent.services["skills"]
    other = LocalAgent("Other", "Verification", Mock(), agent.services, [])
    agent._tool_registrar.register_tools()
    other._tool_registrar.register_tools()
    assert "craft_skill" not in agent.tool_definitions
    assert "activate_skill" not in agent.tool_definitions
    write_skill(service.project_root, "a")
    agent._refresh_agent_skills()
    other._refresh_agent_skills()
    assert "activate_skill" in agent.tool_definitions
    assert "activate_skill" in other.tool_definitions
    write_skill(service.project_root, "b")
    agent._refresh_agent_skills()
    other._refresh_agent_skills()
    for target in (agent, other):
        assert target._tool_registrar.get_tool_definition("activate_skill")["function"][
            "parameters"
        ]["properties"]["name"]["enum"] == ["a", "b"]
        assert target.llm.register_tool.called
    (service.project_root / ".agents/skills/a/SKILL.md").unlink()
    (service.project_root / ".agents/skills/b/SKILL.md").unlink()
    agent._refresh_agent_skills()
    assert "activate_skill" not in agent.tool_definitions
    assert "craft_skill" not in agent.tool_definitions


def test_excluded_skills_stay_excluded(agent):
    agent.excluded_tools = frozenset({"skills"})
    write_skill(agent.services["skills"].project_root, "a")
    agent._tool_registrar.register_tools()
    agent._refresh_agent_skills()
    assert "activate_skill" not in agent.tool_definitions
    assert "craft_skill" not in agent.tool_definitions
    assert not agent._context_manager.build_adaptive_context()[1:]


def test_adaptive_catalog_has_no_skill_body_or_resources(agent):
    service = agent.services["skills"]
    write_skill(service.project_root, "a", body="PRIVATE-SKILL-BODY")
    service.refresh()
    with patch(
        "AgentCrew.modules.llm.model_registry.ModelRegistry.get_model_limit",
        return_value=10000,
    ):
        sections = agent._context_manager.build_adaptive_context()
    catalog = next(section for section in sections if "## Available Skills" in section)
    assert len(catalog) <= 800
    assert "PRIVATE-SKILL-BODY" not in catalog
    assert "scripts/" not in catalog


@pytest.mark.asyncio
async def test_acp_craft_requests_edit_permission():
    from AgentCrew.modules.acp.tools.context import classify_tool_kind
    from AgentCrew.modules.acp.tools.permission_broker import AcpPermissionBroker

    broker = AcpPermissionBroker(conn=Mock(), session_id="session")
    assert classify_tool_kind("craft_skill") == "edit"
    with patch.object(
        broker, "_call_client_permission", new=AsyncMock(return_value="reject")
    ) as request:
        assert (
            await broker.request_permission(
                {"id": "1", "name": "craft_skill", "input": {}}
            )
            == "reject"
        )
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_requires_craft_approval_before_execution():
    from AgentCrew.modules.chat.message.tool_manager import ToolManager

    manager = ToolManager.__new__(ToolManager)
    agent = Mock()
    agent.validate_tool_use.return_value = None
    manager.message_handler = SimpleNamespace(agent=agent, _messages_append=Mock())
    manager.bus = SimpleNamespace(emit=AsyncMock())
    manager.get_effective_yolo_mode = lambda: False
    manager._auto_approved_tools = set()
    manager._wait_for_tool_confirmation = AsyncMock(return_value={"action": "deny"})
    manager._execute_approved_tool = AsyncMock()
    await manager.execute_tool({"id": "1", "name": "craft_skill", "input": {}})
    manager._wait_for_tool_confirmation.assert_awaited_once()
    manager._execute_approved_tool.assert_not_awaited()
