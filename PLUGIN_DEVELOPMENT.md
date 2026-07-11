# AgentCrew Plugin Development

AgentCrew plugins can subscribe to application events and register `tool.execute`
hooks. Plugins are discovered from local Python sources or the `agentcrew.plugins`
Python entry-point group.

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
    name = "example"
    version = "1.0.0"
    dependencies = []

    async def activate(self, bus, hooks, plugin_config=None):
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

## Discovery

### Local source

Configure a Python file or package directory in `config.json`:

```json
{
  "plugins": {
    "sources": [
      {
        "name": "example",
        "path": "./plugins/example.py"
      }
    ],
    "config": {
      "example": {
        "enabled": true,
        "settings": {
          "level": "verbose"
        }
      }
    }
  }
}
```

A package directory must contain `__init__.py`. Relative paths are resolved from the current process directory; `~` is expanded.

### Python entry point

Declare the plugin in its package:

```toml
[project.entry-points."agentcrew.plugins"]
example = "example_plugin:ExamplePlugin"
```

Local sources take precedence when the same plugin name is discovered from both mechanisms.

## Dependencies and lifecycle

- Dependencies activate before dependents.
- Missing, disabled, cyclic, or failed dependencies prevent dependent activation.
- A failed plugin does not prevent unrelated plugins from loading.
- Unloading a dependency unloads active dependents first.
- `unload_all()` deactivates plugins in reverse activation order and continues after failures.
- Reload means unload, deterministic registration cleanup, then activation. It does not reload Python module code.
- Plugins activate and deactivate within the interactive console and GUI lifecycles, where application events and `tool.execute` hooks are integrated.
- A2A server, ACP, and job modes do not load plugins automatically.
- Activation and deactivation are async. Plugins should not assume the short-lived console activation event loop remains available for persistent background tasks.

## EventBus

Use `AppEvents` constants rather than raw event strings. Every event's keyword payload contract is listed in `EVENT_PAYLOAD_MAP`; payloadless events map to `None`.

```python
bus.on(AppEvents.TOOL_RESULT, self.on_tool_result)
bus.emit_sync(AppEvents.SYSTEM_MESSAGE, message="Plugin activated")
```

Plugin-owned EventBus subscriptions are removed automatically. Explicit `off()` is still available for early removal.

## `tool.execute` hooks

Only `before` and `after` phases are supported. Around hooks are not supported.

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

After hooks run for both successful execution and executor errors. They may replace the result or recover from an error by returning a modified envelope. Sequential and parallel approved tools use the same hook pipeline. The special `ask` interaction remains outside `tool.execute`.

## Failure isolation and cleanup

PluginManager rolls back owned registrations after constructor or activation failure. Cleanup also occurs in a `finally` block when deactivation raises. Plugin authors should make `deactivate()` idempotent for their external resources.

## Verification

No API key or network service is required:

```bash
uv run python examples/plugins/verify_local_plugin.py
uv run --with ./examples/plugins/entry_point_plugin \
  python examples/plugins/verify_entry_point_plugin.py
uv run python examples/plugins/verify_event_payload_map.py
```

See `examples/plugins/README.md` for fixture details.
