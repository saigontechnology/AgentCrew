import os
import platform
import subprocess
import sys
import threading

import click
import requests

from AgentCrew.setup import PROVIDER_LIST, common_options


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
    import tempfile

    from loguru import logger

    formatter = "{time} - {name} - {level} - {message}"
    logger.remove(0)

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.ERROR)

    if os.getenv("AGENTCREW_ENV", "development") == "production":
        log_level = os.getenv("AGENTCREW_LOG_LEVEL", "ERROR").upper()
        log_dir_path = os.getenv("AGENTCREW_LOG_PATH", tempfile.gettempdir())
        os.makedirs(log_dir_path, exist_ok=True)
        log_path = log_dir_path + "/agentcrew_log_{time}.log"

        formatter = "{time} - {name} - {level} - {message}"
        logger.add(log_path, level=log_level, format=formatter, rotation="10 MB")

    else:
        log_level = os.getenv("AGENTCREW_LOG_LEVEL", "WARNING").upper()
        logger.add(
            sys.stderr,
            level=log_level,
            format=formatter,
        )


def cli_prod():
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        click.echo(f"AgentCrew version: {get_current_version()}")
        sys.exit(0)
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


def check_and_update():
    """Check for updates against the GitHub repository and run update command if needed"""
    try:
        current_version = get_current_version()

        click.echo(f"Current version: {current_version}\nChecking for updates...")
        latest_version, release_notes = get_latest_release_info()

        if not current_version or not latest_version:
            click.echo("⚠️ Could not determine version information", err=True)
            return

        click.echo(f"Latest version: {latest_version}")

        if version_is_older(current_version, latest_version):
            system = platform.system().lower()

            if system == "linux" or system == "darwin":
                click.echo("\n" + "=" * 60)
                click.echo("🔄 New version available!")
                click.echo("=" * 60)

                if release_notes:
                    click.echo("\n📝 Release Notes:")
                    click.echo("-" * 40)
                    click.echo(release_notes)
                    click.echo("-" * 40 + "\n")
                else:
                    click.echo("\n⚠️ Could not fetch release notes.")

                if click.confirm(
                    "\nDo you want to update now?",
                    default=False,
                ):
                    click.echo("🔄 Starting update...")
                    run_update_command()
                    sys.exit(0)
                else:
                    click.echo("⏭️ Skipping update. Starting application...")
            else:
                command = "uv tool install --python=3.12 --reinstall agentcrew-ai[cpu]@latest --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match"

                click.echo("\n" + "=" * 60)
                click.echo("🔄 New version available!")
                click.echo("=" * 60)

                if release_notes:
                    click.echo("\n📝 Release Notes:")
                    click.echo("-" * 40)
                    click.echo(release_notes)
                    click.echo("-" * 40 + "\n")

                click.echo(f"Run the following command to update:\n\n{command}")
        else:
            click.echo("✅ You are running the latest version")

    except Exception as e:
        click.echo(f"❌ Update check failed: {e!s}", err=True)


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


def get_latest_release_info():
    """Get the latest release information including version and release notes from GitHub

    Returns:
        tuple: (version, release_notes) where both can be None if not found.
    """
    try:
        api_url = (
            "https://api.github.com/repos/saigontechnology/AgentCrew/releases/latest"
        )
        response = requests.get(api_url, timeout=10)

        if response.status_code == 200:
            release_data = response.json()
            tag_name = release_data.get("tag_name", "").lstrip("v")
            name = release_data.get("name", "")
            body = release_data.get("body", "")

            release_notes = None
            if body:
                release_notes = f"## {name or tag_name}\n\n{body}"

            return tag_name, release_notes

        return None, None
    except Exception:
        return None, None


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


def _start_version_check_background(
    current_version: str | None,
) -> tuple[dict[str, object], threading.Thread]:
    """Start a background thread to check for updates via the GitHub API.

    The network call runs in a daemon thread so it does not block startup.
    Caller should join the returned thread before accessing the result dict.

    Returns:
        Tuple of (result_dict, thread). The result dict is populated with:
            - ``latest``: latest version string (or None)
            - ``notes``: release notes (or None)
            - ``is_newer``: bool (or not present if check failed)
            - ``error``: error message (only present if check failed)
    """
    result: dict[str, object] = {}

    def _check() -> None:
        try:
            latest_version, release_notes = get_latest_release_info()
            result["latest"] = latest_version
            result["notes"] = release_notes
            if latest_version and current_version:
                result["is_newer"] = version_is_older(current_version, latest_version)
        except Exception as e:
            result["error"] = str(e)

    thread = threading.Thread(target=_check, daemon=True)
    thread.start()
    return result, thread


