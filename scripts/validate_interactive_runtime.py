"""Focused validation for the interactive asyncio runtime (not pytest)."""

from __future__ import annotations

import asyncio
import gc
import sys
import threading
import warnings

import httpx2

from AgentCrew.modules.events.event_bus import EventBus
from AgentCrew.modules.runtime import get_interactive_runtime, run_interactive
from AgentCrew.modules.unraisable_hook import custom_unraisable_hook


def _reset_runtime() -> None:
    runtime = get_interactive_runtime()
    if runtime.is_alive():
        runtime.shutdown()


def validate_eventbus_nested_reentry() -> None:
    _reset_runtime()
    runtime = get_interactive_runtime()
    runtime.start()
    bus = EventBus()
    log: list[str] = []

    async def async_starter(**_kwargs) -> None:
        log.append("async_starter")
        await bus.emit("nested", token=1)

    def sync_nested(**_kwargs) -> None:
        log.append("sync_nested")

        async def inner() -> None:
            log.append("inner_async")

        run_interactive(inner())

    bus.on("starter", async_starter)
    bus.on("nested", sync_nested)

    async def main() -> None:
        await bus.emit("starter")

    run_interactive(main())
    runtime.shutdown()
    assert log == ["async_starter", "sync_nested", "inner_async"], log


def validate_owner_thread_guard() -> None:
    _reset_runtime()
    runtime = get_interactive_runtime()
    runtime.start()

    async def attempt_nested_run() -> None:
        async def noop() -> None:
            return

        coro = noop()
        try:
            runtime.run(coro)
        except RuntimeError as exc:
            if "runtime loop" in str(exc):
                coro.close()
                return
            coro.close()
            raise
        coro.close()
        raise AssertionError("expected RuntimeError for nested runtime.run()")

    run_interactive(attempt_nested_run())
    assert runtime.is_owner_thread() is False
    runtime.shutdown()


def validate_cancellation() -> None:
    _reset_runtime()
    runtime = get_interactive_runtime()
    runtime.start()
    cancelled = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def cancel_from_loop() -> None:
        task = asyncio.create_task(work())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run_interactive(cancel_from_loop())
    assert cancelled.is_set()
    runtime.shutdown()


def validate_two_streams_same_loop_close() -> None:
    _reset_runtime()
    runtime = get_interactive_runtime()
    runtime.start()
    loops: list[asyncio.AbstractEventLoop] = []

    async def one_stream() -> None:
        loops.append(asyncio.get_running_loop())
        async with httpx2.AsyncClient() as client:
            await client.get("https://example.com", timeout=10)

    run_interactive(one_stream())
    run_interactive(one_stream())
    assert len(loops) == 2
    assert loops[0] is loops[1]
    runtime.shutdown()


def validate_keepalive_two_streams() -> None:
    _reset_runtime()
    runtime = get_interactive_runtime()
    runtime.start()
    unraisable: list[BaseException] = []
    previous = sys.unraisablehook

    def capture(unraisable_arg) -> None:
        if unraisable_arg.exc_value is not None:
            unraisable.append(unraisable_arg.exc_value)
        custom_unraisable_hook(unraisable_arg)

    sys.unraisablehook = capture

    async def two_requests_one_client() -> None:
        async with httpx2.AsyncClient(http2=False) as client:
            for _ in range(2):
                response = await client.get("https://example.com", timeout=15)
                response.raise_for_status()

    try:
        run_interactive(two_requests_one_client())
    finally:
        sys.unraisablehook = previous
        runtime.shutdown()
        gc.collect()

    loop_closed = [exc for exc in unraisable if "Event loop is closed" in str(exc)]
    if loop_closed:
        raise AssertionError(f"unexpected loop-closed unraisable: {loop_closed!r}")


def validate_concurrent_submit() -> None:
    _reset_runtime()
    runtime = get_interactive_runtime()
    runtime.start()
    barrier = threading.Barrier(2)
    results: list[int] = []

    async def mark(value: int) -> None:
        results.append(value)

    def worker(value: int) -> None:
        barrier.wait(timeout=5)
        runtime.submit(mark(value)).result()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    runtime.shutdown()
    assert sorted(results) == [1, 2]


def main() -> None:
    validate_eventbus_nested_reentry()
    validate_owner_thread_guard()
    validate_cancellation()
    validate_two_streams_same_loop_close()
    validate_keepalive_two_streams()
    validate_concurrent_submit()
    print("validate_interactive_runtime: OK")


if __name__ == "__main__":
    main()
