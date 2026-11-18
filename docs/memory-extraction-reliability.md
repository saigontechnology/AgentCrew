# Memory extraction reliability

## Phase 1: privacy-safe diagnostics

Implemented in an isolated branch based on `31138f71f5adfc9dee8fd446fa7fb75309d007cc`.
Structured JSON extraction, corrective retries, auxiliary routing corrections and
`/stats` role reporting are subsequent slices, not implemented by this milestone.
The reported runtime uses `openai_codex/gpt-6.1-sol`; a routing mismatch has **not**
been demonstrated as the cause of that incident.

`MemoryRecordBuilder` emits bounded diagnostic metadata instead of response
previews or extracted memory data. Attempt failures use WARNING. Final fallback
uses ERROR, including when resource enforcement replaces an otherwise successful
extraction. The reason and identity also appear in the message so default
ERROR-level text sinks retain useful information without needing a custom format.

Stable categories:

| Category | Meaning |
| --- | --- |
| `provider_error` | Request/stream failure, including unconfirmed premature termination |
| `empty_response` | Empty, whitespace-only or absent usable text |
| `missing_memory_block` | Required opening or closing memory marker is absent |
| `malformed_xml` | Memory block fails XML parsing |
| `invalid_memory_root` | Parsed memory root is not a mapping |
| `output_truncated` | Provider explicitly identifies output-token exhaustion |
| `resource_enforcement_failed` | Serialization/resource authority enforcement failed |

Each event has provider, model, qualified model, registered service family when
known, concrete service class, attempt number, retry count, returned response
character count, exception class, category, and final fallback reason when final.
Attempt is one-based; retry count is `max(0, attempt - 1)`. Intentional no-LLM
fallback is not an extraction error. Successful recovery has no final ERROR.
The diagnostics are logs, not new fields in canonical XML or backend metadata.

`ServiceManager.describe_service()` is the shared identity introspector for later
role reporting. It does not create a service or initialize the registry. Unknown
registry family is reported as `unknown`, not guessed from the provider; the
concrete class is still shown. Values must be bounded identity tokens; URLs,
absolute paths, traversal, whitespace and credential-bearing query strings are
not returned. Auth, endpoint, account and prompt attributes are never inspected.

`LLMResponseError` adds provider-neutral machine-readable truncation metadata
while remaining a `RuntimeError`. The existing Responses stream guard uses it
without changing replay, close or terminal-event behavior. Only
`response.incomplete` with reason `max_output_tokens` sets truncation. Failed,
filtered and naturally exhausted-but-uncompleted streams do not imply token
exhaustion. Providers returning only strings still cannot expose reliable finish
reasons: missing closing XML alone is **not** diagnosed as confirmed truncation.
When a provider raises before returning text, response character count is zero
(the available returned text), not an estimate of discarded partial output.

No prompts, conversation text, CV/attachment contents, raw invalid responses,
exception messages/tracebacks, credentials, resource URIs or URI query values are
passed to the new diagnostic logger. This is a scoped guarantee about extraction
diagnostics, not an audit of every other provider/application log statement.

Canonical XML, resource snapshots/authority, schema-v2 projections, persistence,
consolidation and retrieval remain unchanged. The ordinary successful extraction
still makes one LLM call. There are no new queues, threads, per-access database
writes or runtime filesystem/hash operations. Diagnostics execute on failure;
no production performance benchmark or live model validation is claimed.

## Phase 1 validation (2026-11-18)

Use the existing environment without resolving/installing project dependencies:

```bash
uv run --no-project --no-config --offline --python ../AgentCrew/.venv/bin/python python -m pytest -q verification/memory/test_extraction_reliability.py verification/memory/test_resource_provenance.py
```

Result: **75 passed** (24 new diagnostics/stream checks, 51 existing provenance
checks), all fictional inputs and fake services. Tests inspect message, structured
fields and exception attachment at DEBUG through ERROR to verify no sensitive
response/exception content is emitted. Registry identity tests prohibit singleton
initialization and exercise unsafe identity rejection.

The following commands passed with the same `uv run` prefix:

```text
python verification/memory/verify_tool_evidence.py
python verification/memory/verify_exact_lookup.py
python verification/memory/verify_fts_payload.py
python verification/memory/verify_grounded_reconstruction.py
python verification/memory/verify_migration_operator.py
python verification/memory/verify_startup_migration.py
python verification/memory/verify_phase2.py
python verification/memory/verify_phase5.py
python verification/memory/verify_phase6.py
python verification/memory/verify_semantic_candidates.py
```

The operator initially stopped because its ignored output parent was absent in
the new worktree; `mkdir -p verification/memory/local` supplied that parent, and
rerunning passed with 25 fictional records. No real memory/configuration inputs
or main-worktree untracked files were copied into the worktree.

FTS equivalence passed 140 candidate/coordinator, 120 entity and 298
prior-coordinator comparisons plus concurrent-reader/writer snapshot checks,
with zero remote calls. Exact lookup passed 151 comparisons and two index-led
plans. All 10 Phase 4 and 12 lossless-consolidation behavioral checks passed
separately from their unchanged historical gates:

```python
from verification.memory import verify_phase4, verify_lossless_consolidation
for module, excluded in (
    (verify_phase4, "checks_policy_freeze"),
    (verify_lossless_consolidation, "checks_freeze_and_schema_contract"),
):
    for name, check in vars(module).items():
        if name.startswith("checks_") and name != excluded:
            check()
```

The historical gates still report `Retrieval-critical file changed:
scripts/benchmark_memory_retrieval.py` and `Slice 4 introduced freeze drift`.
They are **not whole-harness passes**. No historical pins were changed.

Ruff `check --statistics`, `format --check`, whitespace, syntax and imports are
checked on the changed files. Pyright on the four changed production files
reports the same one diagnostic on baseline and feature (the existing
`BaseLLMService.reasoning_effort` assignment in `ServiceManager`), with zero new
diagnostics. The baseline is a tracked-only Git archive, not a copy of untracked
user files. The worktree lacks its own `.venv`; the explicit existing Python path
is used, and Pyright emits an environment-directory warning.

Setup/service-routing and `/stats` contracts will be tested with their respective
implementation slice; those paths are not modified here. The deprecated `tests/`
tree is not run.

Logging reference: [Loguru binding and structured fields](https://loguru.readthedocs.io/en/stable/api/logger.html#loguru._logger.Logger.bind).