def _show_version_result(result: dict[str, object]) -> bool:
    """Display the version check result and optionally prompt for an update.

    Args:
        result: The result dict populated by ``_start_version_check_background``.

    Returns:
        True if the caller should exit (user chose to update), False otherwise.
    """
    if "error" in result:
        click.echo(f"\u26a0\ufe0f  Update check failed: {result['error']}", err=True)
        return False

    latest = result.get("latest")
    if not latest:
        return False

    if result.get("is_newer"):
        release_notes = result.get("notes", "")
        system = platform.system().lower()

        click.echo("\n" + "=" * 60)
        click.echo("\U0001f504 New version available!")
        click.echo(f"Latest version: {latest}")
        click.echo("=" * 60)

        if release_notes:
            click.echo("\n\U0001f4dd Release Notes:")
            click.echo("-" * 40)
            click.echo(release_notes)
            click.echo("-" * 40 + "\n")

        if system in ("linux", "darwin"):
            if click.confirm("\nDo you want to update now?", default=False):
                click.echo("\U0001f504 Starting update...")
                run_update_command()
                return True
            click.echo("\u23ed\ufe0f Skipping update. Starting application...")
        else:
            command = (
                "uv tool install --python=3.12 --reinstall agentcrew-ai[cpu]@latest "
                "--index https://download.pytorch.org/whl/cpu "
                "--index-strategy unsafe-best-match"
            )
            click.echo(f"Run the following command to update:\n\n{command}")
    else:
        click.echo(f"\u2705 You are running the latest version ({latest})")

    return False


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
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, check=False
        )  # returncode checked manually below

        if result.returncode == 0:
            click.echo("✅ Update completed successfully!")
            click.echo("🔄 Please restart the application to use the new version.")
        else:
            click.echo("❌ Update failed!")
            if result.stderr:
                click.echo(f"Error: {result.stderr}")

    except Exception as e:
        click.echo(f"❌ Update execution failed: {e!s}", err=True)


@cli.command()
@common_options
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
@click.option("--model-id", default=None, help="Model ID from provider")
def chat(
    provider,
    agent_config,
    mcp_config,
    memory_llm,
    memory_path,
    console,
    with_voice,
    model_id,
    trusted_project_plugins,
):
    """Start an interactive chat session with LLM"""
    current_version = get_current_version()
    click.echo(f"AgentCrew version: {current_version or 'unknown'}")

    # Start version check in a background thread so the network call
    # runs in parallel with application setup (~1.2s).
    version_result, version_thread = _start_version_check_background(current_version)

    from AgentCrew.app import AgentCrewApplication

    if memory_path:
        os.environ["MEMORYDB_PATH"] = memory_path

    app = AgentCrewApplication(trusted_project_plugins)

    try:
        from PySide6 import __version__

        if __version__ != "6.11.0":
            console = True
    except ImportError:
        console = True

    # Wait for version check to complete (it ran in parallel with setup)
    version_thread.join(timeout=5.0)

    # Show result before starting the console/GUI
    if _show_version_result(version_result):
        sys.exit(0)

    if console:
        app.run_console(
            provider, agent_config, mcp_config, memory_llm, with_voice, model_id
        )
    else:
        app.run_gui(
            provider, agent_config, mcp_config, memory_llm, with_voice, model_id
        )


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
    help="Store options as key=value pairs (e.g. --store-option base_dir=./data --store-option redis_url=redis://localhost:6379)",
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


@cli.command()
def chatgpt_auth():
    """Authenticate with ChatGPT subscription (Plus/Pro) for API access via Codex OAuth"""
    from AgentCrew.setup import ApplicationSetup

    ApplicationSetup.chatgpt_login()


@cli.command("create-agent")
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
@click.option("--model-id", default=None, help="Model ID from provider")
@click.option(
    "--name",
    "-n",
    default=None,
    help="Name for the new agent (will prompt interactively if omitted)",
)
@click.option(
    "--description",
    "-d",
    default=None,
    help="Description of what the agent should do (will prompt interactively if omitted)",
)
def create_agent_command(
    provider,
    agent_config,
    model_id,
    name,
    description,
):
    """Create a new agent interactively using the same flow as onboarding"""
    from AgentCrew.modules.config import ConfigManagement
    from AgentCrew.modules.onboarding import OnboardingService
    from AgentCrew.setup import ApplicationSetup

    if agent_config:
        os.environ["SW_AGENTS_CONFIG"] = agent_config

    setup = ApplicationSetup(ConfigManagement())
    setup.load_api_keys_from_config()

    if not model_id:
        model_id = setup.detect_model_id()

    detected_provider = provider or setup.detect_provider()
    if not detected_provider:
        click.echo(
            "No LLM provider configured. Please set an API key or use --provider.",
            err=True,
        )
        raise SystemExit(1)

    services = setup.setup_services(
        detected_provider, model_id=model_id, need_memory=False, with_voice=False
    )
    onboarding = OnboardingService(services["llm"], services=services)
    success = onboarding.create_agent(name=name, description=description)
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    """Check for updates and update AgentCrew if a new version is available"""
    cli()
