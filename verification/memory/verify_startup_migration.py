"""Fictional startup policy checks: no uvx, network, model, or private memory."""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory import engine_selection as engine
from AgentCrew.modules.memory import startup_migration as startup
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository


def require(value, message):
    if not value:
        raise AssertionError(message)


def rejected(call):
    try:
        call()
    except engine.MemoryEngineError as exc:
        return str(exc)
    raise AssertionError("Unsafe startup was accepted")


class Case:
    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agentcrew-startup-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "memory root"
        self.source.mkdir()
        self.destination = self.source / "agent_memory.sqlite3"
        self.work = self.source / "migration-work"
        self.backups = self.root / "backups"
        self.segment = self.source / "11111111-1111-4111-8111-111111111111"
        self.empty_segment = self.source / "22222222-2222-4222-8222-222222222222"
        self.unrelated_uuid = self.source / "33333333-3333-4333-8333-333333333333"
        self.segment.mkdir()
        (self.segment / "header.bin").write_bytes(b"fictional Chroma segment")
        self.empty_segment.mkdir()
        self.unrelated_uuid.mkdir()
        (self.unrelated_uuid / "keep.txt").write_text("not Chroma")
        (self.source / "unrelated.txt").write_text("leave this file")
        with sqlite3.connect(self.source / "chroma.sqlite3") as connection:
            connection.execute("CREATE TABLE segments(id TEXT)")
            connection.executemany(
                "INSERT INTO segments VALUES(?)",
                [(self.segment.name,), (self.empty_segment.name,)],
            )
        self.confirm = Mock(return_value=True)
        self.connections = []
        self.execute = Mock(side_effect=self.success)
        self.find = Mock(return_value="/fictional/uvx")
        self.plan = startup.MigrationPlan(self.source, self.destination, self.work)
        return self

    def __exit__(self, *args):
        for connection in self.connections:
            connection.close()
        self.temporary.cleanup()

    def success(self, argv):
        require(argv == self.plan.argv, "Execution did not use expanded argv")
        SQLiteMemoryRepository(self.destination)
        connection = sqlite3.connect(self.destination)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE schema_version SET version=version")
        connection.commit()
        self.connections.append(connection)
        self.work.mkdir(exist_ok=True)
        (self.work / "progress.flag").write_text("keep work")
        startup._write(self.work / "report.json", {"status": "verified"})
        (self.source / "agent_memory.sqlite3.lock").write_text("keep SQLite lock")
        return SimpleNamespace(returncode=0)

    def ensure(self, *, interactive=True, **changes):
        options = {
            "interactive": interactive,
            "confirm": self.confirm,
            "execute": self.execute,
            "find_executable": self.find,
            "backup_root": self.backups,
        }
        options.update(changes)
        return startup.ensure_startup_sqlite_store(
            self.destination, self.source, self.work, **options
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.source)): (
                path.read_bytes(),
                path.stat().st_mode & 0o777,
            )
            for path in self.source.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    def chroma_snapshot(self):
        return {
            path.name: startup._hash(path) for path in startup._artifacts(self.source)
        }

    def manifest(self):
        directories = sorted(self.backups.glob(startup.PREFIX + "*"))
        completed = [
            (path, json.loads((path / "manifest.json").read_text()))
            for path in directories
        ]
        return next(
            (path, data)
            for path, data in completed
            if data["completion_status"] == "completed"
        )


def checks_commands_and_foreground_execution():
    with (
        tempfile.TemporaryDirectory() as temporary,
        patch.dict(os.environ, {"HOME": temporary}),
    ):
        source = Path(temporary) / ".AgentCrew/memorydb"
        plan = startup.MigrationPlan(
            source, source / "agent_memory.sqlite3", source / "migration-work"
        )
        require(
            plan.display
            == "uvx --from 'agentcrew-ai[chromadb]@latest' agentcrew-migrate-memory --source ~/.AgentCrew/memorydb/ --destination ~/.AgentCrew/memorydb/agent_memory.sqlite3 --work-dir ~/.AgentCrew/memorydb/migration-work/",
            "Default command differs from approved production command",
        )
        require(
            all(not value.startswith("~") for value in plan.argv),
            "Execution received unexpanded tilde",
        )
    with Case() as case:
        require(
            "'" in case.plan.display and str(case.source) in case.plan.display,
            "Custom paths were not safely quoted",
        )
        with patch.object(
            startup.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ) as run:
            startup._execute(case.plan.argv)
        require(run.call_args.args == (case.plan.argv,), "Command was not an argv list")
        require(
            run.call_args.kwargs == {"check": False, "shell": False},
            "Foreground inheritance/no-shell contract changed",
        )
        with patch.object(startup, "input", return_value="yes", create=True):
            require(startup._confirm("confirm"), "Yes was not accepted")
        with patch.object(startup, "input", return_value="", create=True):
            require(not startup._confirm("confirm"), "Empty response was accepted")


