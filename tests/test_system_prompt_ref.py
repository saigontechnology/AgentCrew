"""Focused coverage for local-agent prompt references and config reload."""

import asyncio
import json

import pytest
from loguru import logger

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.manager import AgentManager
from AgentCrew.modules.agents.prompt_evolution_service import PromptEvolutionService
from AgentCrew.modules.config import agents_config as config_module
from AgentCrew.modules.config.agents_config import AgentsFileConfig, LocalAgentConfig


def load_config(tmp_path, agents, name="agents.json"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return path


def test_ordered_literal_wrappers_and_inline_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "first.txt").write_text("one", encoding="utf-8")
    (tmp_path / "second.txt").write_text("two\n", encoding="utf-8")
    (tmp_path / "third.txt").write_text("three\n\n", encoding="utf-8")
    config = load_config(
        tmp_path,
        [
            {
                "name": "Writer",
                "system_prompt": "inline",
                "system_prompt_ref": ["prompts/first.txt", "second.txt", "third.txt"],
            }
        ],
    )

    [agent] = AgentManager.load_agents_from_config(str(config))

    assert agent["system_prompt"] == (
        "<prompts/first.txt>\none\n</prompts/first.txt>\n\n"
        "<second.txt>\ntwo\n</second.txt>\n\n"
        "<third.txt>\nthree\n\n</third.txt>"
    )
    assert agent["system_prompt_ref"] == [
        "prompts/first.txt",
        "second.txt",
        "third.txt",
    ]


@pytest.mark.parametrize("content", ["first\r\nsecond\r\n", "first\rsecond\r"])
def test_prompt_file_preserves_original_line_endings(tmp_path, monkeypatch, content):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lines.txt").write_bytes(content.encode("utf-8"))
    config = load_config(
        tmp_path, [{"name": "Writer", "system_prompt_ref": ["lines.txt"]}]
    )
    [agent] = AgentManager.load_agents_from_config(str(config))
    assert agent["system_prompt"] == f"<lines.txt>\n{content}</lines.txt>"


def test_empty_prompt_file_has_closing_line_boundary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    config = load_config(
        tmp_path, [{"name": "Writer", "system_prompt_ref": ["empty.txt"]}]
    )
    [agent] = AgentManager.load_agents_from_config(str(config))
    assert agent["system_prompt"] == "<empty.txt>\n\n</empty.txt>"


def test_toml_local_prompt_refs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rules.md").write_text("Follow the rules", encoding="utf-8")
    config = tmp_path / "agents.toml"
    config.write_text(
        '[[agents]]\nname = "Writer"\nsystem_prompt = "inline"\n'
        'system_prompt_ref = ["rules.md"]\n',
        encoding="utf-8",
    )
    [agent] = AgentManager.load_agents_from_config(str(config))
    assert agent["system_prompt"] == "<rules.md>\nFollow the rules\n</rules.md>"


def test_absolute_and_cwd_relative_paths_not_config_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "here.txt").write_text("cwd", encoding="utf-8")
    absolute = tmp_path / "absolute.txt"
    absolute.write_text("abs", encoding="utf-8")
    config = load_config(
        tmp_path,
        [
            {
                "name": "Writer",
                "system_prompt": "inline",
                "system_prompt_ref": [str(absolute), "here.txt"],
            }
        ],
        "configs/agents.json",
    )
    (config.parent / "here.txt").write_text("wrong", encoding="utf-8")

    [agent] = AgentManager.load_agents_from_config(str(config))

    assert agent["system_prompt"] == (
        f"<{absolute}>\nabs\n</{absolute}>\n\n<here.txt>\ncwd\n</here.txt>"
    )


def test_partial_and_total_failure_with_warnings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "bad.txt").write_bytes(b"\xff")
    config = load_config(
        tmp_path,
        [
            {
                "name": "partial",
                "system_prompt": "inline",
                "system_prompt_ref": [
                    "missing.txt",
                    "bad.txt",
                    "ok.txt",
                    "",
                    7,
                    "bad\u0000path",
                ],
            },
            {
                "name": "fallback",
                "system_prompt": "inline",
                "system_prompt_ref": [
                    "missing.txt",
                    "bad.txt",
                    ".",
                ],
            },
            {"name": "empty", "system_prompt": "inline", "system_prompt_ref": []},
            {"name": "absent", "system_prompt": "inline"},
            {
                "name": "invalid",
                "system_prompt": "inline",
                "system_prompt_ref": "ok.txt",
            },
        ],
    )

    warnings = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        agents = AgentManager.load_agents_from_config(str(config))
    finally:
        logger.remove(sink)

    assert [agent["system_prompt"] for agent in agents] == [
        "<ok.txt>\nok\n</ok.txt>",
        "inline",
        "inline",
        "inline",
        "inline",
    ]
    assert any("Skipping system_prompt_ref" in warning for warning in warnings)
    assert any("Invalid system_prompt_ref" in warning for warning in warnings)


