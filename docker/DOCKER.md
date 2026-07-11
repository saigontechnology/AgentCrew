# AgentCrew Docker Guide

This guide explains how to build and run AgentCrew using Docker.

> **When to use Docker:**
> - You want to run AgentCrew on a server without a desktop environment
> - You need isolation from your host system's Python environment
> - You are deploying AgentCrew as a service (A2A server)
> - You want consistent behavior across different machines
>
> **When not to use Docker:**
> - You are using the desktop GUI (requires display server passthrough)
> - You want the fastest possible startup time
> - You are on a resource-constrained system

## Quick Start

### 1. Run AgentCrew

#### Console Mode (Default for Docker)

```bash
# Run with console interface (GUI is disabled by default in Docker)
docker run -it --rm \
  -e ANTHROPIC_API_KEY="your_claude_api_key" \
  -e OPENAI_API_KEY="your_openai_api_key" \
  agentcrew chat
```

**Note:** The Docker version of AgentCrew automatically runs in console mode as GUI dependencies (PySide6) are excluded for smaller image size and better compatibility. The `chat` command will default to console mode in the Docker environment.

#### A2A Server Mode

```bash
# Run as A2A server
docker run -d \
  --name agentcrew-server \
  -p 41241:41241 \
  -e ANTHROPIC_API_KEY="your_claude_api_key" \
  daltonnyx/agentcrew a2a-server --host 0.0.0.0 --port 41241
```

## Persistent Data

### Using Docker Volumes

```bash
# Create a named volume for persistence
docker volume create agentcrew_data

# Run with persistent data
docker run -it --rm \
  -v agentcrew_data:/home/agentcrew/.AgentCrew \
  -e ANTHROPIC_API_KEY="your_api_key" \
  daltonnyx/agentcrew chat --console
```

### Using Host Directories

```bash
# Create local config directory
mkdir -p ~/.agentcrew-docker

# Run with host directory mounting
docker run -it --rm \
  -v ~/.agentcrew-docker:/home/agentcrew/.AgentCrew \
  -e ANTHROPIC_API_KEY="your_api_key" \
  daltonnyx/agentcrew chat --console
```

## Configuration Files

### Custom Agent Configuration

Create a custom `agents.toml` file:

```toml
[[agents]]
name = "researcher"
description = "AI Research Assistant"
system_prompt = """You are a research assistant specialized in finding and analyzing information.
Current date: {current_date}
"""
tools = ["memory", "web_search", "code_analysis"]

[[agents]]
name = "coder"
description = "AI Coding Assistant"
system_prompt = """You are a coding assistant specialized in software development.
Current date: {current_date}
"""
tools = ["memory", "clipboard", "code_analysis"]
```

Mount it when running:

```bash
docker run -it --rm \
  -v $(pwd)/agents.toml:/home/agentcrew/.AgentCrew/agents.toml:ro \
  -e ANTHROPIC_API_KEY="your_api_key" \
  daltonnyx/agentcrew chat --console --agent-config /home/agentcrew/.AgentCrew/agents.toml
```

### API Keys Configuration

Create a `config.json` file:

```json
{
  "api_keys": {
    "ANTHROPIC_API_KEY": "your_claude_api_key",
    "OPENAI_API_KEY": "your_openai_api_key"
  }
}
```

Mount it when running:

```bash
docker run -it --rm \
  -v $(pwd)/config.json:/home/agentcrew/.AgentCrew/config.json:ro \
  daltonnyx/agentcrew chat --console
```

## Available Commands

### Chat Commands

```bash
# Console mode with specific provider
docker run -it --rm daltonnyx/agentcrew chat --console --provider openai


# With custom configurations
docker run -it --rm \
  -v $(pwd)/custom_agents.toml:/home/agentcrew/.AgentCrew/agents.toml:ro \
  daltonnyx/agentcrew chat --console --agent-config /home/agentcrew/.AgentCrew/agents.toml
```

### A2A Server Commands

```bash
# Basic server
docker run -d -p 41241:41241 daltonnyx/agentcrew a2a-server

# Server with specific provider and API key
docker run -d -p 41241:41241 \
  -e OPENAI_API_KEY="your_key" \
  daltonnyx/agentcrew a2a-server --provider openai --host 0.0.0.0 --port 41241

# Server with authentication
docker run -d -p 41241:41241 \
  daltonnyx/agentcrew a2a-server --api-key "your_server_auth_key"
```

### GitHub Copilot Authentication

```bash
# Authenticate with GitHub Copilot (interactive)
docker run -it --rm \
  -v agentcrew_data:/home/agentcrew/.AgentCrew \
  daltonnyx/agentcrew copilot-auth
```

## Troubleshooting

### Memory Issues

- The container creates persistent volumes for conversation memory and settings
- Use `docker volume prune` to clean up unused volumes

### Network Issues

- A2A server mode exposes port 41241 by default
- Ensure the port is not already in use on your host system

### API Key Issues

- Verify API keys are correctly set as environment variables
- Check that the API keys have sufficient permissions and quota
- Use the config.json file for persistent API key storage

## Examples

### Complete Setup Example

```bash
# 1. Create a project directory
mkdir agentcrew-docker && cd agentcrew-docker

# 2. Create environment file
cat > .env << EOF
ANTHROPIC_API_KEY=your_claude_api_key
OPENAI_API_KEY=your_openai_api_key
EOF

# 3. Create custom agents configuration
cat > agents.toml << EOF
[[agents]]
name = "assistant"
description = "General AI Assistant"
system_prompt = """You are a helpful AI assistant.
Current date: {current_date}
"""
tools = ["memory", "clipboard", "web_search", "code_analysis"]
EOF

# 4. Build and run
docker run -it --rm \
  --env-file .env \
  -v $(pwd)/agents.toml:/home/agentcrew/.AgentCrew/agents.toml:ro \
  -v agentcrew_data:/home/agentcrew/.AgentCrew \
  daltonnyx/agentcrew chat --console
```

### Production Server Example

```bash
# Run as production A2A server with restart policy
docker run -d \
  --name agentcrew-prod \
  --restart unless-stopped \
  -p 41241:41241 \
  -v agentcrew_prod_data:/home/agentcrew/.AgentCrew \
  -e ANTHROPIC_API_KEY="your_api_key" \
  daltonnyx/agentcrew a2a-server \
    --host 0.0.0.0 \
    --port 41241 \
    --api-key "your_server_auth_key"
```

## Security Considerations

1. **API Keys**: Never include API keys in the Docker image. Always use
   environment variables or mounted config files.

2. **Authentication**: Use the `--api-key` option for A2A server mode in
   production.

3. **Network**: Consider using Docker networks or reverse proxies for production
   deployments.

4. **File Permissions**: The container runs as non-root user `agentcrew` for
   security.

5. **X11 Security**: Be cautious with X11 forwarding in production environments.

## Building from Source

If you want to modify the image:

```bash
# Clone the repository
git clone https://github.com/saigontechnology/AgentCrew.git
cd AgentCrew

# Build custom image
docker build -t my-agentcrew .

# Or with build arguments (if needed)
docker build --build-arg PYTHON_VERSION=3.12 -t my-agentcrew .
```

