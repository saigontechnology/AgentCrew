"""Recovery ledger validation and interrupted recovery/verification edge cases."""

import errno
import json
import os
import shutil
from unittest.mock import patch

import pytest

from AgentCrew.modules.skills import publisher as module
from AgentCrew.modules.skills.publisher import SkillRecoveryError
from verification.skills import test_publisher as cases


@pytest.fixture
def service(tmp_path):
    return cases.service.__wrapped__(tmp_path)


@pytest.fixture
def recipe():
    return cases.recipe.__wrapped__()


@pytest.fixture
def portable(monkeypatch):
    return cases.portable.__wrapped__(monkeypatch)


@pytest.mark.parametrize("missing_stage", [False, True])
def test_recovery_interrupted_after_backup_restore_is_retryable(
    service, recipe, portable, missing_stage
):
    service.craft_skill(**recipe)
    cases.interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "old_moved")
    _, paths = cases.pending(service)
    if missing_stage:
        shutil.rmtree(paths["stage"])
    original = service._publisher._write_record

    def write(record, paths, warnings):
        if record["phase"] == "rolled_back":
            raise cases.Interrupted("restored but not marked")
        original(record, paths, warnings)

    with (
        patch.object(service._publisher, "_write_record", side_effect=write),
        pytest.raises(cases.Interrupted),
    ):
        service._publisher.recover()
    assert paths["target"].exists() and not paths["backup"].exists()
    assert json.loads(paths["journal"].read_text())["phase"] == "rolling_back"
    assert service._publisher.recover().performed
    assert service.get_skill("checks")["body"] == "Original instructions"
    assert not service._publisher.recover().performed


@pytest.mark.parametrize("content", ["{broken", "x" * 8193, "[]"])
def test_malformed_or_oversized_journal_is_preserved(
    service, recipe, portable, content
):
    service.craft_skill(**recipe)
    cases.interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "prepared")
    _, paths = cases.pending(service)
    paths["journal"].write_text(content)
    before = cases.tree_snapshot(paths["target"].parent)
    with pytest.raises(SkillRecoveryError, match="Evidence retained"):
        service._publisher.recover()
    assert cases.tree_snapshot(paths["target"].parent) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_journal_is_never_followed_or_removed(service, recipe, portable, kind):
    service.craft_skill(**recipe)
    cases.interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "prepared")
    _, paths = cases.pending(service)
    outside = service.project_root / "evidence.json"
    if kind == "symlink":
        paths["journal"].rename(outside)
        paths["journal"].symlink_to(outside)
    else:
        os.link(paths["journal"], outside)
    original = outside.read_bytes()
    with pytest.raises(ValueError):
        service._publisher.recover()
    assert outside.read_bytes() == original and paths["journal"].exists()


@pytest.mark.parametrize(
    "which,phase", [("stage", "prepared"), ("backup", "old_moved")]
)
def test_stage_or_backup_hash_disagreement_fails_closed(
    service, recipe, portable, which, phase
):
    service.craft_skill(**recipe)
    cases.interrupt_at_phase(service, recipe | {"instructions": "Changed"}, phase)
    _, paths = cases.pending(service)
    (paths[which] / "edited.txt").write_text("External edit")
    before = cases.tree_snapshot(paths["target"].parent)
    with pytest.raises(SkillRecoveryError, match="hash disagreement"):
        service._publisher.recover()
    assert cases.tree_snapshot(paths["target"].parent) == before


def test_duplicate_transactions_are_rejected_before_any_recovery(
    service, recipe, portable
):
    service.craft_skill(**recipe)
    cases.interrupt_at_phase(service, recipe | {"instructions": "Changed"}, "prepared")
    record, paths = cases.pending(service)
    publisher = service._publisher
    stage = publisher.create_stage(paths["target"])
    operation_id = module._ARTIFACT.fullmatch(stage.name)[2]
    second_paths = publisher._paths("checks", operation_id)
    shutil.copytree(paths["stage"], stage, dirs_exist_ok=True)
    second_record = record | {
        "operation_id": operation_id,
        "stage": stage.name,
        "backup": second_paths["backup"].name,
    }
    with publisher.lock:
        publisher._write_record(second_record, second_paths, [])
    before = cases.tree_snapshot(paths["target"].parent)
    with pytest.raises(SkillRecoveryError, match="multiple journals"):
        publisher.recover()
    assert cases.tree_snapshot(paths["target"].parent) == before


