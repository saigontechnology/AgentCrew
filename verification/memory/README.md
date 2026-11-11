# Memory migration verification: Phases 0–3

Phases 0–2 are PIC-approved. The private cohort is reviewed and SQLite-only calibration is measured, but **Phase 3 is BLOCKED, not passed**: MEM-306 primary Voyage-4 operational calibration and bounded FTS payload patch `424d963` are PIC-approved (2026-11-07); the authorized single-use holdout failed Recall@5, and Architect/PIC rejected Phase 3 acceptance on 2026-11-07; the subsequent ENTITY-only patch `3d56fe8` is PIC-approved (2026-11-07), accepting warm median 27.326447→34.302914 ms (+25.530092%) and warm p95 114.810110→70.745843 ms on one calibration pair, not Phase 3 acceptance; the subsequent ASCII-only lexical patch `0558610` is PIC-approved (2026-11-07), not broad statistical speedup proof or Phase 3 acceptance. The narrow exact-lookup performance implementation and evidence reconciliation are PIC-approved (2026-11-06), not Phase 3 approval. **MEM-002 stays open; Phases 4–5 have not started and are not authorized; do not cut over.** No production setup, search tool, write path, automatic injection, provider or model pin has changed.

### Authorized Phase 3 sequence — PIC rejected Phase 3 acceptance (2026-11-07)

The user approved one Voyage-4 embedding batch for the 16 sanitized holdout queries and confirmed freezing the current policy only if the new-cache calibration passed. The new combined cache preserves the original 16 calibration vectors and adds 16 holdout vectors. It does not rewrite the old calibration cache. The comparison is the current configured Voyage-4 operational baseline on the retained 408-record cohort, not historical-encoder proof or the live 410-record source.

One Voyage `Client.embed` call used model `voyage-4` and the wrapper's omitted task, dimension, dtype, and normalization settings. Retries and redirects were disabled. The response reported model `voyage-4`, HTTP 200, and 397 tokens. Dollar cost was not returned. Counters were one SDK embedding call, one API-resource call, and one HTTP adapter send. No documents, labels, or calibration queries were sent. No second generation ran.

Calibration used the existing harness once, under network-namespace isolation: modes `chroma,sqlite`, split calibration, k=5, warmup=1, repetitions=3, and the new cache. It evaluated 16 cases per mode with zero embedding, model, or remote calls. All proposed gates passed. SQLite warm p50/p95 were 31.51/77.78 ms; Recall@5 was 1 and fixed Precision@5 was .175. Chroma cached-vector p95 was 5.16 ms. The harness acceptance status stayed blocked because this split was calibration, not holdout. The unchanged policy was then frozen against that exact report.

Holdout ran once through the harness and was consumed before execution. It must not be retried. SQLite warm p50/p95 were 30.41/64.66 ms, so the p95 gate passed. Precision@5 was .1625 versus Chroma .175, within 5 percentage points. Abstention, leak, duplicate, and backend-error gates passed. **Recall@5 failed: SQLite 0.9285714285714286 < Chroma 1.0.** Architect/PIC rejected Phase 3 acceptance on 2026-11-07. The sequence was valid and complete, but a later generic continue does not waive this gate, authorize a retry, or permit threshold tuning on the consumed holdout. Phase 3, MEM-307, and MEM-002 stay open. Phases 4–5 did not start.

### Semantic candidate amendment — historical implementation state (2026-11-07)

The user authorized this implementation slice on 2026-11-07. The code now exists in the SQLite repository and retrieval coordinator. Phase 3 remains rejected. Measurement, embeddings, freeze, holdout retry, and Phases 4–5 remain unauthorized.

Historical 2026-11-07 description (superseded by the correction below; not the current query rule): The original prototype could store existing local 1024-dimensional document vectors in optional table `memory_document_vector` and use them as one semantic candidate source beside exact ID, entity, tag, and FTS. Schema version stays 1. Existing databases without the table remain valid. No vector extension, no new embeddings, no document egress, and no separate vector database. A semantic hit is not discarded only for failing the lexical overlap floor. Cosine distance greater than frozen Chroma cutoff `VOYAGE_RELEVANT_THRESHOLD` (1) is rejected. Equal RRF scores break ties by that distance before record ID. The benchmark can pass a cached 1024-float query vector and copy already-stored local document vectors into a disposable SQLite candidate; a missing cache vector stays lexical. The consumed holdout remains sealed.

