# Project skills and reusable scripts

AgentCrew discovers Agent Skills `SKILL.md` files in `.claude/skills`,
`.agents/skills`, `.od-skills`, and `.codex/skills` under the user home and the
exact trusted project root. Within each scope that is also the root collision
order. Each root is scanned shallow-first, up to **four directory levels below
the root**, with NFC-normalized relative POSIX path ordering within each level
(raw path breaks normalization ties). Direct skills beat nested same-name skills
within a root; the first configured root still wins within a scope. Project
skills override user skills with the same name. Discovery does not search the
repository for additional roots such as `packages/app/.agents/skills`, nor scan
singular `.agent/skills` directories.

Only exact `SKILL.md` entries form bundle boundaries: discovery does not descend
into a bundle's resources, even when its metadata is malformed. The familiar
skip directories (`.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`)
remain excluded. Hidden grouping directories are not traversed. Direct hidden
user skills remain supported; hidden project entries in `.agents/skills` remain
excluded for publication safety.

Discovery visits at most **2,000 candidate directories per configured root** and
**16,000 across all user/project roots per refresh**. The configured roots
themselves do not consume this budget. Candidate storage is bounded, but sibling
names must still be enumerated to choose a deterministic lexical prefix. Warning
logs identify roots truncated by depth or directory budgets; debug logs identify
same-name winners/losers and duplicate physical targets.

`.agents/skills` is the only write target for `craft_skill`; a conflicting name
in another project skill location is rejected rather than silently shadowed.
An existing nonempty direct target without `SKILL.md` is treated as a grouping
container and cannot be crafted into, even with `update_existing=true`; nested
skills cannot accidentally be hidden or overwritten by direct authoring.

## Trust and compatibility

Repository-controlled skills are **not discovered, advertised, or activated**
unless the launch project is trusted. Interactive console/GUI chat offers a
first-run consent prompt and remembers positive grants for the exact canonical
project path. Unattended launches require explicit `--trusted-project`;
`--no-trusted-project` forces an untrusted launch. The legacy config
boolean no longer grants consent. See [Project trust](project-trust.md) for
precedence, persistence, revocation, and security boundaries.
User skills under your home directory retain their discovery and precedence behavior. The
project root is captured at application startup and shared with the plugin
manager, rather than following later working-directory changes.

This is an intentional compatibility change: formerly project skills were
loaded without that trust decision. Discovery retains legacy missing-name fallback
and overlong-name truncation; strict name/directory agreement is enforced for
newly crafted skills, not retroactively imposed on installed user skills. Non-map
frontmatter and non-string names/descriptions are safely ignored. Linked project
skill entries are ignored; an unsafe canonical lock/recovery root can instead fail
closed with an error. Junctions/reparse points are treated conservatively too.
Direct user skill-directory and `SKILL.md` links remain supported as leaf
bundles, with physical targets deduplicated within the scope. Linked grouping
directories and linked directory/marker entries below the first level are not
followed. Project links, nonregular skill
files, and hardlinked skill files remain rejected before recursive descent or
payload reads. These checks do not provide race-proof isolation from concurrent,
non-cooperating filesystem changes.

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
entries using mtime, ctime, size and inode fingerprints. Every refresh rescans
within the discovery bounds above to notice additions, removals and edits.

## Crafting a project skill

`craft_skill` is offered only to trusted-project agents with both
`command_execution` and `file_editing` enabled, their services available, and the
built-in `run_command` and `write_file` capabilities actually registered. Known
ACP replacements (`acp_run_command` / `acp_write_file`) also count within those
enabled groups. Group and individual exclusions still apply; global services,
configuration names, and unrelated same-name tools alone do not grant authoring.
The adapter must successfully register at least one provenance-valid command
capability and one editing capability in the current sync before AgentCrew's
`craft_skill` is registered. A failed prerequisite suppresses authoring and its
instruction until a successful resync; known ACP alternatives can provide a
successful fallback.
The configuration group is `command_execution`, not `code_execution`.

Eligible agents receive one authoring instruction through `agent.tool_prompts`,
including when the skill catalog is empty. It is not adaptive catalog content.
Use crafting on user requests to save repeatable project workflows, reuse or wrap
existing skills/scripts where appropriate, and suggest/get confirmation before
unsolicited persistent creation. Do not craft one-off tasks. Crafting saves and
statically validates; it does not execute scripts. Activation eligibility is
unchanged and does not require these authoring capabilities.

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
relative `scripts/.../file.ext` paths matching the declared language. Absolute paths, traversal, duplicate paths,
symlinks, junctions/reparse points, hardlinked update files and writes outside the
skill tree are rejected. New skill/script paths also reject reserved Windows
device names, trailing dots/spaces, ADS/invalid forms, and case/Unicode-normalized
collisions on every platform so the resulting tree remains portable.
A skill is limited to 32 scripts, a 1 MB file tree, and a 128 KB `SKILL.md`.

