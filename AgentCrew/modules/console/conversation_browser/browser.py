"""Conversation browser with split-panel interface.

Provides Rich-based UI for listing and loading conversations with preview.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.text import Text

from ..constants import RICH_STYLE_YELLOW
from .browser_input_handler import ConversationBrowserInputHandler
from .browser_ui import ConversationBrowserUI


class ConversationBrowser:
    """Interactive conversation browser with split-panel layout.

    This class orchestrates the UI rendering and input handling components
    to provide a complete interactive conversation browsing experience.
    """

    def __init__(
        self,
        console: Console,
        get_conversation_history: Callable[[str], list[dict[str, Any]]] | None = None,
        on_delete: Callable[[list[str]], bool] | None = None,
    ):
        """Initialize the conversation browser.

        Args:
            console: Rich console for rendering
            get_conversation_history: Optional callback to fetch full conversation history
            on_delete: Optional callback to delete conversations by IDs. Returns True if successful.
        """
        self._console = console
        self._ui = ConversationBrowserUI(
            console=console,
            get_conversation_history=get_conversation_history,
        )
        self._input_handler = ConversationBrowserInputHandler(
            ui=self._ui,
            on_delete=on_delete,
        )

    def set_conversations(self, conversations: list[dict[str, Any]]):
        """Set the conversations list to browse."""
        self._ui.set_conversations(conversations)

    def get_selected_conversation_id(self) -> str | None:
        """Get the ID of the currently selected conversation."""
        return self._ui.get_selected_conversation_id()

    def get_selected_conversation_index(self) -> int:
        """Get the 1-based index of the currently selected conversation."""
        return self._ui.get_selected_conversation_index()

    @property
    def ui(self) -> ConversationBrowserUI:
        """Access the UI component directly."""
        return self._ui

    @property
    def input_handler(self) -> ConversationBrowserInputHandler:
        """Access the input handler component directly."""
        return self._input_handler

    def show(self) -> str | None:
        """Show the interactive conversation browser.

        Returns:
            The ID of the selected conversation, or None if cancelled.
        """
        if not self._ui.conversations:
            self._console.print(
                Text("No conversations available.", style=RICH_STYLE_YELLOW)
            )
            return None

        return self._input_handler.run()
