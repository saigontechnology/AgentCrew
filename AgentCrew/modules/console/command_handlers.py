"""
Command handlers for console UI commands.

This module contains handlers for various console commands like /edit_agent, /export_agent, etc.
Extracted from ConsoleUI for better code maintainability and separation of concerns.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

from loguru import logger
from rich.table import Table
from rich.text import Text

from AgentCrew.modules.config.agents_config import AgentsConfig

from .constants import (
    RICH_STYLE_YELLOW,
)

if TYPE_CHECKING:
    from .console_ui import ConsoleUI


class CommandHandlers:
    """Handles console UI commands for file operations and configuration management."""

    def __init__(self, console_ui: ConsoleUI):
        """
        Initialize the command handlers.

        Args:
            console: Rich Console instance for output
            message_handler: MessageHandler instance for agent operations
        """
        self.console = console_ui.console
        self.message_handler = console_ui.message_handler
        self.context_service = console_ui.message_handler.persistent_service

    def open_file_in_editor(self, file_path: str) -> bool:
        """
        Open a file in the system's default editor.

        Args:
            file_path: Path to the file to open

        Returns:
            True if file was opened successfully, False otherwise
        """
        try:
            file_path = os.path.expanduser(file_path)

            # Ensure file exists, create if it doesn't
            if not os.path.exists(file_path):
                os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("")  # Create empty file

            if sys.platform == "darwin":  # macOS
                subprocess.run(["open", file_path], check=True)
            elif sys.platform == "win32":
                os.startfile(file_path)
            elif sys.platform == "linux":
                # Try different editors in order of preference
                editors = ["xdg-open", "sensible-editor", "editor"]
                for editor in editors:
                    try:
                        subprocess.run([editor, file_path], check=True)
                        break
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue
                else:
                    raise RuntimeError("No suitable editor found")

            return True

        except Exception as e:
            self.console.print(
                Text(
                    f"❌ Error opening file: {e!s}\nPlease open the file manually: {file_path}",
                    style="bold red",
                )
            )
            logger.error(f"Error opening file: {e!s}", exc_info=True)
            return False

    def handle_edit_agent_command(self) -> None:
        """Handle the /edit_agent command to open agents configuration in default editor."""
        agents_config_path = os.getenv(
            "SW_AGENTS_CONFIG", os.path.expanduser("./agents.toml")
        )

        self.console.print(
            Text(
                f"📝 Opening agents configuration: {agents_config_path}",
                style=RICH_STYLE_YELLOW,
            )
        )

        self.open_file_in_editor(agents_config_path)
        AgentsConfig().reload()

    def handle_edit_mcp_command(self) -> None:
        """Handle the /edit_mcp command to open MCP configuration in default editor."""
        mcp_config_path = os.getenv(
            "MCP_CONFIG_PATH", os.path.expanduser("./mcp_servers.json")
        )

        self.console.print(
            Text(
                f"📝 Opening MCP configuration: {mcp_config_path}",
                style=RICH_STYLE_YELLOW,
            )
        )

        self.open_file_in_editor(mcp_config_path)
        AgentsConfig().reload()

    def handle_edit_config_command(self) -> None:
        """Handle the /edit_config command to open AgentCrew global configuration in default editor."""
        config_path = os.getenv(
            "AGENTCREW_CONFIG_PATH", os.path.expanduser("./config.json")
        )

        self.console.print(
            Text(
                f"📝 Opening AgentCrew configuration: {config_path}",
                style=RICH_STYLE_YELLOW,
            )
        )

        self.open_file_in_editor(config_path)
        AgentsConfig().reload()

    def handle_toggle_session_yolo_command(self) -> None:
        """Toggle session-level YOLO mode override for auto-approval of tool calls."""
        self.message_handler.tool_manager.session_overrided_yolo_mode ^= True

        state = (
            "🚀 Enabled"
            if self.message_handler.tool_manager.session_overrided_yolo_mode
            else "⛔ Disabled"
        )
        self.console.print(
            Text(f"{state} session overrided YOLO mode", style=RICH_STYLE_YELLOW)
        )

    def handle_export_agent_command(
        self, agent_names_str: str, output_file: str
    ) -> None:
        """
        Handle the /export_agent command to export selected agents to a file.

        Args:
            agent_names_str: Comma-separated list of agent names to export
            output_file: Path to output file (will be created if doesn't exist)
        """
        try:
            # Parse agent names
            agent_names = [
                name.strip() for name in agent_names_str.split(",") if name.strip()
            ]

            if not agent_names:
                self.console.print(
                    Text(
                        "❌ Error: No agent names provided.",
                        style="bold red",
                    )
                )
                return

            result = AgentsConfig().export(agent_names, output_file, file_format="toml")

            if not result["success"]:
                self.console.print(
                    Text(
                        f"❌ Error: {result.get('error', 'Unknown error')}",
                        style="bold red",
                    )
                )
                return

            # Show warning for missing agents
            if result["missing_agents"]:
                self.console.print(
                    Text(
                        f"⚠️  Warning: The following agents were not found: {', '.join(result['missing_agents'])}",
                        style="bold yellow",
                    )
                )

            # Show success message
            agent_count = result["exported_count"]
            agent_word = "agent" if agent_count == 1 else "agents"

            self.console.print(
                Text(
                    f"✅ Successfully exported {agent_count} {agent_word} to: {result['output_file']}",
                    style="bold green",
                )
            )

        except Exception as e:
            self.console.print(
                Text(
                    f"❌ Failed to export agents: {e!s}",
                    style="bold red",
                )
            )
            logger.error(f"Export agent error: {e!s}", exc_info=True)

    def handle_import_agent_command(self, file_or_url: str) -> None:
        """
        Handle the /import_agent command to import agent configurations from a file or URL.

        Args:
            file_or_url: Path to local file or URL to fetch agent configuration
                        Supports @hub/ prefix which converts to https://agentplace.cloud/
        """
        try:
            if file_or_url.startswith("@hub/"):
                hub_host = os.environ.get(
                    "AGENTCREW_HUB_HOST", "https://agentplace.cloud"
                )
                file_or_url = hub_host.rstrip("/") + "/" + file_or_url[5:]

            if file_or_url.startswith(("http://", "https://")):
                self.console.print(
                    Text(
                        f"📥 Downloading agent configuration from: {file_or_url}",
                        style=RICH_STYLE_YELLOW,
                    )
                )

            result = AgentsConfig().import_agents(
                file_or_url, merge_strategy="update", skip_conflicts=False
            )

            if not result["success"]:
                self.console.print(
                    Text(
                        f"❌ Error: {result.get('error', 'Unknown error')}",
                        style="bold red",
                    )
                )
                return

            # Display success message
            success_message = Text(
                "✅ Agent configuration imported successfully!\n", style="bold green"
            )
            if result["added_count"] > 0:
                success_message.append(
                    f"   Added: {result['added_count']} agent(s)\n", style="green"
                )
            if result["updated_count"] > 0:
                success_message.append(
                    f"   Updated: {result['updated_count']} agent(s)\n", style="yellow"
                )
            if result["skipped_count"] > 0:
                success_message.append(
                    f"   Skipped: {result['skipped_count']} agent(s)\n", style="dim"
                )

            self.console.print(success_message)

        except Exception as e:
            self.console.print(
                Text(
                    f"❌ Failed to import agent configuration: {e!s}",
                    style="bold red",
                )
            )
            logger.error(f"Import agent error: {e!s}", exc_info=True)

    def handle_list_behaviors_command(self) -> None:
        try:
            if not self.context_service:
                self.console.print(
                    Text(
                        "❌ Context persistence service not available", style="bold red"
                    )
                )
                return

            global_behaviors = self.context_service.get_adaptive_behaviors(
                self.message_handler.agent.name
            )
            project_behaviors = self.context_service.get_adaptive_behaviors(
                self.message_handler.agent.name, is_local=True
            )

            if not global_behaviors and not project_behaviors:
                self.console.print(
                    Text("ℹ️  No adaptive behaviors found.", style=RICH_STYLE_YELLOW)
                )
                return

            self._display_behaviors_table(global_behaviors, project_behaviors)

        except Exception as e:
            self.console.print(
                Text(f"❌ Error listing behaviors: {e!s}", style="bold red")
            )
            logger.error(f"list behaviors error: {e!s}", exc_info=True)

    def _display_behaviors_table(
        self,
        global_behaviors: dict[str, str],
        project_behaviors: dict[str, str],
    ) -> None:
        if global_behaviors:
            global_table = Table(
                title="🌍 Global Behaviors",
                show_header=True,
                header_style="bold cyan",
                title_style="bold blue",
            )
            global_table.add_column("ID", style="yellow", no_wrap=True)
            global_table.add_column("Behavior", style="white")

            for behavior_id, behavior_text in global_behaviors.items():
                global_table.add_row(behavior_id, behavior_text)

            self.console.print(global_table)
            self.console.print()

        if project_behaviors:
            project_table = Table(
                title="📁 Project Behaviors",
                show_header=True,
                header_style="bold green",
                title_style="bold magenta",
            )
            project_table.add_column("ID", style="yellow", no_wrap=True)
            project_table.add_column("Behavior", style="white")

            for behavior_id, behavior_text in project_behaviors.items():
                project_table.add_row(behavior_id, behavior_text)

            self.console.print(project_table)
            self.console.print()

    def handle_update_behavior_command(
        self, behavior_id: str, behavior_text: str, scope: str = "global"
    ) -> None:
        try:
            if not self.context_service:
                self.console.print(
                    Text(
                        "❌ Context persistence service not available", style="bold red"
                    )
                )
                return

            behavior_text = behavior_text.strip()

            if not behavior_text:
                self.console.print(
                    Text("❌ Behavior text cannot be empty", style="bold red")
                )
                return

            behavior_lower = behavior_text.lower().strip()
            if not behavior_lower.startswith("when"):
                self.console.print(
                    Text(
                        "❌ Behavior must follow 'when..., [action]...' format",
                        style="bold red",
                    )
                )
                return

            is_local = scope == "project"
            success = self.context_service.store_adaptive_behavior(
                self.message_handler.agent.name,
                behavior_id,
                behavior_text,
                is_local=is_local,
            )

            if success:
                self.console.print(
                    Text(
                        f"✅ Behavior '{behavior_id}' updated successfully ({scope} scope)",
                        style="bold green",
                    )
                )
            else:
                self.console.print(Text("❌ Failed to save behavior", style="bold red"))

        except ValueError as e:
            self.console.print(
                Text(f"❌ Invalid behavior format: {e!s}", style="bold red")
            )
        except Exception as e:
            self.console.print(
                Text(f"❌ Error updating behavior: {e!s}", style="bold red")
            )
            logger.error(f"Update behavior error: {e!s}", exc_info=True)

    def handle_delete_behavior_command(
        self, behavior_id: str, scope: str = "global"
    ) -> None:
        try:
            if not self.context_service:
                self.console.print(
                    Text(
                        "❌ Context persistence service not available", style="bold red"
                    )
                )
                return

            is_local = scope == "project"

            success = self.context_service.remove_adaptive_behavior(
                self.message_handler.agent.name, behavior_id, is_local=is_local
            )
            if success:
                self.console.print(
                    Text(
                        f"✅ Behavior '{behavior_id}' deleted successfully",
                        style="bold green",
                    )
                )
            else:
                self.console.print(
                    Text(
                        f"❌ Failed to delete behavior '{behavior_id}'",
                        style="bold red",
                    )
                )

        except Exception as e:
            self.console.print(
                Text(f"❌ Error deleting behavior: {e!s}", style="bold red")
            )
            logger.error(f"Delete behavior error: {e!s}", exc_info=True)

    def get_all_behavior_ids(self) -> list[str]:
        try:
            if not self.context_service:
                return []

            all_behaviors = self.context_service.get_adaptive_behaviors(
                self.message_handler.agent.name
            ) | self.context_service.get_adaptive_behaviors(
                self.message_handler.agent.name, is_local=True
            )
            behavior_ids = []

            for id in all_behaviors:
                behavior_ids.extend(id)

            return behavior_ids
        except Exception:
            return []