The validator registry deliberately accepts only these four languages:

| Language | Extensions | Static validator |
| --- | --- | --- |
| `python` | `.py` | `ast.parse` plus `compile(AST, ..., 'exec', dont_inherit=True)` |
| `bash` | `.sh`, `.bash` | Installed Tree-sitter Bash grammar |
| `powershell` | `.ps1`, `.psm1` | Installed Tree-sitter PowerShell grammar |
| `javascript` | `.js`, `.mjs`, `.cjs` | Installed Tree-sitter JavaScript grammar |

Tree-sitter uses AgentCrew's pinned `tree-sitter-language-pack` runtime and checks
both its manifest and local grammar availability before loading grammar data.
Missing/unavailable parsers fail closed with an explicit error; crafting does not
download/install grammars or invoke Bash, PowerShell, or Node. ERROR and MISSING
syntax nodes are rejected. Fresh parser instances avoid shared mutable parser state.
Each script reports its language and validator identity; the result explicitly
identifies validation as `static_syntax_only`.

Validation never executes, imports, or installs anything from the authored script.
It does not prove semantics, runtime safety, correctness, self-containment,
dependency availability or determinism. Tree-sitter grammar acceptance is not
interpreter-specific semantic validation. Use project-relative paths and document
dependencies. Later execution must use the existing command-execution tool and
its safeguards. Unsupported languages and language/extension mismatches are rejected.

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

### Complete-tree publication on Linux, Windows and macOS

The service uses a `SkillPublisher` strategy rather than requiring Linux APIs.
Every backend publishes a complete validated hidden sibling tree; **final skill
files are never updated one by one**. No Transactional NTFS/TxF is used.

| Backend | Create | Update | Result guarantee |
| --- | --- | --- | --- |
| `linux-renameat2` (preferred when libc/kernel/filesystem support it) | `RENAME_NOREPLACE` | `RENAME_EXCHANGE` | `atomic-no-replace` / `atomic-exchange` |
| `portable-journaled-swap` (Windows, macOS, and unsupported Linux capabilities) | Same-parent rename into an absent target | Journaled target-to-backup, stage-to-target renames | `recoverable-staged-swap` |

The portable path is selected automatically when the Linux capability is absent
or unsupported. Permission, conflict, and disk errors are not silently treated
as lack of atomic-exchange support. All artifacts stay in `.agents/skills` on the
same volume. The target is rechecked under the publication lock immediately
before a rename; cooperating concurrent creates cannot overwrite each other.

**Portable updates have a brief missing-target window for external readers**
between the two directory renames. AgentCrew discovery, catalog/activation reads,
and publishers share the lock and do not consume that gap. This is recoverable
publication, not a portable atomic directory exchange. The portable backend also
journals creation so interrupted publication can be classified consistently.

Identical requested content is a `no-op` (`publication_backend=none`,
`publication_guarantee=unchanged`). Different existing content requires
`update_existing=true`. Unrelated files and empty directories are preserved;
updates do not delete resources. POSIX ordinary permission bits are preserved,
but extended attributes, ownership, and custom Windows ACLs are not copied.
New staged objects inherit platform/parent ACL behavior; review project/skill
permissions if you depend on per-skill ACLs. POSIX mode bits are not a Windows
security or consistency guarantee. Cleanup may clear read-only attributes only
on unpublished staging/backup trees; it never changes the live tree's ACL to
force publication.

### Locking, interruption recovery and limits

A reentrant shared project lock covers recovery, discovery, activation lookups,
and publication. It uses nonblocking `fcntl.flock` on Unix or a nonblocking
`msvcrt` byte lock on Windows, with a **5-second acquisition bound**. The lock file
is `.agents/skills/.skill-publish.lock`; it persists deliberately to prevent
lock-inode races. **Do not unlink it while AgentCrew processes are running.**
Trusted project reads require permission to open/create this lock file, even if
no crafting is requested. When the canonical skill root does not yet exist,
read-only discovery does not create it. Publication creates it only after input,
syntax, path, size, and secret checks. An invalid canonical link/reparse root
fails closed; untrusted projects do not run recovery or acquire project file locks.

Portable transactions use unique `.skill-<name>-<operation-id>.stage`, `.backup`,
`.journal.json`, and `.journal.tmp` sibling names. The version-1 JSON journal is
bounded to 8 KB and stores validated relative sibling names, operation/skill
identity, a captured-project fingerprint, old/new content hashes, and phase.
It contains no authored script body. Journal updates use a flushed temporary
file and atomic file replacement, with file/parent-directory fsync where
supported. Phases are `prepared`, `old_moved`, `new_moved`, `verified`, and, for
rollback retry, `rolling_back`/`rolled_back`.

