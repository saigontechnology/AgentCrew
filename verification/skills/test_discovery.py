"""Bounded exact-root discovery, link policy and authoring boundary checks."""

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from loguru import logger

from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills import service as module
from AgentCrew.modules.skills.tool import get_activate_skill_tool_handler


@pytest.fixture
def service(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return SkillsService(project_root=project, user_root=home, trusted_project=True)


@pytest.fixture
def diagnostics():
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    try:
        yield messages
    finally:
        logger.remove(sink)


def write_skill(root, relative, name="checks", *, filename="SKILL.md"):
    path = root / relative / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Reusable checks\n"
        "metadata:\n  version: '2.0'\n---\nNested instructions\n"
    )
    return path


@pytest.mark.parametrize("relative", module.SKILL_SCAN_DIRS)
@pytest.mark.parametrize("scope", ["user", "project"])
def test_nested_discovery_all_exact_roots(service, relative, scope):
    root = service.user_root if scope == "user" else service.project_root
    path = write_skill(root, relative + "/backend/api-review")
    skill = service.get_skill("checks")
    assert skill["location"] == str(path)
    assert skill["scope"] == scope
    assert skill["trusted"] is True


def test_untrusted_project_and_arbitrary_repository_roots_ignored(service):
    for index, relative in enumerate(module.SKILL_SCAN_DIRS):
        write_skill(service.project_root, relative + "/group/leaf", f"project-{index}")
        write_skill(
            service.project_root,
            "packages/app/" + relative + "/leaf",
            f"package-{index}",
        )
    write_skill(service.project_root, ".agent/skills/leaf", "singular")
    write_skill(service.user_root, ".agents/skills/group/leaf", "user")
    service.trusted_project = False
    assert service.get_skill_names() == ["user"]
    service.trusted_project = True
    assert set(service.get_skill_names()) == {"user"} | {
        f"project-{index}" for index in range(4)
    }


@pytest.mark.parametrize("scope", ["user", "project"])
def test_shallow_then_lexical_order_and_collision_diagnostics(
    service, scope, diagnostics
):
    root = service.user_root if scope == "user" else service.project_root
    base = ".claude/skills/"
    write_skill(root, base + "a-group/nested", "direct-wins")
    direct = write_skill(root, base + "z-direct", "direct-wins")
    write_skill(root, base + "z-group/aaa", "lexical-wins")
    lexical = write_skill(root, base + "a-group/zzz", "lexical-wins")
    service.refresh()
    assert service._skills["direct-wins"]["location"] == str(direct)
    assert service._skills["lexical-wins"]["location"] == str(lexical)
    output = "\n".join(diagnostics)
    assert "shadowed name 'direct-wins'" in output
    assert f"winner={direct}" in output
    assert f"loser={root / base / 'a-group/nested/SKILL.md'}" in output


def test_normalized_posix_order_has_stable_raw_path_tiebreaker(service):
    base = ".claude/skills/"
    write_skill(service.user_root, base + "é/leaf", "unicode")
    first = write_skill(service.user_root, base + "e\u0301/leaf", "unicode")
    service.refresh()
    assert service._skills["unicode"]["location"] == str(first)


def test_root_order_then_project_over_user_unchanged(service, diagnostics):
    for root in (service.user_root, service.project_root):
        for relative in reversed(module.SKILL_SCAN_DIRS):
            write_skill(root, relative + "/group/leaf", "same")
    winner = service.project_root / ".claude/skills/group/leaf/SKILL.md"
    assert service.get_skill("same")["location"] == str(winner)
    assert any(
        "shadowed name 'same'" in message
        and f"winner={winner}" in message
        and str(service.user_root) in message
        for message in diagnostics
    )
    service.trusted_project = False
    assert service.get_skill("same")["location"] == str(
        service.user_root / ".claude/skills/group/leaf/SKILL.md"
    )


def test_first_root_nested_beats_later_root_direct(service):
    first = write_skill(service.user_root, ".claude/skills/group/leaf")
    write_skill(service.user_root, ".agents/skills/direct")
    assert service.get_skill("checks")["location"] == str(first)


@pytest.mark.parametrize("scope", ["user", "project"])
@pytest.mark.parametrize("malformed", [False, True])
def test_bundle_boundary_never_descends_even_when_malformed(service, scope, malformed):
    root = service.user_root if scope == "user" else service.project_root
    bundle = write_skill(root, ".claude/skills/bundle", "outer")
    if malformed:
        bundle.write_text("---\nname: [broken\n---\n")
    write_skill(root, ".claude/skills/bundle/resources/inner", "inner")
    with patch.object(module.os, "scandir", wraps=os.scandir) as scan:
        service.refresh()
    assert "inner" not in service._skills
    assert ("outer" in service._skills) is not malformed
    assert all(
        not Path(call.args[0]).is_relative_to(bundle.parent / "resources")
        for call in scan.call_args_list
    )


