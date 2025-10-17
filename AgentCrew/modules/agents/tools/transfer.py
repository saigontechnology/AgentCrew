from typing import Dict, Any, Callable

from AgentCrew.modules.agents import AgentManager


def get_transfer_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Get the definition for the transfer tool.

    Args:
        provider: The LLM provider (claude, openai, groq)

    Returns:
        The tool definition
    """
    tool_description = "Transfers the current task to a specialized agent when the user's request requires expertise or capabilities beyond your current abilities. This ensures the user receives the most accurate and efficient assistance. Always explain the reason for the transfer to the user before invoking this tool."

    tool_arguments = {
        "target_agent": {
            "type": "string",
            "description": "The unique identifier or name of the specialized agent to transfer the task to. Refer to the official <Available_Agents_List> tags for available specialist agents and their capabilities.",
        },
        "task_description": {
            "type": "string",
            "description": "A precise, actionable description of the task for the target agent. Start with action verbs (Create, Analyze, Design, Implement, etc.) and clearly state what the target agent needs to achieve. Include specific deliverables, success criteria, constraints, and any triggering keywords or phrases from the user that initiated the transfer. Think of this as the 'mission objective' for the other agent.",
        },
        "post_action": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["transfer", "other"],
                },
                "action": {
                    "type": "string",
                    "description": "Defines the expected next action for the target agent after it has completed its assigned task. Examples: 'ask user for next phase', 'report completion status', 'transfer to [specific agent] for implementation'. Omit if task completion is the final objective.",
                },
            },
        },
    }

    tool_required = ["target_agent", "task_description"]
    if provider == "claude":
        return {
            "name": "transfer",
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
                "name": "transfer",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def transfer_tool_prompt(agent_manager: AgentManager) -> str:
    return agent_manager.get_transfer_system_prompt()


def get_transfer_tool_handler(agent_manager: AgentManager) -> Callable:
    """
    Get the handler function for the transfer tool.

    Args:
        agent_manager: The agent manager instance

    Returns:
        The handler function
    """

    def handler(**params) -> str:
        """
        Handle a transfer request.

        Args:
            target_agent: The name of the agent to transfer to
            reason: The reason for the transfer
            context_summary: Optional summary of the conversation context

        Returns:
            A string describing the result of the transfer
        """
        target_agent = params.get("target_agent")
        task = params.get("task_description")
        post_action = params.get("post_action", {})

        if not target_agent:
            raise ValueError("Error: No target agent specified")

        if not task:
            raise ValueError("Error: No task specified for the transfer")

        if (
            agent_manager.current_agent
            and target_agent == agent_manager.current_agent.name
        ):
            raise ValueError("Error: Cannot transfer to same agent")

        result = agent_manager.perform_transfer(target_agent, task)
        if target_agent == "None":
            raise ValueError("Error: Task is completed. This transfer is invalid")

        response = ""

        if result["success"] and result["transfer"]["from"] != "None":
            response = f"<Transfer_Tool>\n  <Task from_agent='{result['transfer']['from']}'>\n{task}\n  </Task>\n"
            response += f"  <Current_Agent>{result['transfer']['to']}</Current_Agent>\n"

            if result["transfer"].get("included_conversations", []):
                response += f"  <Shared_Context>    \n{'    \n'.join(result['transfer'].get('included_conversations', []))}\n  </Shared_Context>\n"

            if post_action.get("action_type", "other") == "transfer":
                agent_manager.defered_transfer = post_action.get("action", "")
            else:
                response += f"  <Chaining_Task Priority='Mandatory'>{post_action.get('action', '')}</Chaining_Task>\n"

            response += "</Transfer_Tool>"

            return response

        else:
            available_agents = ", ".join(result.get("available_agents", []))
            return f"Error: {result.get('error')}. Available agents: {available_agents}"

    return handler


def register(agent_manager, agent=None):
    """
    Register the transfer tool with all agents or a specific agent.

    Args:
        agent_manager: The agent manager instance
        agent: Specific agent to register with (optional)
    """

    # Create the tool definition and handler

    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_transfer_tool_definition, get_transfer_tool_handler, agent_manager, agent
    )
