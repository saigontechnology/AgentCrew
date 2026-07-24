"""Basic verification tests for the A2A v1 migration."""

import pytest
from a2a.helpers import (
    get_artifact_text,
    get_message_text,
    new_text_artifact,
    new_text_artifact_update_event,
    new_text_message,
    new_text_status_update_event,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value


class TestEnumValues:
    """Verify v1 enum SCREAMING_SNAKE_CASE values."""

    def test_task_state_values(self):
        assert TaskState.TASK_STATE_SUBMITTED == 1
        assert TaskState.TASK_STATE_WORKING == 2
        assert TaskState.TASK_STATE_COMPLETED == 3
        assert TaskState.TASK_STATE_FAILED == 4
        assert TaskState.TASK_STATE_CANCELED == 5
        assert TaskState.TASK_STATE_INPUT_REQUIRED == 6

    def test_role_values(self):
        assert Role.ROLE_USER == 1
        assert Role.ROLE_AGENT == 2


class TestPartConstruction:
    """Verify v1 Part construction with unified fields."""

    def test_text_part(self):
        p = Part(text="hello")
        assert p.text == "hello"
        assert p.HasField("text")
        assert not p.HasField("raw")
        assert not p.HasField("url")

    def test_raw_part(self):
        p = Part(
            raw=b"bytes data",
            filename="test.bin",
            media_type="application/octet-stream",
        )
        assert p.HasField("raw")
        assert p.raw == b"bytes data"
        assert p.filename == "test.bin"
        assert p.media_type == "application/octet-stream"

    def test_url_part(self):
        p = Part(
            url="https://example.com/file.pdf",
            filename="file.pdf",
            media_type="application/pdf",
        )
        assert p.HasField("url")
        assert p.url == "https://example.com/file.pdf"
        assert p.filename == "file.pdf"

    def test_data_part(self):
        val = Value()
        ParseDict({"key": "value"}, val)
        p = Part(data=val)
        assert p.HasField("data")


class TestProtoHelpers:
    """Verify protobuf serialization helpers."""

    def test_message_has_field(self):
        msg = Message(role=Role.ROLE_USER)
        # task_id is not set
        assert msg.role == Role.ROLE_USER
        assert msg.parts == []
        # Optional message fields can use HasField
        ts = TaskStatus()
        # HasField only works for message-type fields in proto3
        assert not ts.HasField("message")  # not set

    def test_message_to_dict(self):
        msg = Message(role=Role.ROLE_USER, parts=[Part(text="hello")])
        d = MessageToDict(msg)
        assert "parts" in d

    def test_parse_dict(self):
        msg = Message()
        ParseDict({"role": "ROLE_USER", "parts": [{"text": "hello"}]}, msg)
        assert msg.role == Role.ROLE_USER
        assert msg.parts[0].text == "hello"


class TestAgentCard:
    """Verify v1 AgentCard structure."""

    def test_supported_interfaces(self):
        card = AgentCard(
            name="test-agent",
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                    url="http://localhost:41241/agent/",
                ),
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version="0.3",
                    url="http://localhost:41241/agent/",
                ),
            ],
            capabilities=AgentCapabilities(streaming=True),
        )
        assert len(card.supported_interfaces) == 2
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
        assert card.supported_interfaces[0].protocol_version == "1.0"

    def test_agent_card_to_dict(self):
        card = AgentCard(
            name="test",
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                    url="http://test/",
                )
            ],
            capabilities=AgentCapabilities(streaming=True),
        )
        d = MessageToDict(card)
        assert d["name"] == "test"
        assert "supportedInterfaces" in d


class TestA2AHelpers:
    """Verify SDK helper functions."""

    def test_new_text_message(self):
        msg = new_text_message("hello", role=Role.ROLE_USER)
        assert msg.role == Role.ROLE_USER
        assert msg.parts[0].text == "hello"

    def test_get_message_text(self):
        msg = new_text_message("hello world")
        text = get_message_text(msg)
        assert text == "hello world"

    def test_new_text_artifact(self):
        art = new_text_artifact(name="test", text="content")
        assert art.name == "test"
        assert art.parts[0].text == "content"

    def test_get_artifact_text(self):
        art = new_text_artifact(name="test", text="artifact content")
        text = get_artifact_text(art)
        assert text == "artifact content"

    def test_new_text_status_update_event(self):
        event = new_text_status_update_event(
            task_id="task-1",
            context_id="ctx-1",
            state=TaskState.TASK_STATE_WORKING,
            text="working...",
        )
        assert event.task_id == "task-1"
        assert event.status.state == TaskState.TASK_STATE_WORKING

    def test_new_text_artifact_update_event(self):
        event = new_text_artifact_update_event(
            task_id="task-1", context_id="ctx-1", name="chunk", text="partial response"
        )
        assert event.task_id == "task-1"
        assert event.artifact.name == "chunk"


