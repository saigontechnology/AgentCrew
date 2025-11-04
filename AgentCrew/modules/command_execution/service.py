import os
import sys
import time
import uuid
import queue
import threading
import subprocess
import re
import atexit
import hashlib
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
from .metric import CommandMetrics
from .types import CommandState, CommandProcess
from .constants import (
    MAX_CONCURRENT_COMMANDS,
    MAX_COMMAND_LIFETIME,
    MAX_OUTPUT_SIZE,
    MAX_COMMANDS_PER_MINUTE,
    MAX_INPUT_SIZE,
    BLOCKED_PATTERNS,
    PROHIBITED_WORKING_PATHS,
    USER_SENSITIVE_PATHS,
    PROTECTED_ENV_VARS,
)
from loguru import logger


class CommandExecutionService:
    """
    Secure command execution service with platform detection, threading, and resource limits.

    Security Features:
    - Command validation (whitelist/blacklist)
    - Rate limiting per agent
    - Resource limits (concurrent, lifetime, output size)
    - Audit logging
    - Input sanitization

    All security configuration constants are defined in constants.py
    """

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = CommandExecutionService()
        return cls._instance

    def __init__(self):
        """Initialize command execution service"""
        if CommandExecutionService._instance is not None:
            raise RuntimeError("Use get_instance() to get CommandExecutionService")

        # Platform detection
        self.platform = sys.platform
        self._is_windows = self.platform == "win32"

        # Process tracking
        self._instances: Dict[str, CommandProcess] = {}
        self._instance_lock = threading.Lock()

        # Rate limiting (application-wide)
        self._rate_limiter: List[float] = []

        # Metrics
        self.metrics = CommandMetrics()

        # Register cleanup on shutdown
        atexit.register(self.shutdown)

        logger.info(f"CommandExecutionService initialized (platform: {self.platform})")

    def _get_shell_config(self) -> Tuple[str, List[str]]:
        """Get platform-specific shell configuration"""
        if self._is_windows:
            # Windows PowerShell with UTF-8 encoding and text output
            return "powershell.exe", [
                "-ExecutionPolicy",
                "RemoteSigned",
                "-NoProfile",  # Skip profile loading (faster + safer)
                "-OutputFormat",
                "Text",  # Force text output
                "-Command",
                "[Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8; ",
            ]
        else:
            # Unix: bash shell
            return "/bin/bash", ["-c"]

    def _validate_command(self, command: str) -> Tuple[bool, str]:
        """
        Validate command against security policy.

        Returns:
            Tuple[bool, str]: (is_valid, error_message)
        """
        if not command or not command.strip():
            return False, "Empty command not allowed"

        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Command contains blocked pattern: {pattern}"

        return True, ""

    def _check_rate_limit(self) -> Tuple[bool, str]:
        """
        Check if application is within rate limits.

        Returns:
            Tuple[bool, str]: (is_allowed, error_message)
        """
        now = time.time()

        with self._instance_lock:
            running_commands = [
                cmd
                for cmd in self._instances.values()
                if cmd.state == CommandState.RUNNING
            ]

            if len(running_commands) >= MAX_CONCURRENT_COMMANDS:
                return False, (
                    f"Maximum concurrent commands ({MAX_CONCURRENT_COMMANDS}) "
                    f"reached for application"
                )

            self._rate_limiter = [ts for ts in self._rate_limiter if now - ts < 60]

            if len(self._rate_limiter) >= MAX_COMMANDS_PER_MINUTE:
                return False, (
                    f"Rate limit exceeded: maximum {MAX_COMMANDS_PER_MINUTE} "
                    f"commands per minute for application"
                )

            self._rate_limiter.append(now)

        return True, ""

    def _validate_working_dir(
        self, working_dir: Optional[str]
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Validate and resolve working directory against security blacklist.

        Returns:
            Tuple[bool, str, Optional[str]]: (is_valid, error_message, resolved_path)
        """
        if not working_dir:
            return True, "", None

        try:
            resolved = os.path.abspath(os.path.normpath(working_dir))

            if not os.path.isdir(resolved):
                return False, f"Working directory does not exist: {working_dir}", None

            prohibited_paths = PROHIBITED_WORKING_PATHS.get(self.platform, [])

            for prohibited in prohibited_paths:
                prohibited_normalized = os.path.abspath(os.path.normpath(prohibited))

                if resolved == prohibited_normalized or resolved.startswith(
                    prohibited_normalized + os.sep
                ):
                    return (
                        False,
                        f"Access denied: '{working_dir}' is in prohibited system directory '{prohibited}'",
                        None,
                    )

            user_sensitive = USER_SENSITIVE_PATHS.get(self.platform, [])
            home_dir = os.path.expanduser("~")

            for sensitive_rel in user_sensitive:
                sensitive_full = os.path.abspath(
                    os.path.normpath(os.path.join(home_dir, sensitive_rel))
                )

                if resolved == sensitive_full or resolved.startswith(
                    sensitive_full + os.sep
                ):
                    return (
                        False,
                        f"Access denied: '{working_dir}' is in protected user directory '~/{sensitive_rel}'",
                        None,
                    )

            return True, "", resolved

        except Exception as e:
            return False, f"Invalid working directory: {e}", None

    def _validate_env_vars(
        self, env_vars: Optional[Dict[str, str]]
    ) -> Tuple[bool, str]:
        """
        Validate environment variables.

        Returns:
            Tuple[bool, str]: (is_valid, error_message)
        """
        if not env_vars:
            return True, ""

        for key in env_vars.keys():
            if key in PROTECTED_ENV_VARS:
                return False, f"Cannot override protected environment variable: {key}"

        return True, ""

    def _audit_log(
        self,
        command: str,
        status: str,
        command_id: str,
        duration: float = 0,
        output_size: int = 0,
    ):
        """
        Log command execution for audit trail.
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "command_id": command_id,
            "command_hash": hashlib.sha256(command.encode()).hexdigest(),
            "command": command,
            "status": status,
            "duration_seconds": round(duration, 3),
            "output_size_bytes": output_size,
            "platform": self.platform,
        }

        logger.info(f"COMMAND_AUDIT: {log_entry}")

    def _reader_thread(
        self,
        stream,
        output_queue: queue.Queue,
        stop_event: threading.Event,
        max_size: int,
    ):
        """
        Read stream line by line into queue with size enforcement.

        Uses sentinel values:
        - ('data', line): Normal output line
        - ('eof', None): End of stream
        - ('error', msg): Error occurred
        - ('size_limit', None): Output size limit reached
        """
        total_bytes = 0

        try:
            for line in iter(stream.readline, b""):
                if stop_event.is_set():
                    break

                total_bytes += len(line)
                if total_bytes > max_size:
                    output_queue.put(("size_limit", None))
                    logger.warning(f"Output size limit ({max_size} bytes) exceeded")
                    break

                decoded = line.decode("utf-8", errors="replace")
                output_queue.put(("data", decoded))

        except Exception as e:
            logger.error(f"Reader thread error: {e}")
            output_queue.put(("error", str(e)))
        finally:
            output_queue.put(("eof", None))
            stream.close()

    def execute_command(
        self,
        command: str,
        timeout: int = 5,
        working_dir: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute shell command with timeout and security controls.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds (default: 5)
            working_dir: Working directory for command execution
            env_vars: Additional environment variables

        Returns:
            Dict with status, command_id (if still running), output, exit_code
        """

        start_time = time.time()

        is_valid, error_msg = self._validate_command(command)
        if not is_valid:
            self._audit_log(command, "validation_failed", "N/A")
            return {
                "status": "error",
                "error": f"Command validation failed: {error_msg}",
            }

        is_allowed, error_msg = self._check_rate_limit()
        if not is_allowed:
            self._audit_log(command, "rate_limited", "N/A")
            return {"status": "error", "error": error_msg}

        is_valid, error_msg, resolved_dir = self._validate_working_dir(working_dir)
        if not is_valid:
            self._audit_log(command, "invalid_workdir", "N/A")
            return {"status": "error", "error": error_msg}

        is_valid, error_msg = self._validate_env_vars(env_vars)
        if not is_valid:
            self._audit_log(command, "invalid_env", "N/A")
            return {"status": "error", "error": error_msg}

        command_id = f"cmd_{uuid.uuid4().hex[:12]}"
        cmd_process = None

        try:
            shell_executable, shell_args = self._get_shell_config()

            full_command = [shell_executable] + shell_args + [command]

            env = os.environ.copy()
            if env_vars:
                env.update(env_vars)

            if self._is_windows:
                process = subprocess.Popen(
                    full_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    cwd=resolved_dir,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore
                )
            else:
                process = subprocess.Popen(
                    full_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    cwd=resolved_dir,
                    env=env,
                    preexec_fn=os.setsid,  # Create process group
                )

            cmd_process = CommandProcess(
                id=command_id,
                command=command,
                process=process,
                platform=self.platform,
                start_time=start_time,
                working_dir=resolved_dir,
            )

            cmd_process.transition_to(CommandState.STARTING)

            stdout_thread = threading.Thread(
                target=self._reader_thread,
                args=(
                    process.stdout,
                    cmd_process.output_queue,
                    cmd_process.stop_event,
                    MAX_OUTPUT_SIZE,
                ),
                daemon=True,
                name=f"stdout-reader-{command_id}",
            )

            stderr_thread = threading.Thread(
                target=self._reader_thread,
                args=(
                    process.stderr,
                    cmd_process.error_queue,
                    cmd_process.stop_event,
                    MAX_OUTPUT_SIZE,
                ),
                daemon=True,
                name=f"stderr-reader-{command_id}",
            )

            stdout_thread.start()
            stderr_thread.start()

            cmd_process.reader_threads = [stdout_thread, stderr_thread]
            cmd_process.transition_to(CommandState.RUNNING)

            with self._instance_lock:
                self._instances[command_id] = cmd_process

            process.wait(timeout=timeout)

            cmd_process.exit_code = process.returncode
            cmd_process.transition_to(CommandState.COMPLETING)

            output_lines = []
            error_lines = []

            while not cmd_process.output_queue.empty():
                msg_type, data = cmd_process.output_queue.get()
                if msg_type == "data":
                    output_lines.append(data)

            while not cmd_process.error_queue.empty():
                msg_type, data = cmd_process.error_queue.get()
                if msg_type == "data":
                    error_lines.append(data)

            output = "".join(output_lines)
            error_output = "".join(error_lines)

            duration = time.time() - start_time

            cmd_process.transition_to(CommandState.COMPLETED)

            self._audit_log(
                command,
                "completed",
                command_id,
                duration,
                len(output) + len(error_output),
            )

            self.metrics.record_execution(command, duration, "completed")
            self._cleanup_command_internal(command_id)

            result = {
                "status": "completed",
                "command_id": command_id,
                "output": output,
                "error": error_output if error_output else None,
                "exit_code": process.returncode,
                "duration_seconds": round(duration, 3),
            }

            return result

        except subprocess.TimeoutExpired:
            if cmd_process:
                cmd_process.transition_to(CommandState.RUNNING)

            logger.debug(f"Command {command_id} still running after {timeout}s")

            self._audit_log(command, "timeout_waiting", command_id)

            return {
                "status": "running",
                "command_id": command_id,
                "message": f"Command still running after {timeout} seconds. Use check_command_status to monitor.",
                "timeout_seconds": timeout,
            }

        except Exception as e:
            logger.error(f"Command execution error: {e}")

            self._audit_log(command, "error", command_id)
            self.metrics.record_execution(command, time.time() - start_time, "error")

            if command_id in self._instances:
                self._cleanup_command_internal(command_id)

            return {"status": "error", "error": f"Execution failed: {str(e)}"}

    def get_command_status(
        self, command_id: str, consume_output: bool = True
    ) -> Dict[str, Any]:
        """
        Check status of running command.

        Args:
            command_id: Command identifier
            consume_output: If True, drain and return queued output

        Returns:
            Dict with status, output, exit_code, elapsed_time
        """
        with self._instance_lock:
            cmd_process = self._instances.get(command_id)

        if not cmd_process:
            return {"status": "error", "error": f"Command '{command_id}' not found"}

        exit_code = cmd_process.process.poll()

        output_lines = []
        error_lines = []

        if consume_output:
            while not cmd_process.output_queue.empty():
                try:
                    msg_type, data = cmd_process.output_queue.get_nowait()
                    if msg_type == "data":
                        output_lines.append(data)
                    elif msg_type == "size_limit":
                        output_lines.append("\n[OUTPUT SIZE LIMIT REACHED]\n")
                except queue.Empty:
                    break

            while not cmd_process.error_queue.empty():
                try:
                    msg_type, data = cmd_process.error_queue.get_nowait()
                    if msg_type == "data":
                        error_lines.append(data)
                except queue.Empty:
                    break

        output = "".join(output_lines)
        error_output = "".join(error_lines)
        elapsed = time.time() - cmd_process.start_time

        if elapsed > MAX_COMMAND_LIFETIME:
            logger.warning(f"Command {command_id} exceeded max lifetime, terminating")
            self.cleanup_command(command_id)
            cmd_process.transition_to(CommandState.TIMEOUT)

            return {
                "status": "timeout",
                "command_id": command_id,
                "output": output,
                "error": error_output if error_output else None,
                "elapsed_seconds": round(elapsed, 3),
                "message": f"Command exceeded maximum lifetime ({MAX_COMMAND_LIFETIME}s)",
            }

        if exit_code is not None:
            cmd_process.exit_code = exit_code
            cmd_process.transition_to(CommandState.COMPLETED)

            duration = elapsed
            self._audit_log(
                cmd_process.command,
                "completed",
                command_id,
                duration,
                len(output) + len(error_output),
            )
            self.metrics.record_execution(cmd_process.command, duration, "completed")
            self._cleanup_command_internal(command_id)

            return {
                "status": "completed",
                "command_id": command_id,
                "output": output,
                "error": error_output if error_output else None,
                "exit_code": exit_code,
                "duration_seconds": round(duration, 3),
            }
        else:
            return {
                "status": "running",
                "command_id": command_id,
                "output": output,
                "error": error_output if error_output else None,
                "elapsed_seconds": round(elapsed, 3),
                "state": cmd_process.state.value,
            }

    def send_input(self, command_id: str, input_text: str) -> Dict[str, Any]:
        """
        Send input to running command's stdin.

        Args:
            command_id: Command identifier
            input_text: Text to send (will append newline if not present)

        Returns:
            Dict with status and message
        """
        if len(input_text) > MAX_INPUT_SIZE:
            return {
                "status": "error",
                "error": f"Input too large (max {MAX_INPUT_SIZE} characters)",
            }

        if any(ord(c) < 32 and c not in "\n\t\r" for c in input_text):
            return {
                "status": "error",
                "error": "Input contains invalid control characters",
            }

        with self._instance_lock:
            cmd_process = self._instances.get(command_id)

        if not cmd_process:
            return {"status": "error", "error": f"Command '{command_id}' not found"}

        if cmd_process.process.poll() is not None:
            return {"status": "error", "error": "Command has already completed"}

        try:
            if not input_text.endswith("\n"):
                input_text += "\n"

            if cmd_process.process.stdin:
                cmd_process.process.stdin.write(input_text.encode("utf-8"))
                cmd_process.process.stdin.flush()

            logger.debug(f"Sent input to command {command_id}: {repr(input_text)}")

            if cmd_process.state == CommandState.WAITING_INPUT:
                cmd_process.transition_to(CommandState.RUNNING)

            return {
                "status": "success",
                "message": "Input sent to command",
                "command_id": command_id,
            }

        except Exception as e:
            logger.error(f"Failed to send input to command {command_id}: {e}")
            return {"status": "error", "error": f"Failed to send input: {str(e)}"}

    def cleanup_command(self, command_id: str) -> Dict[str, Any]:
        """
        Terminate and cleanup command (user-callable).

        Args:
            command_id: Command identifier

        Returns:
            Dict with status and message
        """
        with self._instance_lock:
            cmd_process = self._instances.get(command_id)

        if not cmd_process:
            return {"status": "error", "error": f"Command '{command_id}' not found"}

        try:
            self._cleanup_command_internal(command_id)

            return {
                "status": "success",
                "message": f"Command {command_id} terminated",
                "command_id": command_id,
            }
        except Exception as e:
            logger.error(f"Cleanup error for {command_id}: {e}")
            return {"status": "error", "error": f"Cleanup failed: {str(e)}"}

    def _cleanup_command_internal(self, command_id: str):
        """
        Internal cleanup implementation with proper process termination.
        """
        with self._instance_lock:
            cmd_process = self._instances.get(command_id)
            if not cmd_process:
                return

        try:
            # Signal reader threads to stop
            cmd_process.stop_event.set()

            # Terminate process if still running
            if cmd_process.process.poll() is None:
                try:
                    if self._is_windows:
                        # Windows: terminate then kill with grace period
                        cmd_process.process.terminate()
                        time.sleep(0.5)
                        if cmd_process.process.poll() is None:
                            cmd_process.process.kill()
                    else:
                        # Unix: SIGTERM to process group, then SIGKILL
                        import signal

                        try:
                            os.killpg(
                                os.getpgid(cmd_process.process.pid), signal.SIGTERM
                            )
                            time.sleep(0.5)
                            if cmd_process.process.poll() is None:
                                os.killpg(
                                    os.getpgid(cmd_process.process.pid), signal.SIGKILL
                                )
                        except ProcessLookupError:
                            # Process already terminated
                            pass

                    cmd_process.transition_to(CommandState.KILLED)
                    self.metrics.record_execution(
                        cmd_process.command,
                        time.time() - cmd_process.start_time,
                        "killed",
                    )

                except Exception as e:
                    logger.error(f"Process termination error: {e}")

            # Close stdin
            try:
                if cmd_process.process.stdin:
                    cmd_process.process.stdin.close()
            except Exception:
                pass

            for thread in cmd_process.reader_threads:
                if thread.is_alive():
                    thread.join(timeout=2.0)

            with self._instance_lock:
                if command_id in self._instances:
                    del self._instances[command_id]

            logger.debug(f"Command {command_id} cleaned up")

        except Exception as e:
            logger.error(f"Cleanup error for {command_id}: {e}")

    def list_running_commands(self) -> Dict[str, Any]:
        """
        List all currently running commands.

        Returns:
            Dict with status and list of running commands with their details
        """
        with self._instance_lock:
            running_commands = []

            for cmd_id, cmd_process in self._instances.items():
                elapsed = time.time() - cmd_process.start_time

                command_info = {
                    "command_id": cmd_id,
                    "command": cmd_process.command,
                    "state": cmd_process.state.value,
                    "elapsed_seconds": round(elapsed, 3),
                    "working_dir": cmd_process.working_dir or "./",
                    "platform": cmd_process.platform,
                }

                # Add exit code if completed
                if cmd_process.exit_code is not None:
                    command_info["exit_code"] = cmd_process.exit_code

                running_commands.append(command_info)

        return {
            "status": "success",
            "count": len(running_commands),
            "commands": running_commands,
        }

    def terminate_command(self, command_id: str) -> Dict[str, Any]:
        """
        Terminate a running command by its ID.

        This is an alias for cleanup_command with clearer naming for external use.

        Args:
            command_id: Command identifier

        Returns:
            Dict with status and message
        """
        return self.cleanup_command(command_id)

    def get_metrics(self) -> Dict[str, Any]:
        """Get command execution metrics"""
        return self.metrics.get_report()

    def shutdown(self):
        """Shutdown service and cleanup all running commands"""
        logger.info("Shutting down CommandExecutionService")

        with self._instance_lock:
            command_ids = list(self._instances.keys())

        for cmd_id in command_ids:
            try:
                self._cleanup_command_internal(cmd_id)
            except Exception as e:
                logger.error(f"Cleanup failed for {cmd_id} during shutdown: {e}")

        logger.info("CommandExecutionService shutdown complete")
