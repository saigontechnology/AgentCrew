# SQLite Memory Migration and Retrieval Plan

## Status

**Phases 0–2 PIC-approved. Reviewed private SQLite-only calibration is measured; Phase 3 remains BLOCKED: Architect/PIC rejected Phase 3 acceptance on 2026-11-07. The authorized sequence was valid and complete, but the single-use holdout failed the pre-agreed Recall@5 gate (SQLite 0.9285714285714286 < Chroma 1.0). A later generic continue does not waive that gate or authorize a retry. MEM-306 and the bounded FTS payload patch remain PIC-approved (2026-11-07). This failed holdout does not close MEM-307, MEM-002, or Phase 3, and it must not be rerun. The narrow exact entity/tag performance fix and evidence reconciliation are PIC-approved (2026-11-06); this does not approve Phase 3. The FTS-only payload patch `424d963` is PIC-approved (2026-11-07), not Phase 3 acceptance. The subsequent ASCII-only lexical patch `0558610` is PIC-approved (2026-11-07), not a statistically proven broad speedup or Phase 3 acceptance; the subsequent ENTITY-only payload patch `3d56fe867a03744e7c6509c61464daaea32f26c8` is PIC-approved (2026-11-07). Architect accepted that one fixed calibration pair's trade-off: warm median 27.326447→34.302914 ms (+25.530092%), warm p95 114.810110→70.745843 ms, and first-call p95 121.315279→73.610382 ms. That pair meets the proposed <100 ms p95 bound only. It is not statistical proof, live-API latency, disk-cold measurement, or Phase 3 acceptance. MEM-307, MEM-002 and Phase 3 remain open. No further speculative local optimization is approved.**

This document defines the work needed to replace AgentCrew's ChromaDB memory backend with SQLite and FTS5. It also defines a safe migration script and a repeatable benchmark job. Standalone Phases 1–3 tooling exists alongside the existing services; production memory behavior is unchanged. Phase 3 has a user-approved, Architect-adjudicated private cohort and SQLite-only calibration evidence. One authorized performance-only exact-lookup correction and matched calibration A/B preserve the relevance policy and saved result signatures. The later authorized sequence froze the unchanged policy and ran one holdout; Architect/PIC rejected Phase 3 acceptance on 2026-11-07 because that holdout failed Recall@5. No remote LLM call or threshold tuning occurred. The verified reconstructed-default comparator remains optional. The user subsequently confirmed current `voyage-4` / 1024 dimensions and selected current configured Voyage-4 operational comparison on the retained reviewed 408-record cohort as primary, not proven historical reproduction. They explicitly authorized sending only its 16 sanitized calibration queries to Voyage: one batch completed, and the source/review-bound query cache is verified. No documents, separate case/memory IDs, label/context fields or holdout queries were transmitted. One authorized network-isolated Chroma-cache versus deterministic SQLite calibration run and one matched postpatch run have now completed. Quality/safety and all saved final outputs were unchanged; SQLite p95 improved from 326.10 to 131.50 ms but still misses the proposed <100 ms target. Architect/PIC approved MEM-306 closure and the narrow FTS patch on 2026-11-07 with the documented limitations, not Phase 3 acceptance. Phases 4–5 have not started and are not authorized; cutover remains unauthorized.

Local milestone 2 profiled the remaining tail before selecting one further performance-only change. A valid instrumented 16-case pass showed substantial coordinator `_words()` work as well as SQL, not connection setup alone. Fictional reproduction justified bypassing the nonspacing-mark scan only for normalized ASCII; the Unicode path, word sets, mutability and all ranking/policy inputs remain unchanged. The broader translation prototype was rejected. One predeclared matched pair observed SQLite p50 30.89→28.20 ms and p95 132.94→126.20 ms, with all 32 saved final outputs/metrics unchanged and zero network/model/API calls; this modest fixed-order observation is not statistical speedup proof and still fails the proposed target. Architect/PIC approved `05586103bc391ca3ac72dce31235fa04b6b646b9` on 2026-11-07 for the narrow ASCII-only change: normalization/NFD still precedes the branch, normalized ASCII contains no Mn marks, the complete non-ASCII generator/regex/STOP_WORDS path is unchanged, and fresh mutable word sets remain independent without a shared cache or policy change. This does not establish historical per-vector encoder identity, broad statistical speedup or Phase 3 acceptance. Diagnostic-only timings, helper failures, Unicode/mutation checks, source/report hashes and limitations are in the verification README. No further fix, threshold tuning, freeze, holdout or rollout occurred.

Local milestone 3 used low-overhead phase timers, not cProfile, on the existing calibration cases and reproduced wide ENTITY materialization with fictional data. One correction reuses the approved scoped six-key late-hydration path for ENTITY only; FTS, TAG and MEMORY_ID SQL/bindings, lexical behavior, policies and operation-owned snapshots are unchanged. One fixed-order matched pair observed SQLite p50 27.33→34.30 ms (25.5% regression), p95 114.81→70.75 ms, and first-call p95 121.32→73.61 ms. All 32 saved final outputs/metrics and non-timing mode metrics remain identical, with zero network/model/API events. The median regression and unchanged-control variation are disclosed, not rerun away. Architect/PIC approved `3d56fe867a03744e7c6509c61464daaea32f26c8` on 2026-11-07 and accepted that fixed-pair trade-off. Calibration-only p95 passing is not holdout acceptance or a phase/task closure. No further speculative local optimization is approved. See the verification README and hash-bound milestone receipt for the profile, all helper preflights, commands, references and preservation checks.

The authorized Phase 3 completion sequence then built combined cache `f6cb3fea…`, preserved calibration cache `594666c4…`, and sent only the 16 sanitized holdout queries to Voyage-4 once (397 tokens; dollar cost unobserved). Network-isolated calibration passed every proposed gate, including SQLite warm p95 77.78 ms. Freeze digest `188a5aec…` binds that exact cache, review, code, policy, and protocol. The single-use holdout then failed Recall@5, SQLite 0.9285714285714286 versus Chroma 1.0; its other proposed gates passed, including warm p95 64.66 ms. Architect/PIC rejected Phase 3 acceptance on 2026-11-07. Holdout report `791037a7…` remains the failed evidence. It was not retried, and it does not close Phase 3 or authorize Phases 4–5. Safe aggregate `c4c95e59…` records the gate table and conservation checks.

**PIC override of the Phase 3 gate — Phase 4 start (2026-11-12).** After the reservation-order runner fix `2ef1df258c574e1d1883bcb1afccae60eda55bdf` was accepted, the one-shot fresh lexical holdout exam ran once, network-isolated, with the user-reviewed packet derived from draft `626f5bb43da8` (local-human review evidence; not a semantic-non-overlap audit), the retained reviewed 408-record corpus, the matched SQLite candidate and Chroma source, and the single authorized Voyage-4 query cache `aba4c6ea…` (339 tokens, one embed call, no retry). The exam reserved fresh ledger token `2d088d52a9aa5df417bd0948d114c5c1471c3c8344f9be6ac61e65707bb7c199` and returned status **blocked** with failures `recall_regression` and `precision_regression`; report SHA-256 `4065c502b8d9b6e5482af64f87556c0b0b8da4ef1bead4849e746d511ae1a969`, output directory `phase3-fresh-holdout-exam-55dc95132c75` under the fresh-holdout worktree. Accepted regressions in the SQLite search: Recall@5 0.50 versus Chroma 1.00, Precision@5 0.0875 versus 0.175 (−8.75 pp against a 5 pp allowance). Passed gates: no-relevant abstention 1.0, SQLite warm p95 33.4 ms, zero leaks of any kind, zero embedding calls. On **2026-11-12 the PIC explicitly reviewed this verified fresh-exam record and overrode the Phase 3 gate, authorizing Phase 4 to start now with the current lexical-only policy.** Phase 3 remains recorded as **NOT MET**; this override does not rewrite sealed evidence, and the consumed exams, the benchmark-holdout-ledger, the fresh-holdout ledger, draft packets and Phase 3 verification artifacts stay untouched. No new embedding or Voyage call was sent. The shipped retrieval policy stays behavior-identical to the measured one at commit `2ef1df25` (code hash `e42f148d…`): `qualified-sqlite-signals-v1`, fts_weights `[6,4,1,8]`, rrf_k 60, kind_weight 1.1, min_overlap 0.35, relative_coverage 0.7, cosine_distance with explicit-cutoff-before-rrf admission and default max distance null (semantic matching disabled), cross-source ties by diversity then stable ID. No threshold changes, no semantic cutoff, no tuning of any kind.

**Phase 4 implemented under the override (2026-11-12).** MEM-400 through MEM-405 are implemented on branch `feat/sqlite-memory-search-tool` (worktree `AgentCrew-sqlite-search-tool`, based on `2ef1df25`): the `search_memory` definition gained `kinds`, `memory_ids`, `detail` (summary/evidence/full) and a server-bounded `limit` (1–20) with accurate wording; the handler uses structured results through a non-breaking `BaseMemoryService.search_structured` compatibility adapter plus a Chroma override, preserving date validation, fail-open behavior and server-side agent scope; optional query expansion runs at most one bounded memory-LLM call with validated JSON and deterministic fallback only when the deterministic pass returns no hits and the request is not ID-restricted; optional reranking is gated by `MEMORY_RERANK_ENABLED` (default off), accepts known candidate IDs only, and falls back deterministically; result expansion by known memory IDs supports summary, evidence and full-detail modes through an agent-scoped `get_memory_documents` contract; and tool registration was verified across all five surfaces (local interactive agents, jobs and delegation, A2A, ACP, and the Cursor-agent path), with automatic headline injection unchanged. The disposable `verification/memory/verify_phase4.py` verifier asserts the frozen policy parameters, the unchanged measured code hash, the expanded tool contract, fail-open/date validation, expansion/rerank bounds and fallbacks, detail modes, the Chroma adapter scope/threshold/ID behavior, and per-surface registration; all existing offline verifiers still pass and lint is clean apart from two pre-existing-style `TRY004` findings. Phase 4 completion is tool-layer only: the user-visible default memory backend remains Chroma, MEM-002 and MEM-307 remain open, and Phases 5–6 remain blocked.

