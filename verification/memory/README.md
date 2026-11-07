# Memory migration verification: Phases 0–3

Phases 0–2 are PIC-approved. The private cohort is reviewed and SQLite-only calibration is measured, but **Phase 3 is BLOCKED, not passed**: MEM-306 primary Voyage-4 operational calibration and bounded FTS payload patch `424d963` are PIC-approved (2026-11-07); performance target (latest paired SQLite p95 126.20 ms), freeze and untouched holdout remain unresolved; the subsequent ASCII-only lexical patch `0558610` is PIC-approved (2026-11-07), not broad statistical speedup proof or Phase 3 acceptance. The narrow exact-lookup performance implementation and evidence reconciliation are PIC-approved (2026-11-06), not Phase 3 approval. **MEM-002 stays open; Phases 4–5 have not started and are not authorized; do not cut over.** No production setup, search tool, write path, automatic injection, provider or model pin has changed.

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

MEM-100 through MEM-106 are implemented and verified for this isolated prototype. MEM-002 stays open: the private, sanitized, locally reviewed real-memory cohort is mandatory before Phase 3 can pass or any cutover can be authorized. Do not invent labels or commit/return private documents, IDs, sessions, source paths, queries or labels. Architect approved Phase 1; Phase 2 approval is recorded below.

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

`prepare-review --sqlite-source "$RETAINED_CANDIDATE" --output "$UNUSED_PRIVATE_REVIEW"` creates proposals, not ground truth. The approved private cohort contains **408 records and 32 reviewed cases (16 calibration / 16 holdout), 14 positive memories and four abstention cases**. Architect adjudicated the AI-assisted semantic proposals and the user explicitly approved their labels, sanitization and transparent local-human attestation. No private case data is committed. No private IDs, agents, sessions, paths, queries, labels, content or maps are committed.

Every real case must explicitly cease being a draft, be reviewed locally, and match a separate `local-human` attestation bound to corpus/case hashes. The loaded dataset retains the exact SHA-256 of the validated attestation bytes. Until review is complete the loader rejects the cohort **before backend execution**. No review is auto-attested. Reading a retained copy for migration is not relevance evidence. SQLite-only calibration is recorded below. The configured Chroma comparison and real holdout remain **null / not run**; no threshold tuning or freeze has occurred.

After genuine calibration, `freeze --calibration-report ... --output ...` seals code, policy, dataset/backend fingerprints, embedding identity and k/warmup/repeat protocol. Holdout requires unchanged inputs, both baselines and a matching freeze. Its locked, persistent ledger consumes each normalized held-out question/scope input—not freeze timestamps or labels. Renaming cases, changing CWD, relabeling, regenerating a freeze, or partial question recycling cannot unlock consumed inputs. Markers are consumed before execution; corrections require a fresh holdout or invalidation, never erasing history. Hashes do not prove semantic novelty or honest review, and this local guard cannot prevent a malicious owner moving/deleting the workspace.

Coverage is required **within each calibration and holdout split**, not just across the cohort: at least 15 cases, five distinct positively labeled memory IDs (expected or acceptable), and two abstention cases. Each split must cover `exact_identifier`, `paraphrase`, `no_relevant_memory`, and `ambiguous_followup`; at least one of `decision`/`preference`; and at least one of `constraint_rejected_approach`/`unresolved`. Existing overall coverage and distinct normalized-question requirements still apply. These checks do not edit or attest the private draft.

Reports, calibration freezes and holdout checks bind `review_manifest_sha256` and `embedding_cache_sha256` to exact input bytes. The cache hash is explicitly null for non-cache sources, and synthetic runs have no review-attestation hash. Altered attestations and mutated or replaced caches are rejected before backend setup or holdout reservation. The cache rows used by the backend come from the checksum-validated snapshot, not a later reread of the mutable input file.

### Local milestone 2 — tail profile and ASCII-only lexical bypass (2026-11-07)

PIC decisions were recorded in `1fb71933ad0df24af162413726df4c7bbe138577`: **MEM-306 is closed** for CURRENT CONFIGURED VOYAGE-4 OPERATIONAL baseline on the retained reviewed 408-record cohort, and FTS payload patch `424d963` is approved with its disclosed limits. This is neither historical per-vector encoder proof nor Phase 3 acceptance. Architect/PIC subsequently **approved `05586103bc391ca3ac72dce31235fa04b6b646b9` on 2026-11-07** for the narrow ASCII-only change. Normalization/NFD still happens first; normalized ASCII cannot contain Mn marks; the entire non-ASCII generator/regex/STOP_WORDS path is unchanged; fresh mutable sets are independent; no shared cache or policy change was introduced. Approval accepts the disclosed evidence limits, not a statistically proven broad speedup, historical per-vector encoder proof or Phase 3 acceptance.

