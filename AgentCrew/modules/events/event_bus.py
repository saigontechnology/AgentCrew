"""
Typed, async-first event bus with sync backward compatibility.

Provides subscription-based application event dispatch with:
  - Filtered subscriptions (receive only matching events)
  - Priority ordering (high-priority handlers run first)
  - One-shot listeners (auto-remove after first dispatch)
  - Weak references (no manual detach needed)
  - Propagation control (raise StopPropagation to halt dispatch)
  - Mixed sync/async handlers
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


class StopPropagation(BaseException):
    """Raise from a handler to stop subsequent handlers from running."""


@dataclass
class Subscription:
    """Token returned by EventBus.on(). Use to unsubscribe."""

    event: str
    handler_id: int
    _bus: Any = field(repr=False)
    owner: str | None = None

    def unsubscribe(self) -> None:
        """Remove this subscription."""
        bus = self._bus()
        if bus is not None:
            bus.off(self)


class _WeakMethod:
    """Weak reference to a bound method — auto-cleanup when object is GC'd."""

    def __init__(self, method: Callable) -> None:
        try:
            self._self = weakref.ref(method.__self__)  # type: ignore[union-attr]
            self._func = method.__func__  # type: ignore[union-attr]
            self._is_bound = True
        except AttributeError:
            self._self = None
            self._func = method
            self._is_bound = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._is_bound:
            obj = self._self()  # type: ignore
            if obj is None:
                return None  # object was garbage collected
            return self._func(obj, *args, **kwargs)
        return self._func(*args, **kwargs)

    def dead(self) -> bool:
        return self._is_bound and self._self() is None  # type: ignore

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, _WeakMethod)
            and self._func == other._func
            and (not self._is_bound or self._self == other._self)
        )

    def __hash__(self) -> int:
        return hash((self._func, id(self._self) if self._is_bound else None))


@dataclass
class _HandlerEntry:
    """Internal handler registration."""

    handler: _WeakMethod | Callable
    priority: int = 0
    once: bool = False
    filter_func: Callable[[str, dict[str, Any]], bool] | None = None
    handler_id: int = 0
    owner: str | None = None

    @property
    def alive(self) -> bool:
        if isinstance(self.handler, _WeakMethod):
            return not self.handler.dead()
        return True


