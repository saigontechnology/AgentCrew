from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QTextEdit,
)

from AgentCrew.modules.clipboard.service import ClipboardService


class PasteAwareTextEdit(QTextEdit):
    """Custom QTextEdit that handles paste events to detect images and binary content."""

    image_inserted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clipboard_service = ClipboardService()

    def insertFromMimeData(self, source):
        """Override paste behavior to detect and handle images/binary content."""
        try:
            # Check clipboard content using our service
            paste_result = self.clipboard_service.read_and_process_paste()

            if paste_result["success"]:
                content_type = paste_result.get("type")

                if content_type == "file_command":
                    # It's an image or binary file - use the file command
                    file_command = paste_result["content"]

                    self.image_inserted.emit(file_command)

                    # Show status message

                    return  # Don't call parent method

                elif content_type == "text":
                    # Regular text content - let the parent handle it normally
                    super().insertFromMimeData(source)
                    return

                else:
                    # Other content types (like base64 image) - handle normally for now
                    super().insertFromMimeData(source)
                    return
            else:
                # Failed to read clipboard, fall back to default behavior
                super().insertFromMimeData(source)

        except Exception as e:
            # If anything goes wrong, fall back to default paste behavior
            print(f"Error in paste handling: {e}")
            super().insertFromMimeData(source)
