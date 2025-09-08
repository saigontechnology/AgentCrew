from PySide6.QtWidgets import QCompleter
from PySide6.QtCore import Qt
from AgentCrew.modules.llm.model_registry import ModelRegistry


class GuiModelCompleter:
    """GUI completer for model commands."""

    def __init__(self):
        self.registry = ModelRegistry.get_instance()

    def get_completions(self, text):
        """Get model completions for GUI."""
        if not text.startswith("/model "):
            return []

        word_after_command = text[7:]  # Remove "/model "

        # Get all available models from the registry
        all_models = []
        for provider in self.registry.get_providers():
            for model in self.registry.get_models_by_provider(provider):
                all_models.append((model.id, model.name, provider))

        # Filter models based on what the user has typed
        completions = []
        for model_id, model_name, provider in all_models:
            if model_id.startswith(word_after_command):
                completions.append(f"{provider}/{model_id}")

        return completions


class GuiAgentCompleter:
    """GUI completer for agent commands."""

    def __init__(self):
        from AgentCrew.modules.agents.manager import AgentManager

        self.agent_manager = AgentManager.get_instance()

    def get_completions(self, text):
        """Get agent completions for GUI."""
        if not text.startswith("/agent "):
            return []

        word_after_command = text[7:]  # Remove "/agent "

        # Get all available agents from the manager
        completions = []
        for agent_name, agent in self.agent_manager.agents.items():
            if agent_name.startswith(word_after_command):
                completions.append(agent_name)

        return completions


class GuiJumpCompleter:
    """GUI completer for jump commands."""

    def __init__(self, message_handler=None):
        self.message_handler = message_handler

    def get_completions(self, text):
        """Get jump completions for GUI."""
        if not text.startswith("/jump "):
            return []

        word_after_command = text[6:]  # Remove "/jump "

        conversation_turns = (
            self.message_handler.conversation_turns if self.message_handler else []
        )

        completions = []
        for i, turn in enumerate(conversation_turns, 1):
            turn_str = str(i)
            if turn_str.startswith(word_after_command):
                completions.append(turn_str)

        return completions


class GuiMCPCompleter:
    """GUI completer for MCP commands."""

    def __init__(self, message_handler=None):
        if message_handler:
            self.mcp_service = message_handler.mcp_manager.mcp_service
        else:
            self.mcp_service = None

    def get_completions(self, text):
        """Get MCP completions for GUI."""
        if not text.startswith("/mcp "):
            return []

        word_after_command = text[5:]  # Remove "/mcp "

        completions = []
        if self.mcp_service and hasattr(self.mcp_service, "server_prompts"):
            for server_id, prompts in self.mcp_service.server_prompts.items():
                for prompt in prompts:
                    prompt_name = getattr(prompt, "name", None) or prompt.get("name")
                    if prompt_name:
                        full_name = f"{server_id}/{prompt_name}"
                        if full_name.startswith(word_after_command):
                            completions.append(full_name)

        return completions


class GuiCommandCompleter:
    """GUI completer for all commands."""

    def __init__(self):
        self.commands = [
            "/clear",
            "/copy",
            "/debug",
            "/think",
            "/consolidate",
            "/unconsolidate",
            "/jump",
            "/agent",
            "/model",
            "/mcp",
            "/file",
            "/list",
            "/load",
            "/help",
            "/toggle_transfer",
            "/exit",
            "/quit",
        ]

    def get_completions(self, text):
        """Get command completions for GUI."""
        if not text.startswith("/"):
            return []

        completions = []
        for command in self.commands:
            if command.startswith(text):
                completions.append(command)

        return completions


class GuiChatCompleter:
    """Combined GUI completer for chat commands."""

    def __init__(self, message_handler=None):
        self.model_completer = GuiModelCompleter()
        self.agent_completer = GuiAgentCompleter()
        self.jump_completer = GuiJumpCompleter(message_handler)
        self.mcp_completer = GuiMCPCompleter(message_handler)
        self.command_completer = GuiCommandCompleter()

    def get_completions(self, text):
        """Get all completions for the given text."""
        if text.startswith("/model "):
            return self.model_completer.get_completions(text)
        elif text.startswith("/agent "):
            return self.agent_completer.get_completions(text)
        elif text.startswith("/jump "):
            return self.jump_completer.get_completions(text)
        elif text.startswith("/mcp "):
            return self.mcp_completer.get_completions(text)
        elif text.startswith("/"):
            return self.command_completer.get_completions(text)
        else:
            return []

    def create_qt_completer(self, parent=None):
        """Create a QCompleter instance for Qt widgets."""
        completer = QCompleter(parent)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
        return completer