class EventBus:
    """
    Central event dispatch bus.

    This is a singleton-like infrastructure class (matching AgentManager/
    ServiceManager patterns) with optional constructor injection for testing.

    Usage:
        bus = EventBus.get_instance()

        # Subscribe
        sub = bus.on("tool_use", my_handler, priority=10)

        # Emit (async)
        await bus.emit("tool_use", name="navigate", input={...})

        # Emit (sync — runs sync handlers directly, schedules async ones)
        bus.emit_sync("system_message", message="Hello")

        # Unsubscribe
        sub.unsubscribe()
    """

    _instance: EventBus | None = None

    @classmethod
    def get_instance(cls) -> EventBus:
        """Get the singleton EventBus instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Clear the singleton (useful for testing)."""
        cls._instance = None

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[_HandlerEntry]] = {}
        self._handler_counter = 0
        self._lock = asyncio.Lock()

    # ── Subscription API ──

    def on(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        priority: int = 0,
        once: bool = False,
        filter_func: Callable[[str, dict[str, Any]], bool] | None = None,
        owner: str | None = None,
    ) -> Subscription:
        """
        Subscribe to an event.

        Args:
            event: Event name (from AppEvents).
            handler: Sync or async callable. Receives ``**data`` kwargs.
            priority: Higher = runs first. Default 0.
            once: Auto-unsubscribe after first dispatch.
            filter_func: Optional ``(event, data) → bool`` to filter dispatch.
            owner: Optional registration owner used for deterministic cleanup.

        Returns:
            Subscription token — call ``.unsubscribe()`` to remove.
        """
        wrapped: _WeakMethod | Callable
        if inspect.ismethod(handler):
            wrapped = _WeakMethod(handler)
        else:
            wrapped = handler

        self._handler_counter += 1
        entry = _HandlerEntry(
            handler=wrapped,
            priority=priority,
            once=once,
            filter_func=filter_func,
            handler_id=self._handler_counter,
            owner=owner,
        )
        self._subscriptions.setdefault(event, []).append(entry)
        self._subscriptions[event].sort(key=lambda e: e.priority, reverse=True)

        return Subscription(event, entry.handler_id, weakref.ref(self), owner)

    def off(self, sub: Subscription) -> None:
        """Remove a subscription by its token."""
        entries = self._subscriptions.get(sub.event, [])
        self._subscriptions[sub.event] = [
            e for e in entries if e.handler_id != sub.handler_id
        ]
        if not self._subscriptions[sub.event]:
            del self._subscriptions[sub.event]

    def off_owner(self, owner: str) -> None:
        """Remove every subscription registered by *owner*."""
        for event in list(self._subscriptions):
            remaining = [
                entry for entry in self._subscriptions[event] if entry.owner != owner
            ]
            if remaining:
                self._subscriptions[event] = remaining
            else:
                del self._subscriptions[event]

    def has_subscribers(self, event: str) -> bool:
        """Check whether any alive handlers are registered for *event*."""
        return bool(self._get_alive_handlers(event))

    # ── Emit API ──

    async def emit(self, event: str, **data: Any) -> None:
        """
        Async emit — runs all handlers in priority order.

        - Async handlers are awaited.
        - Sync handlers are offloaded to ``asyncio.to_thread``.
        - Dead weak references are pruned during iteration.
        """
        entries = self._get_alive_handlers(event)
        if not entries:
            return

        for entry in entries:
            if entry.filter_func and not entry.filter_func(event, data):
                continue

            try:
                if inspect.iscoroutinefunction(self._unwrap(entry.handler)):
                    await entry.handler(**data)
                else:
                    await asyncio.to_thread(entry.handler, **data)
            except StopPropagation:
                break
            except Exception:
                logger.exception("Event handler error for %s", event)

        self._prune_once(event)

    def emit_sync(self, event: str, **data: Any) -> None:
        """
        Sync emit — runs sync handlers immediately.

        Async handlers are scheduled on the running event loop (if one exists)
        or skipped with a warning.
        """
        entries = self._get_alive_handlers(event)
        if not entries:
            return

        for entry in entries:
            if entry.filter_func and not entry.filter_func(event, data):
                continue

            try:
                if inspect.iscoroutinefunction(self._unwrap(entry.handler)):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(entry.handler(**data))
                    except RuntimeError:
                        logger.warning(
                            "Skipping async handler %r for sync emit of %s "
                            "(no running event loop)",
                            entry.handler,
                            event,
                        )
                else:
                    entry.handler(**data)
            except StopPropagation:
                break
            except Exception:
                logger.exception("Event handler error (sync) for %s", event)

        self._prune_once(event)

    # ── Internal helpers ──

    def _get_alive_handlers(self, event: str) -> list[_HandlerEntry]:
        """Get handlers for *event*, pruning dead weak references."""
        entries = self._subscriptions.get(event, [])
        alive = []
        for e in entries:
            if e.alive:
                alive.append(e)
        if len(alive) != len(entries):
            if alive:
                self._subscriptions[event] = alive
            else:
                self._subscriptions.pop(event, None)
        return alive

    def _prune_once(self, event: str) -> None:
        """Remove one-shot handlers after dispatch."""
        entries = self._subscriptions.get(event, [])
        if not entries:
            return
        self._subscriptions[event] = [e for e in entries if not e.once]
        if not self._subscriptions[event]:
            del self._subscriptions[event]

    @staticmethod
    def _unwrap(handler: _WeakMethod | Callable) -> Callable:
        if isinstance(handler, _WeakMethod):
            return handler
        return handler
