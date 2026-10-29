"""Application-scoped asyncio loop for interactive AgentCrew modes."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from loguru import logger

T = TypeVar("T")


class InteractiveAsyncRuntime:
    """Dedicated thread hosting one long-lived asyncio event loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._shutdown_lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        self._ensure_started()
        assert self._loop is not None
        return self._loop

    def is_alive(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._loop is not None
            and self._loop.is_running()
        )

    def is_owner_thread(self) -> bool:
        return self._thread_id is not None and threading.get_ident() == self._thread_id

    def start(self) -> None:
        with self._shutdown_lock:
            if self.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="AgentCrew-InteractiveRuntime",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=60):
            raise RuntimeError("Interactive async runtime failed to start")

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run ``coro`` on the runtime loop, blocking the calling thread."""
        loop = self.loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise RuntimeError(
                "InteractiveAsyncRuntime.run() cannot be invoked on the runtime loop; "
                "await the coroutine instead"
            )
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def submit(
        self, coro: Coroutine[Any, Any, T]
    ) -> concurrent.futures.Future[T]:
        """Schedule ``coro`` on the runtime loop without serializing other submitters."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self) -> None:
        with self._shutdown_lock:
            loop = self._loop
            thread = self._thread
            if loop is None or not loop.is_running():
                self._loop = None
                self._thread = None
                self._thread_id = None
                return
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._drain_and_stop(), loop
                )
                future.result(timeout=120)
            except Exception as exc:
                logger.warning(f"Interactive async runtime shutdown error: {exc}")
            if thread is not None:
                thread.join(timeout=120)
            self._loop = None
            self._thread = None
            self._thread_id = None

    def _ensure_started(self) -> None:
        if not self.is_alive():
            raise RuntimeError(
                "Interactive async runtime is not running; call start() first"
            )

    def _thread_main(self) -> None:
        self._thread_id = threading.get_ident()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()

    async def _drain_and_stop(self) -> None:
        loop = asyncio.get_running_loop()
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks(loop) if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await loop.shutdown_asyncgens()
        loop.stop()


_runtime: InteractiveAsyncRuntime | None = None
_runtime_lock = threading.Lock()


def get_interactive_runtime() -> InteractiveAsyncRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = InteractiveAsyncRuntime()
        return _runtime


def try_get_interactive_runtime() -> InteractiveAsyncRuntime | None:
    with _runtime_lock:
        if _runtime is None or not _runtime.is_alive():
            return None
        return _runtime