def checks_decline_and_launch_failures():
    for response in (False, EOFError(), KeyboardInterrupt()):
        with Case() as case:
            before = case.snapshot()
            if isinstance(response, BaseException):
                case.confirm.side_effect = response
            else:
                case.confirm.return_value = response
            rejected(case.ensure)
            require(
                case.snapshot() == before,
                "Decline/EOF/interrupt changed source or work",
            )
            require(
                not case.destination.exists() and not case.work.exists(),
                "Decline created SQLite or migration work",
            )
            case.execute.assert_not_called()
    with Case() as case:
        before = case.snapshot()
        case.find.return_value = None
        message = rejected(case.ensure)
        require(
            "uvx" in message and case.plan.display in message,
            "Missing executable lacks recovery command",
        )
        case.confirm.assert_not_called()
        case.execute.assert_not_called()
        require(case.snapshot() == before, "Missing executable changed data")
    for result in (
        SimpleNamespace(returncode=7),
        FileNotFoundError("fictional missing executable"),
    ):
        with Case() as case:
            before = case.chroma_snapshot()
            if isinstance(result, Exception):
                case.execute.side_effect = result
            else:
                case.execute.side_effect = None
                case.execute.return_value = result
            require(
                case.plan.display in rejected(case.ensure),
                "Launch failure lacks recovery command",
            )
            require(
                case.chroma_snapshot() == before and not case.backups.exists(),
                "Failed migration backed up or changed Chroma",
            )


def checks_verified_artifact_backup():
    with Case() as case:
        before = case.chroma_snapshot()
        with patch.object(
            engine,
            "require_chromadb",
            side_effect=AssertionError("startup imported Chroma"),
        ) as dependency:
            require(case.ensure() == case.destination, "Wrong canonical destination")
        dependency.assert_not_called()
        case.execute.assert_called_once_with(case.plan.argv)
        prompt = case.confirm.call_args.args[0]
        require(
            all(
                text in prompt
                for text in (
                    "Source:",
                    "Destination:",
                    "Work directory:",
                    "network",
                    "@latest",
                    case.plan.display,
                )
            ),
            "Incomplete confirmation disclosure",
        )
        directory, manifest = case.manifest()
        require(
            directory.parent == case.backups
            and directory.name.startswith(startup.PREFIX),
            "Wrong backup location",
        )
        require(directory.stat().st_mode & 0o777 == 0o700, "Backup mode is not 0700")
        require(
            (directory / "manifest.json").stat().st_mode & 0o777 == 0o600,
            "Manifest mode is not 0600",
        )
        require(
            manifest["paths"] == case.plan.paths
            and manifest["argv"] == case.plan.argv
            and manifest["display_command"] == case.plan.display,
            "Manifest paths/argv mismatch",
        )
        require(
            manifest["utc_time"].endswith("+00:00")
            and manifest["report_status"] == "verified",
            "Manifest lacks UTC/report status",
        )
        require(
            manifest["report_sha256"]
            == hashlib.sha256((case.work / "report.json").read_bytes()).hexdigest(),
            "Report hash mismatch",
        )
        require(
            {entry["relative_path"] for entry in manifest["artifacts"]} == set(before),
            "Backup moved unrelated files or omitted Chroma",
        )
        for entry in manifest["artifacts"]:
            require(
                entry["pre_sha256"]
                == entry["post_sha256"]
                == startup._hash(directory / entry["relative_path"]),
                "Backup hash mismatch",
            )
        require(
            not startup._artifacts(case.source),
            "Chroma artifacts remain after completed backup",
        )
        require(
            (case.source / "unrelated.txt").read_text() == "leave this file"
            and case.unrelated_uuid.exists(),
            "Unrelated data was moved",
        )
        require(
            case.destination.is_file() and (case.work / "progress.flag").is_file(),
            "SQLite or work was moved",
        )
        require(
            (case.source / "agent_memory.sqlite3-wal").exists()
            and (case.source / "agent_memory.sqlite3-shm").exists()
            and (case.source / "agent_memory.sqlite3.lock").is_file(),
            "SQLite sidecars/lock were moved",
        )
        case.confirm.reset_mock()
        require(case.ensure() == case.destination, "Completed clean startup failed")
        case.confirm.assert_not_called()


