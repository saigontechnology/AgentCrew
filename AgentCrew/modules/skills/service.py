"""Progressive skill discovery and project-local, non-executing skill authoring."""

import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path, PurePosixPath

import yaml
from loguru import logger

from .validators import validate_script

SKILL_SCAN_DIRS = [".claude/skills", ".agents/skills", ".od-skills", ".codex/skills"]
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}
MAX_BODY_CHARS = 16000
MAX_SKILL_FILE_BYTES = 128000
MAX_CRAFT_BYTES = 1000000
MAX_CACHE_ENTRIES = 512
CATALOG_POLICY = (
    "## Available Skills\n"
    "Call activate_skill for matching instructions. At most 3 distinct skills are "
    "active per history (FIFO); duplicates do not reorder, evicted skills may be "
    "reactivated. Read resources only on demand.\n"
)
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SECRETS = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|"
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"
)


def _valid_name(name: object) -> bool:
    return isinstance(name, str) and len(name) <= 64 and bool(_NAME.fullmatch(name))


def _atomic_publish(source: Path, target: Path, *, exchange: bool) -> None:
    """Publish a complete tree without a missing/partial-target window."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Atomic skill publication requires Linux renameat2 support")
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ValueError("Atomic skill publication unavailable: libc has no renameat2")
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(
        -100, os.fsencode(source), -100, os.fsencode(target), 2 if exchange else 1
    ):
        error = ctypes.get_errno()
        raise OSError(error, "Atomic skill publication failed", str(target))


def _remove_staging(stage: Path) -> None:
    """Make only the unpublished, owned staging directories removable."""
    stage.chmod(stage.stat().st_mode | 0o700)
    for root, directories, _ in os.walk(stage):
        for name in directories:
            path = Path(root) / name
            path.chmod(path.stat().st_mode | 0o700)
    shutil.rmtree(stage)


class SkillsService:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_root: str | Path | None = None,
        trusted_project: bool = False,
    ) -> None:
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.user_root = Path(user_root or Path.home()).resolve()
        self.trusted_project = trusted_project
        self._skills: dict[str, dict] = {}
        self._cache: OrderedDict[str, tuple[tuple, dict | None]] = OrderedDict()
        self._lock = threading.RLock()
        self.refresh()

    def _discover(self) -> None:
        skills: dict[str, dict] = {}
        roots = [(self.user_root, "user")]
        if self.trusted_project:
            roots.append((self.project_root, "project"))
        for root, scope in roots:
            for rel_dir in SKILL_SCAN_DIRS:
                base = root / rel_dir
                try:
                    if scope == "project":
                        self._check_project_path(base)
                    entries = sorted(base.iterdir(), key=lambda p: p.name)
                except (OSError, ValueError):
                    continue
                for entry in entries:
                    if entry.name in _SKIP_DIRS or (
                        scope == "project"
                        and rel_dir == ".agents/skills"
                        and entry.name.startswith(".")
                    ):
                        continue
                    path = entry / "SKILL.md"
                    try:
                        if not entry.is_dir():
                            continue
                        if scope == "project":
                            self._check_project_path(path)
                        skill = self._load_skill(path, scope)
                    except (OSError, ValueError, UnicodeError, RecursionError):
                        continue
                    if not skill:
                        continue
                    existing = skills.get(skill["name"])
                    if existing and existing["scope"] == scope:
                        continue
                    skills[skill["name"]] = skill
        self._skills = skills

    def _load_skill(self, path: Path, scope: str) -> dict | None:
        stat = path.stat()
        if stat.st_size > MAX_SKILL_FILE_BYTES:
            return None
        fingerprint = (
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            stat.st_size,
            stat.st_ino,
            scope,
        )
        key = str(path)
        cached = self._cache.get(key)
        if cached and cached[0] == fingerprint:
            self._cache.move_to_end(key)
            return cached[1]
        with path.open("rb") as source:
            raw_bytes = source.read(MAX_SKILL_FILE_BYTES + 1)
        if len(raw_bytes) > MAX_SKILL_FILE_BYTES:
            return None
        raw = raw_bytes.decode("utf-8")
        name, description, body = self._parse_skill_md(raw, key)
        skill = None
        if name and description:
            parts = re.split(r"(?m)^---\s*$", raw.lstrip("\ufeff"), maxsplit=2)
            frontmatter = self._parse_yaml_lenient(parts[1], key) or {}
            metadata = frontmatter.get("metadata")
            version = metadata.get("version", "") if isinstance(metadata, dict) else ""
            skill = {
                "name": name,
                "description": description,
                "location": key,
                "body": body,
                "scope": scope,
                "trusted": True,
                "content_hash": hashlib.sha256(raw.encode()).hexdigest(),
                "version": str(version),
            }
        self._cache[key] = (fingerprint, skill)
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return skill

    def _parse_skill_md(self, raw: str, path: str):
        parts = re.split(r"(?m)^---\s*$", raw.lstrip("\ufeff"), maxsplit=2)
        if len(parts) != 3 or parts[0].strip():
            return None, None, ""
        frontmatter = self._parse_yaml_lenient(parts[1], path)
        if not frontmatter:
            return None, None, ""
        name = frontmatter.get("name") or Path(path).parent.name
        description = frontmatter.get("description")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not description.strip()
        ):
            return None, None, ""
        if len(name) > 64:
            logger.warning("Skills: name exceeds 64 characters in {}; truncating", path)
            name = name[:64]
        return name, description.strip(), parts[2].strip()

    def _parse_yaml_lenient(self, yaml_block: str, path: str) -> dict | None:
        try:
            result = yaml.safe_load(yaml_block)
        except yaml.YAMLError:
            fixed = []
            for line in yaml_block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    value = value.strip()
                    if ":" in value and not value.startswith(('"', "'")):
                        line = f"{key}: {json.dumps(value)}"
                fixed.append(line)
            try:
                result = yaml.safe_load("\n".join(fixed))
            except yaml.YAMLError:
                logger.warning("Skills: unparseable YAML in {}", path)
                return None
        return result if isinstance(result, dict) else None

    def has_skills(self) -> bool:
        return bool(self._skills)

    def get_catalog(self) -> list[dict]:
        return [
            {"name": s["name"], "description": s["description"]}
            for s in sorted(
                self._skills.values(),
                key=lambda s: (s["scope"] != "project", s["name"]),
            )
        ]

    def catalog_prompt(self, context_tokens: int | None = None) -> str:
        """Bound the complete metadata prompt, including policy and diagnostics."""
        budget = (
            min(8000, max(0, int(context_tokens * 0.02 * 4)))
            if context_tokens
            else 8000
        )
        if not self._skills or budget <= len(CATALOG_POLICY) + 80:
            return ""
        lines = []
        remaining = budget - len(CATALOG_POLICY) - 80
        catalog = self.get_catalog()
        for skill in catalog:
            description = " ".join(skill["description"].split())
            line = f"- {skill['name']}: {description}\n"
            if len(line) <= remaining:
                lines.append(line)
                remaining -= len(line)
        omitted = len(catalog) - len(lines)
        diagnostic = (
            f"{omitted} skills omitted (catalog character budget: {budget}).\n"
            if omitted
            else ""
        )
        if omitted:
            logger.debug(
                "Skills: {} catalog entries omitted at {} characters", omitted, budget
            )
        return CATALOG_POLICY + "".join(lines) + diagnostic

    def get_skill(self, name: str) -> dict | None:
        return self._skills.get(name)

    def refresh(self) -> bool:
        """Rescan names, reusing bounded parsed entries for unchanged fingerprints."""
        with self._lock:
            old = self._skills
            self._discover()
            return old != self._skills

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self.refresh()

    def get_skill_names(self) -> list[str]:
        return [s["name"] for s in self.get_catalog()]

    def list_resources(self, skill_dir: str) -> list[str]:
        resources = []
        root = Path(skill_dir).resolve()
        try:
            for entry in sorted(root.iterdir()):
                if entry.is_symlink():
                    continue
                if entry.is_file() and entry.name != "SKILL.md":
                    resources.append(entry.name)
                elif entry.is_dir() and entry.name not in _SKIP_DIRS:
                    for sub in sorted(entry.iterdir()):
                        if sub.is_file() and not sub.is_symlink():
                            resources.append(sub.relative_to(root).as_posix())
                if len(resources) >= 100:
                    break
        except OSError:
            pass
        return resources[:100]

    def _check_project_path(self, path: Path) -> None:
        relative = path.relative_to(self.project_root)
        current = self.project_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlinks are not allowed in project skill paths")
        if not path.resolve().is_relative_to(self.project_root):
            raise ValueError("Skill path escapes project root")

    def _tree_files(self, root: Path) -> dict[str, bytes]:
        files = {}
        total = 0
        self._check_project_path(root)
        for path in sorted(root.rglob("*")):
            self._check_project_path(path)
            if path.is_dir():
                continue
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError(
                    "Only regular, non-hardlinked skill files are supported"
                )
            total += path.stat().st_size
            if total > MAX_CRAFT_BYTES:
                raise ValueError("Existing skill exceeds the 1 MB update limit")
            with path.open("rb") as source:
                content = source.read(MAX_CRAFT_BYTES + 1)
            if len(content) > MAX_CRAFT_BYTES:
                raise ValueError("Existing skill file exceeds the 1 MB update limit")
            files[path.relative_to(root).as_posix()] = content
        if sum(map(len, files.values())) > MAX_CRAFT_BYTES:
            raise ValueError("Existing skill exceeds the 1 MB update limit")
        return files

    @staticmethod
    def _files_hash(files: dict[str, bytes]) -> str:
        digest = hashlib.sha256()
        for path, content in sorted(files.items()):
            digest.update(path.encode() + b"\0" + hashlib.sha256(content).digest())
        return digest.hexdigest()

    def craft_skill(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        scripts: list[dict],
        compatibility: str | None = None,
        metadata: dict[str, str] | None = None,
        update_existing: bool = False,
        expected_existing_hash: str | None = None,
    ) -> dict:
        """Validate without execution, then atomically publish a complete skill tree."""
        if not self.trusted_project:
            raise ValueError("Project skills require trusted_project_plugins approval")
        if not _valid_name(name):
            raise ValueError(
                "Invalid skill name: use 1-64 lowercase letters/digits and single hyphens"
            )
        if (
            not isinstance(description, str)
            or not 1 <= len(description.strip()) <= 1024
        ):
            raise ValueError("Description must contain 1-1024 characters")
        if (
            not isinstance(instructions, str)
            or not instructions.strip()
            or len(instructions.strip()) > MAX_BODY_CHARS
        ):
            raise ValueError(
                f"Instructions must contain 1-{MAX_BODY_CHARS} characters; use references for longer content"
            )
        if compatibility is not None and (
            not isinstance(compatibility, str) or not 1 <= len(compatibility) <= 500
        ):
            raise ValueError("Compatibility must contain 1-500 characters")
        if metadata is not None and (
            not isinstance(metadata, dict)
            or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
            )
        ):
            raise ValueError(
                "Metadata must map strings to strings; name environment variables, never embed secrets"
            )
        if not isinstance(update_existing, bool):
            raise TypeError("update_existing must be a boolean")
        if not isinstance(scripts, list) or not 1 <= len(scripts) <= 32:
            raise ValueError(
                "Provide 1-32 reusable scripts; instruction-only skills are not supported"
            )
        frontmatter = {"name": name, "description": description.strip()}
        if compatibility is not None:
            frontmatter["compatibility"] = compatibility
        if metadata is not None:
            frontmatter["metadata"] = metadata
        skill_md = (
            "---\n"
            + yaml.safe_dump(frontmatter, sort_keys=True, allow_unicode=True)
            + "---\n\n"
            + instructions.strip()
            + "\n"
        )
        files = {"SKILL.md": skill_md.encode()}
        validation = []
        for script in scripts:
            if not isinstance(script, dict) or set(script) != {
                "path",
                "language",
                "content",
            }:
                raise ValueError("Each script requires path, language and content only")
            path, language, content = (
                script["path"],
                script["language"],
                script["content"],
            )
            if not isinstance(path, str) or not re.fullmatch(
                r"scripts/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.[A-Za-z0-9]+", path
            ):
                raise ValueError(
                    "Script paths must be relative scripts/.../file.ext paths without traversal"
                )
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or path in files:
                raise ValueError("Invalid or duplicate script path")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Script content must be a nonempty string")
            if len(content.encode()) > MAX_CRAFT_BYTES:
                raise ValueError("Script exceeds the 1 MB skill limit")
            validation.append(validate_script(path, language, content))
            files[path] = content.encode("utf-8")
        if (
            sum(map(len, files.values())) > MAX_CRAFT_BYTES
            or len(files["SKILL.md"]) > MAX_SKILL_FILE_BYTES
        ):
            raise ValueError("Skill exceeds the 1 MB tree or 128 KB SKILL.md limit")
        if any(_SECRETS.search(value.decode()) for value in files.values()):
            raise ValueError(
                "Obvious credential material detected; reference environment variables instead"
            )
        with self._lock:
            return self._publish_skill(
                name, files, validation, update_existing, expected_existing_hash
            )

    def _publish_skill(
        self, name, files, validation, update_existing, expected_existing_hash
    ):
        target = self.project_root / ".agents" / "skills" / name
        self.refresh()
        discovered = self.get_skill(name)
        if (
            discovered
            and discovered["scope"] == "project"
            and discovered["location"] != str(target / "SKILL.md")
        ):
            raise ValueError(
                "Skill name belongs to another project skill root; choose a distinct name"
            )
        self._check_project_path(target)
        exists = target.exists()
        if exists and not target.is_dir():
            raise ValueError("Existing skill target is not a directory")
        existing = self._tree_files(target) if exists else {}
        previous_hash = self._files_hash(existing) if exists else None
        if (
            expected_existing_hash is not None
            and expected_existing_hash != previous_hash
        ):
            raise ValueError(
                "Skill hash conflict: existing content changed; reload before updating"
            )
        merged = existing | files
        if sum(map(len, merged.values())) > MAX_CRAFT_BYTES:
            raise ValueError("Merged skill tree exceeds the 1 MB update limit")
        content_hash = self._files_hash(merged)
        operation = "create"
        warnings = []
        if exists:
            if merged == existing:
                operation = "no-op"
            elif not update_existing:
                raise ValueError(
                    "Skill already exists; set update_existing=true to change it"
                )
            else:
                operation = "update"
        if operation != "no-op":
            target.parent.mkdir(parents=True, exist_ok=True)
            self._check_project_path(target)
            stage = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=target.parent))
            try:
                directory_modes = {}
                if exists:
                    for directory in sorted(target.rglob("*")):
                        self._check_project_path(directory)
                        if directory.is_dir():
                            relative_dir = directory.relative_to(target)
                            (stage / relative_dir).mkdir(parents=True, exist_ok=True)
                            directory_modes[relative_dir] = (
                                directory.stat().st_mode & 0o777
                            )
                for relative, content in merged.items():
                    path = stage / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    original = target / relative
                    if relative in existing:
                        self._check_project_path(original)
                        path.chmod(original.stat().st_mode & 0o777)
                for relative_dir, mode in reversed(directory_modes.items()):
                    (stage / relative_dir).chmod(mode)
                if exists:
                    stage.chmod(target.stat().st_mode & 0o777)
                self._check_project_path(target)
                if (
                    exists
                    and self._files_hash(self._tree_files(target)) != previous_hash
                ):
                    raise ValueError(
                        "Skill hash conflict: files changed during staging"
                    )
                _atomic_publish(stage, target, exchange=exists)
            finally:
                if stage.exists():
                    try:
                        _remove_staging(stage)
                    except OSError:
                        warnings.append(f"Could not remove staging directory: {stage}")
                        logger.warning(
                            "Skills: could not remove staging directory {}", stage
                        )
            self.invalidate()
        return {
            "operation": operation,
            "paths": [str(target / path) for path in sorted(files)],
            "content_hash": content_hash,
            "validation": validation,
            "validation_scope": "static_syntax_only",
            "executed": False,
            "warnings": warnings,
        }
