# AgentCrew Configuration Guide

This guide covers AgentCrew's global settings, credentials, agents, MCP servers,
adaptive behaviors, and runtime overrides. All example credentials are
placeholders: replace them locally or remove unused entries. Never commit a real
credential.

## Configuration Files

The production CLI stores configuration in `~/.AgentCrew/` by default (or
`%USERPROFILE%\.AgentCrew\` on Windows):

```text
~/.AgentCrew/
├── config.json
├── agents.toml
├── mcp_servers.json
├── persistents/
│   └── adaptive.json
└── conversations/
```

| File | Purpose |
| --- | --- |
| `config.json` | Global settings, supported API keys, last-used state, and custom LLM providers. |
| `agents.toml` | Local and remote agent definitions. |
| `mcp_servers.json` | Model Context Protocol server definitions. |
| `persistents/adaptive.json` | Learned adaptive behaviors; normally auto-managed. |

You can edit these files directly or use the GUI settings panel. Restart or
reload AgentCrew after direct edits when the active process does not pick them
up automatically.

## Global Configuration (`config.json`)

`config.json` contains global settings, credentials, remembered selections, and
custom LLM definitions. Set `AGENTCREW_CONFIG_PATH` before startup to use a
different file.

### Complete Example

This complete, copy-pasteable template contains every user-editable top-level
section. `last_used` is auto-managed by AgentCrew and is included only to show
the full file shape.

```json
{
  "api_keys": {
    "ANTHROPIC_API_KEY": "YOUR_ANTHROPIC_API_KEY",
    "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY",
    "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
    "DEEPINFRA_API_KEY": "YOUR_DEEPINFRA_API_KEY",
    "TOGETHER_API_KEY": "YOUR_TOGETHER_API_KEY",
    "OPENCODE_API_KEY": "YOUR_OPENCODE_API_KEY",
    "COMMAND_CODE_API_KEY": "YOUR_COMMAND_CODE_API_KEY",
    "GITHUB_COPILOT_API_KEY": "YOUR_GITHUB_COPILOT_API_KEY",
    "FIREWORKS_API_KEY": "YOUR_FIREWORKS_API_KEY",
    "TAVILY_API_KEY": "YOUR_TAVILY_API_KEY",
    "VOYAGE_API_KEY": "YOUR_VOYAGE_API_KEY",
    "ELEVENLABS_API_KEY": "YOUR_ELEVENLABS_API_KEY"
  },
  "auto_approval_tools": [
    "read_file"
  ],
  "global_settings": {
    "theme": "dark",
    "swap_enter": false,
    "yolo_mode": false,
    "auto_context_shrink": true,
    "tool_result_summary_enabled": true,
    "shrink_excluded": [],
    "trusted_project_plugins": false,
    "agent_mode": "transfer"
  },
  "last_used": {
    "model": "",
    "provider": "",
    "timestamp": "",
    "agent": ""
  },
  "custom_llm_providers": [
    {
      "name": "example_provider",
      "type": "openai_compatible",
      "api_base_url": "https://llm.example.com/v1",
      "api_key": "YOUR_CUSTOM_PROVIDER_API_KEY",
      "default_model_id": "example-model",
      "available_models": [
        {
          "id": "example-model",
          "name": "Example model",
          "provider": "example_provider",
          "capabilities": ["tool_use", "stream"],
          "default": true,
          "description": "Example OpenAI-compatible model",
          "input_token_price_1m": 0.0,
          "output_token_price_1m": 0.0,
          "cached_token_price_1m": 0.0,
          "default_reasoning": null,
          "max_context_token": 72000,
          "service_name": null,
          "force_sample_params": null
        }
      ],
      "extra_headers": {}
    }
  ]
}
```

At least one hosted LLM needs a provider credential or an applicable
OAuth/subscription login. Remove unused entries, or use empty strings, rather
than keeping placeholder text in a live configuration.

### API Keys and Credential Scope

The following twelve names are the complete `config.json.api_keys` allow-list.
At startup, AgentCrew copies each non-empty value into the process environment.
They can also be set directly as environment variables.

| Key | Purpose/provider |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic Claude provider credential. |
| `GEMINI_API_KEY` | Google Gemini provider credential. |
| `OPENAI_API_KEY` | OpenAI API provider credential. It is not required for OpenAI Codex subscription login. |
| `DEEPINFRA_API_KEY` | DeepInfra LLM, image-generation, or voice credential. |
| `TOGETHER_API_KEY` | Together AI provider credential. |
| `OPENCODE_API_KEY` | OpenCode provider credential, when required by the selected account or deployment. |
| `COMMAND_CODE_API_KEY` | Command Code provider credential. |
| `GITHUB_COPILOT_API_KEY` | GitHub Copilot provider credential. Use `agentcrew copilot-auth` for the supported Copilot login flow. |
| `FIREWORKS_API_KEY` | Fireworks AI provider credential. |
| `TAVILY_API_KEY` | Tavily web-search feature credential; not an LLM provider credential. |
| `YDC_API_KEY` | You.com web-search credential, used when `WEB_SEARCH_PROVIDER` is `youcom`. Optional; without it the You.com provider runs against the keyless free profile. |
| `WEB_SEARCH_PROVIDER` | Web-search backend selector. Set to `youcom` to use the You.com web search backend instead of the default Tavily. |
| `VOYAGE_API_KEY` | Voyage embedding credential for the normal memory path. |
| `ELEVENLABS_API_KEY` | ElevenLabs voice-synthesis credential. |

For OpenAI Codex subscription authentication, use `agentcrew chatgpt-auth`.
That flow stores OAuth state and does not introduce another `*_API_KEY` setting.

Every distinct `*_API_KEY` name referenced by runtime source is covered here:

| Name | Scope and use |
| --- | --- |
| The twelve keys above | Accepted under `config.json.api_keys` and loaded into the environment at startup. |
| `A2A_SERVER_API_KEY` | Environment-only inbound A2A server authentication key. Alternatively pass `a2a-server --api-key`. It is not a provider credential and is not read from `config.json.api_keys`. |
| `CHROMA_OPENAI_API_KEY` | Advanced, low-level OpenAI-compatible embedding-adapter default. It is not used by the normal memory setup path. |
| `CHROMA_GOOGLE_GENAI_API_KEY` | Advanced, low-level Google GenAI embedding-adapter default. It is not used by the normal memory setup path. |
| `CHROMA_VOYAGE_API_KEY` | Advanced, low-level Voyage embedding-adapter default. It is not used by the normal memory setup path; use `VOYAGE_API_KEY` for normal memory. |
| `custom_llm_providers[].api_key` | Per-custom-provider field in `config.json`, not a named environment variable. |

Normal memory selects Voyage when `VOYAGE_API_KEY` is available. Otherwise it
uses Chroma's default embedding function; users normally do not need any
`CHROMA_*` key.

### Web Search Provider

The `web_search` tool defaults to Tavily. To use You.com instead, set
`WEB_SEARCH_PROVIDER=youcom`:

```bash
export WEB_SEARCH_PROVIDER=youcom
agentcrew chat
```

The You.com provider talks to the You.com MCP endpoint and works without any
API key (keyless free profile). Optionally set `YDC_API_KEY` (get one at
<https://you.com/platform/api-keys>) to switch to the authenticated endpoint,
which also enables server-side URL content extraction
for the `fetch_webpage` tool; without a key, `fetch_webpage` falls back to
fetching the page directly and converting it to markdown. `crawl_website` is
not supported by the You.com provider and returns an explanatory message
pointing to `fetch_webpage` / `search_web`.

### Custom LLM Providers

Use `custom_llm_providers` for an OpenAI-compatible endpoint such as a local
server or supported gateway. The complete template above shows the persisted
shape. `name`, `api_base_url`, and model `id` values must match the provider you
intend to use, and `default_model_id` must identify an entry in
`available_models`.

Model capability values include `tool_use`, `thinking`, `vision`, `stream`, and
`structured_output`. `force_sample_params`, when supplied, is an object that can
contain supported sampling overrides such as `temperature`, `top_p`, `min_p`,
`top_k`, `frequency_penalty`, `presence_penalty`, or `repetition_penalty`.

### Global Settings

The complete template shows all active settings and their defaults:

- `theme`: `"dark"` by default.
- `swap_enter`: `false` by default; switches Enter-key behavior in the chat UI.
- `yolo_mode`: `false` by default; when true, automatically approves all tool
  use.
- `auto_context_shrink`: `true` by default; shrinks older tool results when the
  context nears its limit.
- `tool_result_summary_enabled`: `true` by default; enables background factual
  summaries used by context shrinking.
- `shrink_excluded`: empty by default; names tools whose results must not be
  shrunk.
- `trusted_project_plugins`: `false` by default; enables discovered project
  plugins only when explicitly trusted.
- `agent_mode`: `"transfer"` by default. Allowed values are `"transfer"`,
  `"delegate"`, and `"none"`.

`auto_context_shrink` starts shrinking after input usage reaches 85% of the
configured model context limit. The newest messages remain intact, and summary
creation is asynchronous; if a matching summary is unavailable, AgentCrew uses
a compact tool-result placeholder instead.

### Plugins

AgentCrew discovers project plugins in `.agentcrew/plugins/` and global plugins
in `~/.AgentCrew/plugins/`. Project plugins are not activated unless
`global_settings.trusted_project_plugins` is true. See
[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) for lifecycle and security
details.

### Auto-Approval Tools and Last-Used State

`auto_approval_tools` lists tools that do not require approval when `yolo_mode`
is false. Limit this list to trusted, low-risk tools.

`last_used` records the selected model, provider, agent, and timestamp. It is
maintained by AgentCrew; do not use it as a shared configuration setting.

## Agent Configuration (`agents.toml`)

Define specialized local agents and remote A2A agents in `agents.toml`. Set
`SW_AGENTS_CONFIG` before startup to use another path.

### Complete Local and Remote Example

```toml
[[agents]]
name = "ExampleAssistant"
description = "Example local AgentCrew agent"
system_prompt = "You are a helpful assistant. Today is {current_date}."
tools = ["web_search", "read_file"]
enabled = true
temperature = 0.7
voice_enabled = "disabled"
voice_id = "YOUR_ELEVENLABS_VOICE_ID"
model_id = "openai/YOUR_MODEL_ID"
reason_effort = "medium"

[[remote_agents]]
name = "ExampleRemoteAgent"
description = "Example remote AgentCrew agent"
base_url = "https://agents.example.com"
enabled = true
headers = { Authorization = "Bearer YOUR_A2A_TOKEN" }
```

### Agent Fields

Local agent fields are:

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Unique local-agent identifier. |
| `description` | No | Short purpose used for selection and transfers. Defaults to `""`. |
| `system_prompt` | No | Agent instructions. `{current_date}` and `{agent_name}` are replaced automatically. Defaults to `""`. |
| `tools` | No | List of tool names available to the agent. Defaults to `[]`. |
| `enabled` | No | Whether the agent is available. Defaults to `true`. |
| `temperature` | No | Sampling temperature. |
| `voice_enabled` | No | String flag: `"enabled"` or `"disabled"`; not a Boolean. |
| `voice_id` | No | ElevenLabs voice ID. |
| `model_id` | No | Registered or provider-qualified model ID. |
| `reason_effort` | No | Reasoning level supported by the selected model. |

Remote agent fields are `name`, `description`, `base_url`, `enabled`, and
`headers`. `base_url` is the remote A2A server endpoint. Remote entries do not
inherit local-agent fields such as `tools`, `model_id`, or `voice_enabled`.

### Tool Selection

Common built-in tools include `web_search`, `fetch_webpage`, `read_file`,
`command_execution`, `browser`, `memory`, and `transfer`. Enable only the tools
an agent needs. Tool availability can vary with the installed dependencies and
configured services.

## MCP Server Configuration (`mcp_servers.json`)

MCP servers extend agents with external tools. Set `MCP_CONFIG_PATH` before
startup to use another path.

### Complete MCP Examples

A local stdio server starts `command` with `args`. A remote streaming server uses
`url` with `streaming_server` set to `true`; an `/sse` URL selects legacy SSE,
while other URLs use Streamable HTTP. Both entries below include the full
`MCPServerEntry` field set.

```json
{
  "example_stdio": {
    "name": "example_stdio",
    "command": "npx",
    "args": ["-y", "@example/mcp-server"],
    "env": {
      "EXAMPLE_SERVER_TOKEN": "YOUR_EXAMPLE_SERVER_TOKEN"
    },
    "enabledForAgents": ["ExampleAssistant"],
    "streaming_server": false,
    "url": "",
    "headers": {},
    "includeTools": ["example_tool"]
  },
  "example_remote": {
    "name": "example_remote",
    "command": "",
    "args": [],
    "env": {},
    "enabledForAgents": [],
    "streaming_server": true,
    "url": "https://mcp.example.com/mcp",
    "headers": {
      "Authorization": "Bearer YOUR_MCP_TOKEN"
    },
    "includeTools": ["search", "fetch"]
  }
}
```

| Field | Description |
| --- | --- |
| `name` | Server identifier. |
| `command` and `args` | Local stdio executable and arguments. Leave empty for remote streaming servers. |
| `env` | Environment variables passed to a local server process. |
| `enabledForAgents` | Agent names allowed to use the server. An empty list makes it available to all agents. |
| `streaming_server` | `false` for local stdio; `true` for remote HTTP/SSE. |
| `url` | Remote server URL. Leave empty for local stdio servers. |
| `headers` | Optional request headers, normally for remote authentication. |
| `includeTools` | Optional allow-list of MCP tool names. |

Remote servers can use static headers or an OAuth flow. When OAuth is required,
AgentCrew opens the authorization URL and persists the resulting token state
under the persistence directory.

MCP discovery runs after agent activation. Built-in tools are available
immediately while discovered MCP tools are synchronized in the background.

## Adaptive Behaviors (`persistents/adaptive.json`)

Agents learn behavior patterns and store them in one shared file. Set
`AGENTCREW_ADAPTIVE_PATH` to place it elsewhere.

```json
{
  "ExampleAssistant": {
    "python_tools": "when working with a Python project, use uv to run Python commands"
  },
  "default": {
    "concise_status": "when providing progress updates, keep them concise and actionable"
  }
}
```

Each behavior follows `when [condition], [action]`. Agent-specific behaviors
apply only to that agent; `default` behaviors apply to all agents. Prefer the
GUI or conversation-driven learning for normal edits.

## Environment Variables

Set environment variables before starting AgentCrew. The twelve standard API
keys in the table above can be set directly this way. A non-empty supported key
in `config.json.api_keys` is exported into the startup process.

AgentCrew reads JSON string values literally. Do not use `${VAR}` interpolation
inside `config.json`; use environment variables directly for shared setups.

```bash
export AGENTCREW_CONFIG_PATH="/path/to/config.json"
export SW_AGENTS_CONFIG="/path/to/agents.toml"
export MCP_CONFIG_PATH="/path/to/mcp_servers.json"
export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
export AGENTCREW_PROVIDER="openai"
export AGENTCREW_MODEL_ID="YOUR_MODEL_ID"
```

### Paths, Runtime, and Logging

| Variable | Purpose |
| --- | --- |
| `AGENTCREW_CONFIG_PATH` | Path to `config.json`. |
| `SW_AGENTS_CONFIG` | Path to `agents.toml`. |
| `MCP_CONFIG_PATH` | Path to `mcp_servers.json`. |
| `MEMORYDB_PATH` | Path to the Chroma memory database. |
| `AGENTCREW_PERSISTENCE_DIR` | Base directory for conversations, tokens, and persisted state. |
| `AGENTCREW_ADAPTIVE_PATH` | Explicit path to `adaptive.json`. |
| `AGENTCREW_ACP_SESSION_DIR` | Directory for persisted ACP session JSON. Defaults to `.agentcrew/acp_sessions` when no constructor base directory is supplied. |
| `AGENTCREW_PROVIDER` | Provider override; accepts a built-in provider or configured custom-provider name. |
| `AGENTCREW_MODEL_ID` | Runtime model-ID override. |
| `AGENTCREW_FAST_CODEX` | Set to `1` to request the priority service tier for OpenAI Codex. |
| `AGENTCREW_LOG_PATH` | Log directory in production mode. |
| `AGENTCREW_LOG_LEVEL` | Log level, such as `ERROR`, `WARNING`, or `DEBUG`. |
| `AGENTCREW_ENV` | Runtime environment; production enables file logging. |

### Context Shrinking and Summaries

| Variable | Purpose | Default |
| --- | --- | --- |
| `AGENTCREW_DEFAULT_MAX_CONTEXT` | Token limit used before the 85% shrink trigger is calculated. | Selected model's context limit |
| `AGENTCREW_CONTEXT_SHRINK_THRESHOLD` | Number of most-recent messages preserved in full. | `10` |
| `AGENTCREW_TOOL_SUMMARY_TIMEOUT_SECONDS` | Timeout for one background tool-summary request. | `5` seconds |
| `AGENTCREW_TOOL_SUMMARY_MAX_PROMPT_CHARS` | Maximum summary-provider prompt payload. | `30000` characters |

### Provider, A2A, Network, and Advanced Overrides

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_BASE_URL` | Anthropic-compatible API base URL override. |
| `OPENAI_BASE_URL` | OpenAI-compatible API base URL override. |
| `GEMINI_BASE_URL` | Gemini API base URL override. |
| `TOGETHER_BASE_URL` | Together API base URL override. |
| `A2A_SERVER_API_KEY` | Inbound A2A server authentication key; equivalent to the `a2a-server --api-key` option. |
| `A2A_SERVER_EXPOSED_URL` | Public URL advertised by an A2A server when it differs from its bound URL. |
| `AGENTCREW_HUB_HOST` | Base host used to resolve `@hub/...` agent configuration URIs. Defaults to `https://agentplace.cloud`. |
| `AGENTCREW_VISION_CONCURRENCY` | Maximum concurrent document-image descriptions. |
| `AGENTCREW_VISION_CACHE_PATH` | Directory for the vision-description cache. |
| `AGENTCREW_VISION_CACHE_DISABLED` | Set to `true` to disable the vision-description cache. |
| `AGENTCREW_BROWSER_PROFILE_PATH` | Browser automation profile directory. |

`AGENTCREW_DISABLE_GUI` and `AGENTCREW_DOCKER` are internal process-set flags,
not normal user configuration.

## Configuration Resolution

Runtime provider, model, and reasoning options take precedence when supplied by
the CLI or application entry point. Otherwise AgentCrew resolves configuration,
environment values, remembered `last_used` selections, and defaults for the
active provider/model. Use explicit CLI options when a one-off run must not use
the remembered selection.

## GUI Configuration

The GUI settings panel manages global settings, API keys, agents, MCP servers,
custom LLM providers, and adaptive behaviors. It writes the same files described
in this guide.

## Configuration Best Practices

### Security

- Never commit API keys, bearer tokens, or provider passwords.
- Prefer environment variables for shared, CI, container, and team setups.
- Restrict local global-config permissions where supported, for example:
  `chmod 600 ~/.AgentCrew/config.json`.
- Rotate credentials and use the smallest practical provider/token scope.

### Organization and Performance

- Create focused agents with descriptive names and limited tool access.
- Use `auto_approval_tools` only for operations you trust.
- Keep `auto_context_shrink` enabled for long conversations unless full tool
  outputs are required.
- Enable only MCP servers needed by each agent, and use `enabledForAgents` or
  `includeTools` to limit access.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Provider is unavailable | Verify a supported provider credential or login, `AGENTCREW_PROVIDER`, and `AGENTCREW_MODEL_ID`. |
| Agent is unavailable | Check `enabled = true`, the TOML syntax, and the `SW_AGENTS_CONFIG` path. |
| MCP server fails | Confirm executable/URL, JSON syntax, `env`/`headers`, and `enabledForAgents`. Test the local command separately. |
| API key error | Check that the credential is active, correctly named, and has no accidental whitespace. Do not print it in logs. |
| Configuration does not load | Check the active path override, file permissions, and JSON/TOML syntax. |
| Voice is unavailable | Start with `--with-voice`, configure `ELEVENLABS_API_KEY` or `DEEPINFRA_API_KEY`, and use `voice_enabled = "enabled"`. |

## Advanced Topics

### Multi-Environment Setup

Use path overrides to isolate work, personal, or test configurations:

```bash
AGENTCREW_CONFIG_PATH="/path/to/work-config.json" \
SW_AGENTS_CONFIG="/path/to/work-agents.toml" \
MCP_CONFIG_PATH="/path/to/work-mcp.json" \
agentcrew chat
```

### Team Configuration Sharing

Share `agents.toml`, MCP templates without secrets, and a `config.json` template
whose values are empty or placeholders. Each team member sets real credentials
in their own environment:

```bash
export ANTHROPIC_API_KEY="YOUR_ANTHROPIC_API_KEY"
export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
```

### A2A Server Authentication

```bash
A2A_SERVER_API_KEY="YOUR_A2A_SERVER_API_KEY" agentcrew a2a-server --host 127.0.0.1 --port 41241
```

You can instead pass `--api-key` to `a2a-server`. A reverse proxy can provide
additional deployment-specific controls.

### Document Processing

Documents added to chat are converted to Markdown before being sent to the
agent. If document images are described, AgentCrew uses the configured provider
credentials; without a suitable credential, document conversion continues
without image descriptions.

## Configuration Schema Reference

The runtime sources are the authoritative schema references:

- `AgentCrew/modules/config/global_config.py`
- `AgentCrew/modules/config/agents_config.py`
- `AgentCrew/modules/config/mcp_config.py`
- `AgentCrew/setup.py`

**Need Help?**

- GitHub Issues: <https://github.com/saigontechnology/AgentCrew/issues>
- Documentation: <https://github.com/saigontechnology/AgentCrew>
- Examples: `examples/agents/` in the repository