def test_refs_only_apply_to_local_agents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "agents.json"
    config.write_text(
        json.dumps(
            {
                "agents": [{"name": "local", "system_prompt_ref": []}],
                "remote_agents": [
                    {
                        "name": "remote",
                        "base_url": "http://localhost:8000",
                        "system_prompt_ref": ["missing.txt"],
                    }
                ],
                "cursor_agents": [
                    {"name": "cursor", "system_prompt_ref": ["missing.txt"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    local, remote, cursor = AgentManager.load_agents_from_config(str(config))
    assert local["system_prompt"] == ""
    assert "system_prompt" not in remote
    assert "system_prompt" not in cursor


def test_typed_config_round_trip():
    data = {
        "agents": [
            {
                "name": "Writer",
                "description": "writer",
                "system_prompt": "inline",
                "system_prompt_ref": ["a", "/b"],
            }
        ]
    }
    result = AgentsFileConfig.from_dict(data).to_dict()
    assert result["agents"][0]["system_prompt_ref"] == ["a", "/b"]
    assert "system_prompt_ref" not in LocalAgentConfig("NoRefs", "").to_dict()


@pytest.mark.parametrize("async_revision", [False, True])
@pytest.mark.parametrize("file_state", ["available", "missing", "no_refs"])
def test_prompt_evolution_reloads_effective_prompt(
    tmp_path, monkeypatch, async_revision, file_state
):
    monkeypatch.chdir(tmp_path)
    prompt_file = tmp_path / "rules.txt"
    if file_state == "available":
        prompt_file.write_text("file rules", encoding="utf-8")
    refs = [] if file_state == "no_refs" else ["rules.txt"]
    config_path = load_config(
        tmp_path,
        [
            {
                "name": "Writer",
                "description": "writer",
                "tools": [],
                "system_prompt": "original inline",
                "system_prompt_ref": refs,
            }
        ],
    )
    monkeypatch.setenv("SW_AGENTS_CONFIG", str(config_path))
    [definition] = AgentManager.load_agents_from_config(str(config_path))
    agent = LocalAgent("Writer", "writer", None, {}, [])
    agent.set_system_prompt(definition["system_prompt"])

    class Manager:
        def __init__(self):
            self.agents = {"Writer": agent}

        load_agents_from_config = staticmethod(AgentManager.load_agents_from_config)

        def get_local_agent(self, name):
            return self.agents.get(name)

    monkeypatch.setattr(AgentManager, "get_instance", lambda: Manager())
    monkeypatch.setattr(
        config_module, "_reload_model_selection", lambda *args: (None, None, None)
    )
    monkeypatch.setattr(agent, "reapply_reasoning", lambda: None)
    service = PromptEvolutionService(agents_config=config_module.AgentsConfig())
    revised = "revised inline fallback"

    if async_revision:
        result = asyncio.run(
            service.apply_prompt_revision_async(agent, revised, "approved")
        )
    else:
        result = service.apply_prompt_revision(agent, revised, "approved")

    saved = service.agents_config.read().agents[0]
    assert saved.system_prompt == revised
    assert saved.system_prompt_ref == refs
    expected = (
        "<rules.txt>\nfile rules\n</rules.txt>"
        if file_state == "available"
        else revised
    )
    assert agent.get_system_prompt() == expected
    assert result["revised_system_prompt"] == revised


def test_export_import_preserves_refs(tmp_path, monkeypatch):
    manager = config_module.AgentsConfig()
    original = AgentsFileConfig(
        agents=[
            LocalAgentConfig(
                name="Writer", description="writer", system_prompt_ref=["one", "two"]
            )
        ]
    )
    monkeypatch.setattr(manager, "read", lambda: original)
    exported = tmp_path / "export.json"
    assert manager.export(["Writer"], str(exported), "json")["success"]
    assert json.loads(exported.read_text(encoding="utf-8"))["agents"][0][
        "system_prompt_ref"
    ] == ["one", "two"]

    saved = []
    monkeypatch.setattr(manager, "read", lambda: AgentsFileConfig())
    monkeypatch.setattr(manager, "write", saved.append)
    assert manager.import_agents(str(exported))["success"]
    assert saved[0].agents[0].system_prompt_ref == ["one", "two"]


def test_gui_save_preserves_unedited_refs():
    from PySide6.QtCore import Qt

    from AgentCrew.modules.gui.widgets.configs.agent_config.tab import AgentsConfigTab

    class Item:
        def __init__(self):
            self.data_value = {
                "name": "Writer",
                "system_prompt_ref": ["a", "/b"],
                "agent_type": "local",
            }

        def data(self, _role):
            return self.data_value

        def setText(self, name):
            assert name == "Writer"

        def setData(self, role, value):
            assert role == Qt.ItemDataRole.UserRole
            self.data_value = value

    class ListPanel:
        agents_list = None

    class List:
        def currentItem(self):
            return item

    class Editor:
        def collect(self):
            return {"name": "Writer", "system_prompt": "updated"}

    class Button:
        def setEnabled(self, enabled):
            assert not enabled

    class Tab:
        agent_list_panel = ListPanel()
        local_agent_editor = Editor()
        save_btn = Button()
        _is_dirty = True

        def save_all_agents(self):
            assert item.data_value["system_prompt_ref"] == ["a", "/b"]

    item = Item()
    Tab.agent_list_panel.agents_list = List()
    AgentsConfigTab.save_agent(Tab())
    assert item.data_value["system_prompt"] == "updated"


@pytest.mark.parametrize("async_reload", [False, True])
def test_reload_uses_shared_resolver(tmp_path, monkeypatch, async_reload):
    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("from file", encoding="utf-8")
    config = load_config(
        tmp_path,
        [
            {
                "name": "Writer",
                "description": "writer",
                "tools": [],
                "system_prompt": "inline",
                "system_prompt_ref": ["prompt.txt"],
            }
        ],
    )
    monkeypatch.setenv("SW_AGENTS_CONFIG", str(config))

    class FakeAgent:
        def __init__(self):
            self.name = "Writer"
            self.is_active = False
            self.prompts = []

        def set_system_prompt(self, prompt):
            self.prompts.append(prompt)

        def reapply_reasoning(self):
            pass

    class FakeManager:
        def __init__(self):
            self.agent = FakeAgent()
            self.agents = {"Writer": self.agent}

        load_agents_from_config = staticmethod(AgentManager.load_agents_from_config)

        def get_local_agent(self, name):
            return self.agents.get(name)

    fake_manager = FakeManager()
    monkeypatch.setattr(AgentManager, "get_instance", lambda: fake_manager)
    monkeypatch.setattr(
        config_module, "_reload_model_selection", lambda *args: (None, None, None)
    )
    monkeypatch.setattr("AgentCrew.modules.agents.LocalAgent", FakeAgent)
    manager = config_module.AgentsConfig()

    if async_reload:
        asyncio.run(manager.reload_async())
    else:
        manager.reload()

    assert fake_manager.agent.prompts == ["<prompt.txt>\nfrom file\n</prompt.txt>"]
    prompt.write_text("changed", encoding="utf-8")
    if async_reload:
        asyncio.run(manager.reload_async())
    else:
        manager.reload()
    assert fake_manager.agent.prompts[-1] == "<prompt.txt>\nchanged\n</prompt.txt>"


@pytest.mark.parametrize("url", ["https://example.org/agents.json", "@hub/agents.json"])
def test_remote_config_warning_and_cwd_loading(tmp_path, monkeypatch, url):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "local.txt").write_text("local", encoding="utf-8")
    config = load_config(
        tmp_path,
        [
            {
                "name": "Writer",
                "system_prompt_ref": ["local.txt"],
            }
        ],
    )

    class Response:
        def __init__(self):
            self.headers = {"content-type": "application/json"}
            self.text = config.read_text(encoding="utf-8")

        def raise_for_status(self):
            pass

    monkeypatch.setenv("AGENTCREW_HUB_HOST", "https://example.org")
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    warnings = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        [agent] = AgentManager.load_agents_from_config(url)
    finally:
        logger.remove(sink)
    assert agent["system_prompt"] == "<local.txt>\nlocal\n</local.txt>"
    assert any("may be sent to the configured LLM" in warning for warning in warnings)
