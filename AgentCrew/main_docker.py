import nest_asyncio
from AgentCrew.modules.config import ConfigManagement
import click
import importlib
import os
import sys
import traceback
import json
import requests
import time
from AgentCrew.modules.console import ConsoleUI
from AgentCrew.modules.chat import MessageHandler
from AgentCrew.modules.web_search import TavilySearchService
from AgentCrew.modules.clipboard import ClipboardService
from AgentCrew.modules.memory import (
    ChromaMemoryService,
    ContextPersistenceService,
)
from AgentCrew.modules.code_analysis import CodeAnalysisService
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.agents import AgentManager, LocalAgent, RemoteAgent
from AgentCrew.modules.image_generation import ImageGenerationService

nest_asyncio.apply()


@click.group()
def cli():
    """SwissKnife - AI Assistant and Agent Framework"""
    pass


def cli_prod():
    from AgentCrew.modules import FileLogIO

    sys.stderr = FileLogIO()

    os.environ["AGENTCREW_LOG_PATH"] = os.path.expanduser("~/.AgentCrew/logs")
    os.environ["MEMORYDB_PATH"] = os.path.expanduser("~/.AgentCrew/memorydb")
    os.environ["MCP_CONFIG_PATH"] = os.path.expanduser("~/.AgentCrew/mcp_servers.json")
    os.environ["SW_AGENTS_CONFIG"] = os.path.expanduser("~/.AgentCrew/agents.toml")
    os.environ["PERSISTENCE_DIR"] = os.path.expanduser("~/.AgentCrew/persistents")
    os.environ["AGENTCREW_CONFIG_PATH"] = os.path.expanduser("~/.AgentCrew/config.json")

    cli()  # Delegate to main CLI function


def load_api_keys_from_config():
    """Loads API keys from the global config file and sets them as environment variables,
    prioritizing them over any existing environment variables."""

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
            click.echo(f"⚠️  Error decoding API keys from {config_file_path}.", err=True)
        except Exception as e:
            click.echo(
                f"⚠️  Could not load API keys from {config_file_path}: {e}", err=True
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
    ]

    for key_name in keys_to_check:
        if key_name in api_keys_config and api_keys_config[key_name]:
            # Prioritize config file over existing environment variables
            os.environ[key_name] = str(api_keys_config[key_name]).strip()


def setup_services(provider, memory_llm=None):
    # Initialize the model registry and service manager
    registry = ModelRegistry.get_instance()
    llm_manager = ServiceManager.get_instance()

    # Set the current model based on provider
    models = registry.get_models_by_provider(provider)
    if models:
        # Find default model for this provider
        default_model = next((m for m in models if m.default), models[0])
        registry.set_current_model(f"{default_model.provider}/{default_model.id}")

    # Get the LLM service from the manager
    llm_service = llm_manager.get_service(provider)

    try:
        config_manager = ConfigManagement()
        last_model = config_manager.get_last_used_model()
        last_provider = config_manager.get_last_used_provider()

        # Only restore if the last used provider matches current provider or if no specific provider was requested
        if last_model and last_provider:
            # Check if we should restore the last used model
            should_restore = False
            if provider == last_provider:
                # Same provider, definitely restore
                should_restore = True
            elif provider is None:
                # No specific provider requested, try to restore anyway
                should_restore = True

            last_model_class = registry.get_model(last_model)
            if should_restore and last_model_class:
                llm_service.model = last_model_class.id
    except Exception as e:
        # Don't fail startup if restoration fails
        click.echo(f"⚠️  Could not restore last used model: {e}")

    # Initialize services
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
    # Try to create search service if API key is available
    try:
        search_service = TavilySearchService()
    except Exception as e:
        click.echo(f"⚠️ Web search tools not available: {str(e)}")
        search_service = None

    # Initialize code analysis service
    try:
        code_analysis_service = CodeAnalysisService()
    except Exception as e:
        click.echo(f"⚠️ Code analysis tool not available: {str(e)}")
        code_analysis_service = None

    # Initialize image generation service
    try:
        # Use OpenAI by default if API key is available
        if os.getenv("OPENAI_API_KEY"):
            image_gen_service = ImageGenerationService()
        else:
            image_gen_service = None
            click.echo("⚠️ Image generation service not available: No API keys found.")
    except Exception as e:
        click.echo(f"⚠️ Image generation service not available: {str(e)}")
        image_gen_service = None

    # Clean up old memories (older than 1 month)
    try:
        removed_count = memory_service.cleanup_old_memories(months=1)
        if removed_count > 0:
            click.echo(f"🧹 Cleaned up {removed_count} old conversation memories")
    except Exception as e:
        click.echo(f"⚠️ Memory cleanup failed: {str(e)}")

    # Register all tools with their respective services
    services = {
        "llm": llm_service,
        "memory": memory_service,
        "clipboard": clipboard_service,
        "code_analysis": code_analysis_service,
        "web_search": search_service,
        "context_persistent": context_service,
        "image_generation": image_gen_service,
    }
    return services


