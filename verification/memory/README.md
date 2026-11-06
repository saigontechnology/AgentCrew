# Memory migration verification: Phases 0–3

Phases 0–2 are PIC-approved. Phase 3 tooling and synthetic verification are implemented, but **Phase 3 is BLOCKED, not passed**: private human review, a configured Chroma relevance baseline, calibration and real holdout remain unperformed. **MEM-002 stays open; do not start Phase 4 or cut over.** No production setup, search tool, write path, automatic injection, provider or model pin has changed.

## Phase 1 read-only prototype

Implementation files:

- `AgentCrew/modules/memory/types.py`: request-local immutable query/candidate/hit/result contracts, explicit match reasons and relevance-confidence bands. Existing `BaseMemoryService` signatures are unchanged.
- `AgentCrew/modules/memory/sqlite_repository.py`: version 1 schema and migration-ledger table; original document and arbitrary JSON metadata plus checksums; lifecycle/supersession/validity/evolution fields; nullable CWD and legacy session ID; canonical, unit, entity/tag and external-content FTS5 tables. Population/upsert/deletion began as disposable Phase 1 setup APIs; Phase 2 shares preparation/writes for explicit offline migration, not automatic production memory storage.
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

MEM-100 through MEM-106 are implemented and verified for this isolated prototype. MEM-002 stays open: the private, sanitized, locally reviewed real-memory cohort is mandatory before Phase 3 can pass or any cutover can be authorized. Do not invent labels or commit/return private documents, IDs, sessions, source paths, queries or labels. Architect approved Phase 1; the Phase 2 review gate below is pending.

