# Getting Started with AgentCrew

*Your first 30 minutes — from zero to running a multi-agent workflow.*

This guide walks you through installing AgentCrew, connecting to an AI provider,
creating your first agents, and running a real multi-agent task. By the end,
you will have a working team of specialists that can hand off work to each other.

---

## Prerequisites

- A computer running macOS, Linux, or Windows (with Python 3.12 or later)
- About 30 minutes
- One of the following:
  - An **OpenCode Go subscription** (curated open-source models, no API key needed)
  - A **ChatGPT Plus / Pro** or **GitHub Copilot** subscription
  - An API key from CrofAI, DeepInfra, OpenAI, Google Gemini, or another provider

---

## Step 1: Install AgentCrew

Choose the method for your platform:

**macOS / Linux — one-liner:**
```bash
curl -LsSf https://agentcrew.dev/install.sh | bash
```

**Windows — one-liner:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://agentcrew.dev/install.ps1 | iex"
```

**Any platform with Python and pip:**
```bash
pip install agentcrew-ai
```

> **What gets installed?** AgentCrew and all its core dependencies: the CLI,
> desktop GUI (PySide6), LLM service clients for all providers, browser
> automation engine, code analysis tools, MCP client, and more.
>
> **Verify the installation:**
> ```bash
> agentcrew --help
> ```
> You should see the list of available commands (`chat`, `job`, `a2a-server`, etc.).

---

## Step 2: Connect to an AI provider

AgentCrew requires a backend AI model to power your agents. Pick one:

### Option A: OpenCode Go (subscription, no API key)

The quickest way to start with curated open-source models:

```bash
agentcrew chat --provider opencode_go
```

AgentCrew prompts you for your OpenCode Go credentials on first use.
No environment variables or config files needed.

### Option B: ChatGPT Plus / Pro subscription

```bash
agentcrew chatgpt-auth
agentcrew chat --provider openai_codex
```

The `chatgpt-auth` command opens a browser for you to log in to your OpenAI
account. After that, AgentCrew uses your subscription directly.

### Option C: GitHub Copilot subscription

```bash
agentcrew copilot-auth
agentcrew chat --provider github_copilot
```

Same flow — browser-based login, no API key required.

### Option D: API key (any provider)

Set an environment variable and launch:

```bash
export CROFAI_API_KEY="crof-..."
export DEEPINFRA_API_KEY="your-key"
export OPENAI_API_KEY="sk-proj-..."
export GEMINI_API_KEY="AIza..."

agentcrew chat
```

You can also store keys permanently in `~/.AgentCrew/config.json`:

```json
{
  "api_keys": {
    "CROFAI_API_KEY": "crof-..."
  }
}
```

> **First launch:** If this is your first time, AgentCrew detects that no
> agents exist yet and starts the agent creation wizard.

---

## Step 3: Create your first agent

When you launch without any agents configured, AgentCrew walks you through
creating one interactively. But let's create two agents by hand so you
experience the full multi-agent setup.

Create the file `~/.AgentCrew/agents.toml` (the `~/.AgentCrew` directory may
not exist yet — create it):

```bash
mkdir -p ~/.AgentCrew
```

Add this content:

```toml
[[agents]]
name = "Architect"
description = "Specialized in software architecture and design"
tools = ["web_search", "code_analysis", "memory"]
system_prompt = """You are a software architect. Focus on system design,
trade-offs, and keeping things simple.
Today is {current_date}."""

[[agents]]
name = "Coding"
description = "Specialized in code implementation"
tools = ["code_analysis", "file_editing", "command_execution", "memory"]
system_prompt = """You are a focused implementer. Write clean, maintainable
code. Do not over-engineer.
Today is {current_date}."""

