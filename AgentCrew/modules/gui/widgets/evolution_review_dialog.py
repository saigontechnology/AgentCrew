from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

CATEGORY_LABELS = (
    ("Durable traits", "durable_traits"),
    ("Output preferences", "output_preferences"),
    ("Recurring corrections", "recurring_user_corrections"),
    ("Workflow patterns", "workflow_patterns"),
    ("Tool usage preferences", "tool_usage_preferences"),
)


class EvolutionReviewDialog(QDialog):
    def __init__(
        self,
        parent=None,
        agent_name: str = "",
        summary: str = "",
        analysis_summary: dict | None = None,
        source_memory_count: int = 0,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Prompt Evolution Review - {agent_name}")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self._accepted = False

        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>Agent:</b> {agent_name} &nbsp;&nbsp; "
            f"<b>Memories analyzed:</b> {source_memory_count}"
        )
        layout.addWidget(header)

        if analysis_summary:
            for label, key in CATEGORY_LABELS:
                items = analysis_summary.get(key, [])
                if items:
                    section = QLabel(f"<b>{label}:</b>")
                    layout.addWidget(section)
                    for item in items:
                        text = item.get("item", "")
                        strength = item.get("strength", "medium")
                        entry = QLabel(f"  • {text} [{strength}]")
                        entry.setWordWrap(True)
                        layout.addWidget(entry)

            confidence_notes = analysis_summary.get("confidence_notes", [])
            if confidence_notes:
                notes_label = QLabel("<b>Confidence notes:</b>")
                layout.addWidget(notes_label)
                for note in confidence_notes:
                    layout.addWidget(QLabel(f"  • {note}"))

        sep = QLabel("<hr>")
        layout.addWidget(sep)

        label = QLabel(
            "Review the extracted durable prompt evolution summary. "
            "Edit it if needed, then accept or decline."
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self.summary_edit = QTextEdit()
        self.summary_edit.setPlainText(summary)
        layout.addWidget(self.summary_edit)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.accept_btn = QPushButton("Accept")
        self.decline_btn = QPushButton("Decline")
        self.accept_btn.clicked.connect(self._on_accept)
        self.decline_btn.clicked.connect(self.reject)
        buttons.addWidget(self.accept_btn)
        buttons.addWidget(self.decline_btn)
        layout.addLayout(buttons)

    def _on_accept(self):
        self._accepted = True
        self.accept()

    def get_summary(self) -> str:
        return self.summary_edit.toPlainText().strip()
