"""Case-preserving consent identity, including simulated Windows path equality."""

import json
import os
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from AgentCrew.modules.config import project_trust as trust
from AgentCrew.setup import ApplicationSetup


@pytest.fixture
def windows_identity(tmp_path, monkeypatch):
    resolved = {}

    class WindowsEqualityPath(type(Path())):
        def __eq__(self, other):
            return str(self).casefold() == str(other).casefold()

        def __ne__(self, other):
            return not self.__eq__(other)

        def __hash__(self):
            return hash(str(self).casefold())

        def resolve(self, strict=False):
            return resolved.get(str(self), self)

    upper = WindowsEqualityPath(tmp_path / "Checkout")
    lower = WindowsEqualityPath(tmp_path / "checkout")
    alias = WindowsEqualityPath(tmp_path / "alias")
    resolved.update({str(upper): upper, str(lower): lower, str(alias): upper})
    monkeypatch.setattr(
        trust,
        "canonical_project_root",
        lambda path=None, exists=True: resolved[str(path)],
    )
    path_api = SimpleNamespace(**vars(os.path))
    path_api.normcase = str.casefold
    monkeypatch.setattr(trust, "os", SimpleNamespace(**{**vars(os), "path": path_api}))
    return upper, lower, alias, resolved


@pytest.fixture
def store(tmp_path):
    return trust.ProjectTrustStore(tmp_path / "user-state", timeout=0.1)


def test_windows_path_equality_is_not_consent_identity(windows_identity):
    upper, lower, _, _ = windows_identity
    assert PureWindowsPath("C:/Projects/Checkout") == PureWindowsPath(
        "c:/projects/checkout"
    )
    assert upper == lower
    assert str(upper) != str(lower)
    assert trust._project_key(upper) == str(upper)
    assert trust._project_key(lower) == str(lower)
    assert trust._project_key(upper) != trust._project_key(lower)


def test_case_distinct_grants_require_separate_consent(windows_identity, store):
    upper, lower, _, _ = windows_identity
    store.grant(upper)
    assert store.is_trusted(upper)
    assert not store.is_trusted(lower)
    prompt = Mock(return_value=False)
    resolver = trust.ProjectTrustResolver(lower, override=None, store=store)
    assert not resolver.resolve(interactive=True, prompt=prompt, diagnostic=Mock())
    prompt.assert_called_once_with(lower)
    store.grant(lower)
    assert store.is_trusted(upper)
    assert store.is_trusted(lower)
    assert json.loads(store.path.read_text()) == {
        "version": 2,
        "projects": sorted([str(upper), str(lower)]),
    }


@pytest.mark.parametrize("revoke_lower", [False, True])
def test_case_distinct_revocation_is_isolated(windows_identity, store, revoke_lower):
    upper, lower, _, _ = windows_identity
    store.grant(upper)
    store.grant(lower)
    revoked, retained = (lower, upper) if revoke_lower else (upper, lower)
    assert store.revoke(revoked)
    assert not store.is_trusted(revoked)
    assert store.is_trusted(retained)


def test_ungranted_case_variant_cannot_revoke_another_root(windows_identity, store):
    upper, lower, _, _ = windows_identity
    store.grant(upper)
    assert not store.revoke(lower)
    assert store.is_trusted(upper)


def test_alias_is_resolved_before_grant_and_revocation(windows_identity, store):
    upper, lower, alias, _ = windows_identity
    prompt = Mock(return_value=True)
    resolver = trust.ProjectTrustResolver(alias, override=None, store=store)
    assert resolver.resolve(interactive=True, prompt=prompt, diagnostic=Mock())
    prompt.assert_called_once_with(upper)
    store.grant(lower)
    assert store.revoke(alias)
    assert not store.is_trusted(upper)
    assert store.is_trusted(lower)


def test_case_only_redirect_refuses_lookup_and_grant(windows_identity, store):
    upper, lower, _, resolved = windows_identity
    store.grant(upper)
    before = store.path.read_bytes()
    resolved[str(upper)] = lower
    assert upper.resolve() == upper
    assert not trust.project_root_is_unchanged(upper)
    with pytest.raises(trust.ProjectTrustError, match="changed"):
        store.is_trusted(upper)
    with pytest.raises(trust.ProjectTrustError, match="changed"):
        store.grant(upper)
    assert store.path.read_bytes() == before


def test_case_only_redirect_during_prompt_aborts_startup(windows_identity, store):
    upper, lower, _, resolved = windows_identity
    setup = ApplicationSetup.__new__(ApplicationSetup)
    setup.services = None
    setup._plugins_initialized = False
    setup._project_root = upper
    setup._trusted_project = False
    setup.plugin_manager = Mock()
    original_manager = setup.plugin_manager
    setup._project_trust_resolver = trust.ProjectTrustResolver(
        upper, override=None, store=store
    )

    def redirect(root):
        assert str(root) == str(upper)
        resolved[str(upper)] = lower
        return True

    prompt = Mock(side_effect=redirect)
    messages = []
    with pytest.raises(RuntimeError, match="Launch project path changed"):
        setup.resolve_interactive_project_trust(
            interactive=True, prompt=prompt, diagnostic=messages.append
        )
    prompt.assert_called_once_with(upper)
    assert not setup._trusted_project
    assert setup.plugin_manager is original_manager
    assert not store.path.exists()
    assert messages


@pytest.mark.parametrize("folded", [False, True])
def test_version_one_grants_never_authorize_case_variants(
    windows_identity, store, folded
):
    upper, lower, _, _ = windows_identity
    store.directory.mkdir(mode=0o700)
    legacy = str(upper).casefold() if folded else str(upper)
    store.path.write_text(json.dumps({"version": 1, "projects": [legacy]}))
    store.path.chmod(0o600)
    before = store.path.read_bytes()
    prompt = Mock(return_value=True)
    messages = []
    for root in (upper, lower):
        resolver = trust.ProjectTrustResolver(root, override=None, store=store)
        assert not resolver.resolve(
            interactive=True, prompt=prompt, diagnostic=messages.append
        )
    prompt.assert_not_called()
    assert len(messages) == 2
    assert all("version 1" in message.casefold() for message in messages)
    assert store.path.read_bytes() == before
    store.path.rename(store.path.with_suffix(".legacy"))
    resolver = trust.ProjectTrustResolver(upper, override=None, store=store)
    assert resolver.resolve(interactive=True, prompt=prompt, diagnostic=Mock())
    assert store.is_trusted(upper)
    assert not store.is_trusted(lower)
    assert json.loads(store.path.read_text())["version"] == 2


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows filesystem")
def test_native_windows_case_sensitive_checkout_isolation(tmp_path):
    upper = tmp_path / "Checkout"
    lower = tmp_path / "checkout"
    upper.mkdir()
    try:
        lower.mkdir()
    except FileExistsError:
        pytest.skip("Temporary directory is not case-sensitive")
    upper = trust.canonical_project_root(upper)
    lower = trust.canonical_project_root(lower)
    assert str(upper) != str(lower)
    store = trust.ProjectTrustStore(tmp_path / "user-state")
    store.grant(upper)
    assert not store.is_trusted(lower)
    store.grant(lower)
    assert store.revoke(upper)
    assert store.is_trusted(lower)
