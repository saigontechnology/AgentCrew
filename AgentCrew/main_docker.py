import click
import os
import sys


PROVIDER_LIST = [
    "claude",
    "groq",
    "openai",
    "google",
    "deepinfra",
    "github_copilot",
    "copilot_response",
]


@click.group()
def cli():
    """Agentcrew - AI Assistant and Agent Framework"""
    from loguru import logger
    import logging

    formatter = "{time} - {name} - {level} - {message}"
    log_level = os.getenv("AGENTCREW_LOG_LEVEL", "ERROR").upper()
    logger.remove(0)

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.ERROR)

    logger.add(
        sys.stderr,
        level=log_level,
        format=formatter,
    )


def cli_prod():
    if sys.argv[1] == "--version":
        click.echo(f"AgentCrew version: {get_current_version()}")
        exit(0)
    os.environ["AGENTCREW_LOG_PATH"] = os.path.expanduser("~/.AgentCrew/logs")
    os.environ["MEMORYDB_PATH"] = os.path.expanduser("~/.AgentCrew/memorydb")
    os.environ["MCP_CONFIG_PATH"] = os.path.expanduser("~/.AgentCrew/mcp_servers.json")
    os.environ["SW_AGENTS_CONFIG"] = os.path.expanduser("~/.AgentCrew/agents.toml")
    os.environ["AGENTCREW_PERSISTENCE_DIR"] = os.path.expanduser(
        "~/.AgentCrew/persistents"
    )
    os.environ["AGENTCREW_CONFIG_PATH"] = os.path.expanduser("~/.AgentCrew/config.json")
    os.environ["AGENTCREW_ENV"] = os.getenv("AGENTCREW_ENV", "production")
    os.environ["AGENTCREW_LOG_LEVEL"] = os.getenv("AGENTCREW_LOG_LEVEL", "ERROR")
    cli()


def get_current_version():
    """Get the current version of AgentCrew"""
    try:
        import AgentCrew

        if hasattr(AgentCrew, "__version__"):
            return AgentCrew.__version__

        return None
    except Exception:
        return None


@cli.command()
@click.option(
    "--provider",
    type=click.Choice(PROVIDER_LIST),
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
    from AgentCrew.app import AgentCrewApplication

    app = AgentCrewApplication()
    app.run_console(provider, agent_config, mcp_config, memory_llm)


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind the server to")
@click.option("--port", default=41241, help="Port to bind the server to")
@click.option("--base-url", default=None, help="Base URL for agent endpoints")
@click.option(
    "--provider",
    type=click.Choice(PROVIDER_LIST),
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
    from AgentCrew.app import AgentCrewApplication

    app = AgentCrewApplication()
    app.run_server(
        host=host,
        port=port,
        base_url=base_url,
        provider=provider,
        model_id=model_id,
        agent_config=agent_config,
        api_key=api_key,
        mcp_config=mcp_config,
        memory_llm=memory_llm,
    )


@cli.command()
@click.option("--agent", type=str, help="Name of the agent to run")
@click.option(
    "--provider",
    type=click.Choice(PROVIDER_LIST),
    default=None,
    help="LLM provider to use (claude, groq, openai, google, github_copilot or deepinfra)",
)
@click.option("--model-id", default=None, help="Model ID from provider")
@click.option("--agent-config", default=None, help="Path to agent configuration file")
@click.option(
    "--mcp-config", default=None, help="Path to the mcp servers configuration file."
)
@click.option(
    "--memory-llm",
    type=click.Choice(["claude", "groq", "openai", "google"]),
    default=None,
    help="LLM Model use for analyzing and processing memory",
)
@click.option(
    "--output-schema",
    default=None,
    help="JSON schema (file path or JSON string) to enforce structured output format",
)
@click.argument(
    "task",
    nargs=1,
    type=str,
)
@click.argument(
    "files",
    nargs=-1,
    type=click.Path(),
)
def job(
    agent,
    provider,
    model_id,
    agent_config,
    mcp_config,
    memory_llm,
    output_schema,
    task,
    files,
):
    """Run a single job/task with an agent"""
    from AgentCrew.app import AgentCrewApplication

    try:
        app = AgentCrewApplication()
        response = app.run_job(
            agent=agent,
            task=task,
            files=list(files) if files else None,
            provider=provider,
            model_id=model_id,
            agent_config=agent_config,
            mcp_config=mcp_config,
            memory_llm=memory_llm,
            output_schema=output_schema,
        )
        click.echo(response)
    except Exception as e:
        import traceback

        print(traceback.format_exc())
        click.echo(f"❌ Error: {str(e)}", err=True)
        raise SystemExit(1)


@cli.command()
def copilot_auth():
    """Authenticate with GitHub Copilot and save the API key to config"""
    from AgentCrew.app import AgentCrewApplication

    app = AgentCrewApplication()
    app.login()


if __name__ == "__main__":
    """Check for updates and update AgentCrew if a new version is available"""
    cli()
