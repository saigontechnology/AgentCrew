from __future__ import annotations

import functools
import json
import os
import time
import webbrowser
from typing import TYPE_CHECKING, Any

import click
import requests
from loguru import logger

from AgentCrew.modules.agents.example import (
    DEFAULT_DESCRIPTION,
    DEFAULT_NAME,
    DEFAULT_PROMPT,
)
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.llm.model_selection import (
    ModelSelection,
    ModelSelectionSource,
    RuntimeModelInput,
    resolve_model_selection,
)
from AgentCrew.modules.llm.reasoning_selection import (
    REASONING_LEVELS,
    apply_reasoning_to_service,
    default_reasoning_for_service,
    resolve_reasoning_selection,
    validate_reason_effort,
)

if TYPE_CHECKING:
    from AgentCrew.modules.agents import AgentManager
    from AgentCrew.modules.config import ConfigManagement

PROVIDER_LIST = [
    "claude",
    "openai",
    "openai_codex",
    "google",
    "crofai",
    "deepinfra",
    "together",
    "opencode_go",
    "commandcode",
    "fireworks",
    "github_copilot",
]


def common_options(func):
    @click.option(
        "--provider",
        type=click.Choice(PROVIDER_LIST),
        default=None,
        help="LLM provider to use (claude, openai, google, crofai, github_copilot, deepinfra, together, opencode_go, commandcode, or openai_codex)",
    )
    @click.option(
        "--agent-config",
        default=None,
        help="Path/URL to the agent configuration file.",
    )
    @click.option(
        "--mcp-config", default=None, help="Path to the mcp servers configuration file."
    )
    @click.option(
        "--memory-llm",
        type=click.Choice(
            [
                "claude",
                "openai",
                "openai_codex",
                "google",
                "crofai",
                "deepinfra",
                "together",
                "opencode_go",
                "commandcode",
                "github_copilot",
                "copilot_response",
            ]
        ),
        default=None,
        help="LLM Model use for analyzing and processing memory",
    )
    @click.option(
        "--memory-path", default=None, help="Path to the memory database location"
    )
    @click.option(
        "--trusted-project-plugins",
        is_flag=True,
        default=False,
        help="Enable project plugins from .agentcrew/plugins/ (disabled by default)",
    )
    @click.option(
        "--reason-effort",
        type=click.Choice(list(REASONING_LEVELS)),
        default=None,
        help="Reasoning effort level (none, minimal, low, medium, high)",
    )
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class ApplicationSetup:
    def __init__(
        self,
        config_manager: ConfigManagement | None = None,
        trusted_project_plugins: bool | None = None,
    ):
        from AgentCrew.modules.config import ConfigManagement
        from AgentCrew.modules.events import PluginManager

        self.config_manager = config_manager or ConfigManagement()
        self.services: dict[str, Any] | None = None
        self.agent_manager: AgentManager | None = None

        # Read config early (before setup_services) so PluginManager can
        # respect the user's trust decision for project plugins.
        # CLI --trusted-project-plugins flag overrides config.json value.
        if trusted_project_plugins is None:
            _global_config = GlobalConfig().read()
            _trusted = _global_config.get("global_settings", {}).get(
                "trusted_project_plugins", False
            )
        else:
            _trusted = trusted_project_plugins

        # Initialize events infrastructure
        self.plugin_manager = PluginManager(trusted_project_plugins=_trusted)
        self._plugins_initialized = False

    async def initialize_plugins(self) -> None:
        """Discover and load all plugins transactionally."""
        if self._plugins_initialized:
            return
        try:
            await self.plugin_manager.load_all()
        except BaseException:
            try:
                await self.plugin_manager.unload_all()
            except BaseException:
                logger.exception(
                    "Failed to roll back plugins after initialization error"
                )
            finally:
                self._plugins_initialized = False
            raise
        self._plugins_initialized = True

    async def shutdown_plugins(self) -> None:
        """Unload active plugins idempotently, including partial startup state."""
        try:
            await self.plugin_manager.unload_all()
        finally:
            self._plugins_initialized = False

    async def shutdown(self) -> None:
        """Release application-owned plugin, remote-agent, and dedicated LLM resources."""
        try:
            await self.shutdown_plugins()
        finally:
            if self.agent_manager is not None:
                try:
                    await self.agent_manager.close_all_remote_agents()
                finally:
                    await self._close_dedicated_llm_services()
            else:
                from AgentCrew.modules.llm.service_manager import ServiceManager

                await ServiceManager.get_instance().drain_pending_closes()

    async def _close_dedicated_llm_services(self) -> None:
        """Close all dedicated LocalAgent LLM services, deduplicated by identity.

        Individual close failures are logged by ServiceManager and do not stop
        the remaining agents from being cleaned up. Cached ServiceManager
        services are intentionally left open (the manager owns them).
        """
        from AgentCrew.modules.agents import LocalAgent
        from AgentCrew.modules.llm.service_manager import ServiceManager

        llm_manager = ServiceManager.get_instance()
        seen: set[int] = set()
        agents = getattr(self.agent_manager, "agents", None)
        if agents:
            for agent in agents.values():
                if not isinstance(agent, LocalAgent) or agent.llm is None:
                    continue
                if id(agent.llm) in seen:
                    continue
                seen.add(id(agent.llm))
                llm_manager.close_service(agent.llm)
        # Drain any scheduled async close tasks so clients are released before
        # the process exits.
        await llm_manager.drain_pending_closes()

    def load_api_keys_from_config(self) -> None:
        config_file_path = os.getenv("AGENTCREW_CONFIG_PATH")
        if not config_file_path:
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
                            f"\u26a0\ufe0f  API keys in {config_file_path} are not in the expected format.",
                            err=True,
                        )
            except json.JSONDecodeError:
                click.echo(
                    f"\u26a0\ufe0f  Error decoding API keys from {config_file_path}.",
                    err=True,
                )
            except Exception as e:
                click.echo(
                    f"\u26a0\ufe0f  Could not load API keys from {config_file_path}: {e}",
                    err=True,
                )

        keys_to_check = [
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "CROFAI_API_KEY",
            "OPENAI_API_KEY",
            "DEEPINFRA_API_KEY",
            "TOGETHER_API_KEY",
            "OPENCODE_API_KEY",
            "COMMAND_CODE_API_KEY",
            "GITHUB_COPILOT_API_KEY",
            "FIREWORKS_API_KEY",
            "TAVILY_API_KEY",
            "VOYAGE_API_KEY",
            "ELEVENLABS_API_KEY",
        ]

        for key_name in keys_to_check:
            if api_keys_config.get(key_name):
                os.environ[key_name] = str(api_keys_config[key_name]).strip()

    def detect_provider(self) -> str | None:
        env_provider = os.getenv("AGENTCREW_PROVIDER")
        if env_provider:
            if env_provider in PROVIDER_LIST:
                return env_provider
            custom_providers = GlobalConfig().read_custom_llm_providers_config()
            if any(p["name"] == env_provider for p in custom_providers):
                return env_provider

        try:
            last_provider = GlobalConfig().get_last_used_provider()
            if last_provider:
                if last_provider in PROVIDER_LIST:
                    api_key_map = {
                        "claude": "ANTHROPIC_API_KEY",
                        "google": "GEMINI_API_KEY",
                        "crofai": "CROFAI_API_KEY",
                        "openai": "OPENAI_API_KEY",
                        "deepinfra": "DEEPINFRA_API_KEY",
                        "together": "TOGETHER_API_KEY",
                        "opencode_go": "OPENCODE_API_KEY",
                        "commandcode": "COMMAND_CODE_API_KEY",
                        "github_copilot": "GITHUB_COPILOT_API_KEY",
                        "fireworks": "FIREWORKS_API_KEY",
                    }
                    if last_provider == "openai_codex":
                        from AgentCrew.modules.openai_codex.oauth import (
                            OpenAICodexOAuth,
                        )

                        if OpenAICodexOAuth().has_valid_tokens:
                            return last_provider
                    elif os.getenv(api_key_map.get(last_provider, "")):
                        return last_provider
                else:
                    custom_providers = GlobalConfig().read_custom_llm_providers_config()
                    if any(p["name"] == last_provider for p in custom_providers):
                        return last_provider
        except Exception as e:
            logger.warning(f"Could not restore last used provider: {e}")
            click.echo(f"\u26a0\ufe0f  Could not restore last used provider: {e}")

        if os.getenv("GITHUB_COPILOT_API_KEY"):
            return "github_copilot"
        elif os.getenv("ANTHROPIC_API_KEY"):
            return "claude"
        elif os.getenv("GEMINI_API_KEY"):
            return "google"
        elif os.getenv("OPENAI_API_KEY"):
            return "openai"
        elif os.getenv("DEEPINFRA_API_KEY"):
            return "deepinfra"
        elif os.getenv("TOGETHER_API_KEY"):
            return "together"
        elif os.getenv("OPENCODE_API_KEY"):
            return "opencode_go"
        elif os.getenv("CROFAI_API_KEY"):
            return "crofai"
        elif os.getenv("COMMAND_CODE_API_KEY"):
            return "commandcode"
        elif os.getenv("FIREWORKS_API_KEY"):
            return "fireworks"
        else:
            custom_providers = GlobalConfig().read_custom_llm_providers_config()
            if len(custom_providers) > 0:
                return custom_providers[0]["name"]

        return None

    def detect_model_id(self) -> str | None:
        return os.getenv("AGENTCREW_MODEL_ID")

    def resolve_runtime_model(
        self, provider: str | None, model_id: str | None
    ) -> RuntimeModelInput:
        """Fill in missing provider/model values via detection.

        Captures explicitness before detection so auto-detected values
        (environment, persisted last-used) never masquerade as explicit
        runtime arguments in the precedence chain.
        """
        explicit_provider = provider is not None
        explicit_model = model_id is not None
        if provider is None:
            provider = self.detect_provider()
        detected_model_id = None
        if not model_id:
            detected_model_id = self.detect_model_id()
        return RuntimeModelInput(
            provider=provider,
            explicit_provider=explicit_provider,
            explicit_model_id=model_id if explicit_model else None,
            detected_model_id=detected_model_id,
        )

    def setup_services(
        self,
        runtime: RuntimeModelInput,
        reason_effort: str | None = None,
        memory_llm: str | None = None,
        need_memory: bool = True,
        with_voice: bool = False,
    ) -> dict[str, Any]:
        from AgentCrew.modules.llm.model_registry import ModelRegistry
        from AgentCrew.modules.llm.service_manager import ServiceManager

        registry = ModelRegistry.get_instance()
        llm_manager = ServiceManager.get_instance()

        provider = runtime.provider
        if provider is None:
            raise ValueError("Provider must be resolved before setting up services")

        try:
            last_full_qualified_model = GlobalConfig().get_last_used_model()
            last_provider = GlobalConfig().get_last_used_provider()
        except Exception as e:
            logger.warning(f"Could not restore last used model: {e}")
            click.echo(f"\u26a0\ufe0f  Could not restore last used model: {e}")
            last_full_qualified_model = None
            last_provider = None

        selection = resolve_model_selection(
            runtime,
            agent_model_id=None,
            last_used_model=last_full_qualified_model,
            last_used_provider=last_provider,
        )
        if selection.source is ModelSelectionSource.LAST_USED:
            try:
                llm_service = llm_manager.get_service_for_selection(selection)
            except Exception as e:
                logger.warning(f"Could not restore last used model: {e}")
                click.echo(f"\u26a0\ufe0f  Could not restore last used model: {e}")
                llm_service = None
        else:
            llm_service = llm_manager.get_service_for_selection(selection)

        if llm_service is None:
            llm_service = llm_manager.get_service_for_selection(
                ModelSelection(
                    provider=provider,
                    model_id=None,
                    source=ModelSelectionSource.DEFAULT,
                )
            )

        # Apply base reasoning: explicit CLI effort > selected model default.
        # Lenient here (never blocks startup); per-agent reasoning is applied
        # with explicit validation in setup_agents.
        base_reasoning = resolve_reasoning_selection(
            validate_reason_effort(reason_effort),
            None,
            default_reasoning_for_service(llm_service),
        )
        apply_reasoning_to_service(llm_service, base_reasoning.level, explicit=False)

        memory_service = None
        context_service = None
        if need_memory:
            from AgentCrew.modules.memory.chroma_service import ChromaMemoryService
            from AgentCrew.modules.memory.context_persistent import (
                ContextPersistenceService,
            )

            memory_provider = memory_llm or provider
            memory_llm_svc = llm_manager.initialize_standalone_service(memory_provider)

            if runtime.model_id:
                model = registry.get_model(f"{provider}/{runtime.model_id}")
                if model:
                    memory_llm_svc.model = model.id
                    logger.info(f"Set default model '{model.id}' for memory service")
            else:
                memory_llm_svc.model = llm_service.model

            memory_service = ChromaMemoryService(llm_service=memory_llm_svc)

            context_service = ContextPersistenceService()

        from AgentCrew.modules.clipboard import ClipboardService

        clipboard_service = ClipboardService()

        try:
            from AgentCrew.modules.web_search import TavilySearchService

            search_service = TavilySearchService()
        except ValueError as e:
            click.echo(
                f"\u26a0\ufe0f Web search tools not available: {e!s}\n"
                "   Without web search, agents cannot look up current information, recent events,\n"
                "   documentation, or real-time data from the internet.\n"
                "   \U0001f4a1 Get a free Tavily API key (no credit card, 1,000 calls/month) at:\n"
                "   https://app.tavily.com/\n"
                "   Then set it via the TAVILY_API_KEY environment variable or config.json."
            )
            search_service = None
        except Exception as e:
            click.echo(f"\u26a0\ufe0f Web search tools not available: {e!s}")
            search_service = None

        try:
            from AgentCrew.modules.code_analysis import CodeAnalysisService

            code_analysis_llm = llm_manager.initialize_standalone_service(provider)

            if runtime.model_id:
                model = registry.get_model(f"{provider}/{runtime.model_id}")
                if model:
                    code_analysis_llm.model = model.id
                    logger.info(
                        f"Set default model '{model.id}' for code analysis service"
                    )
            else:
                code_analysis_llm.model = llm_service.model

            code_analysis_service = CodeAnalysisService(llm_service=code_analysis_llm)
        except Exception as e:
            click.echo(f"\u26a0\ufe0f Code analysis tool not available: {e!s}")
            code_analysis_service = None

        try:
            from AgentCrew.modules.browser_automation import BrowserAutomationService

            browser_automation_service = BrowserAutomationService()
        except Exception as e:
            click.echo(f"\u26a0\ufe0f Browser automation service not available: {e!s}")
            browser_automation_service = None

        try:
            from AgentCrew.modules.file_editing import FileEditingService

            file_editing_service = FileEditingService()
        except Exception as e:
            click.echo(f"\u26a0\ufe0f File editing service not available: {e!s}")
            file_editing_service = None

        try:
            from AgentCrew.modules.command_execution import CommandExecutionService

            command_execution_service = CommandExecutionService.get_instance()
        except Exception as e:
            click.echo(f"\u26a0\ufe0f Command execution service not available: {e!s}")
            command_execution_service = None

        try:
            from AgentCrew.modules.skills import SkillsService

            skills_service = SkillsService()
        except Exception as e:
            click.echo(f"\u26a0\ufe0f Skills service not available: {e!s}")
            skills_service = None

        try:
            from AgentCrew.modules.image_generation import ImageGenerationService

            image_generation_service = ImageGenerationService()
            # Defer provider SDK imports — quick check using env vars and file existence
            _codex_auth_path = os.path.expanduser("~/.codex/auth.json")
            if not any(
                (
                    os.getenv("OPENAI_API_KEY"),
                    os.getenv("GEMINI_API_KEY"),
                    os.getenv("DEEPINFRA_API_KEY"),
                    os.path.isfile(_codex_auth_path),
                )
            ):
                click.echo(
                    "⚠️  Image generation tool not available:"
                    " No image generation provider found.\n"
                    "   Set OPENAI_API_KEY, GEMINI_API_KEY,"
                    " or DEEPINFRA_API_KEY to enable.\n"
                    "   Or run 'agentcrew chatgpt-auth' to use your"
                    " ChatGPT subscription for OpenAI gpt-image-2."
                )
                image_generation_service = None
        except Exception as e:
            click.echo(f"⚠️ Image generation service not available: {e!s}")
            image_generation_service = None

        voice_service = None
        if with_voice:
            try:
                from AgentCrew.modules.voice import AUDIO_AVAILABLE

                if AUDIO_AVAILABLE and os.getenv("ELEVENLABS_API_KEY"):
                    from AgentCrew.modules.voice.elevenlabs_service import (
                        ElevenLabsVoiceService,
                    )

                    voice_service = ElevenLabsVoiceService()
                elif AUDIO_AVAILABLE and os.getenv("DEEPINFRA_API_KEY"):
                    from AgentCrew.modules.voice.deepinfra_service import (
                        DeepInfraVoiceService,
                    )

                    voice_service = DeepInfraVoiceService()
            except Exception as e:
                click.echo(f"\u26a0\ufe0f Voice service not available: {e!s}")

        self.services = {
            "llm": llm_service,
            "memory": memory_service,
            "clipboard": clipboard_service,
            "code_analysis": code_analysis_service,
            "web_search": search_service,
            "context_persistent": context_service,
            "browser": browser_automation_service,
            "file_editing": file_editing_service,
            "command_execution": command_execution_service,
            "skills": skills_service,
            "voice": voice_service,
            "image_generation": image_generation_service,
        }

        from AgentCrew.modules.agents.manager import AgentManager, AgentMode

        self.agent_manager = AgentManager.get_instance()
        self.services["agent_manager"] = self.agent_manager

        global_config = GlobalConfig().read()
        self.agent_manager.context_shrink_enabled = global_config.get(
            "global_settings", {}
        ).get("auto_context_shrink", True)
        self.agent_manager.shrink_excluded_list = global_config.get(
            "global_settings", {}
        ).get("shrink_excluded", [])

        agent_mode_str = global_config.get("global_settings", {}).get(
            "agent_mode", "transfer"
        )
        try:
            self.agent_manager.agent_mode = AgentMode(agent_mode_str)
        except ValueError:
            self.agent_manager.agent_mode = AgentMode.TRANSFER

        return self.services

    def setup_agents(
        self,
        services: dict[str, Any],
        config_uri: str | None = None,
        use_standalone_provider: str | None = None,
        runtime_model: RuntimeModelInput | None = None,
        reason_effort: str | None = None,
    ) -> AgentManager:
        from AgentCrew.modules.agents import AgentManager, LocalAgent
        from AgentCrew.modules.llm.service_manager import ServiceManager

        if self.agent_manager is None:
            raise ValueError("Agent manager is not initialized")
        llm_manager = ServiceManager.get_instance()

        if runtime_model is None:
            raise ValueError("Runtime model input is required to set up agents")
        provider = runtime_model.provider
        if provider is None:
            raise ValueError("Provider must be resolved before setting up agents")

        try:
            last_used_model = GlobalConfig().get_last_used_model()
            last_used_provider = GlobalConfig().get_last_used_provider()
        except Exception as e:
            logger.warning(f"Could not read last used model: {e}")
            last_used_model = None
            last_used_provider = None

        if config_uri:
            config_uri = os.path.expanduser(config_uri)
            os.environ["SW_AGENTS_CONFIG"] = config_uri
        else:
            config_uri = os.getenv("SW_AGENTS_CONFIG")
            if not config_uri:
                config_uri = "./agents.toml"
            config_uri = os.path.expanduser(config_uri)

        agent_definitions: list = []
        try:
            agent_definitions = AgentManager.load_agents_from_config(config_uri)
        except FileNotFoundError:
            pass

        if not agent_definitions and not use_standalone_provider:
            from AgentCrew.modules.onboarding import OnboardingService

            onboarding = OnboardingService(services["llm"], services=services)
            if onboarding.should_run(config_uri) and onboarding.run():
                agent_definitions = AgentManager.load_agents_from_config(config_uri)

        if not agent_definitions:
            click.echo(
                f"Agent configuration not found at {config_uri}. Creating default configuration."
            )
            config_dir = os.path.dirname(config_uri)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)

            escaped_default_prompt = DEFAULT_PROMPT.replace('"""', '\\"\\"\\"')
            default_config = f'''
[[agents]]
name = "{DEFAULT_NAME}"
description = "{DEFAULT_DESCRIPTION}"
system_prompt = """{escaped_default_prompt}"""
tools = ["memory", "browser", "web_search", "code_analysis"]
'''

            with open(config_uri, "w+", encoding="utf-8") as f:
                f.write(default_config)

            click.echo(f"Created default agent configuration at {config_uri}")
            agent_definitions = AgentManager.load_agents_from_config(config_uri)

        base_selection = resolve_model_selection(
            runtime_model,
            agent_model_id=None,
            last_used_model=last_used_model,
            last_used_provider=last_used_provider,
        )

        for agent_def in agent_definitions:
            if agent_def.get("base_url", ""):
                from AgentCrew.modules.agents.remote_agent import RemoteAgent

                try:
                    agent = RemoteAgent(
                        agent_def["name"],
                        agent_def.get("base_url"),
                        headers=agent_def.get("headers", {}),
                    )
                except Exception as e:
                    logger.warning(
                        f"Cannot connect to remote agent '{agent_def['name']}', skipping: {e}"
                    )
                    click.echo(
                        f"⚠️  Cannot connect to remote agent '{agent_def['name']}', skipping.",
                        err=True,
                    )
                    continue
            else:
                selection = resolve_model_selection(
                    runtime_model,
                    agent_model_id=agent_def.get("model_id"),
                    last_used_model=last_used_model,
                    last_used_provider=last_used_provider,
                )
                # Every local agent owns a dedicated uncached LLM service so
                # per-agent reasoning (CLI/config//think) never leaks to another
                # agent sharing a cached service instance.
                if selection.source is ModelSelectionSource.AGENT_CONFIG:
                    agent_llm = AgentManager.resolve_llm_service_from_config(agent_def)
                    if agent_llm is None:
                        agent_llm = llm_manager.get_service_for_selection(
                            base_selection, standalone=True
                        )
                        selection = base_selection
                else:
                    agent_llm = llm_manager.get_service_for_selection(
                        selection, standalone=True
                    )

                reasoning = resolve_reasoning_selection(
                    validate_reason_effort(reason_effort),
                    validate_reason_effort(agent_def.get("reason_effort")),
                    default_reasoning_for_service(agent_llm),
                )
                apply_reasoning_to_service(
                    agent_llm, reasoning.level, explicit=reasoning.is_explicit
                )

                agent = LocalAgent(
                    name=agent_def["name"],
                    description=agent_def["description"],
                    llm_service=agent_llm,
                    services=services,
                    tools=agent_def["tools"],
                    temperature=agent_def.get("temperature", None),
                    voice_enabled=(
                        "enabled"
                        if agent_def.get("voice_enabled", "disabled")
                        in (True, "enabled", "full", "partial")
                        else "disabled"
                    ),
                    voice_id=agent_def.get("voice_id", None),
                )
                agent.set_system_prompt(agent_def["system_prompt"])
                agent.model_selection = selection
                agent.reasoning_selection = reasoning
                if use_standalone_provider:
                    agent.set_custom_system_prompt(
                        self.agent_manager.get_remote_system_prompt()
                    )
                    agent.is_remoting_mode = True
            self.agent_manager.register_agent(agent)

        from AgentCrew.modules.mcpclient.tool import register as mcp_register

        mcp_register()

        if use_standalone_provider:
            for agent in self.agent_manager.agents.values():
                agent.activate()

        return self.agent_manager

    def restore_last_agent(self) -> None:
        initial_agent_selected = False
        try:
            last_agent = GlobalConfig().get_last_used_agent()

            if (
                last_agent
                and self.agent_manager
                and last_agent in self.agent_manager.agents
            ) and self.agent_manager.select_agent(last_agent):
                initial_agent_selected = True
        except Exception as e:
            click.echo(f"\u26a0\ufe0f  Could not restore last used agent: {e}")

        if not initial_agent_selected and self.agent_manager:
            first_agent_name = next(iter(self.agent_manager.agents.keys()))
            if not self.agent_manager.select_agent(first_agent_name):
                available_agents = ", ".join(self.agent_manager.agents.keys())
                click.echo(
                    f"\u26a0\ufe0f Unknown agent: {first_agent_name}. Using default agent. Available agents: {available_agents}"
                )

    @staticmethod
    def login() -> bool:
        try:
            click.echo("\U0001f510 Starting GitHub Copilot authentication...")

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
                    f"\u274c Failed to get device code: {resp.status_code}", err=True
                )
                return False

            resp_json = resp.json()
            device_code = resp_json.get("device_code")
            user_code = resp_json.get("user_code")
            verification_uri = resp_json.get("verification_uri")

            if not all([device_code, user_code, verification_uri]):
                click.echo("\u274c Invalid response from GitHub", err=True)
                return False

            click.echo(
                f"\U0001f4cb Please visit {verification_uri} and enter code: {user_code}"
            )
            click.echo("\u23f3 Waiting for authentication...")

            webbrowser.open(verification_uri)

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

                resp_json = resp.json()
                access_token = resp_json.get("access_token")
                error = resp_json.get("error")

                if access_token:
                    click.echo("\u2705 Authentication successful!")
                    break
                elif error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    time.sleep(5)
                    continue
                elif error == "expired_token":
                    click.echo(
                        "\u274c Authentication expired. Please try again.", err=True
                    )
                    return False
                elif error == "access_denied":
                    click.echo("\u274c Authentication denied by user.", err=True)
                    return False
                else:
                    click.echo(f"\u274c Authentication error: {error}", err=True)
                    return False

            global_config = GlobalConfig().read()

            if "api_keys" not in global_config:
                global_config["api_keys"] = {}

            global_config["api_keys"]["GITHUB_COPILOT_API_KEY"] = access_token
            GlobalConfig().write(global_config)

            click.echo("\U0001f4be GitHub Copilot API key saved to config file!")
            click.echo(
                "\U0001f680 You can now use GitHub Copilot with --provider github_copilot"
            )
            return True

        except ImportError:
            click.echo(
                "\u274c Error: 'requests' package is required for authentication",
                err=True,
            )
            click.echo("Install it with: pip install requests")
            return False
        except Exception as e:
            click.echo(f"\u274c Authentication failed: {e!s}", err=True)
            return False

    @staticmethod
    def chatgpt_login() -> bool:
        try:
            click.echo("\U0001f510 Starting ChatGPT subscription authentication...")
            click.echo(
                "This will open your browser to sign in with your ChatGPT account."
            )
            click.echo("Your subscription (Plus/Pro) will be used for API access.\n")

            from AgentCrew.modules.openai_codex.oauth import OpenAICodexOAuth

            oauth = OpenAICodexOAuth()
            if oauth.login():
                click.echo("\u2705 ChatGPT authentication successful!")
                click.echo(f"\U0001f4be OAuth tokens saved to {oauth.token_path}")
                click.echo(
                    "\U0001f680 You can now use ChatGPT with --provider openai_codex"
                )
                return True
            else:
                click.echo("\u274c ChatGPT authentication failed.", err=True)
                return False
        except Exception as e:
            click.echo(f"\u274c ChatGPT authentication failed: {e!s}", err=True)
            return False
