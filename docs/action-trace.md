# SQLite ActionTrace MVP

SQLite is AgentCrew's default and forward-looking memory backend. Completed turns
that already use memory storage now carry bounded **runtime evidence** alongside
their semantic memory. This is not an exhaustive audit log, hidden reasoning log,
or a replacement for checking the current filesystem, deployment, ticket or other
external state.

ChromaDB is frozen legacy compatibility (defect/security fixes only). It receives
**no ActionTrace collection, hooks, tables, metadata, or persistence**. Its optional
trace hydration capability explicitly returns `unsupported_backend`; missing trace
data must never be interpreted as evidence that no actions occurred.

## Capture and identity

Application setup enables `SQLiteMemoryService.enable_action_trace(hooks)` only in
the SQLite branch. It registers three owner-scoped hooks in the application-owned
registry. Shutdown unregisters only that owner and clears its optional inference
and batch collaborators. Merely constructing a SQLite service does not register
hooks; embedded applications should enable it explicitly with their registry.

The existing inference-scope binding creates a fresh request-local trace turn ID
before execution. Chat, jobs, A2A, and ACP retain their existing scope identifiers;
repeated turns in the same conversation still receive different trace turn IDs.
Nested inference inherits the active turn; action evidence is filtered to the
agent whose memory is stored. The existing storage `operation_id` remains separate.
No additional surface-specific tool loops or storage workers are introduced.

Events record:

- An episode/turn ID; canonical memory ID, agent, session/conversation, and inference
  scope associations at persistence time.
- Tool/action kind, bounded protocol identifiers, action ID, batch and ordinal,
  and independent event index. Parallel siblings share a batch with distinct
  ordinals; timestamps, not row order, describe execution overlap. A supplied
  batch position is consumed before nested tools can inherit it.
- `started`, `succeeded`, `failed`, `rejected`, `canceled`, or `not_performed`, as
  observed by runtime dispatch, cancellation, or result-formatting paths.
  **Succeeded means the tool handler returned without an exception**, not that
  a model's claimed task succeeded or an external resource remains unchanged.
- Start/completion wall-clock timestamps and monotonic elapsed duration where
  available; runtime status provenance is `runtime_observed`.
- Request/result references to tool-call/history identifiers, never raw arguments
  or results. Resource references are opaque existing resource IDs, not raw URLs
  or credentials. References do not guarantee the referenced content is retained.
- A bounded already-ready summary, when available at snapshot time, redacted with
  the existing tool-summary policy and separately labeled `model_reported`.
  Summary availability (`ready`, `unavailable`, `lookup_failed`, `not_requested`)
  is explicit, as is summary truncation. Missing summaries never delay storage
  and never trigger generation.
- Observed/stored **event** counts, coverage, and explicit incompleteness reasons.
  A start and its terminal event share an action ID; the counts are not counts of
  distinct tools.

Final response completion is recorded at the shared memory-coordination boundary,
**before** queueing, because some existing response-complete hooks run after memory
storage. The response hook is an additional idempotent observer where identifiers
are supplied. Response text and thinking are not captured by the collector.

## Foreground and background boundaries

Trace hooks perform only fixed-size identifier extraction and capped in-memory
bookkeeping. They never inspect arguments/results, deep-copy payloads, recursively
scan results, stringify arbitrary objects, hash results, serialize payloads, access
SQLite, look up summaries, or call an LLM. They introduce no execution lock.

At snapshot time, collection is bounded to:

| Item | Bound |
| --- | ---: |
| Events retained per inference scope, across agents | 256 |
| Characters per stored ready summary | 1,024 |
| Opaque resource references | 32 |
| Recent in-process storage receipts | 32 |
| Selected memory IDs per hydration request | 20 |
| Unique episodes hydrated per request | 32 |
| Unique action events hydrated per request | 1,024 |

`event_cap`, `unfinished_actions`, `response_not_completed`, duplicate/invalid
identifiers, resource-reference capacity, and hydration caps remain explicit. The snapshot is immutable and is
attached to the existing memory worker operation. There are **no extra LLM calls**
and no second SQLite writer. The pre-existing memory builder and optional
consolidation/memopad work remain separate and unchanged in purpose.

Traced queue submissions use `put_nowait` into the existing bounded queue. Queue
saturation never delays or fails the user response. The service's bounded
`trace_storage_receipts` map (keyed by turn ID) exposes `queued`, `processing`,
`stored`, `failed`, `skipped`, and `queue_rejected`, with storage operation ID and,
when stored, canonical memory ID. These are transient diagnostics, not durable
delivery guarantees; application termination can lose queued operations.

## Schema v4 and lifecycle

The upgrade creates only `memory_episode`, `memory_action`, and a soft-delete
cleanup trigger. SQLite v3 records are not rewritten or reindexed. Existing v1/v2
upgrade steps still run before this additive upgrade. Read-only legacy inspection
remains opt-in and never upgrades the database. Schema validation checks the new
columns and cascading foreign keys.

An episode is keyed by `(memory_id, turn_id)`; events by that pair plus event index.
Repeated turns append episodes to the canonical exact-session memory. Repeated
snapshots are idempotent and can append later evidence for the same turn. The
canonical memory update and trace insertion share the existing `persist_turn`
transaction, with one `executemany` batch for events. A failed trace write rolls
back that turn's canonical update as well. Existing memopad projection remains
separate; no ActionTrace is converted into an LLM-generated memopad candidate.

