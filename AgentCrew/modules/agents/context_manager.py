from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from .local_agent import LocalAgent

SHRINK_LENGTH_THRESHOLD = 10


class AgentContextManager:
    """
    Manages context preparation and token budget for a LocalAgent.

    Owns:
    - Injecting system prompt and adaptive context into message lists
    - Pruning tool results when approaching the model token limit
    """

    def __init__(self, agent: LocalAgent) -> None:
        self._agent = agent

    def build_adaptive_context(self) -> list[str]:
        """Build the adaptive behavior dict (cwd, git branch, open files, etc.)."""

        agent = self._agent
        adaptive_messages: list[str] = []

        if (
            agent.services.get("agent_manager")
            and agent.services["agent_manager"].one_turn_process
        ):
            adaptive_messages.append(
                """User request is single-turn conversation.
You must analyze and plan out the steps then execute it with your available tools and execute then give the results without asking for confirmation or clarification.""",
            )

        mcp_resources_prompt = self._build_mcp_resources_prompt()
        if mcp_resources_prompt:
            adaptive_messages.append(mcp_resources_prompt)

        skills_service = agent.services.get("skills")
        if skills_service and skills_service.has_skills():
            catalog = skills_service.get_catalog()
            skill_lines = [f"- **{s['name']}**: {s['description']}" for s in catalog]
            adaptive_messages.append(
                "## Available Skills\n"
                f"{'\n'.join(skill_lines)}\n\n"
                "When a task matches a skill's description, call the "
                "`activate_skill` tool with the skill's name to load its "
                "full instructions before proceeding."
            )

        from AgentCrew.modules.memory.context_persistent import (
            ContextPersistenceService,
        )

        if "context_persistent" not in agent.services or not isinstance(
            agent.services["context_persistent"], ContextPersistenceService
        ):
            return adaptive_messages

        global_behaviors = dict(
            agent.services["context_persistent"].get_adaptive_behaviors(agent.name)
        )
        global_behaviors.update(
            {
                "good-to-say-no": "When encountering tasks that you have no data in the context and you don't know the anwser, be honest and say I don't know and ask user for helping you find the solution.",
            }
        )
        project_behaviors = agent.services["context_persistent"].get_adaptive_behaviors(
            agent.name, is_local=True
        )

        adaptive_sections = []
        if global_behaviors:
            adaptive_sections.append(
                "### Global Behaviors\n"
                + "\n".join(
                    f"- `{key}`: {value}" for key, value in global_behaviors.items()
                )
            )
        if project_behaviors:
            adaptive_sections.append(
                "### Project Behaviors\n"
                + "\n".join(f"- {value}" for _, value in project_behaviors.items())
            )

        if adaptive_sections:
            adaptive_messages.append(
                "## Current Behaviors\n"
                "### How to Apply\n"
                "Each behavior follows the format: `when [condition], [action steps]`.\n"
                "- **Condition matching**: At the start of every task, scan all behaviors. If the current task matches a condition, apply its action steps.\n"
                "- **Multiple matches**: Apply all matching behaviors. Project behaviors override global ones when they conflict.\n"
                "- **What counts as a match**: The condition describes a triggering situation — project context, user preferences, task type, etc. Use your best judgment.\n\n"
                f"{'\n\n'.join(adaptive_sections)}"
            )

        return adaptive_messages

    def _build_mcp_resources_prompt(self) -> str:
        agent = self._agent
        mcp_resources = getattr(agent, "mcp_resources", None)
        if not mcp_resources:
            return ""

        server_blocks = []
        for server_name, resources in mcp_resources.items():
            resource_blocks = []
            for resource in resources:
                fields = []
                for key in ("uri", "description"):
                    value = resource.get(key)
                    if value is not None and value != "":
                        fields.append(f"  - **{key}**: {value}")
                if fields:
                    resource_blocks.append("- Resource\n" + "\n".join(fields))
            if resource_blocks:
                tool_name = f"{server_name}__get_resource"
                server_blocks.append(
                    f"### Server: `{server_name}`\n"
                    f"Get resource tool: `{tool_name}`\n\n" + "\n".join(resource_blocks)
                )

        if not server_blocks:
            return ""

        return (
            "## MCP Resources\n"
            "Available MCP resources are listed by server. To fetch a resource, call the matching server-scoped get_resource tool with the exact uri.\n\n"
            f"{'\n\n'.join(server_blocks)}"
        )

    def _get_directory_structure(self) -> str:
        try:
            cwd = os.getcwd()
            entries = []
            for entry in sorted(os.listdir(cwd)):
                full_path = os.path.join(cwd, entry)
                if os.path.isdir(full_path):
                    entries.append(f"{entry}/")
                else:
                    entries.append(entry)
            return "\n".join(entries) if entries else ""
        except Exception as e:
            logger.warning(f"Failed to get directory structure: {e}")
            return ""

    def enhance_messages(self, final_messages: list[dict[str, Any]]) -> None:
        """Inject system prompt and adaptive context into the message list in place."""
        agent = self._agent

        last_user_index = next(
            (
                i
                for i, msg in enumerate(reversed(final_messages))
                if msg.get("role") == "user"
            ),
            None,
        )
        if last_user_index is None:
            return
        last_user_index = len(final_messages) - 1 - last_user_index

        adaptive_messages = self.build_adaptive_context()
        is_user_request = len(
            final_messages[last_user_index].get("content", [])
        ) > 0 and (
            final_messages[last_user_index]["content"][0]
            .get("text", "")
            .find("<Transfer_Request>")
            != 0
            and final_messages[last_user_index]["content"][0]
            .get("text", "")
            .find("<Post_Transfer_Action_Reminder>")
            != 0
        )

        if is_user_request:
            if agent.services.get("memory"):
                memory_headers = agent.services["memory"].list_memory_headers(
                    agent_name=agent.name
                )
                if memory_headers:
                    adaptive_messages.append(
                        f"## Recent conversation memory headlines:\n- {'\n - '.join(memory_headers)}\n---\nIf the user request related to any recent memories, call search_memory before responding."
                    )

            dir_structure = self._get_directory_structure()
            if dir_structure:
                dir_name = os.path.basename(os.getcwd())
                adaptive_messages.append(
                    f"## Current directory is {dir_name} with following structure:\n{dir_structure}"
                )

            code_analysis = agent.services.get("code_analysis")
            if code_analysis and "code_analysis" in agent.tools:
                try:
                    cache_entries = code_analysis.get_cache_entries_for_context(
                        os.getcwd()
                    )
                except Exception as exc:
                    logger.warning(f"Failed to list analyze_repo cache entries: {exc}")
                    cache_entries = []
                if cache_entries:
                    lines = [
                        "## Cached analyze_repo Results",
                        "",
                        (
                            "The following `analyze_repo` calls have cached results "
                            "for this project. Calling `analyze_repo` with the same "
                            "arguments returns the cached result quickly."
                        ),
                        "",
                    ]
                    for entry in cache_entries:
                        args_parts: list[str] = []
                        # Use json.dumps for safe, unambiguous quoting
                        args_parts.append(f"path={json.dumps(entry['path'])}")
                        exc_patterns = entry.get("exclude_patterns")
                        if exc_patterns:
                            args_parts.append(
                                f"exclude_patterns={json.dumps(exc_patterns)}"
                            )
                        feature_scope = entry.get("feature_scope")
                        if feature_scope is not None:
                            args_parts.append(
                                f"feature_scope={json.dumps(feature_scope)}"
                            )
                        args_parts.append(
                            f"deep_analysis={json.dumps(entry['deep_analysis'])}"
                        )
                        lines.append(f"- `analyze_repo({', '.join(args_parts)})`")
                    adaptive_messages.append("\n".join(lines))

        if (
            agent.services.get("agent_manager")
            and not agent.services["agent_manager"].one_turn_process
        ):
            from AgentCrew.modules.agents.manager import AgentMode

            if is_user_request and agent._colaboration_mode == AgentMode.TRANSFER:
                eval_text = """Generate then execute the plan to fulfill user or agent task below, the plan must stay inside <agent_evaluation> tags:
    - Plan out the tool call strategy for this task: which tools you should call, in what order, and what inputs each needs.
    - The purpose of the planning is finding the optimal way to gather necessary information and organize steps to be taken to accomplished the task
    - Sum up what you get from tool results in your next messages.
    - Is another agent better suited? If yes, transfer immediately.
    Then execute your plan.
    Skip evaluation for: simple one-sentence answers, or when the request matches "when [condition], [action]" — call `learn_behavior` directly instead."""
            elif is_user_request and agent._colaboration_mode == AgentMode.DELEGATE:
                eval_text = """Generate then execute the plan to fulfill user task below, the plan must stay inside <agent_evaluation> tags:
    - Plan out the tool call strategy for this task: which tools you should call, in what order, and what inputs each needs.
    - The purpose of the planning is finding the optimal way to gather necessary information and organize steps to be taken to accomplished the task
    - Sum up what you get from tool results in your next messages.
    - Can this request break into multiple sub-tasks and be delegated to specialist agents? If yes, delegate them.
    - Can multiple sub-tasks run in parallel? If yes, emit multiple delegate calls in one turn.
    Then execute your plan.
    Skip evaluation for: simple one-sentence answers, or when the request matches "when [condition], [action]" — call `learn_behavior` directly instead."""
            else:
                eval_text = """"Generate then execute the plan to fulfill user task below, the plan must stay inside <agent_evaluation> tags:
    - Plan out the tool call strategy for this task: which tools you should call, in what order, and what inputs each needs.
    - The purpose of the planning is finding the optimal way to gather necessary information and organize steps to be taken to accomplished the task
    - Sum up what you get from tool results in your next messages."""

            if eval_text:
                adaptive_messages.append(eval_text)

        if len(adaptive_messages) > 0:
            adaptive_messages.append(
                f"---Start {'user' if is_user_request else 'agent'} task from here---"
            )
            final_messages.insert(
                last_user_index,
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "\n\n".join(adaptive_messages)}
                    ],
                },
            )

    def shrink_tool_results(self, final_messages: list[dict[str, Any]]) -> None:
        """Prune large tool results when token usage exceeds 85% of model context."""
        from AgentCrew.modules.llm.model_registry import ModelRegistry

        agent = self._agent
        shrink_context_threshold = (
            int(
                os.getenv(
                    "AGENTCREW_DEFAULT_MAX_CONTEXT",
                    ModelRegistry.get_model_limit(agent.get_model()),
                )
            )
            * 0.85
        )

        unique_tool_indices = []
        agent_manager = agent.services.get("agent_manager", None)

        is_shrinkable = (
            agent_manager.context_shrink_enabled if agent_manager else False
        ) and agent.token_usage.total_input_tokens > shrink_context_threshold
        shrink_threshold = len(final_messages) - int(
            os.getenv("AGENTCREW_CONTEXT_SHRINK_THRESHOLD", SHRINK_LENGTH_THRESHOLD)
        )
        shrink_excluded = set(
            agent_manager.shrink_excluded_list if agent_manager else []
        )
        shrink_excluded.add("activate_skill")
        shrink_excluded.add("search_memory")
        last_agent_tool_calls = -1
        tool_result_needed_rearrange: dict[int, list[int]] = {}
        tool_with_id_needed_rearrange: list[str] = []
        shrink_tool_call_args: dict[str, dict[str, Any]] = {}

        for i, msg in enumerate(final_messages):
            content = None

            if msg.get("role") == "assistant":
                if len(msg.get("tool_calls", [])) == 0:
                    continue

                if is_shrinkable and i < shrink_threshold:
                    last_agent_tool_calls = i

                    for tool_call in msg.get("tool_calls", []):
                        if tool_call.get("id", None):
                            if tool_call.get("name") in shrink_excluded:
                                tool_with_id_needed_rearrange.append(
                                    tool_call.get("id")
                                )
                                continue
                            shrink_tool_call_args[tool_call.get("id")] = tool_call.get(
                                "arguments", {}
                            )
                    msg["tool_calls"] = [
                        t
                        for t in msg.get("tool_calls", [])
                        if t.get("name", "") in shrink_excluded
                    ]
                    if len(msg["tool_calls"]) == 0:
                        msg.pop("tool_calls", None)

            elif msg.get("role") == "tool":
                tool_name = msg.get("tool_name", "")

                if msg.get("tool_call_id", None) in tool_with_id_needed_rearrange:
                    if tool_result_needed_rearrange.get(last_agent_tool_calls, None):
                        tool_result_needed_rearrange[last_agent_tool_calls].append(i)
                    else:
                        tool_result_needed_rearrange[last_agent_tool_calls] = [i]
                    continue

                if msg.get("tool_call_id", None) in shrink_tool_call_args:
                    _raw_args = shrink_tool_call_args.get(
                        msg.get("tool_call_id", ""), {}
                    )
                    _arg_parts = []
                    for _k, _v in _raw_args.items():
                        _v_str = str(_v)
                        if len(_v_str) > 50:
                            _v_str = _v_str[:50] + "..."
                        _arg_parts.append(f"{_k}={_v_str}")
                    _arg_preview = ", ".join(_arg_parts)

                    # keep the reason of tool rejected remains
                    if not msg.get("is_rejected", False):
                        msg["content"] = [
                            {
                                "text": f"[tool:{tool_name}({_arg_preview}) was truncated]",
                                "type": "text",
                            }
                        ]
                    else:
                        msg["content"] = [
                            {
                                "type": "text",
                                "text": f"[tool: {tool_name}({_arg_preview}) was rejected by user with reason: {msg.get('content')}]",
                            }
                        ]
                    msg.pop("tool_name", None)
                    msg.pop("is_rejected", None)
                    msg["role"] = "user"
                    continue

                content = msg.get("content", "")
                if (
                    content
                    and isinstance(content, str)
                    and content.startswith("[UNIQUE]")
                ):
                    unique_tool_indices.append(i)
                elif (
                    content
                    and isinstance(content, list)
                    and (
                        len(
                            [
                                d.get("text", "")
                                for d in content
                                if isinstance(d, dict)
                                and d.get("text", "").startswith("[UNIQUE]")
                            ]
                        )
                        > 0
                    )
                ):
                    unique_tool_indices.append(i)
                    continue

        if len(unique_tool_indices) > 1:
            for i in unique_tool_indices[:-1]:
                msg = final_messages[i]

                if msg.get("role") == "tool" and "content" in msg:
                    msg["content"] = "[stale]"

        for assistant_idx, tool_result_indices in sorted(
            tool_result_needed_rearrange.items(), reverse=True
        ):
            if assistant_idx < 0 or not tool_result_indices:
                continue

            tool_results = [final_messages[idx] for idx in tool_result_indices]

            for idx in sorted(tool_result_indices, reverse=True):
                final_messages.pop(idx)

            for offset, tool_result in enumerate(tool_results):
                final_messages.insert(assistant_idx + 1 + offset, tool_result)
