"""Reject unsafe descendants before traversal, payload reads or cleanup mutations."""

import ast
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from AgentCrew.modules.skills import publisher as module
from verification.skills import test_publisher as cases


@pytest.fixture
def service(tmp_path):
    return cases.service.__wrapped__(tmp_path)


@pytest.fixture
def recipe():
    return cases.recipe.__wrapped__()


def forbidden(path, boundary, outside):
    path = Path(path)
    return (
        path == boundary
        or path.is_relative_to(boundary)
        or path.is_relative_to(outside)
    )


@contextmanager
def guarded_tree(
    boundary, outside, kind, *, metadata_only=False, zero_cached_links=False
):
    scans, effects, reads, stat_flags = [], [], [], []
    real_scandir = os.scandir
    real_lstat = Path.lstat
    real_junction = Path.is_junction
    real_open = Path.open
    real_chmod = Path.chmod
    real_unlink = Path.unlink
    real_rmdir = Path.rmdir

    def reparse(info):
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_nlink=info.st_nlink,
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
        )

    class Child:
        def __init__(self, entry):
            self.entry = entry
            self.name = entry.name

        def stat(self, *, follow_symlinks=True):
            stat_flags.append(follow_symlinks)
            assert follow_symlinks is False
            info = self.entry.stat(follow_symlinks=False)
            if zero_cached_links:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_nlink=0,
                    st_file_attributes=getattr(info, "st_file_attributes", 0),
                )
            return (
                reparse(info)
                if kind == "reparse" and Path(self.entry.path) == boundary
                else info
            )

    @contextmanager
    def scandir(path):
        assert not forbidden(path, boundary, outside), (
            "Descended into an unchecked junction/reparse target"
        )
        scans.append(Path(path))
        with real_scandir(path) as children:
            yield [Child(child) for child in children]

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return (
            reparse(info)
            if kind == "reparse" and not metadata_only and path == boundary
            else info
        )

    def open_file(path, *args, **kwargs):
        assert not forbidden(path, boundary, outside), (
            "Read outside the validated skill tree"
        )
        reads.append(path)
        return real_open(path, *args, **kwargs)

    def mutation(operation):
        def apply(path, *args, **kwargs):
            assert not forbidden(path, boundary, outside), (
                "Mutated outside the validated skill tree"
            )
            effects.append(path)
            return operation(path, *args, **kwargs)

        return apply

    with (
        patch.object(module.os, "scandir", scandir),
        patch.object(Path, "lstat", lstat),
        patch.object(
            Path,
            "is_junction",
            lambda path: (
                (kind == "junction" and path == boundary) or real_junction(path)
            ),
        ),
        patch.object(Path, "open", open_file),
        patch.object(Path, "chmod", mutation(real_chmod)),
        patch.object(Path, "unlink", mutation(real_unlink)),
        patch.object(Path, "rmdir", mutation(real_rmdir)),
    ):
        yield scans, effects, reads, stat_flags


def make_tree(service, *, shallow=False):
    root = service.project_root / ".agents/skills/checks"
    root.mkdir(parents=True)
    (root / "keep.txt").write_bytes(b"Unrelated project resource")
    boundary = root / "junction" if shallow else root / "nested/junction"
    boundary.mkdir(parents=True)
    outside = service.project_root.parent / (service.project_root.name + "-outside")
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"Never read, chmod or delete this")
    return root, boundary, outside, sentinel


@pytest.mark.parametrize("kind", ["junction", "reparse"])
@pytest.mark.parametrize("operation", ["snapshot", "hash", "cleanup", "resources"])
def test_reject_before_descending_reading_or_mutating(service, kind, operation):
    root, boundary, outside, sentinel = make_tree(
        service, shallow=operation == "resources"
    )
    original_mode = sentinel.stat().st_mode
    with guarded_tree(boundary, outside, kind) as (scans, effects, reads, flags):
        with pytest.raises(ValueError, match="junctions|reparse"):
            if operation == "snapshot":
                module.skill_tree_snapshot(service.project_root, root)
            elif operation == "hash":
                service._tree_files(root)
            elif operation == "cleanup":
                service._publisher._remove_tree(root)
            else:
                service.list_resources(str(root))
        assert boundary not in scans
        assert effects == [] and reads == []
        assert all(flag is False for flag in flags)
    assert sentinel.read_bytes() == b"Never read, chmod or delete this"
    assert sentinel.stat().st_mode == original_mode
    assert (root / "keep.txt").exists()


def test_nonfollowing_direntry_metadata_rejects_reparse_before_path_resolution(service):
    root, boundary, outside, _ = make_tree(service)
    with guarded_tree(boundary, outside, "reparse", metadata_only=True) as (
        scans,
        effects,
        reads,
        flags,
    ):
        with pytest.raises(ValueError, match="reparse"):
            module.skill_tree_snapshot(service.project_root, root)
        assert boundary not in scans and flags
        assert reads == [] and effects == []


