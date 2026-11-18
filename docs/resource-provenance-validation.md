# Resource provenance validation — 2026-11-18

## Focused checks

`verification/memory/test_resource_provenance.py`: **51 passed** with pytest after review remediation.
All inputs are fictional; no live network/model calls or private memories were used.
The deprecated `tests/` tree was not run.

Coverage includes concurrent chat/A2A/ACP/job scope isolation and cleanup on
success/error/cancellation; actual resolved tool dispatch; successful/failed MCP
fetches and registration without capture; chat pre-inference attachment seeding;
actual ACP embedded-resource turns; actual job-file preparation through final
memory queuing; A2A and ACP chained asks carrying original and newly read resources
with bounded deduplication (A2A memory/file/Redis backends); chat setup-failure
retry provenance; host-independent Windows drive/UNC paths; preservation of
single-letter custom URI schemes; raw/local/HTTP A2A attachment locators; URI
caps/deduplication/no stat or symlink resolution; legacy/structured XML; Unicode
entity ambiguity; model URI invention/rewrite rejection; one-call extraction and
no-model fallback; immutable queue data; explicit candidate/CV links without
cross-linking; scoped exact lookup before caps; single-batch late hydration;
transaction rollback; schema-1 upgrade rollback and canonical XML/metadata-byte
preservation; read-only compatibility; Chroma mapping and derived-row verification;
delete cascades; and consolidation link preservation/ambiguous-merge rejection.

Passing existing harnesses:

- `verify_tool_evidence.py`
- `verify_exact_lookup.py` — 151 candidate/result comparisons, two index-led plans
- `verify_fts_payload.py` — full equivalence harness: 140 candidate/coordinator
  comparisons, 120 entity comparisons, 298 prior-coordinator comparisons, 15 word
  equivalence/mutation cases, 4 FTS and 12 entity raw SQL checks, and concurrent
  reader/writer snapshot checks
- `verify_grounded_reconstruction.py`
- `verify_migration_operator.py` — 25 fictional source/destination records
- `verify_startup_migration.py`
- `verify_phase2.py`
- `verify_phase5.py`
- `verify_phase6.py`
- `verify_semantic_candidates.py`
- All 10 Phase 4 behavioral checks, invoked without `checks_policy_freeze`
- All 12 lossless-consolidation behavioral checks, invoked without
  `checks_freeze_and_schema_contract`

Changed Python files pass `uvx ruff check --statistics` and
`uvx ruff format --check`; `git diff --check` passes. In the initial implementation,
Pyright 1.1.414 reported the same **75 existing diagnostics** on the 19 changed
existing production files as on the original base checkout, with no diagnostics
in the new collector module. For remediation, all **9 changed production files**
were compared against an archive of commit `be3064b6`: **5 diagnostics before and
5 after, with identical diagnostic messages**. This is not a clean repository-wide
type-check claim.

## Historical gates, kept explicit

Historical reviewed-derivation hashes/provenance pins were **not updated**.

