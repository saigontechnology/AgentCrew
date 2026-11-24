"""Complete-tree publication, bounded cooperative locking and fail-closed recovery."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol
from weakref import WeakValueDictionary

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACT = re.compile(
    r"\.skill-([a-z0-9]+(?:-[a-z0-9]+)*)-([0-9a-f]{32})\.(stage|backup|journal\.json|journal\.tmp)\Z"
)
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)
}
_PHASES = {
    "prepared",
    "old_moved",
    "new_moved",
    "verified",
    "rolling_back",
    "rolled_back",
}
_UNSUPPORTED = {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}


class SkillRecoveryError(ValueError):
    """Publication evidence cannot safely be resolved automatically."""


class AtomicPublicationUnavailable(OSError):
    """The preferred directory-exchange capability is unavailable."""


def portable_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def validate_relative_names(
    paths: list[str], *, directories: list[str] | None = None
) -> None:
    """Reject Windows-invalid names and case/normalization/file-directory collisions everywhere."""
    prefixes: dict[str, str] = {}
    file_keys = {portable_key(path) for path in paths}
    for path in paths + (directories or []):
        pure = PurePosixPath(path)
        if not path or pure.is_absolute() or str(pure) != path:
            raise ValueError("Invalid portable relative skill path")
        for index, part in enumerate(pure.parts):
            if (
                part in {".", ".."}
                or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in part)
            ):
                raise ValueError(
                    f"Invalid Windows-compatible skill path component: {part!r}"
                )
            if portable_key(part.split(".")[0].rstrip(" .")) in _RESERVED:
                raise ValueError(f"Reserved Windows device name: {part!r}")
            prefix = "/".join(pure.parts[: index + 1])
            key = portable_key(prefix)
            if key in prefixes and prefixes[key] != prefix:
                raise ValueError(
                    f"Case-insensitive skill path collision: {prefixes[key]!r} and {prefix!r}"
                )
            if index < len(pure.parts) - 1 and key in file_keys:
                raise ValueError(f"Skill file/directory collision: {prefix!r}")
            prefixes[key] = prefix


def check_project_path(project_root: Path, path: Path) -> None:
    relative = path.relative_to(project_root)
    current = project_root
    for part in ("", *relative.parts):
        if part:
            current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError(
                "Symlinks, junctions and reparse points are not allowed in project skill paths"
            )
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            continue
        if (
            getattr(attributes, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError(
                "Symlinks, junctions and reparse points are not allowed in project skill paths"
            )
    if not path.resolve().is_relative_to(project_root):
        raise ValueError("Skill path escapes project root")


def _reject_tree_link(metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError(
            "Symlinks, junctions and reparse points are not allowed in skill trees"
        )


def inspect_skill_entry(
    project_root: Path, path: Path, metadata: os.stat_result | None = None
) -> os.stat_result:
    """Reject cached link flags, then use fresh lstat for complete metadata/link counts."""
    path.relative_to(project_root)
    if metadata is not None:
        _reject_tree_link(metadata)
    check_project_path(project_root, path)
    metadata = path.lstat()
    _reject_tree_link(metadata)
    if not stat.S_ISDIR(metadata.st_mode) and not (
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
    ):
        raise ValueError(
            "Only regular, non-hardlinked skill files and directories are supported"
        )
    return metadata


def skill_tree_snapshot(
    project_root: Path, root: Path
) -> list[tuple[Path, os.stat_result]]:
    """Inspect immediate children without following links; enqueue only validated directories."""
    root_metadata = inspect_skill_entry(project_root, root)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("Skill tree root is not a directory")
    pending = [root]
    result = []
    while pending:
        directory = pending.pop()
        inspect_skill_entry(project_root, directory)
        with os.scandir(directory) as children:
            batch = [
                (
                    directory / child.name,
                    inspect_skill_entry(
                        project_root,
                        directory / child.name,
                        child.stat(follow_symlinks=False),
                    ),
                )
                for child in sorted(children, key=lambda entry: entry.name)
            ]
        validate_relative_names(
            [path.name for path, info in batch if stat.S_ISREG(info.st_mode)],
            directories=[
                path.name for path, info in batch if stat.S_ISDIR(info.st_mode)
            ],
        )
        result.extend(batch)
        pending.extend(
            path for path, info in reversed(batch) if stat.S_ISDIR(info.st_mode)
        )
    return sorted(result, key=lambda item: item[0].relative_to(root).as_posix())


def retry_filesystem(operation: Callable):
    """Retry only known Windows interference, at most three attempts."""
    for attempt in range(3):
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33, 145} or attempt == 2:
                raise
            time.sleep(0.025 * (attempt + 1))


def _try_file_lock(fd: int, *, unlock: bool = False) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


class ProjectSkillLock:
    """Reentrant within a thread; bounded across threads and cooperating processes."""

    def __init__(self, root: Path, timeout: float = 5.0):
        self.root = root
        self.parent = root / ".agents/skills"
        self.timeout = timeout
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    @property
    def file_locked(self) -> bool:
        return getattr(self._local, "fd", None) is not None

    def acquire(self, *, create: bool = False) -> None:
        deadline = time.monotonic() + self.timeout
        if not self._thread_lock.acquire(timeout=self.timeout):
            raise TimeoutError(
                "Timed out waiting for the project skill publication lock"
            )
        fd = None
        try:
            if not self.file_locked and (create or self.parent.exists()):
                check_project_path(self.root, self.parent)
                if create:
                    self.parent.mkdir(parents=True, exist_ok=True)
                check_project_path(self.root, self.parent)
                path = self.parent / ".skill-publish.lock"
                check_project_path(self.root, path)
                fd = retry_filesystem(
                    lambda: os.open(
                        path,
                        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                )
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError(
                        "Skill lock must be a regular, non-hardlinked file"
                    )
                if info.st_size == 0:
                    os.write(fd, b"\0")
                while True:
                    try:
                        _try_file_lock(fd)
                        break
                    except OSError as exc:
                        if exc.errno not in {
                            errno.EAGAIN,
                            errno.EACCES,
                            errno.EDEADLK,
                        } and getattr(exc, "winerror", None) not in {32, 33}:
                            raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                "Timed out waiting for the project skill publication lock; stop other writers and retry"
                            ) from exc
                        time.sleep(min(0.02, remaining))
                check_project_path(self.root, path)
                self._local.fd = fd
                fd = None
            self._local.depth = getattr(self._local, "depth", 0) + 1
        except BaseException:
            if fd is not None:
                os.close(fd)
            self._thread_lock.release()
            raise

    def release(self) -> None:
        try:
            self._local.depth -= 1
            if self._local.depth == 0 and self.file_locked:
                fd = self._local.fd
                self._local.fd = None
                try:
                    _try_file_lock(fd, unlock=True)
                finally:
                    os.close(fd)
        finally:
            self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()

    @contextmanager
    def hold(self, *, create: bool = False):
        self.acquire(create=create)
        try:
            yield
        finally:
            self.release()


_LOCKS: WeakValueDictionary[str, ProjectSkillLock] = WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


def project_skill_lock(root: Path) -> ProjectSkillLock:
    with _LOCKS_GUARD:
        try:
            identity = root.stat()
            key = (
                f"inode:{identity.st_dev}:{identity.st_ino}"
                if identity.st_ino
                else "path:" + os.path.normcase(str(root))
            )
        except OSError:
            key = "path:" + os.path.normcase(str(root))
        lock = _LOCKS.get(key)
        if lock is None:
            lock = ProjectSkillLock(root)
            _LOCKS[key] = lock
        return lock


def _atomic_publish(source: Path, target: Path, *, exchange: bool) -> None:
    if not sys.platform.startswith("linux"):
        raise AtomicPublicationUnavailable(
            "Linux renameat2 is unavailable on this platform"
        )
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise AtomicPublicationUnavailable(
            "Cannot load libc renameat2 capability"
        ) from exc
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise AtomicPublicationUnavailable("libc has no renameat2")
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
        if error in _UNSUPPORTED:
            raise AtomicPublicationUnavailable(
                error, "Filesystem does not support renameat2"
            )
        raise OSError(error, "Atomic skill publication failed", str(target))


@dataclass
class RecoveryReport:
    performed: bool = False
    warnings: list[str] = field(default_factory=list)


class SkillPublisher(Protocol):
    lock: ProjectSkillLock

    def create_stage(self, target: Path) -> Path: ...
    def recover(self) -> RecoveryReport: ...
    def publish(
        self, stage: Path, target: Path, old_hash: str | None, new_hash: str
    ) -> dict: ...
    def discard_stage(self, stage: Path, warnings: list[str]) -> None: ...


class DirectorySkillPublisher:
    def __init__(self, project_root: Path, tree_hash: Callable[[Path], str]):
        self.project_root = project_root
        self.parent = project_root / ".agents/skills"
        self.lock = project_skill_lock(project_root)
        self.tree_hash = tree_hash

    def _error(self, journal: Path, reason: str) -> SkillRecoveryError:
        return SkillRecoveryError(
            f"Skill recovery blocked: {reason}. Evidence retained at {journal}. Stop external writers, inspect the target/stage/backup and journal, resolve the conflict, then retry."
        )

    def _paths(self, name: str, operation_id: str) -> dict[str, Path]:
        if (
            len(name) > 64
            or not _NAME.fullmatch(name)
            or not re.fullmatch(r"[0-9a-f]{32}", operation_id)
        ):
            raise SkillRecoveryError(
                "Invalid skill transaction identity; evidence retained"
            )
        validate_relative_names([name])
        prefix = f".skill-{name}-{operation_id}"
        return {
            "target": self.parent / name,
            "stage": self.parent / (prefix + ".stage"),
            "backup": self.parent / (prefix + ".backup"),
            "journal": self.parent / (prefix + ".journal.json"),
            "temporary": self.parent / (prefix + ".journal.tmp"),
        }

    def _check(self, path: Path) -> None:
        check_project_path(self.project_root, path)

    def create_stage(self, target: Path) -> Path:
        with self.lock.hold(create=True):
            if target.parent != self.parent:
                raise ValueError(
                    "Skill target must be in the canonical project skill root"
                )
            paths = self._paths(target.name, uuid.uuid4().hex)
            self._check(paths["stage"])
            paths["stage"].mkdir(mode=0o700)
            return paths["stage"]

    def _hash(self, path: Path) -> str | None:
        self._check(path)
        if not path.exists():
            return None
        if not path.is_dir():
            raise ValueError(f"Skill tree is not a directory: {path}")
        return self.tree_hash(path)

    def _regular(self, path: Path) -> None:
        self._check(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8192:
            raise self._error(
                path, "journal must be a bounded regular non-hardlinked file"
            )

    def _record(self, path: Path) -> tuple[dict, dict[str, Path]]:
        match = _ARTIFACT.fullmatch(path.name)
        if not match or match[3] not in {"journal.json", "journal.tmp"}:
            raise self._error(path, "invalid journal filename")
        paths = self._paths(match[1], match[2])
        if path not in {paths["journal"], paths["temporary"]}:
            raise self._error(path, "journal is outside the canonical transaction root")
        self._regular(path)
        try:
            with path.open("rb") as source:
                raw = source.read(8193)
            if len(raw) > 8192:
                raise ValueError("Journal exceeds 8 KB")
            record = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise self._error(path, "unreadable or malformed journal") from exc
        fields = {
            "version",
            "project_id",
            "operation_id",
            "skill_name",
            "target",
            "stage",
            "backup",
            "phase",
            "old_hash",
            "new_hash",
        }
        expected = {
            "version": 1,
            "project_id": hashlib.sha256(str(self.project_root).encode()).hexdigest(),
            "operation_id": match[2],
            "skill_name": match[1],
            **{key: paths[key].name for key in ("target", "stage", "backup")},
        }
        if (
            not isinstance(record, dict)
            or set(record) != fields
            or type(record.get("version")) is not int
            or any(record.get(key) != value for key, value in expected.items())
        ):
            raise self._error(path, "journal identity/path mismatch")
        if (
            not isinstance(record["phase"], str)
            or record["phase"] not in _PHASES
            or not isinstance(record["new_hash"], str)
            or not _HASH.fullmatch(record["new_hash"])
            or (
                record["old_hash"] is not None
                and (
                    not isinstance(record["old_hash"], str)
                    or not _HASH.fullmatch(record["old_hash"])
                )
            )
        ):
            raise self._error(path, "invalid phase or hashes")
        for value in paths.values():
            self._check(value)
        return record, paths

    def _sync_parent(self, warnings: list[str]) -> None:
        try:
            fd = os.open(self.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED | {errno.EACCES, errno.EBADF}:
                raise
            message = "Directory fsync unavailable; no power-loss durability guarantee"
            if message not in warnings:
                warnings.append(message)

    def _write_record(
        self, record: dict, paths: dict[str, Path], warnings: list[str]
    ) -> None:
        for path in paths.values():
            self._check(path)
        temporary = paths["temporary"]
        if temporary.exists():
            previous, _ = self._record(temporary)
            if any(previous[key] != record[key] for key in record if key != "phase"):
                raise self._error(temporary, "conflicting journal temporary file")
            retry_filesystem(temporary.unlink)
        payload = json.dumps(record, sort_keys=True).encode()
        if len(payload) > 8192:
            raise ValueError("Skill transaction journal exceeds 8 KB")
        with retry_filesystem(lambda: temporary.open("xb")) as output:
            output.write(payload)
            output.flush()
            try:
                os.fsync(output.fileno())
            except OSError as exc:
                if exc.errno not in _UNSUPPORTED:
                    raise
                message = (
                    "Journal fsync unavailable; no power-loss durability guarantee"
                )
                if message not in warnings:
                    warnings.append(message)
        retry_filesystem(lambda: os.replace(temporary, paths["journal"]))
        self._sync_parent(warnings)

    def _move_absent(self, source: Path, target: Path) -> None:
        def move():
            self._check(source)
            self._check(target)
            if target.exists():
                raise FileExistsError(
                    f"Skill publication conflict: target appeared at {target}"
                )
            os.rename(source, target)

        retry_filesystem(move)

    def _remove_tree(self, path: Path) -> None:
        entries = skill_tree_snapshot(self.project_root, path)
        root_metadata = inspect_skill_entry(self.project_root, path)
        path.chmod(root_metadata.st_mode | stat.S_IRWXU)
        for child, _ in entries:
            metadata = inspect_skill_entry(self.project_root, child)
            child.chmod(
                metadata.st_mode
                | stat.S_IWRITE
                | stat.S_IREAD
                | (stat.S_IEXEC if stat.S_ISDIR(metadata.st_mode) else 0)
            )
        for child, _ in reversed(entries):
            metadata = inspect_skill_entry(self.project_root, child)
            retry_filesystem(
                child.rmdir if stat.S_ISDIR(metadata.st_mode) else child.unlink
            )
        inspect_skill_entry(self.project_root, path)
        retry_filesystem(path.rmdir)

    def _clean_journal(self, paths: dict[str, Path], warnings: list[str]) -> None:
        for key in ("temporary", "journal"):
            path = paths[key]
            if path.exists():
                self._regular(path)
                retry_filesystem(path.unlink)
        self._sync_parent(warnings)

    def _finish(
        self, record: dict, paths: dict[str, Path], *, committed: bool
    ) -> list[str]:
        warnings: list[str] = []
        phase = "verified" if committed else "rolled_back"
        if record["phase"] != phase:
            record = record | {"phase": phase}
            self._write_record(record, paths, warnings)
        cleanup = paths["backup"] if committed else paths["stage"]
        try:
            if cleanup.exists():
                self._remove_tree(cleanup)
            self._clean_journal(paths, warnings)
        except OSError as exc:
            warnings.append(
                f"Skill {'commit' if committed else 'rollback'} complete; cleanup pending at {paths['journal']}: {exc}"
            )
        return warnings

    def _recover_one(
        self, record: dict, paths: dict[str, Path]
    ) -> tuple[bool, list[str]]:
        journal = paths["journal"]
        try:
            hashes = {
                key: self._hash(paths[key]) for key in ("target", "stage", "backup")
            }
        except (OSError, ValueError) as exc:
            raise self._error(journal, f"cannot validate trees: {exc}") from exc
        old, new = record["old_hash"], record["new_hash"]
        if (
            hashes["stage"] not in {None, new}
            or hashes["backup"] not in {None, old}
            or (old is None and hashes["backup"] is not None)
        ):
            raise self._error(journal, "stage/backup hash disagreement")
        if (
            hashes["target"] == new
            and hashes["stage"] is None
            and record["phase"] not in {"rolling_back", "rolled_back"}
        ):
            return True, self._finish(record, paths, committed=True)
        if old is not None and hashes["target"] is None and hashes["backup"] == old:
            record = record | {"phase": "rolling_back"}
            warnings = []
            self._write_record(record, paths, warnings)
            self._move_absent(paths["backup"], paths["target"])
            return False, warnings + self._finish(record, paths, committed=False)
        if (
            hashes["target"] == old
            and hashes["backup"] is None
            and record["phase"]
            in {"prepared", "old_moved", "rolling_back", "rolled_back"}
            and (
                hashes["stage"] == new
                or record["phase"] in {"rolling_back", "rolled_back"}
            )
        ):
            return False, self._finish(record, paths, committed=False)
        raise self._error(journal, "ambiguous target/stage/backup state")

    def recover(self) -> RecoveryReport:
        with self.lock:
            report = RecoveryReport()
            if not self.lock.file_locked:
                return report
            self._check(self.parent)
            candidates = sorted(
                set(self.parent.glob(".skill-*.journal.json"))
                | set(self.parent.glob(".skill-*.journal.tmp"))
            )
            transactions: dict[Path, tuple[dict, dict[str, Path]]] = {}
            targets: set[str] = set()
            for path in candidates:
                try:
                    record, paths = self._record(path)
                except SkillRecoveryError:
                    raise
                except (OSError, ValueError) as exc:
                    raise self._error(path, f"cannot validate journal: {exc}") from exc
                canonical = paths["journal"]
                if canonical in transactions:
                    previous = transactions[canonical][0]
                    if any(
                        previous[key] != record[key] for key in record if key != "phase"
                    ):
                        raise self._error(canonical, "conflicting journal records")
                    continue
                key = portable_key(record["target"])
                if key in targets:
                    raise self._error(
                        canonical, "multiple journals refer to the same target"
                    )
                targets.add(key)
                if canonical.exists() and path != canonical:
                    record, paths = self._record(canonical)
                transactions[canonical] = record, paths
            for record, paths in transactions.values():
                try:
                    if not paths["journal"].exists():
                        retry_filesystem(
                            lambda paths=paths: os.replace(
                                paths["temporary"], paths["journal"]
                            )
                        )
                    _, warnings = self._recover_one(record, paths)
                    report.performed = True
                    report.warnings.extend(warnings)
                except SkillRecoveryError:
                    raise
                except (OSError, ValueError) as exc:
                    raise self._error(
                        paths["journal"], f"filesystem recovery failed: {exc}"
                    ) from exc
            return report

    def publish(
        self, stage: Path, target: Path, old_hash: str | None, new_hash: str
    ) -> dict:
        with self.lock.hold(create=True):
            recovery = self.recover()
            match = _ARTIFACT.fullmatch(stage.name)
            if not match or match[3] != "stage" or match[1] != target.name:
                raise ValueError("Invalid skill staging identity")
            paths = self._paths(match[1], match[2])
            if stage != paths["stage"] or target != paths["target"]:
                raise ValueError("Skill publication path mismatch")
            outstanding = []
            for path in self.parent.glob(".skill-*.journal.*"):
                identity = _ARTIFACT.fullmatch(path.name)
                if identity and identity[1] == target.name:
                    outstanding.append(path)
            if outstanding:
                raise self._error(
                    min(outstanding),
                    "previous publication cleanup is still pending for this target",
                )
            if self._hash(stage) != new_hash or self._hash(target) != old_hash:
                raise ValueError("Skill hash conflict: tree changed before publication")
            try:
                _atomic_publish(stage, target, exchange=old_hash is not None)
                return {
                    "publication_backend": "linux-renameat2",
                    "publication_guarantee": "atomic-exchange"
                    if old_hash is not None
                    else "atomic-no-replace",
                    "recovery_performed": recovery.performed,
                    "warnings": recovery.warnings,
                }
            except AtomicPublicationUnavailable:
                pass
            warnings = list(recovery.warnings)
            record = {
                "version": 1,
                "project_id": hashlib.sha256(
                    str(self.project_root).encode()
                ).hexdigest(),
                "operation_id": match[2],
                "skill_name": target.name,
                **{key: paths[key].name for key in ("target", "stage", "backup")},
                "phase": "prepared",
                "old_hash": old_hash,
                "new_hash": new_hash,
            }
            try:
                self._write_record(record, paths, warnings)
                if self._hash(target) != old_hash or self._hash(stage) != new_hash:
                    raise ValueError(
                        "Skill hash conflict: files changed after journal preparation"
                    )
                if old_hash is not None:
                    self._move_absent(target, paths["backup"])
                record = record | {"phase": "old_moved"}
                self._write_record(record, paths, warnings)
                self._move_absent(stage, target)
                record = record | {"phase": "new_moved"}
                self._write_record(record, paths, warnings)
                if self._hash(target) != new_hash:
                    raise self._error(
                        paths["journal"], "published tree hash disagreement"
                    )
                warnings.extend(self._finish(record, paths, committed=True))
            except Exception as exc:
                try:
                    if paths["journal"].exists() or paths["temporary"].exists():
                        actual = (
                            paths["journal"]
                            if paths["journal"].exists()
                            else paths["temporary"]
                        )
                        current, paths = self._record(actual)
                        committed, recovered_warnings = self._recover_one(
                            current, paths
                        )
                        recovery.performed = True
                        warnings.extend(recovered_warnings)
                    else:
                        committed = (
                            self._hash(target) == new_hash and not stage.exists()
                        )
                except (OSError, ValueError) as recovery_error:
                    raise self._error(
                        paths["journal"],
                        f"publication interrupted; recovery failed: {recovery_error}",
                    ) from exc
                if not committed:
                    raise
                warnings.append(
                    f"Publication committed and recovered after filesystem error: {exc}"
                )
            return {
                "publication_backend": "portable-journaled-swap",
                "publication_guarantee": "recoverable-staged-swap",
                "recovery_performed": recovery.performed,
                "warnings": warnings,
            }

    def discard_stage(self, stage: Path, warnings: list[str]) -> None:
        match = _ARTIFACT.fullmatch(stage.name)
        if not match:
            raise ValueError("Invalid skill staging identity")
        paths = self._paths(match[1], match[2])
        if paths["journal"].exists() or paths["temporary"].exists():
            return
        if stage.exists():
            try:
                self._remove_tree(stage)
            except OSError as exc:
                warnings.append(
                    f"Could not remove unpublished staging directory {stage}: {exc}"
                )
