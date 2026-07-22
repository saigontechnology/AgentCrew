"""
UI effects and animations for console interface.
Handles loading animations, live displays, and other visual effects.
"""

from __future__ import annotations

import colorsys
import math
import time
import threading
import itertools
import random
from rich.live import Live
from rich.box import HORIZONTALS
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .constants import (
    CODE_THEME,
    RICH_STYLE_GRAY,
    RICH_STYLE_GREEN,
    COMMAND_HELP_MESSAGES,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .console_ui import ConsoleUI


class UIEffects:
    """Handles UI effects like loading animations and live displays."""

    EVOLUTION_FRAMES = [
        [
            "        ╭──────╮        ",
            "        │ ◠  ◠ │        ",
            "        │  ──  │        ",
            "        ╰──────╯        ",
            "         /   \\         ",
            "        /     \\        ",
            "       ╱       ╲        ",
        ],
        [
            "       ╭────────╮       ",
            "       │  ◠  ◠  │       ",
            "       │   ──   │       ",
            "       ╰────────╯       ",
            "        /     \\        ",
            "       / ·   · \\       ",
            "      ╱         ╲       ",
        ],
        [
            "    ·  ╭────────╮  ·    ",
            "   ·   │  ◉  ◉  │   ·   ",
            "  ·    │   ──   │    ·  ",
            "   ·   ╰────────╯   ·   ",
            "    ·   /     \\   ·    ",
            "   ·   / ·· ·· \\   ·   ",
            "  ·   ╱         ╲  ·    ",
        ],
        [
            "  ✦ · ╭──────────╮ · ✦ ",
            "  · ✦ │  ★    ★  │ ✦ · ",
            "  ✦ · │    ──    │ · ✦ ",
            "  · ✦ ╰──────────╯ ✦ · ",
            "  ✦ ·  /       \\   · ✦ ",
            "  · ✦ / ✦·   ·✦ \\  ✦ · ",
            "  ✦ ·╱           ╲ · ✦ ",
        ],
        [
            " ✦✦✦╭────────────╮✦✦✦  ",
            " ✦✦ │   ★    ★   │ ✦✦  ",
            " ✦  │     ══     │  ✦  ",
            " ✦✦ ╰────────────╯ ✦✦  ",
            " ✦✦  /         \\   ✦✦  ",
            " ✦  / ✦✦ ·✦· ✦✦ \\   ✦  ",
            " ✦ ╱             ╲  ✦  ",
        ],
        [
            "✦✧✦╭──────────────╮✦✧✦ ",
            "✧✦✧│   ✦★    ★✦   │✧✦✧ ",
            "✦✧✦│      ══      │✦✧✦ ",
            "✧✦✧╰──────────────╯✧✦✧ ",
            "✦✧✦  /         \\   ✦✧✦ ",
            "✧✦✧ /  ✦✧✦·✦✧✦  \\  ✧✦✧ ",
            "✦✧✦╱             ╲ ✦✧✦",
        ],
    ]

    EVOLUTION_LABELS = [
        "Gathering memories...",
        "Analyzing patterns...",
        "Extracting traits...",
        "Synthesizing...",
        "Evolving prompt...",
        "Modifying DNA...",
    ]

    def __init__(self, console_ui: ConsoleUI):
        """Initialize UI effects with a console instance."""
        self.console = console_ui.console
        self.live = None
        self._loading_stop_event = None
        self._loading_thread = None
        self._visible_buffer = -1
        self._tracking_buffer = 0
        self.message_handler = console_ui.message_handler
        self.spinner = itertools.cycle(["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"])
        self.updated_text = ""
        self.updated_planning_text = ""
        # Delegate animation state
        # key: tool_use_id, value: (agent_name, start_time)
        self._delegate_agents: dict[str, tuple[str, float]] = {}
        self._delegate_lock = threading.Lock()
        self._delegate_stop_event: threading.Event | None = None
        self._delegate_thread: threading.Thread | None = None

    # Hue oscillates between (h_start - h_range/2) and (h_start + h_range/2)
    # using a cosine wave, so both ends of the string share the same hue →
    # zero jump at wrap-around and small per-character steps for smoothness.
    _GRADIENT_HUE_START = 0.45
    _GRADIENT_HUE_RANGE = 0.25
    _GRADIENT_SATURATION = 0.70
    _GRADIENT_VALUE = 1.0

    def _gradient_text(self, text: str, offset: float = 0.0) -> Text:
        """Create a Text with a left-to-right hue gradient that flows over time.

        Uses a cosine wave in HSV hue space for smooth cyclic transitions.
        Both ends of the string share the same hue, so there is no color
        jump at wrap-around, and the limited hue range keeps per-character
        steps small for a visually smooth gradient.

        Args:
            text: The string to render.
            offset: Normalized position offset (0.0–1.0) that shifts the gradient,
                creating a flowing effect when incremented per frame.
        """
        result = Text(text)
        n = len(text)
        if n == 0:
            return result

        h_start = self._GRADIENT_HUE_START
        h_range = self._GRADIENT_HUE_RANGE
        s = self._GRADIENT_SATURATION
        v = self._GRADIENT_VALUE

        for i in range(n):
            pos = ((i / max(n - 1, 1)) - offset) % 1.0
            # cosine wave: 0 → +1 → 0 → -1 → 0 across one full cycle
            wave = math.cos(2 * math.pi * pos)
            hue = (h_start + wave * h_range / 2) % 1.0
            r_f, g_f, b_f = colorsys.hsv_to_rgb(hue, s, v)
            r = int(r_f * 255)
            g = int(g_f * 255)
            b = int(b_f * 255)
            result.stylize(f"rgb({r},{g},{b})", i, i + 1)

        return result

    def _loading_animation(self, stop_event):
        """Display a loading animation in the terminal."""
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
        tips = COMMAND_HELP_MESSAGES
        tip_index = random.randint(0, len(tips) - 1)
        tip_timer = 0.0
        tip_interval = 5.0
        gradient_offset = 0.0

        with Live(
            "", console=self.console, auto_refresh=True, refresh_per_second=30
        ) as live:
            while not stop_event.is_set():
                spin = next(self.spinner)
                tip_line = self._gradient_text(
                    f"  💡 {tips[tip_index]}", gradient_offset
                )
                spinner_line = self._gradient_text(
                    f"  {fun_word} {spin}", gradient_offset
                )
                live.update(Group(spinner_line, tip_line))
                time.sleep(0.1)
                tip_timer += 0.1
                gradient_offset = (gradient_offset + 0.02) % 1.0
                if tip_timer >= tip_interval:
                    tip_timer = 0.0
                    tip_index = (tip_index + 1) % len(tips)
            live.update("")  # Clear the live display when done
            live.stop()  # Stop the live display
            import sys

            sys.stdout.write("\x1b[1A")  # cursor up one line
            sys.stdout.write("\x1b[2K")

    def _delegate_animation(self, stop_event):
        """Animate active delegate agents with per-agent spinners and elapsed time."""
        spinner = itertools.cycle(["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"])
        with Live(
            "", console=self.console, auto_refresh=True, refresh_per_second=10
        ) as live:
            while not stop_event.is_set():
                spin = next(spinner)
                with self._delegate_lock:
                    agents = dict(self._delegate_agents)
                if agents:
                    now = time.time()
                    parts = []
                    for agent_name, start in agents.values():
                        elapsed = int(now - start)
                        parts.append(
                            f"📋 [bold yellow]{agent_name}[/] [dim]{spin} {elapsed}s[/]"
                        )
                    live.update("   " + "   [dim]|[/]   ".join(parts))
                else:
                    live.update("")
                time.sleep(0.1)
            live.update("")
            live.stop()
            import sys

            sys.stdout.write("\x1b[1A")
            sys.stdout.write("\x1b[2K")

    def start_delegate_animation(self, tool_use_id: str, agent_name: str):
        """Register a delegation by tool_use_id and start/keep the animation running."""
        with self._delegate_lock:
            self._delegate_agents[tool_use_id] = (agent_name, time.time())
        if self._delegate_thread and self._delegate_thread.is_alive():
            return  # Already running
        self._delegate_stop_event = threading.Event()
        self._delegate_thread = threading.Thread(
            target=self._delegate_animation, args=(self._delegate_stop_event,)
        )
        self._delegate_thread.daemon = True
        self._delegate_thread.start()

    def stop_delegate_animation(self, tool_use_id: str):
        """Unregister a delegation by tool_use_id. Stop animation only when all are done."""
        with self._delegate_lock:
            self._delegate_agents.pop(tool_use_id, None)
            any_remaining = bool(self._delegate_agents)
        if not any_remaining:
            if self._delegate_stop_event:
                self._delegate_stop_event.set()
                self._delegate_stop_event = None
            if self._delegate_thread and self._delegate_thread.is_alive():
                self._delegate_thread.join(timeout=0.5)
                self._delegate_thread = None

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

    def _evolution_animation(self, stop_event, agent_name: str):
        frames = self.EVOLUTION_FRAMES
        labels = self.EVOLUTION_LABELS
        total_frames = len(frames)
        frame_cycle = itertools.cycle(range(total_frames))
        label_cycle = itertools.cycle(labels)
        sparkle_cycle = itertools.cycle(["✦", "✧", "✦", "·", "✧", "✦"])
        start_time = time.time()
        ticks = 0
        current_frame_idx = next(frame_cycle)
        current_label = next(label_cycle)

        with Live(
            "", console=self.console, auto_refresh=True, refresh_per_second=4
        ) as live:
            while not stop_event.is_set():
                elapsed = time.time() - start_time

                if ticks > 0 and ticks % 10 == 0:
                    current_frame_idx = next(frame_cycle)
                    current_label = next(label_cycle)

                sparkle = next(sparkle_cycle)
                art = "\n".join(frames[current_frame_idx])

                panel_content = Text.from_markup(
                    f"[bold yellow]{art}[/]\n\n"
                    f"   {sparkle} [bold cyan]{current_label}[/] {sparkle}\n"
                    f"   [dim]{int(elapsed)}s elapsed[/]"
                )
                live.update(
                    Panel(
                        panel_content,
                        title=Text(
                            f"🧬 {agent_name} is evolving...",
                            style="bold yellow",
                        ),
                        border_style="yellow",
                        box=HORIZONTALS,
                        width=42,
                    )
                )
                ticks += 1
                time.sleep(0.3)
            live.update("")
            live.stop()
            import sys

            sys.stdout.write("\x1b[1A")
            sys.stdout.write("\x1b[2K")

    def start_evolution_animation(self, agent_name: str = "Agent"):
        if (
            hasattr(self, "_evolution_thread")
            and self._evolution_thread
            and self._evolution_thread.is_alive()
        ):
            return
        self._evolution_stop_event = threading.Event()
        self._evolution_thread = threading.Thread(
            target=self._evolution_animation,
            args=(self._evolution_stop_event, agent_name),
        )
        self._evolution_thread.daemon = True
        self._evolution_thread.start()

    def stop_evolution_animation(self):
        if hasattr(self, "_evolution_stop_event") and self._evolution_stop_event:
            self._evolution_stop_event.set()
            self._evolution_stop_event = None
        if (
            hasattr(self, "_evolution_thread")
            and self._evolution_thread
            and self._evolution_thread.is_alive()
        ):
            self._evolution_thread.join(timeout=1.0)
            self._evolution_thread = None

    def start_streaming_response(self, agent_name: str, is_thinking=False):
        """Start streaming the assistant's response."""
        from .constants import RICH_STYLE_GREEN_BOLD

        header = Text(
            f"💭 {agent_name.upper()}'s thinking:"
            if is_thinking
            else f"🤖 {agent_name.upper()}:",
            style=RICH_STYLE_GRAY if is_thinking else RICH_STYLE_GREEN_BOLD,
        )

        live_panel = Panel(
            "",
            title=header,
            box=HORIZONTALS,
            border_style=RICH_STYLE_GRAY if is_thinking else RICH_STYLE_GREEN,
        )

        self.live = Live(
            live_panel,
            auto_refresh=True,
            refresh_per_second=20,
            console=self.console,
            vertical_overflow="crop",
        )
        self.live.start()

    def _build_planning_panel(self, planning_content: str):
        return Panel(
            Markdown(planning_content.replace("\n", "  \n"), code_theme=CODE_THEME),
            title=Text("🧭 AGENT PLAN", style=RICH_STYLE_GRAY),
            box=HORIZONTALS,
            title_align="left",
            border_style=RICH_STYLE_GRAY,
        )

    def _build_response_renderable(
        self,
        response: str,
        planning_content: str = "",
        is_thinking: bool = False,
        subtitle: Text | None = None,
        height_limit: int | None = None,
    ):
        from .constants import RICH_STYLE_GREEN_BOLD

        renderables = []
        if planning_content.strip() and not is_thinking:
            renderables.append(self._build_planning_panel(planning_content.strip()))

        if response.strip() or is_thinking:
            agent_name = self.message_handler.agent.name
            header = Text(
                f"💭 {agent_name.upper()}'s thinking:"
                if is_thinking
                else f"🤖 {agent_name.upper()}:",
                style=RICH_STYLE_GRAY if is_thinking else RICH_STYLE_GREEN_BOLD,
            )
            panel_kwargs = {
                "title": header,
                "box": HORIZONTALS,
                "title_align": "left",
                "border_style": RICH_STYLE_GRAY if is_thinking else RICH_STYLE_GREEN,
            }
            if subtitle is not None:
                panel_kwargs["subtitle"] = subtitle
            if height_limit is not None:
                panel_kwargs["height"] = max(1, height_limit)
                panel_kwargs["expand"] = False
            renderables.append(
                Panel(
                    Markdown(response.replace("\n", "  \n"), code_theme=CODE_THEME),
                    **panel_kwargs,
                )
            )

        if not renderables:
            return Text("")
        if len(renderables) == 1:
            return renderables[0]
        return Group(*renderables)

    def scroll_live_display(self, direction: str):
        speed = 10
        if self._visible_buffer == -1:
            self._visible_buffer = self._tracking_buffer
        if direction == "up":
            self._visible_buffer = max(0, self._visible_buffer - speed)
        elif direction == "down":
            self._visible_buffer += speed
        if self.live:
            self.update_live_display(self.updated_text, self.updated_planning_text)

    def update_live_display(
        self,
        chunk: str,
        planning_content: str = "",
        is_thinking: bool = False,
    ):
        """Update the live display with a new chunk of the response."""
        if not self.live:
            self.start_streaming_response(self.message_handler.agent.name, is_thinking)

        if chunk != self.updated_text:
            self.updated_text = self.updated_text + chunk if is_thinking else chunk
        if not is_thinking:
            self.updated_planning_text = planning_content

        lines = self.updated_text.split("\n")
        height_limit = int(self.console.size.height * 0.9)
        if len(lines) > height_limit:
            if (
                self._visible_buffer == -1
                or self._visible_buffer > len(lines) - height_limit
            ):
                self._tracking_buffer = len(lines) - height_limit
                self._visible_buffer = -1
                lines = lines[-height_limit:]
            else:
                lines = lines[
                    self._visible_buffer : self._visible_buffer + height_limit
                ]

        if self.live:
            subtitle = Text(
                "(Use Ctrl+U/Ctrl+D to scroll)",
                style=RICH_STYLE_GRAY,
            )
            live_renderable = self._build_response_renderable(
                "\n".join(lines),
                planning_content=self.updated_planning_text,
                is_thinking=is_thinking,
                subtitle=subtitle if not is_thinking else None,
                height_limit=min(height_limit, len(lines)),
            )
            self.live.update(live_renderable)

    def finish_live_update(self):
        """Stop the live update display."""
        self._visible_buffer = -1
        self._tracking_buffer = 0
        self.updated_text = ""
        self.updated_planning_text = ""
        if self.live:
            self.console.print(self.live.get_renderable())
            self.live.update("")
            self.live.stop()
            self.live = None

    def finish_response(
        self,
        response: str,
        planning_content: str = "",
        is_thinking: bool = False,
    ):
        """Finalize and display the complete response."""
        self._visible_buffer = -1
        self._tracking_buffer = 0
        self.updated_text = ""
        self.updated_planning_text = ""

        if self.live:
            self.live.update(Text("", end=""))
            self.live.stop()
            self.live = None

        renderable = self._build_response_renderable(
            response,
            planning_content=planning_content,
            is_thinking=is_thinking,
        )
        if isinstance(renderable, Text) and not renderable.plain.strip():
            return
        self.console.print(renderable)

    def cleanup(self):
        """Clean up all running effects."""
        self.stop_loading_animation()
        self.stop_evolution_animation()
        self.finish_live_update()
        with self._delegate_lock:
            self._delegate_agents.clear()
        if self._delegate_stop_event:
            self._delegate_stop_event.set()
            self._delegate_stop_event = None
        if self._delegate_thread and self._delegate_thread.is_alive():
            self._delegate_thread.join(timeout=0.5)
            self._delegate_thread = None
