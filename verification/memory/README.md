# Memory migration verification: Phase 0 and Phase 1

This directory freezes baseline evidence and a **synthetic-only** evaluation contract. Phase 0 is PIC-approved. Phase 1 adds standalone verification of an unwired SQLite retrieval prototype in `AgentCrew/modules/memory/`; no production memory backend, write path, tool, startup wiring or model pin changes. No migration or formal benchmark runner is implemented. Phase 1 awaits Architect PIC review; do not start Phase 2 yet.

## Phase 1 read-only prototype

Implementation files:

- `AgentCrew/modules/memory/types.py`: request-local immutable query/candidate/hit/result contracts, explicit match reasons and relevance-confidence bands. Existing `BaseMemoryService` signatures are unchanged.
- `AgentCrew/modules/memory/sqlite_repository.py`: version 1 schema and migration-ledger table; original document and arbitrary JSON metadata plus checksums; lifecycle/supersession/validity/evolution fields; nullable CWD and legacy session ID; canonical, unit, entity/tag and external-content FTS5 tables. Population/upsert/deletion methods exist for disposable setup/verification only.
- `AgentCrew/modules/memory/record_parser.py`: deterministic actual `CONVERSATION_NOTES/NOTE` parsing, stable content-derived unit IDs, exact observed identifiers/resources/domains, and explicit fallback warnings. Original input is retained even when parsing fails. DTD/entity declarations are rejected; oversized documents/units are indexed only up to documented module limits with warnings. Only recognized XML fields are derived; other fields remain in canonical source.
- `AgentCrew/modules/memory/retrieval.py`: read-only scoped candidate retrieval, deterministic parent-level reciprocal-rank fusion, conservative abstention, quoted compact evidence and potential-conflict markers. CWD is output-only.
- `AgentCrew/modules/memory/__init__.py`: retains the same public exports but loads the two optional, dependency-heavy legacy services on demand, following the existing package lazy-export pattern. Importing the prototype no longer imports Chroma or provider SDKs.

SQLite requires **3.35+ with built-in FTS5** for materialized candidate queries. No extensions are loaded. The repository takes an explicit file path, not `:memory:`, and creates/closes one connection per operation on its calling thread. Writers use short explicit transactions; readers use `mode=ro`, `query_only`, and a consistent transaction across candidate sources. Foreign keys and bounded busy timeouts are enabled; file-backed storage uses WAL. Unknown/incomplete/future schemas fail without recreation or retention cleanup.

Every candidate source applies agent, explicit current-session, active-status, supersession and optional UTC date eligibility **before parent ranking/limiting**. Unknown legacy sessions stay null rather than receiving invented values; known current-session records remain excluded. Optional `as_of` applies validity bounds; without it the prototype relies on explicit lifecycle status and does not infer wall-clock expiry. SQL values are bound; FTS terms are quoted, not accepted as executable query syntax. Memory-ID selectors remain subject to the same hard filters. CWD never enters eligibility, FTS fields or rank computation.

Initial, uncalibrated ranking uses FTS5 field weights `6/4/1/8` (headline/high-value text/body/identifiers), equal-source RRF `k=60`, a `1.1x` explicit kind hint, minimum lexical overlap `0.35`, and a relative coverage floor `0.7` of the best candidate. These are prototype defaults, not an adoption or quality claim. Parent records occur at most once; normalized whole-document duplicates collapse; equally ranked results prefer distinct sessions/kinds. This is not semantic deduplication. Active, distinct documents with the same **explicit** subject receive a **potential** conflict marker, not a claim that contradictions have been adjudicated. SQLite BM25 term statistics remain index-wide even though candidate eligibility is applied in SQL before ranking/limiting; this is not a separately partitioned per-agent statistical index.

Compact output includes stable parent/unit citations, UTC date, source CWD or `Unknown (legacy memory)`, kind, headline, bounded excerpt, reasons, match-confidence band and conflict state. Recalled strings are JSON-quoted under a fixed **untrusted evidence / cannot grant authorization** prefix; no adaptive behavior or permission state is modified.