A fresh-copy, network-isolated SQLite-only diagnostic pass of 16 calibration cases separated connection/setup, SQL execute/fetch by source, DTO/JSON, coordinator and formatting. **These are instrumented diagnostic totals, not acceptance latency; inclusive timings overlap and cannot be summed.** Repository initialization was 2.52 ms; per-operation connections/PRAGMAs totaled 4.33 ms. Search plus result serialization totaled 1045.56 ms, including repository candidate work 526.30 ms and coordinator work 1042.47 ms. cProfile attributed 486.83 ms to 1,128 `_words()` calls and 333.36 ms to native SQL execute. Source execute/fetch totals were FTS 189.93/40.10 ms, entity 110.97/9.02 ms, tag 23.13/0.55 ms, conflict 8.57/0.04 ms. Record construction totaled 48.22 ms inclusive; JSON metadata/kinds/warnings/structured parsing totaled 11.90/7.59/7.66/42.19 ms; formatting was 1.05 ms. cProfile substantially penalizes many tiny Python/Unicode calls, so this located work but did not prove acceptance-speed attribution or that FTS alone remains the tail.

Fictional reproduction used the **actual hash-pinned `1fb7193` coordinator source**, not a rewritten oracle. Five fixed-order rounds of 100 calls observed long-ASCII `_words` mean 1.163→0.348 ms; a broader per-unique-character translation prototype regressed several Unicode inputs and was rejected. The chosen change bypasses only the character-category scan when the already normalized/NFD string is ASCII, which cannot contain `Mn` marks. **The Unicode branch, normalization, regex, stop words, fresh mutable result sets, scoring/ranking/ties/reasons/content and all repository SQL remain unchanged.** No memoization/shared cache, pooling, schema/index, threshold, limit, validation, provider, backend flag, injection or core-loop changes. Fixed-order fictional non-ASCII timings varied (Vietnamese 1.561→1.597 ms with the unchanged fallback); no Unicode or universal speedup is claimed.

The expanded existing `verify_fts_payload.py` passed 15 word-set/mutation cases, including all Unicode Mn/Mc/Me categories, compatibility forms, controls and surrogates; **159 prior-coordinator comparisons**, 140 full candidate/coordinator cases, 19 concurrent comparisons, four raw SQL/BM25/payload checks, mutable metadata isolation across siblings/operations and the concurrent-writer snapshot check. It pins the pre-change coordinator to `1fb7193` / `d273dfc0…`, while retaining the actual committed `2e6c6c4` SQL oracle. All **151 exact-lookup comparisons/two index-led plans** passed. No broad/deprecated `tests/` suite was run. Existing exact-verifier microtimings concern the earlier index optimization, not this ASCII change.

There were two preserved local diagnostic failures: an invalid private-helper loader argument stopped before dataset parsing/retrieval; then a live-tuple versus JSON-list comparison failed after 16 diagnostic retrievals. The latter's timing counters were not saved before the assertion; no missing measurements were invented or pooled. A fresh third copy used the harness's canonical JSON digest and completed one valid 16-case profile, matching all 16 prior saved SQLite outputs. Failed helpers, receipts, logs and copies are retained. Diagnostic helpers can report a stopped status with shell rc0, so the explicit receipt status—not exit code alone—was checked.

One predeclared matched pair then used unchanged primary Chroma-cache and deterministic SQLite, calibration16/k5/warmup1/reps3, 96 invocations per mode/run, separate stable copies and network isolation. No favorable acceptance rerun, additional API call or new query/vector generation occurred. The baseline was clean `1fb7193` / code `769213c8…`; postpatch was clean `0558610` / code `8db33231…`.

| Cached-vector retrieval | Before p50 ms | After p50 ms | Before p95 ms | After p95 ms | First-call p95 before → after ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Chroma unchanged control | 2.91 | 3.12 | 4.46 | 3.91 | 6.80 → 5.83 |
| Deterministic SQLite | 30.89 | 28.20 | 132.94 | **126.20** | 135.01 → 134.60 |

**All 32 saved final case outputs and metric dictionaries, and complete nonperformance metric dictionaries, are identical across the pair.** Quality remains as in milestone 1, including both correct SQLite abstentions, unchanged non-gating kind metric and the two preserved Chroma abstention failures per report (not backend errors). Observed backend/busy/leak/duplicate/repeat/CWD failures and network/model/embedding/API calls are zero. These are modest fixed-order observations with varying control timings, not statistical proof, live-API end-to-end latency or controlled disk-cold results; no timing samples are pooled with milestone 1 or the older 655.52 ms observation. **126.20 ms still fails the proposed <100 ms target.**

