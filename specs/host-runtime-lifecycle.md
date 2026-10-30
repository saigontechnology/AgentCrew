# A2A, ACP, and job owner-loop lifecycle

## Phase 1: shared foundation

This branch starts at `434a7984a647cdc2934b36883bdcec6b567fa434`. The shared
foundation and A2A/ACP adapters are implemented and validated. The jobs adapter
is implemented and locally validated, pending architectural review. Console and
GUI behavior are unchanged.

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

## Phase 2: A2A adapter

`run_server()` uses the host lifecycle with application-owned primary and
code-analysis LLMs and `runner.run` available to onboarding. `A2AServer.serve()`
awaits `uvicorn.Server.serve()` on that loop; synchronous `start()` remains a
compatibility entry point.

Cancellation waits for startup to finish or fail, then requests graceful Uvicorn
shutdown and awaits the server task rather than immediately canceling its
lifespan. Fatal Uvicorn startup exceptions are returned through the server task
before being re-raised by the parent, preserving
`SystemExit` while still running cleanup. This is verified with a real occupied
local TCP port.

Lifespan shutdown drains SDK request handlers before session and task stores.
`aclose()` serializes cleanup, deduplicates resources by identity, supports stores
with optional synchronous/asynchronous `close()` methods, and records failures
without skipping remaining resources. Repeated cleanup does not close resources
twice. Application-owned providers close after server/lifespan cleanup, before
the Runner closes.

Executor, tool, artifact, task/session-store, authentication, resubscription and
web-route business logic is unchanged. Protocol validation uses the real A2A v1
SDK routes and in-memory stores with a fake executor, not live LLM calls.

## Phase 3: ACP adapter

`run_acp()` uses the same host Runner for setup/onboarding, ACP serving, and
application provider shutdown. It does not use the console runtime or create a
dedicated event-loop thread. Existing default-agent, model, MCP, memory,
reasoning, plugin behavior, and ACP SDK argument/field names are preserved.

`AgentCrewAcpAgent` registers in-flight session operations using an agent-local
async context. Global shutdown stops admitting operations, cancels and awaits
outstanding requests, then snapshots all remaining sessions and session-close
tasks. New/load/resume/fork requests cannot recreate resources during teardown.
Prompt cancellation continues using the existing `cancelled` flag and task
cancellation; no additional session event or state-constructor parameter is added.

`close_session()` shares a cleanup task between concurrent callers. It awaits
prompt unwind and persistence without holding the session lock, then performs
terminal release, builtin-tool restoration, MCP/config cleanup, and state/lock
removal. Cleanup continues when an individual step fails and reports the collected
errors.
Repeated close does not release resources twice; persisted sessions remain
available to a later load/resume. Self-waits are rejected instead of deadlocking.

`aclose()` coordinates this session-close path, clears the connection last, and
is idempotent. `run_acp_agent()` closes the constructed agent in `finally` for
normal/EOF return, cancellation, startup, and protocol failures. A protocol error
remains primary if ACP cleanup also fails. Application-owned and dedicated LLMs
close only afterward on the owner loop, before Runner closure.

The SDK can close its connection before returning from `run_agent`. Terminal
release/kill is therefore best-effort after a real client disconnect; existing
terminal helpers log that failure and clear local tracking. Validation includes
that case, but does not claim that an absent client confirmed remote terminal
termination.

## Phase 4: jobs adapter

`run_job()` enters the host lifecycle once. Setup receives
`application_owned_llms=True`, `need_memory=False`, and `runner.run` for onboarding.
The lightweight agent uses the application-owned primary service; configured
local agents retain dedicated ownership. Cached templates and worker-owned
clients are not added to the host close list. No plugin activation is added.

A job-local coroutine binds its inference scope and resets its token in the same
task on every exit. It preserves the scope helper's existing inheritance of an
already-active caller scope. Agent selection uses the existing async selection
method. Files are prepared in input order using a lazily imported `FileHandler`
and `async_process_file()`. Existing grouped user/content file blocks and skipping
of falsey file results are unchanged. All schema attempts directly await
`run_agent_loop()` on the same loop and pass the stable job scope.

After attempts finish, the coroutine awaits a local synchronous token-file writer
via `asyncio.to_thread()`, before final response formatting. The write contents
and placement relative to attempts/formatting are preserved; disk I/O no longer
blocks the event loop, and provider I/O never moves to the executor. Application
shutdown still runs on the owner loop before Runner closure.

Source inspection showed that parts of the initial jobs handoff described a
different contract than the approved base. The user confirmed preserving the
actual `d8ba1c65` contracts: a missing named local agent raises `ValueError` (no
current-agent fallback); four invalid schema attempts return the cleaned last
response rather than raise; and the usage file retains the flat dataclass for the
last schema attempt, not a new cross-attempt total. Existing inner empty-response
retry usage merging, schema parsing/validation, warning messages, fence cleanup,
public arguments, and CLI behavior remain unchanged.

