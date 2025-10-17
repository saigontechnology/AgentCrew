"""
File editing module for AgentCrew.

Provides intelligent file editing capabilities using search/replace blocks
with syntax validation via tree-sitter.
"""

from .service import FileEditingService

__all__ = [
    "FileEditingService",
]