def test_exact_skill_filename_and_nonregular_boundary(service):
    write_skill(
        service.user_root, ".agents/skills/lowercase", "lower", filename="skill.md"
    )
    write_skill(
        service.user_root, ".agents/skills/uppercase", "upper", filename="SKILL.MD"
    )
    write_skill(service.user_root, ".agents/skills/exact", "exact")
    marker = service.user_root / ".agents/skills/not-a-file/SKILL.md"
    marker.mkdir(parents=True)
    write_skill(service.user_root, ".agents/skills/not-a-file/child", "boundary-child")
    assert service.get_skill_names() == ["exact"]


def test_case_insensitive_lookup_does_not_accept_lowercase_marker(service):
    lower = write_skill(
        service.user_root, ".claude/skills/lowercase", "lower", filename="skill.md"
    )
    original = Path.lstat

    def case_insensitive_lstat(path, *args, **kwargs):
        if path == lower.parent / "SKILL.md":
            path = lower
        return original(path, *args, **kwargs)

    with patch.object(Path, "lstat", case_insensitive_lstat):
        assert service.get_skill_names() == []


def test_default_depth_limit_and_diagnostic(service, diagnostics):
    assert module.MAX_DISCOVERY_DEPTH == 4
    assert module.MAX_DISCOVERY_DIRS_PER_ROOT == 2000
    assert module.MAX_DISCOVERY_DIRS_TOTAL == 16000
    write_skill(service.user_root, ".agents/skills/a/b/c/leaf", "depth-four")
    write_skill(service.user_root, ".agents/skills/a/b/c/d/leaf", "depth-five")
    assert service.get_skill_names() == ["depth-four"]
    assert any(
        "depth truncation" in message
        and str(service.user_root / ".agents/skills") in message
        and "depth limit=4" in message
        for message in diagnostics
    )


def test_directory_budget_shallow_first_deterministic(
    service, monkeypatch, diagnostics
):
    monkeypatch.setattr(module, "MAX_DISCOVERY_DIRS_PER_ROOT", 3)
    write_skill(service.user_root, ".claude/skills/z-direct", "direct")
    write_skill(service.user_root, ".claude/skills/a-group/z-nested", "nested-z")
    first = write_skill(
        service.user_root, ".claude/skills/a-group/a-nested", "nested-a"
    )
    service.refresh()
    assert set(service._skills) == {"direct", "nested-a"}
    assert service._skills["nested-a"]["location"] == str(first)
    snapshot = dict(service._skills)
    service.refresh()
    assert service._skills == snapshot
    assert any("directory budget truncation" in message for message in diagnostics)


def test_actual_per_root_limit_never_visits_overflow_directory(service, diagnostics):
    base = service.user_root / ".claude/skills"
    base.mkdir(parents=True)
    for index in range(module.MAX_DISCOVERY_DIRS_PER_ROOT):
        (base / f"group-{index:04}").mkdir()
    overflow = write_skill(service.user_root, ".claude/skills/z-overflow", "overflow")
    with patch.object(module.os, "scandir", wraps=os.scandir) as scans:
        assert service.get_skill_names() == []
    assert all(Path(call.args[0]) != overflow.parent for call in scans.call_args_list)
    assert any("directory budget truncation" in message for message in diagnostics)


def test_budget_exhausted_at_next_level_and_aggregate_across_scopes(
    service, monkeypatch, diagnostics
):
    monkeypatch.setattr(module, "MAX_DISCOVERY_DIRS_TOTAL", 3)
    write_skill(service.user_root, ".claude/skills/a", "first-root")
    write_skill(service.user_root, ".agents/skills/a", "second-root")
    write_skill(service.project_root, ".claude/skills/a", "project")
    write_skill(service.project_root, ".claude/skills/z-group/leaf", "over-budget")
    assert set(service.get_skill_names()) == {"first-root", "second-root", "project"}
    assert any(
        "directory budget truncation" in message
        and str(service.project_root / ".claude/skills") in message
        and "total limit=3" in message
        for message in diagnostics
    )


def test_exhausted_root_budget_does_not_block_other_roots(
    service, monkeypatch, diagnostics
):
    monkeypatch.setattr(module, "MAX_DISCOVERY_DIRS_PER_ROOT", 1)
    write_skill(service.user_root, ".claude/skills/group/leaf", "nested")
    write_skill(service.user_root, ".agents/skills/direct", "direct")
    assert service.get_skill_names() == ["direct"]
    assert any("directory budget truncation" in message for message in diagnostics)


