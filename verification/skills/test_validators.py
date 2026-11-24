"""Four-language static validation, without interpreter or dependency execution."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from AgentCrew.modules.skills import SkillsService
from AgentCrew.modules.skills.tool import get_craft_skill_tool_definition
from AgentCrew.modules.skills.validators import SCRIPT_VALIDATORS, validate_script

SAMPLES = [
    ("python", ".py", "print('héllo')\n", "return 1"),
    ("bash", ".sh", "echo 'héllo'\n", "if then fi"),
    ("bash", ".bash", "echo hello\n", "if then fi"),
    ("powershell", ".ps1", 'Write-Output "héllo"\n', "function broken {"),
    (
        "powershell",
        ".psm1",
        'function Test-Check { Write-Output "ok" }\n',
        "function broken {",
    ),
    ("javascript", ".js", 'console.log("héllo");\n', "const x = ;"),
    ("javascript", ".mjs", 'export const check = "ok";\n', "const x = ;"),
    ("javascript", ".cjs", 'module.exports = "ok";\n', "const x = ;"),
]


@pytest.mark.parametrize("language,extension,valid,invalid", SAMPLES)
def test_installed_parser_accepts_valid_utf8_and_rejects_invalid(
    language, extension, valid, invalid
):
    result = validate_script("scripts/check" + extension, language, valid)
    assert result["status"] == "passed" and result["language"] == language
    assert (
        result["validator"] == "ast.parse+compile"
        if language == "python"
        else result["validator"].startswith("tree-sitter-language-pack/1.15.8:")
    )
    with pytest.raises(ValueError, match="syntax validation failed"):
        validate_script("scripts/check" + extension, language, invalid)


@pytest.mark.parametrize("language", SCRIPT_VALIDATORS)
def test_extension_mismatch_and_registry_limit(language):
    with pytest.raises(ValueError, match="Language/extension mismatch"):
        validate_script("scripts/check.txt", language, "placeholder")
    with pytest.raises(ValueError, match="Unsupported validator"):
        validate_script("scripts/check.rb", "ruby", "puts 'no'")
    schema = get_craft_skill_tool_definition()["function"]["parameters"]
    assert schema["properties"]["scripts"]["items"]["properties"]["language"][
        "enum"
    ] == list(SCRIPT_VALIDATORS)


@pytest.mark.parametrize("language", ["bash", "powershell", "javascript"])
@pytest.mark.parametrize("manifest,downloaded", [(False, True), (True, False)])
def test_unavailable_grammar_fails_closed_without_loading(
    language, manifest, downloaded
):
    from AgentCrew.modules.code_analysis.tree_sitter_runtime import TreeSitterRuntime

    runtime = SimpleNamespace(
        is_in_manifest=Mock(return_value=manifest),
        is_downloaded=Mock(return_value=downloaded),
        get_language=Mock(),
    )
    extension = SCRIPT_VALIDATORS[language].extensions[0]
    with (
        patch.object(TreeSitterRuntime, "get_instance", return_value=runtime),
        pytest.raises(ValueError, match="validator unavailable"),
    ):
        validate_script("scripts/check" + extension, language, "text")
    runtime.get_language.assert_not_called()


@pytest.mark.parametrize("language", ["bash", "powershell", "javascript"])
def test_parser_load_error_is_explicit(language):
    from AgentCrew.modules.code_analysis.tree_sitter_runtime import TreeSitterRuntime

    runtime = SimpleNamespace(
        is_in_manifest=lambda _: True,
        is_downloaded=lambda _: True,
        get_language=Mock(side_effect=RuntimeError("grammar ABI unavailable")),
    )
    with (
        patch.object(TreeSitterRuntime, "get_instance", return_value=runtime),
        pytest.raises(ValueError, match="grammar ABI unavailable"),
    ):
        validate_script(
            "scripts/check" + SCRIPT_VALIDATORS[language].extensions[0],
            language,
            "text",
        )


@pytest.fixture
def service(tmp_path):
    return SkillsService(
        project_root=tmp_path, user_root=tmp_path / "home", trusted_project=True
    )


def recipe(scripts):
    return {
        "name": "multi-check",
        "description": "Project checks in multiple languages",
        "instructions": "Run each project-relative script through the approved command tool.",
        "scripts": scripts,
    }


def test_mixed_language_skill_is_statically_validated(service):
    scripts = [
        {"path": "scripts/check" + extension, "language": language, "content": valid}
        for language, extension, valid, _ in SAMPLES
    ]
    result = service.craft_skill(**recipe(scripts))
    assert len(result["validation"]) == 8
    assert result["validation_scope"] == "static_syntax_only"
    assert result["executed"] is False
    assert (
        service.craft_skill(**recipe(list(reversed(scripts))))["operation"] == "no-op"
    )
    for script in scripts:
        assert (
            service.project_root / ".agents/skills/multi-check" / script["path"]
        ).read_text() == script["content"]


@pytest.mark.parametrize(
    "language,extension",
    [(name, validator.extensions[0]) for name, validator in SCRIPT_VALIDATORS.items()],
)
def test_validators_never_execute_content(service, language, extension, tmp_path):
    sentinel = tmp_path / "must-not-exist"
    content = {
        "python": f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        "bash": f"printf executed > '{sentinel}'\n",
        "powershell": f"Set-Content -Path '{sentinel}' -Value 'executed'\n",
        "javascript": f"require('fs').writeFileSync('{sentinel}', 'executed');\n",
    }[language]
    with (
        patch(
            "subprocess.Popen", side_effect=AssertionError("No interpreter subprocess")
        ),
        patch("os.system", side_effect=AssertionError("No shell execution")),
    ):
        result = service.craft_skill(
            **recipe(
                [
                    {
                        "path": "scripts/check" + extension,
                        "language": language,
                        "content": content,
                    }
                ]
            )
        )
    assert not sentinel.exists() and result["executed"] is False


@pytest.mark.parametrize(
    "language,extension",
    [(name, validator.extensions[0]) for name, validator in SCRIPT_VALIDATORS.items()],
)
def test_all_languages_keep_path_secret_and_size_guards(service, language, extension):
    prefix = "//" if language == "javascript" else "#"
    cases = [
        {
            "path": "scripts/../escape" + extension,
            "language": language,
            "content": prefix + " safe",
        },
        {
            "path": "scripts/check" + extension,
            "language": language,
            "content": prefix + " AKIA" + "A" * 16,
        },
        {
            "path": "scripts/check" + extension,
            "language": language,
            "content": "x" * 1000001,
        },
    ]
    for script in cases:
        with pytest.raises(ValueError):
            service.craft_skill(**recipe([script]))
        assert not (service.project_root / ".agents").exists()
