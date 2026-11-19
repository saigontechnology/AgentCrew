# Memory extraction reliability

## Phase 1: privacy-safe diagnostics

Implemented in an isolated branch based on `31138f71f5adfc9dee8fd446fa7fb75309d007cc`.
The sections below record successive implementation milestones; the final
integration section covers all three phases and `/stats` service-role reporting.
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

The XML-specific failure categories above describe the Phase 1 boundary; normal
extraction now uses the JSON categories below. Their original names are retained,
not repurposed to mislabel JSON failures.

## Phase 2: structured extraction

Normal extraction now uses provider-neutral JSON-only prompting followed by
strict Pydantic validation in `MemoryRecordBuilder`. Native provider JSON modes
are deliberately not required: provider service families do not currently share
one native structured-output contract. The local `parse_extraction()` boundary
can validate either prompted or future native JSON without changing persistence.

The full payload requires `head`, `flow` (`intent`, `exploration`, `outcome`) and
`notes`. Optional `insights`, `entities` (`name`, `desc`), `domains` and `resources`
default to empty lists. Unknown keys, coercions, duplicate JSON keys, non-finite
constants, blank entity/reference names or relations, and XML-illegal characters fail validation. JSON Markdown fences with
LF/CRLF are accepted, but arbitrary surrounding prose is not repaired. All
semantic text is serialized by `xmltodict`, never unescaped XML interpolation.
Date is assigned by the application. Legacy XML parsing remains available to
consolidation and existing-record readers, but is not the new extraction contract. Header projections
are taken from the serialized canonical XML, preserving empty-header/null semantics.

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

## Phase 3: auxiliary routing and service-role visibility

`ServiceManager.initialize_auxiliary_service(primary, provider_override)` is the
shared setup path for memory extraction, tool-result summarization, and code
analysis. Model preferences live only in
`AgentCrew/modules/llm/auxiliary_model_selection.py`:

- No override (including a same-provider override) selects the primary provider's
  registered lightweight model, **not the primary runtime model**. A provider
  override selects that provider's lightweight model, not its catalog default.
- Every built-in provider has an explicit registered preference. Models bind
  through their declared `resolved_service_name()`, preserving authentication and
  endpoint families even in mixed OpenAI, Copilot, OpenCode and CommandCode
  catalogs. The legacy `copilot_response` alias selects a lightweight registered
  GitHub Copilot Responses model.
- Custom providers use their configured default model, or their first registered
  model if no default exists. Only a same-provider custom client without a
  catalog retains the raw runtime model and recorded implementation family.
  Unknown overrides fail rather than guessing incompatible model IDs.
- `--memory-llm` remains the provider-only CLI option for all three background
  roles. Its built-in choices now also include Fireworks. The central API also
  accepts configured custom provider names and explicit endpoint-family aliases.
- Each role gets a fresh standalone client and requests reasoning disabled via
  the existing lenient adapter. Constructors consume that selection unchanged;
  neither primary chat selection nor the registry's current model is mutated.
- Memory and summary worker loops retain ownership of their separate clients.
  Only code analysis is application-owned when standalone ownership is enabled.
  Clients that fail configuration or role construction are closed before the
  failure propagates or the optional role is marked unavailable.

This routing correction is separate from the reported Codex extraction incident.
Neither the earlier model-selection speculation nor the newly found constructor
rewrites demonstrate what caused that historical fallback.

The existing `/stats` handler now prepends stable role rows for primary/current
chat, memory extraction and tool-result summary. It additionally reports a
separate primary setup client when it differs from the current agent, and code
analysis when instantiated. Each active row has provider, implementation family,
concrete service class and qualified model ID. `same as primary` means **the same
client object**, not just two separate clients using the same model.

Role introspection and identity redaction are centralized in
`ServiceManager.describe_service_roles()` and `describe_service()`; extraction
diagnostics use that same identity helper. No introspection creates clients,
initializes the registry, reads credentials, inspects configuration dictionaries,
or traverses arbitrary service graphs. Recorded factory families are preferred;
registered family metadata and concrete class remain available otherwise. Unknown
or unsafe identity values are explicitly `unknown`. To avoid treating relative
file paths as model IDs, hierarchical IDs containing `/` are shown only when
registered. Ordinary unregistered single-token model IDs remain visible.

