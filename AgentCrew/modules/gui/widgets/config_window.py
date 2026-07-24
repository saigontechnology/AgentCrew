from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.gui.themes import StyleProvider

from .configs.agent_config import AgentsConfigTab
from .configs.custom_llm_provider import CustomLLMProvidersConfigTab
from .configs.global_settings import SettingsTab
from .configs.mcp_config import MCPsConfigTab


class ConfigWindow(QDialog):
    """Configuration window with tabs for Agents and MCP servers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agentcrew - Settings")
        self.setMinimumSize(800, 600)

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        # Flag to track if changes were made
        self.changes_made = False

        # Initialize config management and style provider
        self.config_manager = ConfigManagement()
        self.style_provider = StyleProvider()

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create tabs
        self.agents_tab = AgentsConfigTab(self.config_manager)
        self.mcps_tab = MCPsConfigTab(self.config_manager)
        self.settings_tab = SettingsTab(self.config_manager)
        self.custom_llm_providers_tab = CustomLLMProvidersConfigTab(self.config_manager)

        # Connect change signals
        self.agents_tab.config_changed.connect(self.on_config_changed)
        self.mcps_tab.config_changed.connect(self.on_config_changed)
        self.settings_tab.config_changed.connect(self.on_config_changed)
        self.custom_llm_providers_tab.config_changed.connect(self.on_config_changed)

        # Add tabs to widget
        self.tab_widget.addTab(self.agents_tab, "Agents")
        self.tab_widget.addTab(self.mcps_tab, "MCP Servers")
        self.tab_widget.addTab(self.custom_llm_providers_tab, "Custom LLMs")
        self.tab_widget.addTab(self.settings_tab, "Settings")

        # Main layout
        layout = QVBoxLayout()
        layout.addWidget(self.tab_widget)

        # Add buttons at the bottom
        button_layout = QHBoxLayout()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.on_close)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Apply styling
        self.setStyleSheet(self.style_provider.get_config_window_style())

    def on_config_changed(self):
        """Track that changes were made to configuration"""
        self.changes_made = True

    def on_close(self):
        """Handle close button click with restart notification if needed"""
        # if self.changes_made:
        #     QMessageBox.information(
        #         self,
        #         "Configuration Changed",
        #         "Configuration changes have been saved.\n\nPlease restart the application for all changes to take effect."
        #     )
        self.accept()