Profiling made 32 calibration retrievals across the failed-output-check and valid passes; the matched pair made 384, for 416 total calibration retrieval invocations in this milestone. Four full-loader passes validated 128 case objects, including 64 schema/reference/coverage/normalized-query-key validation occurrences for the same 16 holdout objects. Held-out retrieval, quality scoring, embeddings/transmission, reservation/ledger access and freeze remained zero. Original source/corpus/cases/review/cache/current candidate state and optional default target/manifest hashes, and every milestone-1 report/summary hash, were conserved; historical missing empty-WAL uncertainty was not repaired or explained away.

| Milestone-2 evidence | SHA-256 |
| --- | --- |
| Diagnostic-only safe profile | `8fe4a2df74b36d74d88a6e338d3a99505744fa13691bb31a15c35d8832bebed2` |
| Baseline raw report | `2cb9a93437c5ea55fac95e36e09383290ba521b1c56f2292de71e853cdcb92c8` |
| Postpatch raw report | `e672c5726fc0a742b6e92cd52ca698b0d24467fb89ce16f704ef486734b6ef0b` |
| Hash-bound safe milestone summary | `f24163ecf2f1bfe1f1c19f1adb7b9ed9de503a4aca6ab2f660bd479af4a89040` |
| Postpatch retrieval source | `7b5d0e7920af65f444a9012656e1ca555c673c331d3ca9bd9d4287a03591ef8f` |
| Postpatch seven-file benchmark identity | `8db3323128c89b7650c4ed6ccefa024f1a0645155a8ff32c4c705ff009dfa583` |

MEM-306/FTS approval stands; Architect/PIC approved the ASCII-only patch on 2026-11-07 with the stated limitations, not Phase 3 acceptance. **MEM-307, MEM-002 and Phase 3 stay open/BLOCKED**, with no threshold relaxation, freeze, holdout, production/default change or Phase 4/5 work. Next local recommendation is low-overhead tail-operation attribution separating exact-entity SQL from remaining Unicode/coordinator work before another fictional reproduction and one justified fix—not assuming another hotspot from this profile alone. Planning only: exact cache-hash freeze prevents appending held vectors afterward; any future explicitly authorized held-query generation must use a **new immutable combined cache preserving the existing 16 entries**, followed by final calibration on that exact cache before freeze/single-use holdout. No combined cache or held vectors were prepared now; private-data egress still requires separate informed authorization.

### Local milestone 1 — calibration and FTS payload patch (2026-11-07)

Following PIC approval of the existing Voyage-4 cache for calibration use and the user's bounded local authorization, **one baseline and one matched postpatch run** compared Chroma cache mode with deterministic SQLite on the retained reviewed 408-record cohort. This is the **current configured Voyage-4 operational** objective, not independently proven historical encoder reproduction. The optional reconstructed-default target was not run or changed; the live 410-record source was not accessed.

Both runs used unchanged harness guards/policies, the same approved corpus/cases/review/cache/source hashes, separate stable disposable copies, calibration only, **k=5, warmups=1, repetitions=3**. Fixed mode order was Chroma then SQLite; fixed run order was baseline then patch. Each mode/run made **96 retrieval invocations** (16 first calls + 16 warmups + 48 measured repetitions + 16 CWD probes). Network namespace isolation and an audit guard enforced **zero network, embedding/model/API calls**; cache misses could not regenerate vectors. Chroma kept Voyage threshold 1, `n_results=10`, top-three cap and existing scope/date behavior. SQLite kept Phase 1 relevance policy. Existing full-dataset validation structurally/reference/coverage-validated all 32 objects, including 16 holdout objects per run; only the 16 calibration cases were evaluated. No holdout ledger, freeze, threshold/policy tuning or further remote generation was used.

| Cached-vector retrieval | Before p50 ms | After p50 ms | Before p95 ms | After p95 ms | First-call p95 before → after ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Chroma (unchanged control) | 1.66 | 4.35 | 2.18 | 5.84 | 3.23 → 6.46 |
| Deterministic SQLite | 51.33 | 32.42 | 326.10 | **131.50** | 339.82 → 136.03 |

These are **cached-query-vector retrieval timings**, not live embedding/API end-to-end latency; first-call is not controlled disk-cold. There was no favorable rerun or interleaved statistical experiment. The unchanged Chroma control slowed in the second run, cause unknown; that variation limits causal/performance generalization. The earlier 655.52 ms SQLite-only observation remains historical, not a sample combined into this comparison. **131.50 ms still fails the proposed <100 ms p95 target.**

