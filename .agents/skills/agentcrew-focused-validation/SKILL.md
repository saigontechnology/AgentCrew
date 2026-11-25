---
compatibility: AgentCrew repository, Python >=3.12 and uv. Ruff is optional and invoked
  via uvx. Uses only the Python standard library; no project dependency edits. Run
  from the repository root.
description: 'Validate targeted AgentCrew Python changes without sweeping unrelated
  WIP: no-bytecode syntax checks, optional explicit import probes, and read-only Ruff
  statistics grouped by rule, file, and fix risk. Use before reporting a fix complete
  or assessing a lint cleanup.'
metadata:
  category: verification
  project: AgentCrew
  side-effects: No source edits or automatic fixes; explicit import probes execute
    module code; uvx may populate its tool cache.
name: agentcrew-focused-validation
---

<AgentCrewFocusedValidation>
  <WhenToUse>
    Use after a localized AgentCrew Python change, before reporting completion, or when triaging Ruff findings. This skill gathers evidence; it does not fix code, run application startup, commit files, or establish functional correctness.
  </WhenToUse>
  <Preflight>
    <Step>Inspect repository structure, CONTRIBUTING.md, pyproject.toml, git status --short, git diff --stat, and registered worktrees. Preserve all unrelated edits. Current pyproject.toml requires Python 3.12 or newer; do not rely on older prerequisite text in CONTRIBUTING.md.</Step>
    <Step>Prefer an isolated Git worktree for implementation. A HEAD-based worktree does not include uncommitted user work. If the problem exists only in WIP, ask before editing that WIP; never silently copy, revert, or commit it.</Step>
    <Step>Select explicit affected Python files and review import side effects before requesting import checks. Run from the repository root. Do not pass directories or broad globs. Deleted files are reviewed through Git, not passed to this script.</Step>
  </Preflight>
  <Execution>
    <Syntax>uv run --no-sync python -B .agents/skills/agentcrew-focused-validation/scripts/validate.py AgentCrew/modules/agents/message_metadata.py</Syntax>
    <Lint>Append --ruff to the command above. This runs uvx ruff check --no-cache --statistics and a JSON diagnostic pass on exactly the same files. It reports the Ruff version and groups findings by rule, file, and advertised fix applicability. It never applies fixes.</Lint>
    <Imports>After reviewing module-level side effects, append --import-module AgentCrew.modules.agents.message_metadata. Repeat the flag for additional explicitly approved modules. Each import runs in a separate process with bytecode disabled and a 30-second timeout. Imports can execute code, initialize services, or touch external state; they are not sandboxed. Output is suppressed to avoid leaking initialization data. A failure requires a separately reviewed diagnostic command; do not dump environment variables, credentials, or provider payloads.</Imports>
    <DependencyPolicy>Use uv run --no-sync to avoid implicit dependency or lock refresh. Use uvx ruff check as the established Ruff runner. uvx may populate its external tool cache or need network access; it does not install project dependencies. If tools or dependencies are unavailable, report the blocker instead of changing pyproject.toml or uv.lock.</DependencyPolicy>
  </Execution>
  <Interpretation>
    <Syntax>Compilation checks syntax without writing __pycache__; it does not resolve imports, execute code, type-check, or prove behavior.</Syntax>
    <Ruff>Record the actual Ruff version and statistics rather than assuming default rules from memory. Safe fix applicability is tool metadata, not a proof of semantic equivalence. Inspect real types, exception contracts, re-exports, closures, and output consumers before applying even an advertised safe fix.</Ruff>
    <KnownPitfalls>sqlite3.Row iteration yields values rather than dictionary keys; do not mechanically replace row.keys() iteration. Replacing NaN idioms with math.isnan can fail on huge integers. Changing ValueError to TypeError requires caller inspection. Adding logging can break consumers that parse stdout.</KnownPitfalls>
    <ExitCodes>0 means all requested checks passed; 1 means at least one check or tool invocation failed; argparse uses 2 for invalid arguments. Checks not requested are reported as SKIP, never as passing.</ExitCodes>
  </Interpretation>
  <VerificationPolicy>
    <Rule>Current user direction supersedes the older test examples in CONTRIBUTING.md: do not run tests/ because it is deprecated. Prefer focused syntax and reviewed import checks. A relevant script under verification/ may be run only after reading it and confirming its side effects, fixtures, and scope; do not automatically run the entire verification tree.</Rule>
    <Rule>For lint cleanup, first assess justified exclusions or configuration mismatches without automatically changing configuration; then consider approved safe fixes; then localized manual refactors. Never run --unsafe-fixes blindly or silence legitimate findings.</Rule>
    <Rule>When changes overlap existing WIP, compare the same selected files against a suitable baseline before assigning blame. Preserve baseline differences and report pre-existing failures separately.</Rule>
  </VerificationPolicy>
  <Finish>
    <Step>Read the exact resulting contents of every edited file. Run git diff --check and inspect git status --short plus the scoped diff. Confirm pyproject.toml, uv.lock, and unrelated user edits were not changed by verification.</Step>
    <Step>Report exact commands, selected files, concrete outcomes, skipped checks, and unresolved limitations. Syntax, imports, and lint passing do not establish functional completeness or guarantee commit safety.</Step>
    <Step>Stage only intended paths if a commit is authorized; never sweep user WIP or release dump artifacts into a commit with git add -A. Do not push or merge without authorization. If a worktree was created, commit or discard only task-owned changes and close it after delivery.</Step>
  </Finish>
  <Sources>
    Recheck current CONTRIBUTING.md and pyproject.toml before use. Project workflow preferences govern uv execution, deprecated tests/, preserving user edits, and Ruff statistics. Prior AgentCrew lint cleanup supplied the sqlite3.Row, huge-integer, exception-contract, and lock-refresh pitfalls; these are investigation prompts, not assumptions about new call sites.
  </Sources>
</AgentCrewFocusedValidation>
