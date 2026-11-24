"""Focused verification; no crafted script is ever executed."""

import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml
from jsonschema import validate

from AgentCrew.modules.agents.message_formatter import AgentMessageFormatter
from AgentCrew.modules.agents.tool_result_summary import (
    bind_inference_scope,
    reset_inference_scope,
)
from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills.service import MAX_BODY_CHARS
from AgentCrew.modules.skills.tool import (
    active_skill_results,
    bind_skill_history,
    compact_skill_results,
    get_activate_skill_tool_handler,
    get_craft_skill_tool_definition,
    get_craft_skill_tool_handler,
    register,
)


def write_skill(
    root,
    name,
    body="Run scripts/check.py",
    description="Reusable check",
    rel=".agents/skills",
    metadata=None,
):
    path = root / rel / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {"name": name, "description": description}
    if metadata:
        frontmatter["metadata"] = metadata
    path.write_text("---\n" + yaml.safe_dump(frontmatter) + "---\n\n" + body)
    return path


@pytest.fixture
def service(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return SkillsService(project_root=project, user_root=home, trusted_project=True)


@pytest.fixture
def recipe():
    return {
        "name": "project-check",
        "description": "Run the reusable project checks after implementation",
        "instructions": "From the project root run scripts/check.py through the approved command tool.",
        "scripts": [
            {
                "path": "scripts/check.py",
                "language": "python",
                "content": "import os\nprint(os.environ.get('TASK_API_TOKEN', ''))\n",
            }
        ],
        "metadata": {"required_env": "TASK_API_TOKEN", "version": "1.0"},
    }


def canonical_result(formatter, history, name, result):
    call_id = str(len(history))
    tool_use = {"id": call_id, "name": "activate_skill", "input": {"name": name}}
    history.append(formatter.format_assistant_message("", tool_uses=[tool_use]))
    history.append(formatter.format_tool_result(tool_use, result))


@pytest.mark.asyncio
async def test_fifo_duplicate_reactivation_and_protocol_pairing(service):
    owner = SimpleNamespace(name="Verifier")
    formatter = AgentMessageFormatter(owner)
    for name in "abcde":
        write_skill(service.project_root, name, body=f"unique instructions {name}")
    service.refresh()
    history = []
    bind_skill_history(owner, history)
    handler = get_activate_skill_tool_handler(service, agent=owner)
    for name in "abc":
        canonical_result(formatter, history, name, await handler(name=name))
    assert list(active_skill_results(history)[0]) == list("abc")
    before = copy.deepcopy(history)
    compact_skill_results(before)
    assert before == history
    duplicate = await handler(name="a")
    assert "already loaded" in duplicate
    canonical_result(formatter, history, "a", duplicate)
    canonical_result(formatter, history, "d", await handler(name="d"))
    inference = copy.deepcopy(history)
    compact_skill_results(inference)
    assert (
        inference[1]["content"]
        == '<skill_content_evicted name="a" reason="active-skill-limit"/>'
    )
    assert "unique instructions a" in history[1]["content"]
    assert list(active_skill_results(inference)[0]) == list("bcd")
    for original, compacted in zip(history, inference, strict=True):
        assert {k: v for k, v in original.items() if k != "content"} == {
            k: v for k, v in compacted.items() if k != "content"
        }
    canonical_result(formatter, history, "a", await handler(name="a"))
    inference = copy.deepcopy(history)
    compact_skill_results(inference)
    assert list(active_skill_results(inference)[0]) == list("cda")
    assert "unique instructions a" in inference[-1]["content"]
    assert "skill_content_evicted" in inference[3]["content"]
    calls = {call["id"] for msg in inference for call in msg.get("tool_calls", [])}
    results = {msg["tool_call_id"] for msg in inference if msg["role"] == "tool"}
    assert calls == results
    assert all(
        msg["tool_name"] == "activate_skill"
        for msg in inference
        if msg["role"] == "tool"
    )


@pytest.mark.asyncio
async def test_scope_isolation_reload_fork_and_child_task(service):
    owner = SimpleNamespace(name="Verifier")
    write_skill(service.project_root, "a")
    handler = get_activate_skill_tool_handler(service, agent=owner)

    async def request(scope):
        token = bind_inference_scope(scope)
        try:
            history = []
            bind_skill_history(owner, history)
            first = await asyncio.wait_for(handler(name="a"), timeout=2)
            assert first.startswith("<skill_content ")
            assert "already loaded" in await handler(name="a")
            return first
        finally:
            reset_inference_scope(token)

    first, second = await asyncio.gather(request("chat:one"), request("a2a:two"))
    assert first == second
    history = [
        {
            "role": "tool",
            "tool_name": "activate_skill",
            "tool_call_id": "a",
            "content": first,
        }
    ]
    bind_skill_history(owner, json.loads(json.dumps(history)))
    assert "already loaded" in await handler(name="a")
    bind_skill_history(owner, [])
    assert (await handler(name="a")).startswith("<skill_content ")
    bind_skill_history(owner, copy.deepcopy(history))
    assert "already loaded" in await handler(name="a")


@pytest.mark.asyncio
async def test_fifo_on_request_snapshot_and_legacy_duplicate_results(service):
    owner = object()
    for name in "abcd":
        write_skill(service.project_root, name)
    handler = get_activate_skill_tool_handler(service, agent=owner)
    bind_skill_history(owner, [])
    results = [await handler(name=name) for name in "abcda"]
    assert all(result.startswith("<skill_content ") for result in results)
    messages = [
        {"role": "tool", "tool_name": "activate_skill", "content": result}
        for result in results
    ]
    assert list(active_skill_results(messages)[0]) == list("cda")
    messages.insert(1, copy.deepcopy(messages[0]))
    compact_skill_results(messages)
    assert list(active_skill_results(messages)[0]) == list("cda")
    before = copy.deepcopy(messages)
    compact_skill_results(messages)
    assert before == messages


def test_trust_user_scope_and_collision_precedence(service):
    for root, body in [(service.user_root, "user"), (service.project_root, "project")]:
        write_skill(root, "same", body=body)
    write_skill(service.project_root, "project-only")
    write_skill(service.project_root, "same", body="legacy", rel=".claude/skills")
    untrusted = SkillsService(
        project_root=service.project_root, user_root=service.user_root
    )
    assert untrusted.get_skill_names() == ["same"]
    assert untrusted.get_skill("same")["body"] == "user"
    service.refresh()
    assert service.get_skill("same")["body"] == "legacy"
    with pytest.raises(ValueError, match="trusted"):
        untrusted.craft_skill(name="x", description="x", instructions="x", scripts=[])


def test_cache_change_detection_and_bound(service, monkeypatch):
    from AgentCrew.modules.skills import service as module

    path = write_skill(service.project_root, "same")
    with patch.object(
        service, "_parse_skill_md", wraps=service._parse_skill_md
    ) as parse:
        service.refresh()
        assert parse.call_count == 1
        assert service.refresh() is False
        assert parse.call_count == 1
        path.write_text(path.read_text().replace("Reusable check", "Changed check!"))
        assert service.refresh() is True
        assert parse.call_count == 2
        service.invalidate()
        assert parse.call_count == 3
    monkeypatch.setattr(module, "MAX_CACHE_ENTRIES", 2)
    for name in "abc":
        write_skill(service.project_root, name)
    service.refresh()
    assert len(service._cache) <= 2
    path.unlink()
    assert service.refresh() is True
    assert service.get_skill("same") is None


def test_catalog_budget_order_and_no_body(service):
    write_skill(service.user_root, "aaa-user", body="BODY-DO-NOT-INJECT")
    for index in range(25):
        write_skill(
            service.project_root, f"project-{index}", description="Details " * 100
        )
    service.refresh()
    assert service.get_catalog()[0]["name"].startswith("project-")
    prompt = service.catalog_prompt(10000)
    assert len(prompt) <= 800
    assert "omitted" in prompt
    assert "BODY-DO-NOT-INJECT" not in prompt
    assert service.catalog_prompt(10000) == prompt
    assert len(service.catalog_prompt()) <= 8000
    assert service.catalog_prompt(1) == ""


@pytest.mark.asyncio
async def test_provenance_escaping_body_budget_and_lazy_resources(service):
    write_skill(
        service.project_root,
        "a",
        body="Keep <literal> instructions",
        metadata={"version": '1"<&'},
    )
    root = service.project_root / ".agents/skills/a"
    (root / "scripts").mkdir()
    (root / "scripts/a&b.py").write_text("DO-NOT-EAGERLY-LOAD")
    handler = get_activate_skill_tool_handler(service)
    result = await handler(name="a")
    assert 'scope="project"' in result and 'trusted="true"' in result
    assert 'content_hash="' in result
    assert "&lt;" in result and "&amp;" in result
    assert "scripts/a&amp;b.py" in result
    assert "DO-NOT-EAGERLY-LOAD" not in result
    assert "Keep <literal> instructions" in result
    write_skill(service.project_root, "huge", body="x" * (MAX_BODY_CHARS + 1))
    with pytest.raises(ValueError, match="not truncated"):
        await handler(name="huge")


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux atomic publication"
)
def test_craft_create_noop_update_conflict_preserves_files(service, recipe):
    validate(recipe, get_craft_skill_tool_definition()["function"]["parameters"])
    created = service.craft_skill(**recipe)
    assert created["operation"] == "create" and created["executed"] is False
    root = service.project_root / ".agents/skills/project-check"
    frontmatter = yaml.safe_load((root / "SKILL.md").read_text().split("---")[1])
    assert frontmatter["name"] == recipe["name"]
    assert service.get_skill(recipe["name"])
    assert service.craft_skill(**recipe)["operation"] == "no-op"
    reordered = recipe | {"metadata": dict(reversed(recipe["metadata"].items()))}
    assert service.craft_skill(**reordered)["operation"] == "no-op"
    recipe["instructions"] = "Updated instructions"
    with pytest.raises(ValueError, match="update_existing"):
        service.craft_skill(**recipe)
    (root / "keep.txt").write_text("user file")
    (root / "empty").mkdir()
    (root / "scripts/check.py").chmod(0o755)
    with pytest.raises(ValueError, match="hash conflict"):
        service.craft_skill(
            **recipe,
            update_existing=True,
            expected_existing_hash=created["content_hash"],
        )
    before = service._files_hash(service._tree_files(root))
    updated = service.craft_skill(
        **recipe, update_existing=True, expected_existing_hash=before
    )
    assert updated["operation"] == "update" and updated["content_hash"] != before
    assert (root / "keep.txt").read_text() == "user file"
    assert (root / "empty").is_dir()
    assert (root / "scripts/check.py").stat().st_mode & 0o777 == 0o755
    assert not list(root.parent.glob(".skill-project-check-*"))


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("name", "../outside", "Invalid skill name"),
        ("name", "wrong--name", "Invalid skill name"),
        ("description", "", "Description"),
        ("instructions", "", "Instructions"),
        ("instructions", "x" * (MAX_BODY_CHARS + 1), "Instructions"),
        ("scripts", [], "instruction-only"),
        ("metadata", {"bad": 1}, "Metadata"),
        ("compatibility", "x" * 501, "Compatibility"),
        ("update_existing", "yes", "boolean"),
    ],
)
def test_craft_rejects_invalid_without_writing(service, recipe, field, value, match):
    recipe[field] = value
    with pytest.raises((ValueError, TypeError), match=match):
        service.craft_skill(**recipe)
    assert not (service.project_root / ".agents").exists()


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/escape.py",
        "scripts/../escape.py",
        "../escape.py",
        "elsewhere/run.py",
        "scripts/../../x.py",
        "scripts/./x.py",
        "scripts\\x.py",
        "scripts//x.py",
        "C:/x.py",
    ],
)
def test_craft_path_escape_rejected(service, recipe, path):
    recipe["scripts"][0]["path"] = path
    with pytest.raises(ValueError, match="Script paths"):
        service.craft_skill(**recipe)
    assert not (service.project_root / ".agents").exists()