All **32 saved final case hit arrays and metric dictionaries matched exactly** across runs (16 per mode), including rank/citation/score/reason/content. This does not claim independently persisted/replayed signatures for every intermediate invocation. Within-run repeats/CWD checks observed zero changes; both backends evaluated 16 cases with zero backend/busy/leak/duplicate failures. Unchanged Chroma/SQLite quality respectively: Recall@1 1.0/0.8571, Recall@3 and @5 1.0/1.0, MRR 1.0/0.9286, nDCG 1.0/0.9473, fixed-five Precision@5 0.175/0.175, and no-relevant abstention 0.0/1.0 (SQLite correctly abstained on both no-relevant cases). The existing SQLite non-gating `kind_correct=0.142857` is unchanged and was not tuned. All per-category/private traces remain in ignored reports.

Architect/PIC **approved `424d9638922498754a66d5a1c08305206f27e58a` on 2026-11-07**, accepting its bounded correctness/evidence with the disclosed measurement limitations, not Phase 3 acceptance: only FTS materializes six ranking-key columns, selects scoped parent winners/cap, then hydrates full payload in the same operation-owned read snapshot. Original eligibility joins/predicates, weighted BM25 MATCH context/global statistics, tie order and final order remain. Entity/tag/ID SQL and bindings are unchanged after whitespace normalization. No schema/index/tokenizer/weight/threshold, connection/pooling, conflict, DTO, provider or production wiring change was made. A fictional 2,496-hit payload profile of the actual committed SQL showed 29 → six materialized columns and mean execute 293.32 → 16.47 ms; tight/empty samples also improved. That three-repetition, old-then-prototype experiment justified the slice, not a universal speedup claim.

`verification/memory/verify_fts_payload.py` loads and hash-verifies the actual `2e6c6c4` repository source (`ee27d512…`), not an invented SQL oracle. It passed **140 full candidate/coordinator comparisons, 19 concurrent comparisons, four raw SQL/BM25/payload checks**, scope-before-cap, ties, Unicode/quoted OR/empty hits, lifecycle/date/as-of/ID/session restrictions, conflicts/CWD, FK/deletion/read-only checks, and a concurrent writer proving hydration/conflict scans share one snapshot. The existing exact verifier passed **151 comparisons and two index-led plans**. An initial new-verifier assertion mistakenly counted the memory-ID source as FTS-only; it was corrected without a runtime change, and the failed log is preserved. The exact verifier's mixed three-repetition microtimings concern the earlier exact-index optimization, not an FTS regression measurement.

```sh
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -I -B -S verification/memory/verify_fts_payload.py
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -I -B -S verification/memory/verify_exact_lookup.py
```

| Evidence | SHA-256 |
| --- | --- |
| Baseline raw report | `830790ff8b8e1bd64fa83a90ad07e1cb36678b12cc04b82ca19293f7ff4e4a8d` |
| Postpatch raw report | `2db8455617c2041d76af874916932947f44f7146af73c24ce9d087e5ebc68ce6` |
| Hash-bound safe milestone summary | `00757778ddd53f6d575ac72643175b2783d30fea25f3d51cfb85645cd2a354e3` |
| Postpatch repository | `7533666b35ca9f39aedf0f90d0bebed26dd5a2990e153ed3fc9b84ec5dfa65b6` |
| Postpatch seven-file benchmark code identity | `769213c877e4cb8baa16a938e83f7001ae9c51e1bb4f00454ef25d58abeef6ee` |

The baseline was clean `2e6c6c4` / code identity `38e9c303…`; postpatch was clean `424d963`. Objective/protocol/source/cache/review bindings are hash-bound sidecars; raw reports were not rewritten to add an objective field. Original corpus/cases/review, retained source, current candidate file state and optional default target/manifest were conserved; the historical missing empty WAL remains unexplained and was not fabricated. No raw questions, documents, IDs, labels, vectors, credentials or private mappings are committed.

**Architect/PIC approved MEM-306 closure and the narrow FTS patch on 2026-11-07.** The established baseline is CURRENT CONFIGURED VOYAGE-4 OPERATIONAL comparison on the retained reviewed 408-record cohort, not historical encoder proof or the current 410-record source. MEM-307, MEM-002 and Phase 3 acceptance remain open. The performance target remains unmet, no calibration freeze or holdout was run, and Phases 4–5/cutover remain unauthorized. Any later held-out Voyage embedding generation requires separate explicit informed authorization; the user's broad local permission does not authorize transmitting additional private questions. The current cache covers calibration only, while freeze/holdout guards bind the exact cache SHA-256: the held-out embedding strategy must therefore be resolved before freezing, not by silently extending a frozen cache or weakening its guard.

