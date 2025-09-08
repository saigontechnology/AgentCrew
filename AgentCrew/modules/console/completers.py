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


class ModelCompleter(Completer):
    """Completer that shows available models when typing /model command."""

    def __init__(self):
        self.registry = ModelRegistry.get_instance()

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /model command
        if text.startswith("/model "):
            word_before_cursor = document.get_word_before_cursor(
                pattern=COMPLETER_PATTERN
            )

            # Get all available models from the registry
            all_models = []
            for provider in self.registry.get_providers():
                for model in self.registry.get_models_by_provider(provider):
                    all_models.append((model.id, model.name, provider))

            # Filter models based on what the user has typed so far
            for model_id, model_name, provider in all_models:
                if model_id.startswith(word_before_cursor):
                    display = f"{model_id} - {model_name} ({provider})"
                    yield Completion(
                        f"{provider}/{model_id}",
                        start_position=-len(word_before_cursor),
                        display=display,
                    )


class AgentCompleter(Completer):
    """Completer that shows available agents when typing /agent command."""

    def __init__(self):
        from AgentCrew.modules.agents.manager import AgentManager

        self.agent_manager = AgentManager.get_instance()

    def get_completions(self, document, complete_event):
        text = document.text

        # Only provide completions for the /agent command
        if text.startswith("/agent "):
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


class ChatCompleter(Completer):
    """Combined completer for chat commands."""

    def __init__(self, message_handler=None):
        self.file_completer = DirectoryListingCompleter()
        self.model_completer = ModelCompleter()
        self.agent_completer = AgentCompleter()
        self.jump_completer = JumpCompleter(message_handler)
        self.mcp_completer = MCPCompleter(message_handler)
        self.drop_completer = DropCompleter(message_handler)

    def get_completions(self, document, complete_event):
        text = document.text

        if text.startswith("/model "):
            # Use model completer for /model command
            yield from self.model_completer.get_completions(document, complete_event)
        elif text.startswith("/agent "):
            # Use agent completer for /agent command
            yield from self.agent_completer.get_completions(document, complete_event)
        elif text.startswith("/jump "):
            # Use jump completer for /jump command
            yield from self.jump_completer.get_completions(document, complete_event)
        elif text.startswith("/mcp"):
            yield from self.mcp_completer.get_completions(document, complete_event)
        elif text.startswith("/file "):
            yield from self.file_completer.get_completions(document, complete_event)
        elif text.startswith("/drop "):
            yield from self.drop_completer.get_completions(document, complete_event)
        elif text.startswith("/"):
            yield from self.get_command_completions(document)

        else:
            yield from self.file_completer.get_completions(document, complete_event)

    def get_command_completions(self, document):
        """Yield completions for all available commands based on user input."""

        commands = [
            ("/clear", "Clear the conversation history"),
            ("/copy", "Copy the latest assistant response to clipboard"),
            (
                "/debug",
                "Show debug information (agent history and streamline messages)",
            ),
            ("/think", "Set thinking budget (usage: /think <budget>)"),
            (
                "/consolidate",
                "Consolidate conversation messages (usage: /consolidate [count])",
            ),
            (
                "/unconsolidate",
                "Remove the last consolidated message (usage: /unconsolidate)",
            ),
            (
                "/jump",
                "Jump to a previous conversation turn (usage: /jump <turn_number>)",
            ),
            (
                "/voice",
                "Start with voice input",
            ),
            ("/agent", "Switch agent or list available agents (usage: /agent [name])"),
            ("/model", "Switch model or list available models (usage: /model [id])"),
            (
                "/mcp",
                "List MCP prompts or fetch specific prompt (usage: /mcp [server_id/prompt_name])",
            ),
            ("/file", "Process a file (usage: /file <path>)"),
            ("/drop", "Remove a queued file from processing (usage: /drop <file_id>)"),
            ("/list", "List available conversations"),
            ("/load", "Load a conversation (usage: /load <conversation_id>)"),
            ("/help", "Show help message"),
            ("/toggle_transfer", "Toggle agent transfer enforcement on/off"),
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
