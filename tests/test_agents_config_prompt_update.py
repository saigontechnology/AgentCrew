import os
import tempfile
import unittest
from unittest.mock import patch

from AgentCrew.modules.config.agents_config import AgentsConfig, AgentsFileConfig


class TestAgentsConfigPromptUpdate(unittest.TestCase):
    @patch("AgentCrew.modules.config.agents_config.AgentsConfig.reload")
    def test_update_agent_system_prompt_updates_only_target_agent(self, _mock_reload):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "agents.toml")
            old_env = os.environ.get("SW_AGENTS_CONFIG")
            os.environ["SW_AGENTS_CONFIG"] = config_path
            try:
                config = AgentsConfig()
                config.write(
                    AgentsFileConfig.from_dict(
                        {
                            "agents": [
                                {
                                    "name": "Engineer",
                                    "description": "Implementation specialist",
                                    "tools": [],
                                    "system_prompt": "Old prompt",
                                },
                                {
                                    "name": "Architect",
                                    "description": "Design specialist",
                                    "tools": [],
                                    "system_prompt": "Keep me",
                                },
                            ]
                        }
                    )
                )

                updated = config.update_agent_system_prompt("Engineer", "New prompt")
                self.assertTrue(updated)
                data = config.read()
                agents = {agent.name: agent for agent in data.agents}
                self.assertEqual(agents["Engineer"].system_prompt, "New prompt")
                self.assertEqual(agents["Architect"].system_prompt, "Keep me")
            finally:
                if old_env is None:
                    os.environ.pop("SW_AGENTS_CONFIG", None)
                else:
                    os.environ["SW_AGENTS_CONFIG"] = old_env
