"""Application-owned asyncio loop for interactive AgentCrew modes."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine
from contextvars import ContextVar
from typing import Any, TypeVar

T = TypeVar("T")
_current_runtime: ContextVar[InteractiveAsyncRuntime | None] = ContextVar(
    "interactive_runtime", default=None
)


class _DrainTimeout(TimeoutError):
    pass


class InteractiveAsyncRuntime:
    """Keep async clients on one continuously running, explicitly owned loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._state = "new"
        self._error: BaseException | None = None
        self._shutdown_future: concurrent.futures.Future | None = None
        self._operations: dict[concurrent.futures.Future, asyncio.Task] = {}

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._state not in ("running", "closing"):
                raise RuntimeError("Interactive async runtime is not running")
            return self._loop

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_owner_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def start(self) -> None:
        with self._lock:
            if self._state == "new":
                self._state = "starting"
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="AgentCrew-InteractiveRuntime",
                    daemon=False,
                )
                self._thread.start()
            elif self._state not in ("starting", "running"):
                raise RuntimeError("Interactive async runtime cannot be restarted")
        if not self._ready.wait(timeout=10):
            raise TimeoutError("Interactive async runtime failed to start")
        if self._error is not None:
            raise RuntimeError(
                "Interactive async runtime failed to start"
            ) from self._error

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit from a synchronous caller without blocking the owner loop."""
        if self.is_owner_thread():
            coro.close()
            raise RuntimeError(
                "Cannot block the interactive runtime loop; await instead"
            )
        future = self.submit(coro)
        try:
            return future.result()
        except BaseException:
            future.cancel()
            raise

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Submit one cancellable operation without serializing other callers."""
        future: concurrent.futures.Future[T] = concurrent.futures.Future()
        with self._lock:
            if self._state != "running" or self._loop is None:
                coro.close()
                raise RuntimeError("Interactive async runtime is not accepting work")
            loop = self._loop
            loop.call_soon_threadsafe(self._schedule, coro, future)
        future.add_done_callback(
            lambda result: (
                loop.call_soon_threadsafe(self._cancel, result)
                if result.cancelled() and loop.is_running()
                else None
            )
        )
        return future

    def _schedule(self, coro: Coroutine, future: concurrent.futures.Future) -> None:
        if future.cancelled():
            coro.close()
            return
        token = _current_runtime.set(self)
        try:
            task = asyncio.create_task(coro)
        finally:
            _current_runtime.reset(token)
        self._operations[future] = task
        task.add_done_callback(lambda result: self._complete(future, result))

    def _cancel(self, future: concurrent.futures.Future) -> None:
        task = self._operations.get(future)
        if task is not None:
            task.cancel()

    def _complete(self, future: concurrent.futures.Future, task: asyncio.Task) -> None:
        self._operations.pop(future, None)
        try:
            result = task.result()
        except asyncio.CancelledError:
            future.cancel()
        except BaseException as exc:
            if not future.done():
                try:
                    future.set_exception(exc)
                except concurrent.futures.InvalidStateError:
                    pass
        else:
            if not future.done():
                try:
                    future.set_result(result)
                except concurrent.futures.InvalidStateError:
                    pass

    async def _drain(self, timeout: float, *, cancel_initial: bool = True) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        known_operations = set(self._operations.values())
        while True:
            tasks = asyncio.all_tasks() - {asyncio.current_task()}
            if not tasks:
                return
            if cancel_initial:
                for task in tasks:
                    task.cancel()
                cancel_initial = False
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _DrainTimeout(
                    "Interactive operations did not finish cancellation"
                )
            done, _ = await asyncio.wait(tasks, timeout=min(remaining, 0.05))
            for task in done:
                if not task.cancelled():
                    error = task.exception()
                    if error is not None and task not in known_operations:
                        asyncio.get_running_loop().call_exception_handler(
                            {
                                "message": "Interactive background cleanup failed",
                                "exception": error,
                            }
                        )

    async def _finish(self, finalizer: Coroutine | None, timeout: float) -> None:
        token = _current_runtime.set(self)
        try:
            await self._drain(timeout)
            if finalizer is not None:
                cleanup = asyncio.create_task(finalizer)
                finalizer = None
                _, pending = await asyncio.wait({cleanup}, timeout=timeout)
                if pending:
                    cleanup.cancel()
                    await self._drain(timeout, cancel_initial=False)
                    raise TimeoutError("Interactive application cleanup timed out")
                try:
                    await cleanup
                finally:
                    await self._drain(timeout, cancel_initial=False)
        finally:
            _current_runtime.reset(token)
            if finalizer is not None:
                finalizer.close()

    def _stop_after_shutdown(self, future: concurrent.futures.Future) -> None:
        if future.cancelled() or not isinstance(future.exception(), _DrainTimeout):
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._loop.stop)

    def shutdown(self, finalizer: Coroutine | None = None, timeout: float = 30) -> None:
        """Drain operations and finalize clients before stopping the owner loop."""
        if self.is_owner_thread():
            if finalizer is not None:
                finalizer.close()
            raise RuntimeError("Cannot shut down the runtime from its owner thread")
        with self._lock:
            if self._state == "closed":
                if finalizer is not None:
                    finalizer.close()
                return
            if self._state not in ("running", "closing") or self._loop is None:
                if finalizer is not None:
                    finalizer.close()
                raise RuntimeError("Interactive runtime is not ready for shutdown")
            if self._shutdown_future is not None and not self._shutdown_future.done():
                if finalizer is not None:
                    finalizer.close()
                raise RuntimeError(
                    "Interactive runtime shutdown is already in progress"
                )
            self._state = "closing"
            loop = self._loop
            future = asyncio.run_coroutine_threadsafe(
                self._finish(finalizer, timeout), loop
            )
            self._shutdown_future = future
            future.add_done_callback(self._stop_after_shutdown)
        try:
            future.result(timeout=timeout * 3 + 1)
        finally:
            if future.done() and (
                future.cancelled() or not isinstance(future.exception(), _DrainTimeout)
            ):
                assert self._thread is not None
                self._thread.join(timeout=timeout)
                if self._thread.is_alive():
                    raise TimeoutError("Interactive runtime thread did not stop")
        if self._error is not None:
            raise RuntimeError("Interactive runtime thread failed") from self._error

    def _thread_main(self) -> None:
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._lock:
                self._loop = loop
                self._state = "running"
            loop.call_soon(self._ready.set)
            loop.run_forever()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor(timeout=5))
        except BaseException as exc:
            self._error = exc
        finally:
            if loop is not None:
                loop.close()
            with self._lock:
                self._state = "closed"
            self._ready.set()


def try_get_interactive_runtime() -> InteractiveAsyncRuntime | None:
    """Return only the runtime explicitly propagated by a submitted operation."""
    return _current_runtime.get()


def get_interactive_runtime() -> InteractiveAsyncRuntime:
    """Resolve the current operation's runtime without creating a global owner."""
    runtime = try_get_interactive_runtime()
    if runtime is None:
        raise RuntimeError("No interactive runtime was supplied")
    return runtime