### Primary Voyage-4 comparator — calibration-query cache only (2026-11-07)

The user confirmed the current model is exactly **`voyage-4` / 1024 dimensions** and selected **current configured Voyage-4 operational Chroma comparison on the retained reviewed 408-record cohort** as primary. This does **not** independently establish the encoder of every historical vector or prove historical reproduction. The current 410-record source is not substituted. The separately prepared reconstructed-default comparator remains **optional**, with its original provenance and limitations intact.

The user explicitly authorized transmitting **only the 16 approved, sanitized calibration query strings** to Voyage using the existing configured credential, acknowledging API charges. One batch to the official `https://api.voyageai.com/v1/embeddings` endpoint completed with HTTP 200, response model `voyage-4`, and **375 reported tokens**; dollar cost was not returned or estimated. One SDK embed invocation, one API-resource invocation, one session send and one adapter send were observed. SDK/HTTP retries and redirects were disabled, TLS verification remained enabled, and ambient proxy/netrc use and payload/credential logging were disabled. These are logical call/send counters, **not an independently measured wire-level request count**. API-call timing in the private receipt is preparation time, not retrieval latency. No documents, separate case/memory IDs, label/answer/context fields, code, holdout queries or LLM requests were sent.

The helper preserved configured wrapper semantics: `Client.embed(queries, model="voyage-4")`, with **no explicit `input_type`, query prefix, `output_dimension`, `output_dtype` or normalization override**. SDK defaults were truncation enabled and base64 transport decoded to float32. All 16 returned vectors were independently generated for distinct calibration-query digests and verified finite, nonzero and exactly **1024-dimensional**. The private schema-1 cache contains query digests/vectors, **not raw query text**, with `provenance=independent-configured-query`, `reviewed_locally=true`, and exact source/corpus/cases/review bindings. The unchanged cache-mode guard still requires the real configured Voyage branch; no fake key, provider switch or guard weakening was used. Chroma's Voyage threshold **1**, `n_results=10`, top-three cap and scope/date policy were not changed or executed.

The approved combined case file was structurally decoded in **two selection/validation passes: 32 objects per pass, including the same 16 holdout objects per pass**. Holdout query text and labels were not analyzed, used for embeddings, exposed or transmitted; the single-use holdout ledger was not accessed. The first local preflight stopped on SDK-import filesystem guards **before any SDK API or HTTP call**; its receipt/helper/log remain preserved. Offline import checks identified bytecode-cache writes and `/dev/null`; the final runner uses `-B` and allows only the verified null device beyond the private workspace. There was **no second remote generation or retry**. Pure `read_jsonl`/digest/static cache-contract validation required no backend or further case parsing.

Safe aggregate provenance:

| Artifact / binding | SHA-256 |
| --- | --- |
| Complete 16-row query cache | `594666c408277d34253d778421f229f6d48a1d46b5404b7f9b2dd8be04ad7d22` |
| Safe preparation summary | `99fcb8bebea6d6e0135557b38a66c9d2e8c838598039080082cbae3bfd5b4ea3` |
| Pure verification receipt | `7bb68bb79c89a34ba44f23f779877e19d4473d19ae4391dbb3c4d45c9d8c8bc2` |
| Cache source fingerprint: benchmark digest of retained file map | `5e3d9f52cdde7d8884b063e2c6c92ddca385541a2b299289041075f8b9a53b7c` |
| Finalized local-human review manifest | `1c0af2cc2ef24e586645daf3e1186a1a1af681ec2519f03b31d4b96a458a96b2` |

The cache source fingerprint is deliberately **not** the distinct migration collection-bound fingerprint `a2941060da50ed2adca42bfa2d66b8e20a142c586c1540017b42203e84138220`. Corpus/case/review hashes, retained source files, current candidate file state, and the optional default target/manifest were unchanged before/after preparation. The historical missing empty-WAL caveat is not repaired or rewritten. The live source was not accessed; private mappings, queries, vectors and credentials are not committed.

**Cache-preparation handoff boundary (subsequently PIC-approved for calibration-cache use):** that preparation constructed/queried no retrieval backend and ran no calibration benchmark, tuning, freeze, holdout or Phase 4/5. The later separately authorized local milestone is recorded above. MEM-306 is now PIC-closed by the subsequent operational-baseline decision; MEM-307 and MEM-002 remain open and Phase 3 stays **BLOCKED**. At that earlier handoff, historical SQLite-only p95 was **655.52 ms** against the proposed **<100 ms** target; the subsequent milestone's separate measurements are above. The subsequent calibration used separate authorization; cache preparation grants no permission to embed or send future holdout questions.