def checks_resume_report_and_integrity_rejection():
    with Case() as case:
        case.work.mkdir()
        (case.work / "snapshot.json").write_text("fictional pending state")
        startup._write(case.work / "report.json", {"status": "blocked"})
        SQLiteMemoryRepository(case.destination)
        require(case.ensure() == case.destination, "Resume failed")
        require(
            (case.work / "snapshot.json").read_text() == "fictional pending state",
            "Startup rewrote migration state",
        )
    for failure in (
        "missing_destination",
        "bad_integrity",
        "missing_report",
        "bad_report",
        "blocked_report",
    ):
        with Case() as case:
            before = case.chroma_snapshot()

            def execute(argv, failure=failure, case=case):
                if failure != "missing_destination":
                    if failure == "bad_integrity":
                        case.destination.write_bytes(b"not SQLite")
                    else:
                        SQLiteMemoryRepository(case.destination)
                case.work.mkdir(exist_ok=True)
                if failure != "missing_report":
                    if failure == "bad_report":
                        (case.work / "report.json").write_text("{invalid")
                    else:
                        startup._write(
                            case.work / "report.json",
                            {
                                "status": "blocked"
                                if failure == "blocked_report"
                                else "applied"
                            },
                        )
                return SimpleNamespace(returncode=0)

            rejected(lambda: case.ensure(execute=execute))
            require(
                case.chroma_snapshot() == before and not case.backups.exists(),
                "Unverified data was backed up",
            )


def checks_move_rollback_and_recovery():
    for failure in (OSError("fictional move failure"), KeyboardInterrupt()):
        with Case() as case:
            before = case.chroma_snapshot()
            move = startup.shutil.move
            calls = []

            def interrupted(source, target, failure=failure, calls=calls, move=move):
                calls.append((source, target))
                if len(calls) == 2:
                    raise failure
                return move(source, target)

            with patch.object(startup.shutil, "move", side_effect=interrupted):
                require(
                    "rolled_back" in rejected(case.ensure),
                    "Move failure lacks rollback diagnosis",
                )
            require(case.chroma_snapshot() == before, "Rollback did not restore Chroma")
            manifests = list(case.backups.glob("*/manifest.json"))
            require(
                json.loads(manifests[0].read_text())["completion_status"]
                == "rolled_back",
                "Rollback state missing",
            )
            case.execute.reset_mock()
            case.execute.side_effect = AssertionError("completed recovery reran uvx")
            require(
                case.ensure() == case.destination, "Completed-report recovery failed"
            )
            case.execute.assert_not_called()
    with Case() as case:
        case.ensure()
        directory, manifest = case.manifest()
        entry = next(
            item
            for item in manifest["artifacts"]
            if item["relative_path"] == case.empty_segment.name
        )
        shutil.move(str(directory / case.empty_segment.name), str(case.empty_segment))
        manifest["completion_status"] = "in_progress"
        entry["post_sha256"] = None
        startup._write(directory / "manifest.json", manifest)
        case.execute.reset_mock()
        case.execute.side_effect = AssertionError("partial backup reran uvx")
        case.confirm.reset_mock()
        require(case.ensure() == case.destination, "Interrupted-backup recovery failed")
        case.execute.assert_not_called()
        case.confirm.assert_called_once()
        require(
            len(list(case.backups.glob(startup.PREFIX + "*"))) == 1
            and not case.empty_segment.exists(),
            "Recovery did not finish the original backup",
        )
        require(
            json.loads((directory / "manifest.json").read_text())["completion_status"]
            == "completed",
            "Recovery completion not recorded",
        )


def checks_failed_rollback_is_observable():
    with Case() as case:
        move = startup.shutil.move
        count = 0

        def fail_move_and_rollback(source, destination):
            nonlocal count
            count += 1
            if count >= 2:
                raise OSError("fictional move/rollback failure")
            return move(source, destination)

        with patch.object(startup.shutil, "move", side_effect=fail_move_and_rollback):
            message = rejected(case.ensure)
        require(
            "rollback_failed" in message, "Partial rollback falsely reported success"
        )
        directory = next(case.backups.iterdir())
        require(
            json.loads((directory / "manifest.json").read_text())["completion_status"]
            == "rollback_failed",
            "Missing recovery state",
        )
        execute = Mock(side_effect=AssertionError("recovery reran migration"))
        case.ensure(execute=execute)
        require(
            json.loads((directory / "manifest.json").read_text())["completion_status"]
            == "completed",
            "Failed rollback could not recover",
        )
        execute.assert_not_called()


