from prompt_toolkit.completion import Completer, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.completion import Completion
from AgentCrew.modules.llm.model_registry import ModelRegistry
import os
import re

COMPLETER_PATTERN = re.compile(r"[a-zA-Z0-9-_.]*")


class JumpCompleter(Completer):
    """Completer that shows available conversation turns when typing /jump command."""

    def __init__(self, message_handler=None):
        self.message_handler = message_handler

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /jump command
        if text.startswith("/jump "):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )

            conversation_turns = (
                self.message_handler.conversation_turns if self.message_handler else []
            )
            # Get all available turn numbers
            for i, turn in enumerate(conversation_turns, 1):
                turn_str = str(i)
                if turn_str.startswith(word_before_cursor):
                    # Use the stored preview
                    preview = turn.get_preview(40)
                    display = f"{turn_str}: {preview}"
                    yield Completion(
                        turn_str,
                        start_position=-len(word_before_cursor),
                        display=display,
                    )


class ForkCompleter(Completer):
    """Completer that shows available conversation turns when typing /fork command."""

    def __init__(self, message_handler=None):
        self.message_handler = message_handler

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /fork command
        if text.startswith("/fork "):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )

            conversation_turns = (
                self.message_handler.conversation_turns if self.message_handler else []
            )
            # Get all available turn numbers
            for i, turn in enumerate(conversation_turns, 1):
                turn_str = str(i)
                if turn_str.startswith(word_before_cursor):
                    # Use the stored preview
                    preview = turn.get_preview(40)
                    display = f"{turn_str}: {preview}"
                    yield Completion(
                        turn_str,
                        start_position=-len(word_before_cursor),
                        display=display,
                    )


class ModelCompleter(Completer):
    """Completer that shows available models when typing /model command."""

    def __init__(self):
        self.registry = ModelRegistry.get_instance()

    @staticmethod
    def _fuzzy_match(query, text):
        """Check if all characters in query appear in text in order (case-insensitive).
        Returns the match score (lower is better) or None if no match.
        Score is based on gap penalty — consecutive matches score better.
        """
        query_lower = query.lower()
        text_lower = text.lower()
        query_idx = 0
        score = 0
        last_match_pos = -1

        for i, char in enumerate(text_lower):
            if query_idx < len(query_lower) and char == query_lower[query_idx]:
                # Penalize gaps between matched characters
                if last_match_pos >= 0:
                    score += i - last_match_pos - 1
                last_match_pos = i
                query_idx += 1

        if query_idx == len(query_lower):
            return score
        return None

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /model command
        if text.startswith("/model "):
            model_text = text[len("/model "):]
            filter_text = model_text.split("/")[-1] if "/" in model_text else model_text

            if not filter_text:
                # No filter text yet, show all models
                for provider in self.registry.get_providers():
                    for model in self.registry.get_models_by_provider(provider):
                        display = f"{model.id} - {model.name} ({provider})"
                        yield Completion(
                            f"{provider}/{model.id}",
                            start_position=-len(model_text),
                            display=display,
                        )
                return

            # Get all available models from the registry
            all_models = []
            for provider in self.registry.get_providers():
                for model in self.registry.get_models_by_provider(provider):
                    all_models.append((model.id, model.name, provider))

            filter_lower = filter_text.lower()

            # Score and rank matches: prefix (0) > substring (1) > fuzzy (2+)
            scored = []
            for model_id, model_name, provider in all_models:
                id_lower = model_id.lower()
                name_lower = model_name.lower()

                # Prefix match on model_id — best priority
                if id_lower.startswith(filter_lower):
                    scored.append((0, model_id, model_name, provider))
                    continue

                # Substring match on model_id or model_name
                if filter_lower in id_lower or filter_lower in name_lower:
                    scored.append((1, model_id, model_name, provider))
                    continue

                # Fuzzy character match on model_id
                fuzzy_score = self._fuzzy_match(filter_text, model_id)
                if fuzzy_score is not None:
                    scored.append((2 + fuzzy_score, model_id, model_name, provider))
                    continue

                # Fuzzy character match on model_name
                fuzzy_score = self._fuzzy_match(filter_text, model_name)
                if fuzzy_score is not None:
                    scored.append((2 + fuzzy_score, model_id, model_name, provider))

            # Sort by match quality and cap results
            scored.sort(key=lambda x: x[0])
            for _, model_id, model_name, provider in scored[:20]:
                display = f"{model_id} - {model_name} ({provider})"
                yield Completion(
                    f"{provider}/{model_id}",
                    start_position=-len(model_text),
                    display=display,
                )


