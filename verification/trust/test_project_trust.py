"""Human consent records cannot be supplied by project configuration."""

import errno
import io
import json
import multiprocessing
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from AgentCrew.modules.config import project_trust as trust
from AgentCrew.setup import common_options


@pytest.fixture
def roots(tmp_path):
    project = tmp_path / "project"
    sibling = tmp_path / "sibling"
    child = project / "child"
    child.mkdir(parents=True)
    sibling.mkdir()
    return project, sibling, child


@pytest.fixture
def store(tmp_path):
    return trust.ProjectTrustStore(tmp_path / "user-state", timeout=0.05)


def resolve(root, store, *, accept=False, interactive=True, override=None):
    prompt = Mock(return_value=accept)
    messages = []
    resolver = trust.ProjectTrustResolver(root, override, store)
    result = resolver.resolve(
        interactive=interactive, prompt=prompt, diagnostic=messages.append
    )
    return result, resolver, prompt, messages


def test_default_decline_is_once_for_launch_and_not_persisted(roots, store):
    project, _, _ = roots
    result, resolver, prompt, messages = resolve(project, store)
    assert not result
    assert not resolver.resolve(
        interactive=True, prompt=Mock(return_value=True), diagnostic=messages.append
    )
    prompt.assert_called_once_with(project.resolve())
    assert not store.path.exists()
    assert not messages
    assert resolve(project, store)[2].call_count == 1


def test_accept_restart_and_exact_root_isolation(roots, store):
    project, sibling, child = roots
    result, _, prompt, _ = resolve(project, store, accept=True)
    assert result
    prompt.assert_called_once_with(project.resolve())
    assert json.loads(store.path.read_text())["projects"] == [
        trust._project_key(project.resolve())
    ]
    restarted, _, prompt, _ = resolve(project, store)
    assert restarted
    prompt.assert_not_called()
    assert not resolve(sibling, store)[0]
    assert not resolve(child, store)[0]
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700


def test_alias_resolves_to_the_same_displayed_and_persisted_root(
    roots, store, tmp_path
):
    project, _, _ = roots
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    result, _, prompt, _ = resolve(alias, store, accept=True)
    assert result
    prompt.assert_called_once_with(project.resolve())
    assert resolve(project, store)[0]
    assert json.loads(store.path.read_text())["projects"] == [
        trust._project_key(project.resolve())
    ]


@pytest.mark.parametrize("override", [True, False])
@pytest.mark.parametrize("interactive", [True, False])
def test_explicit_overrides_do_not_read_or_persist(roots, override, interactive):
    project, _, _ = roots
    store = Mock()
    result, _, prompt, _ = resolve(
        project, store, accept=True, override=override, interactive=interactive
    )
    assert result is override
    prompt.assert_not_called()
    store.is_trusted.assert_not_called()
    store.grant.assert_not_called()


def test_headless_ignores_an_interactive_grant(roots, store):
    project, _, _ = roots
    store.grant(project)
    with patch.object(store, "is_trusted", side_effect=AssertionError("must not read")):
        assert not resolve(project, store, interactive=False, accept=True)[0]


@pytest.mark.parametrize(
    "error", [EOFError(), click.Abort(), RuntimeError("No UI"), ImportError("No Qt")]
)
def test_closed_or_unavailable_prompt_fails_closed(roots, store, error):
    project, _, _ = roots
    prompt = Mock(side_effect=error)
    messages = []
    resolver = trust.ProjectTrustResolver(project, None, store)
    assert not resolver.resolve(
        interactive=True, prompt=prompt, diagnostic=messages.append
    )
    assert not store.path.exists()
    assert messages
    assert not resolver.resolve(
        interactive=True, prompt=prompt, diagnostic=messages.append
    )
    prompt.assert_called_once()


@pytest.mark.parametrize(
    "data",
    [
        "not json",
        "[]",
        json.dumps({"version": True, "projects": []}),
        json.dumps({"version": 2, "projects": []}),
        json.dumps({"version": 1, "projects": ["relative/path"]}),
        json.dumps({"version": 1, "projects": [], "trust_all": True}),
        " " * 65537,
    ],
)
def test_corrupt_or_oversized_store_never_prompts_or_grants(roots, store, data):
    project, _, _ = roots
    store.directory.mkdir(mode=0o700)
    store.path.write_text(data)
    store.path.chmod(0o600)
    result, _, prompt, messages = resolve(project, store, accept=True)
    assert not result
    prompt.assert_not_called()
    assert messages
    assert store.path.read_text() == data


@pytest.mark.parametrize("artifact", ["directory", "store", "lock"])
def test_linked_storage_is_rejected_including_dangling_links(
    roots, store, tmp_path, artifact
):
    project, _, _ = roots
    outside = tmp_path / "outside"
    outside.mkdir()
    if artifact == "directory":
        store.directory.symlink_to(outside, target_is_directory=True)
    else:
        store.directory.mkdir(mode=0o700)
        path = store.path if artifact == "store" else store.lock_path
        path.symlink_to(outside / "missing")
    result, _, prompt, messages = resolve(project, store, accept=True)
    assert not result
    prompt.assert_not_called()
    assert messages
    assert not list(outside.iterdir())


