"""Offline Cursor run failures using the installed SDK's real event parser."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cursor_sdk import AuthenticationError, LocalAgentOptions, Run
from cursor_sdk.types import RunStreamEvent

from AgentCrew.modules.agents import cursor_agent
from AgentCrew.modules.agents.cursor_agent import CursorAgent, CursorAgentError
from AgentCrew.modules.chat.message.handler import MessageHandler
from AgentCrew.modules.chat.stream_session import StreamSession
from AgentCrew.modules.events import AppEvents

AUTH_ERROR = "Authentication error If you are logged in, try logging out and back in."


def status_event(status, message=""):
    return RunStreamEvent.from_json(
        {"sdkMessage": {"type": "status", "status": status, "message": message}}
    )


def result_event(status, text=""):
    return RunStreamEvent.from_json(
        {"result": {"result": {"status": status, "result": text}}}
    )


def text_event(text):
    return RunStreamEvent.from_json(
        {
            "sdkMessage": {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
            }
        }
    )


def sdk_run(*events):
    return Run(
        client=SimpleNamespace(cancel_run=Mock()),
        event_stream=iter(events),
    )


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    guards = []
    for name in (
        "socket.socket.connect",
        "socket.create_connection",
        "subprocess.Popen",
    ):
        guard = Mock(side_effect=AssertionError(f"Unexpected external call: {name}"))
        monkeypatch.setattr(name, guard)
        guards.append(guard)
    sdk_agent = SimpleNamespace(agent_id="offline-agent", send=Mock(), close=Mock())
    create = Mock(return_value=sdk_agent)
    monkeypatch.setenv("CURSOR_API_KEY", "fictional-offline-key")
    monkeypatch.setattr(
        cursor_agent,
        "_load_cursor_sdk",
        lambda: SimpleNamespace(
            Agent=SimpleNamespace(create=create), LocalAgentOptions=LocalAgentOptions
        ),
    )
    monkeypatch.setattr(cursor_agent.logger, "exception", Mock())
    agent = CursorAgent("OfflineCursor", "offline", cwd=str(tmp_path))
    monkeypatch.setattr(agent, "_custom_tools", lambda _sdk: {})
    yield SimpleNamespace(agent=agent, sdk_agent=sdk_agent, create=create)
    agent.dispose_sdk_agent()
    for guard in guards:
        guard.assert_not_called()


def collect(agent, text="synthetic prompt", *, failure=False):
    async def consume():
        chunks = []

        async def drain():
            async for item in agent.process_messages(
                [{"role": "user", "content": text}]
            ):
                chunks.append(item)

        if failure:
            with pytest.raises(CursorAgentError) as error:
                await drain()
            return chunks, str(error.value)
        await drain()
        return chunks

    return asyncio.run(consume())


@pytest.mark.parametrize("wire_status", ["ERROR", "RUN_LIFECYCLE_STATUS_ERROR"])
@pytest.mark.parametrize("partial", ["", "Already changed a file."])
def test_status_only_auth_failure_is_nonempty_and_never_replayed(
    runtime, wire_status, partial
):
    events = [status_event("RUNNING")]
    if partial:
        events.append(text_event(partial))
    events.append(status_event(wire_status, AUTH_ERROR))
    run = sdk_run(*events)
    runtime.sdk_agent.send.return_value = run

    chunks, response = collect(runtime.agent, failure=True)

    assert chunks[-1][0] == partial
    assert "Cursor agent error:" in response
    assert AUTH_ERROR in response
    assert "CURSOR_API_KEY" in response
    assert "not automatically retried" in response
    runtime.sdk_agent.send.assert_called_once()
    assert run.status == "error"
    run.client.cancel_run.assert_not_called()
    assert runtime.agent._active_run is None


@pytest.mark.parametrize("status", ["error", "expired"])
def test_result_only_failure_is_not_an_empty_success(runtime, status):
    run = sdk_run(status_event("RUNNING"), result_event(status))
    runtime.sdk_agent.send.return_value = run

    _chunks, response = collect(runtime.agent, failure=True)

    assert "Cursor agent error:" in response
    assert status in response
    runtime.sdk_agent.send.assert_called_once()
    run.client.cancel_run.assert_not_called()


def test_result_only_auth_failure_has_supported_recovery_guidance(runtime):
    runtime.sdk_agent.send.return_value = sdk_run(
        status_event("RUNNING"), result_event("error", AUTH_ERROR)
    )

    _chunks, response = collect(runtime.agent, failure=True)

    assert AUTH_ERROR in response
    assert "CURSOR_API_KEY" in response
    assert "restart AgentCrew" in response
    assert "not automatically retried" in response


def test_typed_authentication_exception_has_recovery_guidance(runtime):
    runtime.sdk_agent.send.side_effect = AuthenticationError(
        "Invalid API key", code="unauthenticated"
    )

    _chunks, response = collect(runtime.agent, failure=True)

    assert "Invalid API key" in response
    assert "CURSOR_API_KEY" in response
    runtime.sdk_agent.send.assert_called_once()
    assert runtime.agent._active_run is None


def test_healthy_turns_reuse_sdk_agent_and_transcript(runtime):
    runs = [
        sdk_run(
            status_event("RUNNING"), text_event(text), result_event("finished", text)
        )
        for text in ("First answer.", "Follow-up answer.")
    ]
    runtime.sdk_agent.send.side_effect = runs

    assert collect(runtime.agent)[-1][0] == "First answer."
    assert collect(runtime.agent, "follow-up")[-1][0] == "Follow-up answer."

    runtime.create.assert_called_once()
    assert runtime.sdk_agent.send.call_count == 2
    runtime.sdk_agent.close.assert_not_called()
    for run in runs:
        run.client.cancel_run.assert_not_called()


def test_recoverable_running_status_is_not_treated_as_terminal(runtime):
    runtime.sdk_agent.send.return_value = sdk_run(
        status_event("RUNNING", "Retrying a temporary connection error"),
        text_event("Recovered."),
        result_event("finished", "Recovered."),
    )

    assert collect(runtime.agent)[-1][0] == "Recovered."
    runtime.sdk_agent.send.assert_called_once()


def test_partial_output_does_not_hide_terminal_failure(runtime):
    runtime.sdk_agent.send.return_value = sdk_run(
        text_event("Partial output."), result_event("error", "Upstream failed")
    )

    _chunks, response = collect(runtime.agent, failure=True)

    assert _chunks[-1][0] == "Partial output."
    assert "Cursor agent error:" in response
    assert "Upstream failed" in response
    assert "CURSOR_API_KEY" not in response


def test_interactive_handler_does_not_retry_terminal_auth_failure(runtime):
    runtime.agent.history = [{"role": "user", "content": "synthetic prompt"}]
    runtime.sdk_agent.send.side_effect = lambda _prompt: sdk_run(
        status_event("RUNNING"), status_event("ERROR", AUTH_ERROR)
    )
    session = StreamSession(session_id=1)
    handler = SimpleNamespace(
        agent=runtime.agent,
        bus=SimpleNamespace(emit=AsyncMock()),
        hooks=SimpleNamespace(run_after=AsyncMock()),
        agent_manager=SimpleNamespace(defered_transfer=""),
        _messages_append=Mock(),
        _finalize_current_turn=Mock(),
        current_user_input=None,
        current_conversation_id=None,
        streamline_messages=[],
        last_assisstant_response_idx=0,
        MAX_EMPTY_RESPONSE_RETRIES=MessageHandler.MAX_EMPTY_RESPONSE_RETRIES,
    )
    handler._run_stream_response = lambda *args, **kwargs: (
        MessageHandler._run_stream_response(handler, *args, **kwargs)
    )

    response, _usage = asyncio.run(handler._run_stream_response(session))

    assert response is None
    runtime.sdk_agent.send.assert_called_once()
    errors = [
        call
        for call in handler.bus.emit.call_args_list
        if call.args == (AppEvents.ERROR,)
    ]
    assert len(errors) == 1
    assert AUTH_ERROR in errors[0].kwargs["message"]
    assert "CURSOR_API_KEY" in errors[0].kwargs["message"]
    assert session.status == "failed"
    assert session.finished.is_set()
    assert not any(
        call.args == (AppEvents.RESPONSE_COMPLETED,)
        for call in handler.bus.emit.call_args_list
    )


def test_stream_auth_exception_is_reported_once_and_cancelled(runtime):
    def events():
        yield status_event("RUNNING")
        raise AuthenticationError("Invalid API key")

    run = Run(client=SimpleNamespace(cancel_run=Mock()), event_stream=events())
    runtime.sdk_agent.send.return_value = run

    _chunks, response = collect(runtime.agent, failure=True)

    assert "Invalid API key" in response
    assert "CURSOR_API_KEY" in response
    runtime.sdk_agent.send.assert_called_once()
    run.client.cancel_run.assert_called_once()
    assert runtime.agent._active_run is None


def test_prior_running_message_does_not_hide_final_auth_error(runtime):
    runtime.sdk_agent.send.return_value = sdk_run(
        status_event("RUNNING", "Preparing the workspace"),
        result_event("error", AUTH_ERROR),
    )

    _chunks, response = collect(runtime.agent, failure=True)

    assert AUTH_ERROR in response
    assert "CURSOR_API_KEY" in response


def test_later_manual_followup_reuses_session_without_replaying_failed_prompt(runtime):
    runtime.sdk_agent.send.side_effect = [
        sdk_run(status_event("ERROR", AUTH_ERROR)),
        sdk_run(text_event("Recovered."), result_event("finished", "Recovered.")),
    ]

    _chunks, error = collect(runtime.agent, "first task", failure=True)
    assert "Cursor agent error:" in error
    assert runtime.sdk_agent.send.call_count == 1
    assert collect(runtime.agent, "user-authorized follow-up")[-1][0] == "Recovered."

    runtime.create.assert_called_once()
    assert runtime.sdk_agent.send.call_count == 2
    assert "user-authorized follow-up" in runtime.sdk_agent.send.call_args.args[0]
    assert "first task" not in runtime.sdk_agent.send.call_args.args[0]
    runtime.sdk_agent.close.assert_not_called()


def test_cancelled_run_keeps_existing_partial_output_behavior(runtime):
    run = sdk_run(text_event("Partial output."), result_event("cancelled"))
    runtime.sdk_agent.send.return_value = run

    assert collect(runtime.agent)[-1][0] == "Partial output."
    runtime.sdk_agent.send.assert_called_once()
    run.client.cancel_run.assert_not_called()
