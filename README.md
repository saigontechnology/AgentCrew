<p align="center">
  <a href="https://github.com/saigontechnology/AgentCrew">
    <img src="https://saigontechnology.com/wp-content/uploads/2024/09/logo-black-1.svg" alt="AgentCrew Logo" width="300">
  </a>
</p>

<h1 align="center">AgentCrew</h1>

<p align="center">
  <strong>Your team of AI specialists for coding, research, and automation.</strong><br>
  Build a crew of focused agents that code, research, architect, review, and automate.
  Use them from a desktop app, terminal, HTTP API, or CI pipeline.
</p>

<p align="center">
  <a href="https://github.com/saigontechnology/AgentCrew/stargazers"><img src="https://img.shields.io/github/stars/saigontechnology/AgentCrew" alt="GitHub stars"></a>
  <a href="https://pepy.tech/projects/agentcrew-ai"><img src="https://static.pepy.tech/personalized-badge/agentcrew-ai?period=total&units=INTERNATIONAL_SYSTEM&left_color=LIGHTGREY&right_color=BRIGHTGREEN&left_text=Downloads" alt="PyPI Downloads"></a>
  <a href="https://github.com/saigontechnology/AgentCrew/actions/workflows/pylint.yml"><img src="https://github.com/saigontechnology/AgentCrew/actions/workflows/pylint.yml/badge.svg" alt="Pylint"></a>
  <a href="https://github.com/saigontechnology/AgentCrew/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache2.0-yellow.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python Version"></a>
</p>

---

## Why AgentCrew?

AgentCrew lets you **build a team of specialists, each with the right tools
and personality for the job.**

- **An Architect** that weighs trade-offs before a line of code is written.
- **A Coder** that implements cleanly — no scope creep, no over-engineering.
- **A Reviewer** that catches what others miss.
- **A Researcher** that cross-references multiple sources before drawing conclusions.
- **A Browser Operator** that navigates web apps, fills forms, and clicks through workflows.

Agents hand off work to each other when a task needs a different specialty.
You stay in control, orchestrating the team from one interface.

> **When AgentCrew fits:** You have a complex task that benefits from multiple
> perspectives — building a feature from design to deployment, researching and
> writing a report, or automating a multi-step workflow.
>
> **When a single assistant is enough:** You need one focused conversation with
> no tool access or multi-agent orchestration.

---

## Quick Start

### 1. Install (10 seconds)

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

### 2. Add an API key — or skip this step entirely

Most tools need a paid API key. AgentCrew supports several options that
don't require one:

**Subscription-based (no API key needed):**

```bash
# OpenCode Go — curated open-source models (GLM, Kimi, MiMo, Qwen)
agentcrew chat --provider opencode_go

# ChatGPT Plus / Pro (uses Codex models)
agentcrew chatgpt-auth
agentcrew chat --provider openai_codex

# GitHub Copilot subscribers
agentcrew copilot-auth
agentcrew chat --provider github_copilot
```

**Pay-as-you-go API keys:**

```bash
export CROFAI_API_KEY="your-key"          # CrofAI — OpenAI-compatible, budget-friendly
export DEEPINFRA_API_KEY="your-key"       # DeepInfra — open models (LLaMA, Qwen, etc.)
export OPENAI_API_KEY="sk-proj-..."       # OpenAI GPT-4o
export GEMINI_API_KEY="AIza..."           # Google Gemini — free tier available
```

Or store keys in `~/.AgentCrew/config.json`:

```json
{
  "api_keys": {
    "CROFAI_API_KEY": "your-key",
    "OPENAI_API_KEY": "sk-proj-..."
  }
}
```

**All supported providers:**

| Provider         | Cost profile                    | Best for                              |
| ---------------- | ------------------------------- | ------------------------------------- |
| **OpenCode Go**  | Subscription-based              | Curated open-source models, no API key|
| **CrofAI**       | Pay-as-you-go, low cost         | General tasks, OpenAI-compatible      |
| **Command Code** | Subscription-based              | Frontier models via subscription      |
| **DeepInfra**    | Pay-as-you-go, low cost         | Open models (LLaMA, Qwen, etc.)       |
| **Together AI**  | Pay-as-you-go                   | Open models, fine-tuning              |
| **Fireworks**    | Pay-as-you-go                   | Fast open model inference             |
| OpenAI           | Per-token pricing               | GPT-4o, broad ecosystem               |
| Anthropic        | Per-token pricing               | Claude family                         |
| Google Gemini    | Free tier available             | Budget-friendly, strong reasoning     |
| GitHub Copilot   | Included with subscription      | Coding-focused, no extra cost         |
| ChatGPT Codex    | Included with Plus/Pro sub      | No extra cost for subscribers         |
| Custom           | Free (local)                    | Fully offline (Ollama, llama.cpp...)  |

> **Which provider should I pick?**
>
> | If you...                            | Pick this                            |
> | ------------------------------------ | ------------------------------------ |
> | Want curated open-source models      | **OpenCode Go** — subscription-based |
> | Want low-cost, open-source friendly  | **CrofAI** or **DeepInfra**          |
> | Have a Command Code subscription     | **Command Code**                     |
> | Have ChatGPT Plus / Pro              | `openai_codex` — no extra cost       |
> | Have GitHub Copilot                  | `github_copilot` — no extra cost     |
> | Want a free tier to start            | **Google Gemini**                    |
> | Need fully offline / air-gapped      | **Custom** (Ollama, llama.cpp, etc.) |

### 3. Launch

```bash
# Desktop GUI (default)
agentcrew chat

# Terminal mode — great for SSH, tmux, low-resource environments
agentcrew chat --console
```

On the first launch, AgentCrew walks you through creating your first agent.

