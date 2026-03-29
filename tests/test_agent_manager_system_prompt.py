import textwrap

from AgentCrew.modules.agents.manager import AgentManager


class TestAgentManagerSystemPromptLoading:
    def test_load_agents_from_config_supports_absolute_system_prompt_file(self, tmp_path):
        prompt_file = tmp_path / "software_architect.md"
        prompt_content = "You are a useful assistant for software architects."
        prompt_file.write_text(prompt_content, encoding="utf-8")

        config_path = tmp_path / "agents.toml"
        config_path.write_text(
            textwrap.dedent(
                f'''
                [[agents]]
                name = "SoftwareArchitect"
                description = "Architecture specialist"
                system_prompt = "{prompt_file}"
                tools = ["code_analysis"]
                '''
            ).strip(),
            encoding="utf-8",
        )

        agents = AgentManager.load_agents_from_config(str(config_path))

        assert agents[0]["system_prompt"] == prompt_content

    def test_load_agents_from_config_supports_agentcrew_home_relative_system_prompt_file(
        self, tmp_path, monkeypatch
    ):
        agentcrew_home = tmp_path / ".AgentCrew"
        prompt_file = agentcrew_home / "agents" / "software_architect.md"
        prompt_file.parent.mkdir(parents=True)
        prompt_content = "You are a useful assistant for software architects."
        prompt_file.write_text(prompt_content, encoding="utf-8")

        monkeypatch.setattr(AgentManager, "AGENTCREW_HOME", agentcrew_home)

        config_path = tmp_path / "agents.toml"
        config_path.write_text(
            textwrap.dedent(
                '''
                [[agents]]
                name = "SoftwareArchitect"
                description = "Architecture specialist"
                system_prompt = "agents/software_architect.md"
                tools = ["code_analysis"]
                '''
            ).strip(),
            encoding="utf-8",
        )

        agents = AgentManager.load_agents_from_config(str(config_path))

        assert agents[0]["system_prompt"] == prompt_content

    def test_load_agents_from_config_supports_tilde_system_prompt_file(
        self, tmp_path, monkeypatch
    ):
        fake_home = tmp_path / "home"
        prompt_file = fake_home / ".AgentCrew" / "agents" / "software_architect.md"
        prompt_file.parent.mkdir(parents=True)
        prompt_content = "You are a useful assistant for software architects."
        prompt_file.write_text(prompt_content, encoding="utf-8")

        def fake_expanduser(path: str) -> str:
            if path == "~/.AgentCrew/agents/software_architect.md":
                return str(prompt_file)
            if path == "~/.AgentCrew":
                return str(fake_home / ".AgentCrew")
            return path

        monkeypatch.setattr(
            "AgentCrew.modules.agents.manager.os.path.expanduser", fake_expanduser
        )

        config_path = tmp_path / "agents.toml"
        config_path.write_text(
            textwrap.dedent(
                '''
                [[agents]]
                name = "SoftwareArchitect"
                description = "Architecture specialist"
                system_prompt = "~/.AgentCrew/agents/software_architect.md"
                tools = ["code_analysis"]
                '''
            ).strip(),
            encoding="utf-8",
        )

        agents = AgentManager.load_agents_from_config(str(config_path))

        assert agents[0]["system_prompt"] == prompt_content

    def test_load_agents_from_config_keeps_inline_system_prompt_when_file_does_not_exist(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(AgentManager, "AGENTCREW_HOME", tmp_path / ".AgentCrew")
        inline_prompt = "agents/software_architect.md"

        config_path = tmp_path / "agents.toml"
        config_path.write_text(
            textwrap.dedent(
                f'''
                [[agents]]
                name = "SoftwareArchitect"
                description = "Architecture specialist"
                system_prompt = "{inline_prompt}"
                tools = ["code_analysis"]
                '''
            ).strip(),
            encoding="utf-8",
        )

        agents = AgentManager.load_agents_from_config(str(config_path))

        assert agents[0]["system_prompt"] == inline_prompt
