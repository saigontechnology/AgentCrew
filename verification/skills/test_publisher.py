"""Complete-tree publication and deterministic interruption/recovery checks."""

import errno
import json
import multiprocessing
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills import publisher as module
from AgentCrew.modules.skills.publisher import (
    AtomicPublicationUnavailable,
    ProjectSkillLock,
    SkillRecoveryError,
)


class Interrupted(BaseException):
    pass


@pytest.fixture
def recipe():
    return {
        "name": "checks",
        "description": "Project checks",
        "instructions": "Original instructions",
        "scripts": [
            {
                "path": "scripts/check.py",
                "language": "python",
                "content": "print('check')\n",
            }
        ],
    }


@pytest.fixture
def service(tmp_path):
    return SkillsService(
        project_root=tmp_path, user_root=tmp_path / "home", trusted_project=True
    )


@pytest.fixture
def portable(monkeypatch):
    monkeypatch.setattr(
        module,
        "_atomic_publish",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AtomicPublicationUnavailable("forced portable")
        ),
    )


def pending(service):
    files = list(
        (service.project_root / ".agents/skills").glob(".skill-*.journal.json")
    )
    assert len(files) == 1
    return service._publisher._record(files[0])


def tree_snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def interrupt_at_phase(service, recipe, phase):
    publisher = service._publisher
    original = publisher._write_record

    def write(record, paths, warnings):
        original(record, paths, warnings)
        if record["phase"] == phase:
            raise Interrupted(phase)

    with (
        patch.object(publisher, "_write_record", side_effect=write),
        pytest.raises(Interrupted),
    ):
        service.craft_skill(**recipe, update_existing=True)


@pytest.mark.parametrize("backend", ["portable", "linux"])
def test_create_update_noop_hash_conflict_and_unrelated_files(
    service, recipe, backend, monkeypatch
):
    if backend == "linux" and not sys.platform.startswith("linux"):
        pytest.skip("Actual renameat2 backend needs Linux")
    if backend == "portable":
        monkeypatch.setattr(
            module,
            "_atomic_publish",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AtomicPublicationUnavailable()
            ),
        )
    result = service.craft_skill(**recipe)
    assert result["publication_backend"] == (
        "linux-renameat2" if backend == "linux" else "portable-journaled-swap"
    )
    assert result["publication_guarantee"] == (
        "atomic-no-replace" if backend == "linux" else "recoverable-staged-swap"
    )
    assert result["recovery_performed"] is False
    assert service.craft_skill(**recipe)["publication_guarantee"] == "unchanged"
    root = service.project_root / ".agents/skills/checks"
    (root / "keep.txt").write_text("Keep this user file")
    changed = recipe | {"instructions": "New instructions"}
    with pytest.raises(ValueError, match="update_existing"):
        service.craft_skill(**changed)
    with pytest.raises(ValueError, match="hash conflict"):
        service.craft_skill(
            **changed,
            update_existing=True,
            expected_existing_hash=result["content_hash"],
        )
    expected = service._files_hash(service._tree_files(root))
    result = service.craft_skill(
        **changed, update_existing=True, expected_existing_hash=expected
    )
    assert result["operation"] == "update"
    assert result["publication_guarantee"] == (
        "atomic-exchange" if backend == "linux" else "recoverable-staged-swap"
    )
    assert (root / "keep.txt").read_text() == "Keep this user file"
    assert service.get_skill("checks")["body"] == "New instructions"
    assert not list(root.parent.glob(".skill-*.journal.*"))


@pytest.mark.parametrize("platform", ["win32", "darwin", "unsupported"])
def test_non_linux_automatically_uses_portable_backend(service, recipe, platform):
    with patch.object(module.sys, "platform", platform):
        result = service.craft_skill(**recipe)
        updated = service.craft_skill(
            **(recipe | {"instructions": "Updated"}), update_existing=True
        )
    assert (
        result["publication_backend"]
        == updated["publication_backend"]
        == "portable-journaled-swap"
    )


@pytest.mark.parametrize("error", [errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP])
def test_unsupported_linux_capability_falls_back(service, recipe, error):
    function = type("Rename", (), {"__call__": lambda self, *args: -1})()
    with (
        patch.object(
            module.ctypes,
            "CDLL",
            return_value=type("Libc", (), {"renameat2": function})(),
        ),
        patch.object(module.ctypes, "get_errno", return_value=error),
    ):
        result = service.craft_skill(**recipe)
    assert result["publication_backend"] == "portable-journaled-swap"


