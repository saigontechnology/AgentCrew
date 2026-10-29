# Console async runtime lifecycle

## Problem

A retained async provider client can keep HTTP connections bound to the event loop that opened them. Previously, console input and response operations each used a separate `asyncio.run()`. Reusing or destroying the retained client after that loop closed could raise `RuntimeError: Event loop is closed`. xAI's old workaround replaced the client when the loop changed, abandoning the old connection pool for later garbage collection.

This mechanism was reproduced locally for the two-turn/shutdown lifecycle. The precise trigger for an error after a first-ever response remains unconfirmed.

## Ownership and ordering

The console application explicitly owns an `InteractiveAsyncRuntime`: one non-daemon thread and continuously running asyncio loop. The runtime is injected into ConsoleUI, onboarding, and voice completion. There is no process-global runtime singleton. A ContextVar propagates the existing owner into legacy synchronous bridges invoked by async work or `asyncio.to_thread`; it never creates a runtime.

Normal input and voice turns share a console-level async lock. History loading, model changes, and configuration reload/import apply their async lifecycle work on that same loop. Evolution review callbacks may submit nested work without taking the turn lock: their parent EventBus emission can be awaiting a synchronous handler in an executor thread. The runtime itself does not serialize submissions.

On exit:

1. Console stops accepting turns and requests stream cancellation.
2. Runtime rejects new submissions, cancels current operations, and waits for stream cleanup and cleanup descendants.
3. Application shutdown unloads plugins and closes agent-dedicated and application-owned clients on the owner loop.
4. Runtime finalizes async generators and the default executor, closes the loop, and joins its thread.
5. MCP cleanup runs after async application shutdown.

The console base LLM is an uncached standalone service. The base and code-analysis LLMs are application-owned and closed via ServiceManager's existing identity-based close deduplication. Cached model templates are not closed wholesale. Summary-worker clients retain their worker-loop close path; memory-worker ownership is not changed here.

xAI records its first request loop and raises a descriptive lifecycle error on cross-loop requests rather than silently discarding its client.

## Verification

Run without credentials, external HTTP endpoints, or the deprecated test suite:

```sh
uv run python scripts/validate_interactive_runtime.py
```

The script checks runtime reentry, cancellation and shutdown errors, a real local HTTP/1.1 SSE server with two requests per retained client sharing a keep-alive connection (both a counted transport and the SDK's default client wrapper), same-loop/exactly-once closes, console turns and voice submission, evolution callbacks, history and config/model mutations, onboarding, startup errors, and plugin rollback. The local transport check captures loop exception-handler contexts, unraisable exceptions, and ResourceWarnings and forces collection after explicit client closure. Garbage collection is only used by this validation script, not production cleanup.

For a separately installed Python 3.12 environment that already has AgentCrew's dependencies:

```sh
uv run --no-project --python /absolute/path/to/environment/bin/python /absolute/path/to/worktree/scripts/validate_interactive_runtime.py
```

## Scope and failure limits

- GUI integration and Qt verification are excluded because GUI support is currently discontinued.
- ACP, A2A, and job teardown alignment, process-wide cache ownership, and memory-worker client cleanup remain follow-ups.
- This is credential-free lifecycle verification, not a live-provider or microphone/audio-hardware test.
- If an async operation refuses cancellation beyond the drain deadline, shutdown raises instead of closing its provider underneath it. The runtime remains available for cleanup retry; this is not reported as a successful shutdown. Python cannot safely force-stop an arbitrary blocked executor thread.

## References

- [Python 3.12: asyncio concurrency and multithreading](https://docs.python.org/3.12/library/asyncio-dev.html#concurrency-and-multithreading)
- [Python 3.12: event loop shutdown](https://docs.python.org/3.12/library/asyncio-eventloop.html#asyncio.loop.shutdown_default_executor)
- [HTTPX: async client lifecycle and connection pooling](https://www.python-httpx.org/async/)