def setup_agents(services, config_path, remoting_provider=None, model_id=None):
    """
    Set up the agent system with specialized agents.

    Args:
        services: Dictionary of services
    """
    # Get the singleton instance of agent manager
    agent_manager = AgentManager.get_instance()
    llm_manager = ServiceManager.get_instance()

    # Add agent_manager to services for tool registration
    services["agent_manager"] = agent_manager

    # Get the LLM service
    llm_service = services["llm"]

    # Create specialized agents
    if config_path:
        click.echo("Using command-line argument for agent configuration path.")
        os.environ["SW_AGENTS_CONFIG"] = config_path
    else:
        config_path = os.getenv("SW_AGENTS_CONFIG")
        if not config_path:
            config_path = "./agents.toml"
            # If config path doesn't exist, create a default one
        if not os.path.exists(config_path):
            click.echo(
                f"Agent configuration not found at {config_path}. Creating default configuration."
            )
            os.makedirs(os.path.dirname(config_path), exist_ok=True)

            default_config = """# Default SwissKnife Agent Configuration
[[agents]]
name = "default"
description = "Default assistant agent"
system_prompt = \"\"\"You are a helpful AI assistant. Always provide accurate, helpful, and ethical responses.
Current date: {current_date}
\"\"\"
tools = ["memory", "clipboard", "web_search", "code_analysis"]
"""

            with open(config_path, "w+", encoding="utf-8") as f:
                f.write(default_config)

            click.echo(f"Created default agent configuration at {config_path}")
    # Load agents from configuration
    agent_definitions = AgentManager.load_agents_from_config(config_path)
    first_agent_name = None
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
            if not first_agent_name:
                first_agent_name = agent_def["name"]
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
            )
            agent.set_system_prompt(agent_def["system_prompt"])
            if remoting_provider:
                agent.set_custom_system_prompt(agent_manager.get_remote_system_prompt())
                agent.is_remoting_mode = True
                agent.activate()
        agent_manager.register_agent(agent)

    from AgentCrew.modules.mcpclient.tool import register as mcp_register

    mcp_register()

    if remoting_provider:
        from AgentCrew.modules.mcpclient import MCPSessionManager

        mcp_manager = MCPSessionManager.get_instance()
        mcp_manager.initialize_for_agent()
        return

    initial_agent_selected = False
    try:
        config_manager = ConfigManagement()
        last_agent = config_manager.get_last_used_agent()

        if last_agent and last_agent in agent_manager.agents:
            if agent_manager.select_agent(last_agent):
                initial_agent_selected = True
    except Exception as e:
        # Don't fail startup if restoration fails
        click.echo(f"⚠️  Could not restore last used agent: {e}")

    # Select the initial agent if specified and no agent was restored
    if not initial_agent_selected and first_agent_name:
        if not agent_manager.select_agent(first_agent_name):
            available_agents = ", ".join(agent_manager.agents.keys())
            click.echo(
                f"⚠️ Unknown agent: {first_agent_name}. Using default agent. Available agents: {available_agents}"
            )


def discover_and_register_tools(services=None):
    """
    Discover and register all tools

    DEPRECATED: This function is deprecated and will be removed in a future version.
    Use setup_agents() and register_agent_tools() instead.

    Args:
        services: Dictionary mapping service names to service instances
    """
    import warnings

    warnings.warn(
        "discover_and_register_tools is deprecated. Use setup_agents() and register_agent_tools() instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    if services is None:
        services = {}

    # List of tool modules and their corresponding service keys
    tool_modules = [
        ("modules.memory.tool", "memory"),
        ("modules.code_analysis.tool", "code_analysis"),
        ("modules.clipboard.tool", "clipboard"),
        ("modules.web_search.tool", "web_search"),
        ("modules.coding.tool", "aider"),
        ("modules.image_generation.tool", "image_generation"),
    ]

    for module_name, service_key in tool_modules:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "register"):
                service_instance = services.get(service_key)
                module.register(service_instance)
                # print(f"✅ Registered tools from {module_name}")
        except ImportError as e:
            print(f"⚠️ Error importing tool module {module_name}: {e}")

    from AgentCrew.modules.mcpclient.tool import register as mcp_register

    mcp_register()