Examples of identity-only output (the classes are illustrative):

```text
Primary/current chat: provider=openai_codex | service=openai_codex (OpenAICodexService) | model=openai_codex/gpt-6.1-sol
Memory extraction: provider=openai_codex | service=openai_codex (OpenAICodexService) | model=openai_codex/gpt-5.4-mini
Tool-result summary: disabled
```

Disabled memory and explicitly disabled summaries show `disabled`. Missing or
failed-to-initialize clients show `not configured`; a memory wrapper without an
extractor is not mislabeled as disabled memory. External/remote/SDK-backed agents
without a local chat LLM show no configured **local** chat service, not the
startup model masquerading as the current agent. Existing per-agent token/cost,
context occupancy and current-conversation totals remain unchanged. These are
instantiated-client identities, not a claim that all auxiliary clients follow
later `/model` switches automatically, or that token accounting now includes
auxiliary usage that was previously outside the conversation ledger.

Console and GUI still use the same command processor and event output. The
existing GUI whitelist now forwards `/stats` to that processor, and both existing
completers/shared help advertise it. No competing command implementation or new
UI surface was created. This guide and `--memory-llm` help explain lightweight
model selection. Keys, OAuth tokens, account IDs, URLs, paths, resource locators,
prompts and file contents are never part of the identity descriptor.

### Continuation entity safety

The structured entity schema additionally accepts `update_existing` (strict bool,
default false). Only an explicit update proposal with a unique normalized name in
**both** previous memory and current entities can combine those entity fields;
prior spelling, fields and explicit links are retained, and distinct description
text is appended rather than overwritten. A matching name alone does **not**
authorize identity merging. Duplicate names in either input remain ambiguous,
including identical duplicates. The flag is application-side proposal metadata,
never a new XML tag or database field. Unknown/ambiguous targets are not combined.
If a collision would invalidate an old explicit link, the existing authority
invariant uses deterministic fallback, preserves the old entity/link and retains
bounded current-turn evidence in notes, with the new resource left unlinked.

This prevents repeated known-entity updates from destroying old links while
avoiding automatic inference that two same-name people/resources are identical.
Models must still ground their semantic assertions in evidence; local schema
validation cannot prove real-world identity or factual truth.

## Final integration validation

**Centralized lightweight-selection follow-up (2026-11-19):** the focused
`test_auxiliary_model_selection.py` and `test_auxiliary_services_stats.py` checks
under `verification/memory/` passed (**126 tests**). The evidence below records
the earlier memory-reliability milestone, not validation totals or suite scope
for the later lightweight-selection slice.

All new tests use fictional data/fake services and make **zero remote LLM calls**.
No deprecated `tests/` suite, real CV, private memory or sealed evaluation corpus
was used. No dependency/lock file, benchmark provenance pin or historical freeze
hash was changed.

**152 focused tests passed**, including all 51 resource-provenance cases, privacy
logging, JSON/schema contracts, explicit update/ambiguity protection, mixed
provider/service-family routing, setup integration, `/stats` snapshots and GUI
forwarding. The routing matrix covers Codex, OpenAI chat/Responses, OpenCode
chat/Anthropic, Copilot chat/Responses, CommandCode chat/Anthropic, Claude and
Google. OpenAI chat is covered with fake registered metadata because the current
built-in OpenAI catalog lists Responses-family models; its chat factory remains
supported. Tests also cover raw-family retention, defaults/absent defaults, the
legacy Copilot alias, disabled roles, distinct tool summaries, stale startup vs
current agent, safe hierarchical model IDs and duplicate-free completion/help.

Commands were run from the isolated worktree using the existing environment:

```bash
PYTHON=../AgentCrew/.venv/bin/python
uv run --no-project --no-config --offline --python "$PYTHON" python -m pytest -q \
  verification/memory/test_extraction_reliability.py \
  verification/memory/test_structured_extraction.py \
  verification/memory/test_resource_provenance.py \
  verification/memory/test_auxiliary_services_stats.py
```

The following standalone scripts passed (each invoked with that same
`uv run --no-project --no-config --offline --python "$PYTHON" python` prefix):

