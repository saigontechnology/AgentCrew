"""Bounded, trusted launch-project configuration for skill discovery only."""

import json
import os
import stat
from pathlib import Path, PureWindowsPath

from loguru import logger

MAX_PROJECT_CONFIG_BYTES = 64 * 1024
MAX_PROJECT_ROOTS = 32


def validate_project_roots(
    project_root: Path, entries: list[str | Path], *, resolved: bool = False
) -> list[Path]:
    """Validate ordered project directories; invalid input disables project discovery."""
    from AgentCrew.modules.skills.publisher import inspect_skill_entry

    try:
        if not isinstance(entries, list) or len(entries) > MAX_PROJECT_ROOTS:
            raise ValueError("skills.project_roots must be a list of at most 32 paths")
        roots = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, (str, Path)) or (
                not resolved and not isinstance(entry, str)
            ):
                raise ValueError("skills.project_roots entries must be strings")
            text = str(entry)
            path = Path(text)
            windows = PureWindowsPath(text)
            if (
                not text
                or ("\\" in text and not (resolved and path.is_absolute()))
                or ".." in path.parts
                or (
                    not resolved
                    and (path.is_absolute() or windows.drive or windows.root)
                )
                or (
                    resolved
                    and not path.is_absolute()
                    and (windows.drive or windows.root)
                )
            ):
                raise ValueError(f"Invalid project root: {text!r}")
            candidate = path if path.is_absolute() else project_root / path
            try:
                info = inspect_skill_entry(project_root, candidate)
            except FileNotFoundError:
                logger.warning(
                    "Skills: missing configured project root {}; skipping", candidate
                )
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"Project root is not a directory: {candidate}")
            canonical = candidate.resolve(strict=True)
            if canonical not in seen:
                seen.add(canonical)
                roots.append(canonical)
        return roots
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning(
            "Skills: invalid project roots; project discovery disabled: {}", exc
        )
        return []


def load_project_skill_roots(
    project_root: Path, *, trusted_project: bool
) -> list[Path]:
    """Read project config only after trust; missing settings retain the launch root."""
    if not trusted_project:
        return []

    from AgentCrew.modules.skills.publisher import (
        check_project_path,
        inspect_skill_entry,
    )

    path = project_root / ".agentcrew/config.json"
    try:
        check_project_path(project_root, path)
        try:
            metadata = inspect_skill_entry(project_root, path)
        except FileNotFoundError:
            return validate_project_roots(project_root, ["."])
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Project config must be a regular, non-hardlinked file")
        if metadata.st_size > MAX_PROJECT_CONFIG_BYTES:
            raise ValueError("Project config exceeds 64 KiB")
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise ValueError("Project config changed or is unsafe")
            raw = source.read(MAX_PROJECT_CONFIG_BYTES + 1)
        inspect_skill_entry(project_root, path)
        if len(raw) > MAX_PROJECT_CONFIG_BYTES:
            raise ValueError("Project config exceeds 64 KiB")
        config = json.loads(raw.decode("utf-8"))
        if not isinstance(config, dict):
            raise TypeError("Project config must be a JSON object")
        skills = config.get("skills", {})
        if not isinstance(skills, dict):
            raise TypeError("Project config skills must be an object")
        return validate_project_roots(project_root, skills.get("project_roots", ["."]))
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        logger.warning(
            "Skills: invalid project config {}; project discovery disabled: {}",
            path,
            exc,
        )
        return []