@pytest.mark.parametrize("scope", ["user", "project"])
@pytest.mark.parametrize("relative", module.SKILL_SCAN_DIRS)
def test_skip_and_hidden_directory_policy(service, scope, relative):
    root = service.user_root if scope == "user" else service.project_root
    write_skill(root, relative + "/.hidden-skill", "hidden")
    write_skill(root, relative + "/.hidden-group/leaf", "hidden-group")
    write_skill(root, relative + "/group/.hidden-group/leaf", "nested-hidden-group")
    for index, skipped in enumerate(sorted(module._SKIP_DIRS)):
        write_skill(root, relative + f"/{skipped}/leaf", f"skipped-{index}")
        write_skill(
            root, relative + f"/group/{skipped}/leaf", f"nested-skipped-{index}"
        )
    expected = [] if scope == "project" and relative == ".agents/skills" else ["hidden"]
    assert service.get_skill_names() == expected


@pytest.mark.parametrize("relative", module.SKILL_SCAN_DIRS)
def test_project_grouping_and_leaf_links_never_followed(service, tmp_path, relative):
    outside = tmp_path / "outside"
    write_skill(outside, "bundle", "outside-leaf")
    write_skill(outside, "container/nested", "outside-nested")
    base = service.project_root / relative
    base.mkdir(parents=True)
    (base / "linked-leaf").symlink_to(outside / "bundle", target_is_directory=True)
    (base / "linked-group").symlink_to(outside / "container", target_is_directory=True)
    write_skill(service.project_root, relative + "/safe/leaf", "safe")
    original = os.scandir

    def guarded(path):
        assert path not in {base / "linked-leaf", base / "linked-group"}
        assert not path.is_relative_to(outside)
        return original(path)

    with patch.object(module.os, "scandir", guarded):
        assert service.get_skill_names() == ["safe"]


@pytest.mark.parametrize("kind", ["junction", "reparse"])
def test_project_non_symlink_link_flags_rejected_before_descent(service, kind):
    boundary = service.project_root / ".claude/skills/group"
    write_skill(service.project_root, ".claude/skills/group/leaf", "unsafe")
    write_skill(service.project_root, ".claude/skills/safe", "safe")
    original_lstat, original_scan = Path.lstat, os.scandir
    original_junction = Path.is_junction

    def lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if kind == "reparse" and path == boundary:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    def scan(path):
        assert not path.is_relative_to(boundary)
        return original_scan(path)

    with (
        patch.object(Path, "lstat", lstat),
        patch.object(module.os, "scandir", scan),
        patch.object(
            Path,
            "is_junction",
            lambda path: (
                (kind == "junction" and path == boundary) or original_junction(path)
            ),
        ),
    ):
        assert service.get_skill_names() == ["safe"]


def test_project_nonregular_and_hardlinked_skill_files_are_skipped(service, tmp_path):
    path = write_skill(service.project_root, ".claude/skills/hardlinked", "unsafe")
    os.link(path, tmp_path / "hardlink")
    fifo = service.project_root / ".claude/skills/fifo/SKILL.md"
    fifo.parent.mkdir()
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)
    write_skill(service.project_root, ".claude/skills/safe", "safe")
    assert service.get_skill_names() == ["safe"]


def test_user_direct_leaf_link_but_no_group_or_nested_links(service, tmp_path):
    outside = tmp_path / "outside"
    write_skill(outside, "bundle", "linked-leaf")
    write_skill(outside, "container/nested", "linked-group")
    base = service.user_root / ".claude/skills"
    base.mkdir(parents=True)
    (base / "leaf").symlink_to(outside / "bundle", target_is_directory=True)
    (base / "group").symlink_to(outside / "container", target_is_directory=True)
    (base / "normal").mkdir()
    (base / "normal/leaf").symlink_to(outside / "bundle", target_is_directory=True)
    (base / "normal/group").symlink_to(outside / "container", target_is_directory=True)
    original = os.scandir

    def scan(path):
        assert Path(path) not in {
            base / "group",
            base / "normal/group",
            base / "normal/leaf",
        }
        assert not Path(path).is_relative_to(outside)
        return original(path)

    with patch.object(module.os, "scandir", scan):
        assert service.get_skill_names() == ["linked-leaf"]
        assert service.get_skill("linked-leaf")["location"] == str(
            base / "leaf/SKILL.md"
        )


@pytest.mark.parametrize("scope", ["user", "project"])
@pytest.mark.parametrize("nested", [False, True])
def test_marker_file_links_only_supported_for_direct_user_skills(
    service, tmp_path, scope, nested
):
    external = write_skill(tmp_path, "external", "linked-marker")
    root = service.user_root if scope == "user" else service.project_root
    relative = ".claude/skills/group/leaf" if nested else ".claude/skills/leaf"
    marker = root / relative / "SKILL.md"
    marker.parent.mkdir(parents=True)
    marker.symlink_to(external)
    expected = ["linked-marker"] if scope == "user" and not nested else []
    assert service.get_skill_names() == expected