def checks_permanent_noninteractive_skip():
    for surface in ("server", "job", "a2a", "acp"):
        with Case() as case:
            before = case.chroma_snapshot()
            require(
                case.ensure(interactive=False) == case.destination,
                f"Noninteractive {surface} did not select canonical store",
            )
            marker = case.source / startup.SKIP_NAME
            saved = marker.read_bytes()
            data = json.loads(saved)
            require(
                marker.stat().st_mode & 0o777 == 0o600
                and data["version"] == 1
                and data["reason"] == "noninteractive",
                "Skip marker mode/identity mismatch",
            )
            require(
                data["paths"] == case.plan.paths
                and data["recovery_command"] == case.plan.display
                and "prevents safe automatic merge" in data["warning"],
                "Skip marker lacks recovery warning",
            )
            require(
                case.chroma_snapshot() == before
                and not case.destination.exists()
                and not case.work.exists(),
                "Skip moved source or created migration work",
            )
            require(
                case.ensure(interactive=True) == case.destination
                and marker.read_bytes() == saved,
                "Later interactive start changed permanent marker",
            )
            SQLiteMemoryRepository(case.destination)
            require(
                case.ensure(interactive=True) == case.destination
                and marker.read_bytes() == saved,
                "Active skipped store was auto-migrated",
            )
            case.confirm.assert_not_called()
            case.execute.assert_not_called()
            case.find.assert_not_called()
    for malformed in ("{invalid", '{"version":1}', None):
        with Case() as case:
            case.ensure(interactive=False)
            marker = case.source / startup.SKIP_NAME
            if malformed is None:
                data = json.loads(marker.read_text())
                data["paths"]["source"] = "/other/source"
                marker.write_text(json.dumps(data))
            else:
                marker.write_text(malformed)
            before = marker.read_bytes()
            rejected(lambda: case.ensure(interactive=True))
            require(
                marker.read_bytes() == before and not case.destination.exists(),
                "Malformed marker was overwritten",
            )
            case.execute.assert_not_called()
    with Case() as case:
        SQLiteMemoryRepository(case.destination)
        require(
            case.ensure() == case.destination,
            "Active SQLite coexistence was not retained",
        )
        case.confirm.assert_not_called()
        case.execute.assert_not_called()


def checks_active_store_is_not_automatically_merged():
    for interactive in (False, True):
        with Case() as case:
            SQLiteMemoryRepository(case.destination)
            confirm = Mock(side_effect=AssertionError("active store prompted"))
            execute = Mock(side_effect=AssertionError("active store migrated"))
            before = case.snapshot()
            with patch.object(
                startup, "_artifacts", side_effect=AssertionError("unused Chroma read")
            ):
                require(
                    case.ensure(
                        interactive=interactive, confirm=confirm, execute=execute
                    )
                    == case.destination,
                    "Active SQLite not selected",
                )
            require(
                case.snapshot() == before, "Active store policy mutated legacy data"
            )
            confirm.assert_not_called()
            execute.assert_not_called()
    with Case() as case:
        SQLiteMemoryRepository(case.destination)
        case.work.mkdir()
        state = case.work / "snapshot.json"
        state.write_text("fictional pending state")

        def changed_state(message):
            state.unlink()
            case.work.rmdir()
            return True

        execute = Mock(
            side_effect=AssertionError("active store was merged after state change")
        )
        require(
            case.ensure(confirm=changed_state, execute=execute) == case.destination,
            "Changed resume state was not revalidated",
        )
        execute.assert_not_called()
        require(not case.backups.exists(), "Changed state triggered backup")


def require_backup_only_prompt(message, case):
    require(
        all(
            str(path) in message
            for path in (case.source, case.destination, case.work, case.backups)
        ),
        "Backup-only confirmation omitted paths",
    )
    text = message.casefold()
    require(
        "already verified" in text
        and "will not be rerun" in text
        and "stop all chroma/agentcrew writers" in text,
        "Backup-only confirmation omitted verification or quiescence disclosure",
    )
    require(
        all(
            fragment not in text
            for fragment in ("download", "network", "version drift", "@latest")
        ),
        "Backup-only confirmation claimed migration execution risk",
    )


