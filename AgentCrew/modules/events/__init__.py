"""
AgentCrew Event System — typed event bus, hooks, and plugin infrastructure.

Three layers:
1. EventBus       — typed async-first pub/sub with sync backward compat
2. HookRegistry   — lifecycle hooks (before/after) for data mutation
3. PluginManager  — plugin discovery via filesystem scanning

Plugins are discovered from two directories:
- ``.agentcrew/plugins/`` (project-based, higher precedence)
- ``~/.AgentCrew/plugins/`` (global, fallback)

Each entry may be a ``.py`` file (single-file plugin) or a subdirectory
containing ``main.py`` (project plugin).

Project plugins are only activated when ``trusted_project_plugins=True`` is
passed to the ``PluginManager`` constructor — see security notes in
:ref:`PLUGIN_DEVELOPMENT.md`.
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
from .hook_payloads import (
    HOOK_PAYLOAD_MAP as HOOK_PAYLOAD_MAP,
    AgentDelegateContext as AgentDelegateContext,
    AgentDelegateResult as AgentDelegateResult,
    AgentProcessContext as AgentProcessContext,
    AgentProcessResult as AgentProcessResult,
    AgentTransferContext as AgentTransferContext,
    AgentTransferResult as AgentTransferResult,
    BaseHookContext as BaseHookContext,
    BaseHookResult as BaseHookResult,
    ContextBuildContext as ContextBuildContext,
    ContextBuildResult as ContextBuildResult,
    MemoryRetrieveContext as MemoryRetrieveContext,
    MemoryRetrieveResult as MemoryRetrieveResult,
    MemoryStoreContext as MemoryStoreContext,
    MemoryStoreResult as MemoryStoreResult,
    ResponseCompleteContext as ResponseCompleteContext,
    ResponseCompleteResult as ResponseCompleteResult,
    ToolExecuteContext as ToolExecuteContext,
    ToolExecuteResult as ToolExecuteResult,
    UserMessageContext as UserMessageContext,
    UserMessageResult as UserMessageResult,
)
from .plugin_system import Plugin as Plugin
from .plugin_system import PluginManager as PluginManager
from .plugin_system import PluginMeta as PluginMeta
