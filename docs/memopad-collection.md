# Memopad collection (SQLite, first slice)

Memopad is an **item-level, derived, untrusted knowledge layer** collected during
explicit memory-store actions. It is not another conversation summary store, an
adaptive behavior, or authorization. **Nothing is injected into agent context.**
There is no historical backfill, vector-search change, background polling, or
additional LLM request. Existing memory extraction makes at most eight optional
proposals; canonical memory persistence takes precedence over this projection.

## Evaluate collection

In console or GUI chat:

```text
/memopad stats
```

This reads a consistent **local database-wide** snapshot across projects and
agents. Output includes total items, counts by status/kind/project key/agent,
source memories represented, orphan count, mean application-derived confidence,
and items supported by multiple distinct source memories. It does not print
statements, raw tool results, resource URIs, or workspace paths. It does not ask
the agent or an LLM to inspect the data. Collection is asynchronous, so wait for
the memory worker to finish before expecting the latest turn in these counts.
An empty result is normal when no durable proposals were extracted.

Chroma reports this capability as unsupported; it does **not** collect memopad.
SQLite is the default engine. Switching to Chroma does not delete existing SQLite
memopad data, but subsequent Chroma stores do not update it.

For offline inspection without starting a memory worker, open the existing SQLite
store read-only (no automatic schema upgrade in this mode):

```bash
uv run python - <<'PY'
from AgentCrew.modules.memory.engine_selection import sqlite_store_path
from AgentCrew.modules.memory.memopad import format_memopad_stats
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository

repository = SQLiteMemoryRepository(sqlite_store_path(), read_only=True)
print(format_memopad_stats(repository.memopad_stats()))
PY
```

Use the same `MEMORYDB_PATH` as the running application. Production defaults to
`~/.AgentCrew/memorydb`; development defaults to `./memory_db`. The database file
is `agent_memory.sqlite3`. Schema 1/2 stores upgrade transactionally on normal
SQLite memory startup; read-only inspection requires an already upgraded store.
For programmatic scoped inspection, `memopad_stats(agent_name=..., project_key=...)`
and `SQLiteMemoryService.get_memopad_stats(...)` accept optional exact filters.
No agent tool is registered for memopad.

## Stable project identity

Optionally create and commit `.agentcrew/project.json` **yourself** in the
workspace/repository, using strict JSON:

```json
{"id": "b2895ce0-75d2-41a1-b8b0-ad1fa33b0269", "name": "Example project"}
```

`id` is a stable UUID or project key: 1–128 ASCII letters, digits, underscores,
dots or hyphens, starting with a letter or digit. `name` is a nonempty display name
of at most 128 characters. These are the only supported fields. The file is
bounded to 4096 bytes. Invalid/unreadable files fall back deterministically; they
are never rewritten. Resolution is read-only and Git commands have one-second
timeouts; no network is used.

Resolution order:

1. Nearest valid `.agentcrew/project.json` within the captured workspace/Git root.
   Linked worktrees also check the shared main repository root.
2. Normalized Git `origin` identity: host plus repository path, removing transport,
   userinfo, query/fragment, trailing slash and `.git`. HTTPS and SSH spellings
   normally share identity. Remote-backed clones share identity intentionally.
3. Git common-directory identity when no usable origin exists (shared across
   linked worktrees, including separate Git-directory layouts).
4. Canonical workspace-root hash outside Git. A `.agentcrew` directory can mark
   that root; without a marker, the captured working directory is the fallback.

Persistent keys are namespace-prefixed SHA-256 digests, **never raw paths or remote
URLs**. Display names are separate. Explicit IDs are hashed too. Changing an ID,
origin, or an unmarked non-Git workspace location changes future identity; use an
explicit ID to avoid this. There is no automatic identity merge or rewrite of
previously collected items. Identity is resolved in the existing memory worker
from the queued working directory, not by the context manager.

## Extraction and confidence

Supported kinds: `decision`, `lesson`, `constraint`, `correction`, `workaround`,
`unresolved`, `verified_fact`, `project_preference`.

