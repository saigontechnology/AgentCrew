"""
Command Execution Tools

Tool definitions and handlers for secure shell command execution.
"""

from typing import Dict, Any, Callable
from .service import CommandExecutionService


def get_run_command_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for running shell commands."""
    import sys

    is_windows = sys.platform == "win32"

    if is_windows:
        shell = "PowerShell"
        # cmds = "dir, type, python, pip, node, npm, git, docker, curl, Get-Process"
        ex = "dir, python script.py, git status"
    else:
        shell = "Bash"
        # cmds = "ls, cat, ps, python, pip, node, npm, git, docker, curl, grep, find"
        ex = "ls -la, python script.py, git status, ps aux"

    desc = f"Execute commands via {shell}. Returns command_id if timeout."

    args = {
        "command": {"type": "string", "description": f"Command. Ex: {ex}"},
        "timeout": {
            "type": "integer",
            "description": "Seconds (default: 5, max: 60). Returns command_id if still running.",
            "default": 5,
        },
        "working_dir": {
            "type": "string",
            "default": "./",
            "description": "Working directory.",
        },
        "env_vars": {
            "type": "object",
            "description": "Env vars dict. Cannot override PATH, HOME, USER.",
        },
    }

    if provider == "claude":
        return {
            "name": "run_command",
            "description": desc,
            "input_schema": {
                "type": "object",
                "properties": args,
                "required": ["command"],
            },
        }
    else:
        return {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": args,
                    "required": ["command"],
                },
            },
        }


def get_check_command_status_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for checking command status."""

    desc = "Check status and output of running command. Returns output, status (running/completed), elapsed time, exit code if completed. Use for monitoring long-running commands."

    args = {
        "command_id": {
            "type": "string",
            "description": "Command ID from run_command (format: 'cmd_xxxxxxxxxxxx').",
        },
    }

    if provider == "claude":
        return {
            "name": "check_command_status",
            "description": desc,
            "input_schema": {
                "type": "object",
                "properties": args,
                "required": ["command_id"],
            },
        }
    else:
        return {
            "type": "function",
            "function": {
                "name": "check_command_status",
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": args,
                    "required": ["command_id"],
                },
            },
        }


def get_list_running_commands_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for listing running commands."""

    desc = "List all running commands with IDs, commands, states, elapsed times, working dirs. Use to monitor active processes, find command IDs for status/termination."

    if provider == "claude":
        return {
            "name": "list_running_commands",
            "description": desc,
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
    else:
        return {
            "type": "function",
            "function": {
                "name": "list_running_commands",
                "description": desc,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }


def get_terminate_command_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for terminating commands."""
    import sys

    is_windows = sys.platform == "win32"
    method = "TERMINATE/KILL" if is_windows else "SIGTERM/SIGKILL"

    desc = f"Terminate command by ID using {method}. Cleans up all resources and child processes. Use list_running_commands() to find IDs. Safe: only affects managed commands."

    args = {
        "command_id": {
            "type": "string",
            "description": "Command ID (format: 'cmd_xxxxxxxxxxxx'). Get from list_running_commands().",
        },
    }

    if provider == "claude":
        return {
            "name": "terminate_command",
            "description": desc,
            "input_schema": {
                "type": "object",
                "properties": args,
                "required": ["command_id"],
            },
        }
    else:
        return {
            "type": "function",
            "function": {
                "name": "terminate_command",
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": args,
                    "required": ["command_id"],
                },
            },
        }


