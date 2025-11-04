import os
import sys
import json
import time
import asyncio
import webbrowser
import nest_asyncio
from typing import Optional, Dict, Any, List

import click
import requests

from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.memory.chroma_service import ChromaMemoryService
from AgentCrew.modules.memory.context_persistent import ContextPersistenceService
from AgentCrew.modules.clipboard import ClipboardService
from AgentCrew.modules.web_search import TavilySearchService
from AgentCrew.modules.code_analysis import CodeAnalysisService
from AgentCrew.modules.image_generation import ImageGenerationService
from AgentCrew.modules.browser_automation import BrowserAutomationService
from AgentCrew.modules.agents.manager import AgentManager
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.remote_agent import RemoteAgent
from AgentCrew.modules.agents.example import (
    DEFAULT_NAME,
    DEFAULT_DESCRIPTION,
    DEFAULT_PROMPT,
)

nest_asyncio.apply()


PROVIDER_LIST = [
    "claude",
    "groq",
    "openai",
    "google",
    "deepinfra",
    "github_copilot",
    "copilot_response",
]


class AgentCrewApplication:
    """
    Centralized application class for AgentCrew.

    This class handles:
    - API key loading from configuration
    - Service initialization and management
    - Agent setup and configuration
    - GitHub Copilot authentication
    - Running different modes (console, GUI, server, job)
    """

    def __init__(self):
        """Initialize the AgentCrew application."""
        self.services: Optional[Dict[str, Any]] = None
        self.agent_manager: Optional[AgentManager] = None
        self.config_manager = ConfigManagement()

        self.load_api_keys_from_config()

    def load_api_keys_from_config(self) -> None:
        """
        Loads API keys from the global config file and sets them as environment variables,
        prioritizing them over any existing environment variables.
        """
        config_file_path = os.getenv("AGENTCREW_CONFIG_PATH")
        if not config_file_path:
            # Default for when AGENTCREW_CONFIG_PATH is not set (e.g. dev mode, not using cli_prod)
            config_file_path = "./config.json"
        config_file_path = os.path.expanduser(config_file_path)

        api_keys_config = {}
        if os.path.exists(config_file_path):
            try:
                with open(config_file_path, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                    if isinstance(loaded_config, dict) and isinstance(
                        loaded_config.get("api_keys"), dict
                    ):
                        api_keys_config = loaded_config["api_keys"]
                    else:
                        click.echo(
                            f"⚠️  API keys in {config_file_path} are not in the expected format.",
                            err=True,
                        )
            except json.JSONDecodeError:
                click.echo(
                    f"⚠️  Error decoding API keys from {config_file_path}.", err=True
                )
            except Exception as e:
                click.echo(
                    f"⚠️  Could not load API keys from {config_file_path}: {e}",
                    err=True,
                )

        keys_to_check = [
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "GROQ_API_KEY",
            "DEEPINFRA_API_KEY",
            "GITHUB_COPILOT_API_KEY",
            "TAVILY_API_KEY",
            "VOYAGE_API_KEY",
            "ELEVENLABS_API_KEY",
        ]

        for key_name in keys_to_check:
            if key_name in api_keys_config and api_keys_config[key_name]:
                # Prioritize config file over existing environment variables
                os.environ[key_name] = str(api_keys_config[key_name]).strip()

    def _detect_provider(self) -> Optional[str]:
        """
        Detect available LLM provider from environment or last used provider.

        Returns:
            Provider name or None if no provider found
        """
        # Try to restore last used provider
        try:
            last_provider = self.config_manager.get_last_used_provider()
            if last_provider:
                # Verify the provider is still available
                if last_provider in PROVIDER_LIST:
                    # Check if API key is available for this provider
                    api_key_map = {
                        "claude": "ANTHROPIC_API_KEY",
                        "google": "GEMINI_API_KEY",
                        "openai": "OPENAI_API_KEY",
                        "groq": "GROQ_API_KEY",
                        "deepinfra": "DEEPINFRA_API_KEY",
                        "github_copilot": "GITHUB_COPILOT_API_KEY",
                        "copilot_response": "GITHUB_COPILOT_API_KEY",
                    }
                    if os.getenv(api_key_map.get(last_provider, "")):
                        return last_provider
                else:
                    # Check if it's a custom provider
                    custom_providers = (
                        self.config_manager.read_custom_llm_providers_config()
                    )
                    if any(p["name"] == last_provider for p in custom_providers):
                        return last_provider
        except Exception as e:
            click.echo(f"⚠️  Could not restore last used provider: {e}")

        # Fall back to environment variable detection
        if os.getenv("GITHUB_COPILOT_API_KEY"):
            return "github_copilot"
        elif os.getenv("ANTHROPIC_API_KEY"):
            return "claude"
        elif os.getenv("GEMINI_API_KEY"):
            return "google"
        elif os.getenv("OPENAI_API_KEY"):
            return "openai"
        elif os.getenv("GROQ_API_KEY"):
            return "groq"
        elif os.getenv("DEEPINFRA_API_KEY"):
            return "deepinfra"
        else:
            custom_providers = self.config_manager.read_custom_llm_providers_config()
            if len(custom_providers) > 0:
                return custom_providers[0]["name"]

        return None

    def setup_services(
        self, provider: str, memory_llm: Optional[str] = None, need_memory: bool = True
    ) -> Dict[str, Any]:
        """
        Initialize and configure all AgentCrew services.

        Args:
            provider: LLM provider name (e.g., 'claude', 'openai', 'google')
            memory_llm: Optional LLM provider for memory service

        Returns:
            Dictionary of initialized services
        """
        registry = ModelRegistry.get_instance()
        llm_manager = ServiceManager.get_instance()

        models = registry.get_models_by_provider(provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            registry.set_current_model(f"{default_model.provider}/{default_model.id}")

        llm_service = llm_manager.get_service(provider)

        try:
            last_model = self.config_manager.get_last_used_model()
            last_provider = self.config_manager.get_last_used_provider()

            if last_model and last_provider:
                should_restore = False
                if provider == last_provider:
                    should_restore = True

                last_model_class = registry.get_model(last_model)
                if should_restore and last_model_class:
                    llm_service.model = last_model_class.id
        except Exception as e:
            click.echo(f"⚠️  Could not restore last used model: {e}")

        memory_service = None
        context_service = None
        if need_memory:
            if memory_llm:
                memory_service = ChromaMemoryService(
                    llm_service=llm_manager.initialize_standalone_service(memory_llm)
                )
            else:
                memory_service = ChromaMemoryService(
                    llm_service=llm_manager.initialize_standalone_service(provider)
                )

            context_service = ContextPersistenceService()
        clipboard_service = ClipboardService()

        try:
            search_service = TavilySearchService()
        except Exception as e:
            click.echo(f"⚠️ Web search tools not available: {str(e)}")
            search_service = None

        try:
            code_analysis_service = CodeAnalysisService()
        except Exception as e:
            click.echo(f"⚠️ Code analysis tool not available: {str(e)}")
            code_analysis_service = None

        try:
            if os.getenv("OPENAI_API_KEY"):
                image_gen_service = ImageGenerationService()
            else:
                image_gen_service = None
                click.echo(
                    "⚠️ Image generation service not available: No API keys found."
                )
        except Exception as e:
            click.echo(f"⚠️ Image generation service not available: {str(e)}")
            image_gen_service = None

        try:
            browser_automation_service = BrowserAutomationService()
        except Exception as e:
            click.echo(f"⚠️ Browser automation service not available: {str(e)}")
            browser_automation_service = None

        try:
            from AgentCrew.modules.file_editing import FileEditingService

            file_editing_service = FileEditingService()
        except Exception as e:
            click.echo(f"⚠️ File editing service not available: {str(e)}")
            file_editing_service = None

        try:
            from AgentCrew.modules.command_execution import CommandExecutionService

            command_execution_service = CommandExecutionService.get_instance()
        except Exception as e:
            click.echo(f"⚠️ Command execution service not available: {str(e)}")
            command_execution_service = None

        self.services = {
            "llm": llm_service,
            "memory": memory_service,
            "clipboard": clipboard_service,
            "code_analysis": code_analysis_service,
            "web_search": search_service,
            "context_persistent": context_service,
            "image_generation": image_gen_service,
            "browser": browser_automation_service,
            "file_editing": file_editing_service,
            "command_execution": command_execution_service,
        }
        return self.services

    def setup_agents(
        self,
        services: Dict[str, Any],
        config_path: Optional[str] = None,
        remoting_provider: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> AgentManager:
        """
        Set up the agent system with specialized agents.

        Args:
            services: Dictionary of services
            config_path: Path to agent configuration file
            remoting_provider: Provider for remoting mode
            model_id: Model ID for remoting mode

        Returns:
            Configured AgentManager instance
        """

        self.agent_manager = AgentManager.get_instance()
        llm_manager = ServiceManager.get_instance()

        services["agent_manager"] = self.agent_manager

        global_config = self.config_manager.read_global_config_data()
        self.agent_manager.context_shrink_enabled = global_config.get(
            "global_settings", {}
        ).get("auto_context_shrink", True)
        self.agent_manager.shrink_excluded_list = global_config.get(
            "global_settings", {}
        ).get("shrink_excluded", [])

        llm_service = services["llm"]

        if config_path:
            os.environ["SW_AGENTS_CONFIG"] = config_path
        else:
            config_path = os.getenv("SW_AGENTS_CONFIG")
            if not config_path:
                config_path = "./agents.toml"
            if not os.path.exists(config_path):
                click.echo(
                    f"Agent configuration not found at {config_path}. Creating default configuration."
                )
                os.makedirs(os.path.dirname(config_path), exist_ok=True)

                default_config = f"""
[[agents]]
name = "{DEFAULT_NAME}"
description = "{DEFAULT_DESCRIPTION}"
system_prompt = '''{DEFAULT_PROMPT}'''
tools = ["memory", "browser", "web_search", "code_analysis"]
"""

                with open(config_path, "w+", encoding="utf-8") as f:
                    f.write(default_config)

                click.echo(f"Created default agent configuration at {config_path}")

        agent_definitions = AgentManager.load_agents_from_config(config_path)

        for agent_def in agent_definitions:
            if agent_def.get("base_url", ""):
                try:
                    agent = RemoteAgent(
                        agent_def["name"],
                        agent_def.get("base_url"),
                        headers=agent_def.get("headers", {}),
                    )
                except Exception:
                    print("Error: cannot connect to remote agent, skipping...")
                    continue
            else:
                if remoting_provider:
                    llm_service = llm_manager.initialize_standalone_service(
                        remoting_provider
                    )
                    if model_id:
                        llm_service.model = model_id
                agent = LocalAgent(
                    name=agent_def["name"],
                    description=agent_def["description"],
                    llm_service=llm_service,
                    services=services,
                    tools=agent_def["tools"],
                    temperature=agent_def.get("temperature", None),
                    voice_enabled=agent_def.get("voice_enabled", "disabled"),
                    voice_id=agent_def.get("voice_id", None),
                )
                agent.set_system_prompt(agent_def["system_prompt"])
                if remoting_provider:
                    agent.set_custom_system_prompt(
                        self.agent_manager.get_remote_system_prompt()
                    )
                    agent.is_remoting_mode = True
                    agent.activate()
            self.agent_manager.register_agent(agent)

        from AgentCrew.modules.mcpclient.tool import register as mcp_register

        mcp_register()

        if remoting_provider:
            from AgentCrew.modules.mcpclient import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            mcp_manager.initialize_for_agent()
            return self.agent_manager

        return self.agent_manager

    def restore_last_agent(self) -> None:
        initial_agent_selected = False
        try:
            last_agent = self.config_manager.get_last_used_agent()

            if (
                last_agent
                and self.agent_manager
                and last_agent in self.agent_manager.agents
            ):
                if self.agent_manager.select_agent(last_agent):
                    initial_agent_selected = True
        except Exception as e:
            # Don't fail startup if restoration fails
            click.echo(f"⚠️  Could not restore last used agent: {e}")

        if not initial_agent_selected and self.agent_manager:
            first_agent_name = list(self.agent_manager.agents.keys())[0]
            if not self.agent_manager.select_agent(first_agent_name):
                available_agents = ", ".join(self.agent_manager.agents.keys())
                click.echo(
                    f"⚠️ Unknown agent: {first_agent_name}. Using default agent. Available agents: {available_agents}"
                )

    def run_console(
        self,
        provider: Optional[str] = None,
        agent_config: Optional[str] = None,
        mcp_config: Optional[str] = None,
        memory_llm: Optional[str] = None,
        with_voice: bool = False,
    ) -> None:
        """
        Run AgentCrew in console/terminal mode.

        Args:
            provider: LLM provider name
            agent_config: Path to agent configuration file
            mcp_config: Path to MCP servers configuration file
            memory_llm: LLM provider for memory service
            with_voice: Enable voice input/output
        """
        from AgentCrew.modules.console import ConsoleUI
        from AgentCrew.modules.chat import MessageHandler
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            if provider is None:
                provider = self._detect_provider()
                if provider is None:
                    raise ValueError(
                        "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, or DEEPINFRA_API_KEY"
                    )

            services = self.setup_services(provider, memory_llm)

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            self.setup_agents(services, agent_config)
            self.restore_last_agent()

            # Create the message handler
            message_handler = MessageHandler(
                services["memory"], services["context_persistent"], with_voice
            )

            ui = ConsoleUI(message_handler)
            ui.start()
        except Exception as e:
            import traceback

            print(traceback.format_exc())
            click.echo(f"❌ Error: {str(e)}", err=True)
        finally:
            MCPSessionManager.get_instance().cleanup()

    def run_gui(
        self,
        provider: Optional[str] = None,
        agent_config: Optional[str] = None,
        mcp_config: Optional[str] = None,
        memory_llm: Optional[str] = None,
        with_voice: bool = False,
    ) -> None:
        """
        Run AgentCrew in GUI mode.

        Args:
            provider: LLM provider name
            agent_config: Path to agent configuration file
            mcp_config: Path to MCP servers configuration file
            memory_llm: LLM provider for memory service
            with_voice: Enable voice input/output
        """
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from AgentCrew.modules.gui import ChatWindow
        from AgentCrew.modules.chat import MessageHandler
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            # Detect provider if not specified
            if provider is None:
                provider = self._detect_provider()
                if provider is None:
                    # Show config window to setup API keys
                    from AgentCrew.modules.gui.widgets.config_window import ConfigWindow

                    app = QApplication(sys.argv)
                    config_window = ConfigWindow()
                    config_window.tab_widget.setCurrentIndex(3)  # Show Settings tab
                    config_window.show()
                    sys.exit(app.exec())

            services = self.setup_services(provider, memory_llm)

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            # Set up the agent system
            self.setup_agents(services, agent_config)
            self.restore_last_agent()

            # Create the message handler
            message_handler = MessageHandler(
                services["memory"], services["context_persistent"], with_voice
            )

            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseOpenGLES)
            app = QApplication(sys.argv)
            chat_window = ChatWindow(message_handler)
            chat_window.show()
            sys.exit(app.exec())
        except Exception as e:
            import traceback

            print(traceback.format_exc())
            click.echo(f"❌ Error: {str(e)}", err=True)
        finally:
            MCPSessionManager.get_instance().cleanup()

    def run_server(
        self,
        host: str = "0.0.0.0",
        port: int = 41241,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_config: Optional[str] = None,
        api_key: Optional[str] = None,
        mcp_config: Optional[str] = None,
        memory_llm: Optional[str] = None,
    ) -> None:
        """
        Run AgentCrew as an A2A server.

        Args:
            host: Host to bind the server to
            port: Port to bind the server to
            base_url: Base URL for agent endpoints
            provider: LLM provider name
            model_id: Model ID from provider
            agent_config: Path to agent configuration file
            api_key: API key for authentication (optional)
            mcp_config: Path to MCP servers configuration file
            memory_llm: LLM provider for memory service
        """
        from AgentCrew.modules.a2a.server import A2AServer
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            # Set default base URL if not provided
            if not base_url:
                base_url = f"http://{host}:{port}"

            # Detect provider if not specified
            if provider is None:
                provider = self._detect_provider()
                if provider is None:
                    raise ValueError(
                        "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, or DEEPINFRA_API_KEY"
                    )

            services = self.setup_services(provider, memory_llm)

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            os.environ["AGENTCREW_DISABLE_GUI"] = "true"

            # Set up agents from configuration
            self.setup_agents(services, agent_config, provider, model_id)

            if self.agent_manager is None:
                raise ValueError("Agent manager is not initialized")

            # Get agent manager
            self.agent_manager.enforce_transfer = False

            # Create and start server
            server = A2AServer(
                agent_manager=self.agent_manager,
                host=host,
                port=port,
                base_url=base_url,
                api_key=api_key,
            )

            click.echo(f"Starting A2A server on {host}:{port}")
            click.echo(
                f"Available agents: {', '.join(self.agent_manager.agents.keys())}"
            )
            server.start()
        except Exception as e:
            import traceback

            print(traceback.format_exc())
            click.echo(f"❌ Error: {str(e)}", err=True)
        finally:
            MCPSessionManager.get_instance().cleanup()

    def _parse_output_schema(self, schema_input: str) -> tuple[str, dict]:
        """
        Parse output schema from file or JSON string and create a custom system prompt.

        Args:
            schema_input: Either a file path to a JSON schema or a JSON schema string

        Returns:
            Custom system prompt instructing the agent to follow the schema

        Raises:
            ValueError: If schema is invalid JSON or file doesn't exist
        """
        try:
            from AgentCrew.modules.prompts.constants import SCHEMA_ENFORCEMENT_PROMPT

            # Try to parse as file path first
            if os.path.exists(schema_input):
                with open(schema_input, "r", encoding="utf-8") as f:
                    schema_dict = json.load(f)
            else:
                # Try to parse as JSON string
                schema_dict = json.loads(schema_input)

            # Format the schema as a pretty-printed JSON string
            schema_json = json.dumps(schema_dict, indent=2)

            # Create enforcement prompt
            enforcement_prompt = SCHEMA_ENFORCEMENT_PROMPT.replace(
                "{schema_json}", schema_json
            )
            return enforcement_prompt, schema_dict

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON schema: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load output schema: {e}")

    def _validate_response_against_schema(
        self, response: str, schema_dict: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate agent response against JSON schema.

        Args:
            response: Agent's response string (expected to be valid JSON)
            schema_dict: JSON schema dictionary

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if response matches schema, False otherwise
            - error_message: Detailed error message if validation fails, None if valid
        """
        from jsonschema import validate, ValidationError

        # Parse response as JSON
        try:
            response_json = json.loads(response)
        except json.JSONDecodeError as e:
            return (
                False,
                f"Response is not valid JSON: {e}\n\nResponse received:\n{response[:500]}",
            )

        # Validate against schema
        try:
            validate(instance=response_json, schema=schema_dict)
            return True, None
        except ValidationError as e:
            error_details = (
                "JSON Schema Validation Error:\n"
                f"  - Path: {' -> '.join(str(p) for p in e.path) if e.path else 'root'}\n"
                f"  - Error: {e.message}\n"
                f"  - Failed value: {json.dumps(e.instance, indent=2)}\n"
            )
            if e.schema_path:
                error_details += (
                    f"  - Schema path: {' -> '.join(str(p) for p in e.schema_path)}\n"
                )
            return False, error_details

    def run_job(
        self,
        agent: str,
        task: str,
        files: Optional[List[str]] = None,
        provider: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_config: Optional[str] = None,
        mcp_config: Optional[str] = None,
        memory_llm: Optional[str] = None,
        output_schema: Optional[str] = None,
    ) -> str:
        """
        Run a single job/task with an agent.

        Args:
            agent: Name of the agent to run
            task: Task description
            files: List of file paths to attach
            provider: LLM provider name
            model_id: Model ID from provider
            agent_config: Path to agent configuration file
            mcp_config: Path to MCP servers configuration file
            memory_llm: LLM provider for memory service
            output_schema: JSON schema (file path or JSON string) for enforcing structured output

        Returns:
            Agent response as string
        """
        from AgentCrew.modules.chat import MessageHandler
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            # Detect provider if not specified
            if provider is None:
                provider = self._detect_provider()
                if provider is None:
                    raise ValueError(
                        "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, or DEEPINFRA_API_KEY"
                    )

            services = self.setup_services(provider, memory_llm, need_memory=False)

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            os.environ["AGENTCREW_DISABLE_GUI"] = "true"

            # Set up agents from configuration
            self.setup_agents(services, agent_config)

            # Get agent manager
            llm_manager = ServiceManager.get_instance()

            llm_service = llm_manager.get_service(provider)
            if model_id:
                llm_service.model = model_id

            if self.agent_manager is None:
                raise ValueError("Agent manager is not initialized")

            self.agent_manager.update_llm_service(llm_service)

            for local_agent in self.agent_manager.agents:
                if isinstance(local_agent, LocalAgent):
                    local_agent.is_remoting_mode = True

            self.agent_manager.enforce_transfer = False
            self.agent_manager.one_turn_process = True

            current_agent = self.agent_manager.get_local_agent(agent)

            if current_agent:
                # Parse schema if provided
                schema_dict = None
                if output_schema and isinstance(current_agent, LocalAgent):
                    schema_prompt, schema_dict = self._parse_output_schema(
                        output_schema
                    )
                    current_agent.set_custom_system_prompt(schema_prompt)

                self.agent_manager.select_agent(current_agent.name)

                message_handler = MessageHandler(
                    services["memory"], services["context_persistent"]
                )
                message_handler.is_non_interactive = True
                message_handler.agent = current_agent

                # Process files if provided
                if files:
                    for file_path in files:
                        asyncio.run(
                            message_handler.process_user_input(f"/file {file_path}")
                        )

                # Process task with retry logic for schema validation
                max_attempts = 4
                attempt = 0
                response = None

                asyncio.run(message_handler.process_user_input(task))

                while attempt < max_attempts:
                    attempt += 1
                    response, _, _ = asyncio.run(
                        message_handler.get_assistant_response()
                    )
                    if not output_schema or not schema_dict:
                        break  # No schema validation needed

                    if response is None:
                        asyncio.run(
                            message_handler.process_user_input(
                                "No response was generated. Please try again."
                            )
                        )
                        continue  # No response, retry

                    success, retry_message = self._validate_response_against_schema(
                        response, schema_dict
                    )
                    if success:
                        break  # Valid response
                    else:
                        if retry_message:
                            asyncio.run(
                                message_handler.process_user_input(retry_message)
                            )

                MCPSessionManager.get_instance().cleanup()
                return response.strip() if response else ""
            else:
                raise ValueError(f"Agent '{agent}' not found")

        except Exception:
            import traceback

            print(traceback.format_exc())
            raise

    def login(self) -> bool:
        """
        Authenticate with GitHub Copilot and save the API key to config.

        Returns:
            True if authentication succeeded, False otherwise
        """
        try:
            click.echo("🔐 Starting GitHub Copilot authentication...")

            # Step 1: Request device code
            resp = requests.post(
                "https://github.com/login/device/code",
                headers={
                    "accept": "application/json",
                    "editor-version": "vscode/1.100.3",
                    "editor-plugin-version": "GitHub.copilot/1.330.0",
                    "content-type": "application/json",
                    "user-agent": "GithubCopilot/1.330.0",
                    "accept-encoding": "gzip,deflate,br",
                },
                data='{"client_id":"Iv1.b507a08c87ecfe98","scope":"read:user"}',
            )

            if resp.status_code != 200:
                click.echo(
                    f"❌ Failed to get device code: {resp.status_code}", err=True
                )
                return False

            # Parse the response json, isolating the device_code, user_code, and verification_uri
            resp_json = resp.json()
            device_code = resp_json.get("device_code")
            user_code = resp_json.get("user_code")
            verification_uri = resp_json.get("verification_uri")

            if not all([device_code, user_code, verification_uri]):
                click.echo("❌ Invalid response from GitHub", err=True)
                return False

            # Print the user code and verification uri
            click.echo(
                f"📋 Please visit {verification_uri} and enter code: {user_code}"
            )
            click.echo("⏳ Waiting for authentication...")

            webbrowser.open(verification_uri)

            # Step 2: Poll for access token
            while True:
                time.sleep(5)

                resp = requests.post(
                    "https://github.com/login/oauth/access_token",
                    headers={
                        "accept": "application/json",
                        "editor-version": "vscode/1.100.3",
                        "editor-plugin-version": "GitHub.copilot/1.330.0",
                        "content-type": "application/json",
                        "user-agent": "GithubCopilot/1.330.0",
                        "accept-encoding": "gzip,deflate,br",
                    },
                    data=f'{{"client_id":"Iv1.b507a08c87ecfe98","device_code":"{device_code}","grant_type":"urn:ietf:params:oauth:grant-type:device_code"}}',
                )

                # Parse the response json
                resp_json = resp.json()
                access_token = resp_json.get("access_token")
                error = resp_json.get("error")

                if access_token:
                    click.echo("✅ Authentication successful!")
                    break
                elif error == "authorization_pending":
                    continue  # Keep polling
                elif error == "slow_down":
                    time.sleep(5)  # Additional delay
                    continue
                elif error == "expired_token":
                    click.echo("❌ Authentication expired. Please try again.", err=True)
                    return False
                elif error == "access_denied":
                    click.echo("❌ Authentication denied by user.", err=True)
                    return False
                else:
                    click.echo(f"❌ Authentication error: {error}", err=True)
                    return False

            # Step 3: Save the token to config
            global_config = self.config_manager.read_global_config_data()

            # Ensure api_keys section exists
            if "api_keys" not in global_config:
                global_config["api_keys"] = {}

            # Save the token
            global_config["api_keys"]["GITHUB_COPILOT_API_KEY"] = access_token
            self.config_manager.write_global_config_data(global_config)

            click.echo("💾 GitHub Copilot API key saved to config file!")
            click.echo(
                "🚀 You can now use GitHub Copilot with --provider github_copilot"
            )
            return True

        except ImportError:
            click.echo(
                "❌ Error: 'requests' package is required for authentication", err=True
            )
            click.echo("Install it with: pip install requests")
            return False
        except Exception as e:
            click.echo(f"❌ Authentication failed: {str(e)}", err=True)
            return False
