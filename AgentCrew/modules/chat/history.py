import os
from typing import Optional
from loguru import logger

# Default constants
DEFAULT_HISTORY_FILE = os.path.abspath(".chat_histories")
DEFAULT_HISTORY_LIMIT = 1000
ENTRY_DELIMITER = "\n---ENTRY---\n"


class ConversationTurn:
    """Represents a single turn in the conversation."""

    def __init__(self, user_message, message_index):
        """
        Initialize a conversation turn.

        Args:
            user_message: The user's message
            assistant_response: The assistant's response
            message_index: The index of the last message in this turn
        """
        self.user_message_preview = self._extract_preview(user_message)
        self.message_index = message_index  # Store index instead of full message copy

    def _extract_preview(self, message, max_length=50):
        """Extract a preview of the message for display in completions."""
        # Get the text content from the user message
        if isinstance(message, dict) and "content" in message:
            content = message["content"]
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        break
                else:
                    text = str(content)
            else:
                text = str(content)
        else:
            text = str(message)

        # Truncate and format the preview
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text

    def get_preview(self, max_length=50):
        """Get a preview of the user message for display in completions."""
        return self.user_message_preview


class ChatHistoryManager:
    """Manages chat history for the interactive chat interface."""

    def __init__(
        self,
        history_file: str = DEFAULT_HISTORY_FILE,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ):
        """
        Initialize the chat history manager.

        Args:
            history_file: Path to the history file
            history_limit: Maximum number of entries to store
        """
        self.history_file = history_file
        self.history_limit = history_limit
        self.history = []
        self.position = -1
        self._load_history()

    def _load_history(self) -> None:
        """Load history from file if it exists."""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content:
                        self.history = content.split(ENTRY_DELIMITER)
                        # Remove any empty entries
                        self.history = [
                            entry for entry in self.history if entry.strip()
                        ]
                        # Limit history size
                        if len(self.history) > self.history_limit:
                            self.history = self.history[-self.history_limit :]
            self.position = len(self.history)
        except Exception as e:
            logger.error(f"Failed to load chat history: {str(e)}")
            self.history = []
            self.position = 0

    def _save_history(self) -> None:
        """Save history to file."""
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                f.write(ENTRY_DELIMITER.join(self.history))
        except Exception as e:
            logger.error(f"Failed to save chat history: {str(e)}")

    def add_entry(self, entry: str) -> None:
        """
        Add a new entry to the history.

        Args:
            entry: The message to add to history
        """
        # Don't add empty entries or duplicates of the last entry
        if not entry.strip() or (self.history and self.history[-1] == entry):
            return

        self.history.append(entry)

        # Limit history size
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit :]

        # Reset position to end of history
        self.position = len(self.history)

        # Save history to file
        self._save_history()

    def get_previous(self) -> Optional[str]:
        """
        Get the previous entry in history.

        Returns:
            The previous entry or None if at the beginning
        """
        if not self.history or self.position <= 0:
            return None

        self.position -= 1
        return self.history[self.position]

    def get_next(self) -> Optional[str]:
        """
        Get the next entry in history.

        Returns:
            The next entry or None if at the end
        """
        if not self.history or self.position >= len(self.history) - 1:
            self.position = len(self.history)
            return ""

        self.position += 1
        return self.history[self.position]

    def reset_position(self) -> None:
        """Reset the position to the end of history."""
        self.position = len(self.history)