def test_permission_error_is_not_misclassified_as_unsupported(service, recipe):
    with (
        patch.object(module, "_atomic_publish", side_effect=PermissionError("denied")),
        pytest.raises(PermissionError),
    ):
        service.craft_skill(**recipe)
    assert not (service.project_root / ".agents/skills/checks").exists()


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("prepared", "Original instructions"),
        ("old_moved", "Original instructions"),
        ("new_moved", "Changed"),
        ("verified", "Changed"),
    ],
)
def test_recovery_after_each_journal_phase_is_idempotent(
    service, recipe, portable, phase, expected
):
    service.craft_skill(**recipe)
    changed = recipe | {"instructions": "Changed"}
    interrupt_at_phase(service, changed, phase)
    record, paths = pending(service)
    assert record["phase"] == phase
    recovered = service._publisher.recover()
    assert recovered.performed
    assert service._publisher.recover().performed is False
    assert service.get_skill("checks")["body"] == expected
    assert set(service.get_skill_names()) == {"checks"}
    assert not paths["stage"].exists() and not paths["backup"].exists()


@pytest.mark.parametrize(
    "which,expected", [("old", "Original instructions"), ("new", "Changed")]
)
def test_recovery_when_rename_precedes_phase_update(
    service, recipe, portable, which, expected
):
    service.craft_skill(**recipe)
    original = service._publisher._move_absent
    target = service.project_root / ".agents/skills/checks"

    def move(source, destination):
        original(source, destination)
        if (which == "old" and source == target) or (
            which == "new" and destination == target
        ):
            raise Interrupted(which)

    with (
        patch.object(service._publisher, "_move_absent", side_effect=move),
        pytest.raises(Interrupted),
    ):
        service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
    assert service.get_skill("checks")["body"] == expected
    assert not list(target.parent.glob(".skill-*.journal.*"))


def test_missing_target_with_only_backup_restores_old(service, recipe, portable):
    service.craft_skill(**recipe)
    interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "old_moved")
    _, paths = pending(service)
    shutil.rmtree(paths["stage"])
    assert not paths["target"].exists() and paths["backup"].exists()
    assert service._publisher.recover().performed
    assert service.get_skill("checks")["body"] == "Original instructions"


@pytest.mark.parametrize("phase", ["prepared", "old_moved", "new_moved", "verified"])
def test_interrupted_create_recovers_without_partial_target(
    service, recipe, portable, phase
):
    interrupt_at_phase(service, recipe, phase)
    record, paths = pending(service)
    report = service._publisher.recover()
    assert report.performed and not service._publisher.recover().performed
    assert paths["target"].exists() == (phase in {"new_moved", "verified"})
    if paths["target"].exists():
        assert (
            service._files_hash(service._tree_files(paths["target"]))
            == record["new_hash"]
        )


@pytest.mark.parametrize("cleanup", ["backup", "journal"])
def test_cleanup_failure_warns_and_later_recovery_finishes(
    service, recipe, portable, cleanup
):
    service.craft_skill(**recipe)
    publisher = service._publisher
    method = "_remove_tree" if cleanup == "backup" else "_clean_journal"
    with patch.object(publisher, method, side_effect=PermissionError("held open")):
        result = service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
        assert result["warnings"] and service.get_skill("checks")["body"] == "Changed"
    assert publisher.recover().performed
    assert not publisher.recover().performed


@pytest.mark.parametrize("cleanup", ["backup", "journal"])
def test_interruption_after_cleanup_is_recoverable(service, recipe, portable, cleanup):
    service.craft_skill(**recipe)
    publisher = service._publisher
    method = "_remove_tree" if cleanup == "backup" else "_clean_journal"
    original = getattr(publisher, method)

    def clean(*args):
        original(*args)
        raise Interrupted(cleanup)

    with patch.object(publisher, method, side_effect=clean), pytest.raises(Interrupted):
        service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
    publisher.recover()
    assert service.get_skill("checks")["body"] == "Changed"
    assert not publisher.recover().performed


def test_hash_disagreement_preserves_all_evidence_and_blocks_catalog(
    service, recipe, portable
):
    service.craft_skill(**recipe)
    interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "new_moved")
    _, paths = pending(service)
    (paths["target"] / "external.txt").write_text("Concurrent edit")
    before = tree_snapshot(paths["target"].parent)
    with pytest.raises(SkillRecoveryError, match="Evidence retained"):
        service.get_skill_names()
    assert service._skills == {}
    assert tree_snapshot(paths["target"].parent) == before


def test_ambiguous_duplicate_old_tree_is_not_guessed(service, recipe, portable):
    service.craft_skill(**recipe)
    interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "prepared")
    _, paths = pending(service)
    shutil.copytree(paths["target"], paths["backup"])
    before = tree_snapshot(paths["target"].parent)
    with pytest.raises(SkillRecoveryError, match="ambiguous"):
        service._publisher.recover()
    assert tree_snapshot(paths["target"].parent) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "../outside"),
        ("stage", "/tmp/outside"),
        ("backup", "C:/outside"),
        ("operation_id", "bad"),
        ("skill_name", "../bad"),
        ("phase", "unknown"),
        ("phase", []),
        ("old_hash", "bad"),
        ("version", 2),
        ("project_id", "bad"),
    ],
)
def test_untrusted_journal_fields_are_rejected_without_mutation(
    service, recipe, portable, field, value
):
    service.craft_skill(**recipe)
    interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "prepared")
    record, paths = pending(service)
    record[field] = value
    paths["journal"].write_text(json.dumps(record))
    before = tree_snapshot(paths["target"].parent)
    with pytest.raises((ValueError, SkillRecoveryError)):
        service._publisher.recover()
    assert tree_snapshot(paths["target"].parent) == before


