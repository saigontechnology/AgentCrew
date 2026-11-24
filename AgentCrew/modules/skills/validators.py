"""An intentionally small registry of non-executing script syntax validators."""

import ast
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Protocol


class ScriptValidator(Protocol):
    extensions: tuple[str, ...]

    def validate(self, content: str, path: str) -> str:
        """Reject invalid syntax and return the validator's identity, without execution."""
        ...


@dataclass(frozen=True)
class PythonValidator:
    extensions: tuple[str, ...] = (".py",)

    def validate(self, content: str, path: str) -> str:
        try:
            tree = ast.parse(content, filename=path)
            compile(tree, path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"Static Python syntax validation failed for {path}: {exc}"
            ) from exc
        return "ast.parse+compile"


@dataclass(frozen=True)
class TreeSitterValidator:
    language: str
    extensions: tuple[str, ...]

    def validate(self, content: str, path: str) -> str:
        """Use installed grammar data, with a fresh parser rather than shared parser state."""
        try:
            from tree_sitter import Parser

            from AgentCrew.modules.code_analysis.tree_sitter_runtime import (
                TreeSitterRuntime,
            )

            runtime = TreeSitterRuntime.get_instance()
            if not runtime.is_in_manifest(self.language):
                raise ValueError("grammar is not in the runtime manifest")
            if not runtime.is_downloaded(self.language):
                raise ValueError(
                    "grammar is not installed; provision it separately before crafting"
                )
            parser = Parser(runtime.get_language(self.language))
            tree = parser.parse(content.encode("utf-8"))
            runtime_version = version("tree-sitter-language-pack")
        except Exception as exc:
            raise ValueError(
                f"Static {self.language} validator unavailable: {exc}"
            ) from exc
        nodes = [tree.root_node]
        while nodes:
            node = nodes.pop()
            if node.is_error or node.is_missing:
                condition = "MISSING" if node.is_missing else "ERROR"
                row, column = node.start_point
                raise ValueError(
                    f"Static {self.language} syntax validation failed for {path}: "
                    f"{condition} at {row + 1}:{column + 1}"
                )
            nodes.extend(reversed(node.children))
        if tree.root_node.has_error:
            raise ValueError(
                f"Static {self.language} syntax validation failed for {path}: error tree"
            )
        return f"tree-sitter-language-pack/{runtime_version}:{self.language}"


SCRIPT_VALIDATORS: MappingProxyType[str, ScriptValidator] = MappingProxyType(
    {
        "python": PythonValidator(),
        "bash": TreeSitterValidator("bash", (".sh", ".bash")),
        "powershell": TreeSitterValidator("powershell", (".ps1", ".psm1")),
        "javascript": TreeSitterValidator("javascript", (".js", ".mjs", ".cjs")),
    }
)


def validate_script(path: str, language: str, content: str) -> dict[str, str]:
    if not isinstance(language, str) or language not in SCRIPT_VALIDATORS:
        raise ValueError(
            f"Unsupported validator: {language!r}; choose one of {', '.join(SCRIPT_VALIDATORS)}"
        )
    validator = SCRIPT_VALIDATORS[language]
    if PurePosixPath(path).suffix not in validator.extensions:
        raise ValueError(
            f"Language/extension mismatch for {path}: {language} requires {', '.join(validator.extensions)}"
        )
    return {
        "path": path,
        "language": language,
        "validator": validator.validate(content, path),
        "status": "passed",
    }
