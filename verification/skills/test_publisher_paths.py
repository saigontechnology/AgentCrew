"""Portable path rules and Windows adapter safeguards, exercised without an OS claim."""

import errno
import os
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from AgentCrew.modules.skills import publisher as module
from AgentCrew.modules.skills.publisher import (
    ProjectSkillLock,
    check_project_path,
    validate_relative_names,
)
from verification.skills import test_publisher as publisher_tests
from verification.skills.test_skills import write_skill


@pytest.fixture
def service(tmp_path):
    return publisher_tests.service.__wrapped__(tmp_path)


@pytest.fixture
def recipe():
    return publisher_tests.recipe.__wrapped__()


@pytest.fixture
def portable(monkeypatch):
    return publisher_tests.portable.__wrapped__(monkeypatch)


@pytest.mark.parametrize(
    "path",
    [
        "scripts/CON.py",
        "scripts/PrN.py",
        "scripts/aux.py",
        "scripts/NUL.py",
        "scripts/COM1.py",
        "scripts/LPT9.py",
        "scripts/folder./check.py",
        "scripts/folder /check.py",
        "scripts/check.py:secret",
        "scripts/C:/check.py",
        "scripts\\check.py",
        "../outside.py",
        "/outside.py",
        "scripts//check.py",
        "scripts/control\x01.py",
    ],
)
def test_portable_path_rules_reject_windows_invalid_forms(path):
    with pytest.raises(ValueError):
        validate_relative_names([path])


@pytest.mark.parametrize(
    "name", ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"]
)
def test_reserved_skill_name_never_creates_project_files(service, recipe, name):
    with pytest.raises(ValueError, match="Reserved Windows device"):
        service.craft_skill(**(recipe | {"name": name}))
    assert not (service.project_root / ".agents").exists()


@pytest.mark.parametrize(
    "paths",
    [
        ["scripts/Check.py", "scripts/check.py"],
        ["scripts/Tools/a.py", "scripts/tools/b.py"],
    ],
)
def test_case_insensitive_script_collisions_before_writes(service, recipe, paths):
    changed = recipe | {
        "scripts": [
            {"path": path, "language": "python", "content": "print('safe')\n"}
            for path in paths
        ]
    }
    with pytest.raises(ValueError, match="Case-insensitive"):
        service.craft_skill(**changed)
    assert not (service.project_root / ".agents").exists()


@pytest.mark.parametrize(
    "root", [".agents/skills", ".claude/skills", ".codex/skills", ".od-skills"]
)
def test_case_only_project_root_collision(service, recipe, root):
    original = write_skill(service.project_root, "CHECKS", rel=root)
    before = original.read_bytes()
    with pytest.raises(ValueError, match="project skill.root"):
        service.craft_skill(**recipe)
    assert original.read_bytes() == before


def test_existing_case_collision_not_overwritten(service, recipe, portable):
    service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills/checks"
    if (root / "Scripts").exists():
        pytest.skip(
            "Filesystem cannot represent a legacy case-only directory collision"
        )
    (root / "Scripts").mkdir()
    (root / "Scripts/other.py").write_text("User file")
    before = (root / "SKILL.md").read_bytes()
    with pytest.raises(ValueError, match="Case-insensitive"):
        service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
    assert (root / "SKILL.md").read_bytes() == before
    assert (root / "Scripts/other.py").read_text() == "User file"


@pytest.mark.parametrize("kind", ["junction", "reparse"])
def test_junction_and_name_surrogate_reparse_points_are_rejected(service, recipe, kind):
    parent = service.project_root / ".agents"
    parent.mkdir()
    if kind == "junction":
        original = Path.is_junction
        with (
            patch.object(
                Path, "is_junction", lambda path: path == parent or original(path)
            ),
            pytest.raises(ValueError, match="junctions"),
        ):
            service.craft_skill(**recipe)
    else:
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == parent:
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                )
            return result

        with (
            patch.object(Path, "lstat", lstat),
            pytest.raises(ValueError, match="reparse points"),
        ):
            service.craft_skill(**recipe)
    assert list(parent.iterdir()) == []


def test_containment_rejected_before_outside_write(service, tmp_path):
    outside = tmp_path.parent / "outside-other-project"
    with pytest.raises(ValueError):
        check_project_path(service.project_root, outside)
    assert not outside.exists()


def test_windows_byte_lock_adapter_uses_nonblocking_modes(tmp_path):
    fake = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=Mock())
    fd = os.open(tmp_path / "adapter.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"\0")
        with (
            patch.object(module.os, "name", "nt"),
            patch.dict(sys.modules, {"msvcrt": fake}),
        ):
            module._try_file_lock(fd)
            module._try_file_lock(fd, unlock=True)
        assert fake.locking.call_args_list[0].args == (fd, 2, 1)
        assert fake.locking.call_args_list[1].args == (fd, 0, 1)
    finally:
        os.close(fd)


def test_file_lock_contention_is_bounded_and_releases_thread_lock(tmp_path):
    lock = ProjectSkillLock(tmp_path, timeout=0.01)
    with (
        patch.object(
            module,
            "_try_file_lock",
            side_effect=BlockingIOError(errno.EAGAIN, "locked"),
        ),
        pytest.raises(TimeoutError, match="Timed out"),
    ):
        lock.acquire(create=True)
    assert not lock.file_locked
    with lock:
        assert lock.file_locked


def test_thread_lock_timeout_is_bounded(tmp_path):
    lock = ProjectSkillLock(tmp_path, timeout=0.01)
    with lock, ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(lock.acquire)
        with pytest.raises(TimeoutError, match="Timed out"):
            waiting.result(timeout=1)


def test_activation_reader_blocks_until_portable_swap_completes(
    service, recipe, portable
):
    service.craft_skill(**recipe)
    publisher = service._publisher
    target = service.project_root / ".agents/skills/checks"
    gap = threading.Event()
    resume = threading.Event()
    reading = threading.Event()
    original = publisher._move_absent

    def move(source, destination):
        original(source, destination)
        if source == target:
            gap.set()
            assert resume.wait(3)

    def read():
        reading.set()
        return service.get_skill("checks")

    with (
        patch.object(publisher, "_move_absent", side_effect=move),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        publishing = executor.submit(
            service.craft_skill,
            **(recipe | {"instructions": "Changed"}),
            update_existing=True,
        )
        try:
            assert gap.wait(3)
            assert not target.exists()
            reader = executor.submit(read)
            assert reading.wait(1) and not reader.done()
        finally:
            resume.set()
        assert publishing.result(timeout=3)["operation"] == "update"
        assert reader.result(timeout=3)["body"] == "Changed"
