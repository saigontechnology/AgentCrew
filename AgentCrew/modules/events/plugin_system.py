"""Plugin discovery and lifecycle management for AgentCrew."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .event_bus import EventBus, Subscription
from .hooks import Hook, HookRegistration, HookRegistry

logger = logging.getLogger(__name__)


class Plugin(ABC):
    """Base class for all AgentCrew plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique plugin identifier."""

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return ""

    @property
    def dependencies(self) -> list[str]:
        """Return plugin names that must be active first."""
        return []

    async def activate(
        self,
        bus: EventBus,
        hooks: HookRegistry,
        plugin_config: dict[str, Any] | None = None,
    ) -> None:
        """Activate the plugin and register its integrations."""

    async def deactivate(self) -> None:
        """Release resources owned outside EventBus and HookRegistry."""
        return None


@dataclass
class PluginMeta:
    """Discovered plugin metadata before loading."""

    name: str
    module_path: str
    entry_point: str | None = None
    source: str = "entry_point"


@dataclass
class PluginConfig:
    """Runtime configuration for a plugin."""

    name: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


class _OwnedEventBus:
    """EventBus facade that assigns plugin ownership automatically."""

    def __init__(self, bus: EventBus, owner: str) -> None:
        self._bus = bus
        self._owner = owner

    def on(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        priority: int = 0,
        once: bool = False,
        filter_func: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> Subscription:
        return self._bus.on(
            event,
            handler,
            priority=priority,
            once=once,
            filter_func=filter_func,
            owner=self._owner,
        )

    async def emit(self, event: str, **data: Any) -> None:
        await self._bus.emit(event, **data)

    def emit_sync(self, event: str, **data: Any) -> None:
        self._bus.emit_sync(event, **data)

    def off(self, subscription: Subscription) -> None:
        self._bus.off(subscription)

    def has_subscribers(self, event: str) -> bool:
        return self._bus.has_subscribers(event)


class _OwnedHookRegistry:
    """HookRegistry facade that assigns plugin ownership automatically."""

    def __init__(self, hooks: HookRegistry, owner: str) -> None:
        self._hooks = hooks
        self._owner = owner

    def register(self, hook: Hook) -> HookRegistration:
        return self._hooks.register(hook, owner=self._owner)

    def unregister_registration(self, registration: HookRegistration) -> None:
        self._hooks.unregister_registration(registration)

    def unregister(
        self,
        point: str,
        phase: str,
        handler: Callable[..., Any],
    ) -> None:
        self._hooks.unregister(point, phase, handler)

    async def run_before(self, point: str, **context: Any) -> dict[str, Any] | None:
        return await self._hooks.run_before(point, **context)

    async def run_after(
        self,
        point: str,
        result: Any = None,
        **context: Any,
    ) -> Any:
        return await self._hooks.run_after(point, result=result, **context)

    def get_hooks(self, point: str | None = None) -> list[Hook]:
        return self._hooks.get_hooks(point)


class PluginManager:
    """Discover, activate, reload, and unload plugins deterministically."""

    def __init__(
        self,
        bus: EventBus,
        hooks: HookRegistry,
        config_dir: str | None = None,
    ) -> None:
        self._bus = bus
        self._hooks = hooks
        self._config_dir = Path(config_dir or os.path.expanduser("~/.agentcrew"))
        self._plugins: dict[str, Plugin] = {}
        self._metas: dict[str, PluginMeta] = {}
        self._configs: dict[str, PluginConfig] = {}
        self._activation_order: list[str] = []
        self._loading_stack: list[str] = []
        self._unloading: set[str] = set()

    def discover(self, config_json: dict[str, Any] | None = None) -> list[PluginMeta]:
        """Discover plugins from explicit paths followed by entry points."""
        discovered: dict[str, PluginMeta] = {}
        config_sources: list[Any] = []
        if config_json and isinstance(config_json, dict):
            plugin_section = config_json.get("plugins", {})
            config_sources = plugin_section.get("sources", [])
            for name, config in plugin_section.get("config", {}).items():
                self._configs[name] = PluginConfig(
                    name=name,
                    enabled=config.get("enabled", True),
                    config=config.get("settings", {}),
                )

        for source_entry in config_sources:
            try:
                if isinstance(source_entry, str):
                    path = source_entry
                    name = self._infer_name_from_path(path)
                elif isinstance(source_entry, dict):
                    path = source_entry.get("path", "")
                    name = source_entry.get("name", self._infer_name_from_path(path))
                else:
                    raise TypeError("plugin source must be a path string or object")
                module_path = self._resolve_config_path(path)
                if module_path and name not in discovered:
                    discovered[name] = PluginMeta(
                        name=name,
                        module_path=module_path,
                        source="config_path",
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to discover plugin from config source %r: %s",
                    source_entry,
                    exc,
                )

        try:
            for entry_point in importlib.metadata.entry_points(
                group="agentcrew.plugins"
            ):
                if entry_point.name not in discovered:
                    discovered[entry_point.name] = PluginMeta(
                        name=entry_point.name,
                        module_path=entry_point.value,
                        entry_point=str(entry_point),
                        source="entry_point",
                    )
        except Exception as exc:
            logger.warning("Failed to discover plugins from entry points: %s", exc)

        self._metas.update(discovered)
        return list(discovered.values())

    async def load(self, name: str) -> Plugin | None:
        """Activate one plugin after all dependencies are active."""
        if name in self._plugins:
            return self._plugins[name]
        if name in self._loading_stack:
            cycle = " -> ".join([*self._loading_stack, name])
            logger.error("Plugin dependency cycle detected: %s", cycle)
            return None

        meta = self._metas.get(name)
        if meta is None:
            logger.error("Plugin %r was not discovered", name)
            return None

        plugin_config = self._configs.get(name, PluginConfig(name=name))
        if not plugin_config.enabled:
            logger.info("Plugin %r is disabled", name)
            return None

        self._loading_stack.append(name)
        instance: Plugin | None = None
        try:
            plugin_class = self._import_plugin_class(meta)
            if plugin_class is None:
                return None
            instance = plugin_class()
            if instance.name != name:
                logger.warning(
                    "Plugin source %r declares name %r; using configured name %r",
                    meta.module_path,
                    instance.name,
                    name,
                )

            for dependency in instance.dependencies:
                loaded_dependency = await self.load(dependency)
                if loaded_dependency is None or dependency not in self._plugins:
                    logger.error(
                        "Plugin %r dependency %r failed to activate",
                        name,
                        dependency,
                    )
                    return None

            self._cleanup_owned(name)
            owned_bus = _OwnedEventBus(self._bus, name)
            owned_hooks = _OwnedHookRegistry(self._hooks, name)
            await instance.activate(
                bus=owned_bus,
                hooks=owned_hooks,
                plugin_config=plugin_config.config,
            )
            self._plugins[name] = instance
            self._activation_order.append(name)
            logger.info("Plugin %r v%s activated", name, instance.version)
            return instance
        except Exception as exc:
            logger.exception("Failed to activate plugin %r: %s", name, exc)
            if instance is not None:
                try:
                    await instance.deactivate()
                except Exception:
                    logger.exception("Failed to roll back plugin %r deactivation", name)
            self._cleanup_owned(name)
            self._plugins.pop(name, None)
            self._remove_activation_record(name)
            return None
        finally:
            if self._loading_stack and self._loading_stack[-1] == name:
                self._loading_stack.pop()
            elif name in self._loading_stack:
                self._loading_stack.remove(name)

    async def load_all(
        self,
        config_json: dict[str, Any] | None = None,
    ) -> dict[str, Plugin]:
        """Discover and activate every enabled plugin independently."""
        self.discover(config_json)
        for name in list(self._metas):
            await self.load(name)
        return dict(self._plugins)

    async def unload(self, name: str) -> None:
        """Unload active dependents first, then unload the requested plugin."""
        if name not in self._plugins or name in self._unloading:
            return
        self._unloading.add(name)
        try:
            dependents = [
                plugin_name
                for plugin_name, plugin in list(self._plugins.items())
                if name in plugin.dependencies
            ]
            dependents.sort(
                key=lambda plugin_name: self._activation_index(plugin_name),
                reverse=True,
            )
            for dependent in dependents:
                await self.unload(dependent)

            plugin = self._plugins.get(name)
            if plugin is not None:
                try:
                    await plugin.deactivate()
                    logger.info("Plugin %r deactivated", name)
                except Exception as exc:
                    logger.exception("Error deactivating plugin %r: %s", name, exc)
                finally:
                    self._cleanup_owned(name)
                    self._plugins.pop(name, None)
                    self._remove_activation_record(name)
        finally:
            self._unloading.discard(name)

    async def unload_all(self) -> None:
        """Unload all active plugins in reverse activation order."""
        for name in list(reversed(self._activation_order)):
            await self.unload(name)

    async def reload(self, name: str) -> Plugin | None:
        """Unload and reactivate a plugin without duplicate registrations."""
        await self.unload(name)
        return await self.load(name)

    def get(self, name: str) -> Plugin | None:
        return self._plugins.get(name)

    def list_loaded(self) -> list[Plugin]:
        return list(self._plugins.values())

    def is_loaded(self, name: str) -> bool:
        return name in self._plugins

    def _cleanup_owned(self, name: str) -> None:
        self._bus.off_owner(name)
        self._hooks.unregister_owner(name)

    def _remove_activation_record(self, name: str) -> None:
        self._activation_order = [
            item for item in self._activation_order if item != name
        ]

    def _activation_index(self, name: str) -> int:
        try:
            return self._activation_order.index(name)
        except ValueError:
            return -1

    @staticmethod
    def _import_plugin_class(meta: PluginMeta) -> type[Plugin] | None:
        """Import a plugin class from a module or ``module:Class`` target."""
        module_path, separator, class_name = meta.module_path.partition(":")
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            logger.error(
                "Failed to import plugin %r (%s): %s",
                meta.name,
                meta.module_path,
                exc,
            )
            return None

        if separator:
            plugin_class = getattr(module, class_name, None)
            if (
                inspect.isclass(plugin_class)
                and issubclass(plugin_class, Plugin)
                and plugin_class is not Plugin
            ):
                return plugin_class
            logger.error(
                "Plugin target %r does not reference a Plugin subclass",
                meta.module_path,
            )
            return None

        for _, candidate in inspect.getmembers(module, inspect.isclass):
            if issubclass(candidate, Plugin) and candidate is not Plugin:
                return candidate

        logger.error(
            "No Plugin subclass found in module %r for plugin %r",
            meta.module_path,
            meta.name,
        )
        return None

    @staticmethod
    def _resolve_config_path(path: str) -> str | None:
        """Resolve a Python file or package directory to a module path."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            logger.warning("Plugin path %r does not exist", path)
            return None

        if resolved.is_dir():
            if not (resolved / "__init__.py").exists():
                logger.warning("Plugin directory %r has no __init__.py", resolved)
                return None
            parent = resolved.parent
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return resolved.name

        if resolved.is_file() and resolved.suffix == ".py":
            parent = resolved.parent
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return resolved.stem

        logger.warning("Plugin path %r is not a Python file or package", path)
        return None

    @staticmethod
    def _infer_name_from_path(path: str) -> str:
        resolved = Path(path)
        return resolved.stem if resolved.suffix == ".py" else resolved.name