@cli.command()
@click.option(
    "--provider",
    type=click.Choice(
        ["claude", "groq", "openai", "google", "deepinfra", "github_copilot"]
    ),
    default=None,
    help="LLM provider to use (claude, groq, openai, google, github_copilot, or deepinfra)",
)
@click.option(
    "--agent-config", default=None, help="Path to the agent configuration file."
)
@click.option(
    "--mcp-config", default=None, help="Path to the mcp servers configuration file."
)
@click.option(
    "--memory-llm",
    type=click.Choice(
        ["claude", "groq", "openai", "google", "deepinfra", "github_copilot"]
    ),
    default=None,
    help="LLM Model use for analyzing and processing memory",
)
def chat(provider, agent_config, mcp_config, memory_llm):
    """Start an interactive chat session with LLM"""
    try:
        load_api_keys_from_config()

        # Only check environment variables if provider wasn't explicitly specified
        if provider is None:
            # NEW: First try to restore last used provider
            try:
                config_manager = ConfigManagement()
                last_provider = config_manager.get_last_used_provider()
                if last_provider:
                    # Verify the provider is still available
                    if last_provider in [
                        "claude",
                        "groq",
                        "openai",
                        "google",
                        "deepinfra",
                        "github_copilot",
                    ]:
                        # Check if API key is available for this provider
                        api_key_map = {
                            "claude": "ANTHROPIC_API_KEY",
                            "google": "GEMINI_API_KEY",
                            "openai": "OPENAI_API_KEY",
                            "groq": "GROQ_API_KEY",
                            "deepinfra": "DEEPINFRA_API_KEY",
                            "github_copilot": "GITHUB_COPILOT_API_KEY",
                        }
                        if os.getenv(api_key_map.get(last_provider, "")):
                            provider = last_provider
                    else:
                        # Check if it's a custom provider
                        custom_providers = (
                            config_manager.read_custom_llm_providers_config()
                        )
                        if any(p["name"] == last_provider for p in custom_providers):
                            provider = last_provider
            except Exception as e:
                click.echo(f"⚠️  Could not restore last used provider: {e}")

            # Fall back to environment variable detection if no provider restored
            if provider is None:
                if os.getenv("GITHUB_COPILOT_API_KEY"):
                    provider = "github_copilot"
                elif os.getenv("ANTHROPIC_API_KEY"):
                    provider = "claude"
                elif os.getenv("GEMINI_API_KEY"):
                    provider = "google"
                elif os.getenv("OPENAI_API_KEY"):
                    provider = "openai"
                elif os.getenv("GROQ_API_KEY"):
                    provider = "groq"
                elif os.getenv("DEEPINFRA_API_KEY"):
                    provider = "deepinfra"
                else:
                    config = ConfigManagement()
                    custom_providers = config.read_custom_llm_providers_config()
                    if len(custom_providers) > 0:
                        # Use the first custom provider as default if no API keys found
                        provider = custom_providers[0]["name"]
                    else:
                        raise ValueError(
                            "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, or DEEPINFRA_API_KEY"
                        )
        services = setup_services(provider, memory_llm)

        if mcp_config:
            os.environ["MCP_CONFIG_PATH"] = mcp_config

        # Set up the agent system
        setup_agents(services, agent_config)

        # Create the message handler
        message_handler = MessageHandler(
            services["memory"], services["context_persistent"]
        )

        ui = ConsoleUI(message_handler)
        ui.start()
    except Exception as e:
        print(traceback.format_exc())
        click.echo(f"❌ Error: {str(e)}", err=True)
    finally:
        from AgentCrew.modules.mcpclient import MCPSessionManager

        MCPSessionManager.get_instance().cleanup()


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind the server to")
@click.option("--port", default=41241, help="Port to bind the server to")
@click.option("--base-url", default=None, help="Base URL for agent endpoints")
@click.option(
    "--provider",
    type=click.Choice(
        ["claude", "groq", "openai", "google", "github_copilot", "deepinfra"]
    ),
    default=None,
    help="LLM provider to use (claude, groq, openai, google, github_copilot or deepinfra)",
)
@click.option("--model-id", default=None, help="Model ID from provider")
@click.option("--agent-config", default=None, help="Path to agent configuration file")
@click.option("--api-key", default=None, help="API key for authentication (optional)")
@click.option(
    "--mcp-config", default=None, help="Path to the mcp servers configuration file."
)
@click.option(
    "--memory-llm",
    type=click.Choice(["claude", "groq", "openai", "google"]),
    default=None,
    help="LLM Model use for analyzing and processing memory",
)
def a2a_server(
    host,
    port,
    base_url,
    provider,
    model_id,
    agent_config,
    api_key,
    mcp_config,
    memory_llm,
):
    """Start an A2A server exposing all SwissKnife agents"""
    try:
        load_api_keys_from_config()

        # Set default base URL if not provided
        if not base_url:
            base_url = f"http://{host}:{port}"

        if provider is None:
            if os.getenv("GITHUB_COPILOT_API_KEY"):
                provider = "github_copilot"
            elif os.getenv("ANTHROPIC_API_KEY"):
                provider = "claude"
            elif os.getenv("GEMINI_API_KEY"):
                provider = "google"
            elif os.getenv("OPENAI_API_KEY"):
                provider = "openai"
            elif os.getenv("GROQ_API_KEY"):
                provider = "groq"
            elif os.getenv("DEEPINFRA_API_KEY"):
                provider = "deepinfra"
            else:
                raise ValueError(
                    "No LLM API key found. Please set either ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, or DEEPINFRA_API_KEY"
                )

        services = setup_services(provider, memory_llm)
        if mcp_config:
            os.environ["MCP_CONFIG_PATH"] = mcp_config

        # Set up agents from configuration
        setup_agents(services, agent_config, provider, model_id)

        # Get agent manager
        agent_manager = AgentManager.get_instance()

        agent_manager.enforce_transfer = False
        # Create and start server
        from AgentCrew.modules.a2a.server import A2AServer

        server = A2AServer(
            agent_manager=agent_manager,
            host=host,
            port=port,
            base_url=base_url,
            api_key=api_key,
        )

        click.echo(f"Starting A2A server on {host}:{port}")
        click.echo(f"Available agents: {', '.join(agent_manager.agents.keys())}")
        server.start()
    except Exception as e:
        print(traceback.format_exc())
        click.echo(f"❌ Error: {str(e)}", err=True)
    finally:
        from AgentCrew.modules.mcpclient import MCPSessionManager

        MCPSessionManager.get_instance().cleanup()


