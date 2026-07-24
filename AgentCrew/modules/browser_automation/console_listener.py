"""Background CDP console log listener with ring-buffer storage."""

import collections
import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

import PyChromeDevTools
import requests
import websocket
from loguru import logger

BUFFER_SIZE = 500
TAB_CHECK_INTERVAL_SECONDS = 5.0

CDP_LEVEL_MAP: dict[str, str] = {
    "verbose": "debug",
    "info": "info",
    "warning": "warn",
    "error": "error",
}


def _find_page_tab_index(host: str, port: int) -> int:
    try:
        tabs = requests.get(f"http://{host}:{port}/json", timeout=2).json()
        for i, tab in enumerate(tabs):
            if tab.get("type") == "page":
                return i
    except requests.RequestException:
        pass
    return 0


class ConsoleListener:
    """Captures CDP Log.entryAdded / Runtime.consoleAPICalled / Runtime.exceptionThrown
    on a dedicated ChromeInterface and stores them in a fixed-size ring buffer."""

    def __init__(self, debug_port: int = 9222):
        self._host = "localhost"
        self._port = debug_port

        self._interface: PyChromeDevTools.ChromeInterface | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._buffer: collections.deque = collections.deque(maxlen=BUFFER_SIZE)
        self._buffer_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._read_cursor = 0
        self._write_counter = 0
        self._current_tab_index: int | None = None
        self._last_tab_check_at = 0.0

    def start(self):
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._current_tab_index = None
            self._last_tab_check_at = 0.0
            try:
                self._interface = PyChromeDevTools.ChromeInterface(
                    host=self._host,
                    port=self._port,
                    auto_connect=False,
                    suppress_origin=True,
                )
                self._reconnect_interface(force_tab_refresh=True)
            except Exception as e:
                logger.warning(f"Failed to initialize console listener: {e}")
                self._close_interface()
                return

            self._thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="console-log-listener",
            )
            self._thread.start()
            logger.info("Console log listener started")

    def stop(self):
        self._stop_event.set()
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)
            self._thread = None
            self._close_interface()
            self._current_tab_index = None
            self._last_tab_check_at = 0.0

    def get_logs(
        self,
        limit: int = 50,
        levels: list[str] | None = None,
        since_last_read: bool = True,
    ) -> dict[str, Any]:
        """Return buffered console entries.

        When since_last_read=True, this method uses a single shared read cursor for the
        listener instance. Reads are global to the listener, not tracked per caller.
        Cursor snapshotting and advancement are performed under the same lock to keep
        the shared-consumer semantics thread-safe and internally consistent.
        """
        with self._buffer_lock:
            all_entries = list(self._buffer)
            current_write = self._write_counter

            if since_last_read:
                buffer_start = max(0, current_write - len(all_entries))
                start_offset = max(0, self._read_cursor - buffer_start)
                entries = all_entries[start_offset:]
                self._read_cursor = current_write
            else:
                entries = all_entries

        if levels:
            normalized = {lv.upper() for lv in levels}
            entries = [e for e in entries if e["level"] in normalized]

        entries = entries[-limit:] if len(entries) > limit else entries

        return {"success": True, "count": len(entries), "logs": entries}

    def clear_logs(self) -> dict[str, Any]:
        with self._buffer_lock:
            self._buffer.clear()
            self._write_counter = 0
            self._read_cursor = 0
        return {"success": True, "message": "Console log buffer cleared"}

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                if self._interface is None:
                    self._stop_event.wait(1.0)
                    continue

                if self._should_refresh_tab_index():
                    refreshed_tab_index = self._refresh_tab_index()
                    if refreshed_tab_index != self._current_tab_index:
                        self._reconnect_interface(force_tab_refresh=False)

                if not self._interface.ws:
                    self._reconnect_interface(force_tab_refresh=True)
                    continue

                self._interface.ws.settimeout(0.5)
                raw = self._interface.ws.recv()
                message = json.loads(raw)
                self._dispatch(message)

            except (TimeoutError, websocket.WebSocketTimeoutException):
                continue
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"Console listener error: {e}")
                    try:
                        self._reconnect_interface(force_tab_refresh=True)
                    except Exception as reconnect_error:
                        if not self._stop_event.is_set():
                            logger.warning(
                                f"Listener reconnect failed: {reconnect_error}"
                            )
                            self._stop_event.wait(1.0)

    def _should_refresh_tab_index(self) -> bool:
        return (
            time.monotonic() - self._last_tab_check_at
        ) >= TAB_CHECK_INTERVAL_SECONDS

    def _refresh_tab_index(self) -> int:
        self._last_tab_check_at = time.monotonic()
        return _find_page_tab_index(self._host, self._port)

    def _reconnect_interface(self, force_tab_refresh: bool):
        if self._interface is None:
            raise RuntimeError("Console listener interface is not initialized")

        if force_tab_refresh or self._current_tab_index is None:
            self._current_tab_index = self._refresh_tab_index()

        self._interface.connect(tab=self._current_tab_index)
        self._interface.Log.enable()
        self._interface.Runtime.enable()

    def _close_interface(self):
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                logger.debug("Failed to close CDP interface")
        self._interface = None

    def _dispatch(self, message: dict):
        method = message.get("method", "")
        params = message.get("params", {})

        if method == "Log.entryAdded":
            entry = params.get("entry", {})
            self._store(
                level=CDP_LEVEL_MAP.get(entry.get("level", ""), "log"),
                source=entry.get("source", "other"),
                text=entry.get("text", ""),
                url=entry.get("url", ""),
                line_number=entry.get("lineNumber"),
                timestamp=entry.get("timestamp"),
            )
        elif method == "Runtime.consoleAPICalled":
            args = params.get("args", [])
            text_parts = []
            for arg in args:
                val = arg.get("value")
                if val is not None:
                    text_parts.append(str(val))
                else:
                    text_parts.append(arg.get("description", arg.get("type", "")))

            stack = params.get("stackTrace", {})
            frames = stack.get("callFrames", [{}])
            top_frame = frames[0] if frames else {}

            self._store(
                level=CDP_LEVEL_MAP.get(params.get("type", ""), "log"),
                source="console-api",
                text=" ".join(text_parts),
                url=top_frame.get("url", "") if params.get("stackTrace") else "",
                line_number=top_frame.get("lineNumber")
                if params.get("stackTrace")
                else None,
                timestamp=params.get("timestamp"),
            )
        elif method == "Runtime.exceptionThrown":
            exc_details = params.get("exceptionDetails", {})
            exc_obj = exc_details.get("exception", {})
            text = exc_obj.get("description", exc_obj.get("value", str(exc_obj)))
            self._store(
                level="error",
                source="exception",
                text=str(text),
                url=exc_details.get("url", ""),
                line_number=exc_details.get("lineNumber"),
                timestamp=params.get("timestamp"),
            )

    def _store(
        self,
        level: str,
        source: str,
        text: str,
        url: str = "",
        line_number: int | None = None,
        timestamp: float | None = None,
    ):
        if timestamp:
            ts = datetime.fromtimestamp(timestamp / 1000, tz=UTC).isoformat()
        else:
            ts = datetime.now(tz=UTC).isoformat()

        entry = {
            "timestamp": ts,
            "level": level.upper(),
            "source": source,
            "text": text,
            "url": url,
            "lineNumber": line_number,
        }
        with self._buffer_lock:
            self._buffer.append(entry)
            self._write_counter += 1
