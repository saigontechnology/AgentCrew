from __future__ import annotations

import os
from typing import TYPE_CHECKING, Set
from loguru import logger
from tree_sitter_language_pack import (
    available_languages,
    download,
    get_language,
    get_parser,
    has_language,
    manifest_languages,
)

if TYPE_CHECKING:
    from tree_sitter import Language, Parser


ALIAS_TO_PACK: dict[str, str] = {
    "c-sharp": "csharp",
    "c_sharp": "csharp",
    "c#": "csharp",
    "f-sharp": "fsharp",
    "f#": "fsharp",
    "objective-c": "objc",
    "objective_c": "objc",
}

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".rake": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".sh": "bash",
    ".bash": "bash",
    ".swift": "swift",
    ".scala": "scala",
    ".lua": "lua",
    ".r": "r",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".vim": "vim",
    ".el": "elisp",
    ".clj": "clojure",
}


class TreeSitterRuntime:
    _instance: TreeSitterRuntime | None = None

    @classmethod
    def get_instance(cls) -> TreeSitterRuntime:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._parser_cache: dict[str, "Parser"] = {}
        self._language_cache: dict[str, "Language"] = {}
        self._manifest: Set[str] | None = None

    def _resolve_name(self, name: str) -> str:
        lower = name.lower().strip()
        return ALIAS_TO_PACK.get(lower, lower)

    def _get_manifest(self) -> Set[str]:
        if self._manifest is None:
            self._manifest = set(manifest_languages())
        return self._manifest

    def detect_language_for_file(self, file_path: str) -> str | None:
        ext = os.path.splitext(file_path)[1].lower()
        return EXTENSION_TO_LANGUAGE.get(ext)

    def is_in_manifest(self, name: str) -> bool:
        resolved = self._resolve_name(name)
        return resolved in self._get_manifest()

    def is_downloaded(self, name: str) -> bool:
        resolved = self._resolve_name(name)
        return has_language(resolved)

    def is_supported(self, name: str) -> bool:
        return self.is_in_manifest(name)

    def get_parser(self, name: str) -> "Parser":
        resolved = self._resolve_name(name)
        if resolved not in self._parser_cache:
            parser = get_parser(resolved)  # type: ignore
            self._parser_cache[resolved] = parser
        return self._parser_cache[resolved]

    def get_language(self, name: str) -> "Language":
        resolved = self._resolve_name(name)
        if resolved not in self._language_cache:
            lang = get_language(resolved)  # type: ignore
            self._language_cache[resolved] = lang
        return self._language_cache[resolved]

    def get_available_languages(self) -> list[str]:
        return list(available_languages())

    def get_manifest_languages(self) -> list[str]:
        return list(self._get_manifest())

    def prewarm(self, languages: list[str]) -> int:
        resolved = [self._resolve_name(lang) for lang in languages]
        valid = [lang for lang in resolved if lang in self._get_manifest()]
        if not valid:
            return 0
        count = download(valid)
        logger.info("Pre-downloaded %d language(s)", count)
        return count

    def prewarm_all(self) -> int:
        from tree_sitter_language_pack import download_all

        count = download_all()
        logger.info("Pre-downloaded all languages (%d new)", count)
        return count
