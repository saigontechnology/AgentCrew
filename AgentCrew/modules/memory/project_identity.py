"""Read-only, opaque project identities shared across Git worktrees."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProjectIdentity:
    key: str
    name: str


def _identity(namespace: str, value: str, name: str) -> ProjectIdentity:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    display = " ".join(name.split())[:128] or "Workspace"
    return ProjectIdentity(f"{namespace}:{digest}", display)


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
            check=False,
        )
        return (
            result.stdout.strip()
            if result.returncode == 0 and len(result.stdout) <= 4096
            else ""
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return ""


def _explicit(root: Path) -> ProjectIdentity | None:
    path = root / ".agentcrew" / "project.json"
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return None
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"id", "name"}:
            return None
        key, name = data["id"], data["name"]
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key)
            or not isinstance(name, str)
            or not name.strip()
            or len(name) > 128
        ):
            return None
        return _identity("project", key, name)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def _remote_identity(remote: str) -> str:
    """Discard transport, userinfo, query and fragment; never persist a remote URL."""
    if len(remote) > 4096:
        return ""
    if "://" not in remote:
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", remote)
        if not match:
            return ""
        host, path = match.groups()
    else:
        try:
            parsed = urlsplit(remote)
            host, path = parsed.hostname, parsed.path
            port = parsed.port
            defaults = {"http": 80, "https": 443, "ssh": 22, "git": 9418}
            if host and port is not None and port != defaults.get(parsed.scheme):
                host = f"{host}:{port}"
        except ValueError:
            return ""
        if not host:
            return ""
    path = path.strip("/").removesuffix(".git")
    return f"{host.lower()}/{path}" if path else ""


def resolve_project_identity(cwd: str | Path) -> ProjectIdentity:
    """Resolve explicit identity, Git origin/common root, then canonical workspace.

    Reads never create configuration. Git commands are local and time bounded.
    Remote-backed clones share identity; no-origin worktrees share a common root.
    Non-Git workspaces without a marker use the captured working directory.
    """
    root = Path(cwd).expanduser().resolve()
    git_root = _git(root, "rev-parse", "--show-toplevel")
    workspace = Path(git_root).resolve() if git_root else root
    for parent in (root, *root.parents)[:64]:
        explicit = _explicit(parent)
        if explicit:
            return explicit
        if (git_root and parent == workspace) or (
            not git_root and (parent / ".agentcrew").is_dir()
        ):
            workspace = parent
            break
    if git_root:
        common = _git(
            workspace, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        common_directory = Path(common).resolve() if common else workspace
        common_root = (
            common_directory.parent
            if common_directory.name == ".git"
            else common_directory
        )
        if common_root != workspace:
            explicit = _explicit(common_root)
            if explicit:
                return explicit
        remote = _remote_identity(_git(workspace, "remote", "get-url", "origin"))
        return _identity("git", remote or str(common_directory), common_root.name)
    return _identity("workspace", str(workspace), workspace.name)
