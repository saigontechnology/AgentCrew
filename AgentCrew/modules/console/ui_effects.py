"""
UI effects and animations for console interface.
Handles loading animations, live displays, and other visual effects.
"""

import time
import threading
import itertools
import random
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from .constants import CODE_THEME
from AgentCrew.modules.chat import MessageHandler


class UIEffects:
    """Handles UI effects like loading animations and live displays."""

    def __init__(self, console: Console, message_handler: MessageHandler):
        """Initialize UI effects with a console instance."""
        self.console = console
        self.live = None
        self._loading_stop_event = None
        self._loading_thread = None
        self.message_handler = message_handler

    def _loading_animation(self, stop_event):
        """Display a loading animation in the terminal."""
        spinner = itertools.cycle(["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"])
        fun_words = [
            "Pondering",
            "Cogitating",
            "Ruminating",
            "Contemplating",
            "Brainstorming",
            "Calculating",
            "Processing",
            "Analyzing",
            "Deciphering",
            "Meditating",
            "Daydreaming",
            "Scheming",
            "Brewing",
            "Conjuring",
            "Inventing",
            "Imagining",
        ]
        fun_word = random.choice(fun_words)

        with Live(
            "", console=self.console, auto_refresh=True, refresh_per_second=10
        ) as live:
            while not stop_event.is_set():
                live.update(f"{fun_word} {next(spinner)}")
                time.sleep(0.1)  # Control animation speed
            live.update("")  # Clear the live display when done
            live.stop()  # Stop the live display

    def start_loading_animation(self):
        """Start the loading animation."""
        if self._loading_thread and self._loading_thread.is_alive():
            return  # Already running

        self._loading_stop_event = threading.Event()
        self._loading_thread = threading.Thread(
            target=self._loading_animation, args=(self._loading_stop_event,)
        )
        self._loading_thread.daemon = True
        self._loading_thread.start()

    def stop_loading_animation(self):
        """Stop the loading animation."""
        if self._loading_stop_event:
            self._loading_stop_event.set()
            self._loading_stop_event = None
        if self._loading_thread and self._loading_thread.is_alive():
            self._loading_thread.join(timeout=0.5)
            self._loading_thread = None

    def start_streaming_response(self, agent_name: str):
        """Start streaming the assistant's response."""
        from .constants import RICH_STYLE_GREEN_BOLD
        from rich.text import Text

        self.console.print(
            Text(f"🤖 {agent_name.upper()}:", style=RICH_STYLE_GREEN_BOLD)
        )
        self.live = Live("", console=self.console, vertical_overflow="crop")
        self.live.start()

    def update_live_display(self, chunk: str):
        """Update the live display with a new chunk of the response."""
        if not self.live:
            self.start_streaming_response(self.message_handler.agent.name)

        updated_text = chunk

        # Only show the last part that fits in the console
        lines = updated_text.split("\n")
        height_limit = (
            self.console.size.height - 10
        )  # leave some space for other elements
        if len(lines) > height_limit:
            lines = lines[-height_limit:]

        if self.live:
            self.live.update(Markdown("\n".join(lines), code_theme=CODE_THEME))

    def finish_live_update(self):
        """Stop the live update display."""
        if self.live:
            self.console.print(self.live.get_renderable())
            self.live.update("")
            self.live.stop()
            self.live = None

    def finish_response(self, response: str):
        """Finalize and display the complete response."""
        if self.live:
            self.live.update("")
            self.live.stop()
            self.live = None

        # Replace \n with two spaces followed by \n for proper Markdown line breaks
        markdown_formatted_response = response.replace("\n", "  \n")
        self.console.print(Markdown(markdown_formatted_response, code_theme=CODE_THEME))

    def cleanup(self):
        """Clean up all running effects."""
        self.stop_loading_animation()
        self.finish_live_update()