def test_physical_dedup_separate_from_declared_name_collisions(service, diagnostics):
    path = write_skill(service.user_root, ".claude/skills/z-real", "unused")
    path.write_text("---\ndescription: Legacy fallback name\n---\nBody\n")
    (path.parent.parent / "a-alias").symlink_to(path.parent, target_is_directory=True)
    write_skill(service.user_root, ".agents/skills/other", "a-alias")
    assert service.get_skill_names() == ["a-alias"]
    output = "\n".join(diagnostics)
    assert "duplicate physical target" in output
    assert str(path) in output
    assert "shadowed name 'a-alias'" in output


def test_same_physical_root_still_allows_project_scope_override(tmp_path):
    path = write_skill(tmp_path, ".claude/skills/group/leaf")
    service = SkillsService(
        project_root=tmp_path, user_root=tmp_path, trusted_project=True
    )
    assert service.get_skill("checks")["scope"] == "project"
    assert service.get_skill("checks")["location"] == str(path)


def test_unreadable_and_malformed_candidates_do_not_abort(service):
    write_skill(service.user_root, ".claude/skills/unreadable/leaf", "unreadable")
    malformed = write_skill(service.user_root, ".claude/skills/malformed", "malformed")
    malformed.write_bytes(b"\xff\xfe")
    write_skill(service.user_root, ".claude/skills/good/leaf", "good")
    original = os.scandir

    def scan(path):
        if Path(path).name == "unreadable":
            raise PermissionError("isolated unreadable candidate")
        return original(path)

    with patch.object(module.os, "scandir", scan):
        assert service.get_skill_names() == ["good"]


@pytest.mark.parametrize("update", [False, True])
def test_craft_grouping_container_rejected_without_tree_changes(service, update):
    nested = write_skill(
        service.project_root, ".agents/skills/backend/api-review", "api-review"
    )
    before = {
        str(path): path.read_bytes()
        for path in nested.parent.parent.rglob("*")
        if path.is_file()
    }
    with pytest.raises(ValueError, match="grouping container"):
        service.craft_skill(
            name="backend",
            description="Backend checks",
            instructions="Run the reusable checks",
            scripts=[
                {
                    "path": "scripts/check.py",
                    "language": "python",
                    "content": "print('check')\n",
                }
            ],
            update_existing=update,
        )
    after = {
        str(path): path.read_bytes()
        for path in nested.parent.parent.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (nested.parent.parent / "SKILL.md").exists()
    assert not list(nested.parent.parent.parent.glob(".skill-backend-*"))


def test_crafting_into_empty_direct_target_is_unchanged(service):
    target = service.project_root / ".agents/skills/checks"
    target.mkdir(parents=True)
    result = service.craft_skill(
        name="checks",
        description="Checks",
        instructions="Run checks",
        scripts=[
            {
                "path": "scripts/check.py",
                "language": "python",
                "content": "print('check')\n",
            }
        ],
        update_existing=True,
    )
    assert result["operation"] == "update"
    assert (target / "SKILL.md").is_file()


def test_nested_declared_name_cannot_be_overwritten_by_direct_crafting(service):
    nested = write_skill(service.project_root, ".agents/skills/group/leaf", "checks")
    before = nested.read_bytes()
    with pytest.raises(ValueError, match="another project skill root"):
        service.craft_skill(
            name="checks",
            description="Checks",
            instructions="Run checks",
            scripts=[
                {
                    "path": "scripts/check.py",
                    "language": "python",
                    "content": "print('check')\n",
                }
            ],
            update_existing=True,
        )
    assert nested.read_bytes() == before
    assert not (service.project_root / ".agents/skills/checks").exists()


@pytest.mark.asyncio
async def test_nested_activation_provenance_resources_and_no_execution(
    service, tmp_path
):
    path = write_skill(
        service.project_root, ".agents/skills/backend/api-review", "api-review"
    )
    sentinel = tmp_path / "executed"
    scripts = path.parent / "scripts"
    scripts.mkdir()
    (scripts / "check.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    )
    result = await get_activate_skill_tool_handler(service)(name="api-review")
    assert 'scope="project"' in result and 'trusted="true"' in result
    assert 'version="2.0"' in result and 'content_hash="' in result
    assert "Nested instructions" in result
    assert f"Skill directory: {path.parent}" in result
    assert "<file>scripts/check.py</file>" in result
    assert not sentinel.exists()
