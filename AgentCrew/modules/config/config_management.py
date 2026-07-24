from __future__ import annotations

import json
import os
import tomllib as toml
import warnings
from typing import TYPE_CHECKING, Any

from tomli_w import dump as toml_dump

if TYPE_CHECKING:
    from .agents_config import AgentsFileConfig
    from .mcp_config import MCPServerEntry


class ConfigManagement:
    """
    A class to manage configuration files in different formats (JSON, TOML).
    Supports reading, writing, and updating configuration files.
    """

    def __init__(self, config_path: str | None = None):
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

    def load_config(self) -> dict[str, Any]:
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
                with open(self.config_path, "rb") as f:
                    self.config_data = toml.load(f)
                self.file_format = "toml"
            else:
                raise ValueError(f"Unsupported file format: {file_extension}")

            return self.config_data
        except Exception as e:
            raise ValueError(f"Error loading configuration: {e!s}")

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
                with open(self.config_path, "wb") as f:
                    toml_dump(self.config_data, f, multiline_strings=True)
            else:
                raise ValueError(f"Unsupported file format: {self.file_format}")
        except Exception as e:
            raise ValueError(f"Error saving configuration: {e!s}")

    def get_config(self) -> dict[str, Any]:
        """
        Get the current configuration data.

        Returns:
            The current configuration data.
        """
        return self.config_data

    def update_config(
        self, new_data: dict[str, Any], merge: bool = True
    ) -> dict[str, Any]:
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

    def _deep_update(self, target: dict[str, Any], source: dict[str, Any]) -> None:
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

    def get_sections(self) -> list[str]:
        """
        Get the top-level sections of the configuration.

        Returns:
            A list of top-level section names.
        """
        return list(self.config_data.keys())

    def get_section(self, section: str) -> dict[str, Any]:
        """
        Get a specific section of the configuration.

        Args:
            section: The name of the section to get.

        Returns:
            The section data, or an empty dictionary if the section doesn't exist.
        """
        return self.config_data.get(section, {})

    def read_agents_config(self) -> AgentsFileConfig:
        warnings.warn(
            "Use AgentsConfig().read() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.agents_config import AgentsConfig

        return AgentsConfig().read()

    def write_agents_config(self, config_data: AgentsFileConfig) -> None:
        warnings.warn(
            "Use AgentsConfig().write() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.agents_config import AgentsConfig

        AgentsConfig().write(config_data)

    def reload_agents_from_config(self):
        warnings.warn(
            "Use AgentsConfig().reload() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.agents_config import AgentsConfig

        AgentsConfig().reload()

    def read_mcp_config(self) -> dict[str, MCPServerEntry]:
        warnings.warn(
            "Use MCPConfig().read() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.mcp_config import MCPConfig

        return MCPConfig().read()

    def write_mcp_config(self, config_data: dict[str, MCPServerEntry]) -> None:
        warnings.warn(
            "Use MCPConfig().write() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.mcp_config import MCPConfig

        MCPConfig().write(config_data)

    def read_global_config_data(self) -> dict[str, Any]:
        warnings.warn(
            "Use GlobalConfig().read() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().read()

    def write_global_config_data(self, config_data: dict[str, Any]) -> None:
        warnings.warn(
            "Use GlobalConfig().write() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        GlobalConfig().write(config_data)

    def read_custom_llm_providers_config(self) -> list[dict[str, Any]]:
        warnings.warn(
            "Use GlobalConfig().read_custom_llm_providers_config() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().read_custom_llm_providers_config()

    def write_custom_llm_providers_config(
        self, providers_data: list[dict[str, Any]]
    ) -> None:
        warnings.warn(
            "Use GlobalConfig().write_custom_llm_providers_config() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        GlobalConfig().write_custom_llm_providers_config(providers_data)

    def get_last_used_settings(self) -> dict[str, Any]:
        warnings.warn(
            "Use GlobalConfig().get_last_used_settings() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().get_last_used_settings()

    def set_last_used_model(self, model_id: str, provider: str) -> None:
        warnings.warn(
            "Use GlobalConfig().set_last_used_model() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        GlobalConfig().set_last_used_model(model_id, provider)

    def set_last_used_agent(self, agent_name: str) -> None:
        warnings.warn(
            "Use GlobalConfig().set_last_used_agent() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        GlobalConfig().set_last_used_agent(agent_name)

    def get_last_used_model(self) -> str | None:
        warnings.warn(
            "Use GlobalConfig().get_last_used_model() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().get_last_used_model()

    def get_last_used_provider(self) -> str | None:
        warnings.warn(
            "Use GlobalConfig().get_last_used_provider() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().get_last_used_provider()

    def get_last_used_agent(self) -> str | None:
        warnings.warn(
            "Use GlobalConfig().get_last_used_agent() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().get_last_used_agent()

    def get_auto_approval_tools(self) -> list[str]:
        warnings.warn(
            "Use GlobalConfig().get_auto_approval_tools() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        return GlobalConfig().get_auto_approval_tools()

    def write_auto_approval_tools(self, tool_name: str, add: bool = True) -> None:
        warnings.warn(
            "Use GlobalConfig().write_auto_approval_tools() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.global_config import GlobalConfig

        GlobalConfig().write_auto_approval_tools(tool_name, add)

    def export_agents(
        self, agent_names: list[str], output_file: str, file_format: str = "toml"
    ) -> dict[str, Any]:
        warnings.warn(
            "Use AgentsConfig().export() directly.", DeprecationWarning, stacklevel=2
        )
        from AgentCrew.modules.config.agents_config import AgentsConfig

        return AgentsConfig().export(agent_names, output_file, file_format)

    def import_agents(
        self,
        import_file_path: str,
        merge_strategy: str = "update",
        skip_conflicts: bool = False,
    ) -> dict[str, Any]:
        warnings.warn(
            "Use AgentsConfig().import_agents() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from AgentCrew.modules.config.agents_config import AgentsConfig

        return AgentsConfig().import_agents(
            import_file_path, merge_strategy, skip_conflicts
        )
