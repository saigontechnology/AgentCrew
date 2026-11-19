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

## Phase 2: structured extraction

Normal extraction now uses provider-neutral JSON-only prompting followed by
strict Pydantic validation in `MemoryRecordBuilder`. Native provider JSON modes
are deliberately not required: provider service families do not currently share
one native structured-output contract. The local `parse_extraction()` boundary
can validate either prompted or future native JSON without changing persistence.

The full payload requires `head`, `flow` (`intent`, `exploration`, `outcome`) and
`notes`. Optional `insights`, `entities` (`name`, `desc`), `domains` and `resources`
default to empty lists. Unknown keys, coercions, duplicate JSON keys, non-finite
constants and XML-illegal characters fail validation. JSON Markdown fences with
LF/CRLF are accepted, but arbitrary surrounding prose is not repaired. All
semantic text is serialized by `xmltodict`, never unescaped XML interpolation.
Date is assigned by the application. Legacy XML parsing remains available to
consolidation and existing-record readers, but is not the new extraction contract.

Resource proposals contain only bounded IDs `r1` through `r32`, display names and
entity references; a URI field is forbidden. The application maps IDs back to
captured URIs, rejects IDs absent from that turn, then runs the existing authority
enforcer after hooks and serialization. Invalid/ambiguous entity references stay
unlinked, and all authoritative resources are inserted even if the model omits
them or the final minimal response has no resource field. Previous resources and
explicit links are preserved by the existing enforcer.

Central limits (characters, not token guarantees):

| Input/output | Limit |
| --- | ---: |
| Previous-memory prompt evidence | 30,000 |
| Assistant prompt evidence | 40,000 |
| User prompt evidence | 12,000 |
| Full JSON response | 12,000 |
| Minimal final JSON response | 4,000 |
| Resources | Existing 32 resources, 4,096 characters per URI |

The existing upstream tool-summary budget remains 8 items/6,000 characters;
combined assistant evidence is independently capped. Long evidence keeps both
ends with a shortening marker. Resource descriptors are outside those lossy text
bounds and can add up to approximately 131k URI characters at the existing caps.
Fields are additionally bounded: head/names 256 characters, semantic text 1,200,
relationships 128; 8 notes/insights/domains, 24 entities, 32 proposals with at most
8 references each. The full-result cap applies across fields. These starting
limits follow the reviewed evidence budgets; the extra user/final-minimal limits
prevent a single huge user turn or third response from defeating the budget.

Three attempts remain the maximum, not three calls on success. After a failure,
the next prompt adds only a stable category and shorter-response guidance, never
raw invalid output or Pydantic error details. Attempt 3 uses the reduced
`head`/`notes` schema. Provider failures have bounded async delays of 0.1 then 0.2
seconds; validation/truncation retries have no delay. Additional categories are
`malformed_json`, `schema_validation_failed`, `unknown_resource_id` and
`output_too_large`. Missing closing JSON is not assumed to prove truncation.

The application deterministically retains prior insights, entities, domains,
notes and legacy fields outside prompt truncation, and appends distinct current
flow text. Thus a minimal response or deterministic fallback cannot erase older
facts merely because the model did not see the full prior XML. Fallback notes
are also evidence-bounded and replace XML-illegal characters with U+FFFD. Old
canonical documents are not rewritten in place. Accumulated session records can
still grow; these limits constrain new extraction/evidence, not a lossy cap on
previously persisted facts. Summaries remain model proposals, not proof of truth.

Validation at this milestone: **97 focused tests passed** (the earlier 75 plus
22 structured-extraction cases). SQLite write-path (`verify_phase5.py`) and tool
evidence harnesses pass. Their fictional successful-extractor replies were
updated to JSON; historical benchmark pins, gates and canonical XML fixtures were
not changed to hide incompatibility. Tests cover technical generic syntax/XML
escaping, fenced JSON, schema policy, unknown IDs/invalid links, corrective and
minimal retries, provider failure, long evidence with exact URI retention,
previous-memory preservation and bounded fallback. Retrieval/consolidation
regressions are rerun at final integration.

Validation reference: [Pydantic models, strict validation and forbidden extras](https://docs.pydantic.dev/latest/concepts/models/#extra-data).
