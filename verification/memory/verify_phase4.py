"""Fictional Phase 4 checks: expanded search tool, frozen policy, registration.

No network, embeddings, private memory, or sealed Phase 3 artifacts are used.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
executable = Path(sys.executable)
if executable.is_symlink():
    executable = Path(os.path.abspath(sys.executable))
for site in (executable.parent.parent / "lib").glob("python*/site-packages"):
    site_path = str(site)
    if site_path not in sys.path:
        sys.path.append(site_path)

from AgentCrew.modules.memory import tool
from AgentCrew.modules.memory.base_service import BaseMemoryService
from AgentCrew.modules.memory.benchmark import POLICY
from AgentCrew.modules.memory.chroma_service import ChromaMemoryService
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.sqlite_service import SQLiteMemoryService
from AgentCrew.modules.memory.types import (
    CandidateSource,
    ConfidenceBand,
    MatchReason,
    MemoryQuery,
    MemoryRecord,
    MemorySearchHit,
    MemorySearchResult,
    SQLiteSemanticPolicy,
)

FROZEN_POLICY = {
    "name": "qualified-sqlite-signals-v1",
    "fts_weights": [6, 4, 1, 8],
    "rrf_k": 60,
    "kind_weight": 1.1,
    "min_overlap": 0.35,
    "relative_coverage": 0.7,
    "sqlite_semantic": {
        "metric": "cosine_distance",
        "admission": "explicit-cutoff-before-rrf",
        "default_max_distance": None,
        "cross_source_tie": "diversity-then-stable-id",
    },
}
FROZEN_FILE_HASHES = {
    "scripts/benchmark_memory_retrieval.py": "306f944191043e3ffc365e1991d09d3166a841cd399ee725ef95b36274227c90",
    "scripts/extract_memory_calibration.py": "73871314c8b32481085c178817ae2c084e1322d137a9422b7a9a6c0c71a7b186",
    "AgentCrew/modules/memory/benchmark.py": "09613535d8638d7b7381f4905461919699334fa9db9d79cacf02f4db7b696bd8",
    "AgentCrew/modules/memory/retrieval.py": "a602e6965cd332a48aa10dee0de395866925a019d9e9085cc3901499dd85fa05",
    "AgentCrew/modules/memory/record_parser.py": "c27552f2d77d344a37b68a3df3c18b03c81041f54d98e735081c77f16aca2e36",
    "AgentCrew/modules/memory/types.py": "64a7cd9a321cb624c5aa3c97469d2ecfdd3e9e4e10b1149fb54d738238d1ab06",
    "AgentCrew/modules/memory/voyageai_ef.py": "503ce1c384b354041a28639d7465c9c9a952f840ff7606d3d51b6eccf653593a",
}
PHASE4_REPOSITORY_METHODS = (
    "__init__",
    "_candidate",
    "_connect",
    "_cosine_distance",
    "_eligibility",
    "_ensure_vector_table",
    "_exact_values",
    "_prepare_record",
    "_record",
    "_semantic_candidates",
    "_transaction",
    "_unit_values",
    "_validate_schema",
    "_verify_migrated_record",
    "_write_record",
    "apply_migration_batch",
    "candidates",
    "canonical_checksum",
    "check_fts5",
    "check_integrity",
    "copy_local_document_vectors",
    "delete_record",
    "get_record",
    "migration_counts",
    "upsert_record",
    "validate_record",
    "verify_migration_batch",
)
FROZEN_REPOSITORY_METHODS_DIGEST = (
    "8a5903f49667bc2b297c19eca408daa5531de3a40fe0ae65fcb6fa328d90332a"
)


def _repository_methods_digest() -> str:
    digest = hashlib.sha256()
    for name in PHASE4_REPOSITORY_METHODS:
        member = vars(SQLiteMemoryRepository).get(name)
        if member is None:
            return ""
        func = member.__func__ if hasattr(member, "__func__") else member
        digest.update(name.encode())
        digest.update(inspect.getsource(func).encode())
    return digest.hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_hit(
    memory_id: str, excerpt: str = "fictional excerpt", kind: str = "conversation"
) -> MemorySearchHit:
    return MemorySearchHit(
        memory_id=memory_id,
        unit_id=kind,
        date="2026-11-12",
        cwd=None,
        kind=kind,
        headline="Fictional headline",
        excerpt=excerpt,
        reasons=(
            MatchReason(CandidateSource.SEMANTIC, 0, "vector similarity 0.90", 0.1),
        ),
        confidence=ConfidenceBand.HIGH,
        score=0.9,
    )


class FictionalLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def process_message(self, prompt, temperature=0, model_id=None):
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected model call")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class RecordingService:
    llm_service = None

    def __init__(self, hits_by_query=None):
        self.hits_by_query = hits_by_query or {}
        self.queries = []
        self.documents_calls = []
        self.session_id = "fictional-session"

    def search_structured(self, query):
        self.queries.append(query)
        return MemorySearchResult(tuple(self.hits_by_query.get(query.query, ())))

    def get_memory_documents(self, memory_ids, agent_name="None"):
        self.documents_calls.append((tuple(memory_ids), agent_name))
        return {"mem-1": "FULL FICTIONAL DOCUMENT"}


class ExplodingService:
    llm_service = None
    session_id = "fictional-session"

    def search_structured(self, query):
        raise RuntimeError("fictional backend outage")


class LegacyService(BaseMemoryService):
    def __init__(self, text):
        self.text = text
        self.session_id = ""

    def store_conversation(self, *args, **kwargs):
        return []

    def clear_conversation_context(self):
        pass

    def load_conversation_context(self, *args, **kwargs):
        pass

    def retrieve_memory(
        self, keywords, from_date=None, to_date=None, agent_name="None"
    ):
        return self.text

    def list_memory_headers(self, *args, **kwargs):
        return []

    def cleanup_old_memories(self, months=1):
        return 0

    def forget_topic(self, *args, **kwargs):
        return {}

    def forget_ids(self, *args, **kwargs):
        return {}

    def delete_by_conversation_id(self, *args, **kwargs):
        return {}

    def get_agent_memory_corpus(
        self, agent_name="None", max_items=100, exclude_session_id=None
    ):
        records = [
            {
                "id": "legacy-1",
                "document": "legacy document one",
                "metadata": {"agent": agent_name},
            },
            {
                "id": "legacy-2",
                "document": "legacy document two",
                "metadata": {"agent": "OtherAgent"},
            },
        ]
        return [
            record for record in records if record["metadata"]["agent"] == agent_name
        ][:max_items]

    def mark_memories_evolved(self, *args, **kwargs):
        return 0


def run_handler(service, agent_name="FictionalAgent", **params):
    context = tool.MemoryToolContext(agent_name=agent_name, memory_service=service)
    handler = tool.get_memory_retrieve_tool_handler(context)
    return asyncio.run(handler(**params))


def checks_policy_freeze():
    for path, expected in FROZEN_FILE_HASHES.items():
        digest = hashlib.sha256()
        digest.update(path.encode())
        digest.update((ROOT / path).read_bytes())
        require(
            digest.hexdigest() == expected, f"Retrieval-critical file changed: {path}"
        )
    require(
        _repository_methods_digest() == FROZEN_REPOSITORY_METHODS_DIGEST,
        "Retrieval-critical SQLiteMemoryRepository methods changed",
    )
    require(POLICY == FROZEN_POLICY, "Frozen retrieval policy changed")
    require(
        SQLiteSemanticPolicy().max_distance is None,
        "Semantic cutoff must default to disabled",
    )
    query = MemoryQuery(
        query="fictional policy probe",
        agent_name="FictionalAgent",
        current_session_id="fictional-session",
    )
    require(
        query.semantic_policy.max_distance is None,
        "Default query semantic policy changed",
    )


def checks_tool_definition():
    definition = tool.get_memory_retrieve_tool_definition()
    function = definition["function"]
    require(function["name"] == "search_memory", "Tool renamed")
    properties = function["parameters"]["properties"]
    require(
        set(properties)
        == {"query", "from_date", "to_date", "kinds", "memory_ids", "detail", "limit"},
        "Expanded parameters missing",
    )
    require(
        function["parameters"]["required"] == ["query"], "Required parameters changed"
    )
    require(
        properties["kinds"]["items"]["enum"] == list(tool.MEMORY_KINDS),
        "Kind enum changed",
    )
    require(
        properties["detail"]["enum"] == list(tool.DETAIL_MODES), "Detail modes changed"
    )
    require("agent" not in properties, "Agent scope must stay server-side")
    description = function["description"]
    require(
        "semantic search" not in description.lower(), "Inaccurate vector wording kept"
    )
    require("full-text search" in description.lower(), "Accurate wording missing")
    require(
        "scoped to the current agent" in description, "Agent scoping wording missing"
    )
    for guidance in (description, tool.memory_instruction_prompt()):
        require(
            "detail='summary'" in guidance
            and "canonical Memory ID" in guidance
            and "detail='evidence' or 'full'" in guidance,
            "Two-stage retrieval guidance missing",
        )
    for guidance in (
        description,
        properties["memory_ids"]["description"],
        tool.memory_instruction_prompt(),
    ):
        require(
            "canonical Memory ID values" in guidance
            and "unchanged" in guidance
            and "Citation values are display/provenance only, not alternate selectors"
            in guidance,
            "Canonical-only selector contract not documented",
        )


def checks_handler_contract():
    service = RecordingService({"fictional alpha details": (make_hit("mem-1"),)})
    out = run_handler(service, query="fictional alpha details")
    require(
        "untrusted recalled data" in out
        and "1. [memory:mem-1#conversation]" in out
        and "Memory ID: mem-1\n" in out
        and "Citation: memory:mem-1#conversation\n" in out,
        "Summary format broke",
    )
    require("Matched because" not in out, "Summary must stay compact")

    out = run_handler(service, query="fictional alpha details", detail="evidence")
    require(
        "Matched because" in out and "Unknown (legacy memory)" in out,
        "Evidence format broke",
    )

    out = run_handler(service, query="fictional alpha details", detail="full")
    require("FULL FICTIONAL DOCUMENT" in out, "Full detail did not expand the record")
    require(
        service.documents_calls[-1] == (("mem-1",), "FictionalAgent"),
        "Document expansion calls changed",
    )

    out = run_handler(
        service,
        query="fictional alpha details",
        from_date="2026-11-12",
        to_date="2026-11-12",
    )
    require(
        out.startswith("Memory search failed: from_date must be earlier"),
        "Date validation changed",
    )

    out = run_handler(service, query="fictional alpha details", from_date="not-a-date")
    require(out.startswith("Memory search failed:"), "Invalid date must fail open")

    out = run_handler(
        service,
        query="fictional alpha details",
        from_date="2026-11-01",
        to_date="2026-11-12",
    )
    require("memory:mem-1" in out, "Valid date range broke retrieval")
    require(
        service.queries[-1].from_date == date(2026, 11, 1),
        "Date filter not passed through",
    )
    require(
        service.queries[-1].to_date == date(2026, 11, 12),
        "Date filter not passed through",
    )

    out = run_handler(ExplodingService(), query="fictional alpha details")
    require(
        out.startswith("Memory search failed: fictional backend outage"),
        "Fail-open behavior changed",
    )

    for bad_limit in (0, 21, "5", True, 2.5):
        out = run_handler(service, query="fictional alpha details", limit=bad_limit)
        require(
            out.startswith("Memory search failed: limit"),
            f"Limit bound rejected {bad_limit!r} wrongly",
        )
    for good_limit in (1, 20):
        out = run_handler(service, query="fictional alpha details", limit=good_limit)
        require("memory:mem-1" in out, f"Valid limit {good_limit} rejected")

    out = run_handler(service, query="fictional alpha details", kinds=["bogus"])
    require(
        out.startswith("Memory search failed: Unknown memory kinds"),
        "Unknown kind accepted",
    )
    out = run_handler(
        service, query="fictional alpha details", kinds=["Fact", "decision"]
    )
    require(
        service.queries[-1].kinds == ("decision", "fact"), "Kind hint not normalized"
    )

    out = run_handler(service, query="fictional alpha details", detail="bogus")
    require(out.startswith("Memory search failed: detail"), "Unknown detail accepted")

    out = run_handler(service, query="fictional alpha details", kinds="decision")
    require(
        out.startswith("Memory search failed: kinds must be an array"),
        "Non-array kinds accepted",
    )
    out = run_handler(service, query="fictional alpha details", memory_ids="mem-1")
    require(
        out.startswith("Memory search failed: memory_ids must be an array"),
        "Non-array ids accepted",
    )

    for memory_id in (
        "mem-1",
        "memory:alpha",
        "memory:alpha#note",
        "session:agent",
        "session#agent",
        " memory:alpha#note ",
        "memory:",
        "memory:#note",
        "memory:mem-1#",
        "memory:mem-1#note#extra",
        "memory:mem-1##note",
        "memory:memory:mem-1",
        "memory: mem-1#note",
        "memory:mem-1# note",
        "memory:mem-1 #note",
    ):
        run_handler(service, query="fictional alpha details", memory_ids=[memory_id])
        require(
            service.queries[-1].memory_ids == (memory_id,),
            f"Opaque canonical ID rewritten: {memory_id!r}",
        )
    run_handler(
        service,
        query="fictional alpha details",
        memory_ids=[
            "memory:mem-2#note",
            "mem-1",
            "memory:mem-2",
            "memory:mem-1#conversation",
            "memory:mem-2#note",
            " mem-3 ",
            "mem-3",
            "mem-1",
            " mem-3 ",
            "session_Agent Name",
        ],
    )
    require(
        service.queries[-1].memory_ids
        == (
            "memory:mem-2#note",
            "mem-1",
            "memory:mem-2",
            "memory:mem-1#conversation",
            " mem-3 ",
            "mem-3",
            "session_Agent Name",
        ),
        "Only exact duplicate IDs must be removed in first-seen order",
    )

    out = run_handler(service, query="fictional missing topic")
    require(out.startswith("No memories found for"), "Empty result message changed")

    require(
        service.queries[0].agent_name == "FictionalAgent",
        "Agent scope must be server-side",
    )
    require(
        service.queries[0].current_session_id == "fictional-session",
        "Session identity lost",
    )

    missing = tool.MemoryToolContext(agent_name="FictionalAgent", memory_service=None)
    handler = tool.get_memory_retrieve_tool_handler(missing)
    try:
        asyncio.run(handler(query="fictional alpha details"))
    except ValueError as error:
        require(
            "Memory service not found" in str(error), "Missing service message changed"
        )
    else:
        raise AssertionError("Missing service did not raise")

    try:
        asyncio.run(handler(query="ab"))
    except ValueError:
        pass
    else:
        raise AssertionError("Short query did not raise")


def checks_invalid_memory_references():
    from unittest.mock import patch

    service = RecordingService({"fictional alpha": (make_hit("mem-1"),)})
    service.llm_service = FictionalLLM([])
    controls = [chr(code) for code in (*range(32), 127)]
    invalid_ids = ["", " ", "\u2003", None, 123, False, {}, []]
    invalid_ids.extend(
        value
        for control in controls
        for value in (f"mem{control}1", f"{control}mem-1", f"mem-1{control}")
    )
    with patch.dict(os.environ, {"MEMORY_RERANK_ENABLED": "1"}):
        for memory_id in invalid_ids:
            for memory_ids in ([memory_id], ["mem-1", memory_id]):
                out = run_handler(
                    service,
                    query="fictional alpha",
                    memory_ids=memory_ids,
                    detail="full",
                )
                require(
                    out.startswith("Memory search failed: memory_ids"),
                    f"Malformed restriction accepted: {memory_ids!r}",
                )
        for memory_ids in (None, "", False, 0, {}, (), "mem-1"):
            out = run_handler(
                service, query="fictional alpha", memory_ids=memory_ids, detail="full"
            )
            require(
                out.startswith("Memory search failed: memory_ids must be an array"),
                f"Malformed ID array accepted: {memory_ids!r}",
            )
    require(not service.queries, "Malformed IDs must not broaden lookup")
    require(not service.documents_calls, "Malformed IDs must not hydrate")
    require(not service.llm_service.calls, "Malformed IDs must not trigger LLM")


def checks_summary_to_full_hydration():
    with TemporaryDirectory(prefix="agentcrew-drilldown-") as directory:
        service = SQLiteMemoryService(Path(directory) / "memory.sqlite3")
        service.session_id = "fictional-session"
        timestamp = datetime(2026, 11, 12, 12, tzinfo=UTC).timestamp()
        document = (
            "<MEMORY><HEAD>Fictional drilldown</HEAD><CONVERSATION_NOTES><NOTE>"
            + "fictional alpha detail " * 40
            + "FULL RECORD TAIL</NOTE></CONVERSATION_NOTES></MEMORY>"
        )
        record = MemoryRecord(
            id="mem-1",
            agent_name="FictionalAgent",
            session_id="past-session",
            document=document,
            created_at=timestamp,
            updated_at=timestamp,
        )
        records = [
            replace(
                record,
                id=memory_id,
                document=document.replace(
                    "Fictional drilldown", f"Fictional OpaqueProbe{index}"
                ),
            )
            for index, memory_id in enumerate(
                (
                    "mem-1",
                    "memory:alpha",
                    "memory:alpha#note",
                    "session:agent",
                    "session#agent",
                    " padded:id#part ",
                )
            )
        ]
        try:
            for item in records:
                service._repository.upsert_record(item)
            for blocked in (
                replace(record, id="other-agent", agent_name="OtherAgent"),
                replace(record, id="current-session", session_id=service.session_id),
                replace(record, id="deleted", status="deleted"),
                replace(record, id="superseded", status="superseded"),
                replace(record, id="linked", superseded_by=record.id),
            ):
                service._repository.upsert_record(blocked)
            for index, item in enumerate(records):
                summary = run_handler(
                    service, query=f"OpaqueProbe{index}", detail="summary"
                )
                memory_ids = [
                    line.removeprefix("   Memory ID: ")
                    for line in summary.splitlines()
                    if line.startswith("   Memory ID: ")
                ]
                citation = next(
                    line.removeprefix("   Citation: ")
                    for line in summary.splitlines()
                    if line.startswith("   Citation: ")
                )
                require(memory_ids == [item.id], "Summary rewrote canonical identity")
                require(
                    "FULL RECORD TAIL" not in summary, "Summary must remain bounded"
                )
                for detail in ("evidence", "full"):
                    out = run_handler(
                        service,
                        query="unrelated followup",
                        memory_ids=memory_ids,
                        detail=detail,
                        from_date="2026-11-01",
                        to_date="2026-11-13",
                    )
                    require(
                        f"Memory ID: {item.id}\n" in out
                        and f"Citation: memory:{item.id}#" in out
                        and "untrusted recalled data" in out,
                        f"Drilldown changed identity or trust warning: {item.id!r}",
                    )
                    if detail == "full":
                        require(
                            item.document in out, "Full record was not hydrated exactly"
                        )
                out = run_handler(
                    service,
                    query="fictional alpha",
                    memory_ids=[citation],
                    detail="full",
                )
                require(
                    out.startswith("No memories found"),
                    "Display citation must not select its source record",
                )
                collision = replace(
                    item,
                    id=citation,
                    document=item.document.replace(
                        "FULL RECORD TAIL", "LITERAL ID TAIL"
                    ),
                )
                service._repository.upsert_record(collision)
                out = run_handler(
                    service,
                    query="unrelated followup",
                    memory_ids=[citation],
                    detail="full",
                )
                require(
                    f"Memory ID: {collision.id}\n" in out
                    and collision.document in out
                    and item.document not in out,
                    "Citation-shaped ID must select only that exact canonical record",
                )
            for blocked_id in (
                "other-agent",
                "current-session",
                "deleted",
                "superseded",
                "linked",
                "unknown",
            ):
                out = run_handler(
                    service,
                    query="fictional alpha",
                    memory_ids=[blocked_id],
                    detail="full",
                )
                require(out.startswith("No memories found"), "Eligibility bypassed")
                require(document not in out, "Ineligible document hydrated")
            for dates in (
                {"from_date": "2026-11-13"},
                {"to_date": "2026-11-11"},
            ):
                out = run_handler(
                    service,
                    query="fictional alpha",
                    memory_ids=[records[2].id],
                    detail="full",
                    **dates,
                )
                require(
                    out.startswith("No memories found"), "Date eligibility bypassed"
                )
        finally:
            service.shutdown()


def checks_sqlite_direct_hydration():
    from contextlib import contextmanager
    from unittest.mock import patch

    with TemporaryDirectory(prefix="agentcrew-direct-hydration-") as directory:
        service = SQLiteMemoryService(Path(directory) / "memory.sqlite3")
        service.session_id = "fictional-session"
        timestamp = datetime(2026, 11, 12, 12, tzinfo=UTC).timestamp()
        target = MemoryRecord(
            id=" memory:old#target ",
            agent_name="FictionalAgent",
            session_id="past-session",
            document=(
                "<MEMORY><HEAD>Fictional ArchivedHydrationProbe</HEAD>"
                "<CONVERSATION_NOTES><NOTE>"
                + "fictional archived evidence " * 40
                + "BEYOND CORPUS TAIL</NOTE></CONVERSATION_NOTES></MEMORY>"
            ),
            created_at=timestamp,
            updated_at=timestamp,
        )
        companion = replace(target, id="opaque:'companion#part", document="COMPANION")
        foreign = replace(
            target, id="foreign:id#part", agent_name="OtherAgent", document="FOREIGN"
        )
        try:
            for record in (target, companion, foreign):
                service._repository.upsert_record(record)
            for index in range(501):
                service._repository.upsert_record(
                    replace(
                        target,
                        id=f"newer-{index:03}",
                        document="<MEMORY><HEAD>Fictional newer entry</HEAD></MEMORY>",
                        created_at=timestamp + index + 1,
                        updated_at=timestamp + index + 1,
                    )
                )
            corpus = service.get_agent_memory_corpus("FictionalAgent", max_items=500)
            require(
                len(corpus) == 500 and target.id not in {row["id"] for row in corpus},
                "Archived target must be outside the 500-record corpus window",
            )
            summary = run_handler(service, query="ArchivedHydrationProbe")
            require(
                f"Memory ID: {target.id}\n" in summary,
                "Archived target must remain eligible for search",
            )
            memory_ids = [
                line.removeprefix("   Memory ID: ")
                for line in summary.splitlines()
                if line.startswith("   Memory ID: ")
            ]
            out = run_handler(
                service,
                query="unrelated followup",
                memory_ids=memory_ids,
                detail="full",
            )
            require(
                f"   Record: {target.document}\n" in out,
                "Full hydration must not depend on the newest 500 corpus records",
            )
            requested = [
                companion.id,
                "missing",
                target.id,
                companion.id,
                foreign.id,
                target.id,
                "' OR 1=1 --",
            ]
            statements = []
            transaction = service._repository._transaction

            @contextmanager
            def observed_transaction():
                with transaction() as connection:
                    require(
                        connection.in_transaction
                        and connection.execute("PRAGMA query_only").fetchone()[0] == 1,
                        "Document expansion must use an active read-only transaction",
                    )
                    connection.set_trace_callback(statements.append)
                    yield connection

            with (
                patch.object(
                    service,
                    "get_agent_memory_corpus",
                    side_effect=AssertionError("Hydration must not scan the corpus"),
                ),
                patch.object(
                    service._repository,
                    "_transaction",
                    side_effect=observed_transaction,
                ) as transactions,
            ):
                documents = service.get_memory_documents(requested, "FictionalAgent")
                require(
                    transactions.call_count == 1, "Hydration needs one read transaction"
                )
                require(
                    list(documents.items())
                    == [
                        (companion.id, companion.document),
                        (target.id, target.document),
                    ],
                    "Direct hydration lost requested order, exact IDs, documents or agent scope",
                )
                require(
                    len([sql for sql in statements if sql.startswith("SELECT")])
                    == len(dict.fromkeys(requested)),
                    "Duplicate IDs must not cause duplicate document queries",
                )
                require(
                    statements[-1] == "COMMIT",
                    "Document expansion must stay inside one read transaction",
                )
                transactions.reset_mock()
                for ids, agent in (
                    ([], "FictionalAgent"),
                    ([target.id], ""),
                    ([target.id], " \t"),
                    ([target.id], None),
                    ([target.id], 123),
                ):
                    require(
                        service.get_memory_documents(ids, agent) == {},
                        "Empty IDs or unusable agent scope must return no documents",
                    )
                require(
                    transactions.call_count == 0,
                    "Empty IDs or unusable scope must not open a transaction",
                )
                out = run_handler(
                    service,
                    query="unrelated followup",
                    memory_ids=memory_ids,
                    detail="full",
                )
                require(
                    target.document in out, "Tool hydration fell back to a corpus scan"
                )
        finally:
            service.shutdown()


def checks_legacy_adapter():
    legacy = LegacyService("recalled fictional legacy text")
    result = legacy.search_structured(
        MemoryQuery(
            query="fictional legacy probe",
            agent_name="FictionalAgent",
            current_session_id="fictional-session",
        )
    )
    require(
        len(result.hits) == 1
        and result.hits[0].excerpt == "recalled fictional legacy text",
        "Legacy adapter broke",
    )
    empty = LegacyService("")
    result = empty.search_structured(
        MemoryQuery(
            query="fictional legacy probe",
            agent_name="FictionalAgent",
            current_session_id="fictional-session",
        )
    )
    require(not result.hits, "Legacy empty adapter broke")
    documents = legacy.get_memory_documents(["legacy-1", "legacy-2"], "FictionalAgent")
    require(
        documents == {"legacy-1": "legacy document one"},
        "Legacy document expansion broke",
    )


def checks_expansion():
    llm = FictionalLLM(
        [
            '```json\n{"queries": ["fictional beta phrase", "fictional gamma phrase"]}\n```'
        ]
    )
    service = RecordingService(
        {
            "fictional alpha": (),
            "fictional beta phrase": (make_hit("mem-b"),),
            "fictional gamma phrase": (make_hit("mem-b"), make_hit("mem-c")),
        }
    )
    service.llm_service = llm
    out = run_handler(service, query="fictional alpha")
    require("mem-b" in out and "mem-c" in out, "Expansion merge broke")
    require(len(llm.calls) == 1, "Expansion must be one bounded call")
    require(
        [query.query for query in service.queries]
        == ["fictional alpha", "fictional beta phrase", "fictional gamma phrase"],
        "Expanded phrases not searched in order",
    )
    require(out.count("Citation: memory:mem-b#") == 1, "Expansion dedupe broke")

    llm = FictionalLLM(['{"queries": ["safe"], "scope": "override"}'])
    service = RecordingService({"fictional alpha": ()})
    service.llm_service = llm
    out = run_handler(service, query="fictional alpha")
    require(
        out.startswith("No memories found") and len(llm.calls) == 1,
        "Expansion schema fallback broke",
    )

    llm = FictionalLLM(['{"queries": ["a", "b", "c", "d"]}'])
    service = RecordingService({"fictional alpha": ()})
    service.llm_service = llm
    out = run_handler(service, query="fictional alpha")
    require(
        out.startswith("No memories found") and len(llm.calls) == 1,
        "Expansion bound fallback broke",
    )

    llm = FictionalLLM([ValueError("fictional LLM outage")])
    service = RecordingService({"fictional alpha": ()})
    service.llm_service = llm
    out = run_handler(service, query="fictional alpha")
    require(
        out.startswith("No memories found") and len(llm.calls) == 1,
        "Expansion error fallback broke",
    )

    llm = FictionalLLM([])
    service = RecordingService({"fictional alpha": (make_hit("mem-x"),)})
    service.llm_service = llm
    run_handler(service, query="fictional alpha")
    require(not llm.calls, "Expansion must not run when hits exist")

    llm = FictionalLLM([])
    service = RecordingService({"fictional alpha": ()})
    service.llm_service = llm
    run_handler(service, query="fictional alpha", memory_ids=["mem-known"])
    require(not llm.calls, "Expansion must not run for ID-restricted follow-ups")

    class PartialService(RecordingService):
        def search_structured(self, query):
            self.queries.append(query)
            if query.query == "fictional broken phrase":
                raise RuntimeError("fictional phrase outage")
            return MemorySearchResult(tuple(self.hits_by_query.get(query.query, ())))

    llm = FictionalLLM(
        ['{"queries": ["fictional beta phrase", "fictional broken phrase"]}']
    )
    service = PartialService(
        {"fictional alpha": (), "fictional beta phrase": (make_hit("mem-b"),)}
    )
    service.llm_service = llm
    out = run_handler(service, query="fictional alpha")
    require(
        "mem-b" in out, "Expansion must continue deterministically past a failed phrase"
    )


def checks_rerank():
    llm = FictionalLLM([])
    service = RecordingService(
        {"fictional alpha": (make_hit("mem-1"), make_hit("mem-2"))}
    )
    service.llm_service = llm
    run_handler(service, query="fictional alpha")
    require(not llm.calls, "Reranking must stay disabled by default")

    os.environ["MEMORY_RERANK_ENABLED"] = "1"
    try:
        llm = FictionalLLM(['{"ranked_ids": ["mem-2", "mem-1"], "abstain": false}'])
        service = RecordingService(
            {"fictional alpha": (make_hit("mem-1"), make_hit("mem-2"))}
        )
        service.llm_service = llm
        out = run_handler(service, query="fictional alpha")
        require(
            out.index("memory:mem-2") < out.index("memory:mem-1"), "Rerank order broke"
        )
        require(len(llm.calls) == 1, "Rerank must be one bounded call")
        require("mem-2" in llm.calls[0], "Rerank payload must cite known candidates")

        llm = FictionalLLM(
            ['{"ranked_ids": ["mem-1", "mem-foreign"], "abstain": false}']
        )
        service = RecordingService(
            {"fictional alpha": (make_hit("mem-1"), make_hit("mem-2"))}
        )
        service.llm_service = llm
        out = run_handler(service, query="fictional alpha")
        require(
            out.index("memory:mem-1") < out.index("memory:mem-2"),
            "Unknown rerank ID must fall back",
        )

        llm = FictionalLLM(['{"ranked_ids": ["mem-1", "mem-1"], "abstain": false}'])
        service = RecordingService(
            {"fictional alpha": (make_hit("mem-1"), make_hit("mem-2"))}
        )
        service.llm_service = llm
        out = run_handler(service, query="fictional alpha")
        require(
            out.index("memory:mem-1") < out.index("memory:mem-2"),
            "Duplicate rerank ID must fall back",
        )

        llm = FictionalLLM(['{"ranked_ids": [], "abstain": true}'])
        service = RecordingService({"fictional alpha": (make_hit("mem-1"),)})
        service.llm_service = llm
        out = run_handler(service, query="fictional alpha")
        require(out.startswith("No memories found"), "Rerank abstention broke")

        llm = FictionalLLM(['{"ranked_ids": [], "abstain": false}'])
        service = RecordingService({"fictional alpha": (make_hit("mem-1"),)})
        service.llm_service = llm
        out = run_handler(service, query="fictional alpha")
        require("memory:mem-1" in out, "Contradictory rerank abstention must fall back")

        llm = FictionalLLM([ValueError("fictional rerank outage")])
        service = RecordingService({"fictional alpha": (make_hit("mem-1"),)})
        service.llm_service = llm
        out = run_handler(service, query="fictional alpha")
        require("memory:mem-1" in out, "Rerank error must fall back deterministically")
    finally:
        del os.environ["MEMORY_RERANK_ENABLED"]


class FictionalCollection:
    def __init__(self):
        self.observed = []
        self.rows = {
            "chroma-1": (
                "doc one " * 100,
                {
                    "date": 1760000000,
                    "agent": "FictionalAgent",
                    "session_id": "other",
                    "type": "conversation",
                    "header": "Fictional header",
                },
            ),
            "chroma-2": (
                "doc two",
                {
                    "date": 1760100000,
                    "agent": "FictionalAgent",
                    "session_id": "fictional-session",
                    "type": "conversation",
                },
            ),
            "chroma-3": (
                "doc three",
                {
                    "date": 1760200000,
                    "agent": "OtherAgent",
                    "session_id": "other",
                    "type": "conversation",
                },
            ),
        }

    def _passes(self, metadata, where):
        if where is None:
            return True
        conditions = where.get("$and", [where])
        for condition in conditions:
            for key, rule in condition.items():
                if key not in metadata:
                    return False
                if not isinstance(rule, dict):
                    if metadata[key] != rule:
                        return False
                    continue
                if "$ne" in rule and metadata[key] == rule["$ne"]:
                    return False
                if "$gte" in rule and not (metadata[key] >= rule["$gte"]):
                    return False
                if "$lte" in rule and not (metadata[key] <= rule["$lte"]):
                    return False
        return True

    def query(self, **kwargs):
        self.observed.append(("query", kwargs))
        ids = [
            memory_id
            for memory_id, (_, metadata) in self.rows.items()
            if self._passes(metadata, kwargs.get("where"))
        ][: kwargs.get("n_results", 10)]
        return {
            "ids": [ids],
            "distances": [
                [0.1 if memory_id == "chroma-1" else 0.4 for memory_id in ids]
            ],
            "metadatas": [[self.rows[memory_id][1] for memory_id in ids]],
            "documents": [[self.rows[memory_id][0] for memory_id in ids]],
        }

    def get(self, **kwargs):
        self.observed.append(("get", kwargs))
        ids = [
            memory_id
            for memory_id in kwargs.get("ids", [])
            if memory_id in self.rows
            and self._passes(self.rows[memory_id][1], kwargs.get("where"))
        ]
        include = kwargs.get("include", [])
        out = {"ids": ids}
        if "documents" in include:
            out["documents"] = [self.rows[memory_id][0] for memory_id in ids]
        if "metadatas" in include:
            out["metadatas"] = [self.rows[memory_id][1] for memory_id in ids]
        return out


def checks_chroma_adapter():
    service = object.__new__(ChromaMemoryService)
    service._collection = FictionalCollection()
    service.session_id = "fictional-session"
    service.relevant_threshold = 0.3

    result = service.search_structured(
        MemoryQuery(
            query="fictional alpha",
            agent_name="FictionalAgent",
            current_session_id="fictional-session",
            limit=5,
        )
    )
    where = service._collection.observed[0][1]["where"]
    require(
        "FictionalAgent" in str(where) and "$ne" in str(where),
        "Chroma scope conditions changed",
    )
    require(
        [hit.memory_id for hit in result.hits] == ["chroma-1"],
        "Chroma session/threshold filtering changed",
    )
    hit = result.hits[0]
    require(hit.citation == "memory:chroma-1#conversation", "Chroma citation changed")
    require(hit.confidence.value == "high", "Chroma confidence banding changed")
    require(
        hit.headline == "Fictional header" and len(hit.excerpt) == 360,
        "Chroma hit fields changed",
    )
    require(
        hit.reasons[0].source == CandidateSource.SEMANTIC, "Chroma match reason changed"
    )

    result = service.search_structured(
        MemoryQuery(
            query="fictional alpha",
            agent_name="FictionalAgent",
            current_session_id="fictional-session",
            memory_ids=("chroma-3", "chroma-1", "chroma-1"),
        )
    )
    require(
        [hit.memory_id for hit in result.hits] == ["chroma-1"],
        "Chroma ID restriction changed",
    )
    require(
        result.hits[0].reasons[0].source == CandidateSource.MEMORY_ID
        and result.hits[0].confidence.value == "high",
        "Chroma exact-ID reason changed",
    )

    documents = service.get_memory_documents(["chroma-3", "chroma-1"], "FictionalAgent")
    require(
        sorted(documents) == ["chroma-1"], "Chroma document expansion scope changed"
    )

    service._collection.observed.clear()
    service.search_structured(
        MemoryQuery(
            query="fictional alpha",
            agent_name="FictionalAgent",
            current_session_id="fictional-session",
            from_date=date(2025, 10, 10),
            to_date=date(2025, 10, 11),
        )
    )
    where = str(service._collection.observed[0][1]["where"])
    require("$gte" in where and "$lte" in where, "Chroma date filters changed")


def checks_registration_surfaces():
    from unittest.mock import patch

    class FictionalAgent:
        name = "FictionalAgent"

        def register_tool(
            self, definition_func, handler_factory, service_instance=None
        ):
            return None

    service = RecordingService()
    with patch("AgentCrew.modules.tools.registration.register_tool") as register_tool:
        tool.register(
            service_instance=service,
            persistence_service=None,
            agent=FictionalAgent(),
            llm_service=None,
        )
    require(register_tool.call_count == 2, "Memory registration count changed")
    definitions = {
        call.args[0]()["function"]["name"]: call.args[0]()
        for call in register_tool.call_args_list
    }
    require(
        set(definitions) == {"search_memory", "forget_memory_topic"},
        "Registered tool set changed",
    )
    require(
        set(definitions["search_memory"]["function"]["parameters"]["properties"])
        == {"query", "from_date", "to_date", "kinds", "memory_ids", "detail", "limit"},
        "Registered search definition is not the expanded one",
    )

    with patch("AgentCrew.modules.tools.registration.register_tool") as register_tool:
        tool.register(
            service_instance=service,
            persistence_service=object(),
            agent=FictionalAgent(),
            llm_service=None,
        )
    require(register_tool.call_count == 3, "Persistence registration count changed")

    registrar = (ROOT / "AgentCrew/modules/agents/tool_registrar.py").read_text(
        encoding="utf-8"
    )
    require(
        "register as register_memory" in registrar and '"memory"' in registrar,
        "LocalAgent registrar no longer binds memory tools",
    )
    local_agent = (ROOT / "AgentCrew/modules/agents/local_agent.py").read_text(
        encoding="utf-8"
    )
    require("AgentToolRegistrar" in local_agent, "LocalAgent no longer registers tools")
    cursor_agent = (ROOT / "AgentCrew/modules/agents/cursor_agent.py").read_text(
        encoding="utf-8"
    )
    require(
        "get_memory_retrieve_tool_definition" in cursor_agent
        and "get_memory_retrieve_tool_handler" in cursor_agent,
        "Cursor-agent path no longer binds the shared search tool",
    )
    for surface in (
        "AgentCrew/modules/agents/tools/delegate.py",
        "AgentCrew/modules/agents/agent_runner.py",
        "AgentCrew/modules/a2a/agent_executor.py",
        "AgentCrew/modules/acp/turn_executor.py",
    ):
        source = (ROOT / surface).read_text(encoding="utf-8")
        require(
            "LocalAgent" in source, f"{surface} no longer routes through LocalAgent"
        )

    context_manager = (ROOT / "AgentCrew/modules/agents/context_manager.py").read_text(
        encoding="utf-8"
    )
    require(
        "call search_memory before responding" in context_manager,
        "Automatic headline injection changed",
    )
    require(
        "call search_memory before responding" in cursor_agent,
        "Cursor headline injection changed",
    )


def main():
    checks_policy_freeze()
    checks_tool_definition()
    checks_handler_contract()
    checks_invalid_memory_references()
    checks_summary_to_full_hydration()
    checks_sqlite_direct_hydration()
    checks_legacy_adapter()
    checks_expansion()
    checks_rerank()
    checks_chroma_adapter()
    checks_registration_surfaces()
    print(
        "PASS Phase 4 frozen-policy, expanded-tool, fail-open, expansion, rerank, drilldown, detail, adapter and registration checks"
    )
    print(
        "Fictional data only; no network, embeddings, private memory or sealed Phase 3 artifacts were used."
    )


if __name__ == "__main__":
    main()
