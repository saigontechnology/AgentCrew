# Tool-call loop protection

Local agents use the same provider-independent loop protection in main chat,
background jobs/delegation, ACP, and A2A. Protection is automatic; this first
version has no `agents.toml`, global, or GUI settings.

## Recovery policy

Within one external user/task turn, an identical tool name and canonical
argument set can reach dispatch twice while it keeps appearing in consecutive
model tool rounds. The third occurrence is **not executed**. Instead, the model
receives an error tool result asking it to reuse the previous result, change the
tool or arguments, or finish without repeating the call. The model gets another
round to recover and can finish successfully.

If it repeats the exact call a fourth time, the turn terminates explicitly:

- Chat emits an error and finalizes its stream as failed.
- Jobs/delegation raise `ToolLoopError` to their caller.
- ACP returns its existing terminal error/refusal response and clears the running
  task through normal prompt cleanup.
- A2A emits a failed task status with a diagnostic, persists synthetic tool
  results, and clears pending tools so a failed resumed task cannot remain stuck
  on an old question.

All calls in a terminal batch receive synthetic errors and **none of that
batch's tools execute**. This avoids partial side effects when a terminal repeat
appears alongside otherwise valid calls. During a warning-only batch, other
calls execute normally in generated order. Blocked calls never enter validation,
confirmation/permission, `tool.execute` hooks, or handlers.

There is also a hard limit of **50 model tool rounds per external turn**. Round
51 terminates without dispatch, bounding cycles that alternate tools or keep
changing arguments. A normal final response after round 50 is still accepted.

## Fingerprints and scope

- Object key order does not matter; nested list order and scalar types do.
- Tool-call IDs do not participate in fingerprints.
- A fingerprint's streak resets when it is absent from a tool round.
- Duplicate calls within a batch consume the same budget. Only the first two
  can reach dispatch; additional duplicates receive warnings. Termination
  requires a repeat in a later model round, after the model has actually
  received its recovery warning. Duplicates cannot skip that recovery chance.
- Counts track requests/dispatch opportunities, including calls later rejected
  by validation, permissions, or hooks. This also bounds invalid-call loops.
- State belongs to the external turn, not a shared agent or provider. New input,
  including answers resuming ACP/A2A questions, starts fresh protection. Recursive
  model/tool calls and provider retries retain the current guard.
- Fingerprinting uses deterministic JSON-compatible normalization and SHA-256.
  Opaque objects, cycles, and excessive nesting use conservative type-only
  fallbacks; normal model-generated JSON arguments are compared exactly.
- Protection diagnostics log tool names, occurrence counts, and digests, never
  raw arguments. Existing tool/history displays and persistence are unchanged.

The implementation lives in `AgentCrew/modules/agents/tool_loop_guard.py`.
The canonicalization uses Python's standard-library
[`json`](https://docs.python.org/3.12/library/json.html) encoder, with deterministic
sorting and compact serialization.

## Focused verification

From a development environment with pytest and the project dependencies:

```bash
uv run python -m pytest -q tests/test_tool_loop_guard.py \
  tests/validation/test_cross_mode_validation.py \
  tests/test_agent_runner_reasoning_history.py \
  tests/test_responses_reasoning_history.py \
  tests/a2a/test_a2a_reasoning_history.py \
  tests/test_agent_response_stream.py
```

The guard suite covers recovery, explicit failure, hard caps, duplicates,
canonical fingerprints, mixed sequential/parallel ordering, protocol pairing,
pre-dispatch side-effect prevention with real `LocalAgent` hooks, concurrent job
isolation, and ACP/A2A input resumption.