def backup_only_state(case):
    paths = (case.source / "chroma.sqlite3", case.segment, case.empty_segment)
    return (
        {path.name: startup._hash(path) if path.exists() else None for path in paths},
        startup._hash(case.work),
        {str(path): path.read_bytes() for path in case.backups.glob("*/manifest.json")},
    )


def prepare_backup_recovery(case, state):
    case.ensure()
    directory, manifest = case.manifest()
    entries = (
        manifest["artifacts"]
        if state == "rolled_back"
        else [
            next(
                item
                for item in manifest["artifacts"]
                if item["relative_path"] == case.empty_segment.name
            )
        ]
    )
    for entry in entries:
        name = entry["relative_path"]
        shutil.move(str(directory / name), str(case.source / name))
        entry["post_sha256"] = None
    manifest["completion_status"] = state
    startup._write(directory / "manifest.json", manifest)
    case.confirm.reset_mock()
    return directory


def checks_backup_only_without_uvx():
    for status in ("verified", "applied"):
        for discover_fails in (False, True):
            with Case() as case:
                case.success(case.plan.argv)
                startup._write(case.work / "report.json", {"status": status})
                discover = (
                    Mock(side_effect=AssertionError("backup-only discovered uvx"))
                    if discover_fails
                    else Mock(return_value=None)
                )
                execute = Mock(side_effect=AssertionError("backup-only executed uvx"))
                require(
                    case.ensure(find_executable=discover, execute=execute)
                    == case.destination,
                    "Verified backup-only finalization needs uvx",
                )
                discover.assert_not_called()
                execute.assert_not_called()
                case.confirm.assert_called_once()
                require_backup_only_prompt(case.confirm.call_args.args[0], case)
                _, manifest = case.manifest()
                require(
                    manifest["report_status"] == status,
                    "Backup lost verified report status",
                )
                require(
                    not startup._artifacts(case.source), "Backup-only artifacts remain"
                )


def checks_backup_recovery_without_uvx():
    for state in ("in_progress", "rolled_back", "rollback_failed"):
        with Case() as case:
            directory = prepare_backup_recovery(case, state)
            discover = Mock(
                side_effect=AssertionError("backup recovery discovered uvx")
            )
            execute = Mock(side_effect=AssertionError("backup recovery executed uvx"))
            require(
                case.ensure(find_executable=discover, execute=execute)
                == case.destination,
                "Backup recovery failed without uvx",
            )
            discover.assert_not_called()
            execute.assert_not_called()
            case.confirm.assert_called_once()
            require_backup_only_prompt(case.confirm.call_args.args[0], case)
            completed, _ = case.manifest()
            if state != "rolled_back":
                require(
                    completed == directory, "Recovery did not finish original backup"
                )
            require(
                not startup._artifacts(case.source), "Recovery left Chroma artifacts"
            )


def checks_backup_only_decline_without_uvx():
    for partial in (False, True):
        for response in (False, EOFError(), KeyboardInterrupt()):
            with Case() as case:
                if partial:
                    prepare_backup_recovery(case, "in_progress")
                else:
                    case.success(case.plan.argv)
                before = backup_only_state(case)
                if isinstance(response, BaseException):
                    case.confirm.side_effect = response
                else:
                    case.confirm.return_value = response
                discover = Mock(
                    side_effect=AssertionError("declined backup discovered uvx")
                )
                execute = Mock(
                    side_effect=AssertionError("declined backup executed uvx")
                )
                rejected(
                    lambda discover=discover, execute=execute: case.ensure(
                        find_executable=discover, execute=execute
                    )
                )
                case.confirm.assert_called_once()
                require_backup_only_prompt(case.confirm.call_args.args[0], case)
                discover.assert_not_called()
                execute.assert_not_called()
                require(
                    backup_only_state(case) == before,
                    "Decline changed artifacts, work, or manifest",
                )


