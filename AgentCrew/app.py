import asyncio
import json
import os
import sys
from typing import Any

import click
from loguru import logger

from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.llm.model_selection import RuntimeModelInput
from AgentCrew.setup import ApplicationSetup


class AgentCrewApplication:
    def __init__(self, trusted_project_plugins: bool | None = None):
        self.config_manager = ConfigManagement()
        self.setup = ApplicationSetup(self.config_manager, trusted_project_plugins)
        self.setup.load_api_keys_from_config()

    @property
    def services(self) -> dict[str, Any] | None:
        return self.setup.services

    @property
    def agent_manager(self):
        return self.setup.agent_manager

    def run_console(
        self,
        provider: str | None = None,
        agent_config: str | None = None,
        mcp_config: str | None = None,
        memory_llm: str | None = None,
        with_voice: bool = False,
        model_id: str | None = None,
    ) -> None:
        import asyncio

        from AgentCrew.modules.chat import MessageHandler
        from AgentCrew.modules.console import ConsoleUI
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            runtime_model = self.setup.resolve_runtime_model(provider, model_id)
            if runtime_model.provider is None:
                raise ValueError(
                    "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, CROFAI_API_KEY, DEEPINFRA_API_KEY, TOGETHER_API_KEY, or OPENCODE_API_KEY"
                )

            services = self.setup.setup_services(
                runtime_model,
                memory_llm=memory_llm,
                with_voice=with_voice,
            )

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            self.setup.setup_agents(
                services,
                agent_config,
                runtime_model=runtime_model,
            )
            self.setup.restore_last_agent()

            # Initialize plugins (non-blocking — errors logged, not fatal)
            try:
                asyncio.run(self.setup.initialize_plugins())
            except Exception:
                logger.exception("Failed to initialize plugins (continuing)")

            message_handler = MessageHandler(
                services["memory"],
                services["context_persistent"],
                with_voice,
                services.get("voice"),
            )
            global_config = GlobalConfig().read()

            ui = ConsoleUI(
                message_handler,
                global_config.get("global_settings", {}).get("swap_enter", False),
            )
            ui.start()
        except Exception as e:
            logger.exception("Failed to run console mode")
            click.echo(f"❌ Error: {e!s}", err=True)
        finally:
            try:
                asyncio.run(self.setup.shutdown())
            except Exception:
                logger.exception("Failed to shut down application resources")
            MCPSessionManager.get_instance().cleanup()

    def run_gui(
        self,
        provider: str | None = None,
        agent_config: str | None = None,
        mcp_config: str | None = None,
        memory_llm: str | None = None,
        with_voice: bool = False,
        model_id: str | None = None,
    ) -> None:
        import asyncio

        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtWidgets import QApplication

        from AgentCrew.modules.chat import MessageHandler
        from AgentCrew.modules.gui import ChatWindow
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            runtime_model = self.setup.resolve_runtime_model(provider, model_id)
            if runtime_model.provider is None:
                from AgentCrew.modules.gui.widgets.config_window import ConfigWindow

                app = QApplication(sys.argv)
                config_window = ConfigWindow()
                config_window.tab_widget.setCurrentIndex(3)
                config_window.show()
                sys.exit(app.exec())

            services = self.setup.setup_services(
                runtime_model,
                memory_llm=memory_llm,
                with_voice=with_voice,
            )

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            self.setup.setup_agents(
                services,
                agent_config,
                runtime_model=runtime_model,
            )
            self.setup.restore_last_agent()

            # Initialize plugins in GUI mode (lazy — errors logged, not fatal)
            try:
                asyncio.run(self.setup.initialize_plugins())
            except Exception:
                logger.exception("Failed to initialize plugins (continuing)")

            message_handler = MessageHandler(
                services["memory"],
                services["context_persistent"],
                with_voice,
                services.get("voice"),
            )

            # Pre-initialize the ChromaDB memory collection on the main thread.
            # On macOS, NumPy's OpenBLAS backend crashes (SIGBUS) when first
            # loaded inside a QThread.  Eagerly initialising the collection here
            # ensures the heavy imports happen on the main thread while still
            # keeping the deferred pattern for the console / A2A codepaths.
            memory_service = services.get("memory")
            if memory_service is not None and hasattr(
                memory_service, "ensure_initialized"
            ):
                try:
                    memory_service.ensure_initialized()
                except Exception as e:
                    logger.warning(
                        f"Failed to pre-initialize memory service on main thread: {e}"
                    )

            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseOpenGLES)
            app = QApplication(sys.argv)
            chat_window = ChatWindow(message_handler)
            chat_window.show()
            sys.exit(app.exec())
        except Exception as e:
            logger.exception("Failed to run GUI mode")
            click.echo(f"❌ Error: {e!s}", err=True)
        finally:
            try:
                asyncio.run(self.setup.shutdown())
            except Exception:
                logger.exception("Failed to shut down application resources")
            MCPSessionManager.get_instance().cleanup()

    def run_server(
        self,
        host: str = "0.0.0.0",
        port: int = 41241,
        base_url: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        agent_config: str | None = None,
        api_key: str | None = None,
        mcp_config: str | None = None,
        memory_llm: str | None = None,
        memory_path: str | None = None,
        store_type: str = "memory",
        store_options: dict | None = None,
    ) -> None:
        from AgentCrew.modules.a2a.server import A2AServer
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            if not base_url:
                base_url = f"http://{host}:{port}"

            runtime_model = self.setup.resolve_runtime_model(provider, model_id)
            if runtime_model.provider is None:
                raise ValueError(
                    "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, CROFAI_API_KEY, DEEPINFRA_API_KEY, TOGETHER_API_KEY, or OPENCODE_API_KEY"
                )

            need_memory = bool(memory_path)
            services = self.setup.setup_services(
                runtime_model,
                memory_llm=memory_llm,
                need_memory=need_memory,
            )

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            os.environ["AGENTCREW_DISABLE_GUI"] = "true"

            self.setup.setup_agents(
                services,
                agent_config,
                use_standalone_provider=runtime_model.provider,
                runtime_model=runtime_model,
            )

            if self.agent_manager is None:
                raise ValueError("Agent manager is not initialized")

            self.agent_manager.enforce_transfer = False

            server = A2AServer(
                agent_manager=self.agent_manager,
                host=host,
                port=port,
                base_url=base_url,
                api_key=api_key,
                store_type=store_type,
                store_options=store_options,
            )

            click.echo(f"Starting A2A server on {host}:{port}")
            click.echo(
                f"Available agents: {', '.join(self.agent_manager.agents.keys())}"
            )
            server.start()
        except Exception as e:
            logger.exception("Failed to run A2A server")
            click.echo(f"❌ Error: {e!s}", err=True)
        finally:
            try:
                asyncio.run(self.setup.shutdown())
            except Exception:
                logger.exception("Failed to shut down application resources")
            MCPSessionManager.get_instance().cleanup()

    def run_acp(
        self,
        provider: str | None = None,
        model_id: str | None = None,
        agent_config: str | None = None,
        mcp_config: str | None = None,
        memory_llm: str | None = None,
        agent: str | None = None,
    ) -> None:
        from AgentCrew.modules.acp import run_acp_agent
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            runtime_model = self.setup.resolve_runtime_model(provider, model_id)
            if runtime_model.provider is None:
                runtime_model = RuntimeModelInput(
                    provider="opencode_go",
                    explicit_provider=False,
                    explicit_model_id=runtime_model.explicit_model_id,
                    detected_model_id=runtime_model.detected_model_id,
                )
                logger.error(
                    "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, CROFAI_API_KEY, DEEPINFRA_API_KEY, TOGETHER_API_KEY, or OPENCODE_API_KEY"
                )

            services = self.setup.setup_services(
                runtime_model,
                memory_llm=memory_llm,
            )

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            os.environ["AGENTCREW_DISABLE_GUI"] = "true"

            self.setup.setup_agents(
                services,
                agent_config,
                runtime_model=runtime_model,
            )

            if self.agent_manager is None:
                raise ValueError("Agent manager is not initialized")

            self.agent_manager.enforce_transfer = False
            if agent:
                self.agent_manager.select_agent(agent)
            else:
                self.setup.restore_last_agent()

            asyncio.run(run_acp_agent(self.agent_manager, agent))
        except Exception as e:
            logger.exception("Failed to run ACP agent")
            click.echo(f"❌ Error: {e!s}", err=True)
        finally:
            try:
                asyncio.run(self.setup.shutdown())
            except Exception:
                logger.exception("Failed to shut down application resources")
            MCPSessionManager.get_instance().cleanup()

    def _parse_output_schema(self, schema_input: str) -> tuple[str, dict]:
        try:
            from AgentCrew.modules.prompts.constants import SCHEMA_ENFORCEMENT_PROMPT

            if os.path.exists(schema_input):
                with open(schema_input, "r", encoding="utf-8") as f:
                    schema_dict = json.load(f)
            else:
                schema_dict = json.loads(schema_input)

            schema_json = json.dumps(schema_dict, indent=2)

            enforcement_prompt = SCHEMA_ENFORCEMENT_PROMPT.replace(
                "{schema_json}", schema_json
            )
            return enforcement_prompt, schema_dict

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON schema: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load output schema: {e}")

    def _clean_json_response(self, response: str) -> str:
        import re

        cleaned = response.strip()
        pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
        match = re.match(pattern, cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
        return cleaned

    def _validate_response_against_schema(
        self, response: str, schema_dict: dict[str, Any]
    ) -> tuple[bool, str | None]:
        from jsonschema import ValidationError, validate

        try:
            cleaned_response = self._clean_json_response(response)
            response_json = json.loads(cleaned_response)
        except json.JSONDecodeError as e:
            return (
                False,
                f"Response is not valid JSON: {e}\n\nResponse received:\n{response[:500]}",
            )

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
        task: str,
        agent: str | None = None,
        files: list[str] | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        agent_config: str | None = None,
        mcp_config: str | None = None,
        memory_llm: str | None = None,
        memory_path: str | None = None,
        output_schema: str | None = None,
        token_usage_file: str | None = None,
    ) -> str:
        from AgentCrew.modules.agents import LocalAgent, run_agent_loop
        from AgentCrew.modules.llm.model_registry import ModelRegistry
        from AgentCrew.modules.mcpclient import MCPSessionManager

        try:
            runtime_model = self.setup.resolve_runtime_model(provider, model_id)
            if runtime_model.provider is None:
                raise ValueError(
                    "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, CROFAI_API_KEY, DEEPINFRA_API_KEY, TOGETHER_API_KEY, or OPENCODE_API_KEY"
                )

            need_memory = bool(memory_path)

            services = self.setup.setup_services(
                runtime_model,
                memory_llm=memory_llm,
                need_memory=need_memory,
            )

            if mcp_config:
                os.environ["MCP_CONFIG_PATH"] = mcp_config

            os.environ["AGENTCREW_DISABLE_GUI"] = "true"

            if self.agent_manager is None:
                raise ValueError("Agent manager is not initialized")

            self.agent_manager.enforce_transfer = False
            self.agent_manager.one_turn_process = True

            if agent:
                self.setup.setup_agents(
                    services,
                    agent_config,
                    runtime_model=runtime_model,
                )
                current_agent = self.agent_manager.get_local_agent(agent)
            else:
                llm_service = services["llm"]
                llm_service.clear_tools()
                current_agent = LocalAgent(
                    name="job",
                    description="Lightweight job agent",
                    llm_service=llm_service,
                    services=services,
                    tools=[],
                )
                current_agent.set_system_prompt("you are a helpful AI assistant.")
                self.agent_manager.register_agent(current_agent)

            if isinstance(current_agent, LocalAgent) and current_agent.llm:
                schema_dict = None
                if output_schema:
                    schema_prompt, schema_dict = self._parse_output_schema(
                        output_schema
                    )
                    if "structured_output" in ModelRegistry.get_model_capabilities(
                        f"{runtime_model.provider}/{runtime_model.model_id}"
                    ):
                        current_agent.llm.structured_output = schema_dict
                    else:
                        current_agent.set_custom_system_prompt(schema_prompt)

                self.agent_manager.select_agent(current_agent.name)

                history: list[dict[str, Any]] = []

                if files:
                    from AgentCrew.modules.utils.file_handler import FileHandler

                    file_handler = FileHandler()
                    all_file_contents: list[dict[str, Any]] = []
                    for file_path in files:
                        file_path = os.path.expanduser(file_path.strip())
                        file_content = file_handler.process_file(file_path)
                        if file_content:
                            all_file_contents.append(file_content)
                    if all_file_contents:
                        history.append(
                            {
                                "role": "user",
                                "content": all_file_contents,
                            }
                        )

                history.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": task}],
                    }
                )

                max_attempts = 4
                attempt = 0
                response = ""
                token_usage = None

                while attempt < max_attempts:
                    attempt += 1
                    response, token_usage = asyncio.run(
                        run_agent_loop(
                            agent=current_agent,
                            history=history,
                        )
                    )
                    if not output_schema or not schema_dict:
                        break

                    if response.strip() == "":
                        history.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "No response was generated. Please try again.",
                                    }
                                ],
                            }
                        )
                        continue

                    success, retry_message = self._validate_response_against_schema(
                        response, schema_dict
                    )
                    if success:
                        break
                    else:
                        if retry_message:
                            history.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": retry_message}
                                    ],
                                }
                            )

                if token_usage_file and token_usage:
                    from dataclasses import asdict

                    token_usage_path = os.path.expanduser(token_usage_file)
                    os.makedirs(os.path.dirname(token_usage_path) or ".", exist_ok=True)
                    with open(token_usage_path, "w") as f:
                        json.dump(asdict(token_usage), f, indent=2)

                if not output_schema or not schema_dict:
                    return response.strip()
                return self._clean_json_response(response).strip() if response else ""
            else:
                raise ValueError(f"Agent '{agent}' not found")

        except Exception:
            logger.exception("Failed to run job")
            raise
        finally:
            try:
                asyncio.run(self.setup.shutdown())
            except Exception:
                logger.exception("Failed to shut down application resources")
            MCPSessionManager.get_instance().cleanup()
