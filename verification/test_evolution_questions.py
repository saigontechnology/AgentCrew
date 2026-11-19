"""Console evolution question handoff checks without terminal or LLM access."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from AgentCrew.modules.chat.message import prompt_evolution_coordinator
from AgentCrew.modules.chat.message.command_processor import CommandProcessor
from AgentCrew.modules.chat.message.handler import MessageHandler
from AgentCrew.modules.chat.message.prompt_evolution_coordinator import (
    USER_QUESTIONS,
    PromptEvolutionCoordinator,
)
from AgentCrew.modules.console.console_ui import ConsoleUI
from AgentCrew.modules.events import AppEvents, EventBus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "questions_id,answers,expected",
    [
        pytest.param(
            0,
            ["Be concise", "Explains clearly", "Too verbose"],
            {
                "wanted_change": "Be concise",
                "does_well": "Explains clearly",
                "does_badly": "Too verbose",
            },
            id="initial-filled",
        ),
        pytest.param(0, ["", "", ""], {}, id="initial-skipped"),
        pytest.param(
            0,
            ["  Be concise\n", "", " \t\n"],
            {"wanted_change": "Be concise"},
            id="initial-mixed-whitespace",
        ),
        pytest.param(
            7,
            ["", "Explains clearly", ""],
            {"does_well": "Explains clearly"},
            id="later-filled",
        ),
        pytest.param(7, ["", "", ""], {}, id="later-skipped"),
    ],
)
async def test_console_questions_progress_to_proposal_once(
    monkeypatch, questions_id, answers, expected
):
    bus = EventBus()
    agent = SimpleNamespace(name="VerificationAgent")
    coordinator = PromptEvolutionCoordinator(lambda: agent, bus)
    coordinator._next_questions_id = questions_id
    monkeypatch.setattr(coordinator, "_is_local_agent", Mock(return_value=True))
    proposal = {"agent_name": agent.name, "user_editable_summary": "Be concise"}
    service = SimpleNamespace(
        create_evolution_proposal=AsyncMock(return_value=proposal)
    )
    factory = Mock(return_value=service)
    monkeypatch.setattr(prompt_evolution_coordinator, "PromptEvolutionService", factory)
    handler = MessageHandler.__new__(MessageHandler)
    handler.prompt_evolution_coordinator = coordinator
    handler.resolve_evolution_questions = Mock(
        wraps=handler.resolve_evolution_questions
    )
    ui = ConsoleUI.__new__(ConsoleUI)
    ui.message_handler = handler
    ui.input_handler = Mock()
    ui.input_handler.get_prompt_input.side_effect = answers
    processor = CommandProcessor.__new__(CommandProcessor)
    processor.message_handler = handler
    events = []
    bus.on(AppEvents.EVOLUTION_QUESTIONS_REQUESTED, ui._on_evolution_questions)
    bus.on(
        AppEvents.EVOLUTION_STARTED,
        lambda **data: events.append((AppEvents.EVOLUTION_STARTED, data)),
    )
    bus.on(
        AppEvents.EVOLUTION_SUMMARY,
        lambda **data: events.append((AppEvents.EVOLUTION_SUMMARY, data)),
    )

    result = await asyncio.wait_for(processor.process_command("/evolve"), timeout=2)

    assert result.handled and result.clear_flag
    assert ui.input_handler.get_prompt_input.call_count == len(USER_QUESTIONS) == 3
    ui.input_handler._stop_input_thread.assert_called_once_with()
    ui.input_handler._start_input_thread.assert_called_once_with()
    handler.resolve_evolution_questions.assert_called_once_with(questions_id, expected)
    factory.assert_called_once_with(memory_service=None, persistence_service=None)
    service.create_evolution_proposal.assert_awaited_once_with(
        agent, user_answers=expected
    )
    assert events == [
        (AppEvents.EVOLUTION_STARTED, {"agent_name": agent.name}),
        (AppEvents.EVOLUTION_SUMMARY, proposal),
    ]
    assert coordinator.get_pending_proposal() == proposal
    assert coordinator._pending_questions == {}
    assert coordinator._next_questions_id == questions_id + 1


@pytest.mark.parametrize("payload", [{"questions_id": None}, {}])
def test_console_questions_without_id_do_not_resolve(payload):
    ui = ConsoleUI.__new__(ConsoleUI)
    ui.message_handler = Mock()
    ui.input_handler = Mock()
    ui.input_handler.get_prompt_input.side_effect = ["Be concise", "", ""]

    ui._on_evolution_questions(questions=USER_QUESTIONS, **payload)

    ui.message_handler.resolve_evolution_questions.assert_not_called()
    assert ui.input_handler.get_prompt_input.call_count == 3
    ui.input_handler._stop_input_thread.assert_called_once_with()
    ui.input_handler._start_input_thread.assert_called_once_with()
