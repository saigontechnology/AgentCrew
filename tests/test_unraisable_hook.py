"""Unit tests for the shared unraisable-hook handling.

Covers the narrow suppression of the known httpcore2 async-generator
finalization error, preservation of the legacy AsyncLibraryNotFoundError
suppression, delegation of everything else, and CLI entrypoint wiring.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from AgentCrew.modules.unraisable_hook import custom_unraisable_hook


class _FakeHttpcoreAsyncGenerator:
    """Duck-typed stand-in whose repr matches the reported failing generator."""

    def __repr__(self) -> str:
        return (
            "<async_generator object HTTP11ConnectionByteStream.__aiter__ "
            "at 0x7f48238a8d00>"
        )


def _httpcore_traceback() -> SimpleNamespace:
    """A fake traceback chain whose frame lives under httpcore2."""
    frame = SimpleNamespace(
        f_code=SimpleNamespace(
            co_filename=(
                "/home/user/.local/share/uv/tools/agentcrew-ai/lib/"
                "python3.12/site-packages/httpcore2/_async/http11.py"
            ),
            co_name="__aiter__",
        )
    )
    return SimpleNamespace(tb_frame=frame, tb_next=None)


def _unraisable(
    exc_type=RuntimeError,
    exc_value=None,
    exc_traceback=None,
    object=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        exc_type=exc_type,
        exc_value=exc_value,
        exc_traceback=exc_traceback,
        object=object,
    )


@pytest.fixture
def delegated_calls(monkeypatch):
    """Records delegations to the original unraisable hook."""
    calls = []
    monkeypatch.setattr(sys, "__unraisablehook__", lambda u: calls.append(u))
    return calls


def test_httpcore2_generator_finalization_error_is_suppressed(
    delegated_calls,
):
    unraisable = _unraisable(
        exc_value=RuntimeError("generator didn't stop after athrow()"),
        exc_traceback=None,
        object=_FakeHttpcoreAsyncGenerator(),
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == []


def test_httpcore2_generator_error_with_traceback_evidence_is_suppressed(
    delegated_calls,
):
    unraisable = _unraisable(
        exc_value=RuntimeError("generator didn't stop after athrow()"),
        exc_traceback=_httpcore_traceback(),
        object=None,
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == []


def test_unrelated_generator_runtime_error_delegates(delegated_calls):
    unraisable = _unraisable(
        exc_value=RuntimeError("generator didn't stop after athrow()"),
        exc_traceback=None,
        object=SimpleNamespace(),
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == [unraisable]


def test_unrelated_runtime_error_message_delegates(delegated_calls):
    unraisable = _unraisable(
        exc_value=RuntimeError("some other httpcore failure"),
        exc_traceback=_httpcore_traceback(),
        object=None,
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == [unraisable]


def test_suffixed_athrow_message_delegates(delegated_calls):
    unraisable = _unraisable(
        exc_value=RuntimeError("generator didn't stop after athrow(): another failure"),
        exc_traceback=_httpcore_traceback(),
        object=None,
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == [unraisable]


def test_unrelated_exception_type_delegates(delegated_calls):
    unraisable = _unraisable(
        exc_type=ValueError,
        exc_value=ValueError("generator didn't stop after athrow()"),
        exc_traceback=_httpcore_traceback(),
        object=None,
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == [unraisable]


def test_async_library_not_found_error_is_suppressed(delegated_calls):
    unraisable = _unraisable(
        exc_type=SimpleNamespace(__name__="AsyncLibraryNotFoundError"),
        exc_value=SimpleNamespace(),
        exc_traceback=None,
        object=None,
    )
    custom_unraisable_hook(unraisable)
    assert delegated_calls == []


def test_missing_exc_value_delegates(delegated_calls):
    unraisable = _unraisable(exc_value=None, exc_traceback=None, object=None)
    custom_unraisable_hook(unraisable)
    assert delegated_calls == [unraisable]


def _assert_entrypoint_uses_shared_handler(module):
    assert module.sys.unraisablehook is module._custom_unraisable_hook
    recorded = []
    original = sys.__unraisablehook__
    sys.__unraisablehook__ = lambda u: recorded.append(u)
    try:
        reported = _unraisable(
            exc_value=RuntimeError("generator didn't stop after athrow()"),
            exc_traceback=None,
            object=_FakeHttpcoreAsyncGenerator(),
        )
        module._custom_unraisable_hook(reported)
        assert recorded == []
        unrelated = _unraisable(exc_type=ValueError, exc_value=ValueError("boom"))
        module._custom_unraisable_hook(unrelated)
        assert recorded == [unrelated]
    finally:
        sys.__unraisablehook__ = original


def test_main_entrypoint_installs_and_uses_shared_handler():
    import AgentCrew.main as main_module

    _assert_entrypoint_uses_shared_handler(main_module)


def test_main_docker_entrypoint_installs_and_uses_shared_handler():
    import AgentCrew.main_docker as main_docker_module

    _assert_entrypoint_uses_shared_handler(main_docker_module)
