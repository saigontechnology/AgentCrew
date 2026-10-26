"""Expose assigned AgentCrew MCP tools to Cursor through stateless MCP calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from loguru import logger

from .manager import MCPSessionManager


def _redact_config_secrets(text: str, config: Any) -> str:
    """Avoid forwarding configured credentials in tool output or descriptions."""
    headers = list((config.headers or {}).values())
    values = headers + list((config.env or {}).values())
    values.extend(
        value[7:]
        for value in headers
        if isinstance(value, str) and value.lower().startswith("bearer ")
    )
    oauth = config.oauth
    if oauth is not None:
        for holder, names in (
            (oauth.tokens, ("access_token", "refresh_token")),
            (oauth.client_info, ("client_secret",)),
        ):
            if holder is not None:
                values.extend(getattr(holder, name, None) for name in names)
    for value in values:
        if value:
            text = text.replace(str(value), "[redacted]")
    return text


def _redact_data(value: Any, config: Any) -> Any:
    """Redact credentials before serializing structured MCP output."""
    if isinstance(value, str):
        return _redact_config_secrets(value, config)
    if isinstance(value, dict):
        return {
            _redact_data(key, config): _redact_data(item, config)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_data(item, config) for item in value]
    return value


def cursor_mcp_tools(
    sdk: Any, agent_name: str, run_coroutine: Callable[[Callable[[], Any]], Any]
) -> dict[str, Any]:
    """Build Cursor custom tools for only this agent's enabled MCP servers."""
    manager = MCPSessionManager.get_instance()
    if not manager.initialized:
        manager.initialize()
    manager.config_manager.load_config()
    tools: dict[str, Any] = {}
    for config in manager.config_manager.get_enabled_servers(agent_name).values():
        try:
            definitions = run_coroutine(
                lambda config=config: manager.mcp_service.get_cursor_tool_definitions(
                    config, agent_name
                )
            )
        except Exception:
            logger.warning("Cursor MCP discovery failed for server '{}'", config.name)
            continue
        for original_name, definition in definitions:
            function = definition["function"]

            def execute(
                args: Mapping[str, Any],
                _context: Any = None,
                *,
                _config=config,
                _name=original_name,
            ) -> str:
                try:
                    result = run_coroutine(
                        lambda: manager.mcp_service.call_tool_stateless(
                            _config, _name, dict(args or {})
                        )
                    )
                    return json.dumps(
                        _redact_data(result, _config), ensure_ascii=False, default=str
                    )
                except Exception:
                    logger.warning(
                        "Cursor MCP tool call failed for server '{}'", _config.name
                    )
                    return json.dumps(
                        {"status": "error", "content": "MCP tool call failed"}
                    )

            try:
                tools[function["name"]] = sdk.CustomTool(
                    description=_redact_config_secrets(
                        function["description"] or "MCP tool", config
                    ),
                    input_schema=_redact_data(function["parameters"], config),
                    execute=execute,
                )
            except Exception:
                logger.warning(
                    "Cursor MCP tool setup failed for server '{}'", config.name
                )
    return tools