### Optional reconstructed-default comparator — preparation only (2026-11-07)

The earlier user-authorized **`reconstructed_default_not_historical`** comparator remains prepared from the unchanged, reviewed **408-record corpus**, now as an **optional** comparator rather than primary. Its objective is intended default-model Chroma retrieval, **not** historical reproduction of the unknown encoder or an improvement claim against historical Voyage retrieval. Both the retained 408-record collection and the later current-source snapshot record 1024 dimensions; the latter has 410 records and is not the approved cohort. The user's recollection of Voyage voyage-4 or 3.5 testing does not identify an exact historical model. Production sources and provider selection are not repaired, re-embedded or switched.

Preparation used a fresh ignored private target, installed Chroma 1.5.9 and cached ONNX `all-MiniLM-L6-v2`, under verified OS network isolation and a sanitized offline process. All six model files matched the cached archive whose SHA-256 matches the installed default implementation's expected digest. Original documents were embedded with the installed default tokenizer's 256-token truncation/padding, attention-mask mean pooling, L2 normalization and float32 output, with no query/task prefix or label-derived additions. All **408 records were nondeleted and indexed**, with exact original IDs/documents/metadata; no lifecycle repair or current-410 substitution occurred. Stored vectors are **384-dimensional**; configuration is **cosine / `ef_construction=200`**. The unchanged adapter's intended `n_results=10`, top-three cap, agent/session/date predicates and threshold `0.30` are recorded but were not executed.

During that separate optional-comparator preparation, there were **26 local document-embedding invocations / 26 ONNX graph runs for 408 documents**. Query embeddings, retrieval queries, calibration/benchmark runs, private case/label or holdout-question reads, holdout-ledger access, remote calls, network attempts and downloads were **zero**. The approved corpus/attestation/model bytes and retained frozen-backup hashes remain unchanged; the live 410-record source was not accessed. SDK resources were cleanly closed and the prepared target fingerprint was stable. No query-embedding cache or freeze was created.

Preparation initially stopped on an overly strict bitwise vector-roundtrip assertion; that failed receipt/log is preserved. A separate disposable verification copy, with **no re-embedding or retrieval**, verified all 408 ID/document/metadata triples exactly and compared SDK-returned vectors with the explicitly submitted float32 vectors retained in the target's insertion queue. All 408 were numerically equivalent (`rtol=1e-6`, `atol=1e-7`); 285 were bitwise identical, maximum component delta was `1.4901161193847656e-08`, and maximum cosine-direction difference was `8.881784197001252e-16`. Bitwise vector identity is not claimed. Preparation/verification outer-command times were approximately 12.1 s / 1.9 s, including startup/reporting, **not retrieval latency**; isolated embedding-stage timing was not retained after the initial stop. Nothing was rerun to manufacture benchmark evidence.

Safe aggregate provenance: comparator-manifest SHA-256 `24db2baf144333661e4742e549c017e1e6fafea1c088a1fd7f56e67378185a29`; prepared-target tree SHA-256 `85f9fece3d880fab4da5579c700a7dcb438d69163ff3df1c594d2b6da972d485`; exact indexed-record root `8dac30bc30f08f0665851e1d9af1f9fa9ca79183a83ac844f15fc91c272ee4a4`. Private manifests retain input/model/package/code hashes and the original failed verification attempt; no private records or maps are committed. Future benchmark evidence must explicitly retain this reconstructed objective and its manifest binding; no benchmark guard was weakened or executed to imply acceptance.

**Preparation is not MEM-306 completion or Phase 3 acceptance.** For this optional default comparator, Architect review and separate authorization remain required before generating its query embeddings or running its retrieval benchmark. That optional preparation did not close MEM-306; its later primary operational-baseline PIC closure stands. MEM-307 and MEM-002 remain open; the historical SQLite-only p95 **655.52 ms** still misses the proposed **<100 ms** target. No tuning, freeze, private holdout, Phase 4/5 work or cutover is authorized.

### Exact entity/tag lookup performance correction

Only `SQLiteMemoryRepository.candidates` changes at runtime. Exact entity/tag sources drive from the existing `exact_entity`/`exact_tag` normalized-value indexes using an explicit `CROSS JOIN` ordering fence before joining unit and parent payloads. All existing eligibility predicates still run before per-parent window ranking and source limits. Projection, bound parameters, source-detail tie selection, collations, caps, FTS, conflicts and the operation-owned read-only snapshot are unchanged. No schema/index, planner pragma, cache, pooling, provider or relevance-policy change is included.