References: [Python 3.12 sqlite3 transactions/connection ownership](https://docs.python.org/3.12/library/sqlite3.html), [SQLite FTS5 external content, triggers, Unicode61, BM25 and index integrity](https://www.sqlite.org/fts5.html).

## Phase 2 offline migration

`scripts/migrate_chroma_memory_to_sqlite.py` is a checkout-local operator tool, not a runtime backend switch. It reuses the Phase 0 fingerprint helper and Phase 1 parser/repository. The repository now shares canonical/index preparation between normal setup and **atomic migration batches**, and checks the existing `migration_ledger` before resuming. Schema version stays **1**; there is no automatic schema upgrade or destructive repair.

### One-command operator entry point

**You do not need an existing SQLite source.** Your input is the existing **Chroma persistence directory** containing `chroma.sqlite3`. `scripts/migrate_memory.py` stages it, runs the existing migration inspection, and—only with `--apply`—returns a newly created, verified SQLite candidate as `sqlite_source`.

Stop AgentCrew and every process writing its memory first. `--writers-paused` is your attestation; the script cannot detect that writers are stopped. With the existing provisioned interpreter, run from this implementation checkout (the example assumes the original `AgentCrew` checkout is its sibling):

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python scripts/migrate_memory.py --source ../AgentCrew/memory_db --writers-paused --apply --prepare-review
```

Replace the source with your actual Chroma directory. Source paths are resolved against your shell's working directory; output locations are anchored to this implementation worktree. The entry point is Python, not a Bash alias, so the invocation is also usable from fish and other shells with an appropriate interpreter path. No dependencies are installed by the command.

- Omit `--apply --prepare-review` (or use `--inspect-only`) to inspect without creating any SQLite destination. Inspection still stages private copies and writes reports. `sqlite_source` is null until a verified apply succeeds.
- If `--source` is omitted, an existing `MEMORYDB_PATH` is accepted only when a different valid `./memory_db` is not also present. Otherwise a valid `./memory_db` is the default. Conflicting candidates, an empty environment value, or a missing default require an explicit source; there is no silent fallback to another database.
- All runs use a fresh ignored `verification/memory-migration/<run-id>/` with `staged-source/`, `candidate.sqlite3`, `migration/`, `operator.json` and private diagnostics. `--run-id` optionally selects a fresh label (letters/digits/underscore/hyphen, at most 64 characters). Existing labels, symlinks, tracked source paths, overlap and unsafe output locations are refused. Directories are `0700`; manifests/reports are `0600`.
- Source-tree validation and stable copying are shared with the low-level tool. Only the staged source is passed to its inspection and apply/resume/verify APIs. The live source is never opened with Chroma SDK; original and staged bytes are checked again before completion. No retention, deletion, embeddings or LLM calls run.
- `--collection` defaults to `conversation`; `--batch-size` is `1..1000` (default 100); `--fail-on-parse-error` delegates the existing strict warning/fallback policy. `--prepare-review` requires `--apply` and delegates the existing draft generator only after verified migration. It requires at least 14 eligible records; a review-stage error leaves the verified candidate and reports intact. Omit review preparation if you only need migration.
- Successful local JSON names `sqlite_source` and, when requested, `review_directory`, alongside counts and `review_status: unreviewed_proposals`. These paths are for your local use; do not publish private files. No memory text, record IDs, agents, sessions, queries, labels or credentials are printed. `reviewed_locally` remains false; no human attestation is created.
- Errors contain only `status`, the failing `stage`, and an exact static `reason`, for example `writer_attestation / writers_paused_attestation_required` or `source_resolution / ambiguous_source_requires_explicit_source`. Unexpected errors expose only their class name. Detailed tracebacks remain in private `operator.log`.

The wrapper is deliberately fresh-run-only, not a second resume implementation. After interruption, keep all artifacts and recover through the existing low-level command against the run's unchanged staged source and work directory:

```bash
uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python scripts/migrate_chroma_memory_to_sqlite.py --source "$RUN/staged-source" --destination "$RUN/candidate.sqlite3" --work-dir "$RUN/migration" --apply --resume --verify
```

`RUN` is the local run directory you selected/generated. Do not re-run the wrapper with that label or delete backups to bypass guards. Checkpoints are code-bound: resume with the exact matching tool version; shared-helper extraction does not silently upgrade checkpoints from earlier commits. A successful recovery can use the returned candidate with the existing `prepare-review` command and a fresh review output.

Standalone fictional-only verification:

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python verification/memory/verify_migration_operator.py
```

The verifier covers inspect-only non-writing, 25-record verified apply, 32 draft-only review proposals, required pause attestation, default ambiguity, strict parsing, static error stages, safe/fresh/ignored paths, symlinks, Git tracking, source stability, and an interrupted batch followed by underlying resume (five skipped, 20 inserted). No existing private draft or live source is used. Production stays on Chroma; Phase 3's private gates and Phase 4 remain blocked.

### Low-level operational contract

1. Stop/pause AgentCrew memory writers **operationally**. The script cannot establish this; reports always say `writers_confirmed_paused: false`. A byte-stable snapshot is evidence, not proof of a paused application or a fresh live-data baseline.
2. Supply an existing, authorized **private source copy**. Source, destination and work directory must be mutually non-overlapping paths under `verification/memory/local/` or `verification/memory-migration/`, and Git must confirm they are ignored and untracked. Live-source paths outside these roots are rejected. Source trees and path ancestors may not be symlinks. Use an unused work directory and destination for a new run.
3. Generated private directory modes are `0700`, reports/logs `0600`; do not force-add them. POSIX workspace/destination locks prevent concurrent operator runs and release on process exit. Windows locking code exists but has not been validated on Windows; mode bits alone are not an NTFS ACL guarantee.
4. Default dry-run copies to a frozen `source-backup/`, then a second `chroma-copy/`. Only the latter is opened through Chroma `get_collection(..., embedding_function=None)`, with telemetry disabled. No `ChromaMemoryService`, get-or-create, embedding/answering model, source SDK connection, retention or source deletion is used. No embeddings enter SQLite.
5. Source bytes are fingerprinted before/after copying and after processing; backup equality is checked. Snapshot identity binds file bytes, collection, destination token, schema/mapping version and tool/repository/parser hashes. After preflight, logical source/canonical roots also bind the SDK working copy: resuming a tampered copy fails before writes. Tool/parser/repository changes require a fresh run, not a forced checkpoint reuse.
6. Inspection writes **no destination schema/data and does not open even an existing destination**. Copies/reports in the explicit work directory are expected inspection outputs. `--apply` is the only write authorization. `--verify` requires `--apply`. Reusing a work directory or an existing compatible destination requires `--resume`; unrelated, future/incomplete or unledgered destinations are refused. There is no replacement/force option.
7. Preserve original IDs, exact document text, and arbitrary actual metadata as JSON plus checksums. Required nonempty agent and finite calendar-supported numeric `date` must be mappable to schema 1; missing/non-text documents, missing required metadata or invalid fields **block the whole preflight**, rather than inventing values or silently skipping records. Source `date` supplies both canonical creation/update projection fields; no migration clock is substituted. Missing optional session/header/evolution fields stay NULL. Blank legacy session strings remain verbatim, distinct from NULL; request-local current-session query validation remains nonempty. Numeric evolution values have a TEXT projection while their original types remain exact in metadata JSON.
8. All historical canonical CWDs stay NULL, even if arbitrary raw metadata happens to contain a `cwd` key; that metadata is retained unchanged. Do not infer project, subject, kinds or supersession links. An explicit supported `status` is preserved; absent lifecycle metadata defaults to active. Known unsupported lifecycle aliases, or conflicting active/supersession signals, block instead of silently reactivating data.
9. Parser warnings/fallbacks are counted, with original canonical records retained. `--fail-on-parse-error` is deliberately strict: **any parser warning**, including truncation, prevents destination writes during preflight; it also refuses a permissive snapshot in strict resume mode. No unsupported or malformed document is silently dropped.
10. Each bounded batch commits canonical rows, derived units/entities/tags/FTS and ledger rows in one SQLite transaction. Resume skips only records whose source hash, all canonical projections, stored checksums, parser result, derived rows and ledger ownership match. Mismatches fail closed, never overwrite completed records. Prior committed batches remain intact after interruption or later failure; a crash during initial schema creation may require operator review and a fresh destination rather than automatic repair.

### Commands and results

With already-provisioned dependencies and a staged private source, use separate sibling paths (not a destination inside the source):

```bash
SOURCE_COPY=verification/memory/local/staged-source
DESTINATION=verification/memory/local/migrated.sqlite3
WORK=verification/memory/local/migration-run

uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python scripts/migrate_chroma_memory_to_sqlite.py \
  --source "$SOURCE_COPY" --destination "$DESTINATION" --work-dir "$WORK" --report "$WORK/inspection.json"

uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python scripts/migrate_chroma_memory_to_sqlite.py \
  --source "$SOURCE_COPY" --destination "$DESTINATION" --work-dir "$WORK" --report "$WORK/apply.json" \
  --apply --resume --verify --batch-size 100
```

Other defined options: `--collection` (default `conversation`, default Chroma tenant/database), explicit `--dry-run`, `--resume`, `--verify`, `--fail-on-parse-error`, `--batch-size` (`1..1000`), and `--report` (a file directly in the owned work directory; default `report.json`). SDK diagnostics and tracebacks go to private `sdk.log`; stdout/error summaries contain only static error codes, hashes and aggregate counts. Do not publish diagnostic logs or source/destination files. The current pinned SDK is stopped using a public close method when available, otherwise its system-stop lifecycle; this fallback is version-sensitive.

Reports contain package/Python/SQLite/Chroma versions, checkout commit/dirty-state plus code hashes, source tree/collection and canonical roots, destination-file fingerprint, counts/warnings and verification state—no raw IDs, documents, metadata values or original source paths. The private snapshot manifest additionally retains file checksums. Physical SQLite fingerprints may vary on re-run; canonical roots and matching per-record ledger checks establish logical equality. Verification checks every source ID, all canonical fields and metadata/checksums, parser/derived ownership, active units, NULL legacy CWD, global source/canonical/ledger counts, SQLite/FK checks and the **actual FTS index** (`integrity-check` against external content), not just the FTS virtual-table row count.

Standalone verification (existing environment; **do not use `-S` here**, because installed Chroma/version metadata is needed):

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python verification/memory/verify_phase2.py
```

It builds disposable synthetic Chroma collections with explicitly supplied dummy input embeddings, never a downloaded embedding model. Results: six source records preserved; six verified on apply; repeat resume skipped all six without duplicated rows/indexes; interruption committed two, then restart skipped two and inserted four; one malformed record kept its fallback. Additional checks cover missing optional and blank legacy session preservation, arbitrary actual metadata, strict mode, destination/source/ledger tampering, frozen/working copies, unsupported lifecycle rejection, unknown required metadata, unrelated/future SQLite schemas, whole-batch rollback, concurrent workspace locks/report ownership, safe/ignored paths, symlinks, and simulated unstable copying. Fixtures and generated results stay private/ignored; all inputs are fictional.

Permitted retained-copy aggregate validation also passed: **407 source records → 407 canonical records and 407 ledger rows; 407 independently verified; zero unmappable records; one unsupported-XML fallback preserved; 10,937 units and FTS document-size rows, 31,010 exact-entity rows and 1,497 tag rows; zero foreign-ledger, non-NULL CWD or unitless-active rows**. A repeated verified resume skipped all 407 records, inserted zero, and retained identical canonical roots and index counts. This is the existing Phase 0 frozen copy, not a new claim about the live source. The first inspection correctly blocked blank historical session labels before destination writes; the repository's type-valid legacy-session preservation was corrected and regression-tested, then a fresh private run preserved all records. Failed artifacts are retained rather than silently repurposed.

Rollback remains non-destructive: keep the unchanged original staged source, untouched frozen backup, disposable SDK copy, SQLite candidate, manifest, reports/logs and ledger/checksum evidence. No application was switched to SQLite, so the existing production Chroma path remains unchanged. Do not remove Chroma or copy a candidate over it. Actual cutover/rollback procedures require later phase approval and a fresh paused-writer snapshot. SQL row deletion is not forensic erasure of WAL, pages or backups.

MEM-200 through MEM-204 are verified and PIC-approved for this offline tool. **MEM-002 remains open until a private, sanitized, locally reviewed real-memory benchmark cohort is evaluated before Phase 3 can pass or cutover can be authorized.** Reading a retained copy for preservation is not that review and does not create or score real-case queries/labels. No formal benchmark, model-assisted retrieval, application startup, deprecated tests, production wiring, `search_memory` change or automatic injection was performed.

## Phase 3 tooling — private gate blocked

Files:

- `AgentCrew/modules/memory/benchmark.py`: strict JSON/JSONL schemas, scope/reference/review validation, metrics, bounded adapters and answer-evaluation hooks. Not wired into production search.
- `scripts/benchmark_memory_retrieval.py`: explicit CLI, isolated Chroma/readonly SQLite backends, reports, private draft preparation, calibration freeze and persistent holdout guard.
- `verification/memory/verify_phase3.py`: fictional-data checks, fake HTTP (no network), source-policy cap/threshold checks without lifecycle repair, and single-use/freeze verification.
- `phase3-synthetic-summary.json`: aggregate **synthetic-only** sample. Hash embeddings simulate a contract, not a configured Chroma effectiveness baseline.

### Reproduction

Use the provisioned interpreter offline; install nothing and do not run deprecated tests:

```sh
uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python verification/memory/verify_phase3.py

uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python scripts/benchmark_memory_retrieval.py run \
  --corpus verification/memory/synthetic-corpus.jsonl \
  --cases verification/memory/benchmark-cases.jsonl \
  --sqlite-source "$SYNTHETIC_SQLITE" --chroma-source "$SYNTHETIC_CHROMA" \
  --output "$UNUSED_PRIVATE_OUTPUT" --modes chroma,sqlite,sqlite-expand,sqlite-rerank \
  --split synthetic --embedding-source synthetic-hash --adapter fake --answer-eval \
  --k 5 --warmups 1 --repetitions 3 --model-call-budget 1000
```

Synthetic sources must implement the committed physical-deletion fixture. The verifier retains matching fictional sources privately. All generated inputs/outputs are ignored, untracked, nonsymlinked private paths; public exceptions are the exact committed synthetic fixtures. Outputs must be unused and non-overlapping. JSON, Markdown, failure JSONL, logs, drafts and ledger markers stay private (0700 workspace / 0600 files); never force-add them.

### Metrics and fidelity

Reports include Recall/Precision@1/3/5/k, MRR, graded nDCG, expected/acceptable coverage, abstention, duplicates, excluded/scope/lifecycle/deletion/unknown-ID leaks, conflict/CWD/provenance, payload size and category aggregates. Precision uses the fixed cutoff denominator; recall uses required IDs. Expected/acceptable default grades are 2/1. Duplicates gain no extra ranking credit; no-positive cases are excluded from recall/MRR means and assessed separately for abstention.

Cold and post-warmup p50/p95, repeat stability, observable busy/lock events and assisted retrieval invocation rates are separate. Model/embedding counters count function invocations, not unobservable transport retries; fake adapters are not models. Tokens are observed subtotals with coverage counts; unknown tokens/costs remain null. Payload size uses normalized hit JSON with full Chroma documents, not byte-identical service XML wrapping. Follow-up context metadata is retained but the query is passed unchanged to both retrieval backends.

Chroma uses a frozen backup and a disposable SDK copy, telemetry disabled and no `ChromaMemoryService`. It mirrors source `n_results=10`, top-three cap, threshold and agent/session/date predicates; unsupported kind/lifecycle semantics are not repaired to labels. Stored document vectors are never query embeddings. Independently generated caches require provenance/configuration/source checks; configured local models must already be cached, or sanitized reviewed queries require explicit `--allow-remote-embeddings`. Do not switch provider/model merely because another model is cached.

Expansion and reranking have structured-output validation, budgets, candidate-ID allowlists and deterministic fallback. Reranking cannot add candidates or override eligibility/deletion. Answer evaluation uses the same bounded prompt/provider/model contract across backends, citation hooks and literal-fact/forbidden-claim proxies; semantic correctness stays null without an explicit local evaluator. Remote LLM options require explicit HTTPS/provider/model/key-env/budget and `--allow-remote-llm`, and are **forbidden for private evidence even with that flag**. Application pins never change. This execution made no remote calls.

### Required private review and untouched holdout

`prepare-review --sqlite-source "$RETAINED_CANDIDATE" --output "$UNUSED_PRIVATE_REVIEW"` creates proposals, not ground truth. The private draft contains **407 records, 32 proposed cases (16 calibration / 16 holdout), 14 candidate memories and four absent-identifier proposals**. Human/PIC review must establish genuine categories, paraphrases, labels, sanitization and any multilingual coverage. No private IDs, agents, sessions, paths, queries, labels, content or maps are committed.

Every real case must explicitly cease being a draft, be reviewed locally, and match a separate `local-human` attestation bound to corpus/case hashes. The loaded dataset retains the exact SHA-256 of the validated attestation bytes. Until review is complete the loader rejects the cohort **before backend execution**. No review is auto-attested. Reading a retained copy for migration is not relevance evidence. Current private calibration/holdout metrics, configured Chroma comparison and 407-record p95 are **null / not run**.

After genuine calibration, `freeze --calibration-report ... --output ...` seals code, policy, dataset/backend fingerprints, embedding identity and k/warmup/repeat protocol. Holdout requires unchanged inputs, both baselines and a matching freeze. Its locked, persistent ledger consumes each normalized held-out question/scope input—not freeze timestamps or labels. Renaming cases, changing CWD, relabeling, regenerating a freeze, or partial question recycling cannot unlock consumed inputs. Markers are consumed before execution; corrections require a fresh holdout or invalidation, never erasing history. Hashes do not prove semantic novelty or honest review, and this local guard cannot prevent a malicious owner moving/deleting the workspace.

Coverage is required **within each calibration and holdout split**, not just across the cohort: at least 15 cases, five distinct positively labeled memory IDs (expected or acceptable), and two abstention cases. Each split must cover `exact_identifier`, `paraphrase`, `no_relevant_memory`, and `ambiguous_followup`; at least one of `decision`/`preference`; and at least one of `constraint_rejected_approach`/`unresolved`. Existing overall coverage and distinct normalized-question requirements still apply. These checks do not edit or attest the private draft.

Reports, calibration freezes and holdout checks bind `review_manifest_sha256` and `embedding_cache_sha256` to exact input bytes. The cache hash is explicitly null for non-cache sources, and synthetic runs have no review-attestation hash. Altered attestations and mutated or replaced caches are rejected before backend setup or holdout reservation. The cache rows used by the backend come from the checksum-validated snapshot, not a later reread of the mutable input file.

### Current result and remaining gates

Offline synthetic safety checks pass. SQLite Recall@5 is **0.9259**, consistent with 27/29 labeled-ID checks and honest lexical abstentions. The hash-vector Chroma score is a contract simulation, not relative real-memory evidence. No fixture-specific synonyms or real terms/IDs were hard-coded; Phase 1 retrieval policy is unchanged.

Proposed private-holdout targets remain unassessed: zero SQLite leaks/duplicates; CWD-invariant ranks/citations/scores/reasons; Recall@5 at least configured Chroma; Precision@5 within five points; at least 95% no-relevant abstention; and local p95 below 100 ms on 407 records. **MEM-300–305 tooling is implemented; MEM-306 (real baseline), MEM-307 (calibration/tuning/frozen real holdout), MEM-002 and Phase 3 acceptance stay open. Phase 4 must not start.**

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
  "schema_version": 1,
  "synthetic": false,
  "case_id": "local-proposal-001",
  "categories": ["draft_needs_review"],
  "split": "calibration",
  "query": "Sanitized information need without credentials or private paths",
  "agent_name": "LOCAL_SCOPE_PLACEHOLDER",
  "current_session_id": "LOCAL_SESSION_PLACEHOLDER",
  "expected_memory_ids": [],
  "acceptable_memory_ids": [],
  "excluded_memory_ids": [],
  "expected_facts": [],
  "expected_cwds": {},
  "must_abstain": true,
  "draft": true,
  "query_sanitized": false,
  "reviewed_locally": false
}
```

Real-memory cases remain local and require sanitization, referential checks against their private corpus, and human review. Phase 3 prepared local proposals from the retained candidate, but their labels are unreviewed suggestions, not accepted ground truth; no real review has been attested. **MEM-002 remains open** for its real-case labeling and benchmarking requirement. Synthetic coverage and private draft generation are not evidence of real-world retrieval improvement or permission to cut over. Architect accepted synthetic completion to unblock the earlier implementation phases only.

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
