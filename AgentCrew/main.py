import click
import os
import sys
import requests
import subprocess
import platform


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
    import tempfile
    import logging

    formatter = "{time} - {name} - {level} - {message}"
    log_level = os.getenv("AGENTCREW_LOG_LEVEL", "ERROR").upper()
    logger.remove(0)

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.ERROR)

    if os.getenv("AGENTCREW_ENV", "development") == "production":
        log_dir_path = os.getenv("AGENTCREW_LOG_PATH", tempfile.gettempdir())
        os.makedirs(log_dir_path, exist_ok=True)
        log_path = log_dir_path + "/agentcrew_log_{time}.log"

        formatter = "{time} - {name} - {level} - {message}"
        logger.add(log_path, level=log_level, format=formatter, rotation="10 MB")

    else:
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


def check_and_update():
    """Check for updates against the GitHub repository and run update command if needed"""
    try:
        current_version = get_current_version()

        click.echo(f"Current version: {current_version}\nChecking for updates...")
        latest_version = get_latest_github_version()

        if not current_version or not latest_version:
            click.echo("⚠️ Could not determine version information", err=True)
            return

        click.echo(f"Latest version: {latest_version}")

        if version_is_older(current_version, latest_version):
            system = platform.system().lower()

            if system == "linux" or system == "darwin":
                if click.confirm(
                    "🔄 New version available! Do you want to update now?",
                    default=False,
                ):
                    click.echo("🔄 Starting update...")
                    run_update_command()
                    sys.exit(0)
                else:
                    click.echo("⏭️ Skipping update. Starting application...")
            else:
                command = "uv tool install --python=3.12 --reinstall agentcrew-ai[cpu]@latest --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match"
                click.echo(f"🔄 New version available!\nRun {command} to update.")
        else:
            click.echo("✅ You are running the latest version")

    except Exception as e:
        click.echo(f"❌ Update check failed: {str(e)}", err=True)


def get_current_version():
    """Get the current version of AgentCrew"""
    try:
        import AgentCrew

        if hasattr(AgentCrew, "__version__"):
            return AgentCrew.__version__

        return None
    except Exception:
        return None


def get_latest_github_version():
    """Get the latest version from GitHub repository tags"""
    try:
        api_url = (
            "https://api.github.com/repos/saigontechnology/AgentCrew/releases/latest"
        )
        response = requests.get(api_url, timeout=10)

        if response.status_code == 200:
            release_data = response.json()
            return release_data.get("tag_name", "").lstrip("v")

        tags_url = "https://api.github.com/repos/saigontechnology/AgentCrew/tags"
        response = requests.get(tags_url, timeout=10)

        if response.status_code == 200:
            tags_data = response.json()
            if tags_data:
                # Get the first (latest) tag
                latest_tag = tags_data[0].get("name", "").lstrip("v")
                return latest_tag

        return None
    except Exception:
        return None


def version_is_older(current: str, latest: str) -> bool:
    """
    Compare two semantic version strings to check if current is older than latest.

    Args:
        current: Current version string (e.g., "0.5.1")
        latest: Latest version string (e.g., "0.6.0")

    Returns:
        True if current version is older than latest version
    """
    try:
        current_clean = current.lstrip("v")
        latest_clean = latest.lstrip("v")

        current_parts = [int(x) for x in current_clean.split(".")]
        latest_parts = [int(x) for x in latest_clean.split(".")]

        max_length = max(len(current_parts), len(latest_parts))
        current_parts.extend([0] * (max_length - len(current_parts)))
        latest_parts.extend([0] * (max_length - len(latest_parts)))

        for current_part, latest_part in zip(current_parts, latest_parts):
            if current_part < latest_part:
                return True
            elif current_part > latest_part:
                return False

        return False

    except (ValueError, AttributeError):
        return current != latest


def run_update_command():
    """Run the appropriate update command based on the operating system"""
    try:
        system = platform.system().lower()

        if system == "linux" or system == "darwin":  # Darwin is macOS
            # Linux/macOS update command
            command = "uv tool install --python=3.12 --reinstall agentcrew-ai[cpu]@latest --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match"
            click.echo("🐧 Running Linux/macOS update command...")

        else:
            click.echo(f"❌ Unsupported operating system: {system}", err=True)
            return

        # Execute the update command
        result = subprocess.run(command, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            click.echo("✅ Update completed successfully!")
            click.echo("🔄 Please restart the application to use the new version.")
        else:
            click.echo("❌ Update failed!")
            if result.stderr:
                click.echo(f"Error: {result.stderr}")

    except Exception as e:
        click.echo(f"❌ Update execution failed: {str(e)}", err=True)


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
@click.option(
    "--console",
    is_flag=True,
    default=False,
    help="Use console interface instead of GUI",
)
@click.option(
    "--with-voice",
    is_flag=True,
    default=False,
    help="Enable voice input/output (if supported by the agent)",
)
def chat(provider, agent_config, mcp_config, memory_llm, console, with_voice):
    """Start an interactive chat session with LLM"""
    check_and_update()
    from AgentCrew.app import AgentCrewApplication

    app = AgentCrewApplication()

    if console:
        app.run_console(provider, agent_config, mcp_config, memory_llm, with_voice)
    else:
        app.run_gui(provider, agent_config, mcp_config, memory_llm, with_voice)


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