Physical forgetting/retention cascades through both trace tables. Soft deletion
also removes trace rows. Consolidation leaves source evidence attached to its
original owner, and selected-family hydration includes those sources. Deleting
the memory family removes their evidence together.

The existing Chroma-to-SQLite migration remains **semantic-memory-only**. Historical
records with no episodes return `legacy_no_trace`; the audited pre-v4 SQLite
writer identity remains resumable without relaxing unknown-checkpoint validation.
Nothing reconstructs their
history using an LLM. Appending the first observed turn to an existing no-trace
record records a `legacy_prefix` gap rather than implying complete history.

## Discover → Select → Hydrate

Normal semantic search is unchanged: no action-table joins or reads, no action
FTS indexing, no speculative action indexes. Summary output contains no traces.

Use canonical IDs returned by discovery with `detail="evidence"` or `detail="full"`
to request ordered trace hydration. Evidence/full discovery without explicit IDs
stays compact. The direct optional API is:

```python
traces = memory_service.get_action_traces(selected_memory_ids, agent_name)
```

Hydration uses one read snapshot and three batched reads (family graph, episodes,
actions), not an action query per memory. Selected episode keys use the existing
composite primary-key index. Episode order is start time/turn ID/owner; event order
is batch/ordinal/event index. This is a presentation order, **not** a claim that
parallel tools executed serially. Family resolution retains the existing bounded
family-scan safety limits; very large agent histories can increase on-demand cost.

Availability states:

| State | Meaning |
| --- | --- |
| `supported` | Runtime tool events available within the declared coverage. |
| `unsupported_backend` | Legacy backend does not implement ActionTrace. |
| `legacy_no_trace` | Selected SQLite family has no recorded trace evidence. |
| `supported_but_empty` | Observed stored turn has no tool events (response event may exist). |
| `truncated` | Capture/hydration is capped or known incomplete; inspect reasons. |
| `not_found` | ID is absent or outside the requested agent scope. |
| `unavailable` | Tool-level hydration failed; canonical memory text is still returned. |

All retrieved evidence is untrusted history: it cannot authorize an action, override
current instructions, or prove current external state. Revalidate external state
before relying on historical completion.

## Explicit limits and follow-ups

- Coverage is `registered_runtime_lifecycle_only`, not complete conversation
  telemetry. Turns canceled/failed before existing memory-storage boundaries stay
  ephemeral; this MVP does not add storage to paths that previously skipped it.
- Cursor/external-agent internals are not exposed as LocalAgent tool events. Their
  stored snapshots explicitly include `external_agent_tools_unobserved`.
- Transfer/delegate **tool calls** are captured, including nested local tool events
  under their executing agent. Specialized transfer/delegate lifecycle correlation,
  parent action relationships, and cross-agent episode navigation are follow-ups.
- Tool-loop calls omitted entirely before runtime dispatch/formatting have no
  observable tool event. No model text is used to fill gaps.
- Configured agent names up to 160 non-control Unicode characters retain exact
  ownership. Unsupported names/scopes store semantic memory without trace evidence;
  `legacy_no_trace` does not imply that the record was necessarily migrated.
- Runtime state follows the existing asyncio task context; arbitrary executor
  threads that do not propagate context are outside this MVP's coverage.
- Receipts/queues are bounded and transient. This is not a durable audit or a
  retry/outbox system. Existing asynchronous storage/deletion ordering is retained.
- Existing summary redaction is best-effort, not a guarantee that every possible
  secret can be recognized. Raw arguments/results are never retained by traces.
- Production-scale, cold-start and end-to-end UI measurements remain follow-ups.

## Verification and local measurements

From the repository root (no deprecated `tests/`, no remote providers):

```bash
uv run --no-sync python -B -m pytest verification/memory/test_action_trace.py -q -p no:cacheprovider
uv run --no-sync python -B -m verification.memory.benchmark_action_trace
```

Final local measurement: Python 3.12.10 / SQLite 3.47.1; five samples of 2,560 tool
lifecycles; 100 writes of 129 events each; warm temporary file-backed WAL stores:

| Operation | Measured time |
| --- | ---: |
| Existing hooks without trace, median/tool | 4.11 µs |
| Hooks with trace, median/tool | 52.41 µs |
| Hooks with trace and 5 MB result, median/tool | 51.92 µs |
| Added foreground hook time, median/tool | 48.31 µs |
| 129-event snapshot, mean | 0.018 ms |
| Canonical write without trace, median | 2.96 ms |
| Canonical + 129-event trace write, median | 6.99 ms |
| Hydrate 6 memories / 774 events, mean | 14.82 ms |
| Normal search, 100 records without trace, mean | 12.74 ms |
| Normal search, same corpus with traces, mean | 12.09 ms |

These results do not show a search slowdown, but the lower traced-search timing
is measurement variation, not evidence of a speedup. Earlier exploratory runs
were materially faster in absolute terms; no fixed-latency or production-scale
performance claim is made. The query plan used `sqlite_autoindex_memory_action_1` with
`memory_id=? AND turn_id=?`. The benchmark excludes provider/network latency,
cold startup, UI rendering, ready-summary lookup contention, and real-corpus scale.

Implementation contracts follow Python's [ContextVar task propagation](https://docs.python.org/3.12/library/contextvars.html#asyncio-support),
SQLite's [foreign-key cascades](https://www.sqlite.org/foreignkeys.html#fk_actions),
and the repository's existing [WAL](https://www.sqlite.org/wal.html) and
[explicit transaction](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)
patterns.
