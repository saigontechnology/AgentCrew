# Memory worker async-generator shutdown fix

## Confirmed ownership and sequence

`MemoryWorker._worker_loop` in
`AgentCrew/modules/memory/memory_worker.py` owned a manually created asyncio loop.
It ran each queued storage/consolidation coroutine with `run_until_complete`, then
called `loop.close()` on its shutdown sentinel or stop-event path. It did not
finalize async generators or await remaining tasks. A `BaseException`, including
`CancelledError`, could bypass even that bare close.

This reproduces the reported warning without network access using the installed
OpenAI SDK and an in-memory SSE response ending in `[DONE]`:

1. `openai.AsyncStream.__stream__` breaks at `[DONE]`.
2. Its inner `AsyncStream._iter_events` async generator loses its last reference.
3. asyncio's async-generator finalizer schedules `generator.aclose()` as a task
   whose coroutine is displayed as `async_generator_athrow`.
4. The memory worker stops advancing its loop and consumes the shutdown sentinel.
5. Its bare `loop.close()` destroys the pending finalizer task, producing
   `Task was destroyed but it is pending!`.

The regression fixture records the generator's qualified name, pending tasks at
loop closure, and asyncio exception-handler events. The old worker reproduces the
exact warning with the real SDK. A separate retained, partially consumed
`LocalAgent.process_messages()` stream proves provider cleanup must be awaited.

`SQLiteMemoryService.shutdown()` calls this worker's `stop()`, which sends the
shutdown sentinel and joins its thread. The memory service also calls shutdown
from its destructor. This loop is separate from the interactive application's
runtime loop; draining the application loop cannot drain the memory worker loop.

## Change

Use one `asyncio.Runner` for the worker's entire queue-processing lifetime.
Operations still share one loop, and queue/error handling is unchanged. Leaving
the runner cancels and awaits remaining tasks, finalizes active async generators,
shuts down its executor, and closes the loop, including exceptional exits. The
original cancellation object is preserved. No custom global cancellation sweep,
warning suppression, sleeps, provider mutation, or chat/overlay changes are added.

Reference: [Python 3.12 asyncio.Runner](https://docs.python.org/3.12/library/asyncio-runner.html#asyncio.Runner.close).

## Reproduce and validate

Run from the isolated worktree. The shared existing environment is used without
installing dependencies or changing dependency files:

```bash
export UV_PROJECT_ENVIRONMENT=/home/quytruong/source/github.com/daltonnyx/AgentCrew/.venv
uv run --no-sync python -m pytest verification/memory/test_worker_shutdown.py -q

uv run --no-sync python -m pytest \
  verification/memory/test_worker_shutdown.py \
  tests/chat/message/test_reasoning_cancellation.py \
  tests/chat/message/test_turn_session_cancellation.py \
  tests/test_delegate_cancellation.py \
  tests/test_agent_response_stream.py \
  tests/test_async_lifecycle.py \
  tests/test_responses_stream_guard.py \
  tests/test_tool_loop_guard.py \
  tests/test_tool_loop_config.py \
  tests/test_tool_result_summary.py -q --tb=short

uv run --no-sync python tests/validate_interactive_runtime.py
uvx ruff check AgentCrew/modules/memory/memory_worker.py verification/memory/test_worker_shutdown.py --statistics
uvx ruff check AgentCrew/modules/memory/memory_worker.py verification/memory/test_worker_shutdown.py
uvx ruff format --check AgentCrew/modules/memory/memory_worker.py verification/memory/test_worker_shutdown.py
git diff --check
```

Results:

- New regression file against original worker: **4 failed, 5 passed, 5 teardown
  errors** (teardown errors are additional reports on the same nine cases).
- New regression file after fix: **9 passed**; zero pending tasks at loop close
  and zero asyncio exception-handler events.
- Combined pytest command: **374 passed, 2 failed**.
- Interactive lifecycle script: **11 checks passed**.
- Ruff lint: **0 violations**; Ruff format: **2 files already formatted**.
- Git whitespace check: passed.

The two combined-suite failures also fail with the original worker implementation
loaded from the parent commit:

- `test_delegate_cleanup_timeout_preserves_original_error_and_finishes`:
  `caplog.text` is empty although Loguru emits the timeout warning.
- `test_prompt_is_bounded_while_hash_uses_full_source`: the existing summary
  prompt-length assertion expects `head-`, which the generated prompt omits.

## Limitations

The supplied warning has no task-creation traceback, and the available production
logs contain no matching destroyed-task entry. This proves a concrete owning path
that produces the exact warning, not unique attribution of the historical task
number. No live-provider call or full GUI/interactive exit was performed. Other
independently managed worker loops and GUI forced-thread termination were not
changed. Existing worker join timeouts remain unchanged; this fix does not promise
bounded shutdown for arbitrary cleanup that never finishes.