def test_duplicate_language_syntax_and_secret_fail_before_writes(service, recipe):
    cases = [
        [recipe["scripts"][0], recipe["scripts"][0]],
        [{"path": "scripts/x.py", "language": "bash", "content": "echo hi"}],
        [
            {
                "path": "scripts/x.py",
                "language": "python",
                "content": "def syntax error(",
            }
        ],
        [
            {
                "path": "scripts/x.py",
                "language": "python",
                "content": "key = '" + "AKIA" + "A" * 16 + "'",
            }
        ],
    ]
    for scripts in cases:
        with pytest.raises(ValueError):
            service.craft_skill(**(recipe | {"scripts": scripts}))
        assert not (service.project_root / ".agents").exists()


@pytest.mark.parametrize(
    "link_part", [".agents", ".agents/skills", ".agents/skills/project-check"]
)
def test_symlink_escape_rejected(service, recipe, tmp_path, link_part):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = service.project_root / link_part
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        service.craft_skill(**recipe)
    assert list(outside.iterdir()) == []
    if link_part == ".agents/skills":
        with pytest.raises(ValueError, match="Symlinks"):
            service.get_skill_names()
    else:
        assert service.get_skill_names() == []


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux atomic publication"
)
def test_symlink_and_hardlink_in_existing_skill_rejected(service, recipe, tmp_path):
    service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills/project-check"
    outside = tmp_path / "outside.py"
    outside.write_text("user data")
    link = root / "scripts/link.py"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        service.craft_skill(**recipe, update_existing=True)
    link.unlink()
    os.link(outside, link)
    with pytest.raises(ValueError, match="hardlinked"):
        service.craft_skill(**recipe, update_existing=True)
    assert outside.read_text() == "user data"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux atomic publication"
)
def test_atomic_failure_and_concurrent_edit_leave_original(
    service, recipe, monkeypatch
):
    from AgentCrew.modules.skills import publisher as module

    created = service.craft_skill(**recipe)
    root = service.project_root / ".agents/skills/project-check"
    original = (root / "SKILL.md").read_bytes()
    recipe["instructions"] = "changed"
    with (
        patch.object(
            module, "_atomic_publish", side_effect=OSError("atomic unavailable")
        ),
        pytest.raises(OSError),
    ):
        service.craft_skill(**recipe, update_existing=True)
    assert (root / "SKILL.md").read_bytes() == original
    assert not list(root.parent.glob(".skill-project-check-*"))
    real_write = Path.write_bytes

    def concurrent_write(path, data):
        if path.name == "SKILL.md" and path.parent.name.startswith(
            ".skill-project-check-"
        ):
            (root / "user.txt").write_text("concurrent edit")
        return real_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", concurrent_write)
    with pytest.raises(ValueError, match="hash conflict"):
        service.craft_skill(
            **recipe,
            update_existing=True,
            expected_existing_hash=created["content_hash"],
        )
    assert (root / "SKILL.md").read_bytes() == original
    assert (root / "user.txt").read_text() == "concurrent edit"


