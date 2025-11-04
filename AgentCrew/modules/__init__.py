import tempfile
from datetime import datetime
import os


from typing import TextIO, AnyStr


class FileLogIO(TextIO):
    """File-like object compatible with sys.stderr for MCP logging."""

    def __init__(self, file_format: str = "agentcrew"):
        log_dir_path = os.getenv("AGENTCREW_LOG_PATH", tempfile.gettempdir())
        os.makedirs(log_dir_path, exist_ok=True)
        self.log_path = (
            log_dir_path + f"/{file_format}_{datetime.now().timestamp()}.log"
        )
        self.file = open(self.log_path, "w+")

    def write(self, data: AnyStr) -> int:
        """Write data to the log file."""
        if isinstance(data, bytes):
            # Convert bytes to string for writing
            str_data = data.decode("utf-8", errors="replace")
        else:
            str_data = str(data)
        self.file.write(str_data)
        self.file.flush()  # Ensure data is written immediately
        return 0

    def flush(self):
        """Flush the file buffer."""
        self.file.flush()

    def close(self):
        """Close the file."""
        self.file.close()

    def fileno(self):
        """Return the file descriptor."""
        return self.file.fileno()