def test_new_tree_verification_failure_does_not_delete_evidence(
    service, recipe, portable
):
    service.craft_skill(**recipe)
    publisher = service._publisher
    original = publisher._write_record

    def write(record, paths, warnings):
        original(record, paths, warnings)
        if record["phase"] == "new_moved":
            (paths["target"] / "changed.txt").write_text(
                "Unexpected write after rename"
            )

    with (
        patch.object(publisher, "_write_record", side_effect=write),
        pytest.raises(SkillRecoveryError, match="Evidence retained"),
    ):
        service.craft_skill(
            **(recipe | {"instructions": "Changed"}), update_existing=True
        )
    _, paths = cases.pending(service)
    assert paths["backup"].exists() and (paths["target"] / "changed.txt").exists()


def test_unsupported_fsync_is_reported_without_power_loss_claim(
    service, recipe, portable
):
    with patch.object(
        module.os, "fsync", side_effect=OSError(errno.EINVAL, "unsupported")
    ):
        result = service.craft_skill(**recipe)
    assert result["publication_guarantee"] == "recoverable-staged-swap"
    assert any(
        "no power-loss durability guarantee" in warning
        for warning in result["warnings"]
    )


def test_journal_disk_error_does_not_publish_partial_tree(service, recipe, portable):
    with (
        patch.object(
            module.os, "fsync", side_effect=OSError(errno.EIO, "disk I/O error")
        ),
        pytest.raises((OSError, SkillRecoveryError)),
    ):
        service.craft_skill(**recipe)
    target = service.project_root / ".agents/skills/checks"
    assert not target.exists()
    assert list(target.parent.glob(".skill-*.journal.*"))


def test_missing_libc_symbol_falls_back(service, recipe):
    with patch.object(module.ctypes, "CDLL", return_value=object()):
        result = service.craft_skill(**recipe)
    assert result["publication_backend"] == "portable-journaled-swap"


def test_lock_file_hardlink_rejected_before_any_skill_write(service, recipe):
    parent = service.project_root / ".agents/skills"
    parent.mkdir(parents=True)
    (parent / ".skill-publish.lock").write_bytes(b"\0")
    os.link(parent / ".skill-publish.lock", service.project_root / "user-file")
    with pytest.raises(ValueError, match="non-hardlinked"):
        service.craft_skill(**recipe)
    assert not (parent / "checks").exists()
    assert (service.project_root / "user-file").read_bytes() == b"\0"


def test_unloadable_libc_is_an_unavailable_capability(service, recipe):
    with patch.object(module.ctypes, "CDLL", side_effect=OSError("libc unavailable")):
        result = service.craft_skill(**recipe)
    assert result["publication_backend"] == "portable-journaled-swap"


def test_distinct_case_sensitive_roots_do_not_share_wrong_lock_parent(tmp_path):
    upper = tmp_path / "Project"
    lower = tmp_path / "project"
    upper.mkdir()
    if lower.exists():
        pytest.skip("Filesystem is case-insensitive")
    lower.mkdir()
    first = module.project_skill_lock(upper)
    second = module.project_skill_lock(lower)
    assert first is not second
    assert first.parent == upper / ".agents/skills"
    assert second.parent == lower / ".agents/skills"


@pytest.mark.parametrize("same_skill", [True, False])
def test_pending_cleanup_does_not_allow_conflicting_second_journal(
    service, recipe, portable, same_skill
):
    first = recipe if same_skill else recipe | {"name": "checks-extra"}
    service.craft_skill(**first)
    original = service._publisher._remove_tree

    def remove(path):
        if path.name.endswith(".backup"):
            raise PermissionError("Backup still open")
        original(path)

    with patch.object(service._publisher, "_remove_tree", side_effect=remove):
        committed = service.craft_skill(
            **(first | {"instructions": "Changed"}), update_existing=True
        )
        assert committed["warnings"]
        if same_skill:
            with pytest.raises(SkillRecoveryError, match="cleanup is still pending"):
                service.craft_skill(
                    **(recipe | {"instructions": "Changed again"}), update_existing=True
                )
            assert service.get_skill("checks")["body"] == "Changed"
        else:
            result = service.craft_skill(**recipe)
            assert result["operation"] == "create"
        assert (
            len(
                list(
                    (service.project_root / ".agents/skills").glob(
                        ".skill-*.journal.json"
                    )
                )
            )
            == 1
        )
    assert service._publisher.recover().performed
    assert not service._publisher.recover().performed