class AgentCompleter(Completer):
    """Completer that shows available agents when typing /agent command."""

    def __init__(self):
        from AgentCrew.modules.agents import AgentManager

        self.agent_manager = AgentManager.get_instance()

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /agent command and /export_agent command
        if text.startswith("/agent ") or text.startswith("/export_agent "):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )

            # Get all available agents from the manager
            all_agents = []
            for agent_name, agent in self.agent_manager.agents.items():
                description = getattr(agent, "description", "No description available")
                is_current = (
                    self.agent_manager.current_agent
                    and agent_name == self.agent_manager.current_agent.name
                )
                all_agents.append((agent_name, description, is_current))

            # Filter agents based on what the user has typed so far
            for agent_name, description, is_current in all_agents:
                if agent_name.startswith(word_before_cursor):
                    current_indicator = " (current)" if is_current else ""
                    display = f"{agent_name}{current_indicator} - {description}"
                    yield Completion(
                        agent_name,
                        start_position=-len(word_before_cursor),
                        display=display,
                    )


class AtAgentCompleter(Completer):
    """Handles @AgentName completions mid-text."""

    def __init__(self):
        from AgentCrew.modules.agents import AgentManager

        self.agent_manager = AgentManager.get_instance()

    def get_completions(self, document, complete_event):
        text_to_cursor = document.text_before_cursor
        at_idx = text_to_cursor.rfind("@")
        if at_idx == -1:
            return

        word_after_at = text_to_cursor[at_idx + 1 :]
        if " " in word_after_at:
            return

        for agent_name in self.agent_manager.agents:
            if agent_name.lower().startswith(word_after_at.lower()):
                desc = getattr(self.agent_manager.agents[agent_name], "description", "")
                yield Completion(
                    agent_name,
                    start_position=-len(word_after_at),
                    display=f"@{agent_name} — {desc}",
                )