Each proposal contains only `kind`, `title` (160 characters), `statement` (1200
characters) and a descriptive `subject_key` (128 characters). Invalid lists,
unknown kinds/fields, excessive length, invalid controls, and lists longer than
eight are discarded without invalidating canonical extraction. Missing memopad
is valid. A lightweight guard drops proposals containing labeled credentials,
bearer credentials, private-key headers or credential-bearing HTTP URLs. This is
not a complete secret detector: do not put secrets in conversations. The model is
also instructed to omit secrets and raw tool payloads.

**All proposals start as `candidate` with confidence 0.35.** Current extraction
cannot safely prove exact source-level user/tool attribution, so there is no
automatic promotion. Even `verified_fact` is a proposed kind, not a verified
status. Importance is application-derived: 0.7 for decisions, constraints and
corrections, otherwise 0.5. Distinct supporting memories are counted, but do not
automatically increase confidence or activate an item. Statistics measure
collection volume and provenance coverage, not demonstrated factual accuracy.

An item identity hashes agent ownership, project key, kind, normalized subject,
and whitespace-normalized **case-sensitive** statement. Exact replay is
idempotent; paraphrases and contradictions remain distinct candidates. There is
no semantic deduplication, contradiction resolution or manual promotion UI yet.

## Schema and lifecycle

SQLite schema version **3** adds:

- `memopad`: opaque item ID, agent/project identity and display name, kind, title,
  statement, subject, validated status, application-derived confidence/importance,
  creation/update times, optional validity range/supersession, support count.
- `memopad_evidence`: item/canonical-memory links with a document-checksum snapshot
  and optional retrieval-unit ID/checksum snapshots. One link per item and memory.
- Scope/source indexes and transactional evidence support/deletion triggers.

Canonical memory is committed **before** memopad projection in a separate
transaction. Memopad resolution/write failures are content-free warnings and do
not undo canonical storage or interrupt worker/session progress. There is no
retry queue or automatic backfill for failed projections in this slice.

Evidence links reference the canonical source memory with `ON DELETE CASCADE`.
Unit IDs/checksums are historical snapshots, not live foreign keys: cumulative
memory updates rebuild those units. A unit link is attached only for a unique
exact statement match; otherwise the canonical memory is the evidence link.
Document/unit checksums identify the observed revision; they do not archive that
revision or prove the model statement. Updating cumulative memory does not remove
old proposals or automatically revalidate them.

Consolidation preserves original source memories and their evidence links. An
item supported by a superseded source remains a candidate supported by that
preserved source, not an assertion of the merged root. Family-based forget-by-ID,
forget-by-topic, conversation deletion and retention cascade through the existing
canonical family deletion path. Soft-deleted canonical rows also remove links.
Support counts decrease transactionally. **When the final source disappears, the
item itself is deleted**, so forgotten statements do not survive as stale orphans.
Normal writes therefore leave zero orphans. The schema reserves other statuses
for future workflows, but this slice does not promote, supersede or inject items.

Deferred: historical backfill, source-level attribution/automatic promotion,
entity canonicalization/linking, semantic deduplication, cross-project retrieval,
Chroma parity, human quality review UI, and all context injection.

## Verification

```bash
uv run python verification/memory/verify_memopad.py
uvx ruff check AgentCrew/modules/memory/{memopad,project_identity,memory_record_builder,sqlite_repository,sqlite_service,base_service}.py verification/memory/verify_memopad.py --statistics
```

The dedicated verification uses disposable fictional stores, local temporary Git
repositories and mocked LLM extraction. Existing memory harnesses additionally
cover schema/FTS integrity, migration, retrieval, lossless consolidation and
backend contracts. No private memory data or network service is needed.

### Initial implementation validation record (407606ab, 2026-11-19)

Commands ran from the dedicated feature worktree, reusing the original checkout's
installed environment without syncing dependencies:

