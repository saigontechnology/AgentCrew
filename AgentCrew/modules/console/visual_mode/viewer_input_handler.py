"""Input handler for visual mode viewer."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

if TYPE_CHECKING:
    from .viewer_ui import VisualModeUI


class VisualModeInputHandler:
    """Handles keyboard input for the visual mode viewer."""

    def __init__(
        self,
        ui: VisualModeUI,
        on_copy: Callable[[str], None] | None = None,
    ):
        self._ui = ui
        self._running = False
        self._g_pressed = False
        self._on_copy = on_copy

    def _create_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add(Keys.Up)
        @kb.add("k")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("k")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("up")
            self._ui.render()

        @kb.add(Keys.Down)
        @kb.add("j")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("j")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("down")
            self._ui.render()

        @kb.add(Keys.Left)
        @kb.add("h")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("h")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("left")
            self._ui.render()

        @kb.add(Keys.Right)
        @kb.add("l")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("l")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("right")
            self._ui.render()

        @kb.add("w")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("w")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("word_forward")
            self._ui.render()

        @kb.add("b")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("b")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("word_backward")
            self._ui.render()

        @kb.add("0")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("0")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("line_start")
            self._ui.render()

        @kb.add("$")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("$")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("line_end")
            self._ui.render()

        @kb.add("g")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("g")
                self._ui.render()
                return
            if self._g_pressed:
                self._g_pressed = False
                self._ui.move_cursor("top")
                self._ui.render()
            else:
                self._g_pressed = True

        @kb.add("G")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("G")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.move_cursor("bottom")
            self._ui.render()

        @kb.add(Keys.ControlU)
        def _(event):
            if self._ui._search_mode:
                return
            self._g_pressed = False
            self._ui.move_cursor("half_up")
            self._ui.render()

        @kb.add(Keys.ControlD)
        def _(event):
            if self._ui._search_mode:
                return
            self._g_pressed = False
            self._ui.move_cursor("half_down")
            self._ui.render()

        @kb.add(Keys.PageUp)
        def _(event):
            if self._ui._search_mode:
                return
            self._g_pressed = False
            self._ui.move_cursor("page_up")
            self._ui.render()

        @kb.add(Keys.PageDown)
        def _(event):
            if self._ui._search_mode:
                return
            self._g_pressed = False
            self._ui.move_cursor("page_down")
            self._ui.render()

        @kb.add("v")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("v")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.toggle_visual_mode()
            self._ui.render()

        @kb.add("y")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("y")
                self._ui.render()
                return
            self._g_pressed = False
            text = self._ui.get_selected_text()
            if text and self._on_copy:
                self._on_copy(text)
            if self._ui._visual_mode:
                self._ui.toggle_visual_mode()
                self._ui.render()

        @kb.add("Y")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("Y")
                self._ui.render()
                return
            self._g_pressed = False
            if 0 <= self._ui._cursor_line < len(self._ui._lines):
                line_text = self._ui._lines[self._ui._cursor_line][0]
                if self._on_copy:
                    self._on_copy(line_text)

        @kb.add("/")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("/")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.start_search_mode()
            self._ui.render()

        @kb.add("n")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("n")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.next_search_match()
            self._ui.render()

        @kb.add("N")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("N")
                self._ui.render()
                return
            self._g_pressed = False
            self._ui.prev_search_match()
            self._ui.render()

        @kb.add(Keys.Enter)
        def _(event):
            if self._ui._search_mode:
                self._ui.exit_search_mode(clear_results=False)
                self._ui.render()
                return

        @kb.add(Keys.Backspace)
        def _(event):
            if self._ui._search_mode:
                self._ui.backspace_search()
                self._ui.render()
                return

        @kb.add(Keys.Escape)
        def _(event):
            self._g_pressed = False
            if self._ui._visual_mode:
                self._ui.toggle_visual_mode()
                self._ui.render()
            elif self._ui._search_mode:
                self._ui.exit_search_mode(clear_results=True)
                self._ui.render()
            else:
                event.app.exit()

        @kb.add("q")
        def _(event):
            if self._ui._search_mode:
                self._ui.append_search_char("q")
                self._ui.render()
                return
            self._g_pressed = False
            event.app.exit()

        @kb.add(Keys.ControlC)
        def _(event):
            self._g_pressed = False
            if self._ui._visual_mode:
                text = self._ui.get_selected_text()
                if text and self._on_copy:
                    self._on_copy(text)
                self._ui.toggle_visual_mode()
                self._ui.render()
            elif self._ui._search_mode:
                self._ui.exit_search_mode(clear_results=True)
                self._ui.render()
            else:
                event.app.exit()

        @kb.add(Keys.Any)
        def _(event):
            if self._ui._search_mode:
                char = event.data
                if char and char.isprintable():
                    self._ui.append_search_char(char)
                self._ui.render()
                return
            self._g_pressed = False

        return kb

    def run(self):
        self._running = True
        self._g_pressed = False

        self._ui.start_live()

        kb = self._create_key_bindings()

        try:
            session = PromptSession(key_bindings=kb)
            session.prompt("")
        except (KeyboardInterrupt, EOFError):
            pass
        except Exception as e:
            logger.error(f"Error in visual mode input handler: {e}")
        finally:
            self._ui.stop_live()

        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self):
        self._running = False
