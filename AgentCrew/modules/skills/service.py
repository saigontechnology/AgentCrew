"""Progressive skill discovery and project-local, non-executing skill authoring."""

import hashlib
import json
import os
import re
import stat
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import nullcontext
from heapq import nsmallest
from pathlib import Path, PurePosixPath

import yaml
from loguru import logger

from AgentCrew.modules.config.project_config import validate_project_roots

from .publisher import (
    DirectorySkillPublisher,
    RecoveryReport,
    SkillPublisher,
    check_project_path,
    inspect_skill_entry,
    portable_key,
    skill_tree_snapshot,
    validate_relative_names,
)
from .validators import validate_script

SKILL_SCAN_DIRS = [".claude/skills", ".agents/skills", ".od-skills", ".codex/skills"]
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}
MAX_BODY_CHARS = 16000
MAX_SKILL_FILE_BYTES = 128000
MAX_CRAFT_BYTES = 1000000
MAX_CACHE_ENTRIES = 512
MAX_DISCOVERY_DEPTH = 4
MAX_DISCOVERY_DIRS_PER_ROOT = 2000
MAX_DISCOVERY_DIRS_TOTAL = 16000
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


class SkillsService:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_root: str | Path | None = None,
        trusted_project: bool = False,
        project_roots: list[str | Path] | None = None,
    ) -> None:
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.user_root = Path(user_root or Path.home()).resolve()
        self.trusted_project = trusted_project
        self._project_roots = (
            [self.project_root]
            if project_roots is None
            else validate_project_roots(self.project_root, project_roots, resolved=True)
            if trusted_project
            else []
        )
        self._skills: dict[str, dict] = {}
        self._cache: OrderedDict[str, tuple[tuple, dict | None]] = OrderedDict()
        self._publisher: SkillPublisher = DirectorySkillPublisher(
            self.project_root, lambda path: self._files_hash(self._tree_files(path))
        )
        self._project_publishers: dict[Path, SkillPublisher] = {
            root: self._publisher
            if root == self.project_root
            else DirectorySkillPublisher(
                root, lambda path: self._files_hash(self._tree_files(path))
            )
            for root in self._project_roots
        }
        self._lock = (
            self._publisher.lock
            if trusted_project and self.project_root in self._project_roots
            else threading.RLock()
        )
        self.refresh()

    def _discover(self) -> None:
        skills: dict[str, dict] = {}
        budget = {"remaining": MAX_DISCOVERY_DIRS_TOTAL}
        physical_targets: dict[tuple, Path] = {}
        self._discover_root(self.user_root, "user", budget, physical_targets, skills)
        if self.trusted_project:
            roots = validate_project_roots(
                self.project_root, self._project_roots, resolved=True
            )
            for root in roots:
                publisher = self._project_publishers[root]
                with publisher.lock:
                    self._recover_locked(publisher)
                    self._discover_root(
                        root, "project", budget, physical_targets, skills, publisher
                    )
        self._skills = skills

    def _discover_root(
        self,
        root: Path,
        scope: str,
        budget: dict[str, int],
        physical_targets: dict[tuple, Path],
        skills: dict[str, dict],
        publisher: SkillPublisher | None = None,
    ) -> None:
        for rel_dir in SKILL_SCAN_DIRS:
            if (
                scope == "project"
                and rel_dir == ".agents/skills"
                and (publisher is None or not publisher.lock.file_locked)
            ):
                continue
            base = root / rel_dir
            for path in self._discovery_paths(base, scope, budget):
                try:
                    metadata = path.stat()
                    identity = (
                        (metadata.st_dev, metadata.st_ino)
                        if metadata.st_ino
                        else (str(path.resolve()),)
                    )
                    key = (scope, *identity)
                    previous = physical_targets.get(key)
                    if previous is not None:
                        logger.debug(
                            "Skills: duplicate physical target; winner={}, loser={}",
                            previous,
                            path,
                        )
                        continue
                    skill = self._load_skill(path, scope)
                except (OSError, ValueError, UnicodeError, RecursionError):
                    continue
                if not skill:
                    continue
                physical_targets[key] = path
                existing = skills.get(skill["name"])
                if existing:
                    winner, loser = (
                        (existing, skill)
                        if existing["scope"] == scope
                        else (skill, existing)
                    )
                    logger.debug(
                        "Skills: shadowed name {!r}; winner={}, loser={}",
                        skill["name"],
                        winner["location"],
                        loser["location"],
                    )
                    if existing["scope"] == scope:
                        continue
                skills[skill["name"]] = skill

    @staticmethod
    def _is_discovery_link(path: Path, metadata: os.stat_result) -> bool:
        return bool(
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.is_junction()
        )

    def _discovery_entries(
        self, directory: Path, base: Path, scope: str, depth: int
    ) -> Iterator[Path]:
        """Validate directory candidates before any recursive descent."""
        try:
            if scope == "project":
                inspect_skill_entry(self.project_root, directory)
            with os.scandir(directory) as children:
                for child in children:
                    entry = directory / child.name
                    if entry.name in _SKIP_DIRS or (
                        scope == "project"
                        and base.name == "skills"
                        and base.parent.name == ".agents"
                        and entry.name.startswith(".")
                    ):
                        continue
                    try:
                        metadata = child.stat(follow_symlinks=False)
                        if scope == "project":
                            metadata = inspect_skill_entry(
                                self.project_root, entry, metadata
                            )
                        else:
                            if self._is_discovery_link(entry, metadata):
                                if depth != 1:
                                    continue
                                metadata = entry.stat()
                        if stat.S_ISDIR(metadata.st_mode):
                            yield entry
                    except (OSError, ValueError):
                        continue
        except (OSError, ValueError):
            return

    def _discovery_paths(
        self, base: Path, scope: str, budget: dict[str, int]
    ) -> Iterator[Path]:
        """Yield bundle boundaries shallow-first with bounded frontier storage."""
        pending = [base]
        visited = 0
        for depth in range(1, MAX_DISCOVERY_DEPTH + 1):
            remaining = min(MAX_DISCOVERY_DIRS_PER_ROOT - visited, budget["remaining"])
            candidates = (
                entry
                for directory in pending
                for entry in self._discovery_entries(directory, base, scope, depth)
            )
            entries = nsmallest(
                remaining + 1,
                candidates,
                key=lambda path: (
                    unicodedata.normalize("NFC", path.relative_to(base).as_posix()),
                    path.relative_to(base).as_posix(),
                ),
            )
            truncated = len(entries) > remaining
            if truncated:
                logger.warning(
                    "Skills: directory budget truncation at {} "
                    "(per-root limit={}, total limit={})",
                    base,
                    MAX_DISCOVERY_DIRS_PER_ROOT,
                    MAX_DISCOVERY_DIRS_TOTAL,
                )
            pending = []
            for entry in entries[:remaining]:
                visited += 1
                budget["remaining"] -= 1
                path = entry / "SKILL.md"
                try:
                    if scope == "project":
                        self._check_project_path(entry)
                    marker = None
                    try:
                        path.lstat()
                        with os.scandir(entry) as children:
                            marker = next(
                                (
                                    child
                                    for child in children
                                    if child.name == "SKILL.md"
                                ),
                                None,
                            )
                    except FileNotFoundError:
                        pass
                    if marker is None:
                        if not entry.name.startswith(
                            "."
                        ) and not self._is_discovery_link(entry, entry.lstat()):
                            pending.append(entry)
                        continue
                    metadata = marker.stat(follow_symlinks=False)
                    if scope == "project":
                        metadata = inspect_skill_entry(
                            self.project_root, path, metadata
                        )
                    elif self._is_discovery_link(path, metadata):
                        if depth != 1:
                            continue
                        metadata = path.stat()
                    if stat.S_ISREG(metadata.st_mode):
                        yield path
                except (OSError, ValueError):
                    continue
            if truncated or not pending:
                return
            if depth == MAX_DISCOVERY_DEPTH and any(
                next(
                    self._discovery_entries(directory, base, scope, depth + 1),
                    None,
                )
                is not None
                for directory in pending
            ):
                logger.warning(
                    "Skills: depth truncation at {} (depth limit={})",
                    base,
                    MAX_DISCOVERY_DEPTH,
                )

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
        with self._lock:
            self.refresh()
            return bool(self._skills)

    def get_catalog(self) -> list[dict]:
        with self._lock:
            self.refresh()
            return self._catalog()

    def _catalog(self) -> list[dict]:
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
        with self._lock:
            self.refresh()
            return self._skills.get(name)

    def _recover_locked(
        self, publisher: SkillPublisher | None = None
    ) -> RecoveryReport:
        if not self.trusted_project:
            return RecoveryReport()
        try:
            report = (publisher or self._publisher).recover()
        except (OSError, ValueError, TimeoutError):
            self._skills = {}
            raise
        for warning in report.warnings:
            logger.warning("Skills: {}", warning)
        if report.performed:
            self._cache.clear()
        return report

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
        with self._lock:
            path = Path(skill_dir)
            if path.is_relative_to(self.project_root):
                self._check_project_path(path)
            publisher = None
            if self.trusted_project:
                for root in sorted(
                    self._project_publishers,
                    key=lambda root: len(root.parts),
                    reverse=True,
                ):
                    if path.is_relative_to(root / ".agents/skills"):
                        publisher = self._project_publishers[root]
                        break
            with publisher.lock if publisher else nullcontext():
                if publisher:
                    self._recover_locked(publisher)
                return self._list_resources(skill_dir)

    def _list_resources(self, skill_dir: str) -> list[str]:
        resources = []
        root = Path(skill_dir).resolve()
        try:
            for entry in sorted(root.iterdir()):
                if entry.is_symlink():
                    continue
                if root.is_relative_to(self.project_root):
                    self._check_project_path(entry)
                if entry.is_file() and entry.name != "SKILL.md":
                    resources.append(entry.name)
                elif entry.is_dir() and entry.name not in _SKIP_DIRS:
                    for sub in sorted(entry.iterdir()):
                        if root.is_relative_to(self.project_root):
                            if sub.is_symlink():
                                continue
                            self._check_project_path(sub)
                        if sub.is_file() and not sub.is_symlink():
                            resources.append(sub.relative_to(root).as_posix())
                if len(resources) >= 100:
                    break
        except OSError:
            pass
        return resources[:100]

    def _check_project_path(self, path: Path) -> None:
        check_project_path(self.project_root, path)

    def _tree_files(self, root: Path) -> dict[str, bytes]:
        files = {}
        total = 0
        for path, metadata in skill_tree_snapshot(self.project_root, root):
            if stat.S_ISDIR(metadata.st_mode):
                continue
            metadata = inspect_skill_entry(self.project_root, path)
            total += metadata.st_size
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
        """Validate without execution, then publish a complete skill tree with recovery."""
        if not self.trusted_project:
            raise ValueError("Project skills require trusted_project_plugins approval")
        if not _valid_name(name):
            raise ValueError(
                "Invalid skill name: use 1-64 lowercase letters/digits and single hyphens"
            )
        validate_relative_names([name])
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
        validate_relative_names(list(files))
        if (
            sum(map(len, files.values())) > MAX_CRAFT_BYTES
            or len(files["SKILL.md"]) > MAX_SKILL_FILE_BYTES
        ):
            raise ValueError("Skill exceeds the 1 MB tree or 128 KB SKILL.md limit")
        if any(_SECRETS.search(value.decode()) for value in files.values()):
            raise ValueError(
                "Obvious credential material detected; reference environment variables instead"
            )
        with self._publisher.lock, self._lock:
            recovery = self._recover_locked()
            self._discover()
            self._check_skill_target(name)
        with self._publisher.lock.hold(create=True):
            additional = self._recover_locked()
            recovery.performed |= additional.performed
            recovery.warnings.extend(additional.warnings)
            return self._publish_skill(
                name,
                files,
                validation,
                update_existing,
                expected_existing_hash,
                recovery,
            )

    def _check_skill_target(self, name: str) -> Path:
        target = self.project_root / ".agents" / "skills" / name
        for skill in self._skills.values():
            if (
                skill["scope"] == "project"
                and portable_key(skill["name"]) == portable_key(name)
                and Path(skill["location"]) != target / "SKILL.md"
            ):
                raise ValueError(
                    "Skill name belongs to another project skill root; choose a distinct name"
                )
        for relative in SKILL_SCAN_DIRS:
            root = self.project_root / relative
            try:
                self._check_project_path(root)
            except ValueError:
                if relative == ".agents/skills":
                    raise
                continue
            if root.is_dir():
                for entry in root.iterdir():
                    if (
                        portable_key(entry.name) == portable_key(name)
                        and entry.as_posix() != target.as_posix()
                    ):
                        raise ValueError(
                            "Case-insensitive project skill-root collision; choose a distinct name"
                        )
        self._check_project_path(target)
        if (
            target.is_dir()
            and not (target / "SKILL.md").exists()
            and next(target.iterdir(), None) is not None
        ):
            raise ValueError(
                "Skill target is a grouping container; choose a distinct name"
            )
        return target

    def _publish_skill(
        self, name, files, validation, update_existing, expected_existing_hash, recovery
    ):
        self.refresh()
        target = self._check_skill_target(name)
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
        validate_relative_names(list(merged))
        if sum(map(len, merged.values())) > MAX_CRAFT_BYTES:
            raise ValueError("Merged skill tree exceeds the 1 MB update limit")
        content_hash = self._files_hash(merged)
        operation = "create"
        warnings = list(recovery.warnings)
        publication = {
            "publication_backend": "none",
            "publication_guarantee": "unchanged",
            "recovery_performed": recovery.performed,
        }
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
            self._check_project_path(target)
            stage = self._publisher.create_stage(target)
            try:
                directory_modes = {}
                if exists:
                    for directory, metadata in skill_tree_snapshot(
                        self.project_root, target
                    ):
                        if stat.S_ISDIR(metadata.st_mode):
                            relative_dir = directory.relative_to(target)
                            (stage / relative_dir).mkdir(parents=True, exist_ok=True)
                            directory_modes[relative_dir] = metadata.st_mode & 0o777
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
                publication = self._publisher.publish(
                    stage, target, previous_hash, content_hash
                )
                warnings.extend(publication.pop("warnings"))
                publication["recovery_performed"] |= recovery.performed
            finally:
                self._publisher.discard_stage(stage, warnings)
            self.invalidate()
        return {
            "operation": operation,
            "paths": [str(target / path) for path in sorted(files)],
            "content_hash": content_hash,
            "validation": validation,
            "validation_scope": "static_syntax_only",
            "executed": False,
            **publication,
            "warnings": warnings,
        }
