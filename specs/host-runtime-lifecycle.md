# A2A, ACP, and job owner-loop lifecycle

## Phase 1: shared foundation

This branch starts at `434a7984a647cdc2934b36883bdcec6b567fa434`. The shared
foundation is implemented and validated; the A2A, ACP, and job entry points have
not yet been migrated. They still have their previous lifecycle limitations.
Console and GUI behavior are unchanged in this phase.

`ApplicationSetup.setup_services(application_owned_llms=True, run_async=runner.run)`
creates standalone primary and code-analysis LLM services and tracks them for
same-loop application shutdown. The existing `interactive=True` keyword remains
compatible for the console. Neither option closes the process-wide cached
provider templates. Memory-worker and tool-summary LLMs remain worker-owned.
Onboarding continues to receive the supplied `run_async` callback through setup.

`AgentCrewApplication._host_lifecycle()` owns an `asyncio.Runner` for a host
invocation. Synchronous configuration can call `runner.run` for onboarding;
subsequent mode operations and application shutdown reuse its loop. Before
closing providers, the helper cancels outstanding loop tasks, awaits their
unwinding and cleanup descendants, and collects cleanup failures. Application
cleanup still runs if a task fails during cancellation. The Runner closes last;
non-loop-affine MCP manager cleanup follows Runner closure.

An operation/startup error remains the primary exception if shutdown also fails;
secondary failures are logged. Without a primary error, shutdown failures are
raised. Multiple shutdown failures are grouped. Cooperative cancellation is
required: a task or executor function that never returns cannot be force-stopped
safely by `asyncio.Runner`.

## Adapter phases awaiting confirmation

1. **A2A:** await Uvicorn's async serve method on the Runner loop, retaining
   lifespan reconciliation and handler/store shutdown before provider shutdown.
2. **ACP:** add idempotent session/agent teardown and await it on protocol exit
   before application provider cleanup.
3. **Jobs:** run async attachment preparation, all schema retries, inference-scope
   binding/reset, and provider cleanup on the same Runner loop.

Adapters must stop accepting work and perform their own protocol-specific drain
before returning to the shared helper. The helper's final task drain is a safety
net, not a substitute for session, terminal, handler, or store cleanup.

## Credential-free validation

Run from this worktree:

```sh
uv run python scripts/validate_host_lifecycle.py
uv run python scripts/validate_interactive_runtime.py
uvx ruff check --statistics AgentCrew/app.py AgentCrew/setup.py scripts/validate_host_lifecycle.py
uvx ruff format --check AgentCrew/app.py AgentCrew/setup.py scripts/validate_host_lifecycle.py
git diff --check
```

The host foundation harness covers normal operation, startup/operation/cancellation
failures, same-loop exactly-once provider closure, task/descendant drain ordering,
continued application cleanup after task cleanup failures, ownership deduplication,
console keyword compatibility, and exclusion of worker-owned and cached clients.
It also exercises two streamed SDK requests sharing one local HTTP/1.1 connection,
with loop exception-handler, unraisable-exception, ResourceWarning, and GC checks.
It does not contact external endpoints or require API credentials.

Adapter-specific behavior has not yet been validated in this phase. Memory-worker
LLM cleanup, process-wide cache ownership, live-provider smoke tests, and GUI remain
outside this delivery. No tests from the deprecated `tests/` directory are run.

## References

- [Python 3.12 Runner](https://docs.python.org/3.12/library/asyncio-runner.html)
- [Task cancellation](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation)
