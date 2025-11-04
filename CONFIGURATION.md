# AgentCrew Configuration Guide

This guide explains how to configure AgentCrew to work with different AI
providers, customize agents, set up tools, and manage system settings.

## Configuration Files

AgentCrew stores all configuration in `~/.AgentCrew/` (or
`%USERPROFILE%\.AgentCrew\` on Windows):

```
~/.AgentCrew/
├── config.json              # Global settings and API keys
├── agents.toml              # Agent definitions and configurations
├── mcp_servers.json         # Model Context Protocol server configs
├── persistents/
│   └── adaptive.json        # Learned behaviors for all agents
└── conversations/           # Saved conversation history
```

## Global Configuration (config.json)

The main configuration file controls API keys, UI preferences, and system
behavior.

### API Keys

Add your API keys to connect AgentCrew with AI providers:

```json
{
  "api_keys": {
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "OPENAI_API_KEY": "sk-proj-...",
    "GEMINI_API_KEY": "AIza...",
    "GITHUB_COPILOT_API_KEY": "",
    "GROQ_API_KEY": "gsk_...",
    "DEEPINFRA_API_KEY": "",
    "TAVILY_API_KEY": "tvly-...",
    "VOYAGE_API_KEY": "pa-...",
    "ELEVENLABS_API_KEY": ""
  }
}
```

**Required Keys:**

- At least one AI provider key (Anthropic, OpenAI, Gemini, etc.)

**Optional Keys:**

- `TAVILY_API_KEY` - For web search capabilities
- `VOYAGE_API_KEY` - For alternative embedding provider
- `ELEVENLABS_API_KEY` - For voice synthesis

**Getting API Keys:**

- Anthropic Claude: <https://console.anthropic.com/>
- OpenAI: <https://platform.openai.com/api-keys>
- Google Gemini: <https://aistudio.google.com/apikey>
- GitHub Copilot: Authenticate using `agentcrew copilot-auth`
- Groq: <https://console.groq.com/keys>
- DeepInfra: <https://deepinfra.com/dash/api_keys>
- Tavily: <https://tavily.com/>
- Voyage AI: <https://www.voyageai.com/>
- ElevenLabs: <https://elevenlabs.io/>

### Custom LLM Providers

Add OpenAI-compatible providers like llama.cpp, Ollama, or LM Studio:

```json
{
  "custom_llm_providers": [
    {
      "name": "llama.cpp",
      "type": "openai_compatible",
      "api_base_url": "http://localhost:8009",
      "api_key": "",
      "default_model_id": "qwen-14b",
      "available_models": [
        {
          "id": "../Qwen3-14B-Q6_K.gguf",
          "name": "Local Qwen 14B",
          "provider": "llama.cpp",
          "capabilities": ["thinking", "vision"],
          "default": true,
          "description": "Quantized Qwen model running locally",
          "input_token_price_1m": 0.0,
          "output_token_price_1m": 0.0
        }
      ],
      "is_stream": false,
      "extra_headers": {}
    }
  ]
}
```

**Fields:**

- `name` - Unique identifier for the provider
- `type` - Always `"openai_compatible"` for custom providers
- `api_base_url` - Base URL for the API endpoint
- `api_key` - API key if required (leave empty for local servers)
- `default_model_id` - Default model to use
- `available_models` - List of models this provider offers
- `is_stream` - Whether to enable streaming responses
- `extra_headers` - Additional HTTP headers for requests

**Model Capabilities:**

- `"thinking"` - Supports extended reasoning (like Claude's thinking mode)
- `"vision"` - Can process images
- `"function_calling"` - Supports tool/function calls

### Global Settings

Control UI appearance and system behavior:

```json
{
  "global_settings": {
    "theme": "saigontech",
    "yolo_mode": false,
    "auto_context_shrink": true
  }
}
```

**Settings:**

- `theme` - UI color scheme
  - Options: `"saigontech"`, `"dracula"`, `"nord"`, `"catppuccin"`, `"unicorn"`,
    `"atom_light"`
  - Default: `"saigontech"`

- `yolo_mode` - Auto-approve all tool usage without prompts
  - `true` - Tools execute automatically (use with caution)
  - `false` - Prompt for approval before each tool use (safer)
  - Default: `false`

- `auto_context_shrink` - Automatically consolidate conversation history
  - `true` - Reduce token usage by summarizing old messages
  - `false` - Keep full conversation history
  - Default: `true`

### Auto-Approval Tools

Specify tools that never require approval, even when `yolo_mode` is false:

```json
{
  "auto_approval_tools": ["web_search", "fetch_webpage", "read_file"]
}
```

Tools that modify state (like `file_write_or_edit`, `command_execution`) should
generally require approval.

### Last Used Settings

AgentCrew tracks your last session to restore context on startup:

```json
{
  "last_used": {
    "model": "github_copilot/gpt-4.1",
    "provider": "github_copilot",
    "timestamp": "2025-10-24T14:16:27.731875",
    "agent": "Architect"
  }
}
```

This is automatically maintained. You don't need to edit it manually.

## Agent Configuration (agents.toml)

Define your specialized agents with custom instructions and tool access.

### Basic Agent Structure

```toml
[[agents]]
name = "CodeAssistant"
description = "Specialized in code review and refactoring"
system_prompt = """You are an expert software engineer.
Focus on code quality, security, and maintainability.
Current date: {current_date}"""
tools = ["code_analysis", "file_editing", "web_search", "memory"]
temperature = 0.7
enabled = true
voice_enabled = false
voice_id = "kHhWB9Fw3aF6ly7JvltC"
```

### Agent Fields

**Required:**

- `name` - Unique identifier (used in commands and transfers)
- `description` - Brief explanation of agent's purpose (helps with
  auto-transfers)
- `system_prompt` - Instructions that define the agent's behavior
- `tools` - List of tools the agent can use

**Optional:**

- `temperature` - Creativity level (0.0-1.0, default: 0.7)
  - Lower (0.0-0.3): Focused, deterministic, good for code
  - Medium (0.4-0.7): Balanced, good for most tasks
  - Higher (0.8-1.0): Creative, varied, good for writing
- `enabled` - Whether agent appears in selection menu (default: true)
- `voice_enabled` - Enable voice features for this agent (default: false)
- `voice_id` - ElevenLabs voice ID for this agent's voice output
  - Find voice IDs at <https://elevenlabs.io/app/voice-library>
  - Default: `"kHhWB9Fw3aF6ly7JvltC"` (Marcus)

### System Prompt Variables

AgentCrew automatically replaces these placeholders:

- `{current_date}` - Today's date (e.g., "Monday, 27 October 2025")
- `{agent_name}` - The agent's name
- Custom variables can be added in future versions

### Available Tools

Specify which tools your agent can access:

**Code & File Operations:**

- `code_analysis` - Analyze repository structure, read files
- `file_editing` - Create or modify files with search-replace blocks
- `read_file` - Read file contents (subset of code_analysis)

**Web & Research:**

- `web_search` - Search the internet via Tavily
- `fetch_webpage` - Extract content from URLs
- `browser` - Full browser automation (navigate, click, form fill)

**System Integration:**

- `command_execution` - Execute shell commands
- `clipboard` - Read/write system clipboard
- `image_generation` - Generate images with DALL-E

**Memory & Context:**

- `memory` - Store and retrieve conversation context
- `adaptive_learning` - Learn behavioral patterns

**Transfer:**

- `transfer` - Transfer tasks to other agents (automatically available)

### Remote Agents

Connect to agents running on other AgentCrew instances:

```toml
[[remote_agents]]
name = "ExpertResearcher"
description = "Specialized research agent on remote server"
url = "https://agents.example.com:41241"
headers = { "Authorization" = "Bearer token123" }
enabled = true
```

**Fields:**

- `url` - A2A server endpoint
- `headers` - Optional authentication headers
- Other fields same as local agents

### Agent Examples

**Minimal Agent:**

```toml
[[agents]]
name = "SimpleAssistant"
description = "General purpose helper"
system_prompt = "You are a helpful assistant."
tools = ["web_search", "memory"]
```

**Specialized Developer Agent:**

```toml
[[agents]]
name = "BackendEngineer"
description = "Python backend development specialist"
system_prompt = """You are a senior Python backend engineer.

Focus areas:
- API design and implementation
- Database optimization
- Security best practices
- Performance tuning

Always consider scalability and maintainability.
Current date: {current_date}"""
tools = ["code_analysis", "file_editing", "web_search", "command_execution", "memory"]
temperature = 0.5
```

**Research Agent with Voice:**

```toml
[[agents]]
name = "Researcher"
description = "Deep research and analysis"
system_prompt = """You are a systematic researcher.

Process:
1. Break down research questions
2. Search multiple sources
3. Cross-reference information
4. Synthesize findings
5. Cite sources

Today is {current_date}."""
tools = ["web_search", "fetch_webpage", "browser", "memory"]
temperature = 0.6
voice_enabled = true
voice_id = "pNInz6obpgDQGcFmaJgB"
```

**Writing Agent:**

```toml
[[agents]]
name = "TechnicalWriter"
description = "Technical documentation specialist"
system_prompt = """You write clear, concise technical documentation.

Style guidelines:
- Use active voice
- Short sentences and paragraphs
- Clear headings and structure
- Code examples when relevant
- Avoid jargon unless necessary

Current date: {current_date}"""
tools = ["web_search", "file_editing", "memory"]
temperature = 0.8
```

## MCP Server Configuration (mcp_servers.json)

Model Context Protocol (MCP) extends agent capabilities with external tools.

### Basic MCP Server

```json
{
  "github": {
    "name": "github",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {
      "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."
    },
    "enabledForAgents": ["CodeAssistant", "Researcher"],
    "streaming_server": false
  }
}
```

### MCP Server Fields

**Required:**

- `name` - Unique identifier for the server
- `command` - Executable to run (npx, docker, uv, python, etc.)
- `args` - Command-line arguments

**Optional:**

- `env` - Environment variables for the server process
- `enabledForAgents` - List of agents that can use this server
  - Empty array `[]` = available to all agents
  - `["Agent1", "Agent2"]` = only these agents can use it
- `streaming_server` - Set to `true` for SSE-based MCP servers
- `url` - For remote MCP servers instead of local command

### MCP Server Examples

**Docker-based GitHub Server:**

```json
{
  "github": {
    "name": "github",
    "command": "docker",
    "args": [
      "run",
      "-i",
      "--rm",
      "-e",
      "GITHUB_PERSONAL_ACCESS_TOKEN",
      "mcp/github"
    ],
    "env": {
      "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."
    },
    "enabledForAgents": []
  }
}
```

**UV-based Python Server:**

```json
{
  "filesystem": {
    "name": "filesystem",
    "command": "/home/user/.local/bin/uv",
    "args": ["--directory", "/path/to/mcp/server/", "run", "filesystem_server"],
    "env": {},
    "enabledForAgents": ["CodeAssistant"]
  }
}
```

**NPX-based Server with Arguments:**

```json
{
  "filesystem": {
    "name": "filesystem",
    "command": "npx",
    "args": [
      "-y",
      "@modelcontextprotocol/server-filesystem",
      "/home/user/workspace"
    ],
    "env": {},
    "enabledForAgents": []
  }
}
```

**Remote MCP Server:**

```json
{
  "remote_tools": {
    "name": "remote_tools",
    "url": "https://mcp.example.com/tools",
    "streaming_server": true,
    "enabledForAgents": ["Researcher"]
  }
}
```

## Adaptive Behaviors (persistents/adaptive.json)

Agents learn patterns from interactions and store them as behaviors. All
behaviors are stored in a single file: `~/.AgentCrew/persistents/adaptive.json`

### Behavior Format

The file structure:

```json
{
  "AgentName": {
    "behavior_id": "when [condition], do [action]"
  }
}
```

All behaviors follow the pattern: `when [condition], do [action]`

### Example Adaptive Behaviors File

```json
{
  "CodeAssistant": {
    "python_testing_tool": "when working with python project do use uv as tool/script for testing",
    "project_workflow_analysis": "when beginning work on a project, do start with analyze_repo to understand overall project structure first before making any changes"
  },
  "Architect": {
    "communication_style_analysis": "when explaining complex codebases, do provide structured summaries focusing on architecture patterns, data flow, and key integration points rather than implementation details"
  },
  "Researcher": {
    "research_methodology": "when conducting research, do cross-reference multiple sources before drawing conclusions"
  },
  "default": {
    "personalization_checkout_info": "when user proceeds to checkout, remember their information for future use"
  }
}
```

### Managing Behaviors

**View Behaviors:**

- GUI: Settings → Agent Config → Adaptive Behaviors
- File: `~/.AgentCrew/persistents/adaptive.json`

**Add Behavior:**

- Through conversation: Agents learn from corrections and preferences
- Manual: Edit the JSON file directly

**Behavior Scope:**

- Agent-specific behaviors apply only to that agent
- `"default"` behaviors apply to all agents

Behaviors automatically apply when conditions match. They persist across
sessions.

## Environment Variables

You can use configuration with environment variables:

```bash
# API Keys
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-proj-..."
export GEMINI_API_KEY="AIza..."

# Override config location
export AGENTCREW_CONFIG_DIR="/custom/path"

# Enable debug logging
export LOGURU_LEVEL="DEBUG"
```

> **NOTED**: config.json values take priority over environment variables.

## Configuration Priority

AgentCrew loads settings in this order (later overrides earlier):

1. Default values (hardcoded in source)
2. Environment variables
3. Command-line arguments
4. Configuration files (`~/.AgentCrew/config.json`)

Example:

```bash
# Uses config.json settings
agentcrew chat

# Overrides provider from config
agentcrew chat --provider openai

# Overrides both config and provider
OPENAI_API_KEY="sk-new..." agentcrew chat --provider openai
```

## GUI Configuration

The GUI provides visual configuration management:

**Settings Menu:**

- Global Settings: API keys, theme, yolo mode
- Agents: Create, edit, import, export agents
- MCP Servers: Add, configure, enable MCP servers
- Adaptive Behaviors: View and manage learned behaviors

## Configuration Best Practices

**Security:**

- Never commit API keys to version control
- Use environment variables in shared configs
- Restrict file permissions: `chmod 600 ~/.AgentCrew/config.json`
- Rotate API keys regularly

**Organization:**

- Create focused agents for specific tasks
- Use descriptive names and descriptions
- Document custom system prompts
- Group related MCP servers

**Performance:**

- Enable only necessary tools for each agent
- Use auto_approval_tools for read-only operations
- Enable auto_context_shrink for long conversations
- Choose appropriate temperature for task type

**Maintenance:**

- Backup configuration before major changes
- Test agent configs before deployment
- Review adaptive behaviors periodically
- Keep MCP server versions updated

## Troubleshooting

**Agent not available:**

- Check `enabled = true` in agents.toml
- Verify agent name has no typos
- Restart AgentCrew after config changes

**Tool not working:**

- Confirm tool is in agent's `tools` list
- Check required API keys are set
- Review tool approval settings
- Check logs for error messages

**MCP server fails:**

- Verify command is in PATH
- Check environment variables are set
- Test command manually in terminal
- Review `enabledForAgents` restrictions
- Check server logs for errors

**API key errors:**

- Verify key is correct and active
- Check key has required permissions
- Ensure no extra spaces or quotes
- Test key with provider's CLI or API directly

**Configuration not loading:**

- Check file permissions (readable)
- Verify JSON/TOML syntax is valid
- Look for error messages on startup
- Try `--config` flag to specify file explicitly

**Voice not working:**

- Make sure start with `--with-voice` flag
- Verify `ELEVENLABS_API_KEY` or `DEEPINFRA_API_KEY` is set
- Check `voice_enabled = true` for the agent
- Test voice ID at ElevenLabs website
- Check logs for voice service errors

## Advanced Topics

### Multi-Environment Setup

Manage different configs for work/personal/testing:

```bash
# Testing with custom agents
agentcrew chat --agent-config ./test-agents.toml
```

### Team Configuration Sharing

Share agent configs without exposing API keys:

1. Export agents: `/export agent1,agent2 team_agents.toml`
2. Remove API keys from shared config
3. Document required environment variables
4. Use a template config.json with placeholder keys
5. Team members set their own keys locally

**Example team config:**

```json
{
  "api_keys": {
    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
    "OPENAI_API_KEY": "${OPENAI_API_KEY}"
  }
}
```

Team members set actual keys via environment variables.

### A2A Server Configuration

When running as an A2A server, configure which agents are exposed:

```bash
# Expose all agents
agentcrew a2a-server --port 41241

# Expose specific agents
agentcrew a2a-server --agents "Researcher,CodeAssistant"

# With authentication (add reverse proxy)
agentcrew a2a-server --host 127.0.0.1 --port 41241
```

Add authentication via nginx or Apache reverse proxy. AgentCrew doesn't handle
authentication directly.

## Configuration Schema Reference

For schema validation and IDE support, see the configuration schema definitions
in:

- `AgentCrew/modules/config/config_management.py`
- Example configs in `examples/agents/`

---

**Need Help?**

- GitHub Issues: <https://github.com/saigontechnology/AgentCrew/issues>
- Documentation: <https://github.com/saigontechnology/AgentCrew>
- Examples: `examples/agents/` in the repository