Fictional verification, with no private corpus:

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python verification/memory/verify_semantic_candidates.py
```

| Gate | Calibration | Holdout |
| --- | --- | --- |
| Zero scope/session/deletion/lifecycle leaks | pass | pass |
| Zero duplicate hits | pass | pass |
| Recall@5 >= Chroma | pass | **fail** |
| Precision@5 within 5 percentage points | pass | pass |
| No-relevant abstention >= 95% | pass | pass |
| SQLite warm p95 < 100 ms | pass | pass |
| Zero backend errors | pass | pass |

| Evidence | SHA-256 |
| --- | --- |
| Safe sequence aggregate | `c4c95e59049e4827e844a2e1a1668958c82b790597162cce876f7863ea8f37a2` |
| Combined query cache | `f6cb3feafb3cbcf26060d4535d117c50192a788c8bbc40e09862724aab86d0ed` |
| Preserved calibration-only cache | `594666c408277d34253d778421f229f6d48a1d46b5404b7f9b2dd8be04ad7d22` |
| Calibration report | `e902ffecb513a5836aaf00c4ccc79bfdea228cee15b1e1f102f1e9dfe5a360b1` |
| Freeze digest | `188a5aece2d547ab5bf3d671a1782d3319e4d7e4ff864c9d6bcb287b88b89626` |
| Holdout report | `791037a77a14c878f8fc399aa9e599ea228f2ad3e386af3f3fcf8deab0a05fca` |

These timings are cached-vector retrieval, not live API latency or controlled disk-cold measurements. Original corpus, cases, review, retained source, candidate database, old cache, and optional comparator hashes were unchanged. Production remains Chroma.


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


### Calibration-only loading/provenance correction — code-only (2026-11-09)

The earlier preflight correctly stopped before private input access: `--split calibration` selected cases only **after** the full-cohort loader had opened and validated both splits. The explicit `--dataset-scope calibration-only` route now calls `load_calibration_dataset()` **before case parsing**. It reads only the supplied calibration export, corpus, split-specific review, and parent **review manifest**. It does not open the original combined cases, follow paths mentioned in manifests, generate an export, or generate/upgrade an attestation. The default `full-cohort` route keeps all existing whole-cohort and holdout checks. Both routes share per-case validation and hash-checked snapshot reads, so parsed bytes and reported hashes cannot silently describe different versions.

**Real-input checklist for a later separately authorized operation:**

1. Supply the original reviewed corpus unchanged, a separate calibration-only JSONL export, its separately completed local-human split review, and the original reviewed parent manifest. Whether these real artifacts exist is **unknown**; this code-only slice did not inventory or prepare them.
2. A genuinely authorized human must attest that the export contains **all and only** the original calibration rows, with each case's bytes, labels, and query-sanitization state preserved. The tool does not establish the truth of this attestation or independently prove inclusion. Merely naming a parent SHA is **not cryptographic subset proof**. No heldout objects may be inspected to manufacture this export under the current permission.
3. The split review must bind the actual corpus/export bytes and original parent-review/combined-case identities. The supplied parent review must itself carry the existing `schema_version: 1`, `reviewed_locally: true`, `review_method: local-human`, `corpus_sha256`, and original combined `cases_sha256` fields. A calibration-only review cannot serve as its own full-cohort parent. No original review or labels are rewritten.
4. Every exported case must be private-schema-1, `split: calibration`, locally reviewed and non-draft, with valid scope/date/lifecycle/reference/CWD/provenance labels and boolean sanitization fields. Holdout/combined inputs are rejected, **not filtered**. Duplicate normalized questions and invalid CWD-pair references are rejected. Minimum coverage remains 15 cases, five positively labeled memory IDs, two abstentions; exact identifier, paraphrase, no-relevant-memory and ambiguous-followup categories; decision-or-preference plus rejected-constraint-or-unresolved coverage.
5. Use the explicit scope only with `--split calibration`, `--review-manifest "$SPLIT_REVIEW"`, and `--parent-review-manifest "$PARENT_REVIEW"`. `--cases` denotes the separate export, never the combined cohort. No freeze manifest is accepted in this mode. This describes the input contract, **not permission to run it now**.

Illustrative split-review shape only — the placeholders are intentionally invalid hashes, not a usable review or human attestation:

```json
{
  "schema_version": 1,
  "artifact_type": "reviewed-calibration-export",
  "dataset_scope": "calibration-only",
  "split": "calibration",
  "reviewed_locally": true,
  "review_method": "local-human",
  "membership_reviewed_locally": true,
  "calibration_rows_complete": true,
  "case_bytes_preserved": true,
  "labels_and_sanitization_preserved": true,
  "case_count": 16,
  "corpus_sha256": "<actual unchanged corpus SHA-256>",
  "cases_sha256": "<actual calibration-export SHA-256>",
  "parent_review_manifest_sha256": "<actual original reviewed manifest SHA-256>",
  "parent_cases_sha256": "<original combined-case SHA-256 from parent review>"
}
```

The report's `dataset_scope` is `calibration-only`; its `cases_sha256` and `calibration_export_sha256` identify the **actual export**, never impersonate the combined cohort. `review_manifest_sha256` identifies the split review; `parent_review_manifest_sha256` and `parent_cases_sha256` identify the original provenance. Parent/split-review mutations after loading fail before backend setup. Existing query-cache, document-vector-cache and explicit SQLite semantic-policy binding remain intact. These source files are already included in `code_identity()`.

Limited datasets/reports cannot enter holdout reservation, freeze creation/protocol, or Phase 3 acceptance, even if someone changes the requested split. Supporting a future freeze/holdout protocol for this format is deliberately deferred, not silently inferred from an old freeze. This does not authorize a consumed holdout retry or close Phase 3.

Run only the disposable fictional verifier (no benchmark, model, embedding, private inputs, freeze, or ledger operations):

```bash
unshare --user --map-root-user --net env -i PATH="$PATH" HOME="$HOME" UV_OFFLINE=1 uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_calibration_loader.py
```

It checks genuine-shaped **fictional** split provenance, no combined-case opening on the explicit route, malformed/missing review fields, wrong hashes, snapshot mutation, strict case/split coverage, rejection before mocked backends/reservation, freeze/acceptance barriers and existing full-cohort validation. Tests validate contracts, not real human-review authenticity. No real input discovery/preparation, private measurement, cutoff-grid registration/selection, embedding generation, real freeze, holdout/ledger access, startup, dependencies, rollout, or Phases 4–5 occurred. Retrieval/provider/model/policy defaults are unchanged. **Architect must review this source slice before any private-data work; the earlier bounded calibration authorization remains paused.** Phase 3, MEM-002 and MEM-307 stay blocked/open; production remains Chroma and the consumed holdout remains sealed.

### Calibration extractor method — fictional review milestone (2026-11-09)

`scripts/extract_memory_calibration.py` is a separate explicit-input operator, not a benchmark or an attestation generator. **This milestone authorizes code and fictional verification only. Architect/PIC must review the method and clear the exact combined-input boundary before any private execution.** No original combined cases, private receipts, source corpus, cache, ledger, freeze or database were opened for this implementation slice.

Method contract:

- One explicitly supplied regular source and a mandatory lowercase SHA-256 expected identity. Open every source path component using `dir_fd`/`O_NOFOLLOW`; hold one read-only descriptor. Verify the entire digest **before routing**. Pin device/inode/size/mtime/ctime, repeat the digest while routing, and check descriptor plus current path identity at pass boundaries and before publication. The supplied digest's authentic lineage is an operator/PIC responsibility, not established by this program.
- Two complete source reads necessarily include **heldout bytes**. This is not zero-holdout-byte access. A bounded recursive byte scanner validates JSON grammar, UTF-8, Unicode escapes/surrogate pairs, nesting and all object-key uniqueness. Per-object canonical key digests are ephemeral structural state, not retained case objects. Exact decoded scalar comparisons recognize only the root `split` key and `calibration`/`holdout` values. Nested split fields, escaped key spellings, quoted text and duplicate keys cannot supply an alternative discriminator. No regex/substring-based routing, `json.loads`, complete value-string decoding, payload dictionaries/lists, numeric conversion, or question/label semantics are used to route rows.
- Exactly one JSON object per physical line. Preserve every calibration line's original bytes, LF/CRLF/no-final-newline and order. Skip heldout raw lines without constructing payload objects or persisting their contents, IDs, labels, per-row digests or offsets. Receipt contains whole-source/export identities and aggregate split counts only. Calibration case validation and genuinely completed human review remain separate requirements; neither original labels nor the strict loader are modified.
- Fail closed on missing/unknown/non-string split, duplicate keys (including escaped-equivalent nested keys), malformed syntax/UTF-8, unpaired surrogate escapes, blank/BOM-prefixed input, identity drift, empty/single-split sources or limits. Fixed safety limits: 64 MiB source, 1 MiB physical row, 100,000 rows, nesting depth 64. Numbers are syntax-checked only; case semantics are not validated here.
- Write selected bytes only into an operator-owned private staging directory (`0700`/`0600`) under an ignored output parent. Publish export and evidence together using Linux `renameat2(RENAME_NOREPLACE)`, with no overwrite/cross-platform fallback. Normal failures and interrupts clean task staging; process kill/power loss can leave a restricted hidden staging directory but never an intentionally published partial final bundle. This is atomic publication, not a forensic-erasure or crash-durability guarantee. Output-parent ownership and read-only source isolation are required; this is not a sandbox against a malicious owner concurrently replacing output directories.

The resulting `mechanical-calibration-extraction-evidence` is **not** a split review: `reviewed_locally=false`, `review_method=null`, `valid_split_review=false`. It binds the method and shared validation source digests as well as actual source/export identities. The extractor source is also included in benchmark `code_identity()`. No reviewed Dataset, genuine human-review claim, acceptable attestation, freeze or acceptance status is manufactured. Original source/review/labels remain unchanged. A mechanically selected subsequence plus parent hash does not prove human authenticity, semantic non-overlap, or Phase 3 acceptance.

Run only disposable fictional verification:

```bash
unshare --user --map-root-user --net env -i PATH="$PATH" HOME="$HOME" UV_OFFLINE=1 uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -S verification/memory/verify_calibration_extractor.py
```

Checks exercise the actual operator, mixed byte/newline preservation, exact escaped/nested routing, 44 malformed/ambiguous examples, 500 concurrent generated fictional examples, no payload-deserializer use, required digest before routing, mutation/replacement across passes, no-follow/FIFO/size/depth/row limits, no-replace publication, permission modes, and cleanup after partial staging. Incidental execution durations are not retrieval or calibration evidence. Existing focused loader/retrieval checks remain applicable; the full Phase 3 main is not run.

API/grammar references (no network lookup required for this offline slice): [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259), [Python `os.open`/`dir_fd`](https://docs.python.org/3/library/os.html#os.open), [Linux `renameat2`](https://man7.org/linux/man-pages/man2/rename.2.html). The scanner deliberately rejects lone surrogate escapes and duplicate keys rather than accepting JSON interoperability ambiguities.

**Stop at Architect method review.** No private extraction/preparation, calibration/cutoff grid, embedding, real freeze, heldout execution/retry, ledger access, rollout, or Phases 4–5 occurred. A genuinely reviewed calibration export and later fresh, reviewed, semantically non-overlapping holdout remain gates. Production stays Chroma, semantic cutoff stays unset, and Phase 3/MEM-002/MEM-307 stay blocked/open.

### Accepted-original-review mechanical contract — code-only milestone (2026-11-09)

The user explicitly approved a truthful alternative for **calibration only**: accept the original human-reviewed cohort as the trust anchor and rely on its reviewed mechanical subset derivation, **without claiming a new human review**. `load_calibration_dataset` now dispatches by an explicit artifact type. The existing `reviewed-calibration-export` split-human-review contract and full-cohort per-case/holdout checks remain supported. No existing pending packet, original manifest, execution receipt, historical report or consumed freeze is rewritten or relabeled.

The new `mechanically-derived-calibration-export` contract is deliberately narrow:

1. Policy `accepted-original-review-mechanical-subset-2610c828-v1` pins the original parent review's already-published identity (`Finalized local-human review manifest` below), approved derivation commit `2610c82885bd33085f55b3712d23fc20fbaa1113`, code identity `fb3c44757827f959dcbfa7d3cc0bd49a058c4363a9ea4163b6166df527bcf214`, method source, historical shared sources and complete historical source bundle. A new parent or method requires an explicit reviewed policy change, not caller-selected replacement hashes.
2. Supply a **new, separately PIC-reviewed acceptance record** as `--review-manifest`, plus `--trusted-derivation-sha256` obtained from that trusted review **out of band**. Do not hash an unchecked candidate and call it trusted. This parameter is a trust input, not a signature or independent PIC authentication. The loader cannot distinguish an honest trusted operator from one intentionally blessing forged receipts. User acceptance of the original review is a governance assumption, not newly established human authenticity.
3. Explicitly supply `--derivation-extraction-evidence` and `--derivation-execution-evidence` in addition to corpus, calibration-only export and original `--parent-review-manifest`. No referenced path is followed automatically. Bounded hash-checked JSON snapshots bind each extra artifact; actual corpus/export bytes are checked by the shared strict validators. Missing, changed, malformed, wrong-role or inconsistent evidence fails before backend setup/reservation. Original combined cases, caches and ledgers are never needed for this path.
4. Operator evidence must declare a completed two-pass source/export identity check, both positive split counts, matching export count, correct historical method/shared identities, structural heldout-byte access, no heldout payload objects, and explicitly unreviewed mechanical flags. Execution evidence must match source/export/corpus/parent/method identities, reviewed historical code bundle, exactly one completed operator invocation, strict preparation validation, read-only/isolation declarations, preservation declarations and zero forbidden operational counters. The current loader independently repeats strict validation **only on the supplied calibration export/corpus**; it does not re-execute or cryptographically prove the recorded extraction. Trusted PIC review of the existing execution transcript is essential.
5. Historical operator wording that human review was still required and historical pending packets remain unchanged. This *new* acceptance record states the separately approved alternative; it must not contain the legacy split-human membership/completeness/preservation confirmation fields. It explicitly has `reviewed_locally=false`, `review_method=null`, `new_split_human_review=false`, `draft=false`, and `original_review_trust_anchor_accepted=true`. `draft=false` means the mechanical acceptance record is complete, not that a human reviewed new cases. No acceptance-record generator or authority impersonation is added.

Illustrative record shape only (placeholders are intentionally invalid hashes, **not** a usable attestation or private preparation result):

```json
{
  "schema_version": 1,
  "artifact_type": "mechanically-derived-calibration-export",
  "dataset_scope": "calibration-only",
  "split": "calibration",
  "derivation_policy": "accepted-original-review-mechanical-subset-2610c828-v1",
  "original_review_trust_anchor_accepted": true,
  "new_split_human_review": false,
  "reviewed_locally": false,
  "review_method": null,
  "draft": false,
  "case_count": 16,
  "corpus_sha256": "<actual unchanged corpus hash>",
  "cases_sha256": "<actual calibration export hash>",
  "parent_review_manifest_sha256": "<fixed published accepted parent hash>",
  "parent_cases_sha256": "<original combined identity from parent>",
  "extraction_evidence_sha256": "<reviewed extraction receipt hash>",
  "execution_evidence_sha256": "<reviewed execution receipt hash>"
}
```

The same `--dataset-scope calibration-only --split calibration` route is used. Human split review rejects unused mechanical inputs, as does full-cohort mode. All existing calibration minima (15 cases, five positive IDs, two abstentions), category combinations, reviewed/non-draft original labels, privacy/schema, scope/date/lifecycle/references/CWD checks, duplicate IDs and normalized questions remain strict. A mixed/heldout row is rejected, never filtered. No reviewed Dataset is fabricated to skip these controls.

Reports separately bind the actual acceptance/export/parent identities and `calibration_derivation` (trusted acceptance digest, extraction/execution digests, historical commit/code/method/shared identities, policy, `new_split_human_review=false`, explicit trust limitation). The current benchmark `code_hash` is independently current; changed loader source does **not** require rewriting historical execution/benchmark.py hashes. Pre-backend evidence checks detect changes to supplied acceptance/parent/derivation artifacts. Full-cohort report/protocol evidence now requires explicit positive scope/kind markers and all calibration fields present-and-null: deleting provenance does not silently grant full-cohort status. Old manifests missing these markers are not silently upgraded. Derived calibration remains barred from freeze, holdout reservation and Phase 3 acceptance regardless of split/scope marker edits. No limited-input/fresh-holdout protocol has been added.

Fictional verifier: `verification/memory/verify_derived_calibration.py` reuses the existing fixture/strict-validation checks, checks unchanged raw case bytes/labels, accepted chain versus missing/wrong-role/hash/method/count/parent/evidence failures, snapshot and post-load mutation, invalid private cases, CLI input binding, historical/current identity separation, and barriers before mocked backends/reservation. Existing human/full-cohort/extractor/retrieval checks remain required. All source and fixtures are disposable/public; no private Milestone B artifacts are read or copied by this implementation slice. No extraction repeat, real freeze, ledger, calibration, embeddings, cutoff registration or quality/performance claim occurs.

**Stop for Architect source review.** No private derived-loader pass or completed Phase 3 is claimed. The next private validation and any bounded cached-vector calibration resumption require the coordinating Architect's reviewed execution slice. Fresh one-use semantically non-overlapping human-reviewed holdout requirements, unchanged acceptance gates, consumed-holdout prohibition, production Chroma, unset SQLite cutoff, open Phase 3/MEM-002/MEM-307 and unauthorized rollout/Phases 4–5 are unchanged.

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

Every real case must explicitly cease being a draft, be reviewed locally, and match a separate `local-human` attestation bound to corpus/case hashes. The loaded dataset retains the exact SHA-256 of the validated attestation bytes. Until review is complete the loader rejects the cohort **before backend execution**. No review is auto-attested. Reading a retained copy for migration is not relevance evidence. SQLite-only calibration is recorded below. The configured Chroma comparison was later run from the combined Voyage-4 cache. The single-use holdout failed its Recall@5 gate, and Architect/PIC rejected Phase 3 acceptance on 2026-11-07; no threshold tuning occurred.

After genuine calibration, `freeze --calibration-report ... --output ...` seals code, policy, dataset/backend fingerprints, embedding identity and k/warmup/repeat protocol. Holdout requires unchanged inputs, both baselines and a matching freeze. Its locked, persistent ledger consumes each normalized held-out question/scope input—not freeze timestamps or labels. Renaming cases, changing CWD, relabeling, regenerating a freeze, or partial question recycling cannot unlock consumed inputs. Markers are consumed before execution; corrections require a fresh holdout or invalidation, never erasing history. Hashes do not prove semantic novelty or honest review, and this local guard cannot prevent a malicious owner moving/deleting the workspace.

Coverage is required **within each calibration and holdout split**, not just across the cohort: at least 15 cases, five distinct positively labeled memory IDs (expected or acceptable), and two abstention cases. Each split must cover `exact_identifier`, `paraphrase`, `no_relevant_memory`, and `ambiguous_followup`; at least one of `decision`/`preference`; and at least one of `constraint_rejected_approach`/`unresolved`. Existing overall coverage and distinct normalized-question requirements still apply. These checks do not edit or attest the private draft.

Reports, calibration freezes and holdout checks bind `review_manifest_sha256` and `embedding_cache_sha256` to exact input bytes. The cache hash is explicitly null for non-cache sources, and synthetic runs have no review-attestation hash. Altered attestations and mutated or replaced caches are rejected before backend setup or holdout reservation. The cache rows used by the backend come from the checksum-validated snapshot, not a later reread of the mutable input file.

### Local milestone 3 — ENTITY-only late hydration (2026-11-07)

PIC approved `0558610` before this runtime slice; MEM-306 closure and the FTS approval remain intact. One low-overhead SQLite-only diagnostic pass retrieved the approved 16 calibration cases on a disposable copy under network namespace/audit isolation. It used no cProfile or extra normalization pass: lexical classification observes the NFD result already produced. Native execute totals were ENTITY 111.69 ms, TAG 23.98 ms, FTS 193.15 ms and conflict 8.27 ms; ASCII lexical work was 26.72 ms/884 calls and Unicode 140.09 ms/244 calls. In the three slowest instrumented operations, ENTITY execute totaled 66.89 ms versus FTS 57.61 ms. These diagnostics include instrumentation and overlapping phases, not acceptance samples.

A fictional reproduction against the actual committed repository showed 2,496 eligible ENTITY hits and 29→6 materialized columns. Three fixed-order samples observed mean execute 515.71→27.40 ms, with identical selected fields/scores; tight-scope and empty-hit samples also improved. This justified **one ENTITY-only correction** in `3d56fe8`: reuse the approved late-hydration path, keeping eligibility/index joins before parent ranking/cap and full hydration/conflicts in the same operation-owned snapshot. FTS, TAG and MEMORY_ID SQL/bindings, lexical behavior, schema/index/tokenizer/weights/limits/validation and all policy knobs are unchanged. There is no cache/pool or service rollout.

Exactly one matched before/after calibration pair used clean committed code, fresh copies, the same 16 cases/cache, k=5, warmup=1, repetitions=3 and first-call/CWD probes. Chroma precedes SQLite and baseline precedes patch. Each backend/run made 96 retrieval invocations (48 measured); network/model/embedding/remote API events were zero.

| Backend | Warm p50 before→after (ms) | Warm p95 before→after (ms) | First-call p95 before→after (ms) |
| --- | ---: | ---: | ---: |
| SQLite | 27.33→34.30 | 114.81→70.75 | 121.32→73.61 |
| Chroma cached-vector control | 3.49→4.04 | 5.53→5.86 | 7.19→8.90 |

**The SQLite median regressed 25.530092%, from 27.326447 to 34.302914 ms.** Warm p95 changed 114.810110→70.745843 ms and first-call p95 changed 121.315279→73.610382 ms. Architect/PIC approved `3d56fe867a03744e7c6509c61464daaea32f26c8` on 2026-11-07 and accepted this fixed-pair trade-off. It meets the proposed <100 ms p95 bound on this calibration pair only. It is not statistical proof, live-API latency, disk-cold measurement, or an unqualified speedup. The unchanged Chroma control also varies (warm p50 3.490146→4.042012 ms, warm p95 5.526416→5.859807 ms, first-call p95 7.192401→8.903208 ms); cause is unknown. No rerun was selected for a better number, and no sample is pooled with prior milestones. These are cached-vector retrieval timings, not live Voyage latency or controlled disk-cold measurements.

All 32 saved final hit arrays/per-case metric dictionaries and non-timing mode metrics match exactly. This is not a claim that every intermediate call's output was persisted. Both backends retain Recall@5=1 and fixed Precision@5=.175; all previous ranking/abstention/diagnostic values remain unchanged. Two Chroma abstention quality failures/run remain in the private failure artifacts, not misclassified as backend errors. Backend/busy/leak/duplicate/repeat/CWD events are zero. All seven proposed calibration predicates are true, but overall acceptance remains false: no final-cache freeze or held-out evaluation occurred.

The focused verifier retains its FTS/lexical/mutation checks and adds **120 current-commit ENTITY candidate/coordinator comparisons**, **19 concurrent ENTITY comparisons**, **12 raw SQL/payload/score checks**, six-column materialization, source-detail/unit ties, before-cap scope, and a concurrent-writer ENTITY hydration/conflict snapshot check. It pins actual `b6d262d` source `7533666b…`, not a rewritten old oracle. All existing 151 exact-lookup comparisons and two index-led plans pass. Old exact-index microtimings are mixed and are not this patch's before/after timing evidence.

Two diagnostic helper preflights stopped before retrieval (import/API-shape errors); one had validated the combined case file. Fictional helper setup/SQL-construction preflights and a calibration-wrapper identity-check ordering error are also preserved. The latter stopped before benchmark execution; the actual pair ran once in a fresh pair directory. Together, profiling and the actual pair performed four full-loader passes: 128 object validations, including 64 validation occurrences for the same 16 held-out objects. Only calibration cases were retrieved/scored: 16 diagnostic plus 384 matched-run invocations. No held-out semantic evaluation, transmission, ledger/reservation, labels/sanitization changes or freeze occurred.

| Inspectable evidence | SHA-256 |
| --- | --- |
| Safe milestone aggregate | `c6772c06baddf3f77f5456166491d6ad7650f76767b5e33764bc00bb55cee091` |
| Baseline raw aggregate | `7e34d4ae0b1c703e872ccfded364adc53458ccc52832435016686a9fb57babbd` |
| Postpatch raw aggregate | `4166213ad6a91863887b8bb785a4a7e5f8afc324bfac0dab7b94166eea832270` |
| Before→after seven-file code identity | `8db3323128c89b7650c4ed6ccefa024f1a0645155a8ff32c4c705ff009dfa583` → `e080056682dd617f04e9a1e2505201944ae4cf607966a8f32d8f8be6e032faf9` |
| Postpatch repository source | `19be89d14a34af4ec47736d64c4fb54ea84131d7fa0c1a3bb95393a8da6d9e34` |

Original source/current candidate state, approved corpus/cases/review/cache, optional comparator and prior milestone receipts remain conserved. The historical empty-WAL gap remains unexplained and was not repaired; no live 410-record source or optional comparator was used. **`3d56fe8` is PIC-approved (2026-11-07). MEM-307/MEM-002 and Phase 3 remain open/blocked; Phases 4–5 and cutover remain unauthorized.** The approval accepts the median/tail trade-off and stops further speculative local optimization. Any future held-vector route still requires separate informed egress consent, a NEW immutable combined cache preserving the current 16 entries, final calibration bound to that exact cache, then freeze/single-use holdout. None of that sequence ran here.

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

Real-memory cases remain local and require sanitization, referential checks against their private corpus, and human review. The approved cohort now contains **408 records and 32 reviewed cases (16 calibration / 16 holdout)**: Architect adjudicated the AI-assisted proposals, and the user approved the labels, sanitization and transparent local-human attestation. Newly generated proposals still require their own review; they do not inherit that approval. This reviewed cohort is distinct from the **407-record preservation snapshot**. Reviewed labels and SQLite-only calibration are not benchmark acceptance: **MEM-002 remains open** until the required real-case benchmarking gate passes, alongside MEM-307 and Phase 3 acceptance. The primary operational Chroma comparison is now PIC-established under MEM-306. The later combined-cache calibration passed the proposed p95 gate, but the single-use holdout failed Recall@5, and Architect/PIC rejected Phase 3 acceptance on 2026-11-07. Synthetic coverage, draft generation and the narrow patch sign-off do not authorize Phases 4–5 or cutover.

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


## Fresh lexical holdout

`scripts/benchmark_memory_retrieval.py fresh-holdout` is the only entry point for a new lexical exam. It refuses the consumed `benchmark-holdout-ledger`, a combined calibration/holdout case file, an unset review, and any SQLite semantic cutoff. Query vectors must already be cached as reviewed voyage-4 rows. The runner does not generate embeddings. It opens the SQLite and Chroma backends before reserving the fresh ledger, and publishes that reservation with one directory rename. User acceptance to proceed is not a witnessed semantic-non-overlap audit. A blocked acceptance report is not production approval.