```bash
export UV_PROJECT_ENVIRONMENT=../AgentCrew/.venv
uv run --no-sync python verification/memory/verify_memopad.py
uv run --no-sync python -m pytest \
  verification/memory/test_structured_extraction.py \
  verification/memory/test_extraction_reliability.py \
  verification/memory/test_resource_provenance.py \
  verification/memory/test_auxiliary_services_stats.py \
  verification/memory/test_auxiliary_model_selection.py -q
for name in verify_phase2 verify_phase5 verify_phase6 verify_tool_evidence \
  verify_fts_payload verify_exact_lookup verify_grounded_reconstruction \
  verify_startup_migration verify_semantic_candidates verify_migration_operator; do
  uv run --no-sync python verification/memory/$name.py
done
```

- Dedicated memopad verification: **19 checks passed**, including v1/v2 upgrades,
  upgrade rollback, Git/worktree identities, candidate validation, transaction
  rollback/failure isolation, source checksums, idempotence, scoping, deletion,
  lifecycle families, aggregate command routing and no injection changes.
- The ten existing standalone harnesses above passed. They use fictional data,
  local disposable databases and offline Chroma fixtures, not private memory.
- Targeted pytest: **232 passed, 1 failed**. The one failure is the pre-existing
  `test_stats_snapshot_codex_primary_and_memory`: it expects `gpt-5.4-mini` while
  the current catalog selects `gpt-6-luna`. The same failure was reproduced on the
  untouched starting commit `661df4b0b43be32f6cb10265c806161a43a1253c`; it was not
  corrected as part of this slice.
- Historical complete gates were also attempted: `verify_phase1.py` fails
  `Prototype import loaded optional/runtime services`, `verify_phase4.py` fails
  `Retrieval-critical file changed: scripts/benchmark_memory_retrieval.py`, and
  `verify_lossless_consolidation.py` fails `Slice 4 introduced freeze drift`.
  These same failures were reproduced on the starting commit. No freeze hashes
  were rewritten. Only explicit schema-version expectations in resource/lifecycle
  tests and the old-schema FTS comparison fixture were updated for schema 3.
- Phase 4's **10 behavioral checks** and lossless consolidation's **12 behavioral
  checks** passed independently, excluding only their historical freeze functions:

```bash
uv run --no-sync python - <<'PY'
from verification.memory import verify_phase4, verify_lossless_consolidation
for module, excluded in (
    (verify_phase4, "checks_policy_freeze"),
    (verify_lossless_consolidation, "checks_freeze_and_schema_contract"),
):
    for name, check in vars(module).items():
        if name.startswith("checks_") and name != excluded:
            check()
            print("PASS", name)
PY
```

Ruff lint (`uvx ruff check ... --statistics`) reports zero findings and Ruff
formatting checks pass for all **17 changed Python files**. AST syntax checks
pass for all 17; all **13 changed production modules** import successfully without
starting services. `git diff --check` passes. Protected context manager, memory
coordinator, memory worker and retrieval files have no diff from the starting
commit. All changed files were directly reread after editing.

No deprecated `tests/` suite, live provider quality evaluation, production-scale
performance benchmark or private database migration was run. Secret filtering is
heuristic, and retained proposals are candidates, not validated facts. A failed
projection has no automatic retry; recollection needs a subsequent explicit
memory-store action. Revision snapshots do not preserve historical source text.

### Architectural review correction: cross-version migration resume

Migration preflight now explicitly opts into read-only validation of supported
legacy schemas. Ordinary read-only repositories still require schema 3; normal
writable application startup still upgrades valid v1/v2 stores transactionally.
Legacy inspection does not upgrade the destination or promise current-schema
query capabilities. Future/unknown schema versions and incomplete layouts are
refused rather than repaired.

Both production and standalone migration paths check schema (including core table
column layouts), read-only SQLite/FK integrity and migration ownership/counts
before writes. They repeat these checks under the destination migration lock
before invoking the existing writable constructor. Only that locked constructor performs the v2→v3 upgrade. Failed
upgrade DDL rolls back, preserving the partial destination and its ledger for a
subsequent explicit resume. Memopad tables start empty: migration is not backfill.