Run `verification/memory/verify_exact_lookup.py` with the provisioned offline interpreter. Its fictional legacy-SQL oracle compares candidate and complete result signatures, tests ties/Unicode/filters/limits, verifies real index-led plans, and reports broad-term/selective-scope timings without treating noisy microbenchmarks as adoption evidence. Existing standalone verifiers remain required; deprecated `tests/` are not run.

#### Architect/PIC sign-off — 2026-11-06

Architect/PIC approved `ed105f601debbe8634bee0357f841e633c4a1213` (exact entity/tag lookup only) and `66fccf66624d642ca07ad24f3fb99eb7c2318bee` (documentation reconciliation); no runtime fix was required. The separately retained chain below is accepted as supporting evidence for this narrow patch **with the unresolved historical attribution gap**, not as Phase 3 acceptance or a universal speedup. The withdrawn hashes and former claims remain unsupported.

Completed offline verification used provisioned Python 3.12.10 / SQLite 3.47.1: `validate_assets.py`, `verify_phase1.py`, `verify_exact_lookup.py`, `verify_phase2.py`, `verify_phase3.py` and `verify_migration_operator.py` all exited 0. The exact verifier passed 151 candidate/full-result comparisons and two index-led plans, including scope-before-cap, ties, Unicode, concurrency, read-only, foreign-key and derived-deletion checks. Independent capture matched 24 SQL statements and bound-parameter tuples against the actual committed baseline. The two existing fictional lexical misses remain 27/29, not a new regression.

Retained artifact hashes, variant links, code/input/review bindings and aggregate dictionaries were checked; 25 quality/safety aggregates per variant were independently recomputed without mismatches. All 16 final calibration signatures match the saved baseline and both variants. Historical 192/192 call checks are corroborated by the inspected harness, not replayed by this review; raw timing samples were unavailable, so historical p95s were linked and checked, not independently rederived. No private A/B harness, private holdout, live/original source database, model/embedding call, application startup, dependency installation or deprecated `tests/` suite was run. Fictional Phase 3 checks exercised only simulated holdout-gate mechanics. No historical report was rewritten.

The user confirmed the existing phase order: resolve the genuine comparator, performance and frozen untouched-holdout gates before Phase 4. MEM-306 is now PIC-closed (2026-11-07); MEM-307, MEM-002 and Phase 3 acceptance remain open; Phases 4–5 have not started and are not authorized, and production remains on Chroma.

#### Evidence reconciliation (documentation only; no private benchmark rerun)

The earlier documentation mixed timing claims that cannot be attributed to its linked reports. The three SHA-256 values supplied in the preceding handoff could not be located in the retained private workspaces: combined `aebd09dfaf3b0a87c36725fbd43828af8fbc0664d303f5fe0c5914ba26e627ef`, baseline `7338a7b37668452518b3a5613dd5be39e5b8bba8423acfd6614f10ede484df92`, and optimized `36103954987b1c168a6de115798f7b5630f7190e57f50daa92bd28e3c70b0022`. Their former numeric claims are withdrawn, not explained away as rounding, changed denominators or profiler overhead. No cause for the missing hash attribution has been established, and no measurement was rerun or raw report rewritten to fill the gap.

A separately identified, retained chain is internally verifiable: its combined report references the exact hashes of both variant reports, and its aggregate metrics equal those variant reports. Its baseline repository hash also matches the recorded committed source. **This is the currently inspectable evidence, not a silent replacement of the unavailable handoff hash set. PIC accepted it only for the narrow patch sign-off above; the historical provenance gap remains unresolved.**

| Retained artifact role | Verified SHA-256 |
| --- | --- |
| Combined A/B | `2d58c279b17596a3edc712e3abe73e5a4f5c56910b01ce49334cad7c28e838ea` |
| Baseline variant | `1031454fb4412ee0c81e3a38901d2db78b64a59a37d50eea30d282f0076f9a81` |
| Optimized variant | `2b2c625ad1fb06aef86c9d6faf948b66bdb59be73308c7ce45fa63e415fa34aa` |
| Retained A/B harness, inspected but not executed during reconciliation | `aeed635c3b015d30d86ef6b96468b2f480c2d849af24773ca3c538c664185795` |

