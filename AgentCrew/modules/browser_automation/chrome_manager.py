"""
Chrome browser process management for browser automation.

Adapted from the PoC implementation to manage Chrome browser instances
with DevTools Protocol support.
"""

import os
import signal
import atexit
import subprocess
import threading
import time
import platform
from typing import Optional

from loguru import logger


class ChromeManager:
    """Manages Chrome browser process lifecycle for automation."""

    def __init__(
        self,
        debug_port: int = 9222,
    ):
        """
        Initialize Chrome manager.

        Args:
            debug_port: Port for Chrome DevTools Protocol
            user_data_dir: Directory for Chrome user data storage
        """
        self.debug_port = debug_port
        self.chrome_process: Optional[subprocess.Popen] = None
        self.chrome_thread: Optional[threading.Thread] = None
        self._shutdown = False
        self._user_data_dir = os.path.join(
            os.getenv("AGENTCREW_PERSISTENCE_DIR", "./"), "chrome_user_data"
        )
        self._is_windows = platform.system() == "Windows"

        # Register cleanup on exit
        atexit.register(self.cleanup)

    def _find_chrome_executable(self) -> str:
        """
        Find Chrome/Chromium executable path for current OS.

        Returns:
            Path to Chrome executable

        Raises:
            FileNotFoundError: If Chrome executable not found
        """
        if self._is_windows:
            return self._find_chrome_windows()
        else:
            return self._find_chrome_unix()

    def _find_chrome_windows(self) -> str:
        """Find Chrome executable on Windows."""
        # Common Chrome installation paths on Windows
        possible_paths = [
            # Chrome stable paths
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            # Chrome dev/canary paths
            os.path.expandvars(
                r"%PROGRAMFILES%\Google\Chrome Dev\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%PROGRAMFILES(X86)%\Google\Chrome Dev\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome Dev\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome SxS\Application\chrome.exe"
            ),
            # Edge paths (as Chromium alternative)
            os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(
                r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"
            ),
            # Chromium paths
            os.path.expandvars(r"%PROGRAMFILES%\Chromium\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Chromium\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Found Chrome executable at: {path}")
                return path

        # Try to find via 'where' command (Windows equivalent of 'which')
        try:
            result = subprocess.run(
                ["where", "chrome"], capture_output=True, text=True, check=True
            )
            path = result.stdout.strip().split("\n")[0]  # Get first result
            if os.path.exists(path):
                logger.info(f"Found Chrome via 'where' command: {path}")
                return path
        except subprocess.CalledProcessError:
            pass

        try:
            result = subprocess.run(
                ["where", "msedge"], capture_output=True, text=True, check=True
            )
            path = result.stdout.strip().split("\n")[0]  # Get first result
            if os.path.exists(path):
                logger.info(f"Found Edge via 'where' command: {path}")
                return path
        except subprocess.CalledProcessError:
            pass

        raise FileNotFoundError("Chrome/Chromium/Edge executable not found on Windows")

    def _find_chrome_unix(self) -> str:
        """Find Chrome executable on Unix-like systems (Linux, macOS)."""
        # Unix paths (Linux, macOS)
        possible_paths = [
            # Linux paths
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
            "/opt/google/chrome/chrome",
            # macOS paths
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]

        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Found Chrome executable at: {path}")
                return path

        # Try to find via which command
        try:
            result = subprocess.run(
                ["which", "google-chrome"], capture_output=True, text=True, check=True
            )
            path = result.stdout.strip()
            if os.path.exists(path):
                logger.info(f"Found Chrome via 'which' command: {path}")
                return path
        except subprocess.CalledProcessError:
            pass

        try:
            result = subprocess.run(
                ["which", "chromium"], capture_output=True, text=True, check=True
            )
            path = result.stdout.strip()
            if os.path.exists(path):
                logger.info(f"Found Chromium via 'which' command: {path}")
                return path
        except subprocess.CalledProcessError:
            pass

        raise FileNotFoundError("Chrome/Chromium executable not found on Unix")

    def _start_chrome_process(self, profile: str = "Default"):
        """Start Chrome with remote debugging in a separate process."""
        try:
            chrome_executable = self._find_chrome_executable()
            logger.info(f"Starting Chrome from: {chrome_executable}")

            if not os.path.exists(self._user_data_dir):
                os.makedirs(self._user_data_dir, exist_ok=True)

            is_headless = os.getenv("AGENTCREW_DISABLE_GUI", "") == "true"
            is_docker = os.getenv("AGENTCREW_DOCKER", "") == "true"

            chrome_args = [
                chrome_executable,
                f"--remote-debugging-port={self.debug_port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=TranslateUI",
                "--disable-new-avatar-menu",
                "--remote-allow-origins='*'",
                "--allow-file-access-from-files",
                f"--user-data-dir={self._user_data_dir}",
                f"--profile-directory={profile}",
            ]
            if is_headless:
                chrome_args.append("--headless")
            if is_docker:
                chrome_args.append("--no-sandbox")

            # Platform-specific process creation
            if self._is_windows:
                # Windows: Use CREATE_NEW_PROCESS_GROUP to allow graceful termination
                self.chrome_process = subprocess.Popen(
                    chrome_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore
                )
            else:
                # Unix: Use process session for process group management
                self.chrome_process = subprocess.Popen(
                    chrome_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid,
                )

            time.sleep(2)

            if self.chrome_process.poll() is not None:
                stdout, stderr = self.chrome_process.communicate()
                logger.error(f"Chrome failed to start. Error: {stderr.decode()}")
                logger.error(f"Chrome stdout: {stdout.decode()}")

        except Exception as e:
            logger.error(f"Error starting Chrome: {e}")

    def start_chrome_thread(self, profile: str = "Default"):
        """Start Chrome in a separate thread."""
        if self.chrome_thread and self.chrome_thread.is_alive():
            return

        self.chrome_thread = threading.Thread(
            target=self._start_chrome_process,
            args=(profile,),
            daemon=True,
            name="ChromeDebugProcess",
        )
        self.chrome_thread.start()

        # Wait a bit for Chrome to fully initialize
        time.sleep(3)

    def is_chrome_running(self) -> bool:
        """Check if Chrome process is still running."""
        return self.chrome_process is not None and self.chrome_process.poll() is None

    def cleanup(self):
        """Clean up Chrome process using OS-appropriate methods."""
        if self._shutdown:
            return

        self._shutdown = True

        if self.chrome_process and self.chrome_process.poll() is None:
            try:
                if self._is_windows:
                    self._cleanup_windows()
                else:
                    self._cleanup_unix()
            except (ProcessLookupError, OSError) as e:
                logger.warning(f"Chrome process cleanup: {e}")

    def _cleanup_windows(self):
        """Windows-specific cleanup using taskkill or terminate."""
        try:
            # First try graceful termination
            if self.chrome_process is None:
                return
            self.chrome_process.terminate()
            try:
                self.chrome_process.wait(timeout=5)
                logger.info("Chrome process terminated gracefully")
                return
            except subprocess.TimeoutExpired:
                logger.warning("Chrome did not terminate gracefully, forcing kill")

            # Force kill using taskkill command
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.chrome_process.pid)],
                    check=True,
                    capture_output=True,
                )
                logger.info("Chrome process force-killed via taskkill")
            except subprocess.CalledProcessError:
                # Fallback to process.kill()
                self.chrome_process.kill()
                self.chrome_process.wait()
                logger.info("Chrome process killed via process.kill()")

        except Exception as e:
            logger.error(f"Windows Chrome cleanup error: {e}")

    def _cleanup_unix(self):
        """Unix-specific cleanup using process groups and signals."""
        try:
            if self.chrome_process is None:
                return
            # Send SIGTERM to the process group
            os.killpg(os.getpgid(self.chrome_process.pid), signal.SIGTERM)

            try:
                self.chrome_process.wait(timeout=5)
                logger.info("Chrome process group terminated gracefully")
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Chrome process group did not terminate, sending SIGKILL"
                )
                # Force kill with SIGKILL
                os.killpg(os.getpgid(self.chrome_process.pid), signal.SIGKILL)
                self.chrome_process.wait()
                logger.info("Chrome process group force-killed")

        except Exception as e:
            logger.error(f"Unix Chrome cleanup error: {e}")
