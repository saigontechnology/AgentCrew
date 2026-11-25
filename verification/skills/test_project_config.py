"""Trusted, bounded project configuration and ordered project-root discovery."""

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from AgentCrew.modules.config.project_config import (
    MAX_PROJECT_CONFIG_BYTES,
    MAX_PROJECT_ROOTS,
    load_project_skill_roots,
)
from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills import service as service_module
from AgentCrew.modules.skills.publisher import (
    DirectorySkillPublisher,
    project_skill_lock,
)
from AgentCrew.modules.skills.tool import get_activate_skill_tool_handler
from verification.skills import test_publisher as publisher_cases
from verification.skills.test_discovery import write_skill


@pytest.fixture
def layout(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    (project / "services/api").mkdir(parents=True)
    write_skill(home, ".claude/skills/user", "user")
    return project, home


def write_config(project, value):
    path = project / ".agentcrew/config.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def configured_service(layout, *, trusted=True):
    project, home = layout
    return SkillsService(
        project_root=project,
        user_root=home,
        trusted_project=trusted,
        project_roots=load_project_skill_roots(project, trusted_project=trusted),
    )


@pytest.mark.parametrize("config", [None, {}, {"skills": {}}])
def test_missing_settings_preserve_current_root(layout, config):
    project, _ = layout
    if config is not None:
        write_config(project, config)
    direct = write_skill(project, ".claude/skills/direct", "direct")
    write_skill(project, "services/api/.claude/skills/nested", "nested")
    service = configured_service(layout)
    assert set(service.get_skill_names()) == {"direct", "user"}
    assert service.get_skill("direct")["location"] == str(direct)


@pytest.mark.parametrize("relative", service_module.SKILL_SCAN_DIRS)
def test_selected_subproject_only_scans_standard_roots(layout, relative):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": ["services/api"]}})
    selected = write_skill(
        project / "services/api", relative + "/group/leaf", "selected"
    )
    write_skill(project, relative + "/launch", "launch")
    write_skill(
        project, "services/api/packages/other/" + relative + "/leaf", "arbitrary"
    )
    service = configured_service(layout)
    assert set(service.get_skill_names()) == {"user", "selected"}
    assert service.get_skill("selected")["location"] == str(selected)
    assert service._publisher.project_root == project


@pytest.mark.parametrize("roots", [[".", "services/api"], ["services/api", "."]])
def test_list_precedence_then_standard_root_precedence_then_user(layout, roots):
    project, home = layout
    write_config(project, {"skills": {"project_roots": roots}})
    write_skill(home, ".claude/skills/collision", "collision")
    paths = {}
    for root in [".", "services/api"]:
        paths[root] = write_skill(project / root, ".claude/skills/first", "collision")
        write_skill(project / root, ".codex/skills/later", "collision")
    skill = configured_service(layout).get_skill("collision")
    assert skill["location"] == str(paths[roots[0]])
    assert skill["scope"] == "project"


def test_empty_list_keeps_users_and_does_not_touch_project_publication(layout):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": []}})
    write_skill(project, ".claude/skills/launch", "launch")
    (project / ".agents").symlink_to(project / "services/api", target_is_directory=True)
    with patch.object(
        DirectorySkillPublisher, "recover", side_effect=AssertionError("recovery")
    ):
        assert configured_service(layout).get_skill_names() == ["user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("roots", [[], ["../outside"]])
async def test_disabled_project_preserves_user_activation(layout, roots):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": roots}})
    (project / ".agents").symlink_to(project / "services/api", target_is_directory=True)
    with patch.object(
        DirectorySkillPublisher,
        "recover",
        side_effect=AssertionError("excluded project recovery"),
    ):
        service = configured_service(layout)
        output = await get_activate_skill_tool_handler(service)(name="user")
    assert 'scope="user"' in output
    assert "Nested instructions" in output


def test_additional_root_resource_listing_uses_its_own_lock(layout):
    project, _ = layout
    root = project / "services/api"
    write_config(project, {"skills": {"project_roots": ["services/api"]}})
    skill = write_skill(root, ".agents/skills/leaf", "selected")
    resource = skill.parent / "reference.txt"
    resource.write_text("Lazy resource")
    service = configured_service(layout)
    original = service._list_resources

    def listing(path):
        assert project_skill_lock(root).file_locked
        assert not service._publisher.lock.file_locked
        return original(path)

    with patch.object(service, "_list_resources", listing):
        assert service.list_resources(str(skill.parent)) == ["reference.txt"]
    assert not project_skill_lock(root).file_locked


def test_untrusted_ignores_config_without_reading_it(layout):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": ["services/api"]}})
    write_skill(project, "services/api/.agents/skills/leaf", "project")
    with patch(
        "AgentCrew.modules.skills.publisher.inspect_skill_entry",
        side_effect=AssertionError("project read"),
    ):
        assert configured_service(layout, trusted=False).get_skill_names() == ["user"]


@pytest.mark.parametrize(
    "config",
    [
        [],
        None,
        "invalid",
        {"skills": None},
        {"skills": []},
        {"skills": {"project_roots": None}},
        {"skills": {"project_roots": "."}},
        {"skills": {"project_roots": [".", 1]}},
        {"skills": {"project_roots": ["."] * (MAX_PROJECT_ROOTS + 1)}},
    ],
)
def test_invalid_schema_fails_closed_without_disabling_users(layout, config):
    project, _ = layout
    write_config(project, config)
    write_skill(project, ".claude/skills/launch", "launch")
    assert configured_service(layout).get_skill_names() == ["user"]


@pytest.mark.parametrize(
    "raw", [b"{", b"\xff", b" " * (MAX_PROJECT_CONFIG_BYTES + 1), b"[" * 2000]
)
def test_malformed_or_oversized_config_fails_closed(layout, raw):
    project, _ = layout
    path = write_config(project, {})
    path.write_bytes(raw)
    assert configured_service(layout).get_skill_names() == ["user"]


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "..",
        "../outside",
        "services/../services/api",
        "/tmp",
        "C:/outside",
        "C:outside",
        "\\\\host\\share",
        "services\\api",
        "bad\x00path",
    ],
)
def test_invalid_root_disables_entire_project_list(layout, entry):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": [".", entry]}})
    write_skill(project, ".claude/skills/launch", "launch")
    assert configured_service(layout).get_skill_names() == ["user"]