def test_hardlinked_and_unsafe_mode_store_are_rejected(roots, store, tmp_path):
    project, _, _ = roots
    store.grant(project)
    linked = tmp_path / "linked"
    os.link(store.path, linked)
    assert not resolve(project, store)[0]
    linked.unlink()
    if os.name != "nt":
        store.path.chmod(0o644)
        assert not resolve(project, store)[0]
        store.path.chmod(0o600)
        store.directory.chmod(0o777)
        assert not resolve(project, store)[0]


def test_checkout_cannot_supply_its_own_consent(roots):
    project, _, _ = roots
    store = trust.ProjectTrustStore(project / "user-state")
    result, _, prompt, messages = resolve(project, store, accept=True)
    assert not result
    prompt.assert_not_called()
    assert "outside the launch project" in messages[0]


def test_atomic_write_failure_leaves_old_records_and_trust_off(roots, store):
    project, sibling, _ = roots
    store.grant(sibling)
    before = store.path.read_bytes()
    with patch.object(
        trust.os, "replace", side_effect=PermissionError("store unavailable")
    ):
        result, _, prompt, messages = resolve(project, store, accept=True)
    assert not result
    prompt.assert_called_once()
    assert messages
    assert store.path.read_bytes() == before
    assert not list(store.directory.glob(".project-trust-*"))
    assert not store.is_trusted(project)
    assert store.is_trusted(sibling)


def test_bounded_lock_failure_is_diagnostic(roots, store):
    project, sibling, _ = roots
    store.grant(sibling)
    with patch.object(
        store, "_lock_fd", side_effect=BlockingIOError(errno.EAGAIN, "busy")
    ):
        result, _, prompt, messages = resolve(project, store, accept=True)
    assert not result
    prompt.assert_not_called()
    assert "Timed out" in messages[0]


def test_revoke_and_restart_through_shared_cli(roots, store):
    project, _, _ = roots
    store.grant(project)
    with patch.object(trust, "ProjectTrustStore", return_value=store):
        result = CliRunner().invoke(trust.revoke_project_trust, [str(project)])
    assert result.exit_code == 0, result.output
    assert "Revoked trust" in result.output
    assert not resolve(project, store)[0]


def test_oversized_grant_is_not_persisted(roots, store):
    project, _, _ = roots
    with patch.object(trust, "_project_key", return_value="/" + "x" * 4096):
        value, _, prompt, messages = resolve(project, store, accept=True)
    assert not value
    prompt.assert_called_once_with(project)
    assert not store.path.exists()
    assert any("limit" in message for message in messages)


def test_threaded_read_modify_write_preserves_all_grants(roots, store):
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(
            pool.map(
                lambda root: trust.ProjectTrustStore(store.directory).grant(root), roots
            )
        )
    assert all(store.is_trusted(root) for root in roots)


def _grant_process(directory, root, ready, go):
    ready.put(True)
    assert go.wait(10)
    trust.ProjectTrustStore(Path(directory)).grant(Path(root))


def test_cross_process_read_modify_write(roots, store):
    context = multiprocessing.get_context("spawn")
    ready, go = context.Queue(), context.Event()
    children = [
        context.Process(
            target=_grant_process, args=(str(store.directory), str(root), ready, go)
        )
        for root in roots[:2]
    ]
    try:
        for child in children:
            child.start()
        for _ in children:
            assert ready.get(timeout=10)
        go.set()
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        assert all(store.is_trusted(root) for root in roots[:2])
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(3)


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ([], None),
        (["--trusted-project-plugins"], True),
        (["--no-trusted-project-plugins"], False),
    ],
)
def test_common_cli_flag_is_tri_state(arguments, expected):
    seen = []

    @click.command()
    @common_options
    def command(**kwargs):
        seen.append(kwargs["trusted_project_plugins"])

    result = CliRunner().invoke(command, arguments)
    assert result.exit_code == 0, result.output
    assert seen == [expected]


def test_console_prompt_default_and_warning(roots):
    project, _, _ = roots
    with (
        patch.object(click, "confirm", return_value=False) as confirm,
        patch.object(click, "echo") as echo,
    ):
        assert not trust.confirm_project_trust_console(project)
    assert confirm.call_args.kwargs == {"default": False, "err": True}
    message = echo.call_args.args[0]
    assert str(project) in message
    assert "project plugins can run local code" in message.lower()
    assert "Continue without trust" in confirm.call_args.args[0]


@pytest.mark.parametrize(
    "stdin, stderr, expected",
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_console_requires_visible_terminal(monkeypatch, stdin, stderr, expected):
    class Stream(io.StringIO):
        def __init__(self, terminal):
            super().__init__()
            self.terminal = terminal

        def isatty(self):
            return self.terminal

    monkeypatch.setattr(trust.sys, "stdin", Stream(stdin))
    monkeypatch.setattr(trust.sys, "stderr", Stream(stderr))
    assert trust.console_can_prompt() is expected
