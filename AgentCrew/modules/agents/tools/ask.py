"""
Ask tool for eliciting more details from users.
This tool allows agents to request additional information from users with guided answer options.
"""

from typing import Dict, Any, Callable


def get_ask_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Get the definition for the ask tool.

    Args:
        provider: The LLM provider (claude, openai, groq, etc.)

    Returns:
        The tool definition
    """
    tool_description = (
        "Elicit more details from the user to better fulfill their request. "
        "This tool allows you to ask clarifying questions with suggested answer options "
        "to guide the user toward providing the information you need. "
        "The user can select from the guided answers or provide a custom response."
    )

    tool_arguments = {
        "question": {
            "type": "string",
            "description": "The question to ask the user. Be clear, specific, and concise.",
        },
        "guided_answers": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "A list of 3-6 suggested answers that guide the user. "
                "These should cover the most common or expected responses. "
            ),
            "minItems": 3,
            "maxItems": 6,
        },
    }

    tool_required = ["question", "guided_answers"]

    if provider == "claude":
        return {
            "name": "ask",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:
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
<Ask_Tool_Usage>
  <Purpose>
    Use the `ask` tool when you need additional information or clarification from the user 
    to complete their request effectively. This tool helps you gather specific details 
    through structured questioning with guided answer options.
  </Purpose>
  
  <When_To_Use>
    - User request is ambiguous or lacks critical details
    - Multiple valid approaches exist and user preference is needed
    - Confirmation is required before proceeding with a significant action
    - Technical specifications or constraints need clarification
    - Choice between options cannot be determined from context
  </When_To_Use>
  
  <Best_Practices>
    - Ask ONE specific question at a time
    - Provide 3-6 guided answers that cover common scenarios
    - Make guided answers clear, concise, and mutually exclusive
    - Frame questions positively and professionally
    - Ensure guided answers are actionable and relevant
    - Prevent custom answer or user specify option
    - Always use plain text
  </Best_Practices>
</Ask_Tool_Usage>
"""


def get_ask_tool_handler() -> Callable:
    """
    Get the handler function for the ask tool.

    Note: The actual user interaction is handled by the UI layer (console/GUI).
    This handler serves as a placeholder that signals the need for user input.

    Returns:
        The handler function
    """

    def handler(**params) -> str:
        """
        Handle an ask request.

        This function doesn't directly interact with the user - that's handled
        by the UI layer through the confirmation flow. Instead, it prepares
        the question and guided answers for presentation.

        Args:
            question: The question to ask the user
            guided_answers: List of suggested answers

        Returns:
            A string describing the question (actual response comes from user)
        """
        question = params.get("question", "")
        guided_answers = params.get("guided_answers", [])

        if not question:
            raise ValueError("Error: No question provided")

        if not guided_answers or len(guided_answers) < 3:
            raise ValueError(
                "Error: Must provide at least 3 guided answers for the user"
            )

        if len(guided_answers) > 6:
            raise ValueError("Error: Cannot provide more than 6 guided answers")

        # This return value is a placeholder - the actual response
        # will be injected by the UI layer after user interaction
        return f"Asking user: {question}"

    return handler


def register(agent=None):
    """
    Register the ask tool with all agents or a specific agent.

    Args:
        agent: Specific agent to register with (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(get_ask_tool_definition, get_ask_tool_handler, None, agent)
