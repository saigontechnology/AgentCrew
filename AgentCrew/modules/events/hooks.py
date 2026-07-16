"""
Lifecycle hook system for application operations.

Hooks extend plain events with before/after semantics:
  - **before**: Receives context, can modify and return it, or return None to cancel
  - **after**: Receives context and a result envelope, and can modify the result

This is the foundation for the plugin system — plugins register hooks
to intercept and extend application behaviour at well-defined points.
"""

from __future__ import annotations

import inspect
from loguru import logger
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class CancelOperation(BaseException):
    """Raise from a 'before' hook to cancel the wrapped operation."""


class HookPhase(str, Enum):
    BEFORE = "before"
    AFTER = "after"


# ──────────────────────────────────────────────
#  Lifecycle point definitions
# ──────────────────────────────────────────────


class HookPoints:
    """
    Well-known lifecycle points where hooks can be registered.

    Naming convention: ``<domain>.<action>``
    """

    # ── Tool execution ────────────────────────
    TOOL_EXECUTE = "tool.execute"

    # ── Agent processing ──────────────────────
    AGENT_PROCESS = "agent.process"

    # ── User message lifecycle ────────────────
    USER_MESSAGE = "user.message"

    # ── Response lifecycle ────────────────────
    RESPONSE_COMPLETE = "response.complete"

    # ── Memory lifecycle ──────────────────────
    MEMORY_STORE = "memory.store"
    MEMORY_RETRIEVE = "memory.retrieve"

    # ── Context building ──────────────────────
    CONTEXT_BUILD = "context.build"

    # ── Agent transfer / delegate ─────────────
    AGENT_TRANSFER = "agent.transfer"
    AGENT_DELEGATE = "agent.delegate"


@dataclass
class HookRegistration:
    """Token identifying one registered hook."""

    key: str
    registration_id: int
    owner: str | None = None


@dataclass
class Hook:
    """
    A single hook registration.

    Args:
        point: HookPoints constant (e.g. ``"tool.execute"``)
        phase: ``"before"`` | ``"after"``
        handler: Callable with signature determined by phase (see below)
        priority: Higher = runs first. Default 0.
        description: Optional human-readable description for debugging.

    Handler signatures by phase:

    **before** ``(context: dict) -> dict | None``
        Receives a mutable context dict. Return modified dict to continue,
        or None / raise CancelOperation to abort.

    **after** ``(context: dict, result: Any) -> Any``
        Receives context + result from the wrapped operation. Return modified
        result (or the same result).
    """

    point: str
    phase: HookPhase | str
    handler: Callable[..., Any]
    priority: int = 0
    description: str = ""
    owner: str | None = None
    registration_id: int = 0


class HookRegistry:
    """Manages before/after hooks at named lifecycle points."""

    _instance: HookRegistry | None = None

    def __new__(cls) -> HookRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._hooks: dict[str, list[Hook]] = {}
        self._registration_counter = 0
        self._initialized = True

    @classmethod
    def get_instance(cls) -> HookRegistry:
        """Get the singleton HookRegistry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Clear and discard the singleton registry."""
        if cls._instance is not None:
            cls._instance.clear()
        cls._instance = None

    # ── Registration ──

    @staticmethod
    def _phase_value(phase: HookPhase | str) -> str:
        phase_value = phase.value if isinstance(phase, HookPhase) else phase
        supported = {item.value for item in HookPhase}
        if phase_value not in supported:
            raise ValueError(
                f"Unsupported hook phase {phase_value!r}; expected one of {sorted(supported)}"
            )
        return phase_value

    def register(
        self,
        hook: Hook,
        *,
        owner: str | None = None,
    ) -> HookRegistration:
        """Register a hook at a lifecycle point."""
        phase = self._phase_value(hook.phase)
        key = f"{hook.point}.{phase}"
        self._registration_counter += 1
        hook.owner = owner if owner is not None else hook.owner
        hook.registration_id = self._registration_counter

        self._hooks.setdefault(key, []).append(hook)
        self._hooks[key].sort(key=lambda h: h.priority, reverse=True)
        logger.debug("Hook registered: %s (prio=%d)", key, hook.priority)
        return HookRegistration(key, hook.registration_id, hook.owner)

    def unregister_registration(self, registration: HookRegistration) -> None:
        """Remove a hook by its registration token."""
        hooks = self._hooks.get(registration.key, [])
        remaining = [
            hook
            for hook in hooks
            if hook.registration_id != registration.registration_id
        ]
        if remaining:
            self._hooks[registration.key] = remaining
        else:
            self._hooks.pop(registration.key, None)

    def unregister_owner(self, owner: str) -> None:
        """Remove every hook registered by *owner*."""
        for key in list(self._hooks):
            remaining = [hook for hook in self._hooks[key] if hook.owner != owner]
            if remaining:
                self._hooks[key] = remaining
            else:
                del self._hooks[key]

    def unregister(self, point: str, phase: HookPhase | str, handler: Callable) -> None:
        """Remove a previously registered hook."""
        phase_str = self._phase_value(phase)
        key = f"{point}.{phase_str}"
        self._hooks[key] = [h for h in self._hooks.get(key, []) if h.handler != handler]

    # ── Runtime invocation ──

    async def run_before(
        self,
        point: str,
        **context: Any,
    ) -> dict[str, Any] | None:
        """
        Run all 'before' hooks for *point*.

        Each hook receives the context dict and should return a (possibly
        modified) dict. If any hook returns None or raises CancelOperation,
        the chain stops and None is returned to signal cancellation.

        Returns the final (modified) context dict, or None if cancelled.
        """
        key = f"{point}.{HookPhase.BEFORE.value}"
        hooks = list(self._hooks.get(key, []))
        if not hooks:
            return context

        ctx = dict(context)
        for hook in hooks:
            try:
                result = hook.handler(ctx)
                if inspect.isawaitable(result):
                    result = await result
                if result is None:
                    logger.info(
                        "Hook %s cancelled operation at %s before",
                        hook.description or hook.handler,
                        point,
                    )
                    return None
                ctx = dict(result)  # allow handler to return a modified copy
            except CancelOperation:
                logger.info(
                    "Hook %s cancelled operation (CancelOperation) at %s",
                    hook.description or hook.handler,
                    point,
                )
                return None
            except Exception:
                logger.exception("Hook error in %s before: %s", point, hook.handler)
        return ctx

    async def run_after(
        self,
        point: str,
        result: Any = None,
        **context: Any,
    ) -> Any:
        """
        Run all 'after' hooks for *point*.

        Each hook receives ``(context, result)`` and should return the
        (possibly modified) result. Hooks run in priority order.

        Returns the final result.
        """
        key = f"{point}.{HookPhase.AFTER.value}"
        hooks = list(self._hooks.get(key, []))
        if not hooks:
            return result

        ctx = dict(context)
        current = result
        for hook in hooks:
            try:
                out = hook.handler(ctx, current)
                if inspect.isawaitable(out):
                    out = await out
                current = out
            except Exception:
                logger.exception("Hook error in %s after: %s", point, hook.handler)
        return current

    # ── Introspection ──

    def get_hooks(self, point: str | None = None) -> list[Hook]:
        """List all registered hooks, optionally filtered by lifecycle point."""
        if point:
            result = []
            for phase in HookPhase:
                result.extend(self._hooks.get(f"{point}.{phase.value}", []))
            return result
        all_hooks = []
        for hooks in self._hooks.values():
            all_hooks.extend(hooks)
        return all_hooks

    def clear(self) -> None:
        """Remove all hooks."""
        self._hooks.clear()