def test_duplicates_keep_first_and_missing_directories_are_skipped(layout):
    project, _ = layout
    write_config(
        project,
        {
            "skills": {
                "project_roots": ["services/api", "services/./api", ".", "missing", "."]
            }
        },
    )
    assert load_project_skill_roots(project, trusted_project=True) == [
        project / "services/api",
        project,
    ]
    write_config(project, {"skills": {"project_roots": ["missing"]}})
    write_skill(project, ".claude/skills/launch", "launch")
    assert configured_service(layout).get_skill_names() == ["user"]


@pytest.mark.parametrize(
    "kind", ["symlink", "directory", "hardlink", "fifo", "parent-link"]
)
def test_unsafe_config_files_fail_closed_before_payload_read(layout, kind):
    project, _ = layout
    path = write_config(project, {})
    content = path.read_bytes()
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(path)
    else:
        target = project / "elsewhere.json"
        target.write_bytes(content)
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        else:
            path.parent.rmdir()
            actual = project / "actual"
            actual.mkdir()
            (actual / "config.json").write_bytes(content)
            path.parent.symlink_to(actual, target_is_directory=True)
    with patch(
        "AgentCrew.modules.config.project_config.os.open",
        side_effect=AssertionError("payload open"),
    ):
        assert load_project_skill_roots(project, trusted_project=True) == []
    assert configured_service(layout).get_skill_names() == ["user"]


