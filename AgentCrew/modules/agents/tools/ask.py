"""
Ask tool for eliciting more details from users.
This tool allows agents to request additional information from users with guided answer options.
Supports multiple questions in a single call to reduce round trips.
"""

from typing import Any, Callable


def get_ask_tool_definition() -> dict[str, Any]:
    """
    Get the definition for the ask tool.

    Returns:
        The tool definition
    """
    tool_description = (
        "PRIMARY tool for gathering missing information — use when the request is "
        "ambiguous, details are unclear, you hit a blocker, or need direction.\n\n"
        "When the user presents a plan/design/proposal, interview relentlessly: "
        "walk down each branch of the design tree, resolve dependencies one-by-one, "
        "and provide your recommended answer as the first guided option.\n\n"
        "Bundle up to 10 related questions in the `questions` array. "
        "The user answers them one at a time and submits all at once."
    )

    tool_arguments = {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user. Be clear, specific, and concise.",
                    },
                    "guided_answers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "1-10 suggested answers. Put your recommended answer first. "
                        ),
                        "minItems": 1,
                        "maxItems": 10,
                    },
                },
                "required": ["question", "guided_answers"],
            },
            "minItems": 1,
            "maxItems": 10,
            "description": "One or more questions to ask (up to 10). Use multiple entries to bundle related questions and reduce round trips.",
        },
    }

    tool_required = ["questions"]

    return {
        "type": "function",
        "function": {
            "name": "ask",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def ask_tool_prompt() -> str:
    """
    Get the system prompt instructions for the ask tool.

    Returns:
        System prompt describing when and how to use the ask tool
    """
    return """
<Ask_Tool_Instruction>
  <Purpose>
    Your PRIMARY tool when anything is unclear, incomplete, or blocked.
    Use it to:
    - Resolve ambiguity or blockers before guessing
    - Interview the user on plans/designs/proposals: walk each branch of the design tree,
      resolve dependencies one-by-one, and provide your recommended answer as the first option
    - Clarify specs, constraints, or choices where context is insufficient

    Bundle up to 10 related questions in one call. The user sees them one at a time
    and submits all answers together — reducing round trips.
  </Purpose>

  <Best_Practices>
    - Default to `ask` when unclear — do not guess or assume
    - Bundle related questions (up to 10), resolve one dependency at a time
    - Put your recommended answer first; provide 2-6 clear, mutually exclusive options
    - Ask the smallest useful question — don't bundle unrelated decisions
  </Best_Practices>
</Ask_Tool_Instruction>
"""


def get_ask_tool_handler() -> Callable:
    """
    Get the handler function for the ask tool.

    Note: The actual user interaction is handled by the UI layer (console/GUI).
    This handler serves as a placeholder that signals the need for user input.

    Returns:
        The handler function
    """

    async def handler(**params) -> str:
        """
        Handle an ask request.

        This function doesn't directly interact with the user - that's handled
        by the UI layer through the confirmation flow. Instead, it prepares
        the questions for presentation.

        Args:
            questions: list of {question, guided_answers} objects

        Returns:
            A string describing the questions (actual response comes from user)
        """
        questions = params.get("questions", [])

        if not questions or not isinstance(questions, list):
            raise ValueError(
                "Error: `questions` array is required with at least one {question, guided_answers} entry."
            )

        if len(questions) > 10:
            raise ValueError(
                "Error: Cannot ask more than 10 questions in a single call. "
                "Limit to 10 questions."
            )

        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                raise ValueError(f"Error: Question at index {i} must be an object")
            question_text = q.get("question", "").strip()
            guided_answers = q.get("guided_answers", [])

            if not question_text:
                raise ValueError(f"Error: Question at index {i} has no question text")

            if not isinstance(guided_answers, list) or len(guided_answers) < 2:
                raise ValueError(
                    f"Error: Question at index {i} must have at least 2 guided answers"
                )

            if len(guided_answers) > 6:
                raise ValueError(
                    f"Error: Question at index {i} cannot have more than 6 guided answers"
                )

        # This return value is a placeholder - the actual response
        # will be injected by the UI layer after user interaction
        count = len(questions)
        return f"Asking user {count} question{'s' if count > 1 else ''}"

    return handler


def register(agent=None):
    """
    Register the ask tool with all agents or a specific agent.

    Args:
        agent: Specific agent to register with (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(get_ask_tool_definition, get_ask_tool_handler, None, agent)