@cli.command()
def copilot_auth():
    """Authenticate with GitHub Copilot and save the API key to config"""
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
            click.echo(f"❌ Failed to get device code: {resp.status_code}", err=True)
            return

        # Parse the response json, isolating the device_code, user_code, and verification_uri
        resp_json = resp.json()
        device_code = resp_json.get("device_code")
        user_code = resp_json.get("user_code")
        verification_uri = resp_json.get("verification_uri")

        if not all([device_code, user_code, verification_uri]):
            click.echo("❌ Invalid response from GitHub", err=True)
            return

        # Print the user code and verification uri
        click.echo(f"📋 Please visit {verification_uri} and enter code: {user_code}")
        click.echo("⏳ Waiting for authentication...")

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
                return
            elif error == "access_denied":
                click.echo("❌ Authentication denied by user.", err=True)
                return
            else:
                click.echo(f"❌ Authentication error: {error}", err=True)
                return

        # Step 3: Save the token to config
        config_manager = ConfigManagement()
        global_config = config_manager.read_global_config_data()

        # Ensure api_keys section exists
        if "api_keys" not in global_config:
            global_config["api_keys"] = {}

        # Save the token
        global_config["api_keys"]["GITHUB_COPILOT_API_KEY"] = access_token
        config_manager.write_global_config_data(global_config)

        click.echo("💾 GitHub Copilot API key saved to config file!")
        click.echo("🚀 You can now use GitHub Copilot with --provider github_copilot")

    except ImportError:
        click.echo(
            "❌ Error: 'requests' package is required for authentication", err=True
        )
        click.echo("Install it with: pip install requests")
    except Exception as e:
        click.echo(f"❌ Authentication failed: {str(e)}", err=True)


if __name__ == "__main__":
    """Check for updates and update AgentCrew if a new version is available"""
    cli()
