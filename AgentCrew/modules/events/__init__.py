"""
AgentCrew Event System — typed event bus, hooks, and plugin infrastructure.

Three layers:
1. EventBus       — typed async-first pub/sub with sync backward compat
2. HookRegistry   — lifecycle hooks (before/after) for data mutation
3. PluginManager  — plugin discovery via entry_points + config.json
"""

from .constants import AppEvents as AppEvents
from .constants import EVENT_PAYLOAD_MAP as EVENT_PAYLOAD_MAP
from .event_bus import EventBus as EventBus
from .event_bus import Subscription as Subscription
from .event_bus import StopPropagation as StopPropagation
from .hooks import HookPoints as HookPoints
from .hooks import HookRegistry as HookRegistry
from .hooks import Hook as Hook
from .hooks import HookPhase as HookPhase
from .hooks import HookRegistration as HookRegistration
from .hooks import CancelOperation as CancelOperation
from .plugin_system import Plugin as Plugin
from .plugin_system import PluginManager as PluginManager
from .plugin_system import PluginConfig as PluginConfig
from .plugin_system import PluginMeta as PluginMeta