def checks_backup_only_consent_never_authorizes_migration():
    with Case() as case:
        case.success(case.plan.argv)
        before = case.chroma_snapshot()
        discover = Mock(side_effect=AssertionError("backup-only discovered uvx"))
        execute = Mock(side_effect=AssertionError("backup-only executed uvx"))

        def state_changed(message):
            require_backup_only_prompt(message, case)
            startup._write(case.work / "report.json", {"status": "blocked"})
            return True

        message = rejected(
            lambda: case.ensure(
                confirm=state_changed, find_executable=discover, execute=execute
            )
        )
        require(
            "backup-only" in message.casefold(),
            "Changed backup-only state lacks diagnosis",
        )
        discover.assert_not_called()
        execute.assert_not_called()
        require(
            case.chroma_snapshot() == before and not case.backups.exists(),
            "Changed state triggered backup",
        )


def checks_path_refusal_and_runtime_surfaces():
    for target in ("source", "destination", "work", "artifact"):
        with Case() as case:
            if target == "source":
                alias = case.root / "alias"
                alias.symlink_to(case.source, target_is_directory=True)
                callback = lambda alias=alias: startup.ensure_startup_sqlite_store(
                    case.destination,
                    alias,
                    case.work,
                    interactive=True,
                    confirm=case.confirm,
                    execute=case.execute,
                    backup_root=case.backups,
                )
            elif target == "destination":
                case.destination.symlink_to(case.source / "unrelated.txt")
                callback = case.ensure
            elif target == "work":
                case.work.symlink_to(case.segment, target_is_directory=True)
                callback = case.ensure
            else:
                (case.segment / "bad-link").symlink_to(case.source / "unrelated.txt")
                callback = case.ensure
            rejected(callback)
            case.execute.assert_not_called()
            case.confirm.assert_not_called()
    with Case() as case:
        rejected(
            lambda: startup.ensure_startup_sqlite_store(
                case.destination,
                case.source / ".." / "memory root",
                case.work,
                interactive=True,
            )
        )
    app = ast.parse((ROOT / "AgentCrew/app.py").read_text())
    modes = {}
    for owner in app.body:
        if not isinstance(owner, ast.ClassDef):
            continue
        for function in owner.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(function):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "setup_services"
                ):
                    modes[function.name] = next(
                        (
                            item.value.value
                            for item in call.keywords
                            if item.arg == "interactive"
                            and isinstance(item.value, ast.Constant)
                        ),
                        False,
                    )
    require(
        modes
        == {
            "run_console": True,
            "run_gui": True,
            "run_server": False,
            "run_acp": False,
            "run_job": False,
        },
        "Console/GUI or server/job/ACP propagation changed",
    )
    setup = (ROOT / "AgentCrew/setup.py").read_text()
    require(
        "interactive=interactive" in setup and "interactive: bool = False" in setup,
        "Setup lost explicit noninteractive default/propagation",
    )
    for path in (ROOT / "AgentCrew").rglob("*.py"):
        if path.name == "app.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setup_services"
            ):
                require(
                    not any(
                        item.arg == "interactive"
                        and isinstance(item.value, ast.Constant)
                        and item.value.value is True
                        for item in node.keywords
                    ),
                    "Noninteractive surface opted into prompts",
                )
    tree = ast.parse(
        (ROOT / "AgentCrew/modules/memory/engine_selection.py").read_text()
    )
    ensure = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "ensure_sqlite_store"
    )
    require(
        "_migrate_chroma_to_sqlite" not in ast.unparse(ensure)
        and "require_chromadb" not in ast.unparse(ensure),
        "Startup still uses in-process Chroma migration",
    )
    require(
        importlib.util.find_spec("AgentCrew.modules.memory.benchmark") is None,
        "Production benchmark shim remains",
    )
    benchmark = importlib.import_module("verification.memory.benchmark")
    require(
        Path(benchmark.__file__) == ROOT / "verification/memory/benchmark.py",
        "Verification benchmark import resolved elsewhere",
    )


def main():
    for check in (
        checks_commands_and_foreground_execution,
        checks_decline_and_launch_failures,
        checks_verified_artifact_backup,
        checks_resume_report_and_integrity_rejection,
        checks_move_rollback_and_recovery,
        checks_failed_rollback_is_observable,
        checks_permanent_noninteractive_skip,
        checks_active_store_is_not_automatically_merged,
        checks_path_refusal_and_runtime_surfaces,
        checks_backup_only_without_uvx,
        checks_backup_recovery_without_uvx,
        checks_backup_only_decline_without_uvx,
        checks_backup_only_consent_never_authorizes_migration,
    ):
        check()
        print(f"PASS {check.__name__}")
    print(
        "PASS startup migration policy; fictional data, injected uvx, no network/models"
    )


if __name__ == "__main__":
    main()
