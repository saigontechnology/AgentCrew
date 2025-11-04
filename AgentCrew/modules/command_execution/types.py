import queue
import threading
import subprocess
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass, field

from loguru import logger


class CommandState(Enum):
    """Command lifecycle states"""

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETING = "completing"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    KILLED = "killed"


@dataclass
class CommandProcess:
    """Represents a running command with its process and metadata"""

    id: str
    command: str
    process: subprocess.Popen
    platform: str
    start_time: float
    output_queue: queue.Queue = field(default_factory=queue.Queue)
    error_queue: queue.Queue = field(default_factory=queue.Queue)
    state: CommandState = CommandState.QUEUED
    exit_code: Optional[int] = None
    reader_threads: List[threading.Thread] = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)
    total_output_size: int = 0
    working_dir: Optional[str] = None

    def transition_to(self, new_state: CommandState):
        """Transition to new state with validation"""
        valid_transitions = {
            CommandState.QUEUED: [CommandState.STARTING, CommandState.ERROR],
            CommandState.STARTING: [CommandState.RUNNING, CommandState.ERROR],
            CommandState.RUNNING: [
                CommandState.WAITING_INPUT,
                CommandState.COMPLETING,
                CommandState.TIMEOUT,
                CommandState.ERROR,
                CommandState.KILLED,
            ],
            CommandState.WAITING_INPUT: [
                CommandState.RUNNING,
                CommandState.COMPLETING,
                CommandState.TIMEOUT,
                CommandState.ERROR,
                CommandState.KILLED,
            ],
            CommandState.COMPLETING: [CommandState.COMPLETED, CommandState.ERROR],
            CommandState.COMPLETED: [],
            CommandState.TIMEOUT: [],
            CommandState.ERROR: [],
            CommandState.KILLED: [],
        }

        if new_state not in valid_transitions.get(self.state, []):
            logger.warning(
                f"Invalid state transition: {self.state.value} -> {new_state.value}"
            )
            return

        logger.debug(f"Command {self.id}: {self.state.value} -> {new_state.value}")
        self.state = new_state
