from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QHBoxLayout,
    QCheckBox,
)


class EvolutionQuestionsDialog(QDialog):
    """Dialog asking the user 3 optional questions before evolution analysis."""

    def __init__(self, parent=None, questions: list[dict[str, str]] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Prompt Evolution Questions")
        self.setMinimumWidth(600)
        self.setMinimumHeight(450)

        layout = QVBoxLayout(self)

        header = QLabel(
            "Answer these optional questions to guide the evolution analysis. "
            "Leave blank to skip any question."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self._fields: dict[str, tuple[QTextEdit, QCheckBox]] = {}

        for q in questions or []:
            key = q.get("key", "")
            label = q.get("label", "Question")

            field_label = QLabel(f"<b>{label}</b>")
            layout.addWidget(field_label)

            text_edit = QTextEdit()
            text_edit.setPlaceholderText("Type your answer here (optional)...")
            text_edit.setMaximumHeight(80)
            layout.addWidget(text_edit)

            self._fields[key] = (text_edit, field_label)

        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.skip_all_btn = QPushButton("Skip All")
        self.continue_btn = QPushButton("Continue")
        self.skip_all_btn.clicked.connect(self._on_skip_all)
        self.continue_btn.clicked.connect(self._on_continue)
        buttons.addWidget(self.skip_all_btn)
        buttons.addWidget(self.continue_btn)
        layout.addLayout(buttons)

        self._answers: dict[str, str] = {}
        self._skipped = False

    def _on_continue(self):
        """Collect non-empty answers and close."""
        for key, (text_edit, _) in self._fields.items():
            text = text_edit.toPlainText().strip()
            if text:
                self._answers[key] = text
        self.accept()

    def _on_skip_all(self):
        """Skip all questions and close."""
        self._skipped = True
        self.accept()

    def get_answers(self) -> dict[str, str]:
        return self._answers

    def was_skipped(self) -> bool:
        return self._skipped
