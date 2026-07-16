# AgentCrew Plugin Development

AgentCrew plugins can subscribe to application events and register lifecycle
hooks. Only `tool.execute` is currently wired into runtime execution; contracts
for the other declared hook points are available for forward-compatible plugin
development. Plugins are discovered by scanning two filesystem directories:

1. `.agentcrew/plugins/` — **project-based plugins** (higher precedence)
2. `~/.AgentCrew/plugins/` — **global plugins** (fallback)

Each entry in these directories may be a `.py` file (single-file plugin) or a
subdirectory containing `main.py` (project plugin).

> **Security**: Project plugins are **not activated automatically** unless
> `trusted_project_plugins=True` is passed to `PluginManager`. This prevents
> automatic code execution when AgentCrew is run from an untrusted project
> directory. Symlinked plugin entries are rejected. All resolved source paths
> are logged before import.

> **When to build a plugin:**
> - You need custom behavior on every tool execution (logging, auditing, transforming)
> - You want to react to application events (message sent, tool completed, agent changed)
> - You are building integrations that should work across all agents without configuration
> - You want to package and share reusable extensions with the community
>
> **When not to build a plugin:**
> - A custom tool registered with a specific agent is sufficient
> - The behavior is temporary or experimental
> - You can achieve the same result by configuring an MCP server

## Plugin interface

Implement `AgentCrew.modules.events.Plugin` with a globally unique `name`. `version`, `description`, and `dependencies` are optional metadata.

```python
from AgentCrew.modules.events import AppEvents, Hook, HookPhase, HookPoints, Plugin


class ExamplePlugin(Plugin):
    @property
    def name(self):
        return "example"

    @property
    def version(self):
        return "1.0.0"

    @property
    def dependencies(self):
        return []

    async def activate(self, bus, hooks):
        bus.on(AppEvents.SYSTEM_MESSAGE, self.on_system_message)
        hooks.register(
            Hook(
                point=HookPoints.TOOL_EXECUTE,
                phase=HookPhase.BEFORE,
                handler=self.before_tool,
            )
        )

    async def deactivate(self):
        pass

    def on_system_message(self, message):
        print(message)

    def before_tool(self, context):
        return context
```

`activate()` receives plugin-owned EventBus and HookRegistry facades. AgentCrew automatically removes registrations made through these facades on unload, failed activation, and reload. `deactivate()` must still release resources outside EventBus and HookRegistry, such as files, subprocesses, sockets, and background tasks.

### Plugin identity enforcement

The **discovered key** (the filename without ``.py``, or the directory name)
is the authoritative plugin identity. The declared ``name`` property of your
``Plugin`` subclass **must match** this key exactly. If they differ, loading
fails with an error. This ensures dependency references are unambiguous.

```
# Valid:
my_plugin.py           → class name = "my_plugin"
my_project/main.py     → class name = "my_project"

# Invalid (will fail to load):
my_plugin.py           → class name = "my_plugin" ✗
```

### Dependencies

Dependencies are referenced by the discovered key (the filesystem name),
not the class name or any other identifier.

## Discovery

Plugin directories are auto-created on first discovery. No configuration file is needed.

### Single-file plugin

Place a `.py` file directly in the plugins directory. The plugin name is derived
from the filename (without the `.py` extension).

```
.agentcrew/plugins/
  my_plugin.py          ← key: my_plugin
```

The module is loaded from its exact filesystem path under a private AgentCrew
namespace. It will **never** collide with an installed package of the same name.

### Project plugin

Create a subdirectory containing `main.py`. The plugin name is the directory name.

```
.agentcrew/plugins/
  my_project/
    main.py             ← key: my_project
    helper.py           ← importable as ``from .helper import value``
```

Project plugins **do not require** ``__init__.py``. Relative imports work because
AgentCrew synthesizes a private package for the plugin directory. This is useful
for plugins that need multiple files or have dependencies.

### Supported naming rules

Plugin names must match the following pattern:

```
^[A-Za-z0-9][A-Za-z0-9_-]*$
```

- Must start with a letter or digit.
- May contain letters, digits, underscores, and hyphens.
- Dotted names (``foo.bar.py``), hidden names (``.secret.py``), and names with
  spaces are rejected with a warning.

### Duplicate handling

If both ``foo.py`` and ``foo/main.py`` exist **in the same root**, the duplicate
is rejected and a warning is logged. Precedence only applies across scopes
(global vs project), not within one scope.

### Project vs global precedence

When the same plugin name exists in both locations, the project-based version
(in ``.agentcrew/plugins/``) takes precedence over the global version
(in ``~/.AgentCrew/plugins/``).

