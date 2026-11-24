"""Authoring guidance follows effective built-in capabilities, not tool names."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from AgentCrew.modules.agents import AgentMode
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills.tool import (
    craft_skill_instruction_prompt,
    get_craft_skill_tool_definition,
    get_craft_skill_tool_handler,
    register,
)
from verification.skills.test_skills import write_skill

GROUPS = ("command_execution", "file_editing")


class RecordingLLM:
    provider_name = "fake"
    _provider_name = "fake"
    model = "verification-model"

    def __init__(self):
        self.prompt = "Keep my custom instructions"
        self.tools = {}

    def get_system_prompt(self):
        return self.prompt

    def set_system_prompt(self, prompt):
        self.prompt = prompt

    def register_tool(self, definition, handler):
        self.tools[definition["function"]["name"]] = handler

    def clear_tools(self):
        self.tools.clear()


@pytest.fixture
def make_agent(tmp_path):
    def make(groups=GROUPS, excluded=(), trusted=True, missing=()):
        services = {
            "skills": SkillsService(
                project_root=tmp_path,
                user_root=tmp_path / "home",
                trusted_project=trusted,
            ),
            "command_execution": Mock(),
            "file_editing": Mock(),
            "agent_manager": SimpleNamespace(
                agent_mode=AgentMode.NONE,
                one_turn_process=True,
                context_shrink_enabled=True,
                shrink_excluded_list=[],
            ),
        }
        for key in missing:
            services.pop(key)
        return LocalAgent(
            "GuidanceVerifier",
            "Verification",
            RecordingLLM(),
            services,
            list(groups),
            excluded_tools=excluded,
        )

    return make


def assert_authoring(agent, enabled):
    prompt = craft_skill_instruction_prompt()
    assert agent.tool_prompts.count(prompt) == int(enabled)
    entry = agent.tool_definitions.get("craft_skill")
    assert (entry is not None) == enabled
    if enabled:
        assert entry == (
            get_craft_skill_tool_definition,
            get_craft_skill_tool_handler,
            agent.services["skills"],
        )


@pytest.mark.parametrize(
    ("groups", "excluded", "enabled"),
    [
        (GROUPS, (), True),
        ((), (), False),
        (("command_execution",), (), False),
        (("file_editing",), (), False),
        (("code_execution", "file_editing"), (), False),
        (GROUPS, ("command_execution",), False),
        (GROUPS, ("file_editing",), False),
        (GROUPS, ("run_command",), False),
        (GROUPS, ("write_file",), False),
        (GROUPS, ("run_command", "write_file"), False),
        (GROUPS, GROUPS, False),
        (GROUPS, ("craft_skill",), False),
        (GROUPS, ("skills",), False),
        (GROUPS, ("activate_skill",), True),
        (GROUPS, ("create_worktree",), True),
        (GROUPS, ("check_command_status",), True),
    ],
)
def test_registration_uses_enabled_groups_and_individual_exclusions(
    make_agent, groups, excluded, enabled
):
    agent = make_agent(groups=groups, excluded=excluded)
    agent.register_tools()
    assert agent.registered_tools == set()
    assert_authoring(agent, enabled)
    assert "activate_skill" not in agent.tool_definitions
    assert agent.services["skills"].catalog_prompt() == ""
    assert craft_skill_instruction_prompt() not in "\n".join(
        agent._context_manager.build_adaptive_context()
    )


@pytest.mark.parametrize("missing", [("command_execution",), ("file_editing",), GROUPS])
def test_missing_service_disables_authoring(make_agent, missing):
    agent = make_agent(missing=missing)
    agent.register_tools()
    assert_authoring(agent, False)


def test_configuration_and_later_registered_names_are_not_capabilities(make_agent):
    agent = make_agent()
    agent.registered_tools.update({"run_command", "write_file"})
    register(agent.services["skills"], agent)
    assert_authoring(agent, False)


@pytest.mark.parametrize("with_skill", [False, True])
def test_untrusted_project_preserves_activation_without_authoring(
    make_agent, with_skill
):
    agent = make_agent(trusted=False)
    if with_skill:
        write_skill(agent.services["skills"].user_root, "user-checks")
    agent.register_tools()
    assert_authoring(agent, False)
    assert ("activate_skill" in agent.tool_definitions) == with_skill


@pytest.mark.parametrize("excluded", [(), ("craft_skill",), ("skills",)])
def test_activation_eligibility_remains_independent(make_agent, excluded):
    agent = make_agent(groups=(), excluded=excluded)
    write_skill(agent.services["skills"].project_root, "project-checks")
    agent.register_tools()
    assert_authoring(agent, False)
    assert ("activate_skill" in agent.tool_definitions) == ("skills" not in excluded)


def test_tool_prompt_is_rendered_for_trusted_empty_catalog(make_agent):
    agent = make_agent()
    agent.set_system_prompt("Follow project conventions")
    with patch(
        "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
        return_value=SimpleNamespace(initialized=False),
    ):
        assert agent.activate()
    prompt = craft_skill_instruction_prompt()
    assert_authoring(agent, True)
    assert agent.llm.prompt.count(prompt) == 1
    assert "Follow project conventions" in agent.llm.prompt
    assert "repeatable project workflow" in prompt
    assert "confirmation" in prompt
    assert "one-off" in prompt
    assert "Reuse or wrap existing project skills/scripts" in prompt
    assert "does not execute scripts" in prompt
    assert "<When_To_Use>" in prompt and "<Authoring_Workflow>" in prompt


def test_provider_registration_failure_does_not_advertise_craft_instruction(make_agent):
    agent = make_agent()
    original_register = agent.llm.register_tool

    def reject_craft(definition, handler):
        if definition["function"]["name"] == "craft_skill":
            raise ValueError("Provider rejected tool registration")
        original_register(definition, handler)

    agent.llm.register_tool = reject_craft
    with patch(
        "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
        return_value=SimpleNamespace(initialized=False),
    ):
        assert agent.activate()
    assert "craft_skill" not in agent.registered_tools
    assert craft_skill_instruction_prompt() not in agent.tool_prompts
    assert craft_skill_instruction_prompt() not in agent.llm.prompt
    agent.llm.register_tool = original_register
    agent.resync_tools_to_llm()
    assert_authoring(agent, True)
    assert agent.llm.prompt.count(craft_skill_instruction_prompt()) == 1


def test_registration_and_skill_refresh_do_not_duplicate_authoring_prompt(make_agent):
    agent = make_agent()
    agent.is_active = True
    for _ in range(3):
        agent.register_tools()
        register(agent.services["skills"], agent)
        agent.resync_tools_to_llm()
    for name in ("first", "second"):
        write_skill(agent.services["skills"].project_root, name)
        agent._refresh_agent_skills()
    assert_authoring(agent, True)
    assert agent.llm.prompt.count(craft_skill_instruction_prompt()) == 1


def test_resync_removes_and_restores_owned_tool_and_prompt(make_agent):
    from AgentCrew.modules.command_execution.tool import register as register_command

    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    agent.resync_tools_to_llm()
    agent.tool_definitions.pop("run_command")
    agent.resync_tools_to_llm()
    assert_authoring(agent, False)
    assert "craft_skill" not in agent.llm.tools
    assert craft_skill_instruction_prompt() not in agent.llm.prompt
    assert agent.llm.prompt.startswith("Keep my custom instructions")
    register_command(agent.services["command_execution"], agent)
    agent.resync_tools_to_llm()
    assert_authoring(agent, True)
    assert agent.llm.prompt.count(craft_skill_instruction_prompt()) == 1


@pytest.mark.parametrize("missing", ["skills", *GROUPS])
def test_service_removal_reconciles_owned_registration(make_agent, missing):
    agent = make_agent()
    agent.register_tools()
    agent.is_active = True
    agent.resync_tools_to_llm()
    agent.services.pop(missing)
    agent.resync_tools_to_llm()
    assert_authoring(agent, False)
    assert craft_skill_instruction_prompt() not in agent.llm.prompt


@pytest.mark.parametrize("change", ["skills", "craft_skill", "service"])
def test_reregistration_cleans_ineligible_owned_authoring(make_agent, change):
    agent = make_agent()
    agent.register_tools()
    if change == "service":
        agent.services.pop("skills")
    else:
        agent.excluded_tools = frozenset({change})
    agent.register_tools()
    assert_authoring(agent, False)


@pytest.mark.parametrize("name", ["run_command", "write_file"])
@pytest.mark.parametrize("component", [0, 1, 2])
def test_same_named_unrelated_registration_does_not_count(make_agent, name, component):
    agent = make_agent()
    agent.register_tools()
    agent.is_active = True
    agent.resync_tools_to_llm()
    entry = list(agent.tool_definitions[name])
    if component == 0:
        entry[component] = lambda: {
            "type": "function",
            "function": {"name": name, "parameters": {}},
        }
    elif component == 1:
        entry[component] = lambda _service=None: Mock()
    else:
        entry[component] = object()
    agent.tool_definitions[name] = tuple(entry)
    agent.resync_tools_to_llm()
    assert_authoring(agent, False)
    assert craft_skill_instruction_prompt() not in agent.llm.prompt


def test_unrelated_craft_skill_collision_is_not_overwritten_or_given_prompt(make_agent):
    agent = make_agent()
    agent.register_tools()
    unrelated = (
        lambda: {"type": "function", "function": {"name": "craft_skill"}},
        lambda: Mock(),
        None,
    )
    agent.tool_definitions["craft_skill"] = unrelated
    register(agent.services["skills"], agent)
    agent.resync_tools_to_llm()
    assert agent.tool_definitions["craft_skill"] is unrelated
    assert craft_skill_instruction_prompt() not in agent.tool_prompts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "write"), [(True, False), (False, True), (True, True)]
)
async def test_known_acp_remapping_and_restoration_keep_authoring(
    make_agent, terminal, write
):
    from AgentCrew.modules.acp.session_state import AcpSessionState
    from AgentCrew.modules.acp.tool_manager import AcpToolManager

    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    manager = AcpToolManager(SimpleNamespace(get_local_agent=lambda _name: agent))
    manager.update_capabilities({"terminal": terminal, "fs": {"writeTextFile": write}})
    state = AcpSessionState(cwd=".", agent_name=agent.name)
    await manager.ensure_tools_for_session("session", state)
    assert_authoring(agent, True)
    assert agent.llm.prompt.count(craft_skill_instruction_prompt()) == 1
    assert ("acp_run_command" in agent.tool_definitions) == terminal
    assert ("acp_write_file" in agent.tool_definitions) == write
    manager.restore_builtin_tools(state)
    assert_authoring(agent, True)
    assert agent.llm.prompt.count(craft_skill_instruction_prompt()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", ["acp_run_command", "acp_write_file"])
async def test_acp_excluded_replacement_removes_authoring_until_restore(
    make_agent, excluded
):
    from AgentCrew.modules.acp.session_state import AcpSessionState
    from AgentCrew.modules.acp.tool_manager import AcpToolManager

    agent = make_agent(excluded=(excluded,))
    agent.is_active = True
    agent.register_tools()
    manager = AcpToolManager(SimpleNamespace(get_local_agent=lambda _name: agent))
    manager.update_capabilities({"terminal": True, "fs": {"writeTextFile": True}})
    state = AcpSessionState(cwd=".", agent_name=agent.name)
    await manager.ensure_tools_for_session("session", state)
    assert_authoring(agent, False)
    assert craft_skill_instruction_prompt() not in agent.llm.prompt
    manager.restore_builtin_tools(state)
    assert_authoring(agent, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("groups", "excluded", "missing"),
    [
        ((), (), ()),
        (GROUPS, ("command_execution",), ()),
        (GROUPS, ("file_editing",), ()),
        (GROUPS, ("run_command",), ()),
        (GROUPS, ("write_file",), ()),
        (GROUPS, (), ("command_execution",)),
        (GROUPS, (), ("file_editing",)),
    ],
)
async def test_acp_client_cannot_bypass_agent_capability_policy(
    make_agent, groups, excluded, missing
):
    from AgentCrew.modules.acp.session_state import AcpSessionState
    from AgentCrew.modules.acp.tool_manager import AcpToolManager

    agent = make_agent(groups=groups, excluded=excluded, missing=missing)
    agent.register_tools()
    manager = AcpToolManager(SimpleNamespace(get_local_agent=lambda _name: agent))
    manager.update_capabilities({"terminal": True, "fs": {"writeTextFile": True}})
    await manager.ensure_tools_for_session(
        "session", AcpSessionState(cwd=".", agent_name=agent.name)
    )
    assert_authoring(agent, False)


@pytest.mark.parametrize("name", ["acp_run_command", "acp_write_file"])
def test_unrelated_acp_named_tool_is_not_a_known_remapping(make_agent, name):
    from AgentCrew.modules.acp.tools.filesystem import register as register_fs
    from AgentCrew.modules.acp.tools.terminal import register as register_terminal

    agent = make_agent()
    agent.register_tools()
    agent.tool_definitions.pop("run_command")
    agent.tool_definitions.pop("write_file")
    register_fs(agent=agent, enable_read=False)
    register_terminal(agent=agent)
    agent.tool_definitions[name] = (
        lambda: {"type": "function", "function": {"name": name}},
        lambda: Mock(),
        None,
    )
    agent.resync_tools_to_llm()
    assert_authoring(agent, False)


def reject_adapter_tools(agent, failures):
    original = agent.llm.register_tool
    attempts = []

    def register_tool(definition, handler):
        name = definition["function"]["name"]
        attempts.append(name)
        if name in failures:
            raise ValueError(f"Adapter rejected {name}")
        original(definition, handler)

    agent.llm.register_tool = register_tool
    return attempts


def add_unrelated_tool(agent, name="unrelated_tool"):
    entry = (
        lambda: {"type": "function", "function": {"name": name}},
        lambda: Mock(),
        None,
    )
    agent.tool_definitions[name] = entry
    return entry


def add_acp_capabilities(agent, *, replace=False):
    from AgentCrew.modules.acp.tools.filesystem import register as register_fs
    from AgentCrew.modules.acp.tools.terminal import register as register_terminal

    if replace:
        agent.tool_definitions.pop("run_command")
        agent.tool_definitions.pop("write_file")
    register_fs(agent=agent, enable_read=False)
    register_terminal(agent=agent)


def assert_adapter_authoring(agent, enabled):
    prompt = craft_skill_instruction_prompt()
    assert ("craft_skill" in agent.llm.tools) == enabled
    assert ("craft_skill" in agent.registered_tools) == enabled
    assert agent.tool_prompts.count(prompt) == int(enabled)
    assert agent.llm.prompt.count(prompt) == int(enabled)


@pytest.mark.parametrize("failed", ["run_command", "write_file"])
def test_failed_local_adapter_prerequisite_suppresses_authoring_and_retries(
    make_agent, failed
):
    from AgentCrew.modules.prompts.constants import TOOL_USE_GUIDANCE

    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    unrelated = add_unrelated_tool(agent)
    custom = "<CustomTool>Preserve my tool instructions.</CustomTool>"
    agent.tool_prompts.append(custom)
    agent.llm.prompt += f"\n{custom}"
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, True)
    assert failed in agent.registered_tools
    entry = agent.tool_definitions.pop("craft_skill")
    agent.tool_definitions = {"craft_skill": entry, **agent.tool_definitions}
    failures = {failed}
    attempts = reject_adapter_tools(agent, failures)

    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, False)
    assert failed not in agent.registered_tools
    assert "craft_skill" not in attempts
    assert all(
        actual is expected
        for actual, expected in zip(
            agent.tool_definitions["craft_skill"], entry, strict=True
        )
    )
    assert agent.tool_definitions["unrelated_tool"] is unrelated
    assert "unrelated_tool" in agent.llm.tools
    assert custom in agent.tool_prompts and custom in agent.llm.prompt
    assert agent.llm.prompt.startswith("Keep my custom instructions")
    assert agent.llm.prompt.count(TOOL_USE_GUIDANCE) == 1

    failures.clear()
    attempts.clear()
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, True)
    assert attempts[-1] == "craft_skill"
    assert attempts.count("craft_skill") == 1


@pytest.mark.parametrize("failed", ["acp_run_command", "acp_write_file"])
def test_failed_acp_adapter_prerequisite_suppresses_authoring_and_retries(
    make_agent, failed
):
    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    add_acp_capabilities(agent, replace=True)
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, True)
    assert failed in agent.registered_tools
    failures = {failed}
    attempts = reject_adapter_tools(agent, failures)

    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, False)
    assert "craft_skill" not in attempts
    assert failed not in agent.registered_tools
    failures.clear()
    attempts.clear()
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, True)
    assert attempts[-1] == "craft_skill"
    assert attempts.count("craft_skill") == 1


@pytest.mark.parametrize(
    "failed", ["run_command", "write_file", "acp_run_command", "acp_write_file"]
)
def test_successful_provenance_valid_fallback_keeps_authoring(make_agent, failed):
    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    add_acp_capabilities(agent)
    agent.resync_tools_to_llm()
    assert failed in agent.registered_tools
    attempts = reject_adapter_tools(agent, {failed})
    agent.resync_tools_to_llm()
    assert failed not in agent.registered_tools
    assert_adapter_authoring(agent, True)
    assert attempts[-1] == "craft_skill"


@pytest.mark.parametrize(
    "failures",
    [{"run_command", "acp_run_command"}, {"write_file", "acp_write_file"}],
)
def test_all_candidates_for_either_capability_must_not_fail(make_agent, failures):
    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    add_acp_capabilities(agent)
    attempts = reject_adapter_tools(agent, failures)
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, False)
    assert "craft_skill" not in attempts


@pytest.mark.parametrize(
    ("failed", "unrelated"),
    [("run_command", "acp_run_command"), ("write_file", "acp_write_file")],
)
def test_successful_unrelated_candidate_cannot_replace_failed_prerequisite(
    make_agent, failed, unrelated
):
    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    add_unrelated_tool(agent, unrelated)
    attempts = reject_adapter_tools(agent, {failed})
    agent.resync_tools_to_llm()
    assert unrelated in agent.registered_tools
    assert_adapter_authoring(agent, False)
    assert "craft_skill" not in attempts


def test_reregister_then_sync_clears_active_guidance_after_skills_service_removal(
    make_agent,
):
    from AgentCrew.modules.prompts.constants import TOOL_USE_GUIDANCE

    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, True)
    generic = TOOL_USE_GUIDANCE
    custom = "<CustomTool>Keep unrelated instructions.</CustomTool>"
    agent.tool_prompts.append(custom)
    agent.llm.prompt += f"\n{custom}"
    service = agent.services.pop("skills")
    agent.register_tools()
    assert "craft_skill" not in agent.tool_definitions
    assert craft_skill_instruction_prompt() not in agent.tool_prompts
    assert craft_skill_instruction_prompt() in agent.llm.prompt

    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, False)
    assert custom in agent.tool_prompts and custom in agent.llm.prompt
    assert agent.llm.prompt.count(generic) == 1
    assert agent.llm.prompt.startswith("Keep my custom instructions")
    agent.services["skills"] = service
    agent.register_tools()
    agent.resync_tools_to_llm()
    assert_adapter_authoring(agent, True)


@pytest.mark.parametrize("failed", ["run_command", "write_file"])
def test_unrelated_craft_tool_keeps_normal_adapter_registration(make_agent, failed):
    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    unrelated = add_unrelated_tool(agent, "craft_skill")
    agent.tool_definitions = {
        "craft_skill": unrelated,
        **{
            name: entry
            for name, entry in agent.tool_definitions.items()
            if name != "craft_skill"
        },
    }
    custom = "<CustomTool>Keep external craft behavior.</CustomTool>"
    agent.tool_prompts.append(custom)
    agent.llm.prompt += f"\n{custom}"
    attempts = reject_adapter_tools(agent, {failed})
    agent.resync_tools_to_llm()
    assert attempts[0] == "craft_skill"
    assert agent.tool_definitions["craft_skill"] is unrelated
    assert "craft_skill" in agent.registered_tools and "craft_skill" in agent.llm.tools
    assert craft_skill_instruction_prompt() not in agent.tool_prompts
    assert craft_skill_instruction_prompt() not in agent.llm.prompt
    assert custom in agent.tool_prompts and custom in agent.llm.prompt


def test_unrelated_adapter_failure_does_not_suppress_authoring(make_agent):
    agent = make_agent()
    agent.is_active = True
    agent.register_tools()
    add_unrelated_tool(agent, "unrelated_failure")
    attempts = reject_adapter_tools(agent, {"unrelated_failure"})
    agent.resync_tools_to_llm()
    assert "unrelated_failure" not in agent.registered_tools
    assert_adapter_authoring(agent, True)
    assert attempts[-1] == "craft_skill"