- Phase 1's full harness fails its `Prototype import loaded optional/runtime
  services` guard. This was reproduced on the unchanged base checkout (`provenance`
  already imports Loguru).
- Phase 4's full harness fails its historical
  `Retrieval-critical file changed: scripts/benchmark_memory_retrieval.py` gate,
  also reproduced on the unchanged base checkout. Its behavioral checks pass.
- The lossless-consolidation harness's runtime schema assertions now correctly
  expect version 2. All 12 behavioral checks pass, but its separate historical
  freeze contract detects the intentional implementation changes (`Slice 4
  introduced freeze drift`). This is a feature-related historical gate mismatch,
  not a pre-existing failure and not a behavioral pass for the entire harness.
The FTS payload harness is a **current equivalence harness**, not an excluded
historical gate. Its schema plumbing was corrected: pinned schema-1 repositories
read disposable schema-compatible copies made with SQLite backup; only those
copies drop the schema-2 resource projections and change version markers. The
current schema-2 fixture is unchanged. Equality of canonical/base derived and FTS
rows plus foreign-key integrity is asserted when making each copy. All original
comparison assertions and committed reference hashes remain unchanged, and the
full harness now passes.

## Initial implementation performance samples

Python 3.12.10, SQLite 3.47.1, Linux x86_64, fictional corpus of 2,000 records.
Search samples: 200 paired, alternating-order measurements after warm-up in the
same process. Collector callback/snapshot samples: 5,000. Resource row-write samples:
200, inside one transaction, excluding commit and resource-row deletion/setup.

| Operation | Median | p95 | Target |
| --- | ---: | ---: | ---: |
| Added non-resource hook callback | 0.151 µs | 0.177 µs | <10–20 µs |
| Custom URI capture, including scope bind/reset | 3.654 µs | 4.011 µs | <100 µs |
| Local file hook/capture, including scope bind/reset | 10.622 µs | 11.405 µs | <100 µs |
| Snapshot of 32 resources | 0.312 µs | 0.521 µs | <1 ms |
| Write 32 resources plus 32 explicit links, excluding commit | 277.953 µs | 297.389 µs | <2 ms |
| Exact URI search with 2,000 resource-bearing records | 4.214 ms | 4.410 ms | <10 ms |

Ordinary-search comparisons use identical resource-free source records, isolating
added overhead on searches that do not need resource hydration. Resource-bearing
selected hits are covered by the batched-hydration correctness checks; their
comparative performance at a large production corpus is not claimed here.

| Ordinary search | Base p95 | Feature p95 | Paired p95 change |
| --- | ---: | ---: | ---: |
| Selective entity | 3.466 ms | 3.561 ms | +2.74% |
| Broad lexical | 31.900 ms | 32.407 ms | +1.59% |

These samples meet the targets for this fixture, not universal latency guarantees.
Earlier separate-process comparisons varied substantially (including a broad-search
p95 change of +8.03%); paired measurements reduce, but cannot eliminate, scheduling
and host-load noise. The benchmark emits all raw samples. The final run's raw JSON
and check logs are available locally in `/tmp/agentcrew-resource-checks/`, notably
`benchmark-final-paired.json`. They are not historical calibration/provenance pins.
No production instrumentation, logging or polling was added for measurements.

## Review-remediation performance delta

Compared with the committed implementation `be3064b6` using its archived source,
Python/SQLite and the 2,000-record fixture above. Ordinary search comparisons were
paired in one process (200 samples). Callback/snapshot comparisons below used
separate runs with the same warm-up and 5,000 samples; small relative differences
are raw observations, not noise-free causal estimates. Resource-write and URI
search runs used 200 samples. No benchmark code was added to production.

| Operation | `be3064b6` p95 | Remediated p95 | Observed change |
| --- | ---: | ---: | ---: |
| Non-resource callback | 0.170 µs | 0.174 µs | +2.35% |
| Custom URI capture with scope | 3.940 µs | 4.007 µs | +1.70% |
| Local file hook with scope | 11.446 µs | 11.728 µs | +2.46% |
| Snapshot of 32 | 0.350 µs | 0.362 µs | +3.43% |
| Write 32 resources/32 links, excluding commit | 300.008 µs | 285.882 µs | −4.71% |
| Exact URI search | 4.456 ms | 4.475 ms | +0.42% |
| Ordinary selective search, paired | 3.580 ms | 3.583 ms | +0.10% |
| Ordinary broad search, paired | 30.978 ms | 31.051 ms | +0.23% |

Raw samples: `/tmp/agentcrew-resource-checks/benchmark-remediation-paired.json`
and `benchmark-remediation-baseline.json`. These remain fixture-specific samples;
large production corpora and resource-bearing ordinary-query regression are not
established by them.

The optional chat retry finding was reproduced by a focused failing test: stream
setup cleared a queued attachment before execution. Clearing now occurs only after
stream setup/binding succeeds; the same test verifies a successful retry still
seeds the original URI. No new persistence or background work was added.

## Commands

During this work, the feature worktree reused the installed base environment with
`uv run --project /path/to/base-checkout --no-sync python ...` while executing from
the feature worktree. On a checkout with its own installed environment:

```sh
uv run python -m pytest verification/memory/test_resource_provenance.py -q
uv run python verification/memory/benchmark_resource_provenance.py \
  --records 2000 --repeats 200 --compare-root /path/to/pre-feature-checkout
uv run python verification/memory/verify_phase2.py
uv run python verification/memory/verify_phase5.py
uv run python verification/memory/verify_semantic_candidates.py
uvx ruff check $(git diff --name-only HEAD^ -- '*.py') --statistics
uvx ruff format --check $(git diff --name-only HEAD^ -- '*.py')
```

The migration-operator harness expects its ignored scratch directory to exist:
`mkdir -p verification/memory/local` before running it.

Remaining operational caveats: first writable open backfills the existing corpus
under a writer transaction; read-only v1 opens cannot upgrade; raw upload evidence
locators do not retain content; relation labels are model extraction claims checked
for structural validity, not independently verified identity claims; and real
large-corpus performance remains to be measured on an appropriate private dataset.
Exact HTTP/MCP URIs may retain credential-bearing query strings; this is an explicit
residual privacy/security caveat of the approved exact-URI semantics, not redacted
or encrypted by this feature. Cursor SDK-internal and remote-agent accesses remain
outside the documented local capture surfaces. ACP pending-ask state remains
session-local rather than restart-durable, matching its existing ask lifecycle.
