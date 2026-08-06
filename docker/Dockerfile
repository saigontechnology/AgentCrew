# AgentCrew Docker Image
# Multi-stage build for optimized image size

# Build stage
FROM python:3.12-slim AS builder

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies needed for building
RUN apt-get update && apt-get install -y \
  build-essential \
  git \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Install uv for faster package management
RUN pip install uv

# Set work directory
WORKDIR /build

# Copy the entire source code for installation
# Note: This copies from the Docker build context root (parent of docker/)
COPY . .

# Copy the Docker-specific pyproject.toml 
COPY docker/pyproject.docker.toml pyproject.toml

# Install Python dependencies and the package (without GUI dependencies)
RUN uv venv /opt/venv && \
  . /opt/venv/bin/activate && \
  uv pip install .[redis]

# Pre-download all tree-sitter language parsers to avoid runtime download latency
RUN . /opt/venv/bin/activate && \
  python -c "from tree_sitter_language_pack import download_all; print(f'Pre-downloaded {download_all()} languages')"

# Production stage
FROM python:3.12-slim

LABEL author=daltonnyx

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PATH="/opt/venv/bin:$PATH" \
  AGENTCREW_LOG_PATH="/home/agentcrew/.AgentCrew/logs" \
  MEMORYDB_PATH="/home/agentcrew/.AgentCrew/memorydb" \
  MCP_CONFIG_PATH="/home/agentcrew/.AgentCrew/mcp_servers.json" \
  SW_AGENTS_CONFIG="/home/agentcrew/.AgentCrew/agents.toml" \
  AGENTCREW_PERSISTENCE_DIR="/home/agentcrew/.AgentCrew/persistents" \
  AGENTCREW_CONFIG_PATH="/home/agentcrew/.AgentCrew/config.json" \
  AGENTCREW_DOCKER="true" \
  AGENTCREW_DISABLE_GUI="true" \
  # Chromium headless configuration
  CHROME_BIN=/usr/bin/chromium

# Install system dependencies needed for runtime
RUN apt-get update && apt-get install -y \
  # Basic runtime dependencies
  git \
  curl \
  ripgrep \
  ca-certificates \
  xz-utils \
  libglib2.0-0 \
  libgl1 \
  # Chromium for headless browser automation
  chromium \
  # Minimal dependencies for headless Chrome
  libnss3 \
  libatk-bridge2.0-0 \
  libdrm2 \
  libxkbcommon0 \
  libxcomposite1 \
  libxdamage1 \
  libxrandr2 \
  libgbm1 \
  libxss1 \
  libasound2 \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Allow to run node-based mcp
ARG node_version=v24.4.1
RUN cd /opt \
  && curl -LO https://nodejs.org/dist/${node_version}/node-${node_version}-linux-x64.tar.xz \
  && tar -xJf node-${node_version}-linux-x64.tar.xz \
  && rm node-${node_version}-linux-x64.tar.xz
ENV PATH=/opt/node-${node_version}-linux-x64/bin:${PATH}

RUN mkdir -p /root/.AgentCrew/{logs,memorydb,persistents,chrome_user_data}

# Create non-root user
RUN groupadd -r agentcrew && useradd -r -g agentcrew -m -d /home/agentcrew agentcrew

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy pre-downloaded tree-sitter language parser cache
COPY --from=builder /root/.cache/tree-sitter-language-pack /home/agentcrew/.cache/tree-sitter-language-pack

COPY --from=builder /root/.cache/tree-sitter-language-pack /root/.cache/tree-sitter-language-pack

# Create AgentCrew config directory and Chrome user data directory
RUN mkdir -p /home/agentcrew/.AgentCrew/ && \
  chown -R agentcrew:agentcrew /home/agentcrew/.AgentCrew && \
  chown -R agentcrew:agentcrew /home/agentcrew/.cache

# Create a default agents.toml if it doesn't exist
RUN if [ ! -f /home/agentcrew/.AgentCrew/agents.toml ]; then \
  echo '# Default AgentCrew Agent Configuration' > /home/agentcrew/.AgentCrew/agents.toml && \
  echo '[[agents]]' >> /home/agentcrew/.AgentCrew/agents.toml && \
  echo 'name = "default"' >> /home/agentcrew/.AgentCrew/agents.toml && \
  echo 'description = "Default assistant agent"' >> /home/agentcrew/.AgentCrew/agents.toml && \
  echo 'system_prompt = """You are a helpful AI assistant. Always provide accurate, helpful, and ethical responses.' >> /home/agentcrew/.AgentCrew/agents.toml && \
  echo 'Current date: {current_date}' >> /home/agentcrew/.AgentCrew/agents.toml && \
  echo '"""' >> /home/agentcrew/.AgentCrew/agents.toml && \
  echo 'tools = ["memory", "clipboard", "web_search", "code_analysis"]' >> /home/agentcrew/.AgentCrew/agents.toml && \
  chown agentcrew:agentcrew /home/agentcrew/.AgentCrew/agents.toml; \
  fi

# Create a default config.json template
RUN echo '{"api_keys": {}}' > /home/agentcrew/.AgentCrew/config.json && \
  chown agentcrew:agentcrew /home/agentcrew/.AgentCrew/config.json

# Switch to non-root user
USER agentcrew

# Set the working directory to user home
WORKDIR /home/agentcrew

# Expose port for A2A server (if needed)
EXPOSE 41241

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD agentcrew --help || exit 1

# Set agentcrew as entry point
ENTRYPOINT ["agentcrew"]

# Default command - shows help, but can be overridden
CMD ["--help"]