[[agents]]
name = "Researcher"
description = "Specialized in finding and analyzing information"
tools = ["web_search", "fetch_webpage", "memory"]
system_prompt = """You are a systematic researcher. Cross-reference multiple
sources before drawing conclusions.
Today is {current_date}."""
```

> **What each agent field does:**
> - **`name`** — used in commands like `/agent Architect` or `@Coding task`
> - **`description`** — helps other agents decide when to hand off work
> - **`tools`** — the capabilities this agent can use (code analysis, web
>   search, file editing, etc.)
> - **`system_prompt`** — instructions that shape the agent's behavior
>
> The `{current_date}` placeholder is automatically replaced with today's date.

---

## Step 4: Launch and have your first conversation

```bash
agentcrew chat --console
```

You will see the AgentCrew terminal interface with a prompt like:

```
┌─ AgentCrew ──────────────────────────────────┐
│ Agent: Coding  │  Model: crofai/qwen-14b     │
└──────────────────────────────────────────────┘
>
```

The interface shows your current agent (defaults to the first one defined) and
the active model. Type something:

```
> What is AgentCrew and what can it do?
```

The agent responds, using its available tools when needed. Try attaching a
file:

```
> /file ./README.md
> Summarize this project for me
```

> **Keyboard shortcuts:**
> - `Ctrl+C` — Stop the current agent response
> - `Up/Down arrows` — Navigate through conversation history
> - `Tab` — Auto-complete commands and agent names

---

## Step 5: Switch between agents

Type `/agent` followed by the agent name to switch:

```
> /agent Architect
> How would you design a task management API?

> /agent Researcher
> Find me the latest best practices for REST API design
```

> **When to switch agents:**
> - **Architect** — design decisions and trade-off analysis
> - **Coding** — code implementation or debugging
> - **Researcher** — information gathering and synthesis

---

## Step 6: Try a multi-agent workflow

This is where AgentCrew shines. Use the `@AgentName` syntax to hand off a
subtask to another agent:

```
> @Architect I want to build a personal task manager. Design the architecture.

> @Coding Implement the task manager based on the architecture.
```

The conversation flows as follows:

1. **You** ask the Architect to design the system
2. **Architect** responds with a design (data model, API endpoints, tech stack)
3. **You** hand the design to Coding with `@Coding`
4. **Coding** reads the Architect's design from the conversation, then
   implements the code using its tools

> **How hand-off works:** When you use `@AgentName`, the current agent
> transfers the task to the named agent. That agent receives the full
> conversation history and works with its own specialized tools and system
> prompt. When it finishes, control returns to you.

---

## Step 7: Try a one-shot job

AgentCrew can run agents non-interactively — useful for CI/CD pipelines and
automation:

```bash
agentcrew job --agent "Coding" \
  "Create a Python function that validates email addresses" \
  ./output.py
```

This runs the Coding agent with your task, saves the result to `output.py`,
and exits. No chat interface needed.

---

## What to try next

| If you want to...                              | Go here                                      |
| ---------------------------------------------- | -------------------------------------------- |
| Understand when to use each tool and feature   | [GUIDE_WORKFLOWS.md](GUIDE_WORKFLOWS.md)     |
| Configure advanced provider options            | [CONFIGURATION.md](CONFIGURATION.md)         |
| Set up the desktop GUI with themes             | `agentcrew chat` (no `--console` flag)       |
| Run AgentCrew in Docker                        | [docker/DOCKER.md](docker/DOCKER.md)         |
| Add custom LLM providers (Ollama, llama.cpp)   | [CONFIGURATION.md](CONFIGURATION.md#custom-llm-providers) |
| Enable voice interaction                       | `agentcrew chat --with-voice`                |
| Build a plugin to extend AgentCrew             | [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) |

---

## Troubleshooting

**AgentCrew won't start:**
- Verify Python 3.12+ is installed: `python3 --version`
- Check that installation completed: `pip list | grep agentcrew`
- Try reinstalling: `pip install --upgrade agentcrew-ai`

**No agents available:**
- Ensure `~/.AgentCrew/agents.toml` exists and has valid TOML syntax
- Confirm `enabled = true` (or omitted, which defaults to true) for each agent
- Run `agentcrew create-agent` for a guided setup

**"Provider not available" error:**
- Verify the API key is set correctly (environment variable or config.json)
- For subscription-based providers, run the auth command first
- Check the provider documentation in CONFIGURATION.md

**Agent doesn't respond:**
- Press `Ctrl+C` to cancel and retry
- Check your network connection
- Verify the API key has sufficient quota

**Multi-agent hand-off not working:**
- Verify the target agent name is spelled correctly
- Confirm both agents exist in `agents.toml`
- The `@Name` syntax only works from the chat interface, not from job mode

---

> **Next:** Once you are comfortable with the basics, read
> [GUIDE_WORKFLOWS.md](GUIDE_WORKFLOWS.md) to learn how to use AgentCrew
> effectively — when to use each tool, how to structure conversations, and
> best practices for multi-agent workflows.