class ChatCompleter(Completer):
    """Combined completer for chat commands."""

    def __init__(self, message_handler=None):
        self.file_completer = DirectoryListingCompleter()
        self.model_completer = ModelCompleter()
        self.agent_completer = AgentCompleter()
        self.at_agent_completer = AtAgentCompleter()
        self.jump_completer = JumpCompleter(message_handler)
        self.fork_completer = ForkCompleter(message_handler)
        self.mcp_completer = MCPCompleter(message_handler)
        self.drop_completer = DropCompleter(message_handler)
        self.behavior_completer = BehaviorIDCompleter(message_handler)

    def get_completions(self, document, complete_event):
        text = document.text

        text_before = document.text_before_cursor
        at_idx = text_before.rfind("@")
        if (
            at_idx != -1
            and " " not in text_before[at_idx + 1 :]
            and not text.startswith("/")
        ):
            yield from self.at_agent_completer.get_completions(document, complete_event)
            return

        if text.startswith("/model "):
            # Use model completer for /model command
            yield from self.model_completer.get_completions(document, complete_event)
        elif text.startswith("/agent "):
            # Use agent completer for /agent command
            yield from self.agent_completer.get_completions(document, complete_event)
        elif text.startswith("/jump "):
            # Use jump completer for /jump command
            yield from self.jump_completer.get_completions(document, complete_event)
        elif text.startswith("/fork "):
            # Use fork completer for /fork command
            yield from self.fork_completer.get_completions(document, complete_event)
        elif text.startswith("/mcp"):
            yield from self.mcp_completer.get_completions(document, complete_event)
        elif text.startswith("/file "):
            yield from self.file_completer.get_completions(document, complete_event)
        elif text.startswith("/drop "):
            yield from self.drop_completer.get_completions(document, complete_event)
        elif text.startswith(("/delete_behavior ", "/update_behavior ")):
            yield from self.behavior_completer.get_completions(document, complete_event)
        elif text.startswith("/agent_mode "):
            yield from self.get_agent_mode_completions(document)
        elif text.startswith("/export_agent "):
            remaining_text = text[14:]  # Remove "/export_agent "

            if " " in remaining_text and not remaining_text.endswith(","):
                # User has entered agent names and is now entering file path
                yield from self.file_completer.get_completions(document, complete_event)
            else:
                # User is still entering agent names - suggest available agents
                yield from self.agent_completer.get_completions(
                    document, complete_event
                )
        elif text.startswith("/import_agent "):
            # Use file completer for /import_agent command (supports both file paths and URLs)
            yield from self.file_completer.get_completions(document, complete_event)
        elif text.startswith("/"):
            yield from self.get_command_completions(document)

        else:
            yield from self.file_completer.get_completions(document, complete_event)

    def get_command_completions(self, document):
        """Yield completions for all available commands based on user input."""

        commands = [
            ("/clear", "Clear the conversation history"),
            (
                "/copy",
                "Copy the nth-latest assistant response to clipboard (usage: /copy <n>)",
            ),
            (
                "/debug",
                "Show debug info (usage: /debug [agent|chat|system])",
            ),
            ("/think", "Show or set thinking budget (usage: /think [budget])"),
            ("/usage", "Show current provider usage limits"),
            (
                "/consolidate",
                "Consolidate conversation messages (usage: /consolidate [count])",
            ),
            (
                "/evolve",
                "Analyze agent memory and propose a persisted system prompt evolution (usage: /evolve [accept|edit <summary>|decline])",
            ),
            (
                "/unconsolidate",
                "Remove the last consolidated message (usage: /unconsolidate)",
            ),
            (
                "/jump",
                "Jump to a previous conversation turn (usage: /jump [turn_number])",
            ),
            (
                "/fork",
                "Fork conversation at a turn, creating a new branch (usage: /fork [turn_number])",
            ),
            (
                "/voice",
                "Start with voice input",
            ),
            ("/agent", "Switch agent or list available agents (usage: /agent [name])"),
            ("/model", "Switch model or list available models (usage: /model [id])"),
            (
                "/mcp",
                "list MCP prompts or fetch specific prompt (usage: /mcp [server_id/prompt_name])",
            ),
            ("/file", "Process a file (usage: /file <path>)"),
            (
                "/drop",
                "Remove a queued file or list queued files (usage: /drop [file_id])",
            ),
            (
                "/export_agent",
                "Export selected agents to TOML file (usage: /export_agent <agent_names> <output_file>)",
            ),
            (
                "/import_agent",
                "Import/replace agent configuration from file or URL (usage: /import_agent <file_or_url>)",
            ),
            ("/edit_agent", "Open agent configuration file in default editor"),
            ("/edit_mcp", "Open MCP configuration file in default editor"),
            (
                "/edit_config",
                "Open AgentCrew global configuration file in default editor",
            ),
            ("/list", "list available conversations"),
            ("/load", "Load or browse conversations (usage: /load [conversation_id])"),
            ("/help", "Show help message"),
            ("/retry", "Retry the last assistant response"),
            ("/toggle_transfer", "Toggle agent transfer enforcement on/off"),
            (
                "/agent_mode",
                "View or switch agent interaction mode (usage: /agent_mode [transfer|delegate|none])",
            ),
            (
                "/toggle_session_yolo",
                "Toggle Auto-Approve Mode for Tool Calls (this session only)",
            ),
            ("/list_behaviors", "list all agent adaptive behaviors"),
            (
                "/update_behavior",
                "Update or create an adaptive behavior (usage: /update_behavior <scope> <id> <behavior_text>)",
            ),
            (
                "/delete_behavior",
                "Delete an adaptive behavior (usage: /delete_behavior <id>)",
            ),
            (
                "/clean_behaviors",
                "Normalize and deduplicate adaptive behaviors (usage: /clean_behaviors global|project)",
            ),
            (
                "/visual",
                "Open visual mode to view raw message content with vim-like navigation",
            ),
            ("/exit", "Exit the application"),
            ("/quit", "Exit the application"),
        ]

        # Filter commands based on what the user has typed
        for command, description in commands:
            # Use document.text instead of word_before_cursor for filtering commands that start with /
            if command.startswith(document.text):
                yield Completion(
                    command,
                    start_position=-len(document.text),
                    display=f"{command} - {description}",
                )

    def get_agent_mode_completions(self, document):
        """Yield completions for /agent_mode sub-arguments."""
        word_before_cursor = document.get_word_before_cursor(pattern=COMPLETER_PATTERN)
        modes = [
            ("transfer", "Transfer mode — one agent active at a time"),
            ("delegate", "Delegate mode — dispatch tasks to agents in parallel"),
            ("none", "Disable agent-to-agent interaction"),
        ]
        for mode_val, desc in modes:
            if mode_val.startswith(word_before_cursor):
                yield Completion(
                    mode_val,
                    start_position=-len(word_before_cursor),
                    display=f"{mode_val} - {desc}",
                )


