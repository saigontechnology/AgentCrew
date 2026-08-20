"""LLM ownership, isolation, and reasoning lifecycle for ``LocalAgent``.

Extracted from :mod:`AgentCrew.modules.agents.local_agent` so the agent
class focuses on orchestration while a focused collaborator owns the
dedicated-service lifecycle:

- dedicated service cloning/isolation (``dedicated_llm``)
- ownership checks (``is_service_owned``)
- safe superseded-service close (``close_superseded``)
- deregistration release (``release_llm``)
- reasoning isolation / application / reapplication
- sync and async LLM service replacement

Ownership rules preserved exactly:

- ``ServiceManager`` owns cached services; agents never close them.
- A dedicated service closes exactly once when superseded, deregistered,
  or shut down.
- A service still referenced by another ``LocalAgent`` is never closed.
- ``release_llm`` detaches the agent reference before ownership evaluation.
- Sync contexts close inline; async contexts use tracked
  ``ServiceManager.close_service`` tasks.
- Reactivation failure closes the superseded service but never the current
  service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from AgentCrew.modules.llm import BaseLLMService

    from .local_agent import LocalAgent


class AgentLLMLifecycle:
    """Manages the LLM ownership and reasoning lifecycle for a LocalAgent.

    Follows the ``AgentContextManager`` / ``AgentToolRegistrar`` collaborator
    pattern: constructed with a back-reference to the owning agent, while the
    agent keeps thin public delegation wrappers so existing callers (setup,
    AgentManager, AgentsConfig, slash commands, ACP, tests, plugins) are
    unaffected.
    """

    def __init__(self, agent: LocalAgent) -> None:
        self._agent = agent

    @property
    def agent(self) -> LocalAgent:
        """Return the owning LocalAgent."""
        return self._agent

    def dedicated_llm(self) -> BaseLLMService | None:
        """Return a dedicated LLM service for the agent's current model.

        Clones the current service when it is shared with another agent or
        cached by ServiceManager, so per-agent reasoning mutations never leak
        to other agents referencing the same mutable object.
        """
        agent = self._agent
        if agent.llm is None:
            return None
        from AgentCrew.modules.agents.local_agent import LocalAgent
        from AgentCrew.modules.agents.manager import AgentManager
        from AgentCrew.modules.llm.service_manager import ServiceManager

        llm_manager = ServiceManager.get_instance()
        try:
            if agent.llm in llm_manager.services.values():
                return llm_manager.clone_service(agent.llm)
            manager = AgentManager.get_instance()
            for other in manager.agents.values():
                if (
                    other is not agent
                    and isinstance(other, LocalAgent)
                    and other.llm is agent.llm
                ):
                    return llm_manager.clone_service(agent.llm)
        except Exception as e:
            logger.warning(
                f"Could not isolate LLM service for agent '{agent.name}': {e}"
            )
            return agent.llm
        return agent.llm

    def is_service_owned(self, service: BaseLLMService | None) -> bool:
        """True when ``service`` is a dedicated service the agent may close.

        A service is never owned when it is cached by ServiceManager (the
        manager owns cached instances) or still referenced by another agent.
        """
        agent = self._agent
        if service is None:
            return False
        from AgentCrew.modules.agents.local_agent import LocalAgent
        from AgentCrew.modules.agents.manager import AgentManager
        from AgentCrew.modules.llm.service_manager import ServiceManager

        llm_manager = ServiceManager.get_instance()
        if service in llm_manager.services.values():
            return False
        manager = AgentManager.get_instance()
        for other in manager.agents.values():
            if (
                other is not agent
                and isinstance(other, LocalAgent)
                and other.llm is service
            ):
                return False
        return True

    def close_superseded(self, old_llm: BaseLLMService | None) -> None:
        """Close a superseded dedicated service exactly once when safe.

        Called after the agent adopts a new LLM service. Never closes a
        ServiceManager-cached service or one still referenced by another
        agent; individual close failures are logged, not raised.
        """
        agent = self._agent
        if old_llm is None or old_llm is agent.llm:
            return
        if self.is_service_owned(old_llm):
            from AgentCrew.modules.llm.service_manager import ServiceManager

            ServiceManager.get_instance().close_service(old_llm)

    def release_llm(self) -> None:
        """Release the agent's owned LLM service when safe.

        Detaches the agent's reference to its LLM, then closes the service
        exactly once when it is a dedicated service that is not cached by
        ServiceManager and not referenced by any remaining agent. Used when
        the agent is deregistered (e.g. config reload removes an agent) so
        dedicated clients are not orphaned. Sync/async scheduling is handled
        by ``ServiceManager.close_service``.
        """
        agent = self._agent
        if agent.llm is None:
            return
        service = agent.llm
        agent.llm = None  # detach so ownership evaluation ignores this agent
        if self.is_service_owned(service):
            from AgentCrew.modules.llm.service_manager import ServiceManager

            ServiceManager.get_instance().close_service(service)

    def ensure_reasoning_isolated(self) -> None:
        """Ensure the agent owns a dedicated LLM before reasoning is mutated.

        Swaps a shared service for a dedicated uncached clone when necessary,
        preserving lifecycle (tools/system prompt) via deactivate/activate.
        """
        agent = self._agent
        if agent.llm is None:
            return
        old_llm = agent.llm
        dedicated = self.dedicated_llm()
        if dedicated is not None and dedicated is not agent.llm:
            was_active = agent.is_active
            if was_active:
                agent.deactivate()
            agent.llm = dedicated
            if was_active:
                agent.activate()
            # The superseded service is shared/cached here, so it is not
            # owned and is intentionally left open for other agents.
            self.close_superseded(old_llm)

    def apply_reasoning(self) -> None:
        """Apply the current reasoning selection to the agent's LLM.

        Preserves forced selections (``/think``, CLI ``--reason-effort``) and
        agent-config reasoning; otherwise falls back to the new model's
        ``default_reasoning``. Lenient: if the new model cannot apply the
        level, reasoning is left disabled rather than aborting the switch.
        """
        from AgentCrew.modules.llm.reasoning_selection import (
            ReasoningSelection,
            ReasoningSource,
            apply_reasoning_to_service,
            default_reasoning_for_service,
        )

        agent = self._agent
        if agent.llm is None:
            return
        selection = agent.reasoning_selection
        if selection is not None and selection.is_forced:
            apply_reasoning_to_service(agent.llm, selection.level, explicit=False)
            return
        if selection is not None and selection.source is ReasoningSource.AGENT_CONFIG:
            apply_reasoning_to_service(agent.llm, selection.level, explicit=False)
            return
        level = default_reasoning_for_service(agent.llm)
        agent.reasoning_selection = ReasoningSelection(
            level, ReasoningSource.MODEL_DEFAULT
        )
        apply_reasoning_to_service(agent.llm, level, explicit=False)

    def reapply_reasoning(self) -> None:
        """Isolate the agent's LLM, then recompute reasoning.

        Used after a model/service switch or config reload: the agent first
        ensures it owns a dedicated service, then applies its effective
        reasoning so no other agent sharing the previous service is affected.
        """
        agent = self._agent
        if agent.llm is None:
            return
        old_llm = agent.llm
        dedicated = self.dedicated_llm()
        if dedicated is not None and dedicated is not agent.llm:
            was_active = agent.is_active
            if was_active:
                agent.deactivate()
            agent.llm = dedicated
            if was_active:
                agent.activate()
            self.close_superseded(old_llm)
        self.apply_reasoning()

    def update_llm_service(self, new_llm_service: BaseLLMService) -> bool:
        """
        Update the LLM service used by the agent.

        Args:
            new_llm_service: The new LLM service to use

        Returns:
            True if the update was successful, False otherwise
        """
        agent = self._agent
        was_active = agent.is_active
        old_llm = agent.llm

        # Deactivate with the current LLM if active
        if was_active:
            agent.deactivate()

        # Update the LLM service; isolate a shared incoming service so this
        # agent's reasoning never mutates another agent's LLM.
        agent.llm = new_llm_service
        dedicated = self.dedicated_llm()
        if dedicated is not None and dedicated is not agent.llm:
            agent.llm = dedicated
        self.apply_reasoning()

        try:
            # Reactivate with the new LLM if it was active before
            if was_active:
                agent.activate()
        finally:
            # Close the superseded dedicated service exactly once, even if
            # reactivation fails; never closes cached/shared services.
            self.close_superseded(old_llm)

        return True

    async def update_llm_service_async(self, new_llm_service: BaseLLMService) -> bool:
        """
        Async variant of :meth:`update_llm_service`.

        Uses ``deactivate_async`` and ``activate_async`` so that MCP
        deregistration is awaited natively rather than through a synchronous
        ``asyncio.run()`` bridge.

        Args:
            new_llm_service: The new LLM service to use

        Returns:
            True if the update was successful, False otherwise
        """
        agent = self._agent
        was_active = agent.is_active
        old_llm = agent.llm

        if was_active:
            await agent.deactivate_async()

        agent.llm = new_llm_service
        dedicated = self.dedicated_llm()
        if dedicated is not None and dedicated is not agent.llm:
            agent.llm = dedicated
        self.apply_reasoning()

        try:
            if was_active:
                await agent.activate_async()
        finally:
            # Close the superseded dedicated service exactly once, even if
            # reactivation fails; never closes cached/shared services.
            self.close_superseded(old_llm)

        return True