Recovery runs before trusted discovery/publication/activation lookups:

- If the old target was moved and the target is missing, a hash-valid backup is
  restored by default, including when the staged tree has gone missing.
- If the complete target matches the new hash and staging has been consumed,
  the committed target is retained and validated backup/journal cleanup finishes.
- If an uncommitted create/update is aborted, its validated staging tree is
  removed only after rollback is recorded. Repeated recovery is idempotent.
- Journal-derived names, root identity, containment, symlinks/junctions/reparse
  points, hardlinks, and tree hashes are checked before recovery renames/deletes.
  Tree traversal inspects each immediate child with non-following metadata and
  validates it before queuing a directory for descent. Fresh `lstat` supplies
  hardlink counts, which Windows cached `DirEntry.stat` does not provide.
  Hashing, directory preservation, and cleanup share this deterministic snapshot.
  Cleanup validates
  the complete snapshot before changing permissions, then uses non-recursive
  unlink/rmdir operations; it does not send the tree to a second recursive walker.
  Conflicting journals, malformed/torn journals, or hash disagreements preserve
  evidence and block project skill reads/writes with the journal location and
  actionable recovery guidance. Do not delete a backup or journal blindly:
  stop writers, inspect the indicated trees, resolve the inconsistency, and let
  trusted refresh retry. Partial failed cleanup can require this manual review.

Known Windows sharing/access/directory-interference errors get at most **three
attempts**, with 25 ms and 50 ms backoff. If retries exhaust, the operation rolls
back where hashes make that safe; otherwise the journal/backup is retained.
A verified commit with a retryable cleanup problem returns a warning and retains
its journal for a later cleanup attempt. New writes to that same skill are refused
until cleanup resolves; unrelated skills and verified reads remain available.
Hidden artifacts are never skills.
Orphan staging without a journal (for example a process stopping before journal
creation, or failed Linux post-swap cleanup) is ignored, not guessed/deleted by
recovery; inspect it manually before removing it.

These are **local-filesystem, cooperating-AgentCrew guarantees**. Other writers
and external readers must not rely on the AgentCrew lock: stop editors during
publication. There remains a hostile external-writer/TOCTOU caveat, including the
portable final absence-check/rename interval. Network shares, cloud placeholder
reparse points, unusual filesystems, ACL policies, and open-handle behavior may
prevent publication or safe automatic recovery. Reparse points are rejected
conservatively. This is process-interruption recovery, **not a guarantee of
crash/power-loss durability**: staged file data is not promised durably synced,
and directory fsync is unavailable on some platforms. Unsupported fsync is
reported in warnings; unexpected journal I/O failures retain evidence.

Windows/macOS backend-selection and path/adapter behavior are exercised by
forced-platform/mocked tests on Linux, alongside a real local cross-process lock
test. **No live Windows/macOS execution is claimed by this validation.**

The result includes `operation`, requested `paths`, whole-tree `content_hash`,
per-script `validation`, `validation_scope=static_syntax_only`, `executed=false`,
`publication_backend`, `publication_guarantee`, `recovery_performed`, and cleanup/
recovery `warnings`. The whole-tree hash covers sorted relative file paths and
file-content hashes (including unrelated files); it is distinct from activation's
single-`SKILL.md` hash. Use the previous whole-tree hash as
`expected_existing_hash`; concurrent content changes cause a conflict. Discovery
and parsed-entry caches are refreshed after publication/recovery; lookups check
fingerprints rather than returning stale pre-swap instructions.

Automatic lifecycle triggers, validators beyond the four listed languages, and
script-execution hooks remain deferred.

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
atomic and forced-portable publication, interruption at each journal/cleanup
phase, idempotent recovery and ambiguity refusal, bounded lock contention and
sharing retries, Windows path/reparse safeguards, path escapes, and script
non-execution.

References:
- [Agent Skills specification](https://agentskills.io/specification)
- [Python 3.12 AST and compilation checks](https://docs.python.org/3.12/library/ast.html#ast.parse)
- [Linux renameat2 atomic exchange/no-replace semantics](https://man7.org/linux/man-pages/man2/renameat2.2.html)
- [Python Windows nonblocking byte locks](https://docs.python.org/3.12/library/msvcrt.html#msvcrt.locking)
- [Python Unix file locks](https://docs.python.org/3.12/library/fcntl.html#fcntl.flock)
- [Python non-following directory-entry metadata and Windows cache limits](https://docs.python.org/3.12/library/os.html#os.DirEntry.stat)