**PIC authorization of Phase 5 (2026-11-12).** On 2026-11-12 the PIC explicitly authorized implementing the SQLite write path (MEM-500 through MEM-508) on top of the accepted Phase 4 tool layer, while Phase 3 remains recorded as NOT MET and Phase 6 (shadow mode, cutover, default-backend switch, Chroma removal) stays blocked. The write path is implemented on branch `feat/sqlite-memory-write-path` (worktree `AgentCrew-sqlite-write-path`, based on Phase 4 commit `ab18af2a56335d4dbda9d1403d1e9153b85e01bb`): `store_conversation` captures `os.getcwd()` synchronously at queue time and both backends persist it (Chroma metadata gains `cwd`; SQLite writes the existing `memory_record.cwd` column); the backend-neutral `MemoryRecordBuilder` in `AgentCrew/modules/memory/memory_record_builder.py` preserves the current prompts, hooks, fallback and session context exactly, and `MemoryWorker` now orchestrates a pluggable persistence backend (`ChromaMemoryPersistence` for embeddings/metadata/vector candidates, `SQLiteMemoryPersistence` in the new `AgentCrew/modules/memory/sqlite_service.py` for canonical writes) so Chroma and SQLite share one extraction path with no duplicated logic; `SQLiteMemoryService` fulfills the complete `BaseMemoryService` contract with fail-open storage, header listing with session exclusion and no CWD in headers, corpus/evolution methods preserving `evolved_at` behavior, physical forget/delete transactions that remove canonical, units, FTS, entities and tags atomically (verified with a mid-transaction failure trigger leaving no partial deletion and a clean `PRAGMA foreign_key_check`), explicit retention that never deletes at initialization and returns observable counts, bounded exact/entity/FTS consolidation candidates with zero vector use (FTS-sourced candidates additionally pass the frozen-policy `min_overlap` 0.35 word-overlap gate; no embeddings are created), and worker-thread/concurrency ownership verified for concurrent tasks, concurrent sessions, and shutdown that now drains pending operations without lost or duplicated writes. **Code-identity handling:** the retrieval-critical identity files stay pinned per-file to their Phase 4 values in `verify_phase4.py` (`benchmark_memory_retrieval.py`, `extract_memory_calibration.py`, `benchmark.py`, `retrieval.py`, `record_parser.py`, `types.py`, `voyageai_ef.py`), while `sqlite_repository.py` — which gained only appended write-path methods with every existing retrieval method byte-identical — is pinned by a digest over the exact Phase 4 method sources; the combined measured code hash `e42f148d…` therefore no longer applies verbatim once write methods are appended, and the per-file/method pins now prove the retrieval behavior is unchanged. The new disposable `verification/memory/verify_phase5.py` verifier asserts the Phase 5 gate (SQLite writes, reads, forgets, deletions and evolution standalone without Chroma), CWD capture, builder equivalence, header/session behavior, atomicity, retention, bounded vector-free consolidation candidates, concurrency and shutdown draining, fail-open storage, and the frozen policy parameters; all twelve existing offline verifiers still pass on the branch, and lint is clean apart from pre-existing findings in the frozen repository file. Phase 5 completion is write-path only: the user-visible default memory backend remains Chroma, MEM-002 and MEM-307 remain open, and Phase 6/cutover and the default-backend switch remain blocked.