### 4. Create your first agent

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

```
> /agent Architect
> Design a clean API for a task manager.
>
> @Coding  Implement the task manager in Python using FastAPI.
>
> @Reviewer  Review the code for security issues.
```

The `@AgentName` syntax hands off a subtask to another agent. The receiving
agent picks up with full context and its own specialized tools and instructions.

---

## What's in the box?

### Four ways to use AgentCrew

| Mode              | Command                                                     | Best for                                                |
| ----------------- | ----------------------------------------------------------- | ------------------------------------------------------- |
| **Desktop GUI**   | `agentcrew chat`                                            | Daily work, drag-and-drop files, visual diffs           |
| **Terminal**      | `agentcrew chat --console`                                  | Remote servers, keyboard-first workflows                |
| **One-shot jobs** | `agentcrew job --agent "Name" "task" ./files`               | CI/CD, batch processing, automation                     |
| **HTTP API**      | `agentcrew a2a-server`                                      | Integrate with other apps, multi-instance agent networks |

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

### Tools — enable only what each agent needs

| Tool                    | What it does                                             | When to enable it                                |
| ----------------------- | -------------------------------------------------------- | ------------------------------------------------ |
| `code_analysis`         | Read files, grep, analyze repo structure                 | Any agent working with code                      |
| `file_editing`          | Write/modify files (search-replace blocks, backups)      | Coding and documentation agents                  |
| `web_search`            | Search the web via Tavily                                 | Research and fact-checking agents                |
| `fetch_webpage`         | Extract content from a URL                                | Research agents                                  |
| `browser`               | Full browser automation (navigate, click, form fill)      | QA, web scraping, form-filling agents            |
| `command_execution`     | Run shell commands (rate limits, audit logs)              | DevOps, code execution agents                    |
| `memory`                | Store and retrieve conversation context                   | Almost every agent                               |
| `clipboard`             | Read/write system clipboard                               | Agents that interact with other apps             |
| `adaptive_learning`     | Learn behavioral patterns from interactions               | Agents that should adapt over time               |
| `voice`                 | Speak and listen (ElevenLabs or DeepInfra)                | Voice-interactive agents                         |
| **MCP tools**           | External tools via Model Context Protocol                 | Any agent needing external integrations          |
| `transfer`              | Hand off tasks to other agents                            | Automatically available in multi-agent setups    |

### Communication protocols

AgentCrew speaks three protocols:

- **Console / GUI** — Human-to-agent: the chat interface you use daily.
- **A2A (Agent-to-Agent)** — HTTP+JSON-RPC protocol. Connect multiple AgentCrew
  instances so one can delegate to agents on another machine.
- **ACP (Agent Communication Protocol)** — WebSocket protocol for custom clients,
  IDE integrations, and headless agent control.

---

## Real-world workflows

### 🧑‍💻 Feature development — from idea to PR

```
You: @Architect  Design the data model for a multi-tenant SaaS app
     @Coding     Implement it with SQLAlchemy
     @Reviewer   Review the code for security issues
```

Three agents, one conversation. The Architect thinks about trade-offs, the Coder
implements cleanly, the Reviewer catches blind spots.

### 📊 Financial report generation

```toml
# Five specialized agents, each focused on one step
[[agents]]  name = "FinancialDataExtractor"   # parse raw statements
[[agents]]  name = "RatioAnalyst"             # compute key ratios
[[agents]]  name = "TrendAnalyst"             # spot patterns over time
[[agents]]  name = "RiskAssessor"             # flag red flags
[[agents]]  name = "ReportingSynthesizer"     # compile into final report
```

Each agent handles one step of the pipeline. The Synthesizer collects all
findings and writes the final report. See
[`examples/agents/agents-financial-report.toml`](examples/agents/agents-financial-report.toml).

### 🌐 Multi-instance agent network

```bash
# Server A — hosts research agents
server-a$ agentcrew a2a-server --port 41241

# Server B — hosts coding agents
server-b$ agentcrew a2a-server --port 41241

# Your machine — connects to both
# ~/.AgentCrew/agents.toml:
[[remote_agents]]
name = "RemoteResearcher"
url = "http://server-a:41241"

[[remote_agents]]
name = "RemoteCoder"
url = "http://server-b:41241"
```

---

## Common commands (once you're inside)

| Command                                | What it does                          |
| -------------------------------------- | ------------------------------------- |
| `/agent <name>`                        | Switch to another agent               |
| `@<name> <task>`                       | Hand off a subtask                    |
| `/clear`                               | Start a fresh conversation            |
| `/file <path>`                         | Attach a file                         |
| `/think <low\|medium\|high\|xhigh>`    | Enable extended reasoning             |
| `/model <provider/model>`              | Switch AI model                       |
| `/voice`                               | Toggle voice recording                |
| `/help`                                | Show all commands                     |
| `exit` or `quit`                       | Exit                                  |
| `Ctrl+C`                               | Stop the current response             |

---

## Next steps

- **[GUIDE_GETTING_STARTED.md](GUIDE_GETTING_STARTED.md)** — Your first 30 minutes with
  AgentCrew: install, create agents, run a multi-agent workflow.
- **[GUIDE_WORKFLOWS.md](GUIDE_WORKFLOWS.md)** — When and why: pattern guides for common
  scenarios with decision trees and best practices.
- **[CONFIGURATION.md](CONFIGURATION.md)** — Deep dive into providers, agents, MCP
  servers, themes, and plugins.
- **[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)** — Build plugins with EventBus
  subscriptions and tool execution hooks.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — How to build, test, and contribute.
- **[Docker guide](docker/DOCKER.md)** — Running AgentCrew in containers.

---

## License

Apache 2.0 License. See [LICENSE](LICENSE) for details.