class TestAdapters:
    """Verify AgentCrew adapter conversions."""

    def test_import_adapters(self):
        from AgentCrew.modules.a2a.adapters import (
            convert_a2a_message_to_agent,
            convert_agent_message_to_a2a,
        )

        assert callable(convert_a2a_message_to_agent)
        assert callable(convert_agent_message_to_a2a)

    def test_a2a_to_agent_text(self):
        from AgentCrew.modules.a2a.adapters import convert_a2a_message_to_agent

        msg = Message(
            message_id="test-1",
            role=Role.ROLE_USER,
            parts=[Part(text="hello world")],
        )
        result = convert_a2a_message_to_agent(msg)
        assert result["role"] == "user"
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "hello world"

    def test_agent_to_a2a_text(self):
        from AgentCrew.modules.a2a.adapters import convert_agent_message_to_a2a

        agent_msg = {"role": "user", "content": [{"type": "text", "text": "hi"}]}
        result = convert_agent_message_to_a2a(agent_msg)
        assert result.role == Role.ROLE_USER
        assert result.parts[0].text == "hi"

    def test_artifact_conversion(self):
        from AgentCrew.modules.a2a.adapters import (
            convert_agent_response_to_a2a_artifact,
        )

        art = convert_agent_response_to_a2a_artifact(
            "response text",
            tool_uses=[{"name": "test_tool", "id": "1", "input": {}}],
            artifact_id="art-1",
        )
        assert art.artifact_id == "art-1"
        assert art.parts[0].text == "response text"

    def test_file_to_part(self):
        from AgentCrew.modules.a2a.adapters import convert_file_to_a2a_part

        part = convert_file_to_a2a_part("test.txt", b"file content", "text/plain")
        assert part.filename == "test.txt"
        assert part.media_type == "text/plain"
        assert part.raw == b"file content"

    def test_ask_message(self):
        from AgentCrew.modules.a2a.adapters import create_ask_message

        msg = create_ask_message([{"question": "Test?", "guided_answers": ["a", "b"]}])
        assert msg is not None
        assert msg.role == Role.ROLE_AGENT


class TestSessionStore:
    """Verify AgentCrew session store."""

    @pytest.mark.asyncio
    async def test_in_memory_store(self):
        from AgentCrew.modules.a2a.session_store import InMemorySessionStore

        store = InMemorySessionStore()
        assert await store.get_history("ctx-1") == []

        await store.append_history("ctx-1", {"role": "user", "content": "hi"})
        history = await store.get_history("ctx-1")
        assert len(history) == 1
        assert history[0]["role"] == "user"

        await store.save_pending_tools("task-1", {"name": "ask"}, [])
        pending = await store.get_pending_tools("task-1")
        assert pending is not None
        assert pending["ask_tool_use"]["name"] == "ask"

        await store.clear_pending_tools("task-1")
        assert await store.get_pending_tools("task-1") is None

    @pytest.mark.asyncio
    async def test_file_store(self, tmp_path):
        from AgentCrew.modules.a2a.session_store import FileSessionStore

        store = FileSessionStore(base_dir=str(tmp_path))
        await store.append_history("ctx-1", {"role": "user", "content": "hi"})
        history = await store.get_history("ctx-1")
        assert len(history) == 1
        await store.cleanup("task-1", "ctx-1")
        assert await store.get_history("ctx-1") == []


class TestExecutorComponents:
    """Verify executor building blocks."""

    def test_import_executor(self):
        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor

        assert AgentCrewA2AExecutor is not None

    def test_import_server(self):
        from AgentCrew.modules.a2a.server import A2AServer

        assert A2AServer is not None

    def test_server_constructor(self):
        """Verify A2AServer can be instantiated with proper params."""
        # Just verify constructor signature is compatible
        import inspect

        from AgentCrew.modules.a2a.server import A2AServer

        sig = inspect.signature(A2AServer.__init__)
        params = list(sig.parameters.keys())
        assert "store_type" in params
        assert "store_options" in params
        assert "api_key" in params
