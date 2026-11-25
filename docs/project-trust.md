# Project trust at startup

Interactive console and GUI chat ask before trusting an unknown launch project.
The prompt shows the **resolved, exact launch directory**, not a repository name,
parent folder, Git remote, or another clone. It explicitly warns:

> Trust enables project skills and project plugins. Project plugins can run local
> code as your user.

Choose **Trust this project** only if you trust its contents and the code its
plugins load. **Continue without trust** is the default and Escape/close action.
Console consent requires terminal stdin and stderr; redirected/nonterminal
launches never wait for trust input. GUI chat can prompt through Qt without a terminal.
A working Qt GUI/display is required; if unavailable, use `chat --console`.

The decision is resolved before project skill discovery/recovery, agent tool
registration, model context, or project plugin initialization. GUI chat creates
one QApplication early and reuses it for the dialog and chat/configuration UI.
The launch directory is captured once and passed to both plugins and skills.

## What trust enables

- Project plugin imports from the launch project's `.agentcrew/plugins/`.
- Project skill discovery/catalogs and activation from compatible skill roots.
- `craft_skill` when its existing command/editing capability requirements are met.

Trust does **not** bypass command, write, tool, or ACP client approvals. It is not
a workspace sandbox and does not constrain all project integrations or Python
imports. User-level skills and user-level plugins keep their existing behavior.
Saving a crafted skill still does not execute it.

## Resolution and precedence

1. An explicit `--trusted-project-plugins` enables trust for **this launch only**.
   It does not prompt or persist a grant.
2. An explicit `--no-trusted-project-plugins` forces this launch untrusted, even if
   a grant exists. It does not revoke the stored grant.
3. Interactive console/GUI chat with neither flag checks the exact-root store.
   For an unknown root, acceptance is saved before trust is enabled. Decline,
   close, EOF, unavailable UI, or a store error leaves trust off. Decline is not
   persisted and is not asked again during the same launch.
4. Jobs, ACP, A2A/server, and nonterminal console chat ignore stored interactive
   grants and never prompt. Use the explicit positive flag for unattended launch
   opt-in. A host launch grant does not grant trust to an ACP client's separate
   session cwd; this feature adds no client permission protocol.

Both the regular and Docker entry points share these options. For example:

```sh
agentcrew chat --console
agentcrew chat --no-trusted-project-plugins
agentcrew job --trusted-project-plugins --help
```

The last command illustrates option availability; use the job's existing task,
agent, and provider options for an actual unattended run.

### Legacy configuration migration

`global_settings.trusted_project_plugins` no longer authorizes trust. **Both
values are ignored for consent**, regardless of whether the JSON configuration
is in the checkout or selected through `AGENTCREW_CONFIG_PATH`. A legacy true
value emits a migration warning.

Remove the legacy key from custom configurations. For chat, approve the startup
prompt; for existing unattended scripts that intentionally load project code,
pass `--trusted-project-plugins` explicitly. A repository-controlled config,
marker, hook, or model instruction is never interpreted as consent.

## Persistence, identity, and revocation

Positive interactive grants are stored at:

```text
~/.AgentCrew/project-trust.json
```

The file is outside the checkout and is not selected by the project's config.
The store uses bounded, locked read/modify/write and atomic file replacement.
New directories/files use restrictive permissions where supported; on POSIX,
existing storage must be owned by the user, records/lock files must be private,
and the containing directory must not be writable by other users. Links,
junctions/reparse points, hardlinked records/locks, malformed records, and
oversized stores are rejected. Windows storage relies on the user profile's
native ACL inheritance; custom profile ACLs are not audited here. File and
supported directory fsync are used, without claiming universal power-loss or
network-filesystem durability. Lock waits are bounded.

Version 2 records match **case-preserving canonical path strings exactly**. No
case folding is used, including on Windows volumes with case-sensitive
directories. Aliases share a grant only when canonical resolution produces the
same spelling; otherwise it is safer to ask again. Siblings, children, parents,
and other clones do not inherit a grant.
The key is **path-based**, not a pinned Git commit or repository identity. If you
replace a repository at an already granted path, its grant still applies: revoke
before repurposing a path. Moving a project to a new canonical path requires a
new decision. Startup aborts if the captured directory is redirected to another
canonical path while consent is pending.

### Pre-release version 1 records

Version 1 folded path spelling on Windows, so the original approved checkout
cannot be recovered reliably from those records. All version-1 stores are
rejected, including otherwise valid-looking entries; there is no automatic
migration or reuse of grants. Startup remains untrusted and reports the store
path. Per-root revocation cannot safely infer the lost spelling either.

Stop AgentCrew processes, move `~/.AgentCrew/project-trust.json` to a private
backup such as `project-trust.v1-backup.json`, then restart interactive chat and
approve each exact displayed path again. The new file uses version 2. Keep the
backup private. An explicit launch override remains available without reading or
rewriting the legacy file; it still does not persist approval.

Revoke locally without starting chat or loading plugins:

```sh
agentcrew revoke-project-trust /exact/project/directory
agentcrew revoke-project-trust
```

Omitting the path means the current directory. Revocation takes effect on the
next launch; it does not unload code or remove model context from an existing
session. The command is also available in the Docker CLI. There is deliberately
no CLI grant command and no agent-facing trust tool.

If storage is corrupt, unsafe, or unwritable, AgentCrew reports the path and
continues without trust instead of silently enabling it or repairing records.
Inspect permissions and back up/quarantine damaged state before retrying. Do not
remove an active lock file while cooperating processes are running. If the store
would reside inside the selected launch directory (for example, launching from
an enclosing home/root directory), persistent consent is refused; choose a
normal project directory or an explicit one-launch override.

### Security boundary

This is human startup consent backed by user-owned files, not a security boundary
against arbitrary code already authorized to run as the same OS user. Such code
can alter user-owned files, including consent records. There is no config,
project marker, agent-tool endpoint, or CLI endpoint that grants consent, but
this does not make the store unforgeable to same-user programs. Keep ordinary
execution/write approvals enabled and do not treat project trust as sandboxing.

## Validation scope

Verification covers locked storage, process/thread updates, failures and
revocation, mocked console/GUI startup order, and native Qt widgets/default-safe
behavior with the offscreen platform. No live desktop interaction, external LLM
provider, plugin body, or ACP client permission exchange is needed to validate
consent. Live Windows/macOS behavior and custom ACL/network filesystem setups
have not been validated by these tests.
