"""
User input handling for console UI.
Manages user input threads, key bindings, and prompt sessions.
"""

from __future__ import annotations

import time
import threading
import queue
from threading import Thread, Event
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.formatted_text import HTML
from rich.text import Text

from loguru import logger
from AgentCrew.modules.clipboard.service import ClipboardService
from .completers import ChatCompleter
from .constants import (
    RICH_STYLE_YELLOW,
    RICH_STYLE_YELLOW_BOLD,
    RICH_STYLE_RED,
    RICH_STYLE_BLUE,
    PROMPT_CHAR,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .console_ui import ConsoleUI


class InputHandler:
    """Handles user input in a separate thread and manages key bindings."""

    def __init__(
        self,
        console_ui: ConsoleUI,
        swap_enter: bool = False,
    ):
        """Initialize the input handler."""
        self.console = console_ui.console
        self.ui_effects = console_ui.ui_effects
        self.message_handler = console_ui.message_handler
        self.display_handlers = console_ui.display_handlers
        self.clipboard_service = ClipboardService()

        # Threading for user input
        self._input_queue = queue.Queue()
        self._input_thread = None
        self._input_stop_event = Event()
        self._input_thread_lock = threading.Lock()
        self._current_prompt_session = None
        self._last_ctrl_c_time = 0
        self.is_message_processing = False
        self.swap_enter = swap_enter
        self._jumped_user_message = ""

        self.kb = self._setup_key_bindings()

    def _is_voice_recording_active(self) -> bool:
        voice_service = self.message_handler.voice_service
        return bool(voice_service and voice_service.is_recording())

    def _setup_key_bindings(self):
        """Set up key bindings for multiline input."""
        kb = KeyBindings()
        submit_keys = ("enter",) if self.swap_enter else ("escape", "enter")
        newline_keys = ("escape", "enter") if self.swap_enter else ("enter",)

        def _stop_voice_recording(event):
            if not self._is_voice_recording_active():
                return False
            event.app.exit(result="/end_voice")
            return True

        @kb.add(Keys.ControlS)
        @kb.add(*submit_keys)
        def _(event):
            """Submit on Ctrl+S."""
            if _stop_voice_recording(event):
                return
            if event.current_buffer.text.strip():
                if (
                    event.current_buffer.text == "/exit"
                    or event.current_buffer.text == "/quit"
                    or not self.is_message_processing
                ):
                    event.current_buffer.validate_and_handle()

        @kb.add(*newline_keys)
        def _(event):
            """Insert newline on Enter."""
            if _stop_voice_recording(event):
                return
            event.current_buffer.insert_text(f"\n{PROMPT_CHAR}")

        @kb.add("escape", "c")  # Alt+C
        def _(event):
            """Copy latest assistant response to clipboard."""
            # This will be handled by the main console UI
            pass

        @kb.add(Keys.ControlV)
        def _(event):
            """Handle Ctrl+V with image/binary detection."""
            try:
                # Check if clipboard contains image or binary content
                paste_result = self.clipboard_service.read_and_process_paste()

                if paste_result["success"]:
                    content_type = paste_result.get("type")

                    if content_type == "file_command":
                        # Insert the file command
                        file_command = paste_result["content"]

                        # Insert the file command into the current buffer
                        self._jumped_user_message = event.current_buffer.text
                        event.current_buffer.reset()
                        self._input_queue.put(file_command)
                        return

                # For regular text content, use default paste behavior
                event.current_buffer.paste_clipboard_data(
                    event.app.clipboard.get_data()
                )

            except Exception:
                # Fall back to default paste behavior if anything goes wrong
                try:
                    event.current_buffer.paste_clipboard_data(
                        event.app.clipboard.get_data()
                    )
                except Exception:
                    pass  # Ignore if even default paste fails

        @kb.add(Keys.ControlU)
        def _(event):
            if self.is_message_processing:
                self.ui_effects.scroll_live_display("up")

        @kb.add(Keys.ControlD)
        def _(event):
            if self.is_message_processing:
                self.ui_effects.scroll_live_display("down")

        @kb.add(Keys.Escape)
        def _(event):
            if _stop_voice_recording(event):
                return
            if self.message_handler.has_active_stream():
                try:
                    self.message_handler.request_stop_stream()
                except RuntimeError as e:
                    logger.warning(f"Error requesting stream stop: {e}")
                except Exception as e:
                    logger.warning(f"Exception requesting stream stop: {e}")

        @kb.add(Keys.ControlC)
        def _(event):
            """Handle Ctrl+C with confirmation for exit."""
            current_time = time.time()
            if (
                hasattr(self, "_last_ctrl_c_time")
                and current_time - self._last_ctrl_c_time <= 1
            ):
                # Don't try to join from within the same thread - just exit
                event.app.exit("__EXIT__")
            else:
                if self.message_handler.has_active_stream():
                    try:
                        self.message_handler.request_stop_stream()
                    except RuntimeError as e:
                        logger.warning(f"Error requesting stream stop: {e}")
                    except Exception as e:
                        logger.warning(f"Exception requesting stream stop: {e}")
                else:
                    self._last_ctrl_c_time = current_time
                    self.console.print(
                        Text(
                            "\nPress Ctrl+C again within 1 second to exit.",
                            style=RICH_STYLE_YELLOW,
                        )
                    )
                    self.display_handlers.print_prompt_prefix(
                        self.message_handler.agent.name,
                        self.message_handler.agent.get_model(),
                        self.message_handler.tool_manager.get_effective_yolo_mode(),
                    )
                    time.sleep(0.2)
                    self.clear_buffer()
                    prompt = Text(PROMPT_CHAR, style=RICH_STYLE_BLUE)
                    self.console.print(prompt, end="")

        @kb.add(Keys.Backspace)
        def _(event):
            if not event.current_buffer.text:
                voice_recording_active = self._is_voice_recording_active()
                prompt = Text(
                    PROMPT_CHAR
                    if not self.is_message_processing and not voice_recording_active
                    else "",
                    style=RICH_STYLE_BLUE,
                )
                if not self.is_message_processing and not voice_recording_active:
                    import sys

                    sys.stdout.write("\x1b[1A")  # cursor up one line
                    sys.stdout.write("\x1b[2K")
                    sys.stdout.write("\r")
                    self.display_handlers.print_divider("👤 YOU: ", with_time=True)
                    self.console.print(prompt, end="")
                else:
                    self.console.print("", end="\r")
            else:
                if (
                    event.current_buffer.text[-(len(PROMPT_CHAR) + 1) :]
                    == f"\n{PROMPT_CHAR}"
                ):
                    event.current_buffer.delete_before_cursor((len(PROMPT_CHAR) + 1))
                else:
                    event.current_buffer.delete_before_cursor()

        @kb.add(Keys.ControlUp)
        @kb.add(Keys.Escape, Keys.Up)
        @kb.add(Keys.ControlK)
        def _(event):
            """Navigate to previous history entry."""
            buffer = event.current_buffer
            document = buffer.document

            # Check if cursor is at the first line's start
            cursor_position = document.cursor_position
            if document.cursor_position_row == 0 and cursor_position <= len(
                document.current_line
            ):
                # Get previous history entry
                prev_entry = self.message_handler.history_manager.get_previous()
                if prev_entry is not None:
                    # Replace current text with history entry
                    buffer.text = prev_entry
                    # Move cursor to end of text
                    buffer.cursor_position = len(prev_entry)
            else:
                # Regular up arrow behavior - move cursor up
                buffer.cursor_up()

        @kb.add(Keys.ControlDown)
        @kb.add(Keys.Escape, Keys.Down)
        @kb.add(Keys.ControlJ)
        def _(event):
            """Navigate to next history entry if cursor is at last line."""
            buffer = event.current_buffer
            document = buffer.document

            # Check if cursor is at the last line
            if document.cursor_position_row == document.line_count - 1:
                # Get next history entry
                next_entry = self.message_handler.history_manager.get_next()
                if next_entry is not None:
                    # Replace current text with history entry
                    buffer.text = next_entry
                    # Move cursor to end of text
                    buffer.cursor_position = len(next_entry)
            else:
                # Regular down arrow behavior - move cursor down
                buffer.cursor_down()

        return kb

    def clear_buffer(self):
        if self._current_prompt_session:
            self._current_prompt_session.app.current_buffer.reset()
            if self._jumped_user_message:
                self._current_prompt_session.app.current_buffer.insert_text(
                    self._jumped_user_message, overwrite=True
                )
                self._jumped_user_message = ""
            self._current_prompt_session.message = HTML(PROMPT_CHAR)
            self._current_prompt_session.app.invalidate()
            if not self.is_message_processing and not self._is_voice_recording_active():
                self.display_handlers.print_divider("👤 YOU: ", with_time=True)

    def get_choice_input(self, message: str, values: list[str], default=None) -> str:
        from prompt_toolkit.shortcuts import choice
        from prompt_toolkit.styles import Style

        style = Style.from_dict(
            {
                "frame.border": "#884444",
                "selected-option": "bold",
            }
        )
        kb = KeyBindings()

        @kb.add(Keys.ControlC)
        @kb.add("escape")
        def _(event):
            event.app.exit(result="", style="class:accepted")

        return choice(
            message=HTML(f"<ansiyellow>{message}</ansiyellow> "),
            options=[(v, v) for v in values],
            default=default,
            style=style,
            key_bindings=kb,
            show_frame=True,
        )

    def get_prompt_input(self, prompt_message: str, default: str = "") -> str:
        from prompt_toolkit import prompt

        kb = KeyBindings()

        @kb.add(Keys.ControlC)
        @kb.add("escape")
        def _(event):
            event.current_buffer.text = ""
            event.current_buffer.validate_and_handle()

        @kb.add(Keys.ControlS)
        def _(event):
            """Submit on Ctrl+S."""
            event.current_buffer.validate_and_handle()

        return prompt(
            HTML(f"<ansiblue>{prompt_message}</ansiblue> "),
            default=default,
            key_bindings=kb,
            multiline=True,
            completer=ChatCompleter(self.message_handler),
        )

    def _input_thread_worker(self):
        """Worker thread for handling user input."""
        while not self._input_stop_event.is_set():
            try:
                session = PromptSession(
                    key_bindings=self.kb,
                    completer=ChatCompleter(self.message_handler),
                    refresh_interval=0.2,
                )
                self._current_prompt_session = session

                voice_recording_active = self._is_voice_recording_active()
                if not self.is_message_processing and not voice_recording_active:
                    self.display_handlers.print_divider("👤 YOU: ", with_time=True)
                prompt_text = (
                    HTML(PROMPT_CHAR)
                    if not self.is_message_processing and not voice_recording_active
                    else ""
                )
                user_input = session.prompt(prompt_text)

                if not user_input:
                    continue

                user_input = user_input.replace(f"\n{PROMPT_CHAR}", "\n")

                self.message_handler.history_manager.reset_position()

                self._input_queue.put(user_input.rstrip())
                self.is_message_processing = True
                self.display_handlers.print_divider()

            except KeyboardInterrupt:
                # Handle Ctrl+C in input thread
                current_time = time.time()
                if (
                    hasattr(self, "_last_ctrl_c_time")
                    and current_time - self._last_ctrl_c_time < 2
                ):
                    self._input_queue.put("__EXIT__")
                    break
                else:
                    self._last_ctrl_c_time = current_time
                    self._input_queue.put("__INTERRUPT__")
                    continue
            except Exception as e:
                self._input_queue.put(f"__ERROR__:{str(e)}")
                break

    def _start_input_thread(self):
        """Start the input thread if not already running."""
        with self._input_thread_lock:
            if self._input_thread is not None and self._input_thread.is_alive():
                self._stop_input_thread_locked()
            self._input_stop_event.clear()
            self._input_thread = Thread(target=self._input_thread_worker, daemon=True)
            self._input_thread.start()

    def _stop_input_thread(self):
        """Stop the input thread cleanly."""
        with self._input_thread_lock:
            self._stop_input_thread_locked()

    def _stop_input_thread_locked(self):
        """Internal stop implementation — caller must hold _input_thread_lock."""
        if not self._input_thread or not self._input_thread.is_alive():
            return

        if threading.current_thread() == self._input_thread:
            self._input_stop_event.set()
            return

        self._input_stop_event.set()
        if self._current_prompt_session:
            try:
                if (
                    hasattr(self._current_prompt_session, "app")
                    and self._current_prompt_session.app
                ):
                    self._current_prompt_session.app.exit()
            except Exception:
                pass

        self._input_thread.join(timeout=1.5)

        if self._input_thread.is_alive():
            logger.warning(
                "Input thread did not stop within 3s timeout — "
                "forcing continuation. Old thread may compete for stdin."
            )

    def set_current_buffer(self, content: str):
        self._jumped_user_message = content

    def get_user_input(self):
        """
        Get multiline input from the user with support for command history.
        Now runs in a separate thread to allow events to display during input.

        Returns:
            The user input as a string.
        """
        # Start input thread if not already running
        if self._input_thread is None or not self._input_thread.is_alive():
            self.display_handlers.print_prompt_prefix(
                self.message_handler.agent.name,
                self.message_handler.agent.get_model(),
                self.message_handler.tool_manager.get_effective_yolo_mode(),
            )
            self._start_input_thread()
        else:
            voice_recording_active = self._is_voice_recording_active()
            if not voice_recording_active:
                self.display_handlers.print_prompt_prefix(
                    self.message_handler.agent.name,
                    self.message_handler.agent.get_model(),
                    self.message_handler.tool_manager.get_effective_yolo_mode(),
                )
            self.clear_buffer()

        # Wait for input while allowing events to be processed
        while True:
            try:
                # Check for input with a short timeout to allow event processing
                user_input = self._input_queue.get(timeout=0.1)

                # Add None check here
                if user_input is None:
                    continue

                if user_input == "__EXIT__":
                    self.console.print(
                        Text(
                            "\n🎮 Confirmed exit. Goodbye!",
                            style=RICH_STYLE_YELLOW_BOLD,
                        )
                    )
                    self._stop_input_thread()
                    raise SystemExit(0)
                elif user_input == "__INTERRUPT__":
                    self.console.print(
                        Text(
                            "\n🎮 Chat interrupted. Press Ctrl+C again within 1 second to exit.",
                            style=RICH_STYLE_YELLOW_BOLD,
                        )
                    )
                    return ""
                elif user_input.startswith("__ERROR__:"):
                    error_msg = user_input[10:]  # Remove "__ERROR__:" prefix
                    self.console.print(
                        Text(f"\nInput error: {error_msg}", style=RICH_STYLE_RED)
                    )
                    return ""
                else:
                    return user_input

            except queue.Empty:
                # No input yet, continue waiting
                continue
            except KeyboardInterrupt:
                # Handle KeyboardInterrupt from the prompt session exit
                self.console.print(
                    Text(
                        "\n🎮 Confirmed exit. Goodbye!",
                        style=RICH_STYLE_YELLOW_BOLD,
                    )
                )
                self._stop_input_thread()
                raise SystemExit(0)

    def stop(self):
        """Stop the input handler and clean up."""
        self._stop_input_thread()