@pytest.mark.asyncio
async def test_never_executes_script_and_unavailable_publication(
    service, recipe, tmp_path
):
    sentinel = tmp_path / "must-not-exist"
    recipe["scripts"][0]["content"] = (
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n"
    )
    handler = get_craft_skill_tool_handler(service)
    with patch("AgentCrew.modules.skills.publisher.sys.platform", "unsupported"):
        result = await handler(**recipe)
    assert result["publication_backend"] == "portable-journaled-swap"
    assert not sentinel.exists()
    assert (service.project_root / ".agents/skills/project-check").exists()
    if sys.platform.startswith("linux"):
        await handler(**recipe)
        assert not sentinel.exists()


def test_registration_sequential_permission_and_empty_catalog(service):
    from AgentCrew.modules.acp.tools.permission_broker import SENSITIVE_TOOL_NAMES
    from AgentCrew.modules.agents.local_agent import LocalAgent
    from AgentCrew.modules.tools.parallel_executor import is_sequential_tool

    agent = LocalAgent(
        "Registration",
        "Verification",
        None,
        {
            "skills": service,
            "command_execution": Mock(),
            "file_editing": Mock(),
        },
        ["command_execution", "file_editing"],
    )
    agent.register_tools()
    assert "craft_skill" in agent.tool_definitions
    assert "activate_skill" not in agent.tool_definitions
    assert service.catalog_prompt() == ""
    service.trusted_project = False
    register(service, agent)
    assert "craft_skill" not in agent.tool_definitions
    assert is_sequential_tool("activate_skill") and is_sequential_tool("craft_skill")
    assert "craft_skill" in SENSITIVE_TOOL_NAMES


def test_frontmatter_edge_cases_and_malformed_files(service):
    root = service.project_root / ".agents/skills"
    for name, text in {
        "crlf": "\ufeff---\r\nname: crlf\r\ndescription: CRLF format\r\n---\r\nbody",
        "scalar": "---\nplain yaml\n---\nbody",
        "wrong": "---\nname: mismatch\ndescription: bad\n---\nbody",
        "lenient": "---\nname: lenient\ndescription: Use when: needed\n---\nbody",
        "list": "---\n- invalid\n---\nbody",
    }.items():
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(text.encode())
    bad = root / "bad-encoding/SKILL.md"
    bad.parent.mkdir()
    bad.write_bytes(b"\xff")
    service.refresh()
    assert service.get_skill_names() == ["crlf", "lenient", "mismatch"]
    raw = (root / "crlf/SKILL.md").read_bytes()
    assert service.get_skill("crlf")["content_hash"] == hashlib.sha256(raw).hexdigest()
