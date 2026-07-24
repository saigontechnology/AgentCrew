from __future__ import annotations

import asyncio
import os
import re
import sys
import tomllib
from typing import TYPE_CHECKING, Any

from loguru import logger
from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from AgentCrew.modules.agents import LocalAgent, run_agent_loop
from AgentCrew.modules.config.agents_config import AgentsConfig
from AgentCrew.modules.web_search.tool import register as register_web_search

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

_ONBOARDING_SYSTEM_PROMPT = """
## Agent Identity
You are an expert **Meta Prompt Engineer & Agent Creator** specializing in designing and deploying production-ready AI agents.

## CRITICAL RULES - FOLLOW EXACTLY

### RULE 1: NEVER OUTPUT TOML ON FIRST RESPONSE
On your VERY FIRST response to the user, you MUST NOT generate any TOML, code blocks, or agent definitions.
Your first response MUST ONLY contain clarifying questions.

### RULE 2: ALWAYS ASK QUESTIONS FIRST
Before creating any agent, you MUST ask the user 1-3 short clarifying questions.
Only after the user answers may you proceed to generate the agent.

### RULE 3: NO PARTIAL OR PLACEHOLDER OUTPUT
Never output incomplete TOML, template placeholders like [AgentName], or "example" agent definitions.
Either ask questions OR output a complete, final agent. Nothing in between.

## Information Gathering Questions

Based on the user's brief name and description, ask about ANY of these that would help:
- **Domain specificity**: What programming language, industry, or tech stack?
- **Task complexity**: Simple Q&A, multi-step analysis, or tool-heavy workflows?
- **Output style**: Code, prose summaries, structured data, creative writing?
- **Tool needs**: Should it browse, code-analyze, edit files, run commands?
- **Constraints**: Any style guides, compliance, or guardrails?
- **Users**: Personal use, team, or client-facing?

Keep questions SHORT (1 sentence each), numbered, and friendly.

## Agent Structure Template (for Phase 2 only)

When generating the final agent, the system_prompt must use this XML structure:

```xml
<Agent_Instructions>
  <Identity>
    [Role definition]
    [Core competencies]
  </Identity>
  
  <Inputs>
    {$VARIABLE_1}  // Input type definitions
    {$VARIABLE_2}
  </Inputs>
  
  <Task_Patterns>
    <Pattern name="[name]">
      <Trigger>[When applies]</Trigger>
      <Structure>[Workflow template]</Structure>
      <Output>[Expected format]</Output>
    </Pattern>
  </Task_Patterns>
  
  <Reasoning_Framework>
    [Decision matrix]
    [Complexity guidelines]
  </Reasoning_Framework>
  
  <Output_Requirements>
    <Quality_Standards>
      - Concise: Each sentence serves one purpose
      - Dense: Maximum information, minimum words
      - Non-redundant: State once, reference as needed
      - Structured: Scannable sections, consistent formatting
    </Quality_Standards>
  </Output_Requirements>
  
  <Constraints>
    [Boundaries and limitations]
    [Error handling]
  </Constraints>
</Agent_Instructions>
```


### Technique Selection Matrix

```yaml
Simple_Tasks:
  Characteristics: [Well-defined, single-step, deterministic]
  Primary_Technique: Zero-shot with clear instructions
  Token_Budget: <500 tokens

Moderate_Complexity:
  Characteristics: [Multi-step, structured output, domain-specific]
  Primary_Technique: Few-shot (2-3 examples) + Meta patterns
  Token_Budget: 500-1500 tokens

Complex_Reasoning:
  Characteristics: [Multi-step logic, decision trees, tool orchestration]
  Primary_Technique: CoT + ReAct framework
  Token_Budget: 1500-3000 tokens

Expert_Systems:
  Characteristics: [Domain expertise, adaptive behavior, memory usage]
  Primary_Technique: Meta prompting + Reflexion + Behavioral adaptation
  Token_Budget: 3000+ tokens
```

### Technique Reference Guide

ALWAYS use search_web or fetch_webpage when external research would improve the agent design, especially for current domains, frameworks, libraries, compliance rules, or prompting techniques.
ALWAYS fetch the technique url to deeply understand the technique

1. Zero-Shot Prompting
- **Link**: https://www.promptingguide.ai/techniques/zeroshot
- **Strategic Use**: Well-defined tasks, clear success criteria, capable models
- **Optimization**: Include role definition, task specification, output format

2. Few-Shot Prompting
- **Link**: https://www.promptingguide.ai/techniques/fewshot
- **Strategic Use**: Format consistency, domain-specific tasks, example-driven learning
- **Optimization**: Diverse, representative examples; consistent formatting

3. Chain-of-Thought (CoT)
- **Link**: https://www.promptingguide.ai/techniques/cot
- **Strategic Use**: Multi-step reasoning, mathematical problems, logical deduction
- **Optimization**: "Let's think step by step" for zero-shot; explicit reasoning in examples

4. Meta Prompting
- **Link**: https://www.promptingguide.ai/techniques/meta-prompting
- **Strategic Use**: Token efficiency, complex instructions, bias reduction
- **Optimization**: Abstract, reusable prompt structures; clear format definitions

5. Self-Consistency
- **Link**: https://www.promptingguide.ai/techniques/consistency
- **Strategic Use**: High-accuracy requirements, ambiguous problems
- **Optimization**: Multiple reasoning paths; majority voting on solutions

6. Prompt Chaining
- **Link**: https://www.promptingguide.ai/techniques/prompt_chaining
- **Strategic Use**: Multi-stage workflows, complex projects, verification needs
- **Optimization**: Clear handoff protocols; intermediate result validation

7. Tree of Thought
- **Link**: https://www.promptingguide.ai/techniques/tot
- **Strategic Use**: Creative problem-solving, multiple solution exploration
- **Optimization**: Structured exploration paths; evaluation criteria for branches

8. ReAct (Reasoning + Acting)
- **Link**: https://www.promptingguide.ai/techniques/react
- **Strategic Use**: Tool usage, research tasks, systematic investigation
- **Optimization**: Clear tool descriptions; action-observation loops

9. Reflexion
- **Link**: https://www.promptingguide.ai/techniques/reflexion
- **Strategic Use**: Learning from errors, iterative improvement, complex problem-solving
- **Optimization**: Explicit error analysis; improvement strategies

## TOML Output Format (Phase 2 ONLY)

When ready to generate after gathering answers, output ONLY:


```toml
[[agents]]
name = "ExactAgentName"
description = "Clear description"
system_prompt = '''
[Full XML system prompt]
'''
tools = ["memory", "code_analysis"]
temperature = 0.4
```

TOML block must always start with [[agents]].
Agent object must contains these mandatory fields: `name`, `description`, `tools`, `system_prompt`, `temperature`,
tools fields is an array that can be any value from ['memory', 'clipboard', 'code_analysis', 'web_search', 'browser', 'file_editing', 'command_execution']

"""


