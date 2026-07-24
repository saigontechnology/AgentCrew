import mimetypes
import os
import sys

import markdown
import pyperclip
import qtawesome as qta
from loguru import logger
from PySide6.QtCore import QByteArray, QFileInfo, Qt, QTimer
from PySide6.QtGui import QPixmap, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from AgentCrew.modules.gui.themes import StyleProvider

# File display constants
IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
MAX_IMAGE_WIDTH = 600  # Maximum width for displayed images


class MessageBubble(QFrame):
    """A custom widget to display messages as bubbles."""

    def __init__(
        self,
        text,
        is_user=True,
        agent_name="ASSISTANT",
        parent=None,
        message_index=None,
        is_thinking=False,
        is_consolidated=False,
    ):
        super().__init__(parent)

        # Store message index for rollback functionality
        self.message_index = message_index
        self.is_user = is_user
        self.is_thinking = is_thinking
        self.is_consolidated = is_consolidated
        self.file_path = None
        self.is_file_processed = False
        self.remove_button = None

        # Initialize style provider
        self.style_provider = StyleProvider()

        # Add streaming support
        self.is_streaming = False
        self.raw_text_buffer = ""
        self.streaming_timer = QTimer(self)
        self.streaming_timer.timeout.connect(self._render_next_character)
        self.streaming_text = ""

        # Setup frame appearance - flat modern look
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setLineWidth(0)
        self.rollback_button: QPushButton | None = None

        # Set background color based on sender
        if is_user:
            self.setStyleSheet(self.style_provider.get_user_bubble_style())
        elif is_thinking:  # Check is_thinking before general assistant bubble
            self.setStyleSheet(self.style_provider.get_thinking_bubble_style())
        elif is_consolidated:  # Special styling for consolidated messages
            self.setStyleSheet(self.style_provider.get_consolidated_bubble_style())
        else:  # Assistant bubble
            self.setStyleSheet(self.style_provider.get_assistant_bubble_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # Add sender label - Use agent_name for non-user messages
        label_text = "You" if is_user else agent_name
        if is_thinking:
            label_text = f"{agent_name} is thinking..."
        elif is_consolidated:
            label_text = "Conversation Summary"

        sender_label = QLabel(label_text)
        if is_user:
            sender_label.setStyleSheet(
                self.style_provider.get_user_sender_label_style()
            )
        elif is_thinking:
            sender_label.setStyleSheet(
                self.style_provider.get_thinking_sender_label_style()
            )
        else:
            sender_label.setStyleSheet(
                self.style_provider.get_assistant_sender_label_style()
            )
        layout.addWidget(sender_label)

        # Create label with HTML support
        self.message_label = QLabel()
        self.message_label.setTextFormat(Qt.TextFormat.RichText)
        self.message_label.setWordWrap(True)
        self.message_label.setOpenExternalLinks(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )

        font = self.message_label.font()
        font.setPixelSize(14)
        font.setFamilies(
            ["Inter", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"]
        )
        self.message_label.setFont(font)

        # Set different text color for message content based on bubble type
        if is_user:
            self.message_label.setStyleSheet(
                self.style_provider.get_user_message_label_style()
            )
        elif is_thinking:
            self.message_label.setStyleSheet(
                self.style_provider.get_thinking_message_label_style()
            )
        else:
            self.message_label.setStyleSheet(
                self.style_provider.get_assistant_message_label_style()
            )

        # Setup context menu
        self.message_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.message_label.customContextMenuRequested.connect(self.show_context_menu)

        if text is not None:
            layout.addWidget(self.message_label)

        # Set size policies
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum
        )

        # For user messages, add hover button functionality
        if is_user and message_index is not None:
            # Set up hover events for rollback button
            self.setMouseTracking(True)

            # Create rollback button with icon only
            rollback_icon = qta.icon("fa6s.clock-rotate-left", color="white")

            rollback_button = QPushButton(rollback_icon, "", self)
            rollback_button.setToolTip("Rollback to this message")
            rollback_font = rollback_button.font()
            rollback_font.setPixelSize(12)
            rollback_button.setFont(rollback_font)
            rollback_button.setStyleSheet(
                self.style_provider.get_rollback_button_style()
            )
            rollback_button.hide()
            self.rollback_button = rollback_button

        # For file bubbles, add remove button functionality
        self.unconsolidate_button = None
        self.consolidated_button = None

        if not self.is_consolidated:
            # Create consolidated button with icon only
            consolidated_icon = qta.icon("fa6s.wand-magic-sparkles", color="white")
            consolidated_button = QPushButton(
                consolidated_icon, "", self
            )  # Pin/bookmark icon
            consolidated_font = consolidated_button.font()
            consolidated_font.setPixelSize(9)
            consolidated_button.setFont(consolidated_font)
            consolidated_button.setToolTip("Consolidate to this message")
            consolidated_button.setStyleSheet(
                self.style_provider.get_consolidated_button_style()
            )
            consolidated_button.hide()

            # Store the buttons as properties of the message bubble
            self.consolidated_button = consolidated_button
        else:
            # Create unconsolidate button for consolidated messages
            unconsolidate_icon = qta.icon("fa6s.rotate-left", color="white")
            unconsolidate_button = QPushButton(unconsolidate_icon, "", self)
            unconsolidate_font = unconsolidate_button.font()
            unconsolidate_font.setPixelSize(9)
            unconsolidate_button.setFont(unconsolidate_font)
            unconsolidate_button.setToolTip("Unconsolidate this message")
            unconsolidate_button.setStyleSheet(
                self.style_provider.get_unconsolidate_button_style()
            )
            unconsolidate_button.hide()

            # Store the buttons as properties of the message bubble
            self.unconsolidate_button = unconsolidate_button

        # Override enter and leave events
        original_enter_event = self.enterEvent
        original_leave_event = self.leaveEvent

        def enter_event_wrapper(event):
            if (
                self.rollback_button
                or self.consolidated_button
                or self.remove_button
                or self.unconsolidate_button
            ):
                # Position buttons in the top right corner with spacing
                button_width = 30
                button_height = 30
                spacing = 5
                button_count = 0

                # Count visible buttons to calculate positions
                if self.consolidated_button:
                    button_count += 1
                if self.unconsolidate_button:
                    button_count += 1
                if self.rollback_button:
                    button_count += 1
                if self.remove_button and not self.is_file_processed:
                    button_count += 1

                current_position = 0

                # Position unconsolidate button first (rightmost) for consolidated messages
                if self.unconsolidate_button:
                    self.unconsolidate_button.setGeometry(
                        self.width()
                        - (button_width * (current_position + 1))
                        - (spacing * current_position)
                        - 5,
                        5,
                        button_width,
                        button_height,
                    )
                    self.unconsolidate_button.show()
                    current_position += 1

                # Position consolidated button first (rightmost) for non-consolidated messages
                if self.consolidated_button:
                    self.consolidated_button.setGeometry(
                        self.width()
                        - (button_width * (current_position + 1))
                        - (spacing * current_position)
                        - 5,
                        5,
                        button_width,
                        button_height,
                    )
                    self.consolidated_button.show()
                    current_position += 1

                # Position rollback button
                if self.rollback_button:
                    self.rollback_button.setGeometry(
                        self.width()
                        - (button_width * (current_position + 1))
                        - (spacing * current_position)
                        - 5,
                        5,
                        button_width,
                        button_height,
                    )
                    self.rollback_button.show()
                    current_position += 1

                # Position remove button (only show if file not processed)
                if self.remove_button and not self.is_file_processed:
                    self.remove_button.setGeometry(
                        self.width()
                        - (button_width * (current_position + 1))
                        - (spacing * current_position)
                        - 5,
                        5,
                        button_width,
                        button_height,
                    )
                    self.remove_button.show()

            if original_enter_event:
                original_enter_event(event)

        def leave_event_wrapper(event):
            if self.rollback_button:
                self.rollback_button.hide()
            if self.consolidated_button:
                self.consolidated_button.hide()
            if self.unconsolidate_button:
                self.unconsolidate_button.hide()
            if self.remove_button:
                self.remove_button.hide()
            if original_leave_event:
                original_leave_event(event)

        self.enterEvent = enter_event_wrapper
        self.leaveEvent = leave_event_wrapper

        # Make sure button is properly positioned when message bubble is resized
        original_resize_event = self.resizeEvent

        def resize_event_wrapper(event):
            button_width = 30
            button_height = 30
            spacing = 5
            current_position = 0

            # Position unconsolidate button first (rightmost) for consolidated messages
            if self.unconsolidate_button and self.unconsolidate_button.isVisible():
                self.unconsolidate_button.setGeometry(
                    self.width()
                    - (button_width * (current_position + 1))
                    - (spacing * current_position)
                    - 5,
                    5,
                    button_width,
                    button_height,
                )
                current_position += 1

            # Position consolidated button first (rightmost) for non-consolidated messages
            if self.consolidated_button and self.consolidated_button.isVisible():
                self.consolidated_button.setGeometry(
                    self.width()
                    - (button_width * (current_position + 1))
                    - (spacing * current_position)
                    - 5,
                    5,
                    button_width,
                    button_height,
                )
                current_position += 1

            # Position rollback button
            if (
                hasattr(self, "rollback_button")
                and self.rollback_button
                and self.rollback_button.isVisible()
            ):
                self.rollback_button.setGeometry(
                    self.width()
                    - (button_width * (current_position + 1))
                    - (spacing * current_position)
                    - 5,
                    5,
                    button_width,
                    button_height,
                )
                current_position += 1

            # Position remove button
            if (
                hasattr(self, "remove_button")
                and self.remove_button
                and self.remove_button.isVisible()
            ):
                self.remove_button.setGeometry(
                    self.width()
                    - (button_width * (current_position + 1))
                    - (spacing * current_position)
                    - 5,
                    5,
                    button_width,
                    button_height,
                )

            if original_resize_event:
                original_resize_event(event)

        self.resizeEvent = resize_event_wrapper

        # Set the text content (convert Markdown to HTML)
        if text:
            self.raw_text = text
            self.set_text(text)

    def show_context_menu(self, position):
        """Create and show context menu with standard text actions plus Copy as Markdown."""
        menu = QMenu(self)

        copy_action = menu.addAction("Copy Selected as plain text")
        copy_action.triggered.connect(self._copy_selected_text)

        copy_html_action = menu.addAction("Copy Selected as HTML")
        copy_html_action.triggered.connect(self._copy_selected_html)

        if not self.message_label.hasSelectedText():
            copy_html_action.setEnabled(False)
            copy_action.setEnabled(False)

        select_all_action = menu.addAction("Select All")
        select_all_action.triggered.connect(self._select_all_text)

        menu.addSeparator()

        copy_markdown_action = menu.addAction("Copy all as Markdown")
        copy_markdown_action.triggered.connect(self.copy_as_markdown)

        copy_all_html_action = menu.addAction("Copy all as Html")
        copy_all_html_action.triggered.connect(self._copy_as_html)

        menu.setStyleSheet(self.style_provider.get_context_menu_style())

        menu.exec_(self.message_label.mapToGlobal(position))

    def _copy_as_html(self):
        html_content = self.message_label.text()
        doc = QTextDocument()
        doc.setHtml(html_content)
        if sys.platform == "win32":
            from AgentCrew.modules.gui.utils.wins_clipboard import (
                copy_html_to_clipboard as win_copy_html,
            )

            win_copy_html(doc.toHtml(), self.raw_text)
        elif sys.platform == "darwin":
            from AgentCrew.modules.gui.utils.macos_clipboard import (
                copy_html_to_clipboard as macos_copy_html,
            )

            macos_copy_html(doc.toHtml())
        else:
            pyperclip.copy(doc.toHtml())

    def _select_all_text(self):
        """Select all text in the message label."""
        try:
            html_content = self.message_label.text()
            if html_content:
                doc = QTextDocument()
                doc.setHtml(html_content)
                plain_text = doc.toPlainText()

                self.message_label.setSelection(0, len(plain_text))
        except Exception:
            logger.debug("Failed to process selection for copy")

    def _copy_selected_text(self):
        """Copy selected text as plain text."""
        try:
            selected_text = self.message_label.selectedText()
            if selected_text:
                pyperclip.copy(selected_text)
        except Exception:
            logger.debug("Failed to copy selected text")

    def _copy_selected_html(self):
        """Copy selected text with HTML formatting preserved."""
        try:
            if not self.message_label.hasSelectedText():
                return

            start = self.message_label.selectionStart()
            selected_plain_text = self.message_label.selectedText()

            if start >= 0 and selected_plain_text:
                html_content = self.message_label.text()

                doc = QTextDocument()
                doc.setHtml(html_content)

                # Create cursor and set selection based on plain text positions
                cursor = QTextCursor(doc)
                cursor.setPosition(start)
                cursor.setPosition(
                    start + len(selected_plain_text), QTextCursor.MoveMode.KeepAnchor
                )

                fragment = cursor.selection()
                selected_html = fragment.toHtml()

                if sys.platform == "win32":
                    from AgentCrew.modules.gui.utils.wins_clipboard import (
                        copy_html_to_clipboard as win_copy_html,
                    )

                    win_copy_html(selected_html, selected_plain_text)
                elif sys.platform == "darwin":
                    from AgentCrew.modules.gui.utils.macos_clipboard import (
                        copy_html_to_clipboard as macos_copy_html,
                    )

                    macos_copy_html(selected_html)
                else:
                    pyperclip.copy(selected_html)
        except Exception:
            try:
                selected_text = self.message_label.selectedText()
                if selected_text:
                    pyperclip.copy(selected_text)
            except Exception:
                logger.debug("HTML copy fallback to plain text failed")

    def copy_as_markdown(self):
        """Copy the raw markdown text to clipboard."""
        try:
            if hasattr(self, "raw_text") and self.raw_text:
                pyperclip.copy(self.raw_text)
        except Exception:
            logger.debug("Failed to copy markdown text")

    def set_text(self, text):
        """Set or update the text content of the message."""
        try:
            html_content = markdown.markdown(
                text,
                output_format="html",
                extensions=[
                    "tables",
                    "fenced_code",
                    "codehilite",
                    "nl2br",
                    "sane_lists",
                ],
            )
            html_content = (
                f"""<style>
                * {{ font-family: 'Inter', 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', sans-serif; line-height: 1.6; font-size: 14px; }}
                pre {{ white-space: pre-wrap; margin-bottom: 0; font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size: 13px; }}
                code {{ font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size: 13px; }}
                {self.style_provider.get_code_color_style()}
            </style>"""
                + html_content
            )
            self.message_label.setText(html_content)
        except Exception as e:
            print(f"Error rendering markdown: {e}")
            self.message_label.setText(text)

    def start_streaming(self):
        """Start character-by-character streaming mode."""
        self.is_streaming = True
        self.raw_text_buffer = ""
        self.streaming_text = ""

        self.message_label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.message_label.setText("")

    def update_streaming_text(self, streaming_text: str):
        """Add a chunk of text to the streaming queue."""
        if not streaming_text:  # Skip empty chunks
            return

        if not self.is_streaming:
            self.start_streaming()

        # Start the streaming timer if not active
        if not self.streaming_timer.isActive():
            self.streaming_timer.start(30)

        # Add characters to queue for smooth rendering
        self.streaming_text = streaming_text

    def _render_next_character(self):
        """Render the next character(s) from the queue."""
        if not self.streaming_text:
            return

        if self.streaming_text and self.streaming_text != self.message_label.text():
            self.message_label.setText(self.streaming_text)

    def _finalize_streaming(self):
        """Convert to formatted text once streaming is complete."""
        self.is_streaming = False
        if self.streaming_timer.isActive():
            self.streaming_timer.stop()

        self.message_label.setTextFormat(Qt.TextFormat.RichText)
        # Now convert to markdown with full formatting
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.set_text(self.raw_text_buffer)

    def stop_streaming(self):
        """Force stop streaming and finalize immediately."""
        if self.is_streaming:
            self._finalize_streaming()

    def append_text(self, text):
        """Update method to handle both streaming and normal modes."""
        self.set_text(text)

    def display_file(self, file_path: str):
        """Display a file in the message bubble based on its type."""
        if not os.path.exists(file_path):
            self.append_text(f"File not found: {file_path}")
            return

        self.file_path = file_path

        if self.file_path is not None:
            self.setMouseTracking(True)

            # Create remove button with icon
            remove_icon = qta.icon("fa6s.trash", color="white")

            remove_button = QPushButton(remove_icon, "", self)
            remove_button.setToolTip("Remove file from processing queue")
            remove_font = remove_button.font()
            remove_font.setPixelSize(12)
            remove_button.setFont(remove_font)
            remove_button.setStyleSheet(
                self.style_provider.get_rollback_button_style()  # Reuse the same style
            )
            remove_button.hide()
            self.remove_button = remove_button

        # Create a container for the file display
        file_container = QFrame(self)
        file_layout = QVBoxLayout(file_container)
        file_layout.setContentsMargins(1, 1, 1, 1)

        # Get file extension and determine file type
        _, file_extension = os.path.splitext(file_path)
        file_extension = file_extension.lower()
        file_name = os.path.basename(file_path)

        # Handle image files
        if file_extension in IMAGE_EXTENSIONS:
            # Create image label
            image_label = QLabel()
            pixmap = QPixmap(file_path)

            # Scale image if it's too large
            if pixmap.width() > MAX_IMAGE_WIDTH:
                pixmap = pixmap.scaledToWidth(
                    MAX_IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
                )

            image_label.setPixmap(pixmap)
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # Add file name above the image
            name_label = QLabel(file_name)
            if self.is_user:
                name_label.setStyleSheet(
                    self.style_provider.get_user_file_name_label_style()
                )
            else:
                name_label.setStyleSheet(
                    self.style_provider.get_assistant_file_name_label_style()
                )
            name_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

            file_layout.addWidget(name_label)
            file_layout.addWidget(image_label)
        else:
            # For non-image files, show an icon with the file name
            file_info = QFileInfo(file_path)
            icon_provider = QFileIconProvider()
            file_icon = icon_provider.icon(file_info)

            # Create horizontal layout for icon and file name
            icon_layout = QHBoxLayout()

            # Create icon label
            icon_label = QLabel()
            icon_label.setPixmap(file_icon.pixmap(48, 48))

            # Create file name label
            name_label = QLabel(file_name)
            if self.is_user:
                name_label.setStyleSheet(
                    self.style_provider.get_user_file_name_label_style()
                )
            else:
                name_label.setStyleSheet(
                    self.style_provider.get_assistant_file_name_label_style()
                )

            icon_layout.addWidget(icon_label)
            icon_layout.addWidget(name_label)
            icon_layout.addStretch(1)

            file_layout.addLayout(icon_layout)

            # Add file size and type information
            file_size = os.path.getsize(file_path)
            file_type = mimetypes.guess_type(file_path)[0] or "Unknown type"

            size_label = QLabel(f"Size: {self._format_file_size(file_size)}")
            type_label = QLabel(f"Type: {file_type}")

            if self.is_user:
                size_label.setStyleSheet(
                    self.style_provider.get_user_file_info_label_style()
                )
                type_label.setStyleSheet(
                    self.style_provider.get_user_file_info_label_style()
                )
            else:
                size_label.setStyleSheet(
                    self.style_provider.get_assistant_file_info_label_style()
                )
                type_label.setStyleSheet(
                    self.style_provider.get_assistant_file_info_label_style()
                )

            file_layout.addWidget(size_label)
            file_layout.addWidget(type_label)

        # Add the file container to the message layout
        self.layout().addWidget(file_container)

        # Force update and scroll
        self.updateGeometry()

    def _format_file_size(self, size_bytes):
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} bytes"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def add_metadata_header(self, header_text):
        """Add a metadata header to the consolidated message."""
        header_label = QLabel(header_text)
        header_label.setStyleSheet(
            self.style_provider.get_metadata_header_label_style()
        )
        # Insert header at position 1, just after the sender label
        self.layout().insertWidget(1, header_label)

    def mark_file_processed(self):
        """Mark the file as processed and disable the remove button."""
        self.is_file_processed = True
        if self.remove_button:
            self.remove_button.hide()
            self.remove_button.setEnabled(False)
            self.remove_button.setToolTip(
                "File has been processed and cannot be removed"
            )

    def display_base64_img(self, data: str):
        """
        Display a base64-encoded image in the message bubble.

        Args:
            data: Base64 image data in format 'data:mime_type;base64,data'
        """
        try:
            # Parse the data URL format
            if not data.startswith("data:"):
                raise ValueError("Invalid data URL format. Must start with 'data:'")

            # Extract mime type and base64 data
            header, encoded_data = data.split(",", 1)
            mime_type = header.split(";")[0].split(":")[1]

            if not mime_type.startswith("image/"):
                raise ValueError(
                    f"Unsupported mime type: {mime_type}. Only image types are supported."
                )

            # Create a container for the image display
            img_container = QFrame(self)
            img_layout = QVBoxLayout(img_container)
            img_layout.setContentsMargins(1, 1, 1, 1)

            # Create image label
            image_label = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(QByteArray.fromBase64(encoded_data.encode()))

            # Scale image if it's too large
            if pixmap.width() > MAX_IMAGE_WIDTH:
                pixmap = pixmap.scaledToWidth(
                    MAX_IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
                )

            image_label.setPixmap(pixmap)
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # Add a label indicating it's a base64 image
            name_label = QLabel("Image")
            if self.is_user:
                name_label.setStyleSheet(
                    self.style_provider.get_user_file_name_label_style()
                )
            else:
                name_label.setStyleSheet(
                    self.style_provider.get_assistant_file_name_label_style()
                )
            name_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

            img_layout.addWidget(name_label)
            img_layout.addWidget(image_label)

            # Add the image container to the message layout
            self.layout().addWidget(img_container)

            # Force update and scroll
            self.updateGeometry()

        except Exception as e:
            error_msg = f"Error displaying base64 image: {e!s}"
            print(error_msg)
            self.append_text(f"\n\n*{error_msg}*")
