from __future__ import annotations

from typing import Any

from a2a.types import (
    DataPart,
    Message,
    Part,
    Role,
    TextPart,
)


class TaskInteractionHandler:
    def __init__(self) -> None:
        pass

    def create_ask_message(self, questions: list[dict[str, Any]]) -> Message:
        """Create an ask message for one or more questions.

        Args:
            questions: list of {question, guided_answers} dicts

        Returns:
            A2A Message with ask data
        """
        ask_data = {
            "type": "ask",
            "questions": [
                {"question": q["question"], "guided_answers": q["guided_answers"]}
                for q in questions
            ],
            "instruction": (
                "Please answer the following questions. "
                "Respond with one of the guided answers or provide a custom response for each.\n"
                "Format: q0: <answer to question 1>\nq1: <answer to question 2>\n..."
            ),
        }
        total = len(questions)
        first_q = questions[0]["question"] if questions else ""
        return Message(
            message_id=f"ask_{hash(first_q)}_{total}",
            role=Role.agent,
            parts=[
                Part(root=TextPart(text=f"❓ Agent has {total} question{'s' if total > 1 else ''}")),
                Part(root=DataPart(data=ask_data)),
            ],
        )

    def cleanup(self, task_id: str) -> None:
        pass
