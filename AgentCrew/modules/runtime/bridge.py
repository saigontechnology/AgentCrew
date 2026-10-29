"""Compatibility bridge for sync utilities called by async operations."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger

from .interactive_async_runtime import try_get_interactive_runtime


def run_interactive[T](coro: Coroutine[Any, Any, T]) -> T:
    """Use the explicitly propagated runtime, or the legacy sync boundary."""
    runtime = try_get_interactive_runtime()
    if runtime is not None:
        return runtime.run(coro)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("Cannot block a running event loop; await the coroutine instead")


def run_async_bridge[T](coro: Coroutine[Any, Any, T]) -> T | None:
    """Preserve conversion-only fallback when called on an event-loop thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run_interactive(coro)
    coro.close()
    logger.warning(
        "Cannot block a running event loop; returning file content without image descriptions"
    )
    return None
