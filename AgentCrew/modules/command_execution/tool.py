"""
Command Execution Tools

Tool definitions and handlers for secure shell command execution.
"""

import os
from collections.abc import Callable
from typing import Any

from .service import CommandExecutionService

RUNNING_STDOUT_TAIL_LINES = 40
RUNNING_STDERR_TAIL_LINES = 20


def _append_section(response: str, label: str, content: str | None) -> str:
    if not content:
        return response
    separator = "\n" if response.endswith("\n") else "\n\n"
    return f"{response}{separator}{label}:\n{content}"


def _tail_text(content: str | None, max_lines: int) -> str | None:
    if not content:
        return content
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content
    tail = "\n".join(lines[-max_lines:])
    return f"[truncated: showing last {max_lines}/{len(lines)} lines]\n{tail}"


def get_run_command_tool_definition() -> dict[str, Any]:
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

    desc = f"Execute commands via {shell} using sub-process. Returns command_id if timeout."

    args = {
        "command": {
            "type": "string",
            "description": f"Command, do not use `&` as it already a sub-process. Ex: {ex}",
        },
        "timeout": {
            "type": "integer",
            "description": "Seconds (default: 5, max: 60). Returns command_id if still running.",
            "minimum": 5,
            "maximum": 60,
            "default": 5,
        },
        "working_dir": {
            "type": "string",
            "description": f"Working directory. Current working directory is {os.getcwd()}. Use ./ for current dir.",
        },
        "env_vars": {
            "type": "object",
            "description": "Env vars dict. Cannot override PATH, HOME, USER.",
        },
    }

    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": args,
                "required": ["command", "working_dir"],
            },
        },
    }


def get_check_command_status_tool_definition() -> dict[str, Any]:
    """Get tool definition for checking command status."""

    desc = "Check status and output of running command. Returns output, status (running/completed), elapsed time, exit code if completed. Use for monitoring long-running commands."

    args = {
        "command_id": {
            "type": "string",
            "description": "Command ID from run_command (format: 'cmd_xxxxxxxxxxxx').",
        },
    }

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


def get_list_running_commands_tool_definition() -> dict[str, Any]:
    """Get tool definition for listing running commands."""

    desc = "list all running commands with IDs, commands, states, elapsed times, working dirs. Use to monitor active processes, find command IDs for status/termination."

    return {
        "type": "function",
        "function": {
            "name": "list_running_commands",
            "description": desc,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_terminate_command_tool_definition() -> dict[str, Any]:
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


def get_send_command_input_tool_definition() -> dict[str, Any]:
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

    async def handle_run_command(**params) -> str | dict[str, Any]:
        command = params.get("command")
        timeout = params.get("timeout", 5)
        working_dir = params.get("working_dir", "./")
        env_vars = params.get("env_vars")

        if not command:
            raise ValueError("Missing required parameter: command")
        if timeout < 5:
            timeout = 5
        elif timeout > 60:
            timeout = 60

        result = command_service.execute_command(
            command=command, timeout=timeout, working_dir=working_dir, env_vars=env_vars
        )

        if result["status"] == "completed":
            response = (
                f"ok exit={result['exit_code']} dur={result['duration_seconds']}s"
            )
            response = _append_section(response, "stdout", result.get("output"))
            response = _append_section(response, "stderr", result.get("error"))
            return response

        elif result["status"] == "running":
            cmd_id = result["command_id"]
            return f"running id={cmd_id} timeout={result['timeout_seconds']}s"

        else:
            return f"Command failed: {result.get('error', 'Unknown error')}"

    return handle_run_command


def get_check_command_status_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for check_command_status tool."""

    async def handle_check_command_status(**params) -> str | dict[str, Any]:
        command_id = params.get("command_id")
        if not command_id:
            raise ValueError("Missing required parameter: command_id")

        result = command_service.get_command_status(command_id=command_id)

        if result["status"] == "completed":
            response = (
                f"done exit={result['exit_code']} dur={result['duration_seconds']}s"
            )
            response = _append_section(response, "stdout", result.get("output"))
            response = _append_section(response, "stderr", result.get("error"))
            return response

        elif result["status"] == "running":
            response = f"running id={command_id} elapsed={result['elapsed_seconds']}s state={result.get('state', 'running')}"
            response = _append_section(
                response,
                "stdout_tail",
                _tail_text(result.get("output"), RUNNING_STDOUT_TAIL_LINES),
            )
            response = _append_section(
                response,
                "stderr_tail",
                _tail_text(result.get("error"), RUNNING_STDERR_TAIL_LINES),
            )
            return response

        elif result["status"] == "timeout":
            response = f"timeout id={command_id} elapsed={result['elapsed_seconds']}s"
            response = _append_section(response, "stdout", result.get("output"))
            response = _append_section(response, "stderr", result.get("error"))
            return response

        else:
            return f"Error: {result.get('error', 'Unknown error')}"

    return handle_check_command_status


def get_list_running_commands_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for list_running_commands tool."""

    async def handle_list_running_commands(**params) -> str | dict[str, Any]:
        result = command_service.list_running_commands()

        if result["status"] == "error":
            return f"Error: {result.get('error', 'Unknown error')}"

        count = result["count"]
        commands = result["commands"]

        if count == 0:
            return "No commands currently running."

        lines = [f"{count} running command{'s' if count != 1 else ''}"]
        for cmd in commands:
            line = (
                f"{cmd['command_id']} state={cmd['state']} "
                f"elapsed={cmd['elapsed_seconds']}s dir={cmd['working_dir']} "
                f"cmd={cmd['command']}"
            )
            if "exit_code" in cmd:
                line += f" exit={cmd['exit_code']}"
            lines.append(line)

        return "\n".join(lines)

    return handle_list_running_commands


def get_terminate_command_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for terminate_command tool."""

    async def handle_terminate_command(**params) -> str | dict[str, Any]:
        command_id = params.get("command_id")
        if not command_id:
            raise ValueError("Missing required parameter: command_id")

        result = command_service.terminate_command(command_id=command_id)

        if result["status"] == "success":
            return f"terminated id={command_id}"
        else:
            return f"Failed: {result.get('error', 'Unknown error')}"

    return handle_terminate_command


def get_send_command_input_tool_handler(
    command_service: CommandExecutionService,
) -> Callable:
    """Get handler for send_command_input tool."""

    async def handle_send_command_input(**params) -> str | dict[str, Any]:
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
            return f"input_sent id={command_id}"
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
