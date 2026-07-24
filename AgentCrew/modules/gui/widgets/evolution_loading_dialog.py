from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class EvolutionLoadingDialog(QDialog):
    FRAMES = [
        [
            "        ╭──────╮        ",
            "        │ ◠  ◠ │        ",
            "        │  ──  │        ",
            "        ╰──────╯        ",
            "         /    \\         ",
            "        /      \\        ",
            "       ╱        ╲       ",
        ],
        [
            "       ╭────────╮       ",
            "       │  ◠  ◠  │       ",
            "       │   ──   │       ",
            "       ╰────────╯       ",
            "        /      \\        ",
            "       /  ·  ·  \\       ",
            "      ╱          ╲      ",
        ],
        [
            "     ·  ╭────────╮  ·   ",
            "    ·   │  ◉  ◉  │   ·  ",
            "   ·    │   ──   │    · ",
            "    ·   ╰────────╯   ·  ",
            "     ·   /      \\   ·   ",
            "    ·   / ·· ·· \\   ·  ",
            "   ·   ╱          ╲  ·  ",
        ],
        [
            "  ✦ · ╭──────────╮ · ✦ ",
            "  · ✦ │  ★    ★  │ ✦ · ",
            "  ✦ · │    ──    │ · ✦ ",
            "  · ✦ ╰──────────╯ ✦ · ",
            "  ✦ ·  /        \\  · ✦ ",
            "  · ✦ / ✦·  ·✦  \\ ✦ · ",
            "  ✦ ·╱            ╲· ✦ ",
        ],
        [
            " ✦✦✦╭────────────╮✦✦✦  ",
            " ✦✦ │   ★    ★   │ ✦✦  ",
            " ✦  │     ══     │  ✦  ",
            " ✦✦ ╰────────────╯ ✦✦  ",
            " ✦✦  /          \\  ✦✦  ",
            " ✦  / ✦✦ ·✦· ✦✦ \\  ✦  ",
            " ✦ ╱              ╲ ✦  ",
        ],
        [
            "✦✧✦╭──────────────╮✦✧✦ ",
            "✧✦✧│   ✦★    ★✦   │✧✦✧ ",
            "✦✧✦│      ══      │✦✧✦ ",
            "✧✦✧╰──────────────╯✧✦✧ ",
            "✦✧✦  /            \\  ✦✧✦",
            "✧✦✧ /  ✦✧✦·✦✧✦   \\ ✧✦✧",
            "✦✧✦╱                ╲✦✧✦",
        ],
    ]

    LABELS = [
        "Gathering memories...",
        "Analyzing patterns...",
        "Extracting traits...",
        "Synthesizing...",
        "Evolving prompt...",
        "Deep thinking...",
    ]
    FRAME_WIDTH = max(len(line) for frame in FRAMES for line in frame)

    def __init__(self, parent=None, agent_name: str = "Agent"):
        super().__init__(parent)
        self.agent_name = agent_name
        self.frame_idx = 0
        self.label_idx = 0
        self.tick_count = 0
        self._start_time = None

        self.setWindowTitle(f"{agent_name} is evolving...")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.title_label = QLabel(f"🧬 {agent_name} is evolving...")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #f9e2af;"
        )
        layout.addWidget(self.title_label)

        self.art_label = QLabel()
        self.art_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self.art_label.setTextFormat(Qt.TextFormat.PlainText)
        self.art_label.setStyleSheet(
            "font-family: 'JetBrains Mono', 'Fira Code', monospace;"
            "font-size: 15px; color: #f5e0dc;"
            "background: rgba(49, 50, 68, 0.78);"
            "border: 2px solid rgba(249, 226, 175, 0.55);"
            "border-radius: 14px; padding: 18px;"
        )
        self.art_label.setMinimumHeight(190)
        layout.addWidget(self.art_label)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(
            "font-size: 15px; font-weight: 600; color: #89dceb;"
        )
        layout.addWidget(self.status_label)

        self.elapsed_label = QLabel()
        self.elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.elapsed_label.setStyleSheet("font-size: 13px; color: #bac2de;")
        layout.addWidget(self.elapsed_label)

        self.setStyleSheet(
            "QDialog {"
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "stop:0 rgba(30, 30, 46, 245), stop:1 rgba(17, 17, 27, 245));"
            "border: 2px solid rgba(249, 226, 175, 0.35);"
            "border-radius: 18px;"
            "}"
        )

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self._render()

    def showEvent(self, event):
        super().showEvent(event)
        import time

        self._start_time = time.time()
        self.timer.start(300)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def set_agent_name(self, agent_name: str):
        self.agent_name = agent_name
        self.setWindowTitle(f"{agent_name} is evolving...")
        self.title_label.setText(f"🧬 {agent_name} is evolving...")
        self.frame_idx = 0
        self.label_idx = 0
        self.tick_count = 0
        import time

        self._start_time = time.time()
        self._render()

    def _advance(self):
        self.tick_count += 1
        if self.tick_count % 10 == 0:
            self.frame_idx = (self.frame_idx + 1) % len(self.FRAMES)
            self.label_idx = (self.label_idx + 1) % len(self.LABELS)
        self._render()

    def _normalize_frame(self, frame: list[str]) -> str:
        normalized_lines = [line.center(self.FRAME_WIDTH) for line in frame]
        return "\n".join(normalized_lines)

    def _render(self):
        import time

        self.art_label.setText(self._normalize_frame(self.FRAMES[self.frame_idx]))
        self.status_label.setText(self.LABELS[self.label_idx])
        elapsed = int(time.time() - self._start_time) if self._start_time else 0
        self.elapsed_label.setText(f"{elapsed}s elapsed")