def get_send_command_input_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for sending input to commands."""

    desc = "Send input to interactive command's stdin. Auto-terminates with newline. Use for commands awaiting input (Python input(), prompts, confirmations). Max 1024 chars."

    args = {
        "command_id": {
            "type": "string",
            "description": "Command ID (format: 'cmd_xxxxxxxxxxxx').",
        },
        "input_text": {
            "type": "string",
            "description": "Text to send. Max 1024 chars. Ex: 'yes', 'Alice', '1'",
        },
    }

    if provider == "claude":
        return {
            "name": "send_command_input",
            "description": desc,
            "input_schema": {
                "type": "object",
                "properties": args,
                "required": ["command_id", "input_text"],
            },
        }
    else:
        return {
            "type": "function",
            "function": {
                "name": "send_command_input",
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": args,
                    "required": ["command_id", "input_text"],
                },
            },
        }


def get_run_command_tool_handler(command_service: CommandExecutionService) -> Callable:
    """Get handler for run_command tool."""

    def handle_run_command(**params) -> str | Dict[str, Any]:
        command = params.get("command")
        timeout = params.get("timeout", 5)
        working_dir = params.get("working_dir", "./")
        env_vars = params.get("env_vars")

        if not command:
            raise ValueError("Missing required parameter: command")
        if timeout < 1 or timeout > 60:
            raise ValueError("Timeout must be between 1 and 60 seconds")

        result = command_service.execute_command(
            command=command, timeout=timeout, working_dir=working_dir, env_vars=env_vars
        )

        if result["status"] == "completed":
            response = f"Command completed successfully.\nExit Code: {result['exit_code']}\nDuration: {result['duration_seconds']}s\n\n"
            if result["output"]:
                response += f"Output:\n{result['output']}"
            if result.get("error"):
                response += f"\n\nStderr:\n{result['error']}"
            return response

        elif result["status"] == "running":
            cmd_id = result["command_id"]
            response = f"Command still running after {result['timeout_seconds']}s.\nCommand ID: {cmd_id}\n\n"
            response += f"Use check_command_status(command_id='{cmd_id}') to monitor.\n"
            response += f"Use send_command_input(command_id='{cmd_id}', input_text='...') if waiting for input."
            return response

        else:
            return f"Command failed: {result.get('error', 'Unknown error')}"

    return handle_run_command


def get_check_command_status_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for check_command_status tool."""

    def handle_check_command_status(**params) -> str | Dict[str, Any]:
        command_id = params.get("command_id")
        if not command_id:
            raise ValueError("Missing required parameter: command_id")

        result = command_service.get_command_status(
            command_id=command_id, consume_output=True
        )

        if result["status"] == "completed":
            response = f"Command completed.\nExit Code: {result['exit_code']}\nDuration: {result['duration_seconds']}s\n\n"
            if result["output"]:
                response += f"Output:\n{result['output']}"
            if result.get("error"):
                response += f"\n\nStderr:\n{result['error']}"
            return response

        elif result["status"] == "running":
            response = f"Command still running.\nElapsed: {result['elapsed_seconds']}s\nState: {result.get('state', 'running')}\n\n"
            if result.get("output"):
                response += f"Output so far:\n{result['output']}\n\n"
            if result.get("error"):
                response += f"Stderr so far:\n{result['error']}\n\n"
            response += "Check again later."
            return response

        elif result["status"] == "timeout":
            response = f"Command exceeded max lifetime and was terminated.\nElapsed: {result['elapsed_seconds']}s\n\n"
            if result.get("output"):
                response += f"Output before timeout:\n{result['output']}"
            if result.get("error"):
                response += f"\n\nStderr:\n{result['error']}"
            return response

        else:
            return f"Error: {result.get('error', 'Unknown error')}"

    return handle_check_command_status


def get_list_running_commands_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for list_running_commands tool."""

    def handle_list_running_commands(**params) -> str | Dict[str, Any]:
        result = command_service.list_running_commands()

        if result["status"] == "error":
            return f"Error: {result.get('error', 'Unknown error')}"

        count = result["count"]
        commands = result["commands"]

        if count == 0:
            return "No commands currently running."

        response = f"Running commands: {count}\n\n"
        for idx, cmd in enumerate(commands, 1):
            response += f"{idx}. ID: {cmd['command_id']}\n   Command: {cmd['command']}\n   State: {cmd['state']}\n   Elapsed: {cmd['elapsed_seconds']}s\n   Dir: {cmd['working_dir']}\n"
            if "exit_code" in cmd:
                response += f"   Exit: {cmd['exit_code']}\n"
            response += "\n"

        response += "Use check_command_status(command_id='...') for details.\nUse terminate_command(command_id='...') to stop."
        return response

    return handle_list_running_commands


def get_terminate_command_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for terminate_command tool."""

    def handle_terminate_command(**params) -> str | Dict[str, Any]:
        command_id = params.get("command_id")
        if not command_id:
            raise ValueError("Missing required parameter: command_id")

        result = command_service.terminate_command(command_id=command_id)

        if result["status"] == "success":
            return f"Command {command_id} terminated successfully.\nAll resources cleaned up."
        else:
            return f"Failed: {result.get('error', 'Unknown error')}"

    return handle_terminate_command


def get_send_command_input_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for send_command_input tool."""

    def handle_send_command_input(**params) -> str | Dict[str, Any]:
        command_id = params.get("command_id")
        input_text = params.get("input_text")

        if not command_id:
            raise ValueError("Missing required parameter: command_id")
        if not input_text:
            raise ValueError("Missing required parameter: input_text")

        result = command_service.send_input(
            command_id=command_id, input_text=input_text
        )

        if result["status"] == "success":
            return (
                f"Input sent to {command_id}.\nUse check_command_status to see result."
            )
        else:
            return f"Failed: {result.get('error', 'Unknown error')}"

    return handle_send_command_input


def register(service_instance=None, agent=None):
    """Register command execution tools."""
    from AgentCrew.modules.tools.registration import register_tool

    if service_instance is None:
        service_instance = CommandExecutionService.get_instance()

    register_tool(
        get_run_command_tool_definition,
        get_run_command_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_check_command_status_tool_definition,
        get_check_command_status_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_send_command_input_tool_definition,
        get_send_command_input_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_list_running_commands_tool_definition,
        get_list_running_commands_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_terminate_command_tool_definition,
        get_terminate_command_tool_handler,
        service_instance,
        agent,
    )
