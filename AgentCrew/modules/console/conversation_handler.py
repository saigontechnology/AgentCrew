"""
Conversation handling for console UI.
Manages conversation loading, listing, and display functionality.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from .constants import RICH_STYLE_RED, RICH_STYLE_YELLOW

if TYPE_CHECKING:
    from .console_ui import ConsoleUI


class ConversationHandler:
    """Handles conversation-related operations for the console UI."""

    def __init__(self, console_ui: ConsoleUI):
        """Initialize the conversation handler."""
        self._console_ui = console_ui
        self.console = console_ui.console
        self.display_handlers = console_ui.display_handlers
        self._cached_conversations = []

    @property
    def _message_handler(self):
        """Get message handler from console UI."""
        return self._console_ui.message_handler

    def handle_load_conversation(self, load_arg: str, message_handler):
        """
        Handle loading a conversation by number or ID.

        Args:
            load_arg: Either a conversation number (from the list) or a conversation ID
            message_handler: The message handler instance
        """
        # First check if we have a list of conversations cached
        if not self._cached_conversations:
            # If not, get the list first
            self._cached_conversations = message_handler.list_conversations_with_forks()

        try:
            self.display_handlers.display_divider()
            # Check if the argument is a number (index in the list)
            if load_arg.isdigit():
                index = int(load_arg) - 1  # Convert to 0-based index
                if 0 <= index < len(self._cached_conversations):
                    convo_id = self._cached_conversations[index].get("id")
                    if convo_id:
                        self.console.print(
                            Text(
                                f"Loading conversation #{load_arg}...",
                                style=RICH_STYLE_YELLOW,
                            )
                        )
                        messages = message_handler.load_conversation(convo_id)
                        if messages:
                            self.display_handlers.display_loaded_conversation(
                                messages, message_handler.agent.name
                            )
                        return
                self.console.print(
                    Text(
                        "Invalid conversation number. Use '/list' to see available conversations.",
                        style=RICH_STYLE_RED,
                    )
                )
            else:
                # Assume it's a conversation ID
                self.console.print(
                    Text(
                        f"Loading conversation with ID: {load_arg}...",
                        style=RICH_STYLE_YELLOW,
                    )
                )
                messages = message_handler.load_conversation(load_arg)
                if messages:
                    self.display_handlers.display_loaded_conversation(
                        messages, message_handler.agent.name
                    )

            self.console.print(
                Text("End of conversation history\n", style=RICH_STYLE_YELLOW)
            )
        except Exception as e:
            self.console.print(
                Text(f"Error loading conversation: {e!s}", style=RICH_STYLE_RED)
            )

    def update_cached_conversations(self, conversations: list[dict[str, Any]]):
        """Update the cached conversations list."""
        self._cached_conversations = conversations

    def get_cached_conversations(self):
        """Get the cached conversations list."""
        return self._cached_conversations

    def get_conversation_history(
        self, conversation_id: str
    ) -> list[dict[str, Any]] | None:
        """Get conversation history for preview in browser."""
        if self._message_handler.persistent_service:
            return self._message_handler.persistent_service.get_conversation_history(
                conversation_id
            )
        return None

    def delete_conversations(self, conversation_ids: list[str]) -> bool:
        """Delete conversations by their IDs.

        Args:
            conversation_ids: list of conversation IDs to delete

        Returns:
            True if all deletions were successful
        """
        if not conversation_ids:
            return False
        success = True
        for convo_id in conversation_ids:
            if not self._message_handler.delete_conversation_by_id(convo_id):
                success = False
        return success
