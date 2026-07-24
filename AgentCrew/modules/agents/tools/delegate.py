from collections.abc import Callable
from typing import Any

from AgentCrew.modules.agents import AgentManager, run_agent_loop


def get_delegate_tool_definition() -> dict[str, Any]:
    tool_description = (
        "Delegates a task to a specialized agent for independent execution. "
        "The target agent completes the task and returns the result. "
        "To delegate to MULTIPLE agents in parallel, call this tool multiple "
        "times with the parallel tool calls. all delegations will execute concurrently."
    )

    tool_arguments = {
        "target_agent": {
            "type": "string",
            "description": (
                "The name of the agent to delegate to. "
                "Refer to <Transferable_Agents> for options."
            ),
        },
        "task_description": {
            "type": "string",
            "description": (
                "A clear, actionable description of what the target agent "
                "should accomplish. Start with an action verb. Include "
                "specific deliverables and constraints."
            ),
        },
        "context": {
            "type": "string",
            "description": (
                "Optional. Relevant information the target agent needs. "
                "Include only what's necessary for the agent starts fresh."
            ),
        },
    }

    tool_required = ["target_agent", "task_description"]

    return {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_delegate_tool_handler(agent_manager: AgentManager) -> Callable:
    async def handler(**params) -> str:
        from AgentCrew.modules.agents.local_agent import LocalAgent
        from AgentCrew.modules.llm.service_manager import (
            ServiceManager as LLMServiceManager,
        )

        target_agent_name = params.get("target_agent")
        task_description = params.get("task_description")
        context = params.get("context", "")

        if not target_agent_name:
            raise ValueError("Error: No target agent specified")

        if not task_description:
            raise ValueError("Error: No task description specified for delegation")

        from_agent_name = (
            agent_manager.current_agent.name
            if agent_manager.current_agent
            else "Unknown"
        )

        if target_agent_name not in agent_manager.agents:
            available_agents = ", ".join(agent_manager.agents.keys())
            raise ValueError(
                f"Error: Agent '{target_agent_name}' not found. Available agents: {available_agents}"
            )

        if target_agent_name == from_agent_name:
            raise ValueError("Error: Cannot delegate to yourself")

        target_agent = agent_manager.get_local_agent(target_agent_name)
        if not target_agent:
            raise ValueError(
                f"Error: Could not retrieve local agent '{target_agent_name}'"
            )

        delegation_message = (
            f"## Delegated Task from {from_agent_name}\n\n{task_description}\n"
        )
        if context:
            delegation_message += f"\n## Context\n{context}\n"

        delegation_message += (
            "\n## Instructions\n"
            "Complete the task above. Provide a thorough, complete response."
        )

        llm_manager = LLMServiceManager.get_instance()
        from AgentCrew.modules.llm.model_registry import ModelRegistry

        registry = ModelRegistry.get_instance()
        model = registry.get_model(target_agent.get_model())
        if not target_agent.llm:
            raise ValueError("LLM of Agent not found")

        service_name = (
            model.resolved_service_name() if model else target_agent.llm.provider_name
        )
        fresh_llm = llm_manager.initialize_standalone_service(service_name)
        fresh_llm.model = target_agent.llm.model

        clone = LocalAgent(
            name=target_agent.name,
            description=target_agent.description,
            llm_service=fresh_llm,
            services=target_agent.services,
            tools=target_agent.tools,
            temperature=target_agent.temperature,
        )
        clone.set_system_prompt(target_agent.system_prompt or "")
        if target_agent.custom_system_prompt:
            clone.set_custom_system_prompt(target_agent.custom_system_prompt)
        clone.activate()

        try:
            delegate_history = [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": delegation_message}],
                }
            ]

            response, _ = await run_agent_loop(
                agent=clone,
                history=delegate_history,
                tool_filter=lambda t: t["name"] not in ["transfer", "delegate"],
            )

            return f"## Result from {target_agent_name}:\n\n{response}"

        finally:
            clone.deactivate()
            await fresh_llm.close()

    return handler


def delegate_tool_prompt(agent_manager: AgentManager) -> str:
    return agent_manager.get_delegate_system_prompt()


def register(agent_manager, agent=None):
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_delegate_tool_definition, get_delegate_tool_handler, agent_manager, agent
    )
