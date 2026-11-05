from typing import TYPE_CHECKING

from .base_service import BaseMemoryService

if TYPE_CHECKING:
    from .chroma_service import ChromaMemoryService
    from .context_persistent import ContextPersistenceService

__all__ = [
    "BaseMemoryService",
    "ChromaMemoryService",
    "ContextPersistenceService",
]


def __getattr__(name):
    if name == "ChromaMemoryService":
        from .chroma_service import ChromaMemoryService

        return ChromaMemoryService
    if name == "ContextPersistenceService":
        from .context_persistent import ContextPersistenceService

        return ContextPersistenceService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
