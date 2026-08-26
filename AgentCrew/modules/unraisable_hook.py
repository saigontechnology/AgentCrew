"""Shared sys.unraisablehook handling for known async cleanup errors.

Both CLI entrypoints install a custom ``sys.unraisablehook`` so that known,
non-fatal HTTP/transport finalization errors do not contaminate normal output.
This module centralizes the narrow detection logic: only the exact known
cleanup-only signatures are suppressed, and everything else is delegated to
``sys.__unraisablehook__`` so unexpected unraisable exceptions stay visible.
"""

from __future__ import annotations

import sys

# Message raised by contextlib's async-generator context manager when the
# generator does not stop after being closed via athrow() (GeneratorExit).
_GENERATOR_ATHROW_MESSAGE = "generator didn't stop after athrow()"

# Qualname of the httpcore/httpcore2 HTTP/1.1 byte-stream async iterator whose
# finalization produces the known cleanup-only RuntimeError.
_HTTPCORE_BODY_ITERATOR_QUAL = "HTTP11ConnectionByteStream.__aiter__"


def _is_known_httpcore_generator_error(unraisable) -> bool:
    """Return True only for the known httpcore async-generator cleanup error."""
    exc_value = unraisable.exc_value
    if not isinstance(exc_value, RuntimeError):
        return False
    if str(exc_value) != _GENERATOR_ATHROW_MESSAGE:
        return False
    return _originates_from_httpcore(unraisable)


def _originates_from_httpcore(unraisable) -> bool:
    """Return True when the failing generator or traceback ties to httpcore."""
    obj = unraisable.object
    if obj is not None and _HTTPCORE_BODY_ITERATOR_QUAL in repr(obj):
        return True
    tb = unraisable.exc_traceback
    while tb is not None:
        if "httpcore" in tb.tb_frame.f_code.co_filename:
            return True
        tb = tb.tb_next
    return False


def custom_unraisable_hook(unraisable) -> None:
    """Suppress known unraisable cleanup errors; delegate everything else.

    Suppressed cases:
    - ``AsyncLibraryNotFoundError`` (legacy behavior, matched by name).
    - The httpcore/httpcore2 ``HTTP11ConnectionByteStream.__aiter__``
      async-generator finalization ``RuntimeError`` carrying the exact
      "generator didn't stop after athrow()" message.

    All other unraisable exceptions are delegated to ``sys.__unraisablehook__``.
    """
    exc_type = unraisable.exc_type
    if exc_type is not None and exc_type.__name__ == "AsyncLibraryNotFoundError":
        return
    if _is_known_httpcore_generator_error(unraisable):
        return
    sys.__unraisablehook__(unraisable)
