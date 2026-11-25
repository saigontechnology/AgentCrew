---
compatibility: AgentCrew checkout, CPython >=3.12, uv. Standard-library-only script.
  Run from repository root. Explicit --allow-imports required; subprocess imports
  inherit environment and are not sandboxed.
description: Diagnose AgentCrew startup latency without confusing cached imports with
  real console startup. Includes an opt-in fresh-process import profiler and a phase-timing
  procedure covering version/network overlap, trust, services, agents, plugins, MessageHandler,
  ConsoleUI, and first prompt readiness.
metadata:
  category: performance
  project: AgentCrew
  side-effects: Executes explicitly approved module imports in subprocesses; does
    not itself launch chat, services, plugins, or version requests.
name: agentcrew-startup-profiling
---

<AgentCrewStartupProfiling>
  <WhenToUse>
    Use when investigating slow startup, remaining console startup time, heavy imports, or check_and_update/version-check latency. Measure first; do not turn an investigation into an unrelated startup refactor.
  </WhenToUse>
  <Preflight>
    <Step>Inspect repository structure, CONTRIBUTING.md, pyproject.toml, git status --short, git diff --stat, and registered worktrees. Read the current startup flow before choosing measurement boundaries; cached analyses and historical timings can be stale.</Step>
    <Step>Inspect AgentCrew/main.py (cli_prod, chat, version helpers), AgentCrew/app.py (AgentCrewApplication.__init__ and run_console), AgentCrew/setup.py (ApplicationSetup), and AgentCrew/modules/console/console_ui.py (ConsoleUI.__init__ and start). Follow delegated handlers when first-prompt readiness is measured.</Step>
    <Step>Record Python version, Git HEAD/branch/dirty state, actual launch command, provider/model selection, console/GUI mode, memory engine, trust policy, plugin/MCP configuration, bytecode policy, and first-run state without exposing values from environment/config secrets. Do not install dependencies or change uv.lock during profiling.</Step>
  </Preflight>
  <ImportProfiling>
    <Safety>The script imports only explicitly named AgentCrew modules in subprocesses, but imports execute code and inherit the environment. Review module and parent-package initialization before using --allow-imports. This is not a security sandbox or a network blocker. It does not invoke CLI callbacks, construct UI/services, grant project trust, or call version endpoints itself. Do not profile unknown modules on the assumption that import is harmless.</Safety>
    <BasicCommand>From the repository root: uv run --no-sync python -B .agents/skills/agentcrew-startup-profiling/scripts/profile_imports.py AgentCrew --repeat 3 --allow-imports</BasicCommand>
    <Targets>After side-effect review, replace AgentCrew with explicit targets such as AgentCrew.main, AgentCrew.app, or AgentCrew.modules.console.console_ui. Each target/sample gets a fresh Python interpreter. Multiple targets are independent experiments, not phases that can be summed.</Targets>
    <TraceCommand>Add --importtime --top 10 to enable CPython -X importtime and report the largest self-time rows from each child. Keep uninstrumented samples for latency comparisons: importtime tracing adds overhead. The trace includes interpreter and harness imports, not only imports beneath the target; inspect module names before attributing cost.</TraceCommand>
    <Output>JSON reports every sample, median/min/max milliseconds, module preloading status, process wall time, optional import self/cumulative microseconds, Python version, and measurement limitations. Child application stdout/stderr is not echoed; only structured timing data and recognized importtime rows are retained. A failed, timed-out, or malformed sample exits nonzero rather than silently reducing the sample count.</Output>
    <Definitions>
      <Cold>first_import_ms is the selected import call in a fresh interpreter after the small profiling harness imports. If already_loaded_before is true, it is not cold. This is process-cold, NOT an operating-system page-cache, bytecode-cache, or machine-cold experiment. Do not clear caches or drop filesystem caches.</Cold>
      <Warm>cached_import_ms is a second import of the same name in the same process; it primarily measures the sys.modules fast path. It is NOT warm application startup, a second service setup, or a repeat ConsoleUI construction.</Warm>
      <Wall>process_wall_ms includes interpreter launch, harness imports, selected imports, optional trace output, and process shutdown. It excludes the outer uv launcher and does not represent full chat startup. Do not subtract it mechanically to infer network or UI time.</Wall>
      <Bytecode>-B and PYTHONDONTWRITEBYTECODE disable new bytecode writes but existing bytecode may still be read. Preserve the same policy before/after an optimization and state it in the report.</Bytecode>
    </Definitions>
  </ImportProfiling>
  <ActualConsoleStartup>
    <Approval>A live console run can load credentials, create/update memory databases, start workers, contact providers/MCP endpoints, and execute trusted plugins. Obtain approval for these effects and the exact command/data locations first. Never auto-grant trust or auto-accept an update prompt for a benchmark. Separate human prompt wait time from computational startup. Do not use the import profiler as a substitute for this run.</Approval>
    <Instrumentation>Use time.perf_counter_ns() for independent start/end timestamps around actual calls in the current flow, not cumulative timestamps mislabeled as per-phase durations. Prefer a temporary, explicit harness or localized instrumentation in an isolated worktree; no permanent production hooks unless requested. Use synthetic or approved data paths, retain normal shutdown/cleanup, and remove only task-owned instrumentation afterward.</Instrumentation>
    <Phases>
      <Phase>Process launch to entrypoint import/callback entry; importing AgentCrew.main already imports AgentCrew.setup, so starting a clock inside chat misses earlier work.</Phase>
      <Phase>Local version lookup and first version display; start the background version worker separately and record its own network duration without logging release bodies or credentials.</Phase>
      <Phase>AgentCrew.app import, AgentCrewApplication construction, and mode selection/optional Qt import.</Phase>
      <Phase>version_thread.join wait duration and whether the worker is still alive, then _show_version_result display/prompt time.</Phase>
      <Phase>run_console lazy imports; project-trust resolution and any user decision time; interactive runtime start; runtime model resolution.</Phase>
      <Phase>setup_services; setup_agents; restore_last_agent; initialize_plugins, including awaited runtime work.</Phase>
      <Phase>MessageHandler construction; global configuration read; ConsoleUI construction, including handlers imported inside its constructor.</Phase>
      <Phase>ui.start preparation through the actual first input-ready signal/prompt. Do not time the entire blocking UI loop and call it startup. Report ConsoleUI constructor time and first-prompt readiness separately.</Phase>
    </Phases>
    <CurrentOrdering>At authoring time chat displays the local version, starts _start_version_check_background, imports/constructs the application, checks Qt availability, joins with timeout=2.0, calls _show_version_result, and only then calls run_console. Therefore setup_services, setup_agents, plugins, and ConsoleUI construction occur AFTER that join, not concurrently with the version request. Re-read current code before making this claim in a future investigation.</CurrentOrdering>
    <NetworkAccounting>Distinguish background request duration from foreground join wait. A bounded join can return while the request is still running; do not assume completion or attribute the entire request to the critical path. Measure overlap with timestamps; never add overlapping intervals together. Stubbed/offline measurements must be clearly labeled and never presented as live-network results. Existing approximately-500ms/1.2s estimates are historical examples, not measurements of the current checkout.</NetworkAccounting>
  </ActualConsoleStartup>
  <OptimizationAndVerification>
    <Rule>Preserve the established version-check pattern: immediate local version feedback, daemon background request, deliberate join/result display before UI start. If an optimization changes that ordering or timeout behavior, request approval and preserve error handling, thread visibility, and shutdown behavior.</Rule>
    <Rule>Defer dependency-heavy imports at the actual first-use boundary, following existing service patterns. Do not move ConsoleUI work out of the measured path just to improve a partial metric. Docker-only startup changes belong in main_docker.py rather than shared runtime modules.</Rule>
    <Rule>Implement one approved change at a time, compare baseline and candidate under the same command/configuration, and report sample counts plus median/range. Keep process-cold results separate from cached-import results and interactive first-run effects.</Rule>
    <Rule>Reuse agentcrew-focused-validation for changed Python files; do not duplicate its validator or run deprecated tests/. Read final edited files and inspect the scoped Git diff. Preserve dependency files and unrelated WIP; do not commit or push without approval.</Rule>
  </OptimizationAndVerification>
  <Deliverable>
    Report the exact command and checkout, measurement boundaries, sample count, import and actual-console tables separately, first-version/first-prompt elapsed time, network request versus join wait, ConsoleUI time, confirmed bottleneck, one targeted recommendation, and limitations. If actual console startup was not measured, say so explicitly and provide an approved next measurement rather than guessing remaining time.
  </Deliverable>
  <References>
    <Source>Repository: AgentCrew/main.py, AgentCrew/app.py, AgentCrew/setup.py, AgentCrew/modules/console/console_ui.py, CONTRIBUTING.md, pyproject.toml.</Source>
    <Source>https://docs.python.org/3.12/using/cmdline.html#cmdoption-X documents importtime self/cumulative time and its multithreading caveat; traces from imports that spawn threads need extra caution.</Source>
    <Source>https://docs.python.org/3.12/library/time.html#time.perf_counter_ns documents the high-resolution elapsed-time counter.</Source>
  </References>
</AgentCrewStartupProfiling>