### Trust boundary

Project plugins execute arbitrary Python code. To prevent automatic code
execution from untrusted directories, project plugins are **not activated**
by default when stored in ``.agentcrew/plugins/``.

You can enable project plugins by adding the following to
``~/.AgentCrew/config.json`` (or ``./config.json``):

```json
{
  "global_settings": {
    "trusted_project_plugins": true
  }
}
```

When a project plugin is skipped, AgentCrew logs a message explaining
how to enable them. Global plugins (in ``~/.AgentCrew/plugins/``) are
always trusted and do not require this setting.

For programmatic usage, you can also pass ``trusted_project_plugins=True``
directly to the ``PluginManager`` constructor.

## Dependencies and lifecycle

- Dependencies activate before dependents.
- Missing, cyclic, or failed dependencies prevent dependent activation.
- A failed plugin does not prevent unrelated plugins from loading.
- Unloading a dependency unloads active dependents first.
- `unload_all()` deactivates plugins in reverse activation order and continues after failures.
- ``reload()`` unloads the plugin, removes its cached module from ``sys.modules``, invalidates
  import caches, then re-imports from the stored source path. Source changes are reflected
  without requiring a process restart.
- Plugins activate and deactivate within the interactive console and GUI lifecycles, where application events and `tool.execute` hooks are integrated.
- A2A server, ACP, and job modes do not load plugins automatically.
- Activation and deactivation are async. Plugins should not assume the short-lived console activation event loop remains available for persistent background tasks.
- AgentCrew **does not install plugin dependencies**. Plugins execute inside the AgentCrew
  Python environment. Third-party dependencies must already be installed. ``pyproject.toml``
  or ``requirements.txt`` inside a plugin directory is not processed automatically.

## EventBus

Use `AppEvents` constants rather than raw event strings. Every event's keyword payload contract is listed in `EVENT_PAYLOAD_MAP`; payloadless events map to `None`.

```python
bus.on(AppEvents.TOOL_RESULT, self.on_tool_result)
bus.emit_sync(AppEvents.SYSTEM_MESSAGE, message="Plugin activated")
```

Plugin-owned EventBus subscriptions are removed automatically. Explicit `off()` is still available for early removal.

## Hook payload contracts

Only `before` and `after` phases are supported. Around hooks are not supported.
Every declared `HookPoints` value has a context and result `TypedDict` exported
from `AgentCrew.modules.events`. `HOOK_PAYLOAD_MAP` exposes those contracts for
runtime introspection:

```python
from AgentCrew.modules.events import HOOK_PAYLOAD_MAP, HookPhase, HookPoints

before_type = HOOK_PAYLOAD_MAP[HookPoints.TOOL_EXECUTE][HookPhase.BEFORE]
after_type = HOOK_PAYLOAD_MAP[HookPoints.TOOL_EXECUTE][HookPhase.AFTER]
```

The map describes the context passed to a before hook and the result envelope
passed to an after hook. After handlers continue to receive both
`(context, result)`.

The declared contracts cover `tool.execute`, `agent.process`, `user.message`,
`response.complete`, `memory.store`, `memory.retrieve`, `context.build`,
`agent.transfer`, and `agent.delegate`. These declarations do not mean every
point is active:
`tool.execute` is the only hook currently invoked by AgentCrew runtime code.

Payloads are plain dictionary boundaries. They may contain provider-specific
values typed as `Any`, but must not include credentials, authorization headers,
or live service objects.

## `tool.execute` hooks

### Before hook

A before hook receives an independent context dictionary containing:

- `agent_name`
- `tool_id`
- `tool_use`: the original tool call
- `requested_tool_name` and `requested_tool_input`
- `tool_name` and `tool_input`, which the hook may replace

Return the context to continue. Returning `None` or raising `CancelOperation` rejects the tool call. Rejection produces an agent-compatible rejected result and does not invoke after hooks. User approval is evaluated against the original tool request before hook mutation.

### After hook

An after hook receives the context and this result envelope:

```python
{
  "tool_result": object,
  "is_error": bool
}
```

After hooks currently run only after successful executor completion. If the executor raises, the exception escapes before after-hook dispatch. Executor-error after-hook coverage is planned for a later runtime-wiring slice. On successful execution, after hooks may replace the result. Sequential and parallel approved tools use the same hook pipeline. The special `ask` interaction remains outside `tool.execute`.

## Failure isolation and cleanup

PluginManager rolls back owned registrations after constructor or activation failure. Cleanup also occurs in a `finally` block when deactivation raises. Plugin authors should make `deactivate()` idempotent for their external resources.


