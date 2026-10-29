"""Bridge sync callers onto the interactive application asyncio runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger

from .interactive_async_runtime import try_get_interactive_runtime


def run_interactive[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` on the interactive runtime, starting it when needed."""
    runtime = try_get_interactive_runtime()
    if runtime is None:
        from .interactive_async_runtime import get_interactive_runtime

        runtime = get_interactive_runtime()
        runtime.start()
    return runtime.run(coro)


def run_async_bridge[T](coro: Coroutine[Any, Any, T]) -> T | None:
    """Run async work from sync code, preferring the interactive runtime."""
    runtime = try_get_interactive_runtime()
    if runtime is not None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            return runtime.run(coro)
        if running is runtime.loop:
            logger.warning(
                "Cannot run async bridge on the interactive runtime loop from sync code; "
                "returning without running the coroutine"
            )
            return None
        return runtime.run(coro)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    logger.warning(
        "Cannot run asyncio.run() inside a running event loop; "
        "returning without running the coroutine"
    )
    return None
