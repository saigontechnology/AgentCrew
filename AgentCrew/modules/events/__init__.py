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

from .constants import EVENT_PAYLOAD_MAP as EVENT_PAYLOAD_MAP
from .constants import AppEvents as AppEvents
from .event_bus import EventBus as EventBus
from .event_bus import StopPropagation as StopPropagation
from .event_bus import Subscription as Subscription
from .hook_payloads import (
    HOOK_PAYLOAD_MAP as HOOK_PAYLOAD_MAP,
)
from .hook_payloads import (
    AgentDelegateContext as AgentDelegateContext,
)
from .hook_payloads import (
    AgentDelegateResult as AgentDelegateResult,
)
from .hook_payloads import (
    AgentProcessContext as AgentProcessContext,
)
from .hook_payloads import (
    AgentProcessResult as AgentProcessResult,
)
from .hook_payloads import (
    AgentTransferContext as AgentTransferContext,
)
from .hook_payloads import (
    AgentTransferResult as AgentTransferResult,
)
from .hook_payloads import (
    BaseHookContext as BaseHookContext,
)
from .hook_payloads import (
    BaseHookResult as BaseHookResult,
)
from .hook_payloads import (
    ContextBuildContext as ContextBuildContext,
)
from .hook_payloads import (
    ContextBuildResult as ContextBuildResult,
)
from .hook_payloads import (
    MemoryRetrieveContext as MemoryRetrieveContext,
)
from .hook_payloads import (
    MemoryRetrieveResult as MemoryRetrieveResult,
)
from .hook_payloads import (
    MemoryStoreContext as MemoryStoreContext,
)
from .hook_payloads import (
    MemoryStoreResult as MemoryStoreResult,
)
from .hook_payloads import (
    ResponseCompleteContext as ResponseCompleteContext,
)
from .hook_payloads import (
    ResponseCompleteResult as ResponseCompleteResult,
)
from .hook_payloads import (
    ToolExecuteContext as ToolExecuteContext,
)
from .hook_payloads import (
    ToolExecuteResult as ToolExecuteResult,
)
from .hook_payloads import (
    UserMessageContext as UserMessageContext,
)
from .hook_payloads import (
    UserMessageResult as UserMessageResult,
)
from .hooks import CancelOperation as CancelOperation
from .hooks import Hook as Hook
from .hooks import HookPhase as HookPhase
from .hooks import HookPoints as HookPoints
from .hooks import HookRegistration as HookRegistration
from .hooks import HookRegistry as HookRegistry
from .plugin_system import Plugin as Plugin
from .plugin_system import PluginManager as PluginManager
from .plugin_system import PluginMeta as PluginMeta
