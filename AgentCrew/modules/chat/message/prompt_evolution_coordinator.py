from __future__ import annotations

import asyncio
from typing import Any, Callable

from AgentCrew.modules.agents import AgentManager
from AgentCrew.modules.agents.prompt_evolution_service import PromptEvolutionService
from AgentCrew.modules.events import AppEvents, EventBus
from .prompt_evolution_session import PromptEvolutionSession


USER_QUESTIONS = [
    {
        "key": "wanted_change",
        "label": "What do you want to change about the agent? (optional)",
    },
    {"key": "does_well", "label": "What does the agent do well? (optional)"},
    {"key": "does_badly", "label": "What does the agent do poorly? (optional)"},
]


class PromptEvolutionCoordinator:
    def __init__(
        self,
        agent_getter: Callable[[], Any],
        bus: EventBus,
        memory_service=None,
        persistence_service=None,
    ):
        self._agent_getter = agent_getter
        self._bus = bus
        self._memory_service = memory_service
        self._persistence_service = persistence_service
        self._session = PromptEvolutionSession()
        self._service: PromptEvolutionService | None = None
        self._pending_questions: dict[int, dict] = {}
        self._next_questions_id = 0

    def get_pending_proposal(self):
        return self._session.get()

    def get_effective_summary(self) -> str:
        return self._session.get_effective_summary()

    def update_approved_summary(self, approved_summary: str) -> str:
        return self._session.update_approved_summary(approved_summary)

    async def start_review(self) -> bool:
        agent = self._agent_getter()
        if not self._is_local_agent(agent):
            await self._bus.emit(
                AppEvents.ERROR, message="/evolve is only supported with LocalAgent."
            )
            return True

        self._service = PromptEvolutionService(
            memory_service=self._memory_service,
            persistence_service=self._persistence_service,
        )

        # Ask user 3 optional questions before running analysis
        user_answers = await self._ask_user_questions()

        await self._bus.emit(AppEvents.EVOLUTION_STARTED, agent_name=agent.name)
        try:
            proposal = await self._service.create_evolution_proposal(
                agent, user_answers=user_answers
            )
        except Exception as e:
            await self._bus.emit(AppEvents.EVOLUTION_FINISHED)
            await self._bus.emit(
                AppEvents.ERROR, message=f"Prompt evolution failed: {str(e)}"
            )
            return True

        proposal = self._session.start(proposal)
        await self._bus.emit(AppEvents.EVOLUTION_SUMMARY, **proposal)
        return True

    async def _ask_user_questions(self) -> dict[str, str]:
        """Ask the user 3 optional questions before evolution analysis.

        Returns a dict with non-empty answers keyed by question key.
        """
        questions_id = self._next_questions_id
        self._next_questions_id += 1
        self._pending_questions[questions_id] = {"resolved": False}

        await self._bus.emit(
            AppEvents.EVOLUTION_QUESTIONS_REQUESTED,
            questions_id=questions_id,
            questions=USER_QUESTIONS,
        )

        try:
            while not self._pending_questions[questions_id]["resolved"]:
                await asyncio.sleep(0.1)
            answers = self._pending_questions[questions_id].get("answers", {})
            return {k: v for k, v in answers.items() if v and v.strip()}
        finally:
            if questions_id in self._pending_questions:
                del self._pending_questions[questions_id]

    def resolve_evolution_questions(
        self, questions_id: int, answers: dict[str, str]
    ) -> None:
        """Resolve pending evolution user questions with the user's answers."""
        if questions_id in self._pending_questions:
            self._pending_questions[questions_id].update(
                {"resolved": True, "answers": answers}
            )

    async def approve(self) -> bool:
        if not self._session.has_pending():
            await self._bus.emit(
                AppEvents.ERROR, message="No pending evolution proposal to accept."
            )
            return True
        return await self._apply(
            self._session.get_effective_summary(), edited_by_user=False
        )

    async def edit_and_approve(self, approved_summary: str) -> bool:
        try:
            normalized_summary = self._session.update_approved_summary(approved_summary)
        except ValueError as e:
            await self._bus.emit(AppEvents.ERROR, message=str(e))
            return True

        return await self._apply(normalized_summary, edited_by_user=True)

    async def decline(self) -> bool:
        if not self._session.has_pending():
            await self._bus.emit(
                AppEvents.ERROR, message="No pending evolution proposal to decline."
            )
            return True
        self._session.clear()
        await self._bus.emit(AppEvents.EVOLUTION_DECLINED)
        await self._bus.emit(
            AppEvents.SYSTEM_MESSAGE, message="Prompt evolution declined."
        )
        return True

    async def submit_review(
        self, action: str, approved_summary: str | None = None
    ) -> bool:
        if action == "accept":
            return await self.approve()
        if action == "edit":
            return await self.edit_and_approve(approved_summary or "")
        if action == "decline":
            return await self.decline()

        await self._bus.emit(
            AppEvents.ERROR, message=f"Unknown evolution review action: {action}"
        )
        return True

    async def _apply(self, approved_summary: str, edited_by_user: bool) -> bool:
        proposal = self._session.get()
        if not proposal:
            await self._bus.emit(
                AppEvents.ERROR, message="No pending evolution proposal to apply."
            )
            return True
        if not self._service:
            await self._bus.emit(AppEvents.ERROR, message="Evolution is not available")
            return True

        agent = self._agent_getter()
        if not self._is_local_agent(agent):
            await self._bus.emit(
                AppEvents.ERROR, message="/evolve is only supported with LocalAgent."
            )
            return True

        await self._bus.emit(AppEvents.EVOLUTION_STARTED, agent_name=agent.name)
        try:
            revised_prompt = await self._service.build_revised_prompt(
                agent, approved_summary
            )
            result = self._service.apply_prompt_revision(
                agent,
                revised_prompt,
                approved_summary,
                generated_summary=proposal.get("generated_summary")
                or proposal.get("user_editable_summary", ""),
                memory_ids=proposal.get("memory_ids", []),
                edited_by_user=edited_by_user,
            )
        except Exception as e:
            await self._bus.emit(
                AppEvents.ERROR, message=f"Prompt evolution failed: {str(e)}"
            )
            return True
        finally:
            await self._bus.emit(AppEvents.EVOLUTION_FINISHED)

        self._session.clear()
        await self._bus.emit(AppEvents.EVOLUTION_APPLIED, **result)
        await self._bus.emit(
            AppEvents.SYSTEM_MESSAGE,
            message=f"Updated persisted system prompt for {result['agent_name']}.",
        )
        return True

    def _is_local_agent(self, agent: Any) -> bool:
        if not agent:
            return False
        return isinstance(
            agent,
            AgentManager.get_instance().get_local_agent(agent.name).__class__,
        )
