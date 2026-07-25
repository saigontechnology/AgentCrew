import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from tomli_w import dump as toml_dump


@dataclass
class LocalAgentConfig:
    """Type-safe representation of a local agent entry in agents.toml."""

    name: str
    description: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    enabled: bool = True
    temperature: float | None = None
    voice_enabled: str = "disabled"
    voice_id: str | None = None
    model_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LocalAgentConfig":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt", ""),
            tools=data.get("tools", []),
            enabled=data.get("enabled", True),
            temperature=data.get("temperature"),
            voice_enabled=data.get("voice_enabled", "disabled"),
            voice_id=data.get("voice_id"),
            model_id=data.get("model_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "tools": self.tools,
            "enabled": self.enabled,
        }
        if self.temperature is not None:
            result["temperature"] = self.temperature
        if self.voice_enabled != "disabled":
            result["voice_enabled"] = self.voice_enabled
        if self.voice_id is not None:
            result["voice_id"] = self.voice_id
        if self.model_id is not None:
            result["model_id"] = self.model_id
        return result


@dataclass
class RemoteAgentConfig:
    """Type-safe representation of a remote agent entry in agents.toml."""

    name: str
    description: str
    base_url: str
    enabled: bool = True
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteAgentConfig":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            base_url=data.get("base_url", ""),
            enabled=data.get("enabled", True),
            headers=data.get("headers", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "headers": self.headers,
        }