Adapters must stop accepting work and perform their own protocol-specific drain
before returning to the shared helper. The helper's final task drain is a safety
net, not a substitute for session, terminal, handler, or store cleanup.

## Credential-free validation

Run from this worktree:

```sh
uv run python scripts/validate_host_lifecycle.py
uv run python scripts/validate_a2a_lifecycle.py
uv run python scripts/validate_acp_lifecycle.py
uv run python scripts/validate_job_lifecycle.py
uv run python scripts/validate_interactive_runtime.py
uvx ruff check --statistics AgentCrew/app.py AgentCrew/setup.py AgentCrew/modules/a2a/server.py scripts/validate_host_lifecycle.py scripts/validate_a2a_lifecycle.py AgentCrew/modules/acp/agent.py scripts/validate_acp_lifecycle.py scripts/validate_job_lifecycle.py
uvx ruff format --check AgentCrew/app.py AgentCrew/setup.py AgentCrew/modules/a2a/server.py scripts/validate_host_lifecycle.py scripts/validate_a2a_lifecycle.py AgentCrew/modules/acp/agent.py scripts/validate_acp_lifecycle.py scripts/validate_job_lifecycle.py
git diff --check
```

The host foundation harness covers normal operation, startup/operation/cancellation
failures, same-loop exactly-once provider closure, task/descendant drain ordering,
continued application cleanup after task cleanup failures, ownership deduplication,
console keyword compatibility, and exclusion of worker-owned and cached clients.
It also exercises two streamed SDK requests sharing one local HTTP/1.1 connection,
with loop exception-handler, unraisable-exception, ResourceWarning, and GC checks.
It does not contact external endpoints or require API credentials.

The A2A harness covers concurrent JSON-RPC calls, authentication, stable task and
context IDs, terminal SSE resubscription, input-required resume, owner/agent
pending-tool partitioning, stale-task reconciliation, active SDK producer
cancellation, cleanup idempotence/failures, startup failure, real Uvicorn
cancellation during startup and after listening (including socket closure), a real
port conflict, and application entry-point wiring. Its six checks pass on Python
3.13.3 and Python 3.12.10. SDK cancellation diagnostics and
the deliberately occupied-port diagnostic on stderr are expected; captured loop
exceptions, unraisables and resource warnings must remain empty.

The ACP harness has ten checks, including cross-session startup isolation, covering
persisted new/load/resume/fork sessions,
nominal prompt cancellation, concurrent global and explicit session close,
cancelled cleanup waiters, cancellation during MCP discovery, per-step cleanup
failure continuation, connection clearing, context reset, real-SDK EOF using
in-memory streams, protocol failures, construction failure after setup-owned
clients exist, application setup failures, and exactly-once owner-loop client
closure without closing cached templates. Its local HTTP/1.1 provider serves two
ACP prompts over a retained SDK client/connection and captures loop exceptions,
unraisables, ResourceWarnings, and post-close GC. Fixture providers, client
connections, model-controller actions, and MCP discovery are fakes; session
orchestration, persistence, tool restoration, terminal-release helpers, and the
SDK HTTP transport are exercised directly. All ten checks pass on Python
3.13.3 and Python 3.12.10. The simulated disconnected-client terminal warnings
are expected.

The job harness has six groups covering schema outcomes, named/lightweight
ownership, ordered fake and real ordinary-file processing, scope inheritance and
reset, setup/selection/provider/schema/output-file/cancellation failures, continued
cleanup after finalizer failures, existing empty-response retries, and last-attempt
usage output. It exercises the real agent runner and response-stream contract.
A local HTTP/1.1 SSE server checks connection reuse across schema attempts through
the OpenAI SDK's default wrapper. It asserts one exact loop/client identity,
exactly-once owner-loop closure, untouched cached templates, and empty loop
exception, unraisable, ResourceWarning, and post-close GC captures. Its six groups
pass on Python 3.13.3 and Python 3.12.10. File/vision conversion extras and live
provider calls are not exercised.

Memory-worker LLM cleanup, process-wide cache ownership, live-provider and live
ACP-editor smoke tests, GUI, and reconciliation with newer main remain residuals.
The originally reported first-ever-stream failure has not been reproduced.
No tests from the deprecated `tests/` directory are run.

## References

- [Python 3.12 Runner](https://docs.python.org/3.12/library/asyncio-runner.html)
- [Task cancellation](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation)
- [Off-loop synchronous I/O](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.to_thread)
- [Uvicorn async server usage](https://www.uvicorn.org/#config-and-server-instances)
