"""Human startup consent for exact project roots, independent of checkout config."""

import errno
import json
import os
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import click


class ProjectTrustError(ValueError):
    """Trust could not be read or persisted safely; continue without trust."""


def canonical_project_root(path: str | Path | None = None, *, exists=True) -> Path:
    root = Path(path or Path.cwd()).expanduser().resolve(strict=exists)
    if exists and not root.is_dir():
        raise ProjectTrustError("The launch project must be an existing directory")
    return root


def _project_key(root: Path) -> str:
    """Key a captured canonical root without folding case or resolving it again."""
    return str(root)


def project_root_is_unchanged(root: Path) -> bool:
    """Compare canonical spellings exactly, not Windows Path equality."""
    return str(canonical_project_root(root)) == str(root)


def _display_root(root: Path) -> str:
    return json.dumps(str(root), ensure_ascii=True)


def project_trust_message(root: Path) -> str:
    return (
        f"Project directory: {_display_root(root)}\n\n"
        "Trust enables project skills and project plugins for this exact directory.\n"
        "WARNING: Project plugins can run local code as your user.\n"
        "Only trust a project whose contents and code you trust.\n"
        "Acceptance is remembered on this computer; execution/write approvals remain unchanged."
    )


class ProjectTrustStore:
    """Locked, user-owned consent records. This is not a same-user code sandbox."""

    def __init__(self, directory: Path | None = None, *, timeout: float = 2.0):
        self.directory = Path(
            os.path.abspath(directory or Path.home().resolve() / ".AgentCrew")
        )
        self.path = self.directory / "project-trust.json"
        self.lock_path = self.directory / "project-trust.lock"
        self.timeout = timeout

    @staticmethod
    def _check_links(path: Path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if (
            stat.S_ISLNK(info.st_mode)
            or path.is_junction()
            or getattr(info, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ProjectTrustError(f"Unsafe linked trust-store path: {path}")

    @staticmethod
    def _check_owner(info, path: Path, *, file: bool):
        if hasattr(os, "getuid"):
            if info.st_uid != os.getuid():
                raise ProjectTrustError(
                    f"Trust storage is not owned by this user: {path}"
                )
            forbidden = 0o077 if file else 0o022
            if info.st_mode & forbidden:
                raise ProjectTrustError(
                    f"Unsafe trust-storage permissions at {path}; "
                    + (
                        "use mode 600"
                        if file
                        else "remove group/other write permission"
                    )
                )

    def _check_directory(self, *, create: bool):
        for component in [*reversed(self.directory.parents), self.directory]:
            self._check_links(component)
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.exists():
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ProjectTrustError(
                    f"Trust storage is not a directory: {self.directory}"
                )
            self._check_owner(info, self.directory, file=False)

    def _check_file(self, path: Path):
        self._check_links(path)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProjectTrustError(
                    f"Trust storage must be a non-hardlinked file: {path}"
                )
            self._check_owner(info, path, file=True)

    def _outside_project(self, root: Path):
        if not project_root_is_unchanged(root):
            raise ProjectTrustError(
                "The launch project path changed; restart before trusting it"
            )
        self._check_directory(create=False)
        if self.directory.is_relative_to(root):
            raise ProjectTrustError(
                "Trust storage must be outside the launch project; "
                "use the explicit one-launch flag for this directory"
            )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._check_directory(create=True)
        self._check_file(self.lock_path)
        fd = os.open(
            self.lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        acquired = False
        try:
            info = os.fstat(fd)
            self._check_file(self.lock_path)
            current = self.lock_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise ProjectTrustError(f"Unsafe trust lock: {self.lock_path}")
            self._check_owner(info, self.lock_path, file=True)
            if info.st_size == 0:
                os.write(fd, b"\0")
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._lock_fd(fd)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProjectTrustError(
                            f"Timed out locking project trust: {self.lock_path}"
                        ) from exc
                    time.sleep(min(0.02, remaining))
            yield
        finally:
            try:
                if acquired:
                    self._lock_fd(fd, unlock=True)
            finally:
                os.close(fd)

    @staticmethod
    def _lock_fd(fd: int, *, unlock=False):
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _read(self) -> set[str]:
        self._check_file(self.path)
        if not self.path.exists():
            return set()
        fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            current = self.path.lstat()
            self._check_owner(info, self.path, file=True)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise ProjectTrustError(f"Unsafe project trust store: {self.path}")
            raw = stream.read(65537)
        try:
            record = json.loads(raw)
            if (
                isinstance(record, dict)
                and type(record.get("version")) is int
                and record["version"] == 1
            ):
                raise ProjectTrustError(
                    f"Version 1 project trust records are invalidated: {self.path}. "
                    "Their original path spelling cannot be recovered safely. "
                    "Stop AgentCrew, quarantine or remove this file locally, then "
                    "restart and approve each exact project again. No trust was granted."
                )
            if (
                len(raw) > 65536
                or not isinstance(record, dict)
                or set(record) != {"version", "projects"}
                or type(record["version"]) is not int
                or record["version"] != 2
                or not isinstance(record["projects"], list)
                or len(record["projects"]) > 1024
            ):
                raise ValueError("Invalid trust-store schema")
            projects = record["projects"]
            if any(
                not isinstance(path, str)
                or not path
                or len(path) > 4096
                or not Path(path).is_absolute()
                or ".." in Path(path).parts
                or os.path.normpath(path) != path
                for path in projects
            ) or len(set(projects)) != len(projects):
                raise ValueError("Invalid project paths")
            return set(projects)
        except ProjectTrustError:
            raise
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ProjectTrustError(
                f"Corrupt project trust store: {self.path}. "
                "Inspect or quarantine it locally before retrying; no trust was granted."
            ) from exc

    def _write(self, projects: set[str]):
        raw = json.dumps(
            {"version": 2, "projects": sorted(projects)}, indent=2
        ).encode()
        if len(raw) > 65536 or len(projects) > 1024:
            raise ProjectTrustError("Project trust store is full; revoke unused grants")
        fd, name = tempfile.mkstemp(
            prefix=".project-trust-", suffix=".tmp", dir=self.directory
        )
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_file(self.path)
            os.replace(temporary, self.path)
            try:
                directory_fd = os.open(
                    self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                if exc.errno not in {
                    errno.EACCES,
                    errno.EINVAL,
                    errno.ENOTSUP,
                    errno.EBADF,
                }:
                    raise
        finally:
            temporary.unlink(missing_ok=True)

    def is_trusted(self, root: Path) -> bool:
        self._outside_project(root)
        if not self.directory.exists():
            return False
        with self._locked():
            return _project_key(root) in self._read()

    def grant(self, root: Path):
        self._outside_project(root)
        key = _project_key(root)
        if len(key) > 4096:
            raise ProjectTrustError("Project path exceeds the trust-store limit")
        with self._locked():
            projects = self._read()
            projects.add(key)
            self._write(projects)

    def revoke(self, root: Path) -> bool:
        root = canonical_project_root(root, exists=False)
        if not self.directory.exists():
            return False
        with self._locked():
            projects = self._read()
            existed = _project_key(root) in projects
            projects.discard(_project_key(root))
            self._write(projects)
            return existed


class ProjectTrustResolver:
    """Resolve once for one launch; unattended modes never read interactive grants."""

    def __init__(self, root: Path, override: bool | None, store=None):
        self.root = canonical_project_root(root)
        self.override = override
        self.store = store or ProjectTrustStore()
        self.resolved = False
        self.trusted = override is True

    def resolve(
        self,
        *,
        interactive: bool,
        prompt: Callable[[Path], bool],
        diagnostic: Callable[[str], None],
    ) -> bool:
        if self.resolved:
            return self.trusted
        self.resolved = True
        if self.override is not None or not interactive:
            return self.trusted
        try:
            if self.store.is_trusted(self.root):
                self.trusted = True
            elif prompt(self.root):
                self.store.grant(self.root)
                self.trusted = True
        except (
            OSError,
            ProjectTrustError,
            EOFError,
            click.Abort,
            RuntimeError,
            ImportError,
        ) as exc:
            self.trusted = False
            diagnostic(f"Continuing without project trust: {exc}")
        return self.trusted


def console_can_prompt() -> bool:
    try:
        return bool(
            sys.stdin and sys.stderr and sys.stdin.isatty() and sys.stderr.isatty()
        )
    except (OSError, ValueError, AttributeError):
        return False


def confirm_project_trust_console(root: Path) -> bool:
    click.echo(project_trust_message(root), err=True)
    return click.confirm(
        "Trust this project? (No: Continue without trust)", default=False, err=True
    )


def confirm_project_trust_qt(root: Path) -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    dialog = QMessageBox()
    dialog.setWindowTitle("Project trust")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(project_trust_message(root))
    accept = dialog.addButton("Trust this project", QMessageBox.ButtonRole.AcceptRole)
    decline = dialog.addButton(
        "Continue without trust", QMessageBox.ButtonRole.RejectRole
    )
    dialog.setDefaultButton(decline)
    dialog.setEscapeButton(decline)
    dialog.exec()
    return dialog.clickedButton() is accept


def report_project_trust_qt(message: str):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    dialog = QMessageBox()
    dialog.setWindowTitle("Project trust unavailable")
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(message)
    dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
    dialog.exec()


@click.command("revoke-project-trust")
@click.argument(
    "project_path", default=".", type=click.Path(path_type=Path, file_okay=False)
)
def revoke_project_trust(project_path: Path):
    """Forget interactive consent for PROJECT_PATH (default: current directory)."""
    try:
        root = canonical_project_root(project_path, exists=False)
        removed = ProjectTrustStore().revoke(root)
    except (OSError, ProjectTrustError) as exc:
        raise click.ClickException(f"Could not revoke project trust: {exc}") from exc
    click.echo(
        f"{'Revoked trust for' if removed else 'No stored trust for'} {_display_root(root)}"
    )