@pytest.mark.parametrize("target", ["services/api", "missing"])
def test_linked_roots_fail_closed_even_when_target_missing(layout, target):
    project, _ = layout
    (project / "alias").symlink_to(project / target, target_is_directory=True)
    write_config(project, {"skills": {"project_roots": [".", "alias"]}})
    assert configured_service(layout).get_skill_names() == ["user"]


@pytest.mark.parametrize(
    "boundary", ["services", ".agentcrew/config.json", ".agentcrew"]
)
@pytest.mark.parametrize("kind", ["reparse", "junction"])
def test_reparse_and_junction_components_rejected(layout, boundary, kind):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": ["services/api"]}})
    real_lstat = Path.lstat
    real_junction = Path.is_junction

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if kind == "reparse" and path == project / boundary:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    def junction(path):
        return (kind == "junction" and path == project / boundary) or real_junction(
            path
        )

    with (
        patch.object(Path, "lstat", lstat),
        patch.object(Path, "is_junction", junction),
    ):
        assert configured_service(layout).get_skill_names() == ["user"]


def test_direct_service_is_backward_compatible_and_revalidates_roots(layout):
    project, home = layout
    write_skill(project, ".claude/skills/launch", "launch")
    assert set(
        SkillsService(
            project_root=project, user_root=home, trusted_project=True
        ).get_skill_names()
    ) == {"launch", "user"}
    for roots in [[project.parent], ["../outside"], [project, 2], "services/api"]:
        service = SkillsService(
            project_root=project,
            user_root=home,
            trusted_project=True,
            project_roots=roots,
        )
        assert service.get_skill_names() == ["user"]
    root = project / "services/api"
    write_skill(root, ".claude/skills/leaf", "selected")
    service = SkillsService(
        project_root=project, user_root=home, trusted_project=True, project_roots=[root]
    )
    assert set(service.get_skill_names()) == {"user", "selected"}
    root.rename(project / "original")
    root.symlink_to(project / "original", target_is_directory=True)
    assert service.get_skill_names() == ["user"]


def test_additional_agents_roots_are_locked_recovered_and_hide_artifacts(layout):
    project, _ = layout
    other = project / "services/worker"
    other.mkdir()
    roots = [project / "services/api", other]
    for index, root in enumerate(roots):
        write_skill(root, ".agents/skills/leaf", f"selected-{index}")
        write_skill(root, ".agents/skills/.hidden/leaf", f"hidden-{index}")
    write_config(
        project, {"skills": {"project_roots": ["services/api", "services/worker"]}}
    )
    recovered = []
    scanned = []
    recover = DirectorySkillPublisher.recover
    discover = SkillsService._discovery_paths

    def recovery(publisher):
        assert publisher.project_root in roots
        assert publisher.lock.file_locked
        assert all(
            not project_skill_lock(root).file_locked
            for root in roots
            if root != publisher.project_root
        )
        recovered.append(publisher.project_root)
        return recover(publisher)

    def discovery(service, base, scope, budget):
        if (
            scope == "project"
            and base.name == "skills"
            and base.parent.name == ".agents"
        ):
            root = base.parent.parent
            assert project_skill_lock(root).file_locked
            assert root in recovered
            scanned.append(root)
        yield from discover(service, base, scope, budget)

    with (
        patch.object(DirectorySkillPublisher, "recover", recovery),
        patch.object(SkillsService, "_discovery_paths", discovery),
    ):
        service = configured_service(layout)
        assert set(service.get_skill_names()) == {"user", "selected-0", "selected-1"}
    assert scanned == recovered
    assert recovered[:2] == roots
    assert all(not project_skill_lock(root).file_locked for root in roots)
    assert not (project / ".agents").exists()