That chain records separate consistent clones, fixed old-then-new order, the same 16 calibration cases, k=5, one warmup and three repetitions. There are **96 retrieval invocations per variant / 192 total**: 16 first calls, 16 warmups, 48 measured repetitions and 16 CWD probes. All **192/192** rank/citation/score/reason checks match the saved baseline. Recall@1 is 0.8571, Recall@3/@5 1.0, MRR 0.9286, graded nDCG 0.9473 and fixed-denominator Precision@5 0.175. Both abstentions are correct; observed excluded/scope/lifecycle/deletion/unknown-ID leaks, duplicates, backend/busy errors, CWD failures and repeat changes are zero. These are recorded calibration checks, not holdout evidence or phase acceptance.

| Metric from this retained chain (milliseconds) | Baseline | Optimized | Sample / boundary |
| --- | ---: | ---: | --- |
| Warm p50 | 428.86 | 96.88 | 48 measured retrieval calls per variant |
| Warm p95 | 1237.77 | 655.52 | Same 48 measured calls; warmups excluded |
| First-call (“cold”) p95 | 1228.86 | 618.70 | 16 first calls, one per case; not controlled disk-cold I/O |
| Entity `Cursor.execute` wall total | 24475.99 | 731.62 | 96 entity statements per variant across all retrieval phases |
| Tag `Cursor.execute` wall total | 15485.42 | 159.18 | 96 tag statements per variant across all retrieval phases |
| FTS `Cursor.execute` wall total | 15499.83 | 15702.58 | 96 FTS statements per variant across all retrieval phases |

SQL-stage totals are cumulative instrumented `Cursor.execute` wall time, not latency percentiles, per-call averages, or inclusive coordinator totals. `fetchall` is timed separately: entity 65.25→65.49 ms, tag 5.22→3.82 ms, FTS 1352.91→1397.19 ms. Execute intervals end before the harness captures one EXPLAIN per exact family, so plan capture is excluded from those SQL totals but can add to first-call/end-to-end latency. Timer/counter bookkeeping is present. Controls/conflict and setup statements are tracked separately; they are not added to the entity/tag/FTS totals. Harness inspection found no cProfile phase in this A/B. These definitions do not reconcile the unavailable handoff values; the latency and execute/fetch measurements must not be substituted for one another.

The retained observations support faster exact lookup, but **optimized warm p95 655.52 ms still fails the proposed <100 ms target**. FTS SQL was unchanged and its execute total did not improve. Original candidate/review preservation and zero holdout/model/embedding/network invocations are recorded in the combined report. This is one sequential, instrumented comparison with cache/order and fixed-clone effects, not an interleaved statistical experiment, a genuine Chroma comparison or a disk-cold benchmark. Previously reported fictional broad-term/tight-scope roughly 1–5% regressions remain a separate caveat, not a measurement from this private chain. FTS and late hydration were deferred in that earlier exact-lookup chain; the subsequent FTS-only milestone above is separate evidence. No gates or checked tasks changed through that evidence reconciliation.

### Current result and remaining gates

Offline synthetic safety checks pass. SQLite Recall@5 is **0.9259**, consistent with 27/29 labeled-ID checks and honest lexical abstentions. The hash-vector Chroma score is a contract simulation, not relative real-memory evidence. No fixture-specific synonyms or real terms/IDs were hard-coded; Phase 1 retrieval policy is unchanged.

Proposed private-holdout targets remain unassessed: zero SQLite leaks/duplicates; CWD-invariant ranks/citations/scores/reasons; Recall@5 at least configured Chroma; Precision@5 within five points; at least 95% no-relevant abstention; and local p95 below 100 ms on 407 records. **MEM-300–305 tooling is implemented; MEM-306 (primary operational baseline) is PIC-closed (2026-11-07). MEM-307 (calibration/tuning/frozen real holdout), MEM-002 and Phase 3 acceptance stay open. Phase 4 must not start.**

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

Real-memory cases remain local and require sanitization, referential checks against their private corpus, and human review. The approved cohort now contains **408 records and 32 reviewed cases (16 calibration / 16 holdout)**: Architect adjudicated the AI-assisted proposals, and the user approved the labels, sanitization and transparent local-human attestation. Newly generated proposals still require their own review; they do not inherit that approval. This reviewed cohort is distinct from the **407-record preservation snapshot**. Reviewed labels and SQLite-only calibration are not benchmark acceptance: **MEM-002 remains open** until the required real-case benchmarking gate passes, alongside MEM-307 and Phase 3 acceptance. The primary operational Chroma comparison is now PIC-established under MEM-306; performance target, freeze and untouched holdout remain unresolved. Synthetic coverage, draft generation and the narrow patch sign-off do not authorize Phases 4–5 or cutover.

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
