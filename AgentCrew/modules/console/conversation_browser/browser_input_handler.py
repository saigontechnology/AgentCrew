"""Conversation browser input handling."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from loguru import logger

if TYPE_CHECKING:
    from .browser_ui import ConversationBrowserUI


class ConversationBrowserInputHandler:
    """Handles keyboard input for the conversation browser."""

    def __init__(
        self,
        ui: ConversationBrowserUI,
        on_select: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_delete: Callable[[list[str]], bool] | None = None,
    ):
        self._ui = ui
        self._running = False
        self._g_pressed = False
        self._d_pressed = False
        self._selected_id: str | None = None
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._on_delete = on_delete

    def _create_key_bindings(self) -> KeyBindings:
        """Create and configure key bindings for the browser."""
        kb = KeyBindings()

        @kb.add(Keys.Up)
        def _(event):
            if self._ui.search_mode:
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("up"):
                self._ui.render()

        @kb.add("k")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("k")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("up"):
                self._ui.render()

        @kb.add(Keys.Down)
        def _(event):
            if self._ui.search_mode:
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("down"):
                self._ui.render()

        @kb.add("j")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("j")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("down"):
                self._ui.render()

        @kb.add(Keys.ControlP)
        def _(event):
            if self._ui.search_mode:
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("up"):
                self._ui.render()

        @kb.add(Keys.ControlN)
        def _(event):
            if self._ui.search_mode:
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("down"):
                self._ui.render()

        @kb.add("g")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("g")
                self._ui.render()
                return
            self._d_pressed = False
            if self._g_pressed:
                self._g_pressed = False
                if self._ui.handle_navigation("top"):
                    self._ui.render()
            else:
                self._g_pressed = True

        @kb.add("G")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("G")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("bottom"):
                self._ui.render()

        @kb.add(Keys.ControlU)
        @kb.add(Keys.PageUp)
        def _(event):
            if self._ui.search_mode:
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("page_up"):
                self._ui.render()

        @kb.add(Keys.ControlD)
        @kb.add(Keys.PageDown)
        def _(event):
            if self._ui.search_mode:
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.handle_navigation("page_down"):
                self._ui.render()

        @kb.add("v")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("v")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.toggle_selection():
                self._ui.render()

        @kb.add("d")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("d")
                self._ui.render()
                return
            self._g_pressed = False
            if self._d_pressed:
                self._d_pressed = False
                self._handle_delete()
            else:
                self._d_pressed = True

        @kb.add("/")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("/")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            self._ui.start_search_mode()
            self._ui.render()

        @kb.add(Keys.Backspace)
        def _(event):
            if self._ui.search_mode:
                self._ui.backspace_search()
                self._ui.render()

        @kb.add(Keys.Enter)
        def _(event):
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.search_mode:
                self._ui.exit_search_mode(clear_filter=False)
                self._ui.render()
                return
            self._selected_id = self._ui.get_selected_conversation_id()
            event.app.exit()

        @kb.add("l")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("l")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            self._selected_id = self._ui.get_selected_conversation_id()
            event.app.exit()

        @kb.add(Keys.Escape)
        def _(event):
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.search_mode:
                self._ui.exit_search_mode(clear_filter=True)
                self._ui.render()
                return
            event.app.exit()

        @kb.add("q")
        def _(event):
            if self._ui.search_mode:
                self._ui.append_search_char("q")
                self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False
            event.app.exit()

        @kb.add(Keys.ControlC)
        def _(event):
            self._g_pressed = False
            self._d_pressed = False
            if self._ui.search_mode:
                self._ui.exit_search_mode(clear_filter=True)
                self._ui.render()
                return
            event.app.exit()

        @kb.add(Keys.Any)
        def _(event):
            if self._ui.search_mode:
                char = event.data
                if char and char.isprintable():
                    self._ui.append_search_char(char)
                    self._ui.render()
                return
            self._g_pressed = False
            self._d_pressed = False

        return kb

    def _handle_delete(self):
        """Handle delete action for selected or current conversation."""
        if not self._ui.conversations:
            return

        if self._ui.selected_items:
            indices_to_delete = list(self._ui.selected_items)
            ids_to_delete = self._ui.get_selected_conversation_ids()
        else:
            indices_to_delete = [self._ui.selected_index]
            current_id = self._ui.get_selected_conversation_id()
            ids_to_delete = [current_id] if current_id else []

        if not ids_to_delete:
            return

        if self._on_delete:
            success = self._on_delete(ids_to_delete)
            if success:
                self._ui.remove_conversations(indices_to_delete)
                self._ui.render()
        else:
            self._ui.remove_conversations(indices_to_delete)
            self._ui.render()

    def run(self) -> str | None:
        """Run the input handler loop.

        Returns:
            The ID of the selected conversation, or None if cancelled.
        """
        self._running = True
        self._g_pressed = False
        self._d_pressed = False
        self._selected_id = None

        self._ui.start_live()

        kb = self._create_key_bindings()

        try:
            session = PromptSession(key_bindings=kb)
            session.prompt("")
        except (KeyboardInterrupt, EOFError):
            pass
        except Exception as e:
            logger.error(f"Error in conversation browser input handler: {e}")
        finally:
            self._ui.stop_live()

        self._running = False
        return self._selected_id

    @property
    def is_running(self) -> bool:
        """Check if the input handler is currently running."""
        return self._running

    def stop(self):
        """Stop the input handler."""
        self._running = False