@dataclass
class AgentsFileConfig:
    """Type-safe representation of the full agents.toml file."""

    agents: list[LocalAgentConfig] = field(default_factory=list)
    remote_agents: list[RemoteAgentConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentsFileConfig":
        agents = [LocalAgentConfig.from_dict(a) for a in data.get("agents", [])]
        remote_agents = [
            RemoteAgentConfig.from_dict(r) for r in data.get("remote_agents", [])
        ]
        return cls(agents=agents, remote_agents=remote_agents)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.agents:
            result["agents"] = [a.to_dict() for a in self.agents]
        if self.remote_agents:
            result["remote_agents"] = [r.to_dict() for r in self.remote_agents]
        return result


class AgentsConfig:
    """Manages agents.toml — CRUD, hot-reload, export, and import."""

    @property
    def _path(self) -> str:
        return os.getenv("SW_AGENTS_CONFIG", os.path.expanduser("./agents.toml"))

    def read(self) -> AgentsFileConfig:
        """Return the full agents config, or empty AgentsFileConfig on error."""
        try:
            from AgentCrew.modules.config.config_management import ConfigManagement

            config = ConfigManagement(self._path)
            return AgentsFileConfig.from_dict(config.get_config())
        except Exception:
            return AgentsFileConfig()

    def write(self, config_data: AgentsFileConfig) -> None:
        """Persist config_data to agents.toml and hot-reload live agents."""
        from AgentCrew.modules.config.config_management import ConfigManagement

        raw = config_data.to_dict()
        try:
            config = ConfigManagement(self._path)
            config.update_config(raw, merge=False)
            config.save_config()
            self.reload()
        except FileNotFoundError:
            dir_path = os.path.dirname(self._path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(self._path, "wb") as f:
                toml_dump(raw, f, multiline_strings=True)
            self.reload()

    @staticmethod
    def _write_file_fallback(path: str, raw: dict[str, Any]) -> None:
        """Synchronous helper: create parent directories and write TOML file.

        Separated from :meth:`write_async` so it can be offloaded to a
        thread pool via ``asyncio.to_thread`` without blocking the event
        loop.
        """
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "wb") as f:
            toml_dump(raw, f, multiline_strings=True)

    async def write_async(self, config_data: AgentsFileConfig) -> None:
        """Async variant of :meth:`write`.

        Persists config and calls :meth:`reload_async` for async-native
        lifecycle updates.
        """
        from AgentCrew.modules.config.config_management import ConfigManagement

        raw = config_data.to_dict()
        try:
            config = ConfigManagement(self._path)
            config.update_config(raw, merge=False)
            config.save_config()
            await self.reload_async()
        except FileNotFoundError:
            await asyncio.to_thread(self._write_file_fallback, self._path, raw)
            await self.reload_async()

    def reload(self) -> None:
        """Hot-reload all agents from the current agents.toml without restarting."""
        from AgentCrew.modules.agents import AgentManager, LocalAgent, RemoteAgent

        agent_manager = AgentManager.get_instance()
        new_agents_config = agent_manager.load_agents_from_config(self._path)
        for agent_cfg in new_agents_config:
            if agent_cfg.get("base_url"):
                try:
                    agent_manager.agents[agent_cfg["name"]] = RemoteAgent(
                        agent_cfg["name"],
                        agent_cfg["base_url"],
                        headers=agent_cfg.get("headers", {}),
                    )
                except Exception as e:
                    logger.error(str(e))
                    continue

            existing_agent = agent_manager.get_local_agent(agent_cfg["name"])
            system_prompt = agent_cfg.get("system_prompt", "")
            if existing_agent:
                existing_agent.tools = agent_cfg.get("tools", [])
                existing_agent.set_system_prompt(system_prompt)
                existing_agent.temperature = agent_cfg.get("temperature", 0.4)
                existing_agent.voice_enabled = (
                    "enabled"
                    if agent_cfg.get("voice_enabled", "disabled") == "enabled"
                    else "disabled"
                )
                existing_agent.voice_id = agent_cfg.get("voice_id", None)
                new_llm_svc = AgentManager.resolve_llm_service_from_config(agent_cfg)
                if new_llm_svc:
                    existing_agent.update_llm_service(new_llm_svc)
                    existing_agent.pinned_model_id = agent_cfg.get("model_id")
                else:
                    existing_agent.pinned_model_id = None
            else:
                clone_agent = agent_manager.get_current_agent()
                if not isinstance(clone_agent, LocalAgent):
                    clone_agent = next(
                        agent
                        for agent in agent_manager.agents.values()
                        if isinstance(agent, LocalAgent)
                    )

                voice_enabled = (
                    "enabled"
                    if agent_cfg.get("voice_enabled", "disabled") == "enabled"
                    else "disabled"
                )
                voice_id = agent_cfg.get("voice_id", None)

                reload_llm_service = clone_agent.llm

                new_llm_svc = AgentManager.resolve_llm_service_from_config(agent_cfg)
                if new_llm_svc:
                    reload_llm_service = new_llm_svc

                new_agent = LocalAgent(
                    name=agent_cfg["name"],
                    description=agent_cfg["description"],
                    llm_service=reload_llm_service,
                    services=clone_agent.services,
                    tools=agent_cfg["tools"],
                    temperature=agent_cfg.get("temperature", None),
                    voice_enabled=voice_enabled,
                    voice_id=voice_id,
                )
                new_agent.set_system_prompt(system_prompt)
                if new_llm_svc:
                    new_agent.pinned_model_id = agent_cfg.get("model_id")
                agent_manager.register_agent(new_agent)

        new_agent_names = [a["name"] for a in new_agents_config]
        old_agent_names = [n for n in agent_manager.agents if n not in new_agent_names]
        for agent_name in old_agent_names:
            old_agent = agent_manager.get_agent(agent_name)
            if old_agent and old_agent.is_active:
                agent_manager.select_agent(new_agent_names[0])
            agent_manager.deregister_agent(agent_name)

        for agent in agent_manager.agents.values():
            was_active = False
            if agent.is_active:
                was_active = True
                agent.deactivate()
            if isinstance(agent, LocalAgent):
                agent.custom_system_prompt = None
            if was_active:
                agent_manager.select_agent(agent.name)

    async def reload_async(self) -> None:
        """
        Async variant of :meth:`reload`.

        Uses async lifecycle methods (:meth:`update_llm_service_async`,
        :meth:`select_agent_async`, :meth:`deactivate_async`) so that
        MCP deregistration is awaited natively rather than through a
        synchronous ``asyncio.run()`` bridge.
        """
        from AgentCrew.modules.agents import AgentManager, LocalAgent, RemoteAgent

        agent_manager = AgentManager.get_instance()
        new_agents_config = agent_manager.load_agents_from_config(self._path)
        for agent_cfg in new_agents_config:
            if agent_cfg.get("base_url"):
                try:
                    agent_manager.agents[agent_cfg["name"]] = RemoteAgent(
                        agent_cfg["name"],
                        agent_cfg["base_url"],
                        headers=agent_cfg.get("headers", {}),
                    )
                except Exception as e:
                    logger.error(str(e))
                    continue

            existing_agent = agent_manager.get_local_agent(agent_cfg["name"])
            system_prompt = agent_cfg.get("system_prompt", "")
            if existing_agent:
                existing_agent.tools = agent_cfg.get("tools", [])
                existing_agent.set_system_prompt(system_prompt)
                existing_agent.temperature = agent_cfg.get("temperature", 0.4)
                existing_agent.voice_enabled = (
                    "enabled"
                    if agent_cfg.get("voice_enabled", "disabled") == "enabled"
                    else "disabled"
                )
                existing_agent.voice_id = agent_cfg.get("voice_id", None)
                new_llm_svc = AgentManager.resolve_llm_service_from_config(agent_cfg)
                if new_llm_svc:
                    await existing_agent.update_llm_service_async(new_llm_svc)
                    existing_agent.pinned_model_id = agent_cfg.get("model_id")
                else:
                    existing_agent.pinned_model_id = None
            else:
                clone_agent = agent_manager.get_current_agent()
                if not isinstance(clone_agent, LocalAgent):
                    clone_agent = next(
                        agent
                        for agent in agent_manager.agents.values()
                        if isinstance(agent, LocalAgent)
                    )

                voice_enabled = (
                    "enabled"
                    if agent_cfg.get("voice_enabled", "disabled") == "enabled"
                    else "disabled"
                )
                voice_id = agent_cfg.get("voice_id", None)

                reload_llm_service = clone_agent.llm

                new_llm_svc = AgentManager.resolve_llm_service_from_config(agent_cfg)
                if new_llm_svc:
                    reload_llm_service = new_llm_svc

                new_agent = LocalAgent(
                    name=agent_cfg["name"],
                    description=agent_cfg["description"],
                    llm_service=reload_llm_service,
                    services=clone_agent.services,
                    tools=agent_cfg["tools"],
                    temperature=agent_cfg.get("temperature", None),
                    voice_enabled=voice_enabled,
                    voice_id=voice_id,
                )
                new_agent.set_system_prompt(system_prompt)
                if new_llm_svc:
                    new_agent.pinned_model_id = agent_cfg.get("model_id")
                agent_manager.register_agent(new_agent)

        new_agent_names = [a["name"] for a in new_agents_config]
        old_agent_names = [n for n in agent_manager.agents if n not in new_agent_names]
        for agent_name in old_agent_names:
            old_agent = agent_manager.get_agent(agent_name)
            if old_agent and old_agent.is_active:
                await agent_manager.select_agent_async(new_agent_names[0])
            agent_manager.deregister_agent(agent_name)

        for agent in agent_manager.agents.values():
            was_active = False
            if agent.is_active:
                was_active = True
                await agent.deactivate_async()
            if isinstance(agent, LocalAgent):
                agent.custom_system_prompt = None
            if was_active:
                await agent_manager.select_agent_async(agent.name)

    def update_agent_system_prompt(self, agent_name: str, new_prompt: str) -> bool:
        config_data = self.read()
        agents = config_data.agents
        if not isinstance(agents, list):
            return False

        updated = False
        for agent in agents:
            if agent.name == agent_name:
                agent.system_prompt = new_prompt
                updated = True
                break

        if not updated:
            return False

        self.write(config_data)
        return True

    async def update_agent_system_prompt_async(
        self, agent_name: str, new_prompt: str
    ) -> bool:
        """Async variant of :meth:`update_agent_system_prompt`."""
        config_data = self.read()
        agents = config_data.agents
        if not isinstance(agents, list):
            return False

        updated = False
        for agent in agents:
            if agent.name == agent_name:
                agent.system_prompt = new_prompt
                updated = True
                break

        if not updated:
            return False

        await self.write_async(config_data)
        return True

    def export(
        self, agent_names: list[str], output_file: str, file_format: str = "toml"
    ) -> dict[str, Any]:
        """Export selected agents to a portable file."""
        result = {
            "success": False,
            "exported_count": 0,
            "local_count": 0,
            "remote_count": 0,
            "missing_agents": [],
            "output_file": output_file,
        }

        try:
            agents_config = self.read()
            local_agents = agents_config.agents
            remote_agents = agents_config.remote_agents

            selected_local_agents = []
            selected_remote_agents = []
            found_names = set()

            for agent in local_agents:
                if agent.name in agent_names:
                    export_data = {
                        k: v for k, v in agent.to_dict().items() if k != "agent_type"
                    }
                    selected_local_agents.append(export_data)
                    found_names.add(agent.name)

            for agent in remote_agents:
                if agent.name in agent_names:
                    export_data = {
                        k: v for k, v in agent.to_dict().items() if k != "agent_type"
                    }
                    selected_remote_agents.append(export_data)
                    found_names.add(agent.name)

            missing_names = set(agent_names) - found_names
            result["missing_agents"] = list(missing_names)

            if not selected_local_agents and not selected_remote_agents:
                result["error"] = "No matching agents found to export"
                return result

            export_config = {}
            if selected_local_agents:
                export_config["agents"] = selected_local_agents
                result["local_count"] = len(selected_local_agents)
            if selected_remote_agents:
                export_config["remote_agents"] = selected_remote_agents
                result["remote_count"] = len(selected_remote_agents)

            result["exported_count"] = len(selected_local_agents) + len(
                selected_remote_agents
            )

            if file_format == "toml" and not output_file.endswith(".toml"):
                output_file += ".toml"
            elif file_format == "json" and not output_file.endswith(".json"):
                output_file += ".json"

            output_file = os.path.expanduser(output_file)
            result["output_file"] = output_file

            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            if file_format == "toml":
                with open(output_file, "wb") as f:
                    toml_dump(export_config, f, multiline_strings=True)
            else:
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(export_config, f, indent=2, ensure_ascii=False)

            result["success"] = True
            logger.info(f"Exported {result['exported_count']} agents to {output_file}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Export agents error: {e!s}", exc_info=True)

        return result

    def import_agents(
        self,
        import_file_path: str,
        merge_strategy: str = "update",
        skip_conflicts: bool = False,
    ) -> dict[str, Any]:
        """Import agents from a previously exported file."""
        result = {
            "success": False,
            "added_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "conflicts": [],
            "imported_agents": [],
        }

        temp_file = None

        try:
            if import_file_path.startswith(("http://", "https://")):
                import tempfile

                import requests

                response = requests.get(import_file_path, timeout=30)
                response.raise_for_status()

                suffix = (
                    ".json"
                    if "json" in response.headers.get("content-type", "")
                    else ".toml"
                )
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=suffix, delete=False, encoding="utf-8"
                ) as temp_file:
                    temp_file.write(response.text)
                    import_file_path = temp_file.name

            import_file_path = os.path.expanduser(import_file_path)

            if not os.path.exists(import_file_path):
                result["error"] = f"File not found: {import_file_path}"
                return result

            from AgentCrew.modules.config.config_management import ConfigManagement

            temp_config = ConfigManagement(import_file_path)
            imported_config = temp_config.get_config()

            imported_local_agents = imported_config.get("agents", [])
            imported_remote_agents = imported_config.get("remote_agents", [])

            if not imported_local_agents and not imported_remote_agents:
                result["error"] = "No agent configurations found in the file"
                return result

            current_config = self.read()
            current_local_agents = current_config.agents
            current_remote_agents = current_config.remote_agents

            local_agent_map = {agent.name: agent for agent in current_local_agents}
            remote_agent_map = {agent.name: agent for agent in current_remote_agents}

            existing_names = set(local_agent_map.keys()) | set(remote_agent_map.keys())

            for agent in imported_local_agents:
                agent_name = agent.get("name")
                if not agent_name:
                    continue

                is_conflict = agent_name in existing_names

                if is_conflict:
                    result["conflicts"].append(agent_name)

                    if skip_conflicts:
                        result["skipped_count"] += 1
                        continue

                    if merge_strategy == "skip":
                        result["skipped_count"] += 1
                        continue

                if "enabled" not in agent:
                    agent["enabled"] = True

                local_agent_map[agent_name] = agent
                if is_conflict:
                    result["updated_count"] += 1
                else:
                    result["added_count"] += 1
                result["imported_agents"].append(agent_name)

            for agent in imported_remote_agents:
                agent_name = agent.get("name")
                if not agent_name:
                    continue

                is_conflict = agent_name in existing_names

                if is_conflict:
                    if agent_name not in result["conflicts"]:
                        result["conflicts"].append(agent_name)

                    if skip_conflicts:
                        result["skipped_count"] += 1
                        continue

                    if merge_strategy == "skip":
                        result["skipped_count"] += 1
                        continue

                if "enabled" not in agent:
                    agent["enabled"] = True

                remote_agent_map[agent_name] = agent
                if is_conflict:
                    result["updated_count"] += 1
                else:
                    result["added_count"] += 1
                result["imported_agents"].append(agent_name)

            final_config = {}
            if local_agent_map:
                final_config["agents"] = list(local_agent_map.values())
            if remote_agent_map:
                final_config["remote_agents"] = list(remote_agent_map.values())

            self.write(AgentsFileConfig.from_dict(final_config))

            result["success"] = True
            logger.info(
                f"Imported agents: added={result['added_count']}, "
                f"updated={result['updated_count']}, skipped={result['skipped_count']}"
            )

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Import agents error: {e!s}", exc_info=True)

        finally:
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except Exception as e:
                    logger.warning(f"Failed to delete temporary file: {e}")

        return result
