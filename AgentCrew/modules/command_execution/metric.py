import threading
from collections import defaultdict
from typing import Dict, Any


class CommandMetrics:
    """Track command execution metrics"""

    def __init__(self):
        self.total_executed = 0
        self.total_timeouts = 0
        self.total_errors = 0
        self.total_killed = 0
        self.avg_execution_time = 0.0
        self.command_frequency = defaultdict(int)
        self._lock = threading.Lock()

    def record_execution(self, command: str, duration: float, status: str):
        """Record command execution metrics"""
        with self._lock:
            self.total_executed += 1

            if status == "timeout":
                self.total_timeouts += 1
            elif status == "error":
                self.total_errors += 1
            elif status == "killed":
                self.total_killed += 1

            # Update moving average execution time
            if self.total_executed > 1:
                self.avg_execution_time = (
                    self.avg_execution_time * (self.total_executed - 1) + duration
                ) / self.total_executed
            else:
                self.avg_execution_time = duration

            # Track command frequency
            cmd_name = command.split()[0] if command else "unknown"
            self.command_frequency[cmd_name] += 1

    def get_report(self) -> Dict[str, Any]:
        """Get metrics report"""
        with self._lock:
            total = max(self.total_executed, 1)
            return {
                "total_executed": self.total_executed,
                "timeout_rate": self.total_timeouts / total,
                "error_rate": self.total_errors / total,
                "kill_rate": self.total_killed / total,
                "avg_execution_time_seconds": round(self.avg_execution_time, 2),
                "top_commands": sorted(
                    self.command_frequency.items(), key=lambda x: x[1], reverse=True
                )[:10],
            }