def test_additional_root_recovers_real_interrupted_publication(layout, monkeypatch):
    project, home = layout
    root = project / "services/api"
    child = SkillsService(project_root=root, user_root=home, trusted_project=True)
    publisher_cases.portable.__wrapped__(monkeypatch)
    recipe = publisher_cases.recipe.__wrapped__()
    child.craft_skill(**recipe)
    publisher_cases.interrupt_at_phase(
        child, recipe | {"instructions": "Updated instructions"}, "old_moved"
    )
    assert not (root / ".agents/skills/checks").exists()
    write_config(project, {"skills": {"project_roots": ["services/api"]}})
    service = configured_service(layout)
    assert service.get_skill("checks")["body"] == "Original instructions"
    assert not list((root / ".agents/skills").glob(".skill-*.journal.json"))
    assert not project_skill_lock(root).file_locked


@pytest.mark.parametrize("roots", [[], ["services/api"]])
def test_authoring_still_targets_launch_root_when_excluded(layout, roots):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": roots}})
    service = configured_service(layout)
    result = service.craft_skill(**publisher_cases.recipe.__wrapped__())
    target = project / ".agents/skills/checks/SKILL.md"
    assert str(target) in result["paths"]
    assert target.is_file()
    assert service.get_skill_names() == ["user"]
    assert not (project / "services/api/.agents").exists()


@pytest.mark.parametrize("roots", [[], ["services/api"]])
def test_excluded_launch_root_writers_use_consistent_lock_order(layout, roots):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": roots}})
    service = configured_service(layout)
    recipe = publisher_cases.recipe.__wrapped__()
    publishing = threading.Event()
    waiting = threading.Event()
    first_thread = []
    acquire = service._publisher.lock.acquire
    publish = service._publish_skill

    def locking(*, create=False):
        if publishing.is_set() and threading.get_ident() != first_thread[0]:
            waiting.set()
        return acquire(create=create)

    def publication(name, *args):
        if name == "first":
            first_thread.append(threading.get_ident())
            publishing.set()
            assert waiting.wait(3)
        return publish(name, *args)

    with (
        patch.object(service._publisher.lock, "acquire", locking),
        patch.object(service, "_publish_skill", publication),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(service.craft_skill, **(recipe | {"name": "first"}))
        assert publishing.wait(3)
        second = executor.submit(service.craft_skill, **(recipe | {"name": "second"}))
        assert first.result(timeout=10)["operation"] == "create"
        assert second.result(timeout=10)["operation"] == "create"
    assert (project / ".agents/skills/first/SKILL.md").is_file()
    assert (project / ".agents/skills/second/SKILL.md").is_file()
    assert service.get_skill_names() == ["user"]


def test_config_limits_are_inclusive(layout):
    project, _ = layout
    path = write_config(
        project, {"skills": {"project_roots": ["."] * MAX_PROJECT_ROOTS}}
    )
    path.write_bytes(path.read_bytes().ljust(MAX_PROJECT_CONFIG_BYTES))
    assert load_project_skill_roots(project, trusted_project=True) == [project]


def test_absolute_in_project_config_path_and_nondirectory_roots_are_rejected(layout):
    project, _ = layout
    file = project / "plain-file"
    file.write_text("not a directory")
    for entry in [str(project / "services/api"), "plain-file"]:
        write_config(project, {"skills": {"project_roots": [".", entry]}})
        assert configured_service(layout).get_skill_names() == ["user"]


def test_config_changes_require_restart(layout):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": ["services/api"]}})
    write_skill(project / "services/api", ".claude/skills/leaf", "selected")
    service = configured_service(layout)
    write_config(project, {"skills": {"project_roots": []}})
    assert set(service.get_skill_names()) == {"user", "selected"}
    assert configured_service(layout).get_skill_names() == ["user"]


def test_shared_budget_across_user_and_all_selected_projects(layout):
    project, _ = layout
    write_config(project, {"skills": {"project_roots": [".", "services/api"]}})
    write_skill(project, ".claude/skills/launch", "launch")
    write_skill(project / "services/api", ".claude/skills/leaf", "selected")
    with patch.object(service_module, "MAX_DISCOVERY_DIRS_TOTAL", 2):
        assert set(configured_service(layout).get_skill_names()) == {"user", "launch"}