- `verification/memory/verify_tool_evidence.py`
- `verification/memory/verify_exact_lookup.py` — 151 candidate/result comparisons
- `verification/memory/verify_fts_payload.py` — 140 candidate/coordinator,
  120 entity and 298 prior-coordinator comparisons; concurrent snapshot checks;
  frozen reference commits/hashes unchanged; zero remote calls
- `verification/memory/verify_grounded_reconstruction.py`
- `verification/memory/verify_startup_migration.py`
- `verification/memory/verify_migration_operator.py` — 25 fictional records
  verified, 32 review drafts, 5 recovered entries skipped and 20 inserted
- `verification/memory/verify_phase2.py`
- `verification/memory/verify_phase5.py` (also rerun after entity continuation changes)
- `verification/memory/verify_phase6.py`
- `verification/memory/verify_semantic_candidates.py`

The migration operator initially needed its ignored `verification/memory/local/`
output directory created in the fresh worktree; its rerun passed. This is test
scratch data, not an application migration or a private source.

Historical gates remain failures, confirmed against the tracked-file base
snapshot at `31138f71f5adfc9dee8fd446fa7fb75309d007cc` as well:

| Script | Unchanged failure |
| --- | --- |
| `verify_phase1.py` | `Prototype import loaded optional/runtime services` |
| `verify_phase4.py` | `Retrieval-critical file changed: scripts/benchmark_memory_retrieval.py` |
| `verify_lossless_consolidation.py` | `Slice 4 introduced freeze drift` |

The historical Phase 4 **10 behavioral checks** and consolidation **12 behavioral
checks** passed separately without changing/exempting their gates:

```bash
uv run --no-project --no-config --offline --python "$PYTHON" python - <<'PY'
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

Ruff lint (`uvx ruff check --statistics`) and formatting checks
(`uvx ruff format --check`) pass over all **18 changed Python files**. Syntax
checks pass for all 18, and all **13 changed production modules** import without
instantiating services. `git diff --check` passes. Relevant Pyright analysis on
those 13 modules reports **39 errors on both base and feature, zero new
diagnostics** (comparison includes path, message, severity and rule, ignoring
shifted line numbers). The configured worktree-local `.venv` is absent; all
execution and analysis used the original checkout's explicit Python interpreter.
Production changes were reread directly, including completion/help entries to
ensure no duplicate `/stats` entry was introduced.

### Performance and residual risks

- Normal extraction still makes exactly one request; only failures use corrective
  retries. No additional resource-capture request, queue, thread, per-access DB
  write, file hash, recursive scan, polling or content-copy pipeline was added.
  Existing memory/tool-summary workers and queue behavior are unchanged.
- Local schema generation/validation, escaping and deterministic prior-field
  retention add CPU work; accumulated session XML may grow. Production-scale
  extraction latency, large-corpus resource search and real-provider success
  rates were not measured. Retrieval/SQLite projection code is unchanged, and
  existing fictional equivalence harnesses pass; their timings are not a new
  production performance guarantee.
- Lightweight preferences reduce reliance on expensive chat defaults, but are
  catalog-based, not latency benchmarks. xAI has no small/flash tier or published
  comparison in this catalog; its preference is the low-reasoning Grok 4.3 model.
  Custom-provider defaults may still be expensive. Reasoning is requested off,
  but providers retain their existing lenient handling of unsupported settings.
  The 5-second summary timeout and fail-open queue/cache behavior are unchanged.
- Confirmed token exhaustion is distinguished when the Responses guard exposes
  `max_output_tokens`. Other text-only provider interfaces do not consistently
  expose finish reasons yet; incomplete JSON alone is not labeled truncation.
- JSON-only prompting plus local validation is portable, not native constrained
  generation. Incorrect/malformed/oversized responses can still exhaust retries
  and fall back. The historical incident needs new diagnostic observations to
  establish its actual cause; no live provider/CV test was performed.
- Consolidation retains its existing XML contract and invariants, independently
  verified by the behavioral harness; this slice changes normal turn extraction.
- Exact resource URIs can still contain credential-bearing query parameters in
  canonical memory/LLM input. They are never included in the new diagnostics or
  `/stats`; URI redaction/retention policy and retrospective cleanup are separate.
- Existing provenance capture-surface/session-store limitations remain unchanged.
