import os
import json
import toml
from typing import Dict, Any, Optional, List
from datetime import datetime
from loguru import logger


class ConfigManagement:
    """
    A class to manage configuration files in different formats (JSON, TOML).
    Supports reading, writing, and updating configuration files.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the ConfigManagement class.

        Args:
            config_path: Optional path to the configuration file.
                         If not provided, it will be set later.
        """
        self.config_path = config_path
        self.config_data = {}
        self.file_format = None

        if config_path:
            self.load_config()

    def set_config_path(self, config_path: str) -> None:
        """
        Set the configuration file path.

        Args:
            config_path: Path to the configuration file.
        """
        self.config_path = config_path
        self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """
        Load the configuration from the file.

        Returns:
            The loaded configuration data.

        Raises:
            FileNotFoundError: If the configuration file doesn't exist.
            ValueError: If the file format is not supported.
        """
        if not self.config_path:
            raise ValueError("Configuration path not set")

        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        file_extension = os.path.splitext(self.config_path)[1].lower()

        try:
            if file_extension == ".json":
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config_data = json.load(f)
                self.file_format = "json"
            elif file_extension == ".toml":
                self.config_data = toml.load(self.config_path)
                self.file_format = "toml"
            else:
                raise ValueError(f"Unsupported file format: {file_extension}")

            return self.config_data
        except Exception as e:
            raise ValueError(f"Error loading configuration: {str(e)}")

    def save_config(self) -> None:
        """
        Save the configuration to the file.

        Raises:
            ValueError: If the file format is not supported or the configuration path is not set.
        """
        if not self.config_path:
            raise ValueError("Configuration path not set")

        if not self.file_format:
            # Determine format from file extension
            file_extension = os.path.splitext(self.config_path)[1].lower()
            if file_extension == ".json":
                self.file_format = "json"
            elif file_extension == ".toml":
                self.file_format = "toml"
            else:
                raise ValueError(f"Unsupported file format: {file_extension}")

        try:
            if self.file_format == "json":
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(self.config_data, f, indent=2)
            elif self.file_format == "toml":
                with open(self.config_path, "w", encoding="utf-8") as f:
                    toml.dump(self.config_data, f)
            else:
                raise ValueError(f"Unsupported file format: {self.file_format}")
        except Exception as e:
            raise ValueError(f"Error saving configuration: {str(e)}")

    def get_config(self) -> Dict[str, Any]:
        """
        Get the current configuration data.

        Returns:
            The current configuration data.
        """
        return self.config_data

    def update_config(
        self, new_data: Dict[str, Any], merge: bool = True
    ) -> Dict[str, Any]:
        """
        Update the configuration with new data.

        Args:
            new_data: The new data to update the configuration with.
            merge: If True, merge the new data with the existing data.
                   If False, replace the existing data with the new data.

        Returns:
            The updated configuration data.
        """
        if merge:
            self._deep_update(self.config_data, new_data)
        else:
            self.config_data = new_data

        return self.config_data

    def _deep_update(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """
        Deep update a nested dictionary.

        Args:
            target: The target dictionary to update.
            source: The source dictionary with new values.
        """
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                self._deep_update(target[key], value)
            else:
                target[key] = value

    def get_value(self, key_path: str, default: Any = None) -> Any:
        """
        Get a value from the configuration using a dot-separated key path.

        Args:
            key_path: A dot-separated path to the value (e.g., "section.subsection.key").
            default: The default value to return if the key doesn't exist.

        Returns:
            The value at the specified key path, or the default value if not found.
        """
        keys = key_path.split(".")
        current = self.config_data

        try:
            for key in keys:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return default

    def set_value(self, key_path: str, value: Any) -> None:
        """
        Set a value in the configuration using a dot-separated key path.

        Args:
            key_path: A dot-separated path to the value (e.g., "section.subsection.key").
            value: The value to set.
        """
        keys = key_path.split(".")
        current = self.config_data

        # Navigate to the nested dictionary
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]

        # Set the value
        current[keys[-1]] = value

    def delete_value(self, key_path: str) -> bool:
        """
        Delete a value from the configuration using a dot-separated key path.

        Args:
            key_path: A dot-separated path to the value (e.g., "section.subsection.key").

        Returns:
            True if the value was deleted, False otherwise.
        """
        keys = key_path.split(".")
        current = self.config_data

        # Navigate to the parent dictionary
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                return False
            current = current[key]

        # Delete the value
        if keys[-1] in current:
            del current[keys[-1]]
            return True
        return False

    def _get_global_config_file_path(self) -> str:
        """Determines the path for the global config.json file."""
        path = os.getenv("AGENTCREW_CONFIG_PATH")
        if not path:
            path = "./config.json"  # Default for development if env var not set
        return os.path.expanduser(path)

    def read_global_config_data(self) -> Dict[str, Any]:
        """Reads data from the global config.json file."""
        config_path = self._get_global_config_file_path()
        default_config = {
            "api_keys": {},
            "auto_approval_tools": [],
            "global_settings": {
                "theme": "dark",
                "yolo_mode": False,
                "auto_context_shrink": True,
                "shrink_excluded": [],
            },
        }
        try:
            if not os.path.exists(config_path):
                return default_config
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning(
                        f"Warning: Global config file {config_path} does not contain a valid JSON object. Returning default."
                    )
                    return default_config
                # Ensure api_keys key exists and is a dict
                if "api_keys" not in data or not isinstance(data.get("api_keys"), dict):
                    data["api_keys"] = {}
                if "auto_approval_tools" not in data or not isinstance(
                    data.get("auto_approval_tools"), list
                ):
                    data["auto_approval_tools"] = []
                return data
        except json.JSONDecodeError:
            logger.warning(
                f"Warning: Error decoding global config file {config_path}. Returning default config."
            )
            return default_config
        except Exception as e:
            logger.warning(
                f"Warning: Could not read global config file {config_path}: {e}. Returning default config."
            )
            return default_config

    def write_global_config_data(self, config_data: Dict[str, Any]) -> None:
        """Writes data to the global config.json file."""
        from AgentCrew.modules.agents import AgentManager

        config_path = self._get_global_config_file_path()
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            agent_manager = AgentManager.get_instance()
            agent_manager.context_shrink_enabled = config_data.get(
                "global_settings", {}
            ).get("auto_context_shrink", True)
            agent_manager.shrink_excluded_list = config_data.get(
                "global_settings", {}
            ).get("shrink_excluded", [])

        except Exception as e:
            raise ValueError(
                f"Error writing global configuration to {config_path}: {str(e)}"
            )

    def get_sections(self) -> List[str]:
        """
        Get the top-level sections of the configuration.

        Returns:
            A list of top-level section names.
        """
        return list(self.config_data.keys())

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Get a specific section of the configuration.

        Args:
            section: The name of the section to get.

        Returns:
            The section data, or an empty dictionary if the section doesn't exist.
        """
        return self.config_data.get(section, {})

    def read_agents_config(self) -> Dict[str, Any]:
        """
        Read the agents configuration file.

        Returns:
            The agents configuration data.
        """
        agents_config_path = os.getenv(
            "SW_AGENTS_CONFIG", os.path.expanduser("./agents.toml")
        )
        try:
            config = ConfigManagement(agents_config_path)
            return config.get_config()
        except Exception:
            # If file doesn't exist or has errors, return empty config
            return {"agents": []}

    def write_agents_config(self, config_data: Dict[str, Any]) -> None:
        """
        Write the agents configuration to file.

        Args:
            config_data: The configuration data to write.
        """
        agents_config_path = os.getenv(
            "SW_AGENTS_CONFIG", os.path.expanduser("./agents.toml")
        )
        try:
            config = ConfigManagement(agents_config_path)
            config.update_config(config_data, merge=False)
            config.save_config()
            config.reload_agents_from_config()
        except FileNotFoundError:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(agents_config_path), exist_ok=True)

            # Create new config file
            with open(agents_config_path, "w", encoding="utf-8") as f:
                toml.dump(config_data, f)

    def reload_agents_from_config(self):
        from AgentCrew.modules.agents import RemoteAgent, LocalAgent, AgentManager

        agent_manager = AgentManager.get_instance()
        agents_config_path = os.getenv(
            "SW_AGENTS_CONFIG", os.path.expanduser("./agents.toml")
        )
        new_agents_config = agent_manager.load_agents_from_config(agents_config_path)
        for agent_cfg in new_agents_config:
            # Update existing agent
            if agent_cfg.get("base_url"):
                try:
                    agent_manager.agents[agent_cfg["name"]] = RemoteAgent(
                        agent_cfg["name"],
                        agent_cfg["base_url"],
                        headers=agent_cfg.get("headers", {}),
                    )
                except Exception as e:
                    logger.error(str(e))
                finally:
                    continue
            existing_agent = agent_manager.get_local_agent(agent_cfg["name"])
            system_prompt = agent_cfg.get("system_prompt", "")
            if existing_agent:
                existing_agent.tools = agent_cfg.get("tools", [])
                existing_agent.set_system_prompt(system_prompt)
                existing_agent.temperature = agent_cfg.get("temperature", 0.4)
                existing_agent.voice_enabled = agent_cfg.get(
                    "voice_enabled", "disabled"
                )
                existing_agent.voice_id = agent_cfg.get("voice_id", None)
            # New Agent
            else:
                clone_agent = agent_manager.get_current_agent()
                if not isinstance(clone_agent, LocalAgent):
                    clone_agent = [
                        agent
                        for agent in agent_manager.agents.values()
                        if isinstance(agent, LocalAgent)
                    ][0]

                # Extract voice settings from agent config
                voice_enabled = agent_cfg.get("voice_enabled", "disabled")
                voice_id = agent_cfg.get("voice_id", None)

                new_agent = LocalAgent(
                    name=agent_cfg["name"],
                    description=agent_cfg["description"],
                    llm_service=clone_agent.llm,
                    services=clone_agent.services,
                    tools=agent_cfg["tools"],
                    temperature=agent_cfg.get("temperature", None),
                    voice_enabled=voice_enabled,
                    voice_id=voice_id,
                )
                new_agent.set_system_prompt(system_prompt)
                agent_manager.register_agent(new_agent)
        new_agent_name = [a["name"] for a in new_agents_config]
        old_agent_name = [
            n for n in agent_manager.agents.keys() if n not in new_agent_name
        ]
        for agent_name in old_agent_name:
            old_agent = agent_manager.get_agent(agent_name)
            if old_agent and old_agent.is_active:
                agent_manager.select_agent(new_agent_name[0])

            agent_manager.deregister_agent(agent_name)
        ## Finally update transfer prompt for agents
        for _, agent in agent_manager.agents.items():
            was_active = False
            if agent.is_active:
                was_active = True
                agent.deactivate()
            if isinstance(agent, LocalAgent):
                agent.custom_system_prompt = None
            if was_active:
                agent_manager.select_agent(agent.name)

    def read_mcp_config(self) -> Dict[str, Any]:
        """
        Read the MCP servers configuration file.

        Returns:
            The MCP servers configuration data.
        """
        mcp_config_path = os.getenv(
            "MCP_CONFIG_PATH", os.path.expanduser("./mcp_servers.json")
        )
        try:
            with open(mcp_config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # If file doesn't exist or has errors, return empty config
            return {}

    def write_mcp_config(self, config_data: Dict[str, Any]) -> None:
        """
        Write the MCP servers configuration to file.

        Args:
            config_data: The configuration data to write.
        """
        mcp_config_path = os.getenv(
            "MCP_CONFIG_PATH", os.path.expanduser("./mcp_servers.json")
        )
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(mcp_config_path), exist_ok=True)

            # Write to file
            with open(mcp_config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            self.reload_agents_from_config()
        except Exception as e:
            raise ValueError(f"Error writing MCP configuration: {str(e)}")

    def read_custom_llm_providers_config(self) -> List[Dict[str, Any]]:
        """
        Read the custom LLM providers configuration from the global config file.

        Returns:
            A list of custom LLM provider configurations.
        """
        global_config = self.read_global_config_data()
        providers = global_config.get("custom_llm_providers", [])
        for provider in providers:
            if "available_models" not in provider:
                provider["available_models"] = []
        return providers

    def write_custom_llm_providers_config(
        self, providers_data: List[Dict[str, Any]]
    ) -> None:
        """
        Write the custom LLM providers configuration to the global config file.

        Args:
            providers_data: A list of custom LLM provider configurations.
        """
        global_config = self.read_global_config_data()
        global_config["custom_llm_providers"] = providers_data
        self.write_global_config_data(global_config)

    def get_last_used_settings(self) -> Dict[str, Any]:
        """
        Get the last used model and agent settings from the global config.

        Returns:
            A dictionary containing last used settings or empty dict if not found.
        """
        global_config = self.read_global_config_data()
        return global_config.get("last_used", {})

    def set_last_used_model(self, model_id: str, provider: str) -> None:
        """
        Save the last used model to global config.

        Args:
            model_id: The model ID that was last used
            provider: The provider of the model
        """
        try:
            global_config = self.read_global_config_data()

            # Ensure last_used section exists
            if "last_used" not in global_config:
                global_config["last_used"] = {}

            # Update model and provider
            global_config["last_used"]["model"] = model_id
            global_config["last_used"]["provider"] = provider
            global_config["last_used"]["timestamp"] = datetime.now().isoformat()

            self.write_global_config_data(global_config)
        except Exception as e:
            logger.warning(f"Warning: Failed to save last used model to config: {e}")

    def set_last_used_agent(self, agent_name: str) -> None:
        """
        Save the last used agent to global config.

        Args:
            agent_name: The name of the agent that was last used
        """
        try:
            global_config = self.read_global_config_data()

            # Ensure last_used section exists
            if "last_used" not in global_config:
                global_config["last_used"] = {}

            # Update agent
            global_config["last_used"]["agent"] = agent_name
            global_config["last_used"]["timestamp"] = datetime.now().isoformat()

            self.write_global_config_data(global_config)
        except Exception as e:
            logger.warning(f"Warning: Failed to save last used agent to config: {e}")

    def get_last_used_model(self) -> Optional[str]:
        """
        Get the last used model from global config.

        Returns:
            The last used model ID if found, None otherwise
        """
        last_used = self.get_last_used_settings()
        return last_used.get("model")

    def get_last_used_provider(self) -> Optional[str]:
        """
        Get the last used provider from global config.

        Returns:
            The last used provider if found, None otherwise
        """
        last_used = self.get_last_used_settings()
        return last_used.get("provider")

    def get_last_used_agent(self) -> Optional[str]:
        """
        Get the last used agent from global config.

        Returns:
            The last used agent name if found, None otherwise
        """
        last_used = self.get_last_used_settings()
        return last_used.get("agent")

    def get_auto_approval_tools(self) -> List[str]:
        """
        Get the list of auto-approved tools from global config.

        Returns:
            List of tool names that are auto-approved.
        """
        global_config = self.read_global_config_data()
        return global_config.get("auto_approval_tools", [])

    def write_auto_approval_tools(self, tool_name: str, add: bool = True) -> None:
        """
        Add or remove a tool from the auto-approval list in global config.

        Args:
            tool_name: Name of the tool to add/remove from auto-approval list.
            add: True to add the tool, False to remove it.
        """
        try:
            global_config = self.read_global_config_data()
            auto_approval_tools = global_config.get("auto_approval_tools", [])

            if add and tool_name not in auto_approval_tools:
                auto_approval_tools.append(tool_name)
            elif not add and tool_name in auto_approval_tools:
                auto_approval_tools.remove(tool_name)

            global_config["auto_approval_tools"] = auto_approval_tools
            self.write_global_config_data(global_config)
        except Exception as e:
            action = "add" if add else "remove"
            logger.warning(
                f"Warning: Failed to {action} tool {tool_name} from auto-approval list: {e}"
            )

    def export_agents(
        self, agent_names: List[str], output_file: str, file_format: str = "toml"
    ) -> Dict[str, Any]:
        """
        Export selected agents to a file.

        Args:
            agent_names: List of agent names to export
            output_file: Path to output file
            file_format: Output format ('toml' or 'json')

        Returns:
            Dictionary with export results:
            {
                "success": bool,
                "exported_count": int,
                "local_count": int,
                "remote_count": int,
                "missing_agents": List[str],
                "output_file": str,
                "error": str (only if success=False)
            }
        """
        result = {
            "success": False,
            "exported_count": 0,
            "local_count": 0,
            "remote_count": 0,
            "missing_agents": [],
            "output_file": output_file,
        }

        try:
            # Read current agents configuration
            agents_config = self.read_agents_config()
            local_agents = agents_config.get("agents", [])
            remote_agents = agents_config.get("remote_agents", [])

            # Filter agents by provided names
            selected_local_agents = []
            selected_remote_agents = []
            found_names = set()

            for agent in local_agents:
                if agent.get("name") in agent_names:
                    # Remove agent_type if present (not needed in export)
                    export_data = {k: v for k, v in agent.items() if k != "agent_type"}
                    selected_local_agents.append(export_data)
                    found_names.add(agent.get("name"))

            for agent in remote_agents:
                if agent.get("name") in agent_names:
                    # Remove agent_type if present (not needed in export)
                    export_data = {k: v for k, v in agent.items() if k != "agent_type"}
                    selected_remote_agents.append(export_data)
                    found_names.add(agent.get("name"))

            # Check for missing agents
            missing_names = set(agent_names) - found_names
            result["missing_agents"] = list(missing_names)

            if not selected_local_agents and not selected_remote_agents:
                result["error"] = "No matching agents found to export"
                return result

            # Prepare export configuration
            export_config = {}
            if selected_local_agents:
                export_config["agents"] = selected_local_agents
                result["local_count"] = len(selected_local_agents)
            if selected_remote_agents:
                export_config["remote_agents"] = selected_remote_agents
                result["remote_count"] = len(selected_remote_agents)

            result["exported_count"] = len(selected_local_agents) + len(
                selected_remote_agents
            )

            # Ensure output file has correct extension
            if file_format == "toml" and not output_file.endswith(".toml"):
                output_file += ".toml"
            elif file_format == "json" and not output_file.endswith(".json"):
                output_file += ".json"

            # Expand user path
            output_file = os.path.expanduser(output_file)
            result["output_file"] = output_file

            # Create directory if it doesn't exist
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            # Write to file
            with open(output_file, "w", encoding="utf-8") as f:
                if file_format == "toml":
                    toml.dump(export_config, f)
                else:  # json
                    json.dump(export_config, f, indent=2, ensure_ascii=False)

            result["success"] = True
            logger.info(f"Exported {result['exported_count']} agents to {output_file}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Export agents error: {str(e)}", exc_info=True)

        return result

    def import_agents(
        self,
        import_file_path: str,
        merge_strategy: str = "update",
        skip_conflicts: bool = False,
    ) -> Dict[str, Any]:
        """
        Import agents from a file and merge with existing configuration.

        Args:
            import_file_path: Path to file to import (local file or URL)
            merge_strategy: How to handle existing agents:
                           - "update": Update existing agents (default)
                           - "skip": Skip existing agents
                           - "add_only": Only add new agents
            skip_conflicts: If True, skip conflicting agents; if False, update them

        Returns:
            Dictionary with import results:
            {
                "success": bool,
                "added_count": int,
                "updated_count": int,
                "skipped_count": int,
                "conflicts": List[str],
                "imported_agents": List[str],
                "error": str (only if success=False)
            }
        """
        result = {
            "success": False,
            "added_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "conflicts": [],
            "imported_agents": [],
        }

        temp_file = None

        try:
            # Handle URL downloads
            if import_file_path.startswith(("http://", "https://")):
                import requests
                import tempfile

                response = requests.get(import_file_path, timeout=30)
                response.raise_for_status()

                # Create temporary file
                suffix = (
                    ".toml"
                    if "toml" in response.headers.get("content-type", "")
                    else ".json"
                )
                temp_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=suffix, delete=False, encoding="utf-8"
                )
                temp_file.write(response.text)
                temp_file.close()
                import_file_path = temp_file.name

            # Expand user path for local files
            import_file_path = os.path.expanduser(import_file_path)

            # Validate file exists
            if not os.path.exists(import_file_path):
                result["error"] = f"File not found: {import_file_path}"
                return result

            # Load the configuration file
            temp_config = ConfigManagement(import_file_path)
            imported_config = temp_config.get_config()

            # Validate the configuration structure
            imported_local_agents = imported_config.get("agents", [])
            imported_remote_agents = imported_config.get("remote_agents", [])

            if not imported_local_agents and not imported_remote_agents:
                result["error"] = "No agent configurations found in the file"
                return result

            # Read current agents configuration
            current_config = self.read_agents_config()
            current_local_agents = current_config.get("agents", [])
            current_remote_agents = current_config.get("remote_agents", [])

            # Create maps for quick lookup
            local_agent_map = {
                agent.get("name"): agent for agent in current_local_agents
            }
            remote_agent_map = {
                agent.get("name"): agent for agent in current_remote_agents
            }

            # Track existing names for conflict detection
            existing_names = set(local_agent_map.keys()) | set(remote_agent_map.keys())

            # Process local agents
            for agent in imported_local_agents:
                agent_name = agent.get("name")
                if not agent_name:
                    continue

                is_conflict = agent_name in existing_names

                if is_conflict:
                    result["conflicts"].append(agent_name)

                    if skip_conflicts:
                        result["skipped_count"] += 1
                        continue

                    if merge_strategy == "skip":
                        result["skipped_count"] += 1
                        continue

                # Ensure enabled field exists
                if "enabled" not in agent:
                    agent["enabled"] = True

                if is_conflict:
                    # Update existing agent
                    local_agent_map[agent_name] = agent
                    result["updated_count"] += 1
                else:
                    # Add new agent
                    local_agent_map[agent_name] = agent
                    result["added_count"] += 1

                result["imported_agents"].append(agent_name)

            # Process remote agents
            for agent in imported_remote_agents:
                agent_name = agent.get("name")
                if not agent_name:
                    continue

                is_conflict = agent_name in existing_names

                if is_conflict:
                    if agent_name not in result["conflicts"]:
                        result["conflicts"].append(agent_name)

                    if skip_conflicts:
                        result["skipped_count"] += 1
                        continue

                    if merge_strategy == "skip":
                        result["skipped_count"] += 1
                        continue

                # Ensure enabled field exists
                if "enabled" not in agent:
                    agent["enabled"] = True

                if is_conflict:
                    # Update existing agent
                    remote_agent_map[agent_name] = agent
                    result["updated_count"] += 1
                else:
                    # Add new agent
                    remote_agent_map[agent_name] = agent
                    result["added_count"] += 1

                result["imported_agents"].append(agent_name)

            # Prepare final configuration
            final_config = {}
            if local_agent_map:
                final_config["agents"] = list(local_agent_map.values())
            if remote_agent_map:
                final_config["remote_agents"] = list(remote_agent_map.values())

            # Save the configuration
            self.write_agents_config(final_config)

            result["success"] = True
            logger.info(
                f"Imported agents: added={result['added_count']}, "
                f"updated={result['updated_count']}, skipped={result['skipped_count']}"
            )

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Import agents error: {str(e)}", exc_info=True)

        finally:
            # Clean up temporary file
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except Exception as e:
                    logger.warning(f"Failed to delete temporary file: {e}")

        return result
