"""Authoring edge cases found during implementation review."""

import sys

import pytest

from AgentCrew.modules.skills import SkillsService
from verification.skills.test_skills import write_skill


@pytest.fixture
def setup(tmp_path):
    service = SkillsService(
        project_root=tmp_path, user_root=tmp_path / "home", trusted_project=True
    )
    recipe = {
        "name": "checks",
        "description": "Repeatable project checks",
        "instructions": "Run scripts/check.py through the command tool.",
        "scripts": [
            {
                "path": "scripts/check.py",
                "language": "python",
                "content": "print('check')\n",
            }
        ],
    }
    return service, recipe


@pytest.mark.parametrize("content", ["return 1", "break", "await task()"])
def test_compile_rejects_invalid_script_without_execution(setup, content):
    service, recipe = setup
    recipe["scripts"][0]["content"] = content
    with pytest.raises(ValueError, match="Static Python syntax"):
        service.craft_skill(**recipe)
    assert not (service.project_root / ".agents").exists()


def test_second_script_failure_is_all_or_nothing(setup):
    service, recipe = setup
    recipe["scripts"].append(
        {"path": "scripts/bad.py", "language": "python", "content": "def invalid("}
    )
    with pytest.raises(ValueError, match="Static Python syntax"):
        service.craft_skill(**recipe)
    assert not (service.project_root / ".agents").exists()


def test_compatibility_root_collision_is_not_silently_shadowed(setup):
    service, recipe = setup
    path = write_skill(
        service.project_root, "checks", body="Original", rel=".claude/skills"
    )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="another project skill root"):
        service.craft_skill(**recipe)
    assert path.read_bytes() == before
    assert not (service.project_root / ".agents").exists()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux atomic publication"
)
def test_update_preserves_read_only_directory_modes_and_cleans_stage(setup):
    service, recipe = setup
    service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills/checks"
    scripts = root / "scripts"
    root.chmod(0o555)
    scripts.chmod(0o555)
    recipe["instructions"] = "Updated instructions"
    try:
        result = service.craft_skill(**recipe, update_existing=True)
        assert result["operation"] == "update" and not result["warnings"]
        assert root.stat().st_mode & 0o777 == 0o555
        assert scripts.stat().st_mode & 0o777 == 0o555
        assert not list(root.parent.glob(".skill-checks-*"))
    finally:
        root.chmod(0o755)
        scripts.chmod(0o755)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux atomic publication"
)
def test_merged_limit_is_validated_before_publication(setup):
    service, recipe = setup
    service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills/checks"
    original = (root / "SKILL.md").read_bytes()
    (root / "resource.txt").write_bytes(b"x" * 900000)
    recipe["scripts"][0]["content"] = "#" + "x" * 150000
    with pytest.raises(ValueError, match="Merged skill tree"):
        service.craft_skill(**recipe, update_existing=True)
    assert (root / "SKILL.md").read_bytes() == original
    assert not list(root.parent.glob(".skill-checks-*"))


def test_legacy_user_name_fallback_and_truncation_remain_supported(setup):
    service, _ = setup
    legacy = service.user_root / ".claude/skills/installed/SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "---\nname: Legacy_Name\ndescription: Existing user skill\n---\nbody"
    )
    service.refresh()
    assert service.get_skill("Legacy_Name")["scope"] == "user"
    legacy.write_text(
        "---\nname: " + "x" * 65 + "\ndescription: Existing user skill\n---\nbody"
    )
    service.refresh()
    assert service.get_skill("x" * 64)
    legacy.write_text("---\ndescription: Existing user skill\n---\nbody")
    service.refresh()
    assert service.get_skill("installed")
    write_skill(service.user_root, ".hidden", body="Legacy hidden user skill")
    service.refresh()
    assert service.get_skill(".hidden")["scope"] == "user"