**PIC redefinition of Phase 6 — selection-based cutover (2026-11-13).** On 2026-11-13 the PIC redefined Phase 6 away from the original shadow-mode plan: **MEM-600, MEM-601 and MEM-602 (shadow mode, shadow period, retune loop) are superseded by PIC decision, not silently checked** — the PIC explicitly reviewed and accepted the recorded fresh-exam regressions (Recall@5 0.50 versus Chroma 1.00; Precision@5 0.0875 versus 0.175, −8.75 pp against a 5 pp allowance) and chose a user-facing engine selection instead of a shadow soak, so those regressions are now user-visible by default by PIC acceptance. Phase 6 is implemented on branch `feat/sqlite-memory-engine-selection` (worktree `AgentCrew-sqlite-engine-selection`, based on Phase 5 commit `a3612036ab1014f211e7d04933933634279c6ed2`): a `memory_engine` option in `config.json` `global_settings` with values `sqlite` (default) and `chromadb` (invalid values fail startup with the valid values named); the centralized wiring in `AgentCrew/setup.py` constructs `SQLiteMemoryService` for `sqlite` or `ChromaMemoryService` for `chromadb`, with a clear install-hint error when `chromadb` is requested but not installed; `chromadb==1.5.9` and `voyageai==0.5.0` moved from hard dependencies to a `chromadb` optional extra (`uv sync --extra chromadb` / `pip install "agentcrew-ai[chromadb]"`) — voyageai was audited and is imported only by the Chroma embedding path; the SQLite startup chain never imports chromadb or the embedding-function modules at module scope (verified by an AST audit plus a runtime `sys.modules` guard); and a new automatic first-run migration implements the decision matrix: existing SQLite store used as-is; no store plus Chroma data migrates automatically (synchronous, progress-logged, reusing the verified migrator semantics — offline source copy, frozen backup, idempotent batching with resume, canonical-checksum verification, report, lock files, no network, no embeddings, Chroma source untouched) into `memory_db/agent_memory.sqlite3`; no store plus Chroma data plus missing chromadb fails fast with actionable instructions and never silently starts an empty store; no data at all starts a fresh empty store; migration failure aborts startup without silent fallback; an incomplete migration resumes idempotently on the next start (the working copy is rebuilt from the verified frozen backup because the SDK's clean close checkpoints its WAL). **Rollback window (MEM-605):** the Chroma collection stays untouched on disk and rolling back means setting `memory_engine` back to `chromadb` — which requires installing the `chromadb` extra. **Packaging (MEM-606):** the optional-extra move is implemented now per PIC decision, ahead of the original rollback-expiry condition, with collection data retained. The new disposable `verification/memory/verify_phase6.py` verifier asserts the engine contract, both selection paths, the chromadb-free SQLite chain, the automatic migration (checksums verified, source untouched, no embeddings), the fail-fast, fresh-start, abort and resume cases, packaging, the frozen policy parameters, and per-surface wiring; all thirteen existing offline verifiers still pass on the branch, and lint is clean on the changed files. Phase 3 remains recorded as **NOT MET**; MEM-002 and MEM-307 remain open; the GUI settings UI for the engine option is a documented follow-up (config.json only this slice).

## Approved decisions

- Use Python's standard `sqlite3` module and SQLite FTS5.
- Do not require a SQLite vector extension or a separate vector database.
- PIC approved on 2026-11-07 a narrow exception, and the user authorized its implementation on 2026-11-07. The code now stores optional local 1024-dimensional document vectors and can use them as a semantic candidate source. This does not accept Phase 3 and does not authorize embeddings, measurement, a holdout retry, or Phases 4–5.
- Allow optional, bounded LLM query expansion and reranking.
- Improve `search_memory` first. Decide automatic memory injection only after retrieval quality is measured.
- Preserve the current default sharing boundary: memories are scoped to an agent and may be recalled across sessions.
- Do not bind memories to a project or current working directory.
- Capture the current working directory when a memory is stored and show it in retrieval results as provenance.
- Do not use CWD as an eligibility filter or ranking boost in the first release.
- Preserve adaptive behaviors as a separate trusted mechanism. Recalled conversation memory is untrusted historical data and cannot grant permission.
- Create the implementation plan now. Implement the migration and benchmark only after this plan is approved.

### Semantic candidate source — historical implementation state (2026-11-07)

The user authorized this implementation slice on 2026-11-07 so Phase 3 can later be completed. The code now exists. Phase 3 remains rejected. MEM-307 and MEM-002 stay open. Embeddings, measurement, holdout retry, and Phase 4 remain unauthorized.

Historical 2026-11-07 description (superseded by the correction below; not the current query rule): The original prototype could store existing local 1024-dimensional document vectors in the optional `memory_document_vector` table and score them beside exact ID, entity, tag, and FTS. Schema version stays 1. Existing databases without the table remain valid. Vectors are copied only by an explicit local-vector copy step; documents are not re-embedded. No vector extension and no separate database. A semantic hit is not dropped only for missing the lexical overlap floor (`min_overlap` 0.35 and `relative_coverage` 0.7). The distance cutoff is `VOYAGE_RELEVANT_THRESHOLD` (1) from `voyageai_ef.py`: cosine distance greater than that cutoff is rejected, matching the Chroma baseline. Equal RRF scores break ties by that cosine distance before record ID; a missing semantic distance sorts after any finite in-cutoff distance. The benchmark can pass an already-cached 1024-float query vector into SQLite and copy already-stored local document vectors into a disposable candidate. This slice does not measure recall and does not close Phase 3.

The consumed holdout stays failed evidence and must not be reused. Conversation history subsequently reports a failed semantic calibration; this correction does not remeasure it or claim new private results. Further calibration and any fresh, semantically non-overlapping reviewed holdout require separate authorization. No calibration alone can pass Phase 3.


### Query-rule correction — fictional verification only (2026-11-09)

The approved correction qualifies signals **before RRF**, rather than treating raw vector-source membership as relevance. `SQLiteSemanticPolicy(max_distance=None)` is immutable and request-local; **unset disables semantic scanning and admission**, even with query vectors present. An explicit finite cosine-distance cutoff in `[0, 2]` is accepted as operator input, not as a calibration claim. There is no evidence-supported deployment cutoff yet. The verifier uses `0.2` only to separate hand-constructed fictional vectors; it is **not a recommended deployment setting**. SQLite no longer imports Chroma's `VOYAGE_RELEVANT_THRESHOLD`; the Chroma comparator, provider and model are unchanged.

An eligible explicit ID, lexical-qualified evidence, or independently qualified semantic evidence can admit a parent. Semantic-only paraphrases need no lexical overlap. Rejected semantic reasons/evidence are removed before coverage and scoring, so they cannot boost lexical results. Lexical evidence still uses the original `.35` minimum and `.7 * best` relative coverage gates, computed independently of semantic evidence. Unqualified lexical reasons cannot piggyback on a qualified semantic parent. No limit-filling, quotas, CWD scope/boost, new rank weights, parser changes, or production wiring were added. RRF `k=60`, kind `1.1x`, lexical field weights, normalized-document dedup and session/kind diversity remain. Semantic distances order the qualified semantic source before its cap; equal fused scores use existing diversity then stable parent/unit IDs, with **no blanket semantic-distance preference** over lexical-only hits. Semantic confidence remains conservative and is not factual certainty.

The optional plain vector table remains schema version 1. The semantic scan projects only one hard-eligible parent ID/vector pair, scores each once, computes the query norm once, and uses bounded top-candidate selection before loading any canonical/unit payload. Only selected parents hydrate one deterministically chosen evidence unit (outcome first, then ordinal/unit ID). All sources, hydration and conflicts share the operation-owned read snapshot. This is a structural payload correction, **not a measured latency or recall claim**.

Benchmark evidence now records `sqlite_semantic_policy` and `document_vector_cache_sha256`, including explicit `null` cache absence, in reports and frozen protocols. `--sqlite-semantic-max-distance` is the explicit, unset-by-default SQLite-only input. Cache bytes are fingerprinted and hash-checked while loading; changed/missing policy or cache evidence fails before backend setup and holdout reservation. Original SQLite source identity still binds vectors already inside that source. Old manifests lacking the new fields are rejected by the corrected harness; **do not regenerate a freeze or reuse consumed holdout to bypass that rejection**.

Focused offline checks use only disposable fictional data: orthogonal/weak-positive rejection, strong zero-overlap admission, abstention, rejected-signal score invariance, missing-vector/unset-policy lexical invariance, scope/date/current-session/deletion/supersession/validity/ID fences, CWD invariance, optional vectorless schema, deterministic cross-source ties and semantic ordering, one score per parent, one query norm, capped late hydration, concurrent writer/read-snapshot consistency, and evidence mutation/absence rejection without reserving any holdout. No committed labels or synthetic corpus were altered.

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_semantic_candidates.py
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_phase1.py
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_fts_payload.py
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_exact_lookup.py
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/validate_assets.py
```

The existing exact-lookup verifier prints incidental fictional timings; these are not calibration or performance evidence for this change. The full Phase 3 verifier is not run because it exercises ledger reservation and additional benchmark paths; only its input-evidence contract is checked separately with fresh fictional artifacts. No private corpus/cache/trace, existing consumed ledger, private measurement, threshold selection/tuning, embeddings, real freeze, holdout execution/retry, dependency installation, network, application startup, push/merge, rollout, or Phase 4 runs in this slice. Historical failed holdout evidence above is unchanged. **Phase 3, MEM-002 and MEM-307 remain BLOCKED/open; production stays Chroma, and Phases 4–5 remain unauthorized.** Architect artifact review is required before sign-off.


### Calibration-only provenance/loading — code-only review gate (2026-11-09)

The preflight blocker was a harness contract, not a measured calibration failure: the full loader required and validated both splits before `--split calibration` could select cases. The approved correction adds explicit `--dataset-scope calibration-only` routing before case parsing, using only a separate export, original corpus, split-specific local-human attestation and original parent **review manifest**. It never opens the combined cases or auto-generates an export/review. Shared strict validators preserve scope/date/lifecycle/labels, duplicate-question/CWD-pair rules, calibration minimums and all original full-cohort/holdout requirements. Hash-checked reads bind parsed bytes to recorded identities.

A parent SHA identifies provenance, not subset membership proof. A genuinely authorized human must separately attest all-and-only calibration membership/completeness and preservation of case bytes, labels and sanitization; the code cannot establish the truth of that attestation. Existence and provenance of any real export remain **unknown**. See [verification README: calibration-only loading/provenance correction](../verification/memory/README.md#calibration-only-loadingprovenance-correction--code-only-2026-11-09) for fields and the fictional-only verification command.

Reports distinguish export/split-review identities from original combined-case/parent-review identities and mark `dataset_scope: calibration-only`. Limited reports/datasets cannot enter freeze, holdout reservation or Phase 3 acceptance; future protocol support is deferred. Existing semantic qualification, lexical thresholds, RRF, candidate caps, Chroma comparator/provider/model and cache evidence bindings are unchanged.

This approval is source plus disposable fictional verification **only**. No private discovery/preparation or measurement is authorized in this slice; the prior bounded measurement authorization remains paused pending Architect source/provenance review. No cutoff selection, embeddings, real freeze, consumed holdout/ledger access, rollout or Phases 4–5. Historical evidence/receipts remain unchanged. Phase 3, MEM-002 and MEM-307 stay blocked/open; production stays Chroma.

### Confined extraction method — code/fictional milestone (2026-11-09)

The preparation dependency now has an explicit operator method in `scripts/extract_memory_calibration.py`, pending Architect/PIC safety review **before private execution**. It requires an identified expected source digest, no-follow read-only access, syntax-only top-level split routing with exact escaped-key handling/duplicate rejection, raw calibration byte/newline/order preservation, double-pass identity checks and private atomic no-replace output. The scanner constructs no heldout payload objects; scanning/hashing still necessarily touches heldout bytes and therefore requires exact boundary clearance. See the [verification README](../verification/memory/README.md#calibration-extractor-method--fictional-review-milestone-2026-11-09) for limits, trust boundaries and fictional verifier.

This source slice does not extract private data, authenticate parent lineage, supply genuine human review, register cutoffs, measure calibration, freeze, access a consumed holdout/ledger or alter production. A mechanical extraction receipt is never a completed split attestation. Existing strict loader/retrieval policies and historical evidence remain intact. The next milestone is direct method review; Phase 3 remains blocked/open and rollout/Phases 4–5 remain unauthorized.

### Accepted-original-review mechanical calibration governance (code-only)

The user accepted the original review as the trust anchor for mechanically derived **calibration-only** inputs, without a new human event. A distinct `mechanically-derived-calibration-export` acceptance record now dispatches through the same strict loader, preserving genuine split-human and full-cohort validation. It requires an out-of-band PIC-trusted acceptance digest, explicitly supplied extraction/execution evidence and the pinned original parent/reviewed historical derivation lineage. Caller-supplied digests are not independent authentication; the trusted operator/PIC must genuinely review the existing execution evidence. Current loader identity is distinct from historical `2610c828`/`fb3c4475…` derivation identity; no old receipt/pending packet is rewritten or marked human-reviewed.

Actual export/corpus bytes and strict calibration invariants are rechecked. Reports bind all identities, explicitly record no new human review, and cannot become full-cohort evidence by dropping provenance markers. Limited freeze/holdout/acceptance barriers remain; fresh-holdout support is separate. See the [contract/checklist](../verification/memory/README.md#accepted-original-review-mechanical-contract--code-only-milestone-2026-11-09) and disposable `verify_derived_calibration.py` verifier. This slice performs no private input loading, extraction repeat, calibration, cutoff registration, embeddings, real freeze, holdout/ledger access or rollout. Stop at Architect source review; Phase 3/MEM-002/MEM-307 remain blocked/open.

## Goals

1. Replace ChromaDB persistence and vector retrieval with SQLite-backed structured memory and FTS5 search.
2. Improve retrieval relevance for facts, decisions, preferences, constraints, episodes, corrections, and unresolved work.
3. Give the agent compact results with stable IDs, evidence, match reasons, dates, and source CWD.
4. Preserve agent isolation, current-session exclusion, forgetting, conversation deletion, prompt evolution, and memory lifecycle hooks.
5. Provide an idempotent migration script that preserves every readable Chroma memory and supports rollback.
6. Provide a benchmark job that compares Chroma, deterministic SQLite, and optionally LLM-assisted SQLite retrieval.
7. Validate retrieval and answer quality, not only database throughput.

## Non-goals

- Automatic memory injection in this delivery.
- Project-scoped memory filtering.
- A vector extension or a separate vector database. The 2026-11-07 amendment and 2026-11-09 correction allow only the isolated optional plain-table scorer described above, not production wiring or measurement.
- Replacing `ContextPersistenceService`, conversation history, prompt evolution, or tool-result summaries.
- Building a general knowledge graph or copying Mercury's complete belief model.
- Changing provider selection, model definitions, model pins, or model-selection policy.
- Making inferred memories authoritative or allowing memory to grant permissions.
- Reworking every application startup path or agent orchestration flow.

## Repository baseline

The original reviewed local snapshot is AgentCrew `main` at `e83a03b79e207d5e5d9a8b8f655e3fa9dbe61428`, with package version `0.23.2` declared in the repository dossier. Python 3.12+ is declared in `pyproject.toml`; the Python 3.8 statement in `CONTRIBUTING.md` is stale.

Phase 0 rechecked implementation baseline `a6c186fde41f7252fb311abd2af8e6d2abde87f1`. The memory service, worker, coordinator and setup have no intervening changes. Aggregate inventory, snapshot limitations, current metadata findings and synthetic fixture contracts are recorded in `verification/memory/README.md` and `verification/memory/baseline.json`. No private source paths or memory contents are committed. Architect approved the Phase 0 baseline and synthetic cohort for Phase 1. MEM-002 remains open for private, sanitized, locally reviewed real-memory cases before Phase 3 can pass or cutover can be authorized. Phase 1 reads no private memory.

AgentCrew uses service composition, async-generator streaming, singleton service/manager patterns, an EventBus, and module-per-tool registration. The memory migration must follow those existing patterns.

### Current memory path

1. `ApplicationSetup.setup_services()` constructs `ChromaMemoryService` and `ContextPersistenceService` in `AgentCrew/setup.py`.
2. `AgentMemoryCoordinator` extracts the latest user turn and assistant/tool interaction text.
3. `MemoryWorker` runs the `memory.store` before/after hooks, uses the memory LLM to create structured XML, and stores the result in Chroma.
4. A Chroma memory ID is currently based on `session_id + agent_name`; later turns update the accumulated session-level record.
5. Stored metadata currently includes `date`, `session_id`, `agent`, `type`, and optional `header`. Prompt evolution can add `evolved_at`.
6. `search_memory` calls `BaseMemoryService.retrieve_memory()` with query, date range, and agent name.
7. Chroma excludes the active session when `memory_service.session_id` is set, applies the agent/date filters, queries ten vector candidates, and returns at most three below a distance threshold.
8. `AgentContextManager` and `CursorAgent` inject recent memory headlines and tell the model to call `search_memory`; full memories are not automatically injected.
9. CLI, GUI, job, delegation, A2A, and ACP memory storage already converge on the agent memory coordinator and service contract.

### Current structured memory shape

The memory LLM produces:

- `HEAD`
- `DATE`
- `FLOW.INTENT`
- `FLOW.EXPLORATION`
- `FLOW.OUTCOME`
- `INSIGHTS`
- `ENTITIES`
- `DOMAINS`
- `RESOURCES`
- `CONVERSATION_NOTES`

The SQLite design should use these fields directly instead of flattening every record into one large text value.

### Existing constraints to preserve

- Memory remains optional and fail-open; a memory failure must not fail a user turn.
- `memory.store` before/after hooks remain supported.
- Tool registration continues through `AgentToolRegistrar`.
- The memory backend remains behind `BaseMemoryService`.
- Storage remains opt-in where the current runtime already gates it.
- Agent and session identifiers must be carried through the complete path.
- Forget-by-ID and conversation deletion remain physically effective.
- Prompt evolution corpus retrieval and `evolved_at` handling remain available.
- Do not add an `agentprocess` flag or an explicit external-user-turn signal.
- Do not rely on plugin initialization as the only activation path because headless modes do not all initialize plugins identically.

## CWD provenance design

### Required behavior

Capture the current working directory at the time a memory operation is created, not later in the background worker. This prevents a worker from recording a different process CWD if it changes before the queued operation runs.

Store the normalized absolute path as nullable provenance metadata:

```text
cwd = str(Path.cwd().resolve())
```

Retrieval should display:

```text
Source working directory: /home/user/source/project-a
```

For migrated records where the original CWD is unknown:

```text
Source working directory: Unknown (legacy memory)
```

### CWD rules

- CWD is informational provenance.
- CWD is not part of the hard eligibility predicate.
- CWD is not a default ranking feature.
- Memories from another CWD remain searchable by the same agent.
- Do not infer historical CWD from the directory where the migration command runs.
- Store the path once with the canonical memory record; retrieval units inherit it through their parent record.
- Include CWD in benchmark assertions so missing or incorrect provenance is visible.
- Treat the path as potentially sensitive in logs. Do not emit it to benchmark logs unless result details are requested.

### Accepted residual risk

Because memories are not project-scoped, the same agent can recall a relevant-looking memory from another repository. The result must show CWD, date, and headline so the agent can judge the source. The first release must not silently hide or boost results based on CWD.

## Target architecture

```text
AgentMemoryCoordinator
    -> backend-neutral MemoryRecordBuilder
    -> SQLiteMemoryService
         -> SQLiteMemoryRepository
         -> MemorySearchCoordinator
              -> scope eligibility
              -> exact/entity/tag candidates
              -> weighted FTS5 candidates
              -> optional query expansion
              -> deterministic rank fusion
              -> optional bounded reranking
              -> conflict/dedup/diversity
              -> structured results

search_memory tool
    -> MemorySearchCoordinator
    -> compact result formatter
    -> cited, untrusted memory evidence for the agent
```

### Components

#### `SQLiteMemoryService`

Implements `BaseMemoryService` and owns memory lifecycle operations:

- Store/upsert canonical memory.
- Load and clear session context.
- Search memories.
- List recent headers.
- Retrieve prompt-evolution corpus.
- Mark memories evolved.
- Forget by ID.
- Delete by conversation ID.
- Apply explicit retention when requested.
- Shut down owned workers and database resources.

#### `SQLiteMemoryRepository`

Owns SQL, transactions, schema migration, connection policy, and FTS maintenance. Other modules must not issue ad hoc SQL.

Operational requirements:

- WAL mode.
- A bounded busy timeout.
- Short transactions.
- Explicit connection ownership suitable for AgentCrew's worker thread and concurrent A2A/ACP sessions.
- Foreign keys enabled.
- Startup schema/version checks.
- No destructive collection-equivalent recreation during initialization.

#### `MemoryRecordBuilder`

Moves the backend-neutral extraction and structured-memory assembly behavior out of the Chroma-specific worker path. It should preserve:

- Existing prompts and XML parsing behavior.
- Current-session accumulated context.
- `memory.store` before/after hooks.
- Fallback memory creation when the LLM is unavailable.
- Consolidation decisions where still required.

Do not duplicate the full extraction flow in both Chroma and SQLite workers. During migration, either share the builder or keep the read-only SQLite prototype independent until the shared builder is introduced.

#### `MemorySearchCoordinator`

Owns retrieval policy independently of persistence details:

- Query normalization.
- Hard scope eligibility.
- Candidate generation.
- Deterministic rank fusion.
- Optional LLM expansion/reranking.
- Conflict resolution.
- Deduplication and diversity.
- Abstention.
- Structured result formatting data.

#### Structured search contract

Add non-breaking internal types such as:

```text
MemoryQuery
MemoryCandidate
MemorySearchHit
MemorySearchResult
```

Keep the current string-returning `retrieve_memory()` method as a compatibility adapter while Chroma and SQLite are compared. Do not break all `BaseMemoryService` consumers in the first slice.

## Retrieval-oriented SQLite model

Storage is not the main challenge, but retrieval needs a clear physical model.

### Canonical records

`memory_record` should preserve at least:

- `id`
- `agent_name`
- `session_id`
- `created_at`
- `updated_at`
- `cwd` nullable
- `header` nullable
- `status`
- `superseded_by` nullable
- `valid_from` nullable
- `valid_to` nullable
- `evolved_at` nullable
- `document_format`
- original structured document
- normalized structured JSON, if produced
- content checksum

Preserve the original Chroma ID and document exactly during migration. Do not rewrite IDs merely to fit a new schema.

### Searchable retrieval units

Derive smaller units linked to the canonical record:

| Source | Suggested kind |
|---|---|
| `HEAD` | headline |
| `FLOW.INTENT` | episode |
| `FLOW.EXPLORATION` | episode detail |
| `FLOW.OUTCOME` | decision or outcome |
| `INSIGHT` | fact or insight |
| actionable `CONVERSATION_NOTES.NOTE` | preference, constraint, correction, workaround, or unresolved |
| `ENTITY` | exact entity |
| `DOMAIN` | tag |
| `RESOURCE` | exact resource identifier |

Each unit should have:

- Stable unit ID.
- Parent memory ID.
- Kind.
- Searchable text.
- Display excerpt.
- Field/source name.
- Unit order.
- Optional validity/status inherited from the parent.
- Normalized checksum for deduplication.

### Exact-match support

Use separate entity/tag tables for identifiers that FTS tokenization may split:

- File names.
- Symbols.
- Model IDs.
- Provider names.
- URLs.
- Repository paths.
- Acronyms.

Examples include `task_manager.py`, `response_service`, `gpt-5.4`, `A2A`, and `OpenAI Codex`.

### FTS5 index

Create an FTS5 index over retrieval-unit text with field weighting for:

1. Header and exact entities.
2. Decisions, outcomes, corrections, and actionable notes.
3. Insights and constraints.
4. Intent and exploration.
5. General details.

Use a tokenizer suitable for Unicode text. Validate multilingual behavior with the benchmark set. FTS5 is lexical search; it must not be described as vector-semantic retrieval.

Perform a startup capability check for FTS5. Fail with a clear setup error if the runtime SQLite lacks FTS5 rather than silently falling back to low-quality broad `LIKE` scans.

## Retrieval pipeline

### 1. Validate and normalize the query

- Require a meaningful natural-language information need.
- Preserve exact technical identifiers and quoted phrases.
- Normalize Unicode and case for matching while retaining original display text.
- Parse date filters.
- Deduplicate repeated terms.
- Do not reduce the query to broad keywords only.

### 2. Apply hard eligibility before ranking

Required predicate:

```text
agent_name = current agent
session_id != current session
status = active
date within requested range
```

There is intentionally no CWD/project predicate.

The current mutable `memory_service.session_id` can be unsafe under concurrent protocol sessions. The target search contract should accept request-local session context from the caller when the relevant runtime already knows it. This must not be implemented as a global flag.

### 3. Generate deterministic candidates

Combine:

1. Exact memory-ID lookup.
2. Exact entity/alias lookup.
3. Tag/domain lookup.
4. Weighted FTS5 retrieval.
5. Date-aware recent retrieval only when the query asks for recent history.
6. Optional expanded-query FTS5 retrieval.

### 4. Optional bounded query expansion

Only run when the deterministic pass is weak or too sparse.

The configured memory LLM may return validated JSON containing:

- Up to three alternate search phrases.
- Extracted entities.
- Suggested memory kinds.
- Optional time hints.

Rules:

- At most one expansion call.
- Expansion may restate the search need but may not answer it.
- It may not invent memory IDs or facts.
- Malformed output is ignored.
- Retrieval continues deterministically if expansion fails.
- Do not change provider/model pins as part of this migration.

### 5. Merge and rank candidates

Use rank fusion rather than directly comparing raw exact-match and BM25 scores. Candidate signals may include:

- Exact ID/entity match.
- FTS rank.
- Match in a high-value structured field.
- Requested memory kind.
- Active validity.
- Recency when the subject is time-sensitive.
- Evidence/support metadata if available later.
- Duplicate-content penalty.

Use reciprocal-rank fusion or another deterministic method whose inputs can be logged and explained. Weights must be calibrated from AgentCrew benchmark data rather than copied from Mercury.

### 6. Resolve conflicts, duplicates, and diversity

- Exclude deleted memories.
- Exclude superseded units by default.
- Prefer a current active statement only when it addresses the same fact as an older one.
- Preserve older unique information.
- Return both active conflicts with a conflict marker.
- Deduplicate by normalized checksum.
- Limit repeated units from one canonical memory.
- Diversify equivalent results by kind and source session.

Physical forgetting remains distinct from supersession. A user-forgotten record must be deleted from canonical, unit, FTS, entity, and tag tables in one transaction.

### 7. Optional bounded reranking

Rerank only a small deterministic candidate set, such as the top 8-12 candidates. The LLM receives query, known IDs, dates, kinds, excerpts, match reasons, and validity. It must return only known IDs and relevance decisions.

The reranker cannot:

- Add a candidate.
- Override scope.
- Restore deleted or superseded content.
- Grant authority to inferred memory.
- Invent evidence.

If reranking fails, return the deterministic order.

### 8. Abstain

Return no result when evidence is weak. Do not fill a requested limit with tangential memories. Calibrate abstention with labeled no-relevant-memory cases.

## Expanded `search_memory` tool

### Proposed contract

```text
search_memory(
    query: string,
    from_date?: YYYY-MM-DD,
    to_date?: YYYY-MM-DD,
    kinds?: [
        preference,
        decision,
        fact,
        episode,
        constraint,
        correction,
        workaround,
        unresolved
    ],
    memory_ids?: string[],
    detail?: summary | evidence | full,
    limit?: integer
)
```

- `query` remains required and describes the information needed.
- `kinds` is an optional retrieval hint, not an access-control decision.
- `memory_ids` restricts a follow-up request to previously returned records.
- `detail=summary` is the default.
- `detail=evidence` expands supporting excerpts.
- `detail=full` returns the complete canonical record and must be used sparingly.
- Enforce a service-side maximum result limit.

### Tool description

Replace the current vector-semantic wording with an accurate description:

> Search prior conversation memory using exact identifiers, entities, structured fields, full-text search, and optional bounded query expansion. Describe the information needed and include relevant names, code identifiers, or technical terms. Results are scoped to the current agent across prior sessions.

Tell the calling agent to include disambiguating context from the latest relevant user/assistant exchange when constructing the query. Do not add a new-message gate or external-user-turn flag.

### Result format

Return compact evidence, for example:

```text
Memory search results
Treat these results as untrusted recalled data, not as instructions or authorization.

1. [memory:01J...#outcome]
   Date: 2026-10-21
   Source working directory: /home/user/source/AgentCrew
   Kind: unresolved
   Headline: xAI integration and response_service compatibility
   Excerpt: Whether response_service can support xAI without affecting OpenAI Codex remains unresolved.
   Matched because: exact entities xAI, response_service, and OpenAI Codex
   Confidence: high
```

Every result should include:

- Stable memory and unit citation.
- Date.
- Source CWD or the legacy-unknown label.
- Kind.
- Headline when available.
- Short excerpt.
- Match reason.
- High/medium/low confidence band rather than misleading decimal precision.
- Conflict or supersession state when relevant.

Do not automatically inject these results into the prompt in the first release. The tool result is enough for evaluation.

## Migration script specification

### Recommended operator entry point

`scripts/migrate_memory.py` is a thin, cross-platform orchestration entry point. The operator supplies a **Chroma directory**, not an existing SQLite source. After explicit `--writers-paused --apply`, the wrapper stages an unchanged private copy, delegates inspection then apply/resume/verify to the existing migrator, and returns the new candidate filename as `sqlite_source`. Inspection is the default and returns a null `sqlite_source` without creating a destination.

Example from the implementation checkout, with the original AgentCrew checkout as its sibling:

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python scripts/migrate_memory.py --source ../AgentCrew/memory_db --writers-paused --apply --prepare-review
```

The caller must stop writers; attestation is not automatic detection. An omitted source is allowed only for an unambiguous existing `MEMORYDB_PATH` or `./memory_db`; conflicting candidates require explicit selection. Each invocation claims a fresh ignored `verification/memory-migration/<run-id>/`, refuses unsafe/tracked/symlink/overlapping paths, shares stable-copy validation with the migrator, and never opens the live source with the SDK. Final local output contains aggregate counts and the candidate/review paths, never records or credentials. Error output exposes only the failing stage and static error code.

Optional review preparation delegates the existing `prepare-review` path after verified apply and generates **unreviewed proposals only** in a fresh ignored directory. No review flag is set true, no human attestation is created, no benchmark or remote call runs, and no production backend changes. The wrapper does not implement a second resume path: retain interrupted artifacts and use the low-level `--resume --apply --verify` flow with the exact matching code version. Fresh-run guards and code-bound checkpoints must not be bypassed.

Standalone `verification/memory/verify_migration_operator.py` verifies fictional inspect/apply/review, pause and path guards, source stability, strict parsing, static errors, and interrupted underlying resume. Phase 3 private acceptance and Phase 4 remain blocked. See `verification/memory/README.md` for the operator contract, layout and recovery command.

### Low-level file

`scripts/migrate_chroma_memory_to_sqlite.py`

### Purpose

Convert the existing Chroma `conversation` collection into the new SQLite canonical and retrieval schema without modifying the source database.

### Safety model

- Require AgentCrew to be stopped or memory writes to be paused.
- Copy the Chroma directory to an isolated migration working directory before opening it.
- Read from the copy, never the live source.
- Do not instantiate `ChromaMemoryService` for export because its initialization path can recreate an incompatible collection.
- Do not delete or update Chroma records.
- Default to inspection/dry-run behavior; require an explicit `--apply` action to write the destination.
- Refuse to overwrite an existing destination. `--resume` permits only matching compatible migration state; there is no force/replacement option.
- Use transactions and an idempotent migration ledger.
- Preserve raw IDs, documents, and metadata.
- Emit a manifest with counts and checksums.
- Never infer historical CWD.

### Proposed command

```bash
uv run python scripts/migrate_chroma_memory_to_sqlite.py \
  --source ./verification/memory/local/staged-source \
  --destination ./verification/memory/local/migrated.sqlite3 \
  --collection conversation \
  --work-dir ./verification/memory/local/migration-run \
  --apply \
  --verify
```

The implemented CLI requires source, destination and work directory to be non-overlapping paths inside the ignored private roots `verification/memory/local/` or `verification/memory-migration/`. Stage a source copy while writers are stopped/paused; do not pass the live database. The original example placed the destination inside its source and has been corrected above. The command above performs a new apply; omit `--apply --verify` for default inspection, then use the same work directory with `--apply --resume --verify` to apply its validated snapshot. See `verification/memory/README.md` for complete operational safeguards and rollback guidance.

### Proposed options

- `--source`: Chroma persistence directory.
- `--destination`: SQLite file.
- `--collection`: defaults to `conversation`.
- `--work-dir`: location for the isolated source copy and reports.
- `--dry-run`: inspect and report without writing; default behavior when `--apply` is absent.
- `--apply`: authorize destination writes.
- `--resume`: continue an interrupted idempotent migration.
- `--verify`: run count/checksum/relationship checks after migration.
- `--batch-size`: bound transaction size.
- `--report`: optional JSON report path.
- `--fail-on-parse-error`: strict mode for validation environments.

### Per-record migration flow

1. Read Chroma ID, document, and actual metadata.
2. Validate required agent/date metadata; preserve nullable or blank legacy session labels without inventing replacements.
3. Store the canonical record with the original ID and document.
4. Set CWD to `NULL` for legacy records.
5. Parse structured XML.
6. Derive typed retrieval units, entities, domains, and resources.
7. If parsing fails:
   - Keep the canonical record.
   - Create one fallback searchable body unit.
   - Record the parse failure in the report.
8. Populate FTS5 and exact-match tables.
9. Store source and destination checksums in the migration ledger.
10. Commit the batch.

### Migration verification

The script must verify:

- Source record count equals canonical migrated count, excluding only explicitly reported unreadable records.
- Every source ID is present in SQLite.
- Agent/session/date/header/evolved metadata is preserved.
- Original document checksums match.
- Every active canonical record has at least one searchable unit.
- Every unit points to a valid canonical record.
- FTS rows match searchable-unit rows.
- Entity/tag rows do not reference missing records.
- Legacy CWD is `NULL`, never the migration process CWD.
- Re-running with `--resume` is idempotent.
- A failed batch can be restarted without duplicate records.

### Rollback artifact

Keep:

- Original Chroma directory.
- Read-only migration copy.
- SQLite destination.
- Migration manifest.
- Source/destination checksum report.
- Tool version and source commit.

Do not remove Chroma until the benchmark, shadow period, and rollback gate are complete.

## Benchmark job specification

### Proposed file

`scripts/benchmark_memory_retrieval.py`

### Purpose

Compare retrieval relevance, safety, latency, token usage, and downstream answer quality across:

1. Existing Chroma retrieval.
2. Deterministic SQLite retrieval.
3. SQLite with bounded query expansion.
4. SQLite with bounded expansion and reranking, when enabled.

### Benchmark inputs

Use a versioned JSONL case file, proposed at:

`verification/memory/benchmark-cases.jsonl`

Each case should contain:

```json
{
  "schema_version": 1,
  "synthetic": true,
  "case_id": "synthetic-example",
  "categories": ["exact_identifier", "symbol"],
  "query": "Where is the response_service helper defined?",
  "agent_name": "SyntheticArchitect",
  "current_session_id": "synthetic-session-current",
  "current_cwd": "/synthetic/projects/borealis",
  "from_date": null,
  "to_date": null,
  "kinds": [],
  "expected_memory_ids": ["syn-mem-symbols"],
  "acceptable_memory_ids": [],
  "excluded_memory_ids": ["syn-mem-other-agent", "syn-mem-current-session", "syn-mem-deleted"],
  "expected_facts": ["task_manager.py contains response_service"],
  "expected_cwds": {"syn-mem-symbols": "/synthetic/projects/atlas"},
  "must_abstain": false,
  "notes": "Example schema; CWD is display-only and never scope/rank"
}
```

Keep real benchmark data local and sanitized. Do not commit secrets, credentials, private conversation bodies, or identifying paths. The benchmark's answer/expansion/reranking HTTP adapter requires explicit opt-in and is restricted to committed synthetic fixtures; private-memory LLM egress is not authorized. Configured query-embedding calls have a separate opt-in and require reviewed, sanitized query attestations. This phase made no real remote calls.

### Required case categories

- Exact entity and technical identifier.
- Natural-language paraphrase.
- Multilingual query and memory.
- Ambiguous follow-up.
- User preference.
- Architecture decision.
- Constraint or rejected approach.
- Unresolved issue.
- Correction and superseded fact.
- Conflicting active memories.
- Date-range filter.
- Current-session exclusion.
- Cross-agent exclusion.
- Same agent, different CWD retrieval.
- CWD display for new records.
- Unknown CWD display for migrated records.
- Forgotten/deleted memory.
- Prompt-injection-like memory text.
- No relevant memory.
- Delegation/transfer-related memory.
- Code symbol, file path, model ID, URL, and acronym.

### Benchmark modes

#### Retrieval-only mode

Measures candidate quality without an answering model:

- Run the same cases against each backend/mode.
- Record ordered IDs and unit IDs.
- Record match reasons and confidence bands.
- Disable warm caches or report warm/cold runs separately.
- Use deterministic model settings for assisted modes where supported.

#### End-to-end answer mode

Optional and separately reported:

- Give the same user question and retrieved evidence to the same configured model.
- Hold model, provider, prompt, result budget, and sampling settings constant.
- Measure answer correctness and citation quality.
- Do not mix retrieval improvement with a model change.

### Proposed command

```bash
uv run --no-project --no-config --offline --python "$EXISTING_PYTHON" python \
  scripts/benchmark_memory_retrieval.py run \
  --corpus verification/memory/synthetic-corpus.jsonl \
  --cases verification/memory/benchmark-cases.jsonl \
  --chroma-source "$SYNTHETIC_CHROMA" --sqlite-source "$SYNTHETIC_SQLITE" \
  --output "$UNUSED_PRIVATE_OUTPUT" --split synthetic \
  --modes chroma,sqlite,sqlite-expand,sqlite-rerank \
  --embedding-source synthetic-hash --adapter fake --model-call-budget 1000 \
  --k 5 --warmups 1 --repetitions 3
```

The example is an offline contract simulation only, not a configured Chroma relevance baseline. Inputs/output must be inside the allowed ignored, untracked private workspaces. For actual remote adapters, `--allow-remote-llm` is required and only frozen synthetic fixtures are allowed; configured query embeddings use a separate `--allow-remote-embeddings` gate. Private semantic review, genuine baseline/calibration and an untouched single-use holdout remain mandatory.

### Retrieval metrics

- Recall@1, Recall@3, and Recall@5.
- Precision@1, Precision@3, and Precision@5.
- Mean reciprocal rank.
- nDCG when graded relevance labels exist.
- Abstention precision and recall.
- Duplicate-result rate.
- Correct memory-kind rate.
- Conflict/supersession handling accuracy.
- Cross-agent leakage count.
- Current-session leakage count.
- Deleted-memory exposure count.
- CWD display accuracy.

### Agent-level metrics

- Answer correctness against expected facts.
- Correct identification of decided versus unresolved items.
- Citation accuracy.
- Unsupported-claim count.
- Prompt-injection compliance.
- Number of memory tool calls.
- Result tokens consumed.

### Operational metrics

- Search p50, p95, and maximum latency.
- Cold versus warm latency.
- SQLite busy/lock events.
- Query-expansion invocation rate.
- Reranking invocation rate.
- LLM input/output tokens and estimated cost.
- Result payload size.
- Parse/fallback rate.

### Benchmark outputs

Write both machine-readable and reviewable reports:

- `results.json`
- `summary.md`
- `failures.jsonl`
- Optional per-case trace files without raw sensitive content.

Every report should include:

- AgentCrew commit.
- Benchmark script version.
- SQLite version and FTS5 availability.
- Chroma version.
- Provider/model identifiers for assisted modes.
- Case-set checksum.
- Configuration and thresholds.
- Timestamp and machine/runtime details.

### Proposed acceptance gates

These are initial targets and must be calibrated after measuring the current Chroma baseline:

- Zero cross-agent leaks.
- Zero current-session leaks when exclusion is required.
- Zero deleted-memory exposure.
- Zero superseded memory presented as the current fact.
- SQLite Recall@5 meets or exceeds the measured Chroma baseline.
- SQLite Precision@5 does not regress by more than five percentage points.
- No-relevant-memory abstention reaches at least 95% on the labeled set.
- CWD display is correct for 100% of labeled new and legacy records.
- Deterministic local search targets p95 below 100 ms for the expected personal-memory corpus.
- Assisted-mode latency, model-call rate, tokens, and cost stay within an explicitly approved budget.
- End-to-end answers meet or exceed the Chroma baseline for correctness and attribution.

Averages cannot override a safety failure. Any scope leak, deleted-memory exposure, or permission escalation blocks cutover.

## Mercury lessons applied

The reviewed Mercury implementation uses SQLite FTS5, BM25, normalized query terms, evidence, status/supersession, bounded results, and owner-oriented memory. Useful ideas for AgentCrew are:

- FTS5 indexes maintained with database-level consistency.
- Exact/entity matching before general text search.
- Compact retrieval budgets.
- Stable IDs and evidence excerpts.
- Explicit status and correction/supersession links.
- Separation of stable behavior guidance from query-specific recalled facts.

Do not copy these assumptions:

- FTS5 is not semantic vector recall.
- A context/CWD column does not create an access boundary.
- Model extraction or classification is not always correct.
- Mercury's complete belief model is required for AgentCrew.
- Soft deletion matches AgentCrew's current physical forget semantics.

## Detailed implementation task list

### Phase 0 — Approval and baseline

- [x] **MEM-000: Approve this plan.**
  - Approved for phased execution; Architect reviews each phase before the next starts.
  - Confirm the phased scope and file names.
  - Confirm that automatic context injection remains deferred.
  - Confirm that CWD remains display-only provenance.
- [x] **MEM-001: Freeze a migration baseline.**
  - Aggregate baseline and reproducible collector: `verification/memory/`; private frozen backup and SDK working copy remain ignored.
  - Byte stability was verified during capture; writers must still be stopped/paused for the future migration.
  - Record AgentCrew commit, package version, Python version, Chroma version, and SQLite version.
  - Back up the live Chroma directory before any migration work.
  - Record current memory count and actual metadata keys.
- [ ] **MEM-002: Build the labeled benchmark set.**
  - Synthetic cohort complete: 25 fictional records, 29 cases and 27 required categories, with checksums and standalone validation.
  - Still open until the Phase 3/pre-cutover gate: local sanitized real-memory labels and their Architect review. Synthetic labels are PIC-approved; no real cases were invented or committed.
  - Create sanitized real cases and synthetic edge cases.
  - Label expected, acceptable, and excluded memory IDs.
  - Include all required categories from this document.

**Gate:** Architect reviewed and approved the synthetic cases and safety labels for Phase 1. MEM-002's real-case requirement is deferred to the Phase 3/pre-cutover gate, not waived.

### Phase 1 — Retrieval contract and read-only prototype

- [x] **MEM-100: Define structured retrieval types.**
  - Add query, candidate, hit, result, match-reason, and confidence-band types.
  - Keep existing `retrieve_memory()` compatibility.
- [x] **MEM-101: Define the SQLite schema and schema-version table.**
  - Include canonical records, units, entities, tags, FTS5, and migration ledger.
  - Include nullable CWD and lifecycle fields.
- [x] **MEM-102: Implement `SQLiteMemoryRepository`.**
  - Centralize SQL and connection ownership.
  - Enable WAL, foreign keys, busy timeout, and transactional deletion.
  - Add explicit FTS5 capability detection.
- [x] **MEM-103: Implement structured-record parsing and unit derivation.**
  - Parse current XML fields.
  - Preserve canonical input.
  - Fall back safely for malformed records.
  - Keep derivation deterministic and observable.
- [x] **MEM-104: Implement deterministic candidate generation.**
  - Hard agent/session/date/status eligibility.
  - Exact IDs, entities, tags, and weighted FTS5.
  - No CWD filtering or boost.
- [x] **MEM-105: Implement deterministic rank fusion and abstention.**
  - Deduplicate and diversify results.
  - Handle active, superseded, and conflicting records.
  - Produce explainable match reasons.
- [x] **MEM-106: Implement compact result formatting.**
  - Include IDs, date, CWD, kind, headline, excerpt, reason, confidence, and conflict status.
  - Mark recalled content as untrusted evidence.

**Gate:** Standalone `verification/memory/verify_phase1.py` passes the schema, parser/fallback, canonical preservation, rollback, FTS deletion, exact lookup, scope/date/status/validity, dedup, conflict, CWD, concurrency and formatter invariants. Synthetic labeled-ID checks pass 27/29; `syn-case-paraphrase` and `syn-case-constraint` abstain (lexical recall misses). These checks do not score semantic expected facts or constitute the formal Phase 3 benchmark. See `verification/memory/README.md` for defaults, limitations and commands. Architect PIC approved Phase 1. Production memory behavior remains unchanged; the Phase 2 review gate follows.

### Phase 2 — Migration script

- [x] **MEM-200: Implement `scripts/migrate_chroma_memory_to_sqlite.py`.**
  - Use an isolated source copy.
  - Default to dry-run.
  - Require explicit `--apply` for writes.
  - Preserve IDs, documents, and metadata.
  - Set historical CWD to `NULL`.
- [x] **MEM-201: Add idempotent batching and resume support.**
  - Use migration-ledger checksums.
  - Make interrupted batches restartable.
- [x] **MEM-202: Add migration verification.**
  - Counts, IDs, checksums, metadata, relationships, FTS rows, and CWD assertions.
- [x] **MEM-203: Add migration reports and rollback manifest.**
  - JSON report with warnings and parse failures.
  - Record source commit/tool versions.
- [x] **MEM-204: Validate against a disposable Chroma copy.**
  - Never point early validation at the live memory directory.
  - Verify re-run and resume behavior.

**Gate:** Standalone Phase 2 checks pass, including source stability, safe paths/symlinks, dry-run non-writing, strict-mode preflight, batch rollback, interruption/restart, metadata/NULL CWD preservation, ledger/checksum mismatch rejection and actual FK/FTS integrity. The retained Phase 0 frozen copy was migrated privately: 407/407 canonical records and ledger rows, 10,937 units/FTS document-size rows, zero unmappable records, one unsupported-XML fallback preserved. Blank legacy session strings are preserved rather than fabricated or discarded; query current-session input remains nonempty. Reports and rollback artifacts remain ignored/private. See `verification/memory/README.md` for commands, aggregate results and limitations. Phase 2 is PIC-approved. Phase 3 tooling is permitted, but its private review/baseline/holdout gates and MEM-002 must pass before further approval; cutover remains unauthorized.

### Phase 3 — Benchmark job

- [x] **MEM-300: Implement `scripts/benchmark_memory_retrieval.py`.**
  - Support Chroma, deterministic SQLite, expansion, and reranking modes.
  - Keep the same scope and date inputs across modes.
- [x] **MEM-301: Add JSONL benchmark-case loading and validation.**
  - Reject malformed or unsafe case definitions.
  - Check expected IDs exist in the selected corpus.
  - Require each private split to contain at least 15 cases, five distinct positively labeled memory IDs, and two abstention cases. Both splits need exact identifiers, paraphrases, no-relevant cases and ambiguous follow-ups; a decision/preference case; and a constraint/rejected-approach or unresolved case. Existing overall coverage and normalized-question uniqueness remain required.
- [x] **MEM-302: Implement retrieval metrics.**
  - Recall, precision, MRR, nDCG, abstention, duplicates, lifecycle correctness, and leaks.
- [x] **MEM-303: Implement performance and cost metrics.**
  - Latency, lock events, model calls, tokens, cost, and payload size.
- [x] **MEM-304: Implement result reports.**
  - JSON, Markdown summary, and failure JSONL.
  - Include full reproducibility metadata, including the exact review-manifest and query-embedding-cache SHA-256 values in loaded evidence, reports, calibration freezes and holdout checks. Cache evidence is null for non-cache sources. Changed attestations and mutated or replaced caches must fail before backend setup or holdout reservation.
- [x] **MEM-305: Add optional end-to-end answer evaluation.**
  - Same provider/model/prompt across backends.
  - Explicit remote-LLM opt-in.
  - Correctness and attribution scoring.
- [x] **MEM-306: Establish the Chroma baseline.**
  - Run before tuning SQLite.
  - Store baseline results with case-set checksum.
  - Architect/PIC approved MEM-306 closure on 2026-11-07: the primary CURRENT CONFIGURED VOYAGE-4 OPERATIONAL baseline is established on the retained reviewed 408-record cohort, not proven historical per-vector encoder reproduction or substitution of the current 410-record source. Synthetic hash embeddings verify the contract only, not this measured primary comparison.
  - **Primary comparator confirmed / cache verified (2026-11-07):** current configured Voyage-4 operational Chroma comparison on the retained reviewed 408-record cohort, with 1024-dimensional query vectors; historical per-vector encoder identity is not independently established. The explicitly authorized single batch sent only 16 distinct sanitized calibration query strings to Voyage, returned model `voyage-4` / 375 reported tokens, and produced a source/corpus/cases/review-bound cache. SDK and HTTP retries/redirects were disabled; one SDK call and one adapter send were observed, not an independently measured wire-request count. No documents or holdout queries were sent. Existing wrapper task/output settings and Voyage threshold 1 remain unchanged. The combined case file was structurally decoded in two selection passes; holdout content was not analyzed, exposed or executed. That cache-preparation slice constructed no benchmark backend and ran no retrieval, tuning, freeze or holdout. Subsequently authorized calibration is recorded below; cache preparation itself granted no holdout or additional remote-generation permission. See the README for hashes and exact boundaries.
  - **Optional reconstructed-default preparation verified (2026-11-07):** `comparator_kind=reconstructed_default_not_historical`. A fresh private collection contains the exact 408 nondeleted records from the approved 408-record corpus, re-embedded locally with the verified cached Chroma default `all-MiniLM-L6-v2` (384 dimensions), cosine and `ef_construction=200`. It is not the retained 1024-dimensional collection, the current 410-record source, or a historical Voyage reproduction; exact historical model/version remains unknown. No production data/provider changed. All ID/document/metadata checks passed; numerical vector verification and the initial strict-bitwise failure are documented in the verification README. Document preparation made 26 local embedding invocations for 408 documents; query embeddings, benchmarks, private case/holdout reads, network and downloads were zero. Preparation does not complete MEM-306; Architect review and separate calibration authorization are required.
  - **Local milestone 1 PIC-approved (2026-11-07):** unchanged-policy primary calibration used the approved 408-record cohort and Voyage-4 query cache, split calibration, k=5, one warmup and three repetitions, with 96 retrieval invocations per mode/run. One baseline and one matched postpatch run evaluated all 16 cases per mode with zero backend/busy/leak/duplicate/CWD/repeat failures and zero network/model/embedding calls. All 32 saved final case outputs (16 per mode) and complete quality/safety metric dictionaries matched across runs. FTS-only payload patch `424d9638922498754a66d5a1c08305206f27e58a` reduces materialized match columns from 29 to six and hydrates after the scoped parent cap in the same snapshot; actual committed-reference verification passed 140 candidate/coordinator comparisons, 19 concurrent comparisons, raw BM25/payload and snapshot checks, plus all 151 existing exact-lookup comparisons. SQLite warm p50/p95 changed 51.33/326.10 → 32.42/131.50 ms. The unchanged Chroma control p95 drifted 2.18 → 5.84 ms, cause unknown; this is fixed-order cached-vector evidence, not an interleaved experiment, live-API or disk-cold latency. Full metrics/hashes and limitations are in the verification README. No favorable rerun, threshold tuning, freeze or holdout was performed.
- [ ] **MEM-307: Tune and document SQLite thresholds.**
  - Milestone 3's ENTITY-only patch `3d56fe8` is PIC-approved (2026-11-07), including the +25.530092% warm-median regression against the p95 improvement on that one pair. This does not close this task. The later authorized sequence preserved the 16 calibration vectors, added 16 holdout vectors in a new cache, passed calibration gates, froze the unchanged policy, and ran holdout once. Architect/PIC rejected Phase 3 acceptance on 2026-11-07 because holdout failed Recall@5. No thresholds or policies changed, and the consumed holdout must not be rerun. No further speculative local optimization is approved.
  - Use benchmark evidence.
  - Do not copy Mercury or Chroma thresholds blindly.
  - Threshold tuning has not started. Phase 1 relevance policy remains unchanged; the authorized exact-lookup and FTS milestones are recorded, and the subsequently measured ASCII-only lexical milestone is PIC-approved (2026-11-07), not Phase 3 acceptance. Its paired p95 132.94→126.20 ms still fails the proposed <100 ms target. The later ENTITY pair meets that p95 bound on one calibration pair only and is PIC-approved with its median regression. The later new-cache calibration and single-use holdout are recorded below. Architect/PIC rejected Phase 3 acceptance on 2026-11-07. The measured primary operational comparison and FTS patch are PIC-approved, closing MEM-306 only. No holdout acceptance is asserted.

**Gate — BLOCKED:** The private cohort is finalized and strict-valid: 408 records, 32 reviewed cases (16 calibration / 16 holdout), seven positive memories and two abstentions per split. Architect adjudicated the AI-assisted proposals and the user explicitly approved their labels, sanitization and transparent local-human attestation. SQLite-only calibration and the subsequently authorized primary Voyage-4-cache Chroma comparison are measured; Architect/PIC approved MEM-306 and the matched FTS-only patch `424d963` on 2026-11-07 with measurement caveats; it still misses the proposed p95 target. The reconstructed-default comparator remains optional, and the synthetic hash-vector simulation is not relative real-memory evidence. The earlier PIC-approved exact-lookup performance-only patch localizes runtime changes to `SQLiteMemoryRepository.candidates`: exact entity/tag lookup starts at the existing normalized-value indexes, while scope-before-rank/limit, ties, reasons, payload, FTS, conflicts and connection ownership remain unchanged. The evidence reconciliation in `verification/memory/README.md` withdraws the earlier mixed/unattributed latency and handoff claims: the supplied combined/baseline/optimized hashes (`aebd09df…`, `7338a7b3…`, `36103954…`) were not found. No rerun or report rewrite was used to fill that gap. A different retained chain is hash-verified and internally consistent: combined `2d58c279b17596a3edc712e3abe73e5a4f5c56910b01ce49334cad7c28e838ea`, baseline `1031454fb4412ee0c81e3a38901d2db78b64a59a37d50eea30d282f0076f9a81`, optimized `2b2c625ad1fb06aef86c9d6faf948b66bdb59be73308c7ce45fa63e415fa34aa`. Those hashes identify the observations below, not the unavailable handoff artifacts. On **2026-11-06**, Architect/PIC approved `ed105f601debbe8634bee0357f841e633c4a1213` and documentation reconciliation `66fccf66624d642ca07ad24f3fb99eb7c2318bee` for the narrow exact entity/tag lookup scope only, accepting this separately retained chain as supporting evidence **with the unresolved historical attribution gap**, not as Phase 3 acceptance or a universal speedup. All six standalone verifiers passed (exit 0); the exact verifier passed 151 candidate/full-result comparisons and two index-led plans, and 24 independently captured SQL statements/bindings matched the actual committed baseline. No runtime fix was required. See the verification README for verification boundaries and caveats.

That retained chain records 16 calibration cases, k=5, one warmup and three repetitions, in fixed old-then-new order on separate consistent clones: 96 retrieval calls per variant (16 first calls + 16 warmups + 48 measured repetitions + 16 CWD probes), with 192/192 saved-baseline rank/citation/score/reason signature matches. Recorded quality/safety is unchanged: Recall@1 0.8571, Recall@3/@5 1.0, MRR 0.9286, graded nDCG 0.9473, fixed-five Precision@5 0.175, two correct abstentions, and zero observed leaks, duplicates, backend/busy errors, CWD failures or repeat changes. Warm p50 is **428.86→96.88 ms**, warm p95 **1237.77→655.52 ms** (48 samples per variant); first-call p95 is **1228.86→618.70 ms** (16 samples, not a controlled disk-cold measurement). Instrumented entity/tag/FTS `Cursor.execute` wall totals across all 96 retrieval phases are respectively **24475.99→731.62 ms**, **15485.42→159.18 ms**, and **15499.83→15702.58 ms**; these exclude separately measured fetch time and are neither percentiles nor inclusive coordinator totals. The README gives full hashes, fetch totals and instrumentation boundaries. No cProfile phase was found in this A/B harness. The definitions do not explain the unavailable handoff numbers; those remain unsupported.

Those earlier exact-lookup observations still miss the proposed <100 ms p95 target. In that earlier chain FTS SQL was unchanged/deferred and its execute total did not improve; the subsequent FTS-only milestone above is separate evidence. Fixed ordering, warmed validation/clones, instrumentation/EXPLAIN overhead, and previously reported fictional broad-term/tight-scope roughly 1–5% regressions remain caveats. The earlier evidence reconciliation changed no checked tasks, phase acceptance, source data, policy, schema/index, pooling/cache/provider setting, FTS or hydration behavior. The subsequent runtime milestones are separately scoped above: PIC-approved FTS-only hydration, ASCII-only normalizer, and ENTITY-only late hydration `3d56fe8` (2026-11-07), not Phase 3 acceptance. The ENTITY approval accepts the disclosed median regression and one-pair p95 result; it does not approve further speculative local optimization. MEM-306 is PIC-closed; MEM-307 and MEM-002 remain open. No further tuning, holdout retry, cutover, or Phases 4–5 execution is authorized. The one authorized freeze and single-use holdout already ran; Architect/PIC rejected Phase 3 acceptance on 2026-11-07.

### Phase 4 — Expanded search tool

- [x] **MEM-400: Update the `search_memory` definition.**
  - Add kinds, IDs, detail level, and bounded limit.
  - Replace inaccurate vector-semantic wording.
- [x] **MEM-401: Update the handler to use structured results.**
  - Preserve date validation and fail-open behavior.
  - Keep agent scope server-side rather than model-controlled.
- [x] **MEM-402: Add optional query expansion.**
  - One bounded call, validated JSON, deterministic fallback.
- [x] **MEM-403: Add optional reranking.**
  - Known candidates only.
  - No scope/lifecycle override.
- [x] **MEM-404: Add result expansion by known memory IDs.**
  - Summary, evidence, and full detail modes.
- [x] **MEM-405: Verify tool registration across runtime surfaces.**
  - Local interactive agents.
  - Jobs and delegation.
  - A2A.
  - ACP.
  - Cursor-agent path where memory is enabled.

**Gate — met under the 2026-11-12 PIC override (see Status):** Tool behavior is consistent across supported surfaces; all five registration surfaces bind the shared expanded definition and handler, and automatic headline injection remains unchanged. Verified by the disposable offline `verify_phase4.py` verifier plus the full existing verifier suite; the frozen measured retrieval policy and code hash are asserted unchanged. This closes the Phase 4 tool-layer items only: the default backend remains Chroma, MEM-002/MEM-307 stay open, and Phases 5–6 remain blocked.

### Phase 5 — SQLite write path

- [x] **MEM-500: Add CWD to memory operation data.**
  - Capture it synchronously when the store operation is created.
  - Pass it through hooks and persistence.
- [x] **MEM-501: Extract the backend-neutral `MemoryRecordBuilder`.**
  - Preserve current prompts, hooks, fallback, and session context.
  - Avoid duplicated Chroma/SQLite extraction logic.
- [x] **MEM-502: Implement `SQLiteMemoryService`.**
  - Fulfill the complete `BaseMemoryService` contract.
  - Preserve fail-open storage behavior.
- [x] **MEM-503: Implement SQLite header listing and session exclusion.**
  - Preserve current agent/date behavior.
  - Display CWD only in detailed retrieval results unless explicitly approved for headers.
- [x] **MEM-504: Implement corpus/evolution methods.**
  - Preserve prompt-evolution inputs and `evolved_at` behavior.
- [x] **MEM-505: Implement physical forget/delete transactions.**
  - Remove canonical, units, FTS, entities, and tags atomically.
- [x] **MEM-506: Implement explicit retention.**
  - Do not delete memories during database initialization.
  - Make retention a deliberate operation with observable results.
- [x] **MEM-507: Replace vector-based consolidation candidates.**
  - Use exact/entity/FTS candidates.
  - Keep consolidation bounded and benchmark its effect.
- [x] **MEM-508: Verify thread/concurrency ownership.**
  - Worker thread.
  - A2A concurrent tasks.
  - ACP concurrent sessions.
  - Shutdown and pending operations.

**Gate — met (2026-11-12, see Status):** New SQLite writes, reads, forgets, deletions and evolution operations pass standalone verification without Chroma (`verify_phase5.py`: the SQLite-only flows never import `chromadb`, and the full existing verifier suite passes on the branch). This closes the Phase 5 write-path items only: the user-visible default memory backend remains Chroma, MEM-002/MEM-307 stay open, and Phase 6/cutover and the default-backend switch remain blocked.

**Gate:** New SQLite writes, reads, forgets, deletions, and evolution operations pass standalone verification without Chroma.

### Phase 6 — Engine selection and cutover (redefined by PIC 2026-11-13)

- [ ] **MEM-600: Add an explicit shadow comparison mode.** — *Superseded by PIC decision 2026-11-13: the PIC accepted the recorded regressions and chose a selection-based cutover instead of a shadow soak; not implemented by design.*
- [ ] **MEM-601: Run a defined shadow period.** — *Superseded by PIC decision 2026-11-13.*
- [ ] **MEM-602: Review failures and retune.** — *Superseded by PIC decision 2026-11-13; the frozen lexical policy stays untuned.*
- [x] **MEM-603: Cut over setup composition to SQLite.** — Implemented as the `memory_engine` selection (`sqlite` default, `chromadb` optional) at the centralized service creation path in `AgentCrew/setup.py`; no plugin activation; the explicit rollback selection is the config switch itself.
- [x] **MEM-604: Verify every application mode.** — Console and GUI, jobs and delegation, A2A and ACP all receive the selected service from the centralized wiring (no per-surface construction); shutdown and cleanup work through the shared `BaseMemoryService` contract (both services implement `shutdown()`; the worker drains pending operations).
- [x] **MEM-605: Complete the rollback window.** — The Chroma collection stays untouched on disk; rollback means setting `memory_engine` back to `chromadb`, which requires installing the `chromadb` optional extra.
- [x] **MEM-606: Remove Chroma dependencies in a separate cleanup.** — Packaging part implemented now per PIC decision: `chromadb` and `voyageai` moved to the `chromadb` optional extra, ahead of the original rollback-expiry condition, with collection data retained.
- [x] **MEM-607 (new, PIC request 2026-11-13): Automatic first-run migration.** — When `memory_engine=sqlite` and no SQLite store exists, startup migrates existing Chroma data automatically using the verified migrator semantics (offline copy, frozen backup, idempotent resume, canonical-checksum verification, report, locks, no network, no embeddings, source untouched); missing chromadb with Chroma data present fails fast; migration failure aborts startup without silent fallback.

**Gate:** Met under the PIC redefinition: the selection-based cutover is wired and verified across application modes, the frozen retrieval policy is unchanged, and rollback stays available via the retained Chroma collection plus the config switch. The original shadow-mode gate (shadow metrics and rollback review sign-off) is superseded by the PIC decision recorded above.

### Phase 7 — Deferred automatic context injection

- [ ] **MEM-700: Decide whether automatic retrieval is needed.**
  - Use benchmark/tool telemetry and user experience evidence.
  - Do not assume every turn benefits from injected memory.
- [ ] **MEM-701: If approved, design a small high-confidence context bundle.**
  - Enforce a token budget.
  - Preserve the detailed `search_memory` follow-up path.
  - Use role/content gates already present in context assembly.
  - Keep memories as untrusted quoted evidence.
- [ ] **MEM-702: Benchmark auto-injection separately.**
  - Answer quality.
  - False-memory influence.
  - Prompt token spend.
  - Latency and cost.

This phase requires separate approval.

## Verification strategy

The repository's existing tests are treated as historical intent, not the primary acceptance mechanism for this migration. Use:

- Narrow `uv run python` syntax/import checks.
- Standalone migration verification.
- Standalone retrieval benchmark.
- Disposable SQLite and copied Chroma fixtures.
- Targeted runtime scripts for concurrency and lifecycle checks.
- Direct rereading of every changed file after implementation.

Do not claim old tests pass unless they are explicitly run and accepted for this work.

## Security and privacy requirements

- Apply agent/session/status/date eligibility before relevance ranking.
- The model cannot request another agent's scope through tool arguments.
- Retrieved memory is untrusted data, not a system instruction.
- Prompt-injection-like memory text must remain quoted and cannot alter tool or permission policy.
- Inferred preferences or facts cannot grant permission.
- User-forgotten memory is physically removed.
- Benchmark data stays local by default.
- Remote assisted benchmarks require explicit opt-in.
- Reports should prefer IDs, scores, and short sanitized excerpts over raw memory bodies.
- CWD is displayed as requested but should not be copied into broad logs by default.

## Operational risks and mitigations

| Risk | Mitigation |
|---|---|
| FTS5 misses paraphrases | Exact structured units, bounded query expansion, labeled paraphrase benchmark |
| LLM expansion/reranking is nondeterministic | Bounded calls, validated output, deterministic fallback, cost/latency telemetry |
| Same-agent cross-project memory is selected | Display CWD/date/headline; benchmark cross-CWD cases; no hidden filtering |
| Technical identifiers tokenize poorly | Exact entity/resource tables plus FTS |
| Migration loses or rewrites data | Preserve canonical records, IDs, metadata, checksums, reports, and source copy |
| Chroma initialization modifies the source | Migrate from an isolated copy without `ChromaMemoryService` initialization |
| SQLite locks under protocol concurrency | WAL, busy timeout, short transactions, explicit connection ownership, concurrency verification |
| Shared mutable session state leaks memory | Move retrieval session context into request-local query context in the relevant runtime paths |
| Memory prompt injection affects the agent | Mark results untrusted, quote excerpts, enforce scope and permission rules outside memory |
| Benchmark improves only synthetic cases | Mix sanitized real cases with synthetic edge cases and retain a frozen holdout set |
| Rollback misses post-cutover writes | Shadow/dual-write soak, manifests, and an explicit rollback procedure before Chroma removal |
| Scope expands into unrelated persistence | Keep `ContextPersistenceService`, history, and summaries unchanged |

## Deliverables

### This documentation slice

- `docs/sqlite-memory-migration-plan.md`

### Future implementation slice

Proposed primary files:

- `AgentCrew/modules/memory/sqlite_service.py`
- `AgentCrew/modules/memory/sqlite_repository.py`
- `AgentCrew/modules/memory/retrieval.py`
- `AgentCrew/modules/memory/types.py`
- A backend-neutral record builder module under `AgentCrew/modules/memory/`
- Updates to `AgentCrew/modules/memory/base_service.py`
- Updates to `AgentCrew/modules/memory/tool.py`
- Updates to `AgentCrew/modules/agents/memory_coordinator.py`
- Updates to `AgentCrew/setup.py`
- `scripts/migrate_chroma_memory_to_sqlite.py`
- `scripts/benchmark_memory_retrieval.py`
- `verification/memory/benchmark-cases.jsonl`
- Generated benchmark and migration reports under `verification/memory/`

Final file names may be adjusted during implementation review, but responsibilities must remain separated.

## Definition of done

The migration is complete only when:

- SQLite is the centrally composed memory backend.
- New memories record CWD at enqueue time and retrieval displays it.
- CWD does not restrict or boost retrieval.
- Agent and current-session boundaries are correct across all supported runtimes.
- `search_memory` returns compact, cited, explainable results.
- Deterministic search works without an LLM.
- Optional expansion/reranking is bounded and observable.
- Migration is idempotent, verified, and reversible.
- Benchmark results meet approved relevance, safety, latency, and cost gates.
- Forget/delete/evolution behavior is preserved.
- No automatic context injection was introduced without separate approval.
- Chroma is removed only after the rollback window closes.


## Fresh lexical holdout runner

The fresh exam has a separate entry point, `fresh-holdout`. It does not call `reserve_holdout` or `freeze_calibration`, and it refuses any path or manifest that identifies `benchmark-holdout-ledger`. User acceptance to proceed is not a witnessed semantic-non-overlap audit; a manifest claiming `semantic_nonoverlap_witnessed` is rejected. The runner requires a human-reviewed, sanitized, non-draft holdout and an unset SQLite semantic cutoff. It compares lexical SQLite with cached voyage-4 Chroma vectors and does not call an embedding provider. Backend setup must succeed before the fresh ledger is reserved, and that reservation is published atomically. A blocked result is not production approval. The current unreviewed draft is not a valid input.