The standalone snapshot also binds exact mapper/repository/parser code hashes.
Changing only schema preflight would therefore still reject a genuine old
checkpoint. Compatibility is deliberately limited to the audited code identities
from `661df4b0b43be32f6cb10265c806161a43a1253c` (schema 2) and
`407606aba878504c4c9cdc432585041fefb5df0d` (initial schema 3), whose canonical mapper
and parser are unchanged. These immutable code-hash tuples are allowlisted in the
migrator; no Git lookup is required at runtime. Unknown/modified code identities
remain refused, and the current parser hash must still match the predecessor's
parser. Compatibility requires explicit apply/resume and an existing destination.
Every other checkpoint field, including contract, source, collection,
destination, unexpected fields and the inspection fingerprint, remains checked.
Source/backup checks, ledger verification and no-overwrite semantics are retained.
The saved checkpoint is not rewritten to disguise its predecessor code identity.

The no-injection verification now compares committed `HEAD` against the actual
pre-memopad base commit for all four protected files: `context_manager.py`,
`memory_coordinator.py`, `retrieval.py` and `memory_worker.py`. It separately checks
pending changes and verifies no memopad references exist in any of those files.
A negative test injects each protected path into the committed-diff result and
confirms the guard fails, even when the working tree would otherwise be clean.
It does not pin checksums of mutable working-tree bytes.

Correction validation commands (same environment export as above):

```bash
uv run --no-sync python verification/memory/verify_memopad.py
uv run --no-sync python -m pytest verification/memory/test_migration_schema_resume.py -q
uv run --no-sync python -m pytest \
  verification/memory/test_migration_schema_resume.py \
  verification/memory/test_resource_provenance.py \
  verification/memory/test_structured_extraction.py \
  verification/memory/test_extraction_reliability.py -q
for name in verify_phase2 verify_phase5 verify_phase6 verify_migration_operator \
  verify_startup_migration verify_fts_payload; do
  uv run --no-sync python verification/memory/$name.py
done
uvx ruff check AgentCrew/modules/memory/{sqlite_repository,engine_selection,migrate_chroma_memory_to_sqlite}.py \
  verification/memory/{verify_memopad,test_migration_schema_resume}.py --statistics
uvx ruff format --check AgentCrew/modules/memory/{sqlite_repository,engine_selection,migrate_chroma_memory_to_sqlite}.py \
  verification/memory/{verify_memopad,test_migration_schema_resume}.py
```

- Dedicated memopad verification: **20 passed**.
- Cross-release regression suite: **35 passed**, including counterfactual failures
  on immutable initial-feature code, real committed schema-2/3 partial writers,
  both migration paths, locked upgrades, read-only non-mutation, repeat resume,
  preserved canonical/ledger rows, empty memopad, malformed/future rejection,
  failed-upgrade rollback, checkpoint tampering and backup/inspection validation.
  SDK access is replaced with a fictional collection; the existing phase 2/6
  harnesses additionally exercise real disposable offline Chroma stores.
- Combined migration/resource/extraction pytest invocation: **142 passed**.
- All six standalone harnesses in the loop above passed.
- Historical Phase 4 and consolidation behavioral checks, using the previously
  documented runner, again passed **10 + 12** checks with only historical freeze
  functions excluded.
- Complete `verify_phase1.py`, `verify_phase4.py`, and
  `verify_lossless_consolidation.py` were attempted and retain the same three
  pre-existing import/freeze failures listed in the initial validation record.
  Freeze hashes were not updated or silently exempted. The unrelated auxiliary
  provider-model snapshot was not rerun as part of this focused correction.
- Ruff lint reports zero findings and formatting passes for all **five changed
  Python files**. AST syntax checks and imports pass; all seven changed files
  were reread. No deprecated `tests/`, live providers, private databases or
  production-scale benchmarks were used.

Scope remains collection-only. Nonblocking limitations are unchanged: project
stats show opaque keys rather than friendly name/key pairs; failed projections
have no persisted counter/retry; revision checksums do not preserve old text;
optional same-call proposals may affect live extraction length/quality; secret
detection is heuristic; Chroma does not collect memopad. No promotion, retrieval
integration, historical backfill or context injection was introduced.

References: [SQLite foreign keys/cascades](https://www.sqlite.org/foreignkeys.html),
[Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/),
[Git rev-parse](https://git-scm.com/docs/git-rev-parse).
