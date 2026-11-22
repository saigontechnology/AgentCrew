import asyncio
from types import SimpleNamespace

import pytest

from AgentCrew.modules.chat.message.handler import MessageHandler
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.llm.token_usage import TokenUsage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolved_first, missing_result", [(False, False), (True, False), (True, True)]
)
async def test_stop_during_tool_keeps_turn_session_and_does_not_resume_provider(
    resolved_first, missing_result
):
    tool_started = asyncio.Event()
    tool_canceled = asyncio.Event()
    events = []
    calls = 0
    handler = object.__new__(MessageHandler)

    class Bus:
        async def emit(self, event, **kwargs):
            events.append((event, kwargs))

        def emit_sync(self, event, **kwargs):
            events.append((event, kwargs))

    class Stream:
        def __init__(self, callback):
            self.callback = callback

        def __aiter__(self):
            return self.iterate()

        async def iterate(self):
            yield ("Calling tool", "Calling tool", None)
            self.callback(
                [
                    {"id": "tool-1", "name": "delegate", "input": {}},
                    *(
                        [{"id": "tool-2", "name": "delegate", "input": {}}]
                        if resolved_first
                        else []
                    ),
                ],
                TokenUsage(),
            )

        async def aclose(self):
            pass

        def format_assistant_message(self, response, thinking=None, tool_uses=None):
            return {
                "role": "assistant",
                "content": response,
                "tool_calls": [{"id": tool["id"]} for tool in (tool_uses or [])],
            }

    class Agent:
        name = "test"

        def __init__(self):
            self.history = [{"role": "user", "content": "test"}]

        def process_messages(
            self, messages=None, callback=None, *, tools_enabled: bool = True
        ):
            assert tools_enabled is True
            nonlocal calls
            calls += 1
            return Stream(callback)

        def append_message(self, message):
            self.history.append(message)

        def format_message(self, message_type, data):
            if missing_result and data["tool_use"]["id"] == "tool-2":
                return None
            return {
                "role": "tool",
                "tool_call_id": data["tool_use"]["id"],
                "content": data["tool_result"],
            }

        def is_streaming(self):
            return True

    async def execute_tools_batch(tool_uses, tool_round=None):
        assert tool_round is not None
        assert tool_round.errors == (None,) * len(tool_uses)
        assert not tool_round.finalization_required
        assert tool_uses[0]["name"] == "delegate"
        assert handler.has_active_stream()
        assert handler._active_stream_session.task is asyncio.current_task()
        if resolved_first:
            handler._messages_append(
                {"role": "tool", "tool_call_id": "tool-1", "content": "completed"}
            )
        tool_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            tool_canceled.set()
            raise

    handler.bus = Bus()
    handler.agent = Agent()
    handler.agent_manager = SimpleNamespace(defered_transfer="")
    handler.tool_manager = SimpleNamespace(
        reset_request_yolo_mode=lambda: None, execute_tools_batch=execute_tools_batch
    )
    handler.hooks = SimpleNamespace(run_after=async_noop)
    handler._yolo_mode_check = lambda: None
    handler._finalize_current_turn = lambda *args, **kwargs: None
    handler.streamline_messages = []
    handler.stream_generator = None
    handler._stream_session_counter = 0
    handler._active_stream_session = None
    handler.current_conversation_id = None
    handler.persistent_service = None
    handler.voice_service = None
    handler._turn_usage_ledger = {}
    handler._turn_usage_committed = {}
    handler._pending_resource_attachments = []

    turn = asyncio.create_task(handler.get_assistant_response())
    await asyncio.wait_for(tool_started.wait(), 2)
    assert handler.request_stop_stream()
    await asyncio.wait_for(tool_canceled.wait(), 2)
    await asyncio.wait_for(turn, 2)

    assert calls == 1
    assert not handler.has_active_stream()
    assert (
        len([event for event, _ in events if event == AppEvents.STREAM_CANCELED]) == 1
    )
    assert (
        len([msg for msg in handler.streamline_messages if msg["role"] == "assistant"])
        == 1
    )
    tool_ids = [
        msg["tool_call_id"]
        for msg in handler.streamline_messages
        if msg["role"] == "tool"
    ]
    assert tool_ids == (
        ["tool-1", "tool-2"] if resolved_first and not missing_result else ["tool-1"]
    )
    assert len(handler.agent.history) == len(handler.streamline_messages) + 1


async def async_noop(*args, **kwargs):
    pass