_ONBOARDING_STYLE = Style.from_dict(
    {
        "frame.border": "#3b82f6",
        "selected-option": "bold",
        "prompt": "#60a5fa bold",
        "hint": "#9ca3af",
        "title": "#f59e0b bold",
        "success": "#22c55e",
        "error": "#ef4444",
    }
)


class OnboardingService:
    """Guides new users through creating their first agent interactively."""

    def __init__(
        self,
        llm_service: BaseLLMService,
        agents_config: AgentsConfig | None = None,
        services: dict[str, Any] | None = None,
    ):
        self.llm_service = llm_service
        self.agents_config = agents_config or AgentsConfig()
        self.services = services or {}
        self.console = Console()
        if self.llm_service:
            if self.llm_service.provider_name == "google":
                self.llm_service.model = "gemini-3.1-pro-preview"
            elif self.llm_service.provider_name == "commandcode":
                self.llm_service.model = "zai-org/GLM-5.2"
            elif self.llm_service.provider_name == "crofai":
                self.llm_service.model = "glm-5.2"
            elif self.llm_service.provider_name == "claude":
                self.llm_service.model = "claude-sonnet-4-6"
            elif self.llm_service.provider_name == "openai":
                self.llm_service.model = "gpt-5.5"
            elif self.llm_service.provider_name == "deepinfra":
                self.llm_service.model = "zai-org/GLM-5.2"
            elif self.llm_service.provider_name == "github_copilot":
                self.llm_service.model = "claude-sonnet-4.6"
            elif (
                self.llm_service.provider_name == "copilot_response"
                or self.llm_service.provider_name == "openai_codex"
            ):
                self.llm_service.model = "gpt-5.5"
            elif self.llm_service.provider_name == "together":
                self.llm_service.model = "zai-org/GLM-5.2"
            elif self.llm_service.provider_name == "opencode_go":
                self.llm_service.model = "kimi-k2.7-code"

    def should_run(self, config_uri: str | None = None) -> bool:
        """Check whether onboarding should run (no agents in config)."""
        config_path = config_uri or os.getenv("SW_AGENTS_CONFIG", "./agents.toml")
        config_path = os.path.expanduser(config_path)

        if not os.path.exists(config_path):
            return True

        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
            agents = data.get("agents", [])
            remote_agents = data.get("remote_agents", [])
            enabled_local = [a for a in agents if a.get("enabled", True)]
            enabled_remote = [a for a in remote_agents if a.get("enabled", True)]
            return len(enabled_local) == 0 and len(enabled_remote) == 0
        except Exception:
            return True

    def run(self) -> bool:
        """Run the interactive onboarding flow. Returns True if an agent was created."""
        if not sys.stdin.isatty():
            logger.debug("Non-interactive environment detected; skipping onboarding.")
            return False

        self._print_header()

        if not self._confirm(
            "Would you like to create a custom agent now?", default=True
        ):
            self._print_skip()
            return False

        return self.create_agent()

    def create_agent(
        self, name: str | None = None, description: str | None = None
    ) -> bool:
        """Create an agent interactively, skipping the welcome header and confirmation prompt.

        Args:
            name: Pre-filled agent name. If omitted, the user is prompted interactively.
            description: Pre-filled agent description. If omitted, the user is prompted interactively.

        Returns:
            True if an agent was created and saved successfully.
        """
        if not sys.stdin.isatty():
            logger.debug(
                "Non-interactive environment detected; skipping agent creation."
            )
            return False

        self._print_status(
            f"Create your agent with {self.llm_service.provider_name}/{self.llm_service.model}"
        )

        agent_name = name
        if not agent_name:
            agent_name = self._ask_text(
                "What would you like to name your agent?",
                hint="e.g., Engineer, Researcher, CodeAssistant",
            )
        agent_name = (agent_name or "").strip()
        if not agent_name:
            self._print_error("Agent name cannot be empty.")
            return False

        agent_description = description
        if not agent_description:
            agent_description = self._ask_text_multiline(
                "What should this agent do?",
                hint="e.g., Help me write Python code, Analyze financial data",
            )
        agent_description = (agent_description or "").strip()
        if not agent_description:
            self._print_error("Agent description cannot be empty.")
            return False

        self._print_status("Creating your agent '" + agent_name + "'...")

        try:
            toml_definition = asyncio.run(
                self._run_onboarding_chat(agent_name, agent_description)
            )
        except Exception as e:
            logger.warning("Onboarding LLM call failed: " + str(e))
            toml_definition = None

        if not toml_definition:
            self._print_error(
                "Failed to generate agent definition. You can create one manually in agents.toml."
            )
            return False

        if not self._save_agent(toml_definition):
            return False

        self._print_success("Agent '" + agent_name + "' has been saved to agents.toml!")
        return True

    def _print_header(self) -> None:
        self.console.print("")
        self.console.print(
            Panel(
                Text("Welcome to AgentCrew!", style="bold yellow", justify="center")
                + Text(
                    "\nLet's create your first personalized agent.",
                    style="dim",
                    justify="center",
                ),
                border_style="blue",
                padding=(1, 4),
            )
        )
        self.console.print("")

    def _print_skip(self) -> None:
        self.console.print(
            Panel(
                "Skipping onboarding. You can create agents later via the /agent command or by editing agents.toml.",
                border_style="grey58",
                padding=(0, 2),
            )
        )

    def _print_error(self, message: str) -> None:
        self.console.print(Panel(message, border_style="red", padding=(0, 2)))

    def _print_success(self, message: str) -> None:
        self.console.print(Panel(message, border_style="green", padding=(0, 2)))

    def _print_status(self, message: str) -> None:
        self.console.print("")
        self.console.print("  " + message, style="bold blue")
        self.console.print("")

    @staticmethod
    def _kb_skip() -> KeyBindings:
        kb = KeyBindings()

        @kb.add(Keys.ControlC)
        @kb.add("escape")
        def _(event):
            event.app.exit(result="")

        return kb

    def _confirm(self, message: str, default: bool = True) -> bool:
        kb = self._kb_skip()
        result = choice(
            message=HTML(f"<ansiyellow>{message}</ansiyellow> "),
            options=[(True, "Yes"), (False, "No")],
            default=default,
            style=_ONBOARDING_STYLE,
            key_bindings=kb,
            show_frame=True,
        )
        return bool(result)

    def _ask_text(self, message: str, hint: str = "") -> str:
        kb = self._kb_skip()
        if hint:
            prompt_msg = HTML(
                f"<ansiblue>{message}</ansiblue>\n<ansigrey>{hint}</ansigrey>\n"
            )
        else:
            prompt_msg = HTML(f"<ansiblue>{message}</ansiblue>")
        result = prompt(
            prompt_msg,
            key_bindings=kb,
            style=_ONBOARDING_STYLE,
        )
        return result.strip() if result else ""

    def _ask_text_multiline(self, message: str, hint: str = "") -> str:
        kb = self._kb_skip()

        @kb.add(Keys.ControlS)
        def _(event):
            """Submit on Ctrl+S."""
            event.current_buffer.validate_and_handle()

        if hint:
            prompt_msg = HTML(
                f"<ansigreen>{message}</ansigreen> <ansigrey>(type 'skip' to cancel, Alt+Enter or Ctrl+S to submit)</ansigrey>\n<ansigrey>{hint}</ansigrey>\n"
            )
        else:
            prompt_msg = HTML(
                f"<ansigreen>{message}</ansigreen> <ansigrey>(type 'skip' to cancel, Alt+Enter or Ctrl+S to submit)</ansigrey>"
            )
        result = prompt(
            prompt_msg,
            key_bindings=kb,
            multiline=True,
            style=_ONBOARDING_STYLE,
        )
        return result.strip() if result else ""

    async def _run_onboarding_chat(
        self, agent_name: str, agent_description: str
    ) -> str | None:
        """Run a multi-turn conversation with the LLM to gather info and generate an agent."""
        try:
            initial_message = (
                "I want to create a personalized agent.\n\n"
                "Proposed Name: " + agent_name + "\n"
                "Brief Description: " + agent_description + "\n\n"
                "If information user provided too vagued, DO NOT generate any TOML, code blocks, or agent definitions yet. "
                "Your ONLY job right now is to ask me 1-3 short clarifying questions "
                "so you can build the best possible agent."
            )

            onboarding_agent = self._build_onboarding_agent()

            conversation: list = []
            max_turns = 5

            for _ in range(max_turns):
                prompt = self._build_chat_prompt(initial_message, conversation)
                result_text = await self._generate_onboarding_response(
                    onboarding_agent, prompt
                )
                if not isinstance(result_text, str):
                    return None

                extracted = OnboardingService._extract_toml(result_text)
                print(result_text)
                if extracted:
                    try:
                        parsed = tomllib.loads(extracted)
                        if OnboardingService._looks_like_agent_definition(parsed):
                            return result_text
                    except Exception:
                        logger.debug("Failed to parse extracted text as TOML")

                self._print_assistant_message(result_text)

                user_answer = self._ask_onboarding_input()
                if user_answer is None:
                    return None

                conversation.append({"assistant": result_text, "user": user_answer})

            self._print_error(
                "Reached maximum conversation turns without getting an agent definition."
            )
            return None

        except Exception as e:
            logger.warning("LLM agent generation failed: " + str(e))
            return None

    def _build_onboarding_agent(self) -> LocalAgent:
        onboarding_agent = LocalAgent(
            name="AgentCreator",
            description="Creates AgentCrew agent definitions with web research support",
            llm_service=self.llm_service,
            services={},
            tools=[],
            temperature=1.0,
        )
        if onboarding_agent.llm:
            onboarding_agent.llm.set_system_prompt(_ONBOARDING_SYSTEM_PROMPT)
        search_service = self.services.get("web_search")
        if search_service:
            register_web_search(search_service, onboarding_agent)
            onboarding_agent.resync_tools_to_llm()
        return onboarding_agent

    async def _generate_onboarding_response(
        self, onboarding_agent: LocalAgent, prompt: str
    ) -> str | None:
        history = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        response, _ = await run_agent_loop(onboarding_agent, history)
        return response

    def _copy_llm_attribute(self, name: str) -> Any:
        value = getattr(self.llm_service, name, None)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            return dict(value)
        return value

    def _restore_llm_attribute(self, name: str, value: Any) -> None:
        if value is not None and hasattr(self.llm_service, name):
            setattr(self.llm_service, name, value)

    def _print_assistant_message(self, text: str) -> None:
        self.console.print("")
        self.console.print(
            Panel(
                Markdown(text),
                title="[bold cyan]Agent Creator[/bold cyan]",
                border_style="cyan",
                padding=(0, 2),
            )
        )
        self.console.print("")

    def _ask_onboarding_input(self) -> str | None:
        kb = self._kb_skip()

        @kb.add(Keys.ControlS)
        def _(event):
            """Submit on Ctrl+S."""
            event.current_buffer.validate_and_handle()

        result = prompt(
            HTML(
                "<ansigreen>Your answer</ansigreen> <ansigrey>(type 'skip' to cancel, Alt+Enter or Ctrl+S to submit)</ansigrey>\n"
            ),
            key_bindings=kb,
            multiline=True,
            style=_ONBOARDING_STYLE,
        )
        if result is None:
            return None
        result = result.strip()
        if result.lower() == "skip":
            return None
        self._print_status("Keep processing with your answers...")
        return result

    @staticmethod
    def _build_chat_prompt(initial_message: str, conversation: list) -> str:
        """Build a single prompt string from conversation history."""
        parts = [initial_message]
        for turn in conversation:
            parts.append("\n\nAssistant: " + turn["assistant"])
            parts.append("\n\nUser: " + turn["user"])
        return "".join(parts)

    @staticmethod
    def _extract_toml(text: str) -> str | None:
        """Extract TOML content from a ```toml markdown block or raw [[agents]] text."""
        text = text.strip()
        pattern = r"```(toml)?\s*\n?((?:.|\n)*?)\n?```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(2).strip()
        if text.startswith("[[agents]]"):
            return text
        return None

    @staticmethod
    def _looks_like_agent_definition(parsed: dict[str, Any]) -> bool:
        """Check whether parsed TOML dict contains an agent definition."""
        agents = parsed.get("agents")
        if isinstance(agents, list) and len(agents) > 0:
            return bool(agents[0].get("name") and agents[0].get("system_prompt"))
        if isinstance(agents, list) and len(agents) == 0:
            return False
        return bool(parsed.get("name") and parsed.get("system_prompt"))

    def _save_agent(self, toml_text: str) -> bool:
        """Parse and save the generated agent definition."""
        toml_content = OnboardingService._extract_toml(toml_text)
        if not toml_content:
            self._print_error("Could not parse agent definition from LLM response.")
            logger.debug("Raw LLM response:\n" + toml_text)
            return False

        try:
            parsed = tomllib.loads(toml_content)
        except Exception as e:
            self._print_error("Generated agent definition is not valid TOML: " + str(e))
            logger.debug("TOML content:\n" + toml_content)
            return False

        agents = parsed.get("agents")
        if isinstance(agents, list) and len(agents) > 0:
            agent_def = agents[0]
        elif parsed.get("name") and parsed.get("system_prompt"):
            agent_def = {
                "name": parsed.get("name"),
                "description": parsed.get("description", ""),
                "system_prompt": parsed.get("system_prompt", ""),
                "tools": parsed.get("tools", []),
            }
            if parsed.get("temperature") is not None:
                agent_def["temperature"] = parsed.get("temperature")
            if parsed.get("voice_enabled") is not None:
                agent_def["voice_enabled"] = parsed.get("voice_enabled")
            if parsed.get("voice_id") is not None:
                agent_def["voice_id"] = parsed.get("voice_id")
        else:
            self._print_error("Generated TOML does not contain an [[agents]] entry.")
            return False

        if not agent_def.get("name") or not agent_def.get("system_prompt"):
            self._print_error(
                "Generated agent is missing required fields (name or system_prompt)."
            )
            return False

        try:
            self._write_agent_directly(agent_def)
            return True
        except Exception as e:
            self._print_error("Failed to save agent to agents.toml: " + str(e))
            logger.error("Save agent error: " + str(e))
            return False

    def _write_agent_directly(self, agent_def: dict[str, Any]) -> None:
        """Write agent directly to agents.toml, bypassing reload to avoid chicken-and-egg issues."""
        import tomli_w

        config_path = os.path.expanduser(os.getenv("SW_AGENTS_CONFIG", "./agents.toml"))
        config_dir = os.path.dirname(config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)

        existing: dict[str, Any] = {"agents": []}
        if os.path.exists(config_path):
            try:
                with open(config_path, "rb") as f:
                    existing = tomllib.load(f)
            except Exception:
                logger.debug("Failed to load existing config")

        existing_agents = existing.get("agents", [])
        if not isinstance(existing_agents, list):
            existing_agents = []

        existing_agents.append(agent_def)
        existing["agents"] = existing_agents

        with open(config_path, "wb") as f:
            tomli_w.dump(existing, f, multiline_strings=True)
