# Project skills and reusable scripts

AgentCrew discovers Agent Skills `SKILL.md` files in `.claude/skills`,
`.agents/skills`, `.od-skills`, and `.codex/skills`. Within each scope that is
also the collision order; directory names are scanned in sorted order. Project
skills override user skills with the same name. `.agents/skills` is the only
write target for `craft_skill`; a conflicting name in another project root is
rejected rather than silently shadowed.

## Trust and compatibility

Repository-controlled skills are **not discovered, advertised, or activated**
unless project plugins are trusted. The existing `--trusted-project-plugins`
option (or `global_settings.trusted_project_plugins` in config) now gates both
project plugins and project skills. Only enable this for repositories you trust.
User skills under your home directory retain their discovery and precedence behavior. The
project root is captured when the skills service is created (normally the
application's startup working directory).

This is an intentional compatibility change: formerly project skills were
loaded without that trust decision. Discovery retains legacy missing-name fallback
and overlong-name truncation; strict name/directory agreement is enforced for
newly crafted skills, not retroactively imposed on installed user skills. Non-map
frontmatter and non-string names/descriptions are safely ignored. Project skill paths
containing symlinks are ignored. User-scope symlink behavior is unchanged.

## Activation and bounded context

The adaptive context contains **only names, descriptions, and activation policy**,
not full instructions or bundled scripts. Its complete skill section is bounded
by the smaller of 8,000 characters and 2% of the model context estimate (four
characters/token). Without a context size the fallback is 8,000 characters.
Project metadata is prioritized before user metadata, with names sorted in each
scope. Oversized entries are skipped rather than cutting descriptions; omission
counts are included. A context too small to fit the policy omits the section.
No empty activation tool schema is registered. Tool schemas still enumerate all
trusted discovered names; the character budget applies to the adaptive catalog,
not the complete provider tool-schema payload.

`activate_skill` returns the full **body** of `SKILL.md` in a tool result. It does
not promote repository content into the base system prompt. The wrapper includes
escaped name, user/project scope, trusted state, SHA-256 of the raw `SKILL.md`,
and metadata version when supplied. Resource paths are listed lazily; resource
contents are not read during activation. Wrapper tags identify content, not a
security boundary: skill instructions cannot override system/user instructions.

Only **three distinct skills** remain active:

1. Activating A, B, C loads their full bodies.
2. Activating D replaces A's body **only in the inference copy** with
   `<skill_content_evicted name="A" reason="active-skill-limit"/>`.
3. Activating an already-active skill does not change FIFO order.
4. Reactivating A loads its full body again, evicting B.

Canonical/persisted history is retained. The tool-result role, tool name and
call ID remain intact, along with the matching assistant call. Active skill
results remain exempt from ordinary shrinking and background summarization.
Request-local activation state is rebuilt from each supplied history, so reloads
and forks inherit exactly that history's activations; unrelated conversations,
ACP/A2A sessions and job/delegation histories do not share a closure-global set.
Transfers follow the history supplied to the next agent. Sequential activation
also works within a single tool batch and request-only recovery snapshots.

Bodies above **16,000 characters** (approximately 4,000 tokens at the deterministic
four-character estimate, not a tokenizer guarantee) are rejected, not truncated.
Move longer details into reference files. Discovery caches at most 512 parsed
entries using mtime, ctime, size and inode fingerprints. Every refresh still
scans immediate skill directories to notice additions, removals and edits.

## Crafting a project skill

`craft_skill` requires a valid name, description, instructions and **at least one
script**. Instruction-only creation is rejected. Example input:

```json
{
  "name": "project-checks",
  "description": "Run repeatable project checks after implementation.",
  "instructions": "From the project root, run uv run python .agents/skills/project-checks/scripts/check.py through the command-execution tool. Review failures before proceeding.",
  "scripts": [
    {
      "path": "scripts/check.py",
      "language": "python",
      "content": "import subprocess\nimport sys\n\nraise SystemExit(subprocess.call([sys.executable, '-m', 'compileall', '-q', 'AgentCrew']))\n"
    }
  ],
  "metadata": {"version": "1.0", "required_env": "OPTIONAL_TASK_API_TOKEN"}
}
```

Optional fields are `compatibility` (up to 500 characters), string-to-string
`metadata`, `update_existing` (default false), and `expected_existing_hash`.
Names use 1–64 lowercase ASCII letters/digits and single internal hyphens;
descriptions are limited to 1,024 characters. Script paths must be portable,
relative `scripts/.../*.py` paths. Absolute paths, traversal, duplicate paths,
symlinks, hardlinked update files and writes outside the skill tree are rejected.
A skill is limited to 32 scripts, a 1 MB file tree, and a 128 KB `SKILL.md`.

Only **Python** is accepted in this first slice. `ast.parse` plus Python
`compile(..., 'exec')` validate syntax and compile-time restrictions without
executing, importing, or installing anything from the script. Unsupported
validators are explicitly rejected. Validation does not prove script safety,
correctness, self-containment, dependency availability or determinism; write
scripts using project-relative paths and document their dependencies. Later
execution must use the existing command-execution tool and its safeguards.

**Never embed credentials** in instructions, metadata, or scripts. Metadata may
name required environment variables; scripts should obtain their values from the
environment at execution time. Obvious private-key, AWS access-key, GitHub-token
and OpenAI-style token material is rejected before any skill files are written.
This is a conservative known-pattern check, **not** a comprehensive secret scanner.
User approval/review is still necessary.

### Approval policy

`craft_skill` is a write-capable sequential tool, not a read-only convenience.
Chat uses its normal approval flow; ACP classifies it as an `edit` and a sensitive
tool. Normal user-configured auto-approval/YOLO settings still apply.

**A2A, jobs and delegation use their existing autonomous execution policy, just
like `write_file`; these surfaces can craft a trusted project skill without an
interactive approval prompt.** This is intentional. Crafting never executes the
scripts and does not grant future command approval. No new cross-surface approval
mechanism is introduced.

### Atomic writes and updates

Publication requires **Linux `renameat2`**, libc support, and filesystem support
for atomic no-replace/exchange operations. Other platforms or unsupported
filesystems return an explicit error with no published skill change. Discovery
and activation remain platform-independent. No fallback to partial multi-file
replacement is used.

All input and syntax checks precede staging. A complete tree is staged in a
hidden sibling directory; create uses atomic no-replace and update atomically
exchanges the old/new directories. Identical requested content is a `no-op`.
Different existing content requires `update_existing=true`. Unrelated existing
files, empty directories and ordinary permission bits are preserved; updates
do not delete resources. Skill staging is removed after failure or success; a filesystem cleanup failure
is logged and returned as a warning rather than misreporting a successful atomic
publication as a failed write.
An unsupported publication attempt may leave empty `.agents/skills` parent
directories, but never a partial published skill.

The result includes `operation` (`create`, `update`, `no-op`), requested file
`paths`, a whole-tree `content_hash`, per-script static `validation`, and
`executed=false`, and any staging-cleanup `warnings`. The whole-tree hash includes sorted relative file paths and
file-content hashes, including unrelated files; it differs from activation's
single-`SKILL.md` provenance hash. Supply the previous whole-tree hash as
`expected_existing_hash` on update. User edits cause a conflict rather than
being overwritten. Without a matching hash, reload/review the skill first.
The tree is rechecked immediately before publication even when no expected hash
was supplied. This is optimistic concurrency, not a filesystem transaction
against a hostile writer changing files in the final check/rename interval;
external editors should not write concurrently during publication. Atomic
visibility does not promise crash/power-loss durability or preserve extended
filesystem attributes. Discovery is invalidated/refreshed after publication.

Automatic run-start/post-implementation triggers, non-Python validators,
portable atomic-publication backends and script-execution hooks are deferred.

## Verification

Run from the repository with the project's virtual environment:

```sh
uv run --no-sync python -m pytest verification/skills/ -q
uvx ruff check . --statistics
uvx ruff format --check .
```

The `verification/skills` tests cover FIFO eviction/reactivation, preserved
protocol envelopes, real inference preprocessing under context shrinking,
request/scope isolation, reload/fork replay, trust and discovery caching, catalog
budgets, static syntax validation, create/no-op/update/conflict cases, failed
atomic publication, path escapes, symlinks, and script non-execution.

References:
- [Agent Skills specification](https://agentskills.io/specification)
- [Python 3.12 AST and compilation checks](https://docs.python.org/3.12/library/ast.html#ast.parse)
- [Linux renameat2 atomic exchange/no-replace semantics](https://man7.org/linux/man-pages/man2/renameat2.2.html)