class MCPCompleter(Completer):
    """Completer that shows available MCP prompts when typing /mcp command."""

    def __init__(self, message_handler=None):
        if message_handler:
            self.mcp_service = message_handler.mcp_manager.mcp_service

    def get_completions(self, document, complete_event):
        text = document.text
        if text.startswith("/mcp "):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )
            # Collect all prompts from all servers
            if self.mcp_service and hasattr(self.mcp_service, "server_prompts"):
                for server_id, prompts in self.mcp_service.server_prompts.items():
                    for prompt in prompts:
                        # Each prompt may be a dict or object; support both
                        prompt_name = getattr(prompt, "name", None) or prompt.get(
                            "name"
                        )
                        if prompt_name and f"{server_id}/{prompt_name}".startswith(
                            word_before_cursor
                        ):
                            display = f"{server_id}/{prompt_name}"
                            yield Completion(
                                display,
                                start_position=-len(word_before_cursor),
                                display=display,
                            )


class DropCompleter(Completer):
    """Completer that shows available queued files when typing /drop command."""

    def __init__(self, message_handler=None):
        self.message_handler = message_handler

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /drop command
        if text.startswith("/drop "):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )

            # Get all queued attached files
            queued_files = (
                self.message_handler._queued_attached_files
                if self.message_handler
                else []
            )

            # Extract file paths from queued files and create completions
            for file_path in queued_files:
                if file_path.startswith(word_before_cursor):
                    yield Completion(
                        file_path,
                        start_position=-len(word_before_cursor),
                        display=file_path,
                    )
                elif word_before_cursor.startswith("drop"):
                    yield Completion(
                        file_path,
                        display=file_path,
                    )


class BehaviorIDCompleter(Completer):
    """Completer that shows available behavior IDs when typing /delete_behavior or /update_behavior commands."""

    def __init__(self, message_handler=None):
        self.message_handler = message_handler

    def get_completions(self, document, complete_event):
        text = document.text

        if text.startswith(
            (
                "/delete_behavior global ",
                "/delete_behavior project ",
                "/update_behavior global ",
                "/update_behavior project ",
            )
        ):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )

            if self.message_handler and self.message_handler.persistent_service:
                try:
                    scope = text.split(" ")[1].strip()
                    all_behaviors = (
                        self.message_handler.persistent_service.get_adaptive_behaviors(
                            self.message_handler.agent.name, is_local=scope == "project"
                        )
                    )
                    behavior_ids = []
                    for id, _ in all_behaviors.items():
                        behavior_ids.append(id)

                    for behavior_id in behavior_ids:
                        if behavior_id.startswith(word_before_cursor):
                            yield Completion(
                                behavior_id,
                                start_position=-len(word_before_cursor),
                                display=behavior_id,
                            )
                except Exception:
                    pass
        elif text.startswith(("/delete_behavior ", "/update_behavior ")):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )
            for scope in ["global", "project"]:
                if scope.startswith(word_before_cursor):
                    yield Completion(
                        scope,
                        start_position=-len(word_before_cursor),
                        display=scope,
                    )


class DirectoryListingCompleter(Completer):
    def __init__(self):
        # Use PathCompleter for the heavy lifting
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text
        if text == "/":
            return
        # Look for patterns that might indicate a path
        # This regex searches for a potential directory path
        path_match = re.search(r"((~|\.{1,2})?(\/[^\s]*))$", text)

        if path_match:
            path = path_match.group(0)

            # Create a new document with just the path part
            # This is needed because we want completions only for the path part
            path_document = Document(path, cursor_position=len(path))

            # Get completions from PathCompleter
            for completion in self.path_completer.get_completions(
                path_document, complete_event
            ):
                # Yield the completions
                completion.text = completion.text.replace(" ", "\\ ")
                yield completion

    def get_path_completions(self, path):
        """Helper method to get completions for a specific path"""
        # Expand user directory if path starts with ~
        if path.startswith("~"):
            path = os.path.expanduser(path)

        # Ignore network paths
        if path.startswith("//"):
            return

        # Get the directory part
        directory = os.path.dirname(path) if "/" in path else path

        # If directory is empty, use current directory
        if not directory:
            directory = "."

        # If directory ends with '/', it's already a complete directory path
        if path.endswith("/"):
            directory = path

        # Get files and directories in the given directory
        try:
            entries = os.listdir(directory)
            return entries
        except (FileNotFoundError, NotADirectoryError):
            return []