def test_temporary_journal_and_fresh_service_recovery(service, recipe, portable):
    service.craft_skill(**recipe)
    interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "old_moved")
    _, paths = pending(service)
    paths["journal"].rename(paths["temporary"])
    fresh = SkillsService(
        project_root=service.project_root,
        user_root=service.user_root,
        trusted_project=True,
    )
    assert fresh.get_skill("checks")["body"] == "Original instructions"
    assert not fresh._publisher.recover().performed


def test_untrusted_project_does_not_run_recovery(service, recipe, portable):
    service.craft_skill(**recipe)
    interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "old_moved")
    _, paths = pending(service)
    before = tree_snapshot(paths["target"].parent)
    untrusted = SkillsService(
        project_root=service.project_root, user_root=service.user_root
    )
    assert untrusted.get_skill_names() == []
    assert tree_snapshot(paths["target"].parent) == before
    service._publisher.recover()


def test_hidden_artifacts_are_not_discovered(service, recipe, portable):
    service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills"
    for name in [".skill-orphan.stage", ".skill-orphan.backup", ".ignored-lock"]:
        hidden = root / name
        hidden.mkdir()
        (hidden / "SKILL.md").write_text(
            "---\nname: hidden\ndescription: Must not disclose\n---\nbody"
        )
    service.refresh()
    assert service.get_skill_names() == ["checks"]


def sharing_error():
    error = PermissionError("sharing violation")
    error.winerror = 32
    return error


def test_windows_sharing_retry_is_bounded():
    operation = MockOperation([sharing_error(), sharing_error(), "ok"])
    with patch.object(module.time, "sleep") as sleep:
        assert module.retry_filesystem(operation) == "ok"
    assert operation.calls == 3 and [c.args[0] for c in sleep.call_args_list] == [
        0.025,
        0.05,
    ]
    operation = MockOperation([sharing_error()] * 3)
    with patch.object(module.time, "sleep"), pytest.raises(PermissionError):
        module.retry_filesystem(operation)
    assert operation.calls == 3
    operation = MockOperation([OSError(errno.EIO, "disk error")])
    with pytest.raises(OSError):
        module.retry_filesystem(operation)
    assert operation.calls == 1


class MockOperation:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.mark.parametrize("block_restore", [False, True])
def test_exhausted_sharing_retries_roll_back_or_preserve_for_retry(
    service, recipe, portable, block_restore
):
    service.craft_skill(**recipe)
    real_rename = os.rename

    def rename(source, destination):
        if str(source).endswith(".stage") or (
            block_restore and str(source).endswith(".backup")
        ):
            raise sharing_error()
        return real_rename(source, destination)

    with (
        patch.object(module.os, "rename", side_effect=rename),
        patch.object(module.time, "sleep"),
        pytest.raises((OSError, SkillRecoveryError)),
    ):
        service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
    if block_restore:
        _, paths = pending(service)
        assert not paths["target"].exists() and paths["backup"].exists()
        result = service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
        assert result["recovery_performed"] is True
    else:
        assert service.get_skill("checks")["body"] == "Original instructions"


def test_target_appearance_fails_without_overwrite(service, recipe, portable):
    publisher = service._publisher
    original = publisher._move_absent
    target = service.project_root / ".agents/skills/checks"

    def move(source, destination):
        if destination == target:
            destination.mkdir()
            (destination / "external.txt").write_text("External writer")
        original(source, destination)

    with (
        patch.object(publisher, "_move_absent", side_effect=move),
        pytest.raises(SkillRecoveryError),
    ):
        service.craft_skill(**recipe)
    assert (target / "external.txt").read_text() == "External writer"


def _hold_project_lock(root, connection):
    lock = ProjectSkillLock(Path(root), timeout=1)
    with lock.hold(create=True):
        connection.send("locked")
        connection.recv()
    connection.close()


def test_actual_cross_process_lock_timeout_and_release(tmp_path):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe()
    child = context.Process(target=_hold_project_lock, args=(str(tmp_path), sender))
    child.start()
    try:
        assert receiver.poll(10) and receiver.recv() == "locked"
        lock = ProjectSkillLock(tmp_path, timeout=0.04)
        with pytest.raises(TimeoutError, match="Timed out"):
            lock.acquire()
        receiver.send("release")
        child.join(10)
        assert child.exitcode == 0
        with lock:
            assert lock.file_locked
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        receiver.close()
        sender.close()


def test_same_project_services_share_reentrant_lock(service):
    second = SkillsService(
        project_root=service.project_root,
        user_root=service.user_root,
        trusted_project=True,
    )
    assert second._lock is service._lock
    with service._publisher.lock.hold(create=True):
        second.refresh()
        assert second._lock.file_locked
