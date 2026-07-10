from typing import Any

from AgentCrew.modules.events import Hook, HookPhase, HookPoints, Plugin

EVENT_NAME = "example.local.event"
STATE = {"activations": 0, "deactivations": 0, "events": 0, "hooks": 0, "config": None}


class LocalExamplePlugin(Plugin):
    @property
    def name(self) -> str:
        return "local-example"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def activate(
        self, bus, hooks, plugin_config: dict[str, Any] | None = None
    ) -> None:
        STATE["activations"] += 1
        STATE["config"] = plugin_config
        bus.on(EVENT_NAME, self._on_event)
        hooks.register(
            Hook(HookPoints.TOOL_EXECUTE, HookPhase.BEFORE, self._before_tool)
        )

    async def deactivate(self) -> None:
        STATE["deactivations"] += 1

    def _on_event(self, **data: Any) -> None:
        STATE["events"] += 1

    def _before_tool(self, context: dict[str, Any]) -> dict[str, Any]:
        STATE["hooks"] += 1
        context["local_plugin"] = True
        return context
