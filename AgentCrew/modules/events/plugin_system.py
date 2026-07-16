"""Plugin discovery and lifecycle management for AgentCrew."""

from __future__ import annotations

import os
import sys
import types
import importlib
import importlib.machinery
import importlib.util
import inspect
from loguru import logger
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from AgentCrew.modules.events import  EventBus, Subscription, Hook, HookRegistration, HookRegistry


# ── Private namespace for all plugin modules ─────────────────────
_GLOBAL_NS = "_agentcrew_plugins.global"
_PROJECT_NS = "_agentcrew_plugins.project"

# ── Safe name pattern ────────────────────────────────────────────
_VALID_PLUGIN_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


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

    @abstractmethod
    async def activate(
        self,
        bus: _OwnedEventBus,
        hooks: _OwnedHookRegistry,
    ) -> None:
        """Activate the plugin and register its integrations."""

    @abstractmethod
    async def deactivate(self) -> None:
        """Release resources owned outside EventBus and HookRegistry."""
        return None


@dataclass
class PluginMeta:
    """Discovered plugin metadata before loading."""

    name: str
    module_path: str
    resolved_path: Path
    scope: Literal["global", "project"]
    source: str = ""


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
    """Discover, activate, reload, and unload plugins deterministically.

    Plugins are discovered from two filesystem directories:

    * ``.agentcrew/plugins/`` — project-based plugins (higher precedence)
    * ``~/.AgentCrew/plugins/`` — global plugins (fallback)

    Each entry may be a ``.py`` file (single-file plugin) or a subdirectory
    containing ``main.py`` (project plugin).

    Project plugins are NOT activated automatically unless
    ``trusted_project_plugins=True`` is passed to the constructor. This
    establishes a security boundary: project plugins execute arbitrary Python
    code, and activating them from an untrusted project directory is equivalent
    to automatic code execution.
    """

    def __init__(
        self,
        project_plugins_dir: str | None = None,
        global_plugins_dir: str | None = None,
        *,
        trusted_project_plugins: bool = False,
    ) -> None:
        self._bus = EventBus.get_instance()
        self._hooks = HookRegistry.get_instance()
        self._project_plugins_dir = (
            Path(project_plugins_dir or ".agentcrew/plugins").expanduser().resolve()
        )
        self._global_plugins_dir = (
            Path(global_plugins_dir or os.path.expanduser("~/.AgentCrew/plugins"))
            .expanduser()
            .resolve()
        )
        self._trusted_project_plugins = trusted_project_plugins

        self._plugins: dict[str, Plugin] = {}
        self._metas: dict[str, PluginMeta] = {}
        self._modules: dict[str, types.ModuleType] = {}
        self._activation_order: list[str] = []
        self._loading_stack: list[str] = []
        self._unloading: set[str] = set()

    # ── Plugin directory helpers ────────────────────────────────────

    @staticmethod
    def _init_plugins_dir(plugins_dir: Path) -> bool:
        """Create the plugins directory if it does not exist.

        Returns True on success, False on failure.
        """
        try:
            plugins_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.warning("Failed to initialise plugin dir %s: %s", plugins_dir, exc)
            return False

    @staticmethod
    def _validate_plugin_name(name: str, entry: Path) -> bool:
        """Validate a filesystem-derived plugin name.

        Returns True if valid, False with a logged warning otherwise.
        """
        import re as _re

        if not _re.match(_VALID_PLUGIN_NAME_RE, name):
            logger.warning(
                "Skipping plugin %r at %s: name does not match pattern "
                r"'^[A-Za-z0-9][A-Za-z0-9_-]*$'",
                name,
                entry,
            )
            return False
        return True

    def _scan_plugins_dir(
        self,
        plugins_dir: Path,
        discovered: dict[str, PluginMeta],
        scope: Literal["global", "project"],
        *,
        precedence: bool = False,
    ) -> None:
        """Scan a single plugins directory and populate ``discovered``.

        Args:
            plugins_dir: The directory to scan.
            discovered: Accumulator dict keyed by plugin name.
            scope: ``"global"`` or ``"project"``.
            precedence: If True, entries from this dir override existing ones.
        """
        if not plugins_dir.is_dir():
            return

        for entry in sorted(plugins_dir.iterdir()):
            # Reject symlinks
            if entry.is_symlink():
                logger.warning(
                    "Skipping symlink plugin entry %s — symlinks are not supported",
                    entry,
                )
                continue

            name = entry.stem

            # ── Single-file plugin: <dir>/<name>.py ──
            if entry.is_file() and entry.suffix == ".py":
                if not self._validate_plugin_name(name, entry):
                    continue

                # Reject same-root duplicate: foo.py vs foo/main.py
                if (
                    name in discovered
                    and discovered[name].resolved_path.parent == plugins_dir
                ):
                    conflict = discovered[name].resolved_path
                    logger.warning(
                        "Duplicate plugin name %r in %s: both %s and %s exist. "
                        "Skipping %s.",
                        name,
                        plugins_dir,
                        conflict,
                        entry,
                        entry,
                    )
                    continue

                meta = PluginMeta(
                    name=name,
                    module_path=str(entry),
                    resolved_path=entry,
                    scope=scope,
                    source="file",
                )
                if name not in discovered or precedence:
                    discovered[name] = meta

            # ── Project plugin: <dir>/<name>/main.py ──
            elif entry.is_dir():
                if not self._validate_plugin_name(entry.name, entry):
                    continue

                main_file = entry / "main.py"
                if not main_file.is_file():
                    continue

                # Reject same-root duplicate
                if (
                    entry.name in discovered
                    and discovered[entry.name].resolved_path.parent == plugins_dir
                ):
                    conflict = discovered[entry.name].resolved_path
                    logger.warning(
                        "Duplicate plugin name %r in %s: both %s and %s exist. "
                        "Skipping %s.",
                        entry.name,
                        plugins_dir,
                        conflict,
                        entry,
                        entry,
                    )
                    continue

                meta = PluginMeta(
                    name=entry.name,
                    module_path=str(main_file),
                    resolved_path=entry,
                    scope=scope,
                    source="project",
                )
                if entry.name not in discovered or precedence:
                    discovered[entry.name] = meta

    def discover(self) -> list[PluginMeta]:
        """Discover plugins by scanning project and global plugin directories.

        Scans two locations:
        1. ``.agentcrew/plugins/`` (project-based, takes precedence)
        2. ``~/.AgentCrew/plugins/`` (global, fallback)

        A plugin may be:
        - A ``.py`` file directly in the plugins directory (single-file plugin).
        - A subdirectory containing ``main.py`` (project plugin).

        Plugin directories are automatically initialised if they do not exist.
        The returned metadata is an authoritative snapshot — removed plugin
        files no longer appear after the next call to ``discover()``.
        """
        discovered: dict[str, PluginMeta] = {}

        # Global: lower precedence
        if self._init_plugins_dir(self._global_plugins_dir):
            try:
                self._scan_plugins_dir(
                    self._global_plugins_dir, discovered, "global", precedence=False
                )
            except OSError as exc:
                logger.warning("Failed to scan global plugin dir: %s", exc)
        else:
            logger.warning(
                "Skipping global plugin discovery — directory could not be created"
            )

        # Project: higher precedence
        if self._init_plugins_dir(self._project_plugins_dir):
            try:
                self._scan_plugins_dir(
                    self._project_plugins_dir,
                    discovered,
                    "project",
                    precedence=True,
                )
            except OSError as exc:
                logger.warning("Failed to scan project plugin dir: %s", exc)
        else:
            logger.warning(
                "Skipping project plugin discovery — directory could not be created"
            )

        # Authoritative snapshot: replace, do not merge
        self._metas = discovered
        return list(discovered.values())

    # ── Module loading (source-path, private namespace) ─────────────

    @staticmethod
    def _safe_id(name: str) -> str:
        """Derive a Python-safe module identifier from a plugin name."""
        return name.replace("-", "_")

    def _load_module_from_source(self, meta: PluginMeta) -> types.ModuleType | None:
        """Import a plugin module from its exact filesystem path.

        Plugins are loaded under the reserved namespace
        ``_agentcrew_plugins.<scope>.<safe_name>``, which prevents collisions
        with installed packages and other application modules.
        """
        ns_base = _GLOBAL_NS if meta.scope == "global" else _PROJECT_NS
        safe = self._safe_id(meta.name)
        resolved = meta.resolved_path

        if meta.source == "project":
            # Directory plugin: load main.py under <ns_base>.<safe>.main
            main_file = resolved / "main.py"

            # Create a namespace package for the plugin directory so that
            # relative imports (``from .helper import value``) work.
            pkg_name = f"{ns_base}.{safe}"
            pkg_spec = importlib.machinery.ModuleSpec(
                pkg_name,
                None,
                is_package=True,
            )
            pkg_spec.submodule_search_locations = [str(resolved)]
            pkg = importlib.util.module_from_spec(pkg_spec)
            sys.modules[pkg_name] = pkg

            # Load main.py as a submodule of the package
            mod_name = f"{pkg_name}.main"
            spec = importlib.util.spec_from_file_location(mod_name, str(main_file))
            if spec is None or spec.loader is None:
                logger.error(
                    "Could not create module spec for %s (%s)", meta.name, main_file
                )
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            return module

        else:
            # Single-file plugin
            mod_name = f"{ns_base}.{safe}"
            spec = importlib.util.spec_from_file_location(mod_name, str(resolved))
            if spec is None or spec.loader is None:
                logger.error(
                    "Could not create module spec for %s (%s)",
                    meta.name,
                    meta.module_path,
                )
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            return module

    @staticmethod
    def _import_plugin_class(
        meta: PluginMeta, module: types.ModuleType
    ) -> type[Plugin] | None:
        """Locate exactly one locally-defined ``Plugin`` subclass in *module*.

        Only classes whose ``__module__ == module.__name__`` are considered.
        This prevents imported ``Plugin`` subclasses from being mistaken for
        the module's own plugin class.
        """
        candidates: list[type[Plugin]] = []
        for _, candidate in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(candidate, Plugin)
                and candidate is not Plugin
                and candidate.__module__ == module.__name__
            ):
                candidates.append(candidate)

        if len(candidates) == 1:
            return candidates[0]

        if len(candidates) == 0:
            logger.error(
                "No locally defined Plugin subclass found in %s for plugin %r",
                meta.module_path,
                meta.name,
            )
            return None

        names = ", ".join(c.__name__ for c in candidates)
        logger.error(
            "Expected exactly one Plugin subclass in %s for plugin %r; found %d: %s",
            meta.module_path,
            meta.name,
            len(candidates),
            names,
        )
        return None

    # ── Lifecycle ───────────────────────────────────────────────────

    async def load(self, name: str) -> Plugin | None:
        """Activate one plugin after all dependencies are active.

        Project-scope plugins are only activated when
        ``trusted_project_plugins=True`` was set at construction time.
        """
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

        # ── Trust boundary: skip untrusted project plugins ──
        if meta.scope == "project" and not self._trusted_project_plugins:
            logger.info(
                "Skipping project plugin %r (%s) — "
                "trusted_project_plugins is not enabled. "
                "Set trusted_project_plugins=True to activate project plugins.",
                name,
                meta.resolved_path,
            )
            return None

        self._loading_stack.append(name)
        instance: Plugin | None = None
        try:
            logger.info("Loading plugin %r from %s", name, meta.resolved_path)

            # Import from exact source path (not global module name)
            module = self._load_module_from_source(meta)
            if module is None:
                return None
            self._modules[meta.name] = module

            plugin_class = self._import_plugin_class(meta, module)
            if plugin_class is None:
                return None

            instance = plugin_class()

            # Enforce identity: discovered key === declared name
            if instance.name != name:
                logger.error(
                    "Plugin %r declares name %r which does not match the "
                    "discovered key %r. Dependencies also use the discovered "
                    "key; a mismatch would cause identity confusion. "
                    "Rename the file/directory to match the declared name, "
                    "or change the declared name to match the file/directory.",
                    meta.module_path,
                    instance.name,
                    name,
                )
                return None

            # Activate dependencies
            for dependency in instance.dependencies:
                loaded_dependency = await self.load(dependency)
                if loaded_dependency is None or dependency not in self._plugins:
                    logger.error(
                        "Plugin %r dependency %r failed to activate",
                        name,
                        dependency,
                    )
                    return None

            # Clean stale registrations before activation
            self._cleanup_owned(name)
            owned_bus = _OwnedEventBus(self._bus, name)
            owned_hooks = _OwnedHookRegistry(self._hooks, name)
            await instance.activate(
                bus=owned_bus,
                hooks=owned_hooks,
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
            self._modules.pop(name, None)
            self._remove_activation_record(name)
            return None
        finally:
            if self._loading_stack and self._loading_stack[-1] == name:
                self._loading_stack.pop()
            elif name in self._loading_stack:
                self._loading_stack.remove(name)

    async def load_all(self) -> dict[str, Plugin]:
        """Discover and activate every enabled plugin independently.

        This is an idempotent operation: already active plugins are returned
        without being reactivated.

        Project-scope plugins are only activated when
        ``trusted_project_plugins=True`` was set at construction time.
        """
        self.discover()
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
        """Unload and reactivate a plugin, including re-reading its source.

        Unlike :meth:`load`, this method:
        1. Unloads the active plugin.
        2. Removes the cached module from ``sys.modules``.
        3. Calls ``importlib.invalidate_caches()``.
        4. Re-imports from the stored source path.

        This ensures source changes are reflected without requiring
        a process restart.
        """
        await self.unload(name)

        # Remove the cached module under the private namespace
        safe = self._safe_id(name)
        for prefix in (_GLOBAL_NS, _PROJECT_NS):
            pkg_key = f"{prefix}.{safe}"
            if pkg_key in sys.modules:
                del sys.modules[pkg_key]
            main_key = f"{prefix}.{safe}.main"
            if main_key in sys.modules:
                del sys.modules[main_key]

        importlib.invalidate_caches()
        self._modules.pop(name, None)
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
