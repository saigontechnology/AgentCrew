import os
import sys

import click

from AgentCrew.app import common_options


def _custom_unraisable_hook(unraisable):
    """Suppress httpcore async cleanup exceptions when streams are cancelled."""
    exc_type = unraisable.exc_type
    exc_value = unraisable.exc_value
    if exc_type and exc_type.__name__ == "AsyncLibraryNotFoundError":
        return
    if exc_value and "httpcore" in str(type(exc_value).__module__):
        return
    sys.__unraisablehook__(unraisable)


sys.unraisablehook = _custom_unraisable_hook


@click.group()
def cli():
    """Agentcrew - AI Assistant and Agent Framework"""
    import logging

    from loguru import logger

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
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        click.echo(f"AgentCrew version: {get_current_version()}")
        exit(0)
    os.environ["AGENTCREW_LOG_PATH"] = os.getenv(
        "AGENTCREW_LOG_PATH", os.path.expanduser("~/.AgentCrew/logs")
    )
    os.environ["MEMORYDB_PATH"] = os.getenv(
        "MEMORYDB_PATH", os.path.expanduser("~/.AgentCrew/memorydb")
    )
    os.environ["MCP_CONFIG_PATH"] = os.getenv(
        "MCP_CONFIG_PATH", os.path.expanduser("~/.AgentCrew/mcp_servers.json")
    )
    os.environ["SW_AGENTS_CONFIG"] = os.getenv(
        "SW_AGENTS_CONFIG", os.path.expanduser("~/.AgentCrew/agents.toml")
    )
    os.environ["AGENTCREW_PERSISTENCE_DIR"] = os.getenv(
        "AGENTCREW_PERSISTENCE_DIR", os.path.expanduser("~/.AgentCrew/persistents")
    )
    os.environ["AGENTCREW_CONFIG_PATH"] = os.getenv(
        "AGENTCREW_CONFIG_PATH", os.path.expanduser("~/.AgentCrew/config.json")
    )
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
@common_options
@click.option("--model-id", default=None, help="Model ID from provider")
def chat(
    provider,
    agent_config,
    mcp_config,
    memory_llm,
    memory_path,
    model_id,
    trusted_project_plugins,
):
    """Start an interactive chat session with LLM"""
    from AgentCrew.app import AgentCrewApplication

    if memory_path:
        os.environ["MEMORYDB_PATH"] = memory_path

    app = AgentCrewApplication(trusted_project_plugins)
    app.run_console(provider, agent_config, mcp_config, memory_llm, model_id=model_id)


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind the server to")
@click.option("--port", default=41241, help="Port to bind the server to")
@click.option("--base-url", default=None, help="Base URL for agent endpoints")
@common_options
@click.option("--model-id", default=None, help="Model ID from provider")
@click.option("--api-key", default=None, help="API key for authentication (optional)")
@click.option(
    "--store-type",
    default="memory",
    type=click.Choice(["memory", "file", "redis"]),
    help="Task store backend: memory, file, or redis",
)
@click.option(
    "--store-option",
    multiple=True,
    help="Store options as key=value pairs (e.g. --store-option base_dir=./data)",
)
def a2a_server(
    host,
    port,
    base_url,
    provider,
    agent_config,
    mcp_config,
    memory_llm,
    memory_path,
    model_id,
    api_key,
    store_type,
    store_option,
    trusted_project_plugins,
):
    """Start an A2A server exposing all SwissKnife agents"""
    from AgentCrew.app import AgentCrewApplication

    if memory_path:
        os.environ["MEMORYDB_PATH"] = memory_path

    store_options = {}
    for opt in store_option:
        if "=" in opt:
            k, v = opt.split("=", 1)
            store_options[k.strip()] = v.strip()

    app = AgentCrewApplication(trusted_project_plugins)
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
        memory_path=memory_path,
        store_type=store_type,
        store_options=store_options if store_options else None,
    )


@cli.command("acp")
@click.option(
    "--agent", type=str, default=None, help="Name of the local agent to expose"
)
@common_options
@click.option("--model-id", default=None, help="Model ID from provider")
def acp_agent(
    agent,
    provider,
    agent_config,
    mcp_config,
    memory_llm,
    memory_path,
    model_id,
    trusted_project_plugins,
):
    """Start an ACP stdio agent exposing a local AgentCrew agent"""
    from AgentCrew.app import AgentCrewApplication

    if memory_path:
        os.environ["MEMORYDB_PATH"] = memory_path

    app = AgentCrewApplication(trusted_project_plugins)
    app.run_acp(
        provider=provider,
        model_id=model_id,
        agent_config=agent_config,
        mcp_config=mcp_config,
        memory_llm=memory_llm,
        agent=agent,
    )


@cli.command()
@click.option("--agent", type=str, help="Name of the agent to run")
@common_options
@click.option("--model-id", default=None, help="Model ID from provider")
@click.option(
    "--output-schema",
    default=None,
    help="JSON schema (file path or JSON string) to enforce structured output format",
)
@click.option(
    "--token-usage-file",
    default=None,
    type=click.Path(),
    help="Write token usage as JSON to this file",
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
    memory_path,
    output_schema,
    token_usage_file,
    task,
    files,
    trusted_project_plugins,
):
    """Run a single job/task with an agent"""
    from AgentCrew.app import AgentCrewApplication

    if memory_path:
        os.environ["MEMORYDB_PATH"] = memory_path

    try:
        app = AgentCrewApplication(trusted_project_plugins)
        response = app.run_job(
            task=task,
            agent=agent,
            files=list(files) if files else None,
            provider=provider,
            model_id=model_id,
            agent_config=agent_config,
            mcp_config=mcp_config,
            memory_llm=memory_llm,
            memory_path=memory_path,
            output_schema=output_schema,
            token_usage_file=token_usage_file,
        )
        click.echo(response)
    except Exception as e:
        import traceback

        print(traceback.format_exc())
        click.echo(f"❌ Error: {e!s}", err=True)
        raise SystemExit(1)


@cli.command()
def copilot_auth():
    """Authenticate with GitHub Copilot and save the API key to config"""
    from AgentCrew.setup import ApplicationSetup

    ApplicationSetup.login()


if __name__ == "__main__":
    """Check for updates and update AgentCrew if a new version is available"""
    cli()
