"""
Language-specific parsers for code analysis.

This module provides a unified interface for parsing different programming languages
using tree-sitter.
"""

from .base import BaseLanguageParser
from .cpp_parser import CppParser
from .csharp_parser import CSharpParser
from .generic_parser import GenericParser
from .go_parser import GoParser
from .java_parser import JavaParser
from .javascript_parser import JavaScriptParser
from .kotlin_parser import KotlinParser
from .php_parser import PhpParser
from .python_parser import PythonParser
from .ruby_parser import RubyParser
from .rust_parser import RustParser

LANGUAGE_PARSER_MAP = {
    "python": PythonParser,
    "javascript": JavaScriptParser,
    "typescript": JavaScriptParser,
    "tsx": JavaScriptParser,
    "java": JavaParser,
    "cpp": CppParser,
    "ruby": RubyParser,
    "go": GoParser,
    "rust": RustParser,
    "php": PhpParser,
    "csharp": CSharpParser,
    "c_sharp": CSharpParser,
    "kotlin": KotlinParser,
}


def get_parser_for_language(language: str) -> BaseLanguageParser:
    """
    Get the appropriate parser for a given language.

    Args:
        language: The programming language name

    Returns:
        A parser instance for the language
    """
    parser_class = LANGUAGE_PARSER_MAP.get(language)
    if parser_class:
        return parser_class()
    return GenericParser(language)


__all__ = [
    "LANGUAGE_PARSER_MAP",
    "BaseLanguageParser",
    "CSharpParser",
    "CppParser",
    "GenericParser",
    "GoParser",
    "JavaParser",
    "JavaScriptParser",
    "KotlinParser",
    "PhpParser",
    "PythonParser",
    "RubyParser",
    "RustParser",
    "get_parser_for_language",
]
