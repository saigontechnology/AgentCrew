"""Confirmed external startup migration and recoverable, artifact-only Chroma backup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from filelock import FileLock, Timeout
from loguru import logger

from .engine_selection import MemoryEngineError, _is_non_chroma_name

PACKAGE = "agentcrew-ai[chromadb]@latest"
SKIP_NAME = "migration-skipped.json"
WARNING = (
    "Chroma migration was permanently skipped for this memory root. The active SQLite "
    "store prevents safe automatic merge of old Chroma data. Stop all writers, manually "
    "back up/remove the new SQLite store and its sidecars, then run the recovery command."
)
PREFIX = "memorydb-chroma-backup-"


def _path(value: Path) -> Path:
    raw = Path(value).expanduser()
    if ".." in raw.parts:
        raise MemoryEngineError("Migration path traversal is refused")
    absolute = raw.absolute()
    for part in reversed((absolute, *absolute.parents)):
        if part.is_symlink():
            raise MemoryEngineError("Migration symlink paths are refused")
    return absolute


def _tree(path: Path) -> None:
    _path(path)
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink() or not (child.is_dir() or child.is_file()):
                raise MemoryEngineError("Migration symlinks/special files are refused")


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _display_path(path: Path, directory: bool = False) -> str:
    text = str(path) + ("/" if directory and str(path) != "/" else "")
    try:
        relative = path.relative_to(_path(Path.home())).as_posix()
    except ValueError:
        return shlex.quote(text)
    if re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
        return "~/" + relative + ("/" if directory else "")
    return shlex.quote(text)


@dataclass(frozen=True)
class MigrationPlan:
    source: Path
    destination: Path
    work: Path

    @property
    def paths(self) -> dict:
        return {
            "source": str(self.source),
            "destination": str(self.destination),
            "work_dir": str(self.work),
        }

    @property
    def argv(self) -> list[str]:
        return [
            "uvx",
            "--from",
            PACKAGE,
            "agentcrew-migrate-memory",
            "--source",
            str(self.source) + "/",
            "--destination",
            str(self.destination),
            "--work-dir",
            str(self.work) + "/",
        ]

    @property
    def display(self) -> str:
        return f"uvx --from {shlex.quote(PACKAGE)} agentcrew-migrate-memory --source {_display_path(self.source, True)} --destination {_display_path(self.destination)} --work-dir {_display_path(self.work, True)}"


def _json(path: Path) -> dict:
    _path(path)
    try:
        if not path.is_file() or path.stat().st_size > 4_000_000:
            raise ValueError("non-regular or oversized metadata")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("not an object")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise MemoryEngineError(f"Invalid migration metadata: {path}") from exc


def _write(path: Path, value: dict, *, exclusive: bool = False) -> None:
    _path(path)
    data = json.dumps(value, sort_keys=True, indent=2).encode("utf-8")
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.chmod(path, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    _path(temporary)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _segments(root: Path) -> set[str]:
    database = root / "chroma.sqlite3"
    if not database.is_file():
        return set()
    try:
        with sqlite3.connect(
            database.as_uri() + "?mode=ro&immutable=1", uri=True
        ) as connection:
            return {
                str(row[0]) for row in connection.execute("SELECT id FROM segments")
            }
    except sqlite3.Error:
        return set()


def _artifacts(root: Path, extra_segments: set[str] | None = None) -> list[Path]:
    if not root.is_dir():
        return []
    _tree(root)
    segments = _segments(root).union(extra_segments or ())
    hnsw = {"header.bin", "data_level0.bin", "link_lists.bin", "index_metadata.pickle"}
    result = []
    for entry in sorted(root.iterdir()):
        if _is_non_chroma_name(entry.name):
            continue
        if entry.is_file() and entry.name in {
            "chroma.sqlite3",
            "chroma.sqlite3-wal",
            "chroma.sqlite3-shm",
            "chroma.sqlite3-journal",
        }:
            result.append(entry)
        elif entry.is_dir():
            try:
                UUID(entry.name)
                uuid_name = True
            except ValueError:
                uuid_name = False
            if entry.name in segments or (
                (uuid_name or entry.name == "index")
                and any((entry / name).is_file() for name in hnsw)
            ):
                result.append(entry)
    return result


def _hash(path: Path) -> str:
    _tree(path)
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    else:
        for entry in sorted(path.rglob("*")):
            digest.update(entry.relative_to(path).as_posix().encode())
            digest.update(b"D" if entry.is_dir() else b"F")
            if entry.is_file():
                digest.update(bytes.fromhex(_hash(entry)))
    return digest.hexdigest()


def _verified(plan: MigrationPlan) -> tuple[str, str]:
    from .sqlite_repository import SQLiteMemoryRepository

    _tree(plan.source)
    _path(plan.destination)
    _tree(plan.work)
    if not plan.destination.is_file():
        raise MemoryEngineError("Migration did not create the SQLite destination")
    report = plan.work / "report.json"
    data = _json(report)
    if data.get("status") not in {"applied", "verified"}:
        raise MemoryEngineError("Migration report must have applied or verified status")
    try:
        SQLiteMemoryRepository(
            plan.destination, read_only=True
        ).check_read_only_integrity()
    except Exception as exc:
        raise MemoryEngineError(
            "Migrated SQLite failed independent read-only integrity/schema checks"
        ) from exc
    return data["status"], _hash(report)


def _skip(plan: MigrationPlan) -> None:
    path = plan.source / SKIP_NAME
    if path.exists():
        data = _json(path)
        if path.stat().st_mode & 0o777 != 0o600:
            raise MemoryEngineError(
                "Permanent migration skip marker must have mode 0600"
            )
        try:
            stamp = datetime.fromisoformat(data["utc_time"])
        except (ValueError, TypeError, KeyError) as exc:
            raise MemoryEngineError(
                "Malformed permanent migration skip marker"
            ) from exc
        if not (
            data.get("schema") == "agentcrew-memory-migration-skip"
            and type(data.get("version")) is int
            and data["version"] == 1
            and data.get("reason") == "noninteractive"
            and data.get("paths") == plan.paths
            and data.get("argv") == plan.argv
            and data.get("recovery_command") == plan.display
            and data.get("warning") == WARNING
            and stamp.utcoffset() == UTC.utcoffset(stamp)
        ):
            raise MemoryEngineError(
                "Conflicting permanent migration skip marker; manual recovery required"
            )
    else:
        _write(
            path,
            {
                "schema": "agentcrew-memory-migration-skip",
                "version": 1,
                "utc_time": _utc(),
                "reason": "noninteractive",
                "paths": plan.paths,
                "argv": plan.argv,
                "recovery_command": plan.display,
                "warning": WARNING,
            },
            exclusive=True,
        )
    logger.warning("{} Recovery command: {}", WARNING, plan.display)


def _pending(root: Path, plan: MigrationPlan) -> tuple[Path, dict] | None:
    _path(root)
    matches = []
    if root.exists():
        for directory in sorted(root.glob(PREFIX + "*")):
            _path(directory)
            manifest_path = directory / "manifest.json"
            if not directory.is_dir() or not manifest_path.exists():
                continue
            manifest = _json(manifest_path)
            if manifest.get("paths") == plan.paths:
                if manifest.get("completion_status") not in {
                    "in_progress",
                    "rollback_failed",
                    "completed",
                    "rolled_back",
                }:
                    raise MemoryEngineError("Invalid Chroma backup completion status")
                if manifest.get("completion_status") in {
                    "in_progress",
                    "rollback_failed",
                }:
                    matches.append((directory, manifest))
    if len(matches) > 1:
        raise MemoryEngineError(
            "Multiple unfinished Chroma backups; manual recovery required"
        )
    return matches[0] if matches else None


def _backup(
    plan: MigrationPlan, backup_root: Path, report_status: str, report_hash: str
) -> Path:
    pending = _pending(backup_root, plan)
    if pending:
        directory, manifest = pending
        if not (
            manifest.get("schema") == "agentcrew-chroma-backup"
            and manifest.get("version") == 1
            and manifest.get("argv") == plan.argv
            and manifest.get("display_command") == plan.display
            and manifest.get("report_sha256") == report_hash
            and manifest.get("report_status") == report_status
        ):
            raise MemoryEngineError(
                "Unfinished backup does not match the verified migration; manual recovery required"
            )
        entries = manifest.get("artifacts")
        if not isinstance(entries, list) or not entries:
            raise MemoryEngineError("Invalid unfinished backup artifact manifest")
        recognized = {
            path.name for path in _artifacts(plan.source, _segments(directory))
        }
        recognized.update(
            path.name for path in _artifacts(directory, _segments(plan.source))
        )
        names = set()
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("relative_path"), str)
                or Path(entry["relative_path"]).is_absolute()
                or "\\" in entry["relative_path"]
                or Path(entry["relative_path"]).parts != (entry["relative_path"],)
                or entry["relative_path"] in {".", ".."}
                or entry["relative_path"] not in recognized
                or entry["relative_path"] in names
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("pre_sha256")))
            ):
                raise MemoryEngineError("Invalid unfinished backup artifact path/hash")
            names.add(entry["relative_path"])
        if any(path.name not in names for path in _artifacts(plan.source)):
            raise MemoryEngineError(
                "Chroma artifacts changed since the unfinished backup"
            )
    else:
        artifacts = _artifacts(plan.source)
        if not artifacts:
            raise MemoryEngineError("No detected Chroma artifacts to back up")
        entries = [
            {"relative_path": path.name, "pre_sha256": _hash(path), "post_sha256": None}
            for path in artifacts
        ]
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stem = PREFIX + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = backup_root / stem
        suffix = 0
        while True:
            try:
                directory.mkdir(mode=0o700)
                os.chmod(directory, 0o700)
                break
            except FileExistsError:
                suffix += 1
                directory = backup_root / f"{stem}-{suffix}"
        manifest = {
            "schema": "agentcrew-chroma-backup",
            "version": 1,
            "utc_time": _utc(),
            "paths": plan.paths,
            "argv": plan.argv,
            "display_command": plan.display,
            "report_status": report_status,
            "report_sha256": report_hash,
            "artifacts": entries,
            "completion_status": "in_progress",
        }
        _write(directory / "manifest.json", manifest)
    os.chmod(directory, 0o700)
    try:
        for entry in entries:
            source = _path(plan.source / entry["relative_path"])
            target = _path(directory / entry["relative_path"])
            if source.exists() == target.exists():
                raise MemoryEngineError(
                    "Backup artifact is missing or exists in both locations"
                )
            if source.exists():
                if _hash(source) != entry["pre_sha256"]:
                    raise MemoryEngineError("Chroma artifact changed before backup")
                shutil.move(str(source), str(target))
            if _hash(target) != entry["pre_sha256"]:
                raise MemoryEngineError("Chroma artifact changed during backup")
            entry["post_sha256"] = _hash(target)
            _write(directory / "manifest.json", manifest)
        manifest["completion_status"] = "completed"
        _write(directory / "manifest.json", manifest)
    except BaseException as exc:
        failures = []
        for entry in reversed(entries):
            try:
                source = _path(plan.source / entry["relative_path"])
                target = _path(directory / entry["relative_path"])
                if target.exists():
                    if source.exists():
                        raise MemoryEngineError("Backup rollback collision")
                    shutil.move(str(target), str(source))
            except Exception:
                failures.append(entry["relative_path"])
        manifest["completion_status"] = "rollback_failed" if failures else "rolled_back"
        try:
            _write(directory / "manifest.json", manifest)
        except OSError:
            logger.error(
                "Backup recovery manifest could not be written; inspect {}", directory
            )
        raise MemoryEngineError(
            f"Chroma backup failed; {manifest['completion_status']}. Inspect {directory / 'manifest.json'} and {plan.source}; startup aborted"
        ) from exc
    logger.info("Chroma artifacts backed up to {}", directory)
    return directory


def _confirm(message: str) -> bool:
    return input(message + "\nProceed? [y/N] ").strip().lower() in {"y", "yes"}


def _execute(argv: list[str]):
    return subprocess.run(argv, check=False, shell=False)


def ensure_startup_sqlite_store(
    destination: Path,
    source: Path,
    work: Path,
    *,
    interactive: bool = False,
    confirm: Callable[[str], bool] | None = None,
    execute: Callable | None = None,
    find_executable: Callable | None = None,
    backup_root: Path | None = None,
) -> Path:
    plan = MigrationPlan(_path(source), _path(destination), _path(work))
    backups = _path(backup_root or Path.home() / ".AgentCrew")
    marker = plan.source / SKIP_NAME
    if marker.exists() or marker.is_symlink():
        _skip(plan)
        return plan.destination
    if (
        plan.destination.exists()
        and not (plan.work / "report.json").exists()
        and not (plan.work / "snapshot.json").exists()
    ):
        if (plan.source / "chroma.sqlite3").exists():
            logger.warning(f"{WARNING} Recovery command: {plan.display}")
        return plan.destination
    artifacts = _artifacts(plan.source)
    pending = (
        _pending(backups, plan)
        if artifacts
        or (plan.destination.exists() and (plan.work / "report.json").exists())
        else None
    )
    if not artifacts and not pending:
        if (
            plan.work.exists()
            and any(plan.work.iterdir())
            and not plan.destination.exists()
        ):
            raise MemoryEngineError(
                f"Migration work exists without source/destination. Recover with: {plan.display}"
            )
        return plan.destination
    if (
        backups == plan.source
        or plan.source in backups.parents
        or plan.destination == plan.source
        or plan.work == plan.source
        or plan.work == plan.destination
        or plan.source in plan.destination.parents
        and plan.destination.name == "chroma.sqlite3"
    ):
        raise MemoryEngineError("Unsafe overlapping migration/backup paths")
    if not interactive and not plan.destination.exists():
        with FileLock(
            str(plan.source / "startup-migration.lock"), timeout=0, mode=0o600
        ):
            _skip(plan)
        return plan.destination
    report = plan.work / "report.json"
    incomplete = (plan.work / "snapshot.json").exists() or report.exists()
    completed = report.exists() and _json(report).get("status") in {
        "applied",
        "verified",
    }
    if plan.destination.exists() and not completed and not incomplete:
        logger.warning(
            "Active SQLite store and Chroma data coexist; automatic merge is refused. {} Recovery command: {}",
            WARNING,
            plan.display,
        )
        return plan.destination
    if not interactive:
        if not plan.destination.exists():
            with FileLock(
                str(plan.source / "startup-migration.lock"), timeout=0, mode=0o600
            ):
                _skip(plan)
            return plan.destination
        if completed:
            _verified(plan)
            logger.warning(
                "Verified migration needs artifact backup; start interactively to confirm. Recovery command: {}",
                plan.display,
            )
            return plan.destination
        raise MemoryEngineError(
            f"Incomplete migration requires interactive recovery: {plan.display}"
        )
    backup_only = completed
    if backup_only:
        _verified(plan)
        message = (
            f"Source: {plan.source}\nDestination: {plan.destination}\nWork directory: {plan.work}\n"
            f"Backup path: {pending[0] if pending else backups}\n"
            "Stop all Chroma/AgentCrew writers. The existing migration is already verified "
            "and will NOT be rerun. Only detected Chroma artifacts will be backed up."
        )
    else:
        executable = (find_executable or shutil.which)("uvx")
        if not executable:
            raise MemoryEngineError(
                f"uvx is unavailable. Install uv, then run: {plan.display}"
            )
        message = (
            f"Source: {plan.source}\nDestination: {plan.destination}\nWork directory: {plan.work}\nStop all Chroma/AgentCrew writers. uvx may download and execute @latest; network access and version drift are explicitly involved.\nCommand: {plan.display}\n"
            "Run migration in the foreground, verify independently, then back up detected Chroma artifacts."
        )
    try:
        accepted = (confirm or _confirm)(message)
    except (EOFError, KeyboardInterrupt) as exc:
        raise MemoryEngineError(
            f"Migration confirmation interrupted; startup aborted. Recovery command: {plan.display}"
        ) from exc
    if not accepted:
        raise MemoryEngineError(
            f"Migration declined; startup aborted. Recovery command: {plan.display}"
        )
    try:
        with FileLock(
            str(plan.source / "startup-migration.lock"), timeout=0, mode=0o600
        ):
            if marker.exists():
                _skip(plan)
                return plan.destination
            completed = report.exists() and _json(report).get("status") in {
                "applied",
                "verified",
            }
            if backup_only and not completed:
                raise MemoryEngineError(
                    "Verified migration state changed; backup-only confirmation cannot authorize migration; startup aborted."
                )
            incomplete = (plan.work / "snapshot.json").exists() or report.exists()
            if plan.destination.exists() and not completed and not incomplete:
                logger.warning("{} Recovery command: {}", WARNING, plan.display)
                return plan.destination
            if not completed:
                result = (execute or _execute)(plan.argv)
                if result.returncode != 0:
                    raise MemoryEngineError(
                        f"uvx migration failed (exit {result.returncode}); startup aborted. Recovery command: {plan.display}"
                    )
            status, digest = _verified(plan)
            if _artifacts(plan.source) or _pending(backups, plan):
                _backup(plan, backups, status, digest)
    except (OSError, Timeout) as exc:
        raise MemoryEngineError(
            f"Migration launch/lock failed; startup aborted. Recovery command: {plan.display}"
        ) from exc
    return plan.destination
