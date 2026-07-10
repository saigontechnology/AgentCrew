<p align="center">
  <a href="https://github.com/saigontechnology/AgentCrew">
    <img src="https://saigontechnology.com/wp-content/uploads/2024/09/logo-black-1.svg" alt="AgentCrew Logo" width="300">
  </a>
</p>

<h1 align="center">AgentCrew</h1>

<p align="center">
  <strong>Your team of AI specialists for coding, research, and automation.</strong><br>
  Run multiple focused agents from a desktop app, terminal, or API.
</p>

<p align="center">
  <a href="https://github.com/saigontechnology/AgentCrew/stargazers"><img src="https://img.shields.io/github/stars/saigontechnology/AgentCrew" alt="GitHub stars"></a>
  <a href="https://pepy.tech/projects/agentcrew-ai"><img src="https://static.pepy.tech/personalized-badge/agentcrew-ai?period=total&units=INTERNATIONAL_SYSTEM&left_color=LIGHTGREY&right_color=BRIGHTGREEN&left_text=Downloads" alt="PyPI Downloads"></a>
  <a href="https://github.com/saigontechnology/AgentCrew/actions/workflows/pylint.yml"><img src="https://github.com/saigontechnology/AgentCrew/actions/workflows/pylint.yml/badge.svg" alt="Pylint"></a>
  <a href="https://github.com/saigontechnology/AgentCrew/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache2.0-yellow.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python Version"></a>
</p>

---

## What is AgentCrew?

AgentCrew lets you build and run a **team of specialized AI agents** instead of
relying on a single generic assistant.

- Give each agent a role — architect, coder, researcher, reviewer, or browser
  operator.
- Agents can hand off work to teammates when a task fits their specialty.
- Use the same team in a desktop GUI, terminal, automated jobs, or over HTTP.

**[Watch the demo](https://github.com/user-attachments/assets/32876eac-b5e6-4608-bd5e-82d6fa4db80f)**

---

## Quick Start

### 1. Install

**macOS / Linux**

```bash
curl -LsSf https://agentcrew.dev/install.sh | bash
```

**Windows**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://agentcrew.dev/install.ps1 | iex"
```

**pip (any platform)**

```bash
pip install agentcrew-ai
```



### 2. Add an API key

AgentCrew needs at least one AI provider key. Pick your preferred provider and
add the key:

**Option A — Environment variable**

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# or
export OPENAI_API_KEY="sk-proj-..."
# or
export GEMINI_API_KEY="AIza..."
```

**Option B — Config file**

```bash
mkdir -p ~/.AgentCrew
cat > ~/.AgentCrew/config.json << 'EOF'
{
  "api_keys": {
    "ANTHROPIC_API_KEY": "sk-ant-..."
  }
}
EOF
```

**Option C — Subscription login**

If you have a **ChatGPT Plus / Pro** subscription or a **GitHub Copilot**
subscription, you can log in directly instead of using an API key:

```bash
# ChatGPT Plus / Pro (Codex models)
agentcrew chatgpt-auth
agentcrew chat --provider openai_codex

# GitHub Copilot
agentcrew copilot-auth
agentcrew chat --provider github_copilot
```

Supported providers: Anthropic Claude, OpenAI, Google Gemini, GitHub Copilot,
CrofAI, DeepInfra, Together AI, OpenCode, and any OpenAI-compatible endpoint.

> **Tip:** Not sure which provider to pick? Claude and OpenAI work great for
> most users. See [CONFIGURATION.md](CONFIGURATION.md) for provider-specific
> setup details.

### 3. Launch AgentCrew

```bash
# Desktop GUI
agentcrew chat

# Terminal mode
agentcrew chat --console
```

On the first launch, AgentCrew will walk you through creating your first agent
if you do not already have one.

### 4. Create your first agent

If you already have an API key set and want to create a new agent from scratch:

```bash
agentcrew create-agent
```

Or define one manually in `~/.AgentCrew/agents.toml`:

```toml
[[agents]]
name = "CodeAssistant"
description = "Helps write and review code"
tools = ["code_analysis", "file_editing", "web_search", "memory"]
system_prompt = """You are an expert software engineer.
Focus on code quality, security, and maintainability.
Today is {current_date}."""
```

### 5. Start working

Switch between agents, attach files, and let your team handle the rest.

```
/agent Architect
Design a clean API for a task manager.

@Coding
Implement the task manager in Python using FastAPI.

@Reviewer
Review the code for security issues.
```

---

## Ways to Use AgentCrew

| Mode              | Command                                                     | Best for                                                    |
| ----------------- | ----------------------------------------------------------- | ----------------------------------------------------------- |
| **Desktop GUI**   | `agentcrew chat`                                            | Daily interactive work, file drag-and-drop, visual diffs    |
| **Terminal**      | `agentcrew chat --console`                                  | Remote servers, low-overhead use, keyboard-driven workflows |
| **One-shot jobs** | `agentcrew job --agent "CodeAssistant" "your task" ./files` | CI/CD scripts, automation, batch processing                 |
| **HTTP API**      | `agentcrew a2a-server`                                      | Integrating with other apps, multi-instance setups          |

**Job mode example:**

```bash
agentcrew job --agent "CodeAssistant" \
  "Review for security issues" \
  ./src/**/*.py
```

**A2A server example:**

```bash
agentcrew a2a-server --host 0.0.0.0 --port 41241
```

---

## What Can Agents Do?

Agents come with a toolkit you enable per agent:

- **Code analysis** — understand repo structure, read files, grep, search
- **File editing** — write or modify files with search/replace blocks and
  backups
- **Web search & extraction** — pull current information from the web
- **Browser automation** — navigate, click, fill forms, and capture screenshots
- **Command execution** — run safe shell commands with rate limits and audit
  logs
- **Memory** — remember past conversations and retrieve relevant context
- **Voice** — speak and listen using ElevenLabs or DeepInfra (optional)
- **MCP tools** — connect to external services via the Model Context Protocol
- **Structured output** — enforce JSON schema responses in job mode

---

## Example Agent Configurations

See the [`examples/agents/`](examples/agents/) folder for ready-to-use agent
setups. To use an example:

```bash
cp examples/agents/agents.simple.toml ~/.AgentCrew/agents.toml
agentcrew chat
```

---

## Configuration Files

AgentCrew stores settings in `~/.AgentCrew/`:

| File               | Purpose                                  |
| ------------------ | ---------------------------------------- |
| `config.json`      | API keys, theme, global preferences      |
| `agents.toml`      | Agent definitions, tools, system prompts |
| `mcp_servers.json` | External tool servers (optional)         |

You can edit these files directly or manage them through the GUI settings panel.

---

## Common Console Commands

Inside the chat interface, type:

- `/agent <name>` — switch to another agent
- `/clear` — start a new conversation
- `/file <path>` — attach a file
- `/think <low|medium|high|xhigh>` — enable reasoning mode
- `/voice` — start voice recording
- `/help` - show all available commands
- `exit` or `quit` — close AgentCrew

---

## Next Steps

- **[CONFIGURATION.md](CONFIGURATION.md)** — Detailed setup for providers,
  agents, MCP servers, plugins, and advanced options
- **[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)** — Build plugins with
  EventBus subscriptions and `tool.execute` hooks
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — How to build and contribute to
  AgentCrew
- **[Docker guide](docker/DOCKER.md)** — Running AgentCrew in containers

---

## License

Apache 2.0 License. See [LICENSE](LICENSE) for details.