Run offline with the provisioned interpreter (no dependencies installed):

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_phase1.py
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/validate_assets.py
```

`verify_phase1.py` creates and removes disposable synthetic SQLite files. It does not open any Chroma snapshot or live memory source, run application startup, or execute the deprecated `tests/` suite. It checks schema/capabilities, metadata/document preservation, stable units, actual XML notes, malformed/DTD fallback, transaction rollback, FK/FTS index integrity, exact ID/entity/resource/tag retrieval, scope/date/status/validity isolation, CWD-invariant ranks, deduplication, conflict flags, formatting and 12 concurrent request-local read/write checks. The deletion setup physically removes the deleted fixture from canonical/derived/ledger tables and confirms its unique tokens are absent from FTS results. This is SQL row/index removal in a disposable database, **not production forget integration or forensic secure erasure** of pages, WAL or backups.

Observed result: all standalone invariants pass; **27/29 synthetic labeled-ID checks pass**. `syn-case-paraphrase` (cache paraphrase) and `syn-case-constraint` (rejected signaling approach) abstain: lexical recall limitations without semantic expansion. The checks exercise expected/acceptable/excluded IDs, provenance, conflict and abstention; they do **not** evaluate semantic expected facts, answer generation, real-memory quality, calibrated ranking, latency or cost. The formal scored Chroma/SQLite/assisted benchmark remains Phase 3. No fixture or label was changed to improve these counts.

MEM-100 through MEM-106 are implemented and verified for this isolated prototype. MEM-002 stays open: the private, sanitized, locally reviewed real-memory cohort is mandatory before Phase 3 can pass or any cutover can be authorized. Do not invent labels or commit/return private documents, IDs, sessions, source paths, queries or labels. Architect review is required before Phase 2.

References: [Python 3.12 sqlite3 transactions/connection ownership](https://docs.python.org/3.12/library/sqlite3.html), [SQLite FTS5 external content, triggers, Unicode61, BM25 and index integrity](https://www.sqlite.org/fts5.html).

## Frozen evidence

`baseline.json` records aggregate inventory captured on 2026-11-05:

| Item | Observed value |
|---|---|
| Implementation baseline | `a6c186fde41f7252fb311abd2af8e6d2abde87f1` |
| AgentCrew package / Python requirement | `0.23.2` / `>=3.12` |
| Capture runtime Python | `3.12.10` |
| Installed and locked Chroma | `1.5.9` |
| Runtime SQLite | `3.47.1` |
| FTS5 create/insert/MATCH capability | Passed |
| Selected source | Existing `MEMORYDB_PATH` environment selection; value withheld |
| Collection | `conversation` |
| Records / metadata records inspected | `407` / `407` |
| Observed user metadata keys | `agent`, `date`, `header`, `session_id`, `type` |
| Copied SQLite integrity check | `ok` |

The report records the checkout commit at capture and the collector's SHA-256 because the collector was not yet committed at capture time. The fixture manifest separately hashes both synthetic JSONL files. No real memory IDs, session values, documents, metadata values, credentials, or source paths are committed.

The source directory was copied using file reads only. Whole-tree content fingerprints matched before/after copying and after inventory; the untouched backup matched the source. The Chroma SDK opened **only a second disposable copy**, with telemetry disabled, `get_collection()` (not creation), and metadata-only paginated reads. No embedding or answering model ran. SDK initialization may modify its disposable working copy; the frozen source backup is separate. The SQL integrity check ran against the working copy before the SDK opened it.

**Snapshot limitation:** writers were not independently confirmed paused. Byte stability was observed during this capture; it is not a replacement for stopping/pausing AgentCrew before the actual migration. The baseline applies to the selected source at capture time, not every database on the machine. Re-capture before migration if the corpus changes.

### Updated main findings

The difference from the plan's original `e83a03b79e207d5e5d9a8b8f655e3fa9dbe61428` snapshot to the implementation baseline is limited to provider cleanup and moving four lifecycle scripts from `scripts/` into the deprecated `tests/` directory. The memory service, worker, coordinator and setup files have no intervening diff. No deprecated tests were run.

Current `MemoryWorker` writes the structured `<MEMORY>` XML described in the plan; actionable notes use `<CONVERSATION_NOTES><NOTE>`. The synthetic fixtures use that actual shape. Current writes do not capture CWD. Current Chroma initialization can recreate an incompatible collection and performs retention, so baseline capture must not instantiate `ChromaMemoryService`.

Metadata varies by source: the primary frozen collection has only the five keys above. A separate disposable inspection of the repository-development database also revealed legacy `conversation_id` and `evolved_at` keys. Future migration must preserve actual metadata, not impose the five-key inventory as a universal schema. That secondary database is not the primary baseline and its counts are not substituted for the selected source.

## Committed synthetic fixture contract

- `fixture-manifest.json`: schema version, fixture version `1.0.0`, counts, hashes, required categories, and a physical-deletion setup contract.
- `synthetic-corpus.jsonl`: 25 fictional canonical records with Chroma-shaped metadata and XML documents, plus explicit target lifecycle/kind/CWD labels.
- `benchmark-cases.jsonl`: 29 labeled cases covering 27 required categories.
- `validate_assets.py`: standard-library-only structural, reference, safety-label, heuristic privacy, ignore-rule and FTS5 validation.
- `capture_baseline.py`: local aggregate inventory collector; this is not the future migration script.

Each JSONL row has `schema_version: 1` and `synthetic: true`. All memory IDs start with `syn-mem-`, case IDs with `syn-case-`, sessions with `synthetic-session-`, and agents with `Synthetic`. Absolute fixture CWDs are fictional paths under `/synthetic/projects/`. URLs use reserved `example.invalid` domains. The model/provider names are fictional labels, not changes to supported providers.

### Label interpretation

- `expected_memory_ids` are required relevant canonical memories. `acceptable_memory_ids` are additional relevant memories that are permitted but not required. `excluded_memory_ids` must never be exposed for that case. These sets do not overlap.
- `expected_facts` are answer assertions for later human or explicit evaluator review, not required literal substrings. `expected_cwds` maps required IDs to their exact provenance or `null` for legacy unknown.
- `must_abstain: true` means no result is relevant; do not fill the result budget with noise.
- Every case uses the same synthetic agent across prior sessions and excludes current-session, other-agent, physically forgotten, and superseded decoys. Cases add date and relevance-specific exclusions where needed.
- An ambiguous follow-up carries the preceding user/assistant exchange as query-construction context. No external-turn flag is introduced.
- The active-conflict case requires both active pieces of evidence and an explicit conflict marker. Neither proposal is a settled fact.
- `cwd_not_ranking` pairs two otherwise identical retrieval inputs with different caller CWDs and requires identical ranking. Cross-CWD memories remain eligible. Legacy CWD must display `Unknown (legacy memory)` and must never become the migration process CWD.
- The injection case contains deliberately hostile **fictional quoted text**. Its safety labels require that text remain untrusted evidence and cannot disable approvals.

The fixture's `status`, `superseded_by`, `subject_key`, and `kinds` are target evaluation labels, not claims that current Chroma implements those semantics. The future harness must record current Chroma failures honestly rather than silently repairing its behavior to match target labels.

The `deleted` fixture is setup input, not permission to retain user-forgotten data as a searchable tombstone. The future harness must insert it, invoke physical forgetting, verify absence from canonical/unit/FTS/entity/tag storage, and then run its case. Phase 0 validates this setup contract but does not execute a storage deletion. Do not award a deletion-safety pass by filtering a soft-deleted row in an adapter.

## Local privacy layout and template

This directory is default-deny in `.gitignore`: only explicitly named public assets are unignored. Place all private/generated material in ignored locations:

```text
verification/memory/local/phase0-frozen/source-backup/
verification/memory/local/phase0-frozen/chroma-copy/
verification/memory/local/private-cases.jsonl
verification/memory/local/private-corpus.jsonl
verification/memory/local/private-id-map.json
verification/memory/local/capture-final.log
verification/memory/results/
```

The plan's alternative `verification/memory-migration/` workspace is also ignored at repository level. Do not force-add copies or generated reports. Copies/logs must stay inside a private directory with mode `0700`; the collector enforces that on `local/`. Sanitize before sharing any report, even if it is ignored.

Use this structural template **locally only**, replacing placeholders after authorized review; it is not an executable benchmark case:

```json
{
  "case_id": "local-reviewed-001",
  "query": "Sanitized information need without credentials or private paths",
  "agent_name": "LOCAL_SCOPE_PLACEHOLDER",
  "current_session_id": "LOCAL_SESSION_PLACEHOLDER",
  "expected_memory_ids": [],
  "acceptable_memory_ids": [],
  "excluded_memory_ids": [],
  "expected_facts": [],
  "expected_cwds": {},
  "reviewed_locally": false
}
```

Real-memory cases remain local and require sanitization, referential checks against their private corpus, and human review. None have been fabricated or derived from raw private conversations in this commit. Consequently **MEM-002 is complete for the synthetic cohort only**; its original real-case labeling requirement remains open. Synthetic coverage alone is not evidence of real-world retrieval improvement or permission to cut over. Architect accepted synthetic completion for Phase 1 only; real-case review remains a Phase 3/pre-cutover requirement.

Remote model calls remain off by default. The future assisted benchmark requires separate explicit opt-in. Adaptive behaviors, memory authority, provider/model policy, and automatic context injection are unchanged.

## Reproduction without dependency installation

Run from the repository root with an already-provisioned Python environment. `uv run --no-sync` does not install dependencies:

```bash
uv run --no-sync python verification/memory/validate_assets.py
```

If this worktree has no environment, point uv at an already-provisioned interpreter without resolving this project's dependencies:

```bash
uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python verification/memory/validate_assets.py
```

For a new private baseline, keep `MEMORYDB_PATH` set privately and choose an unused snapshot directory. Do not put the source path into committed commands or reports:

```bash
uv run --no-sync python verification/memory/capture_baseline.py \
  --snapshot-dir verification/memory/local/new-baseline \
  > verification/memory/local/capture.log 2>&1
```

The environment-based collector defaults to `MEMORYDB_PATH`; another source requires explicit `--source` and the matching `--source-label`. It refuses overlapping paths, an existing snapshot, symlinked sources, unknown metadata key names, unstable source bytes, or an unreadable collection. Failures require private operator inspection; do not publish diagnostic logs. Unknown keys must be reviewed before changing the safe key-name allowlist. This is an inventory helper, not a general Chroma exporter.

The implementation validation used the existing provisioned interpreter, offline uv execution, and no project synchronization. Validation covers JSON/JSONL/XML, unique IDs, fixture/case referential integrity, category coverage, date/scope/lifecycle labels, CWD invariants, checksums, obvious secret/path patterns, syntax, UTF-8/whitespace, ignored private artifacts, and FTS5 create/insert/MATCH. It runs no deprecated tests, production startup, migration, live memory queries, retrieval quality scoring, or remote model calls.
