# Tool Registration System

This document describes the tool registration system and the migration path from global to agent-specific tools.

## Overview

The tool registration system supports two modes of operation:
1. **Global Registration**: Tools are registered with the central `ToolRegistry` and can be accessed by any agent
2. **Agent-Specific Registration**: Tools are registered directly with specific agents

## Migration Path

### Before (Global Registration)

```python
def register(service_instance=None):
    """Register this tool with the central registry"""
    from modules.tools.registration import register_tool

    register_tool(get_tool_definition, get_tool_handler, service_instance)
```

### After (Supporting Both Global and Agent-Specific)

```python
def register(service_instance=None, agent=None):
    """
    Register this tool with the central registry or directly with an agent

    Args:
        service_instance: Service instance needed by the handler
        agent: Agent instance to register with directly (optional)
    """
    from modules.tools.registration import register_tool

    register_tool(get_tool_definition, get_tool_handler, service_instance, agent)
```

## Best Practices

1. Update all tool modules to support the `agent` parameter
2. Maintain backward compatibility with global registration
3. For agent-specific tools, use the agent's provider for tool definition format
4. When registering tools with specific agents, consider their specialized needs

## Example Implementation

```python
# In your tool module
def register(service_instance=None, agent=None):
    from modules.tools.registration import register_tool

    # Register primary tool
    register_tool(
        get_primary_tool_definition, get_primary_tool_handler, service_instance, agent
    )

    # Register secondary tool
    register_tool(
        get_secondary_tool_definition,
        get_secondary_tool_handler,
        service_instance,
        agent,
    )
```