@pytest.mark.parametrize("kind", ["junction", "reparse"])
def test_directory_preservation_checks_new_junction_before_copying(
    service, recipe, kind
):
    service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills/checks"
    boundary = root / "nested/junction"
    boundary.parent.mkdir()
    outside = service.project_root.parent / (service.project_root.name + "-outside")
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"Outside user file")
    original = (root / "SKILL.md").read_bytes()
    create_stage = service._publisher.create_stage

    def stage(target):
        result = create_stage(target)
        boundary.mkdir()
        return result

    with (
        guarded_tree(boundary, outside, kind) as (scans, _, _, _),
        patch.object(service._publisher, "create_stage", side_effect=stage),
        pytest.raises(ValueError, match="junctions|reparse"),
    ):
        service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
    assert boundary not in scans
    assert sentinel.read_bytes() == b"Outside user file"
    assert (root / "SKILL.md").read_bytes() == original
    assert not list(root.parent.glob(".skill-checks-*.stage"))


@pytest.mark.parametrize("operation", ["hash", "cleanup"])
def test_real_directory_symlink_never_touches_external_sentinel(service, operation):
    root, boundary, outside, sentinel = make_tree(service)
    boundary.rmdir()
    boundary.symlink_to(outside, target_is_directory=True)
    original_mode = sentinel.stat().st_mode
    with guarded_tree(boundary, outside, "symlink") as (_, effects, reads, _):
        with pytest.raises(ValueError, match="Symlinks"):
            if operation == "hash":
                service._tree_files(root)
            else:
                service._publisher._remove_tree(root)
        assert effects == [] and reads == []
    assert sentinel.read_bytes() == b"Never read, chmod or delete this"
    assert sentinel.stat().st_mode == original_mode
    assert boundary.is_symlink()


def test_snapshot_order_empty_directories_and_hash_input_are_preserved(service):
    root = service.project_root / ".agents/skills/checks"
    expected = {
        "z/file.txt": b"last",
        "a.py": b"first",
        "scripts/check.py": b"print('check')",
    }
    for relative, data in expected.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "empty/nested").mkdir(parents=True)
    with patch.object(
        module, "inspect_skill_entry", wraps=module.inspect_skill_entry
    ) as inspect:
        entries = module.skill_tree_snapshot(service.project_root, root)
    first_level = [
        call.args[1].name
        for call in inspect.call_args_list
        if len(call.args) == 3 and call.args[1].parent == root
    ]
    assert first_level == sorted(first_level)
    names = [path.relative_to(root).as_posix() for path, _ in entries]
    assert names == sorted(names)
    assert {"empty", "empty/nested"}.issubset(names)
    assert service._tree_files(root) == expected


def test_cleanup_does_not_recurse_into_entries_added_after_snapshot(service):
    root = service.project_root / ".agents/skills/checks"
    root.mkdir(parents=True)
    (root / "known.txt").write_text("Known file")
    snapshot = module.skill_tree_snapshot

    def inject(project_root, target):
        result = snapshot(project_root, target)
        (target / "unvalidated.txt").write_text("Concurrent user edit")
        return result

    with (
        patch.object(module, "skill_tree_snapshot", side_effect=inject),
        pytest.raises(OSError),
    ):
        service._publisher._remove_tree(root)
    assert (root / "unvalidated.txt").read_text() == "Concurrent user edit"


@pytest.mark.parametrize("kind", ["hardlink", "fifo"])
@pytest.mark.parametrize("operation", ["hash", "cleanup"])
def test_nonregular_or_hardlinked_entries_are_rejected_before_touch(
    service, kind, operation
):
    root = service.project_root / ".agents/skills/checks"
    root.mkdir(parents=True)
    outside = service.project_root.parent / (service.project_root.name + "-outside")
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"External data")
    original_mode = sentinel.stat().st_mode
    entry = root / "unsafe"
    if kind == "hardlink":
        os.link(sentinel, entry)
    elif hasattr(os, "mkfifo"):
        os.mkfifo(entry)
    else:
        pytest.skip("FIFO creation is unavailable on this platform")
    with guarded_tree(root / "absent-junction", outside, kind) as (
        _,
        effects,
        reads,
        _,
    ):
        with pytest.raises(ValueError, match="regular|hardlinked"):
            if operation == "hash":
                service._tree_files(root)
            else:
                service._publisher._remove_tree(root)
        assert effects == [] and reads == []
    assert sentinel.read_bytes() == b"External data"
    assert sentinel.stat().st_mode == original_mode
    assert entry.exists()


@pytest.mark.parametrize("hardlinked", [False, True])
def test_windows_cached_zero_link_count_uses_fresh_nonfollowing_lstat(
    service, hardlinked
):
    root = service.project_root / ".agents/skills/checks"
    root.mkdir(parents=True)
    outside = service.project_root.parent / (service.project_root.name + "-outside")
    outside.mkdir()
    original = outside / "resource.txt"
    original.write_text("User data")
    resource = root / "resource.txt"
    if hardlinked:
        os.link(original, resource)
    else:
        resource.write_text("Project data")
    with guarded_tree(root / "absent", outside, "cache", zero_cached_links=True):
        if hardlinked:
            with pytest.raises(ValueError, match="hardlinked"):
                module.skill_tree_snapshot(service.project_root, root)
        else:
            entries = module.skill_tree_snapshot(service.project_root, root)
            assert len(entries) == 1 and entries[0][1].st_nlink == 1


def test_production_skill_modules_have_no_unsafe_recursive_walkers():
    root = Path(module.__file__).parent
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            assert node.func.attr not in {"rglob", "walk", "rmtree"}, source
            if node.func.attr == "glob":
                assert not any(
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and "**" in arg.value
                    for arg in node.args
                ), source
