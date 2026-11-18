"""Focused resource provenance checks; fictional inputs, no network or deprecated tests."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.agents.tool_result_summary import (
    bind_inference_scope,
    current_inference_scope,
    reset_inference_scope,
)
from AgentCrew.modules.events.hooks import HookPoints, HookRegistry
from AgentCrew.modules.memory.memory_record_builder import MemoryRecordBuilder
from AgentCrew.modules.memory.record_parser import derive_memory_units
from AgentCrew.modules.memory.resources import (
    MAX_RESOURCES_PER_TURN,
    MAX_URI_CHARACTERS,
    ResourceAccess,
    ResourceToolResult,
    canonical_uri,
    capture_attachment,
    capture_resource,
    capture_tool_access,
    enforce_resources,
    register_resource_tool,
    snapshot_resources,
)
from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator, _query_parts
from AgentCrew.modules.memory.sqlite_repository import (
    SCHEMA_V1,
    SCHEMA_VERSION,
    SQLiteMemoryError,
    SQLiteMemoryRepository,
)
from AgentCrew.modules.memory.types import CandidateSource, MemoryQuery, MemoryRecord

URI = "ui://mobbin/search-screens.html"
CV = "file:///home/user/cvs/example-candidate.pdf"
NAME = "Example Candidate"


def xml(uri=CV, name=NAME, *, linked=True):
    root = ET.Element("MEMORY")
    ET.SubElement(root, "HEAD").text = "Candidate review"
    entities = ET.SubElement(root, "ENTITIES")
    ET.SubElement(ET.SubElement(entities, "ENTITY"), "NAME").text = name
    resource = ET.SubElement(
        ET.SubElement(root, "RESOURCES"), "RESOURCE", uri=uri, name="Candidate CV"
    )
    if linked:
        ET.SubElement(resource, "ENTITY_REF", relation="source_document").text = name
    return ET.tostring(root, encoding="unicode")


def record(key="candidate", document=None, **kwargs):
    return MemoryRecord(
        key, "Engineer", "previous-session", document or xml(), 1, 1, **kwargs
    )


def query(text=NAME, **kwargs):
    return MemoryQuery(text, "Engineer", "current-session", **kwargs)


@pytest.fixture(autouse=True)
def clean_hooks():
    HookRegistry.reset_instance()
    yield
    HookRegistry.reset_instance()
    assert snapshot_resources() == ()
    assert current_inference_scope() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["chat", "a2a", "acp", "job"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_concurrent_scope_isolation_and_cleanup(surface, outcome):
    async def turn(index):
        token = bind_inference_scope(f"{surface}:{index}")
        try:
            capture_resource(f"urn:turn:{index}")
            nested = bind_inference_scope("nested")
            assert nested is None
            await asyncio.sleep(0)
            assert [item.uri for item in snapshot_resources()] == [f"urn:turn:{index}"]
            if outcome == "error":
                raise RuntimeError("fictional")
            if outcome == "cancel":
                raise asyncio.CancelledError
        finally:
            reset_inference_scope(token)
            assert snapshot_resources() == ()

    results = await asyncio.gather(
        *(turn(index) for index in range(8)), return_exceptions=True
    )
    if outcome == "success":
        assert all(item is None for item in results)
    else:
        expected = RuntimeError if outcome == "error" else asyncio.CancelledError
        assert all(isinstance(item, expected) for item in results)


@pytest.mark.asyncio
async def test_hook_success_exact_mcp_and_failure_no_content_retention(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    register_resource_tool("fictional__get_resource")
    token = bind_inference_scope("chat:test")
    try:
        hooks = HookRegistry.get_instance()
        calls = [
            ("read_file", {"file_path": "folder/a b.pdf"}, "file contents"),
            (
                "fetch_webpage",
                {"url": "https://example.org/CV?a=1&b=2"},
                [{"type": "text", "text": "page"}],
            ),
            (
                "fictional__get_resource",
                {"uri": URI},
                ResourceToolResult([], accessed=True),
            ),
            ("run_command", {"command": f"cat {CV}"}, CV),
            (
                "fetch_webpage",
                {"url": "https://failed.invalid"},
                "Extract failed: no access",
            ),
            (
                "fictional__get_resource",
                {"uri": "s3://failed"},
                ResourceToolResult([], accessed=False),
            ),
            ("acp_read_file", {"file_path": "bad"}, "Error: failed"),
            (
                "read_file",
                {"file_path": "/logs/runtime.log"},
                "Error: a real log entry",
            ),
        ]
        for name, inputs, result in calls:
            await hooks.run_after(
                HookPoints.TOOL_EXECUTE,
                result={"tool_result": result, "is_error": False},
                resolved_tool_name=name,
                resolved_tool_input=inputs,
            )
        capture_tool_access(
            {"tool_name": "read_file", "tool_input": {"file_path": "/failure"}},
            {"tool_result": "bad", "is_error": True},
        )
        values = snapshot_resources()
        assert {item.uri for item in values} == {
            (tmp_path / "folder/a b.pdf").as_uri(),
            "file:///logs/runtime.log",
            "https://example.org/CV?a=1&b=2",
            URI,
        }
        assert all(item.name is None for item in values)
        assert not hasattr(values[0], "__dict__")
        assert not any("contents" in repr(item) for item in values)
    finally:
        reset_inference_scope(token)


def test_collector_bounds_dedup_and_no_io(monkeypatch):
    token = bind_inference_scope("job:bounds")
    try:
        monkeypatch.setattr(
            Path, "stat", lambda *_args, **_kwargs: pytest.fail("stat called")
        )
        monkeypatch.setattr(
            Path, "resolve", lambda *_args, **_kwargs: pytest.fail("resolve called")
        )
        capture_resource("urn:" + "x" * MAX_URI_CHARACTERS)
        capture_resource("urn:bad\n")
        capture_resource("urn:duplicate")
        capture_resource("urn:duplicate")
        assert len(snapshot_resources()) == 1
        for index in range(100):
            capture_resource(f"s3://bucket/{index}")
        assert len(snapshot_resources()) == MAX_RESOURCES_PER_TURN
        assert canonical_uri("ui://Mobbin/CV%20FILE?x=Y") == "ui://Mobbin/CV%20FILE?x=Y"
        assert (
            canonical_uri("a/../b.pdf", cwd="/workspace") == "file:///workspace/b.pdf"
        )
        assert canonical_uri("/a b.pdf") == "file:///a%20b.pdf"
    finally:
        reset_inference_scope(token)


@pytest.mark.parametrize(
    "uri",
    [
        URI,
        "s3://bucket/CV",
        "urn:document:1",
        "attachment://conversation/upload-1",
        "https://example.org/a?x=1&y=2",
    ],
)
def test_structured_and_legacy_parsing(uri):
    parsed = derive_memory_units(record(document=xml(uri)))
    assert parsed.resources[0].uri == uri
    assert parsed.resources[0].normalized_uri == uri
    assert len(parsed.resource_links) == 1
    legacy = (
        "<MEMORY><RESOURCES><RESOURCE>"
        + uri.replace("&", "&amp;")
        + "</RESOURCE></RESOURCES></MEMORY>"
    )
    parsed = derive_memory_units(record(document=legacy))
    assert parsed.resources[0].uri == uri
    assert parsed.resource_links == ()


def test_parser_invalid_relative_and_ambiguous_links():
    source = xml().replace(
        "</ENTITIES>",
        f"<ENTITY><NAME>{NAME}</NAME><DESCRIPTION>different person</DESCRIPTION></ENTITY></ENTITIES>",
    )
    parsed = derive_memory_units(record(document=source))
    assert parsed.resource_links == ()
    assert "unsafe_resource_entity_link" in parsed.warnings
    legacy = "<MEMORY><HEAD>Legacy</HEAD><RESOURCES><RESOURCE>cv/person.pdf</RESOURCE><RESOURCE>not a locator</RESOURCE></RESOURCES></MEMORY>"
    parsed = derive_memory_units(record(document=legacy, cwd="/workspace"))
    assert parsed.resources[0].uri == "file:///workspace/cv/person.pdf"
    assert "unparseable_resource_uri" in parsed.warnings
    assert any(unit.kind == "headline" for unit in parsed.units)
    parsed = derive_memory_units(record(document=legacy))
    assert parsed.resources[0].uri == "cv/person.pdf"
    assert parsed.resources[0].scheme == ""
    assert canonical_uri("README.md") == "README.md"


def test_authority_rejects_invention_rewrite_and_invalid_entity():
    source = xml(URI) + ""
    root = ET.fromstring(source)
    ET.SubElement(root.find("RESOURCES"), "RESOURCE", uri="https://invented.invalid")
    enforced = enforce_resources(
        ET.tostring(root, encoding="unicode"),
        (ResourceAccess(URI), ResourceAccess("urn:missing")),
    )
    parsed = derive_memory_units(record(document=enforced))
    assert {item.uri for item in parsed.resources} == {URI, "urn:missing"}
    assert len(parsed.resource_links) == 1
    assert (
        derive_memory_units(
            record(
                document=enforce_resources(
                    xml("UI://mobbin/search-screens.html"), (ResourceAccess(URI),)
                )
            )
        ).resource_links
        == ()
    )
    assert derive_memory_units(
        record(
            document=enforce_resources(xml(URI, "Wrong Name"), (ResourceAccess(URI),))
        )
    ).resource_links
    unknown = xml(URI).replace(f">{NAME}</ENTITY_REF>", ">Unknown</ENTITY_REF>")
    assert (
        derive_memory_units(
            record(document=enforce_resources(unknown, (ResourceAccess(URI),)))
        ).resource_links
        == ()
    )


@pytest.mark.asyncio
async def test_builder_fallback_llm_and_previous_links():
    operation = {
        "operation_id": "fictional",
        "user_message": "Review candidate CV",
        "assistant_messages": ["Reviewed"],
        "resources": (ResourceAccess(CV), ResourceAccess(URI)),
    }
    built = await MemoryRecordBuilder().build(operation)
    assert {
        item.uri
        for item in derive_memory_units(record(document=built.document)).resources
    } == {CV, URI}
    llm = SimpleNamespace(process_message=AsyncMock(return_value=xml(CV)))
    built = await MemoryRecordBuilder(llm).build(operation)
    parsed = derive_memory_units(record(document=built.document))
    assert len(parsed.resources) == 2 and len(parsed.resource_links) == 1
    assert llm.process_message.await_count == 1
    assert CV in llm.process_message.call_args.args[0]
    subsequent = await MemoryRecordBuilder().build(
        {**operation, "resources": ()}, built.document
    )
    assert (
        len(derive_memory_units(record(document=subsequent.document)).resource_links)
        == 1
    )


def test_consolidation_preserves_only_old_links_and_all_resources():
    old = xml(CV)
    other = xml(URI, "Other Candidate", linked=False)
    model = xml(URI, "Other Candidate")
    result = enforce_resources(model, (), (old, other))
    parsed = derive_memory_units(record(document=result))
    assert {item.uri for item in parsed.resources} == {CV, URI}
    assert len(parsed.resource_links) == 1
    linked_id = next(item.resource_id for item in parsed.resources if item.uri == CV)
    assert parsed.resource_links[0].resource_id == linked_id
    assert NAME in result


def test_candidate_exact_uri_relations_and_no_cross_links(tmp_path):
    repository = SQLiteMemoryRepository(tmp_path / "memory.db")
    repository.upsert_record(record())
    repository.upsert_record(
        record("other", xml("file:///other.pdf", "Other Candidate"))
    )
    repository.upsert_record(record("legacy", xml("urn:legacy", linked=False)))
    coordinator = MemorySearchCoordinator(repository)
    entity = coordinator.search(query())
    exact = coordinator.search(query(CV))
    assert exact.hits[0].memory_id == "candidate"
    assert CandidateSource.RESOURCE_URI in {
        reason.source for reason in exact.hits[0].reasons
    }
    evidence = next(
        hit for hit in entity.hits if hit.memory_id == "candidate"
    ).resources
    assert [(item.uri, item.entity_name, item.relation) for item in evidence] == [
        (CV, NAME, "source_document")
    ]
    assert not any(item.entity_name == "Other Candidate" for item in evidence)
    legacy = coordinator.search(query("urn:legacy"))
    assert legacy.hits[0].resources[0].entity_name is None
    assert CandidateSource.RESOURCE_ENTITY not in {
        reason.source for reason in legacy.hits[0].reasons
    }
    assert exact.hits[0].resources[0].entity_name == NAME


def test_sql_eligibility_before_cap_and_one_batch_late_hydration(tmp_path, monkeypatch):
    repository = SQLiteMemoryRepository(tmp_path / "memory.db")
    repository.upsert_record(record())
    repository.upsert_record(replace(record("other-agent"), agent_name="Different"))
    repository.upsert_record(replace(record("current"), session_id="current-session"))
    repository.upsert_record(replace(record("superseded"), status="superseded"))
    repository.upsert_record(record("other", xml("urn:other")))
    coordinator = MemorySearchCoordinator(repository)
    statements = []
    original = repository._connect

    def traced(**kwargs):
        connection = original(**kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository, "_connect", traced)
    exact = coordinator.search(query(CV, candidate_limit=1))
    assert [hit.memory_id for hit in exact.hits] == ["candidate"]
    assert not coordinator.search(query(CV, from_date=date(2026, 1, 1))).hits
    statements.clear()
    hits = coordinator.search(query())
    assert len(hits.hits) >= 2
    resource_queries = [sql for sql in statements if "FROM memory_resource r" in sql]
    assert len(resource_queries) == 1
    assert not any(
        "memory_resource" in sql for sql in statements if "WITH matches" in sql
    )
    exact, expression = _query_parts(CV)
    statements.clear()
    repository.candidates(query(CV), exact, expression)
    assert any("INDEXED BY resource_uri" in sql for sql in statements)


@pytest.mark.parametrize("fail_upgrade", [False, True])
def test_v1_upgrade_canonical_preservation_readonly_and_cascades(
    tmp_path, monkeypatch, fail_upgrade
):
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as connection:
        for statement in SCHEMA_V1:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_version VALUES (1)")
        connection.execute("PRAGMA user_version=1")
    original = record(
        document="<MEMORY><HEAD>Legacy</HEAD><RESOURCES><RESOURCE>https://example.org/runbook</RESOURCE><RESOURCE>invalid label</RESOURCE></RESOURCES></MEMORY>",
        metadata={"preserved": True},
    )
    _prepared, values = SQLiteMemoryRepository._prepare_record(original)
    from AgentCrew.modules.memory.sqlite_repository import RECORD_COLUMNS

    with sqlite3.connect(path) as connection:
        connection.execute(
            f"INSERT INTO memory_record ({','.join(RECORD_COLUMNS)}) VALUES ({','.join('?' for _ in values)})",
            values,
        )
    raw_metadata = ' { "preserved" : true } '
    with sqlite3.connect(path) as connection:
        from AgentCrew.modules.memory.record_parser import checksum

        connection.execute(
            "UPDATE memory_record SET metadata_json=?,metadata_checksum=?",
            (raw_metadata, checksum(raw_metadata)),
        )
    with pytest.raises(SQLiteMemoryError):
        SQLiteMemoryRepository(path, read_only=True)
    if fail_upgrade:

        def fail(*_args):
            raise RuntimeError("fictional schema upgrade failure")

        monkeypatch.setattr(
            SQLiteMemoryRepository, "_write_resources", staticmethod(fail)
        )
        with pytest.raises(RuntimeError):
            SQLiteMemoryRepository(path)
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='memory_resource'"
                ).fetchone()
                is None
            )
            assert connection.execute(
                "SELECT document,metadata_json FROM memory_record"
            ).fetchone() == (original.document, raw_metadata)
        return
    repository = SQLiteMemoryRepository(path)
    with repository._transaction() as connection:
        row = connection.execute("SELECT * FROM memory_record").fetchone()
        assert row["document"] == original.document
        assert row["metadata_json"] == raw_metadata
        assert repository._record(row).metadata == original.metadata
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
            == 2
        )
        assert (
            connection.execute("SELECT count(*) FROM memory_resource").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM memory_resource_entity"
            ).fetchone()[0]
            == 0
        )
        assert "unparseable_resource_uri" in row["parse_warnings_json"]
    SQLiteMemoryRepository(path, read_only=True).check_read_only_integrity()
    repository.upsert_record(record("linked"))
    repository.delete_record("linked", "Engineer")
    with repository._transaction() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM memory_resource_entity"
            ).fetchone()[0]
            == 0
        )
    repository.delete_record(original.id, original.agent_name)
    with repository._transaction() as connection:
        assert (
            connection.execute("SELECT count(*) FROM memory_resource").fetchone()[0]
            == 0
        )


def test_transaction_rollback_migration_counts_and_verification(tmp_path, monkeypatch):
    repository = SQLiteMemoryRepository(tmp_path / "memory.db")
    original = record()
    assert repository.apply_migration_batch(
        [(original, "source-hash")], "fictional-source"
    ) == (1, 0)
    assert (
        repository.verify_migration_batch(
            [(original, "source-hash")], "fictional-source"
        )
        == 1
    )
    counts = repository.migration_counts("fictional-source")
    assert counts["memory_resource"] == counts["memory_resource_entity"] == 1

    def fail(*_args):
        raise RuntimeError("fictional rollback")

    monkeypatch.setattr(SQLiteMemoryRepository, "_write_resources", staticmethod(fail))
    with pytest.raises(RuntimeError):
        repository.upsert_record(record("new"))
    with repository._transaction() as connection:
        assert (
            connection.execute("SELECT count(*) FROM memory_record").fetchone()[0] == 1
        )
        connection.execute("SELECT count(*) FROM memory_resource")
    monkeypatch.undo()
    with repository._transaction(write=True) as connection:
        connection.execute("DELETE FROM memory_resource_entity")
    with pytest.raises(SQLiteMemoryError, match="resource projection"):
        repository.verify_migration_batch(
            [(original, "source-hash")], "fictional-source"
        )


@pytest.mark.asyncio
async def test_a2a_original_and_raw_locators_never_temp_paths(monkeypatch):
    from AgentCrew.modules.a2a.attachment_processor import AttachmentProcessor

    processor = AttachmentProcessor()
    processor._file_handler = SimpleNamespace(
        async_process_file=AsyncMock(
            return_value={"type": "text", "text": "CV contents"}
        )
    )
    token = bind_inference_scope("a2a:conversation-1")
    try:
        result = await processor.process_attachments(
            {
                "id": "message-1",
                "content": [
                    {
                        "type": "file",
                        "file_name": "candidate.pdf",
                        "file_data": b"fictional",
                    },
                    {"type": "file_uri", "uri": "/durable/candidate.pdf"},
                ],
            }
        )
        assert len(result["content"]) == 2
        values = snapshot_resources()
        assert values[0].uri.startswith("attachment://")
        assert "message-1" in values[0].uri
        assert values[1].uri == "file:///durable/candidate.pdf"
        assert not any("/tmp/" in item.uri for item in values)
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
async def test_actual_tool_dispatch_uses_resolved_input_and_skips_exceptions():
    from AgentCrew.modules.agents.local_agent import LocalAgent
    from AgentCrew.modules.events.hooks import Hook, HookPhase

    hooks = HookRegistry.get_instance()
    hooks.register(
        Hook(
            HookPoints.TOOL_EXECUTE,
            HookPhase.BEFORE,
            lambda context: {
                **context,
                "tool_name": "read_file",
                "tool_input": {"file_path": "/actual/cv.pdf"},
            },
        )
    )
    agent = SimpleNamespace(
        name="FictionalAgent",
        llm=SimpleNamespace(execute_tool=AsyncMock(return_value="CV contents")),
    )
    token = bind_inference_scope("chat:dispatch")
    try:
        assert (
            await LocalAgent.execute_tool_call(
                agent, {"name": "requested_tool", "input": {"path": "/not-used"}}
            )
            == "CV contents"
        )
        assert snapshot_resources() == (ResourceAccess("file:///actual/cv.pdf"),)
        agent.llm.execute_tool.side_effect = OSError("fictional read failure")
        with pytest.raises(OSError):
            await LocalAgent.execute_tool_call(
                agent, {"name": "read_file", "input": {"file_path": "/failure"}}
            )
        assert len(snapshot_resources()) == 1
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
async def test_mcp_dynamic_registration_and_real_handler_outcomes():
    from AgentCrew.modules.mcpclient.service import MCPService

    service = object.__new__(MCPService)
    service.server_resources = {"fictional": [{"uri": URI, "name": "Screens"}]}
    service._get_server_config = Mock(return_value={"url": "not-used"})
    session = SimpleNamespace(
        read_resource=AsyncMock(return_value=SimpleNamespace(contents=[]))
    )
    service._create_session = AsyncMock(return_value=(session, object()))
    service._close_session = AsyncMock()
    service._format_resource_result = AsyncMock(
        return_value=[{"type": "text", "text": "content"}]
    )
    agent = SimpleNamespace(register_tool=Mock())
    service._register_get_resource_tool(agent, "fictional-id", "fictional")
    factory = agent.register_tool.call_args.args[1]
    handler = factory()
    token = bind_inference_scope("chat:mcp")
    try:
        assert snapshot_resources() == ()
        result = await handler(URI)
        capture_tool_access(
            {
                "resolved_tool_name": "fictional__get_resource",
                "resolved_tool_input": {"uri": URI},
            },
            {"tool_result": result, "is_error": False},
        )
        assert snapshot_resources() == (ResourceAccess(URI),)
        session.read_resource.assert_awaited_once_with(URI)
        assert service._close_session.await_count == 1
        session.read_resource.side_effect = OSError("fictional unavailable")
        failed = await handler(URI)
        assert failed.accessed is False
        assert service._close_session.await_count == 2
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
async def test_chat_pending_attachment_and_scope_seed(tmp_path):
    from AgentCrew.modules.chat.message.commands.file_commands import FileCommands
    from AgentCrew.modules.chat.message.handler import MessageHandler
    from AgentCrew.modules.llm.token_usage import TokenUsage

    handler = SimpleNamespace(
        file_handler=SimpleNamespace(
            async_process_file=AsyncMock(
                return_value={"type": "text", "text": "fictional CV"}
            )
        ),
        bus=SimpleNamespace(emit_sync=Mock()),
        _pending_resource_attachments=[],
        _messages_append=Mock(),
        agent=SimpleNamespace(name="FictionalAgent"),
        current_conversation_id="conversation-1",
        tool_manager=SimpleNamespace(reset_request_yolo_mode=Mock()),
        voice_service=None,
        _clear_stream_session=Mock(),
    )
    session = SimpleNamespace(
        bind=Mock(), finished=SimpleNamespace(is_set=lambda: True)
    )
    handler._create_stream_session = Mock(return_value=session)

    async def run_response(*_args):
        assert snapshot_resources() == (
            ResourceAccess((tmp_path / "candidate.pdf").as_uri()),
        )
        return "Reviewed", TokenUsage()

    handler._run_stream_response = run_response
    await FileCommands(handler).handle_file(f"/file {tmp_path / 'candidate.pdf'}")
    assert len(handler._pending_resource_attachments) == 1
    assert snapshot_resources() == ()
    response, _usage = await MessageHandler.get_assistant_response(handler)
    assert response == "Reviewed"
    assert handler._pending_resource_attachments == []
    assert snapshot_resources() == ()
    handler._pending_resource_attachments.append((tmp_path / "candidate.pdf").as_uri())
    handler._create_stream_session.side_effect = RuntimeError("setup failed")
    with pytest.raises(RuntimeError):
        await MessageHandler.get_assistant_response(handler)
    assert snapshot_resources() == () and current_inference_scope() is None
    assert handler._pending_resource_attachments == [
        (tmp_path / "candidate.pdf").as_uri()
    ]
    handler._create_stream_session.side_effect = None
    response, _usage = await MessageHandler.get_assistant_response(handler)
    assert response == "Reviewed" and handler._pending_resource_attachments == []


@pytest.mark.asyncio
async def test_http_a2a_preserves_original_url(monkeypatch):
    from AgentCrew.modules.a2a.attachment_processor import AttachmentProcessor

    response = SimpleNamespace(content=b"fictional CV", raise_for_status=Mock())
    client = Mock()
    client.__enter__ = Mock(
        return_value=SimpleNamespace(get=Mock(return_value=response))
    )
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(
        sys.modules, "httpx2", SimpleNamespace(Client=Mock(return_value=client))
    )
    processor = AttachmentProcessor()
    processor._file_handler = SimpleNamespace(
        async_process_file=AsyncMock(return_value={"type": "text", "text": "CV"})
    )
    token = bind_inference_scope("a2a:http")
    try:
        url = "https://example.org/CV.pdf?version=2"
        await processor.process_attachments(
            {"content": [{"type": "file_uri", "uri": url, "file_name": "CV.pdf"}]}
        )
        assert snapshot_resources() == (ResourceAccess(url, "CV.pdf"),)
    finally:
        reset_inference_scope(token)


def test_chroma_map_derives_portable_resources_and_verifies_tampering(tmp_path):
    from AgentCrew.modules.memory.migrate_chroma_memory_to_sqlite import map_record

    source = xml(URI)
    mapped, source_hash = map_record(
        "fictional-chroma",
        source,
        {"agent": "Engineer", "date": 1, "session_id": "old"},
    )
    assert mapped.document == source
    repository = SQLiteMemoryRepository(tmp_path / "mapped.db")
    repository.apply_migration_batch([(mapped, source_hash)], "chroma-fixture")
    assert (
        repository.verify_migration_batch([(mapped, source_hash)], "chroma-fixture")
        == 1
    )
    assert (
        MemorySearchCoordinator(repository)
        .search(query(URI))
        .hits[0]
        .resources[0]
        .entity_name
        == NAME
    )


def test_multiple_entities_one_parent_and_duplicate_names_are_not_crosslinked(tmp_path):
    first = ET.fromstring(xml())
    second = ET.fromstring(xml("urn:other-candidate", "Other Candidate"))
    first.find("ENTITIES").extend(second.find("ENTITIES"))
    first.find("RESOURCES").extend(second.find("RESOURCES"))
    repository = SQLiteMemoryRepository(tmp_path / "multiple.db")
    repository.upsert_record(record(document=ET.tostring(first, encoding="unicode")))
    hit = MemorySearchCoordinator(repository).search(query()).hits[0]
    assert {(item.uri, item.entity_name) for item in hit.resources} == {
        (CV, NAME),
        ("urn:other-candidate", "Other Candidate"),
    }
    duplicate = xml().replace(
        "</ENTITIES>", f"<ENTITY><NAME>{NAME}</NAME></ENTITY></ENTITIES>"
    )
    assert derive_memory_units(record(document=duplicate)).resource_links == ()


def test_resources_pass_immutably_through_existing_store_queue():
    from AgentCrew.modules.memory.sqlite_service import SQLiteMemoryService

    service = object.__new__(SQLiteMemoryService)
    service._session_id = "fictional-session"
    service._worker = SimpleNamespace(queue_store=Mock(return_value=True))
    token = bind_inference_scope("job:queue")
    try:
        capture_attachment(
            None, "CV.pdf", scope="fictional-session", identifier="upload-1"
        )
        expected = snapshot_resources()
        assert SQLiteMemoryService.store_conversation(
            service, "user", ["assistant"], "Engineer"
        )
        queued = service._worker.queue_store.call_args.args[0]
        assert isinstance(queued["resources"], tuple)
        assert queued["resources"] == expected
    finally:
        reset_inference_scope(token)
    assert queued["resources"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_actual_acp_embedded_attachment_scope_and_cleanup(outcome):
    from contextlib import asynccontextmanager

    from AgentCrew.modules.acp.agent import AgentCrewAcpAgent

    @asynccontextmanager
    async def request(_session):
        yield

    state = SimpleNamespace(
        pending_ask_tool=None,
        pending_resources=(),
        history=[],
        title="Existing session",
        cancelled=False,
        current_task=None,
        tool_state=SimpleNamespace(acp_active_terminals={}),
    )
    lock = asyncio.Lock()
    agent = SimpleNamespace(
        _session_request=request,
        _get_session_lock=lambda _session: lock,
        _sessions={"session-1": state},
        _tool_manager=SimpleNamespace(client_capabilities={}),
        _conn=None,
        _client_comm=SimpleNamespace(send_agent_message=AsyncMock()),
        _session_lifecycle=SimpleNamespace(_persist_session=AsyncMock()),
    )

    async def run_turn(*_args):
        assert snapshot_resources() == (ResourceAccess(URI),)
        if outcome == "error":
            raise RuntimeError("fictional failure")
        if outcome == "cancel":
            raise asyncio.CancelledError

    agent._turn_executor = SimpleNamespace(run_turn=run_turn)
    prompt = [
        SimpleNamespace(
            type="resource",
            resource=SimpleNamespace(uri=URI, text="Fictional attachment"),
        ),
        SimpleNamespace(
            type="resource_link", uri="ui://advertised/not-fetched", name="Not fetched"
        ),
    ]
    response = await AgentCrewAcpAgent.prompt(
        agent, "session-1", prompt, message_id="message-1"
    )
    assert (
        response.stop_reason
        == {"success": "end_turn", "error": "refusal", "cancel": "cancelled"}[outcome]
    )
    assert snapshot_resources() == () and current_inference_scope() is None


def test_unicode_entity_ambiguity_and_absolute_path_without_cwd(monkeypatch):
    source = xml(name="Candidate").replace(
        "</ENTITIES>", "<ENTITY><NAME>Ｃａｎｄｉｄａｔｅ</NAME></ENTITY></ENTITIES>"
    )
    enforced = enforce_resources(source, (ResourceAccess(CV),))
    assert ET.fromstring(enforced).findall("RESOURCES/RESOURCE/ENTITY_REF") == []
    monkeypatch.setattr("os.getcwd", Mock(side_effect=FileNotFoundError("cwd deleted")))
    assert (
        canonical_uri("/durable/../candidate.pdf", path=True) == "file:///candidate.pdf"
    )


@pytest.mark.asyncio
async def test_ambiguous_merge_cannot_destroy_preserved_links():
    source = xml().replace(
        "</ENTITIES>",
        f"<ENTITY><NAME>{NAME}</NAME><DESCRIPTION>Ambiguous identity</DESCRIPTION></ENTITY></ENTITIES>",
    )
    with pytest.raises(ValueError, match="ambiguous_preserved_resource_entity"):
        enforce_resources(source, (), (xml(),))
    llm = SimpleNamespace(process_message=AsyncMock(return_value=source))
    built = await MemoryRecordBuilder(llm).build(
        {
            "operation_id": "fictional",
            "user_message": "More candidate details",
            "assistant_messages": ["Reviewed"],
            "resources": (ResourceAccess(URI),),
        },
        xml(),
    )
    parsed = derive_memory_units(record(document=built.document))
    assert {item.uri for item in parsed.resources} == {URI, CV}
    assert len(parsed.resource_links) == 1
    assert llm.process_message.await_count == 1


def _queueing_agent():
    from AgentCrew.modules.agents.local_agent import LocalAgent
    from AgentCrew.modules.memory.sqlite_service import SQLiteMemoryService

    service = object.__new__(SQLiteMemoryService)
    service._session_id = "fictional-session"
    service._worker = SimpleNamespace(queue_store=Mock(return_value=True))
    agent = object.__new__(LocalAgent)
    agent.name = "FictionalAgent"
    agent.llm = Mock()
    agent._memory_coordinator = SimpleNamespace(
        extract_last_user_message_for_memory=lambda _history: "Review CV",
        store_memory_if_available=lambda user, _history, response, session: (
            service.store_conversation(user, [response], agent.name, session)
        ),
    )
    agent.format_message = lambda _kind, data: {
        "role": "assistant",
        "content": [{"type": "text", "text": data.get("message", "tool result")}],
    }
    return agent, service._worker.queue_store


class _FictionalStream:
    def __init__(self, callback, tools=()):
        self.callback = callback
        self.tools = list(tools)

    async def __aiter__(self):
        from AgentCrew.modules.llm.token_usage import TokenUsage

        self.callback(self.tools, TokenUsage())
        yield "Reviewed CV", "", None

    def format_assistant_message(self, response, **_kwargs):
        return {"role": "assistant", "content": [{"type": "text", "text": response}]}

    async def aclose(self):
        pass


def test_actual_job_file_preparation_queues_canonical_uri(monkeypatch, tmp_path):
    from contextlib import contextmanager

    from AgentCrew.app import AgentCrewApplication
    from AgentCrew.modules.utils.file_handler import FileHandler

    agent, queued = _queueing_agent()
    agent.process_messages = lambda _history, callback: _FictionalStream(callback)
    path = tmp_path / "Candidate CV.pdf"
    monkeypatch.setattr(
        FileHandler,
        "async_process_file",
        AsyncMock(side_effect=[{"type": "text", "text": "CV"}, None]),
    )
    manager = SimpleNamespace(
        get_local_agent=lambda _name: agent,
        select_agent_async=AsyncMock(return_value=True),
    )

    @contextmanager
    def host():
        with asyncio.Runner() as runner:
            yield runner

    app = SimpleNamespace(
        _host_lifecycle=host,
        setup=SimpleNamespace(
            resolve_runtime_model=Mock(
                return_value=SimpleNamespace(provider="fictional", model_id="mock")
            ),
            setup_services=Mock(return_value={}),
            setup_agents=Mock(),
        ),
        agent_manager=manager,
    )
    assert (
        AgentCrewApplication.run_job(
            app,
            "Review CV",
            agent=agent.name,
            files=[str(path), str(tmp_path / "failed.pdf")],
        )
        == "Reviewed CV"
    )
    operation = queued.call_args.args[0]
    assert operation["resources"] == (ResourceAccess(path.as_uri(), path.name),)
    built = asyncio.run(MemoryRecordBuilder().build(operation))
    assert [
        node.get("uri")
        for node in ET.fromstring(built.document).findall("RESOURCES/RESOURCE")
    ] == [path.as_uri()]
    assert snapshot_resources() == () and current_inference_scope() is None


@pytest.mark.parametrize(
    "path, expected",
    [
        (r"C:\cvs\Candidate CV.pdf", "file:///C:/cvs/Candidate%20CV.pdf"),
        ("C:/cvs/../candidate.pdf", "file:///C:/candidate.pdf"),
        (r"\\server\share\Candidate CV.pdf", "file://server/share/Candidate%20CV.pdf"),
        ("//server/share/candidate.pdf", "file://server/share/candidate.pdf"),
    ],
)
def test_windows_paths_are_host_independent_uris(path, expected):
    from AgentCrew.modules.memory.resources import query_resource_uris

    assert canonical_uri(path) == expected
    assert canonical_uri(path, path=True) == expected
    assert expected in query_resource_uris(path)
    token = bind_inference_scope("acp:windows")
    try:
        capture_attachment(path, "Candidate CV")
        for tool in ("read_file", "acp_read_file"):
            capture_tool_access(
                {
                    "resolved_tool_name": tool,
                    "resolved_tool_input": {"file_path": path},
                },
                {"tool_result": "Read CV", "is_error": False},
            )
        assert snapshot_resources() == (ResourceAccess(expected, "Candidate CV"),)
    finally:
        reset_inference_scope(token)
    assert canonical_uri(URI) == URI
    assert canonical_uri("s://Bucket/EXACT?x=1") == "s://Bucket/EXACT?x=1"
    assert (
        canonical_uri("candidate.pdf", cwd=r"C:\cvs") == "file:///C:/cvs/candidate.pdf"
    )


def _ask(identifier):
    return {
        "id": identifier,
        "name": "ask",
        "input": {"questions": [{"question": "Continue?", "guided_answers": ["Yes"]}]},
    }


def _scripted_agent(stages):
    agent, queued = _queueing_agent()
    turns = iter(stages)
    agent.process_messages = lambda _history, callback: _FictionalStream(
        callback, next(turns)
    )
    agent.validate_tool_use = Mock(return_value=None)
    agent.llm.execute_tool = AsyncMock(return_value="Read fictional file")
    agent.calculate_usage_cost = Mock(return_value=0.0)
    agent.get_model = Mock(return_value="fictional")
    return agent, queued


@pytest.mark.asyncio
@pytest.mark.parametrize("new_count", [1, 40])
async def test_actual_acp_chained_asks_keep_bounded_resources(new_count, tmp_path):
    from contextlib import asynccontextmanager

    from AgentCrew.modules.acp.agent import AgentCrewAcpAgent
    from AgentCrew.modules.acp.session_state import AcpSessionState
    from AgentCrew.modules.acp.turn_executor import TurnExecutor

    new_tools = [
        {
            "id": f"read-{index}",
            "name": "read_file",
            "input": {"file_path": f"/fictional/cv-{index}.pdf"},
        }
        for index in range(new_count)
    ]
    new_tools.insert(
        0,
        {
            "id": "duplicate",
            "name": "read_file",
            "input": {"file_path": "/home/user/cvs/example-candidate.pdf"},
        },
    )
    agent, queued = _scripted_agent([[_ask("ask-1")], [*new_tools, _ask("ask-2")], []])
    comm = SimpleNamespace(
        **{
            name: AsyncMock()
            for name in (
                "send_agent_message",
                "send_thought_chunk",
                "send_ask_request",
                "send_tool_started",
                "send_tool_completed",
                "send_usage_update",
                "send_session_info_update",
            )
        }
    )
    manager = SimpleNamespace(
        client_capabilities={}, ensure_tools_for_session=AsyncMock()
    )
    turns = TurnExecutor(comm, manager)
    turns._get_agent = AsyncMock(return_value=agent)
    state = AcpSessionState(str(tmp_path), agent.name, title="Candidate review")

    def queue_before_clear(operation):
        assert state.pending_resources
        assert operation["resources"][0].uri == CV
        return True

    queued.side_effect = queue_before_clear
    lock = asyncio.Lock()

    @asynccontextmanager
    async def request(_session):
        yield

    host = SimpleNamespace(
        _session_request=request,
        _get_session_lock=lambda _session: lock,
        _sessions={"session": state},
        _tool_manager=manager,
        _conn=None,
        _client_comm=comm,
        _turn_executor=turns,
        _session_lifecycle=SimpleNamespace(_persist_session=AsyncMock()),
    )
    attached = SimpleNamespace(
        type="resource", resource=SimpleNamespace(uri=CV, text="Candidate CV")
    )
    response = await AgentCrewAcpAgent.prompt(host, "session", [attached])
    assert response.stop_reason == "end_turn" and state.pending_ask_tool is not None
    assert state.pending_resources == ((CV, None),)
    queued.assert_not_called()
    assert snapshot_resources() == ()
    answer = [SimpleNamespace(type="text", text="q0: Yes")]
    await AgentCrewAcpAgent.prompt(host, "session", answer)
    assert state.pending_ask_tool["tool_use"]["id"] == "ask-2"
    assert len(state.pending_resources) == min(32, new_count + 1)
    assert state.pending_resources[0] == (CV, None)
    queued.assert_not_called()
    await AgentCrewAcpAgent.prompt(host, "session", answer)
    queued.assert_called_once()
    captured = queued.call_args.args[0]["resources"]
    assert len(captured) == min(32, new_count + 1)
    assert captured[0] == ResourceAccess(CV)
    assert len({item.uri for item in captured}) == len(captured)
    built = await MemoryRecordBuilder().build(queued.call_args.args[0])
    assert {
        node.get("uri")
        for node in ET.fromstring(built.document).findall("RESOURCES/RESOURCE")
    } == {item.uri for item in captured}
    assert state.pending_resources == () and state.pending_ask_tool is None
    assert current_inference_scope() is None and snapshot_resources() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "redis"])
async def test_actual_a2a_chained_asks_keep_attachment_and_remaining_tool_resources(
    store_kind, monkeypatch, tmp_path
):
    from a2a.types.a2a_pb2 import Message, Part, Role, TaskState

    from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor
    from AgentCrew.modules.a2a.session_store import (
        FileSessionStore,
        InMemorySessionStore,
        RedisSessionStore,
    )
    from AgentCrew.modules.utils.file_handler import FileHandler

    reads = [
        {
            "id": f"read-{index}",
            "name": "read_file",
            "input": {"file_path": f"/fictional/candidate-{index}.pdf"},
        }
        for index in range(2)
    ]
    agent, queued = _scripted_agent(
        [[_ask("ask-1"), reads[0], _ask("ask-2"), reads[1]], []]
    )
    if store_kind == "memory":
        store = InMemorySessionStore()
    elif store_kind == "file":
        store = FileSessionStore(str(tmp_path / "a2a"))
    else:
        store = RedisSessionStore()
        data = {}
        store._redis = SimpleNamespace(
            get=AsyncMock(side_effect=lambda key: data.get(key)),
            setex=AsyncMock(
                side_effect=lambda key, _ttl, value: data.update({key: value})
            ),
            delete=AsyncMock(side_effect=lambda key: data.pop(key, None)),
        )
    executor = AgentCrewA2AExecutor(agent, store)
    clear_pending = store.clear_pending_tools

    async def clear_after_queue(*args):
        queued.assert_called_once()
        await clear_pending(*args)

    monkeypatch.setattr(store, "clear_pending_tools", clear_after_queue)
    monkeypatch.setattr(
        FileHandler,
        "async_process_file",
        AsyncMock(return_value={"type": "text", "text": "Candidate CV"}),
    )
    events = SimpleNamespace(enqueue_event=AsyncMock())
    context = SimpleNamespace(
        task_id="task-1",
        context_id="context-1",
        call_context=None,
        current_task=None,
        message=Message(
            role=Role.ROLE_USER, parts=[Part(url=CV, filename="Candidate CV.pdf")]
        ),
    )
    await executor.execute(context, events)
    pending = await store.get_pending_tools("task-1", "default", agent.name)
    assert pending is not None and pending["ask_tool_use"]["id"] == "ask-1"
    assert [tuple(pair) for pair in pending["resources"]] == [(CV, "Candidate CV.pdf")]
    assert len(pending["remaining_tools"]) == 3
    assert (
        await store.get_pending_tools("task-1", "different-owner", agent.name) is None
    )
    assert await store.get_pending_tools("task-1", "default", "OtherAgent") is None
    queued.assert_not_called()
    assert current_inference_scope() is None and snapshot_resources() == ()
    context.message = Message(role=Role.ROLE_USER, parts=[Part(text="Yes")])
    await executor.execute(context, events)
    pending = await store.get_pending_tools("task-1", "default", agent.name)
    assert pending["ask_tool_use"]["id"] == "ask-2"
    assert {pair[0] for pair in pending["resources"]} == {
        CV,
        "file:///fictional/candidate-0.pdf",
    }
    queued.assert_not_called()
    await executor.execute(context, events)
    queued.assert_called_once()
    operation = queued.call_args.args[0]
    assert {item.uri for item in operation["resources"]} == {
        CV,
        "file:///fictional/candidate-0.pdf",
        "file:///fictional/candidate-1.pdf",
    }
    assert operation["resources"][0] == ResourceAccess(CV, "Candidate CV.pdf")
    built = await MemoryRecordBuilder().build(operation)
    assert {
        node.get("uri")
        for node in ET.fromstring(built.document).findall("RESOURCES/RESOURCE")
    } == {item.uri for item in operation["resources"]}
    assert await store.get_pending_tools("task-1", "default", agent.name) is None
    assert any(
        getattr(event.args[0], "status", None)
        and event.args[0].status.state == TaskState.TASK_STATE_COMPLETED
        for event in events.enqueue_event.call_args_list
    )
    assert current_inference_scope() is None and snapshot_resources() == ()
