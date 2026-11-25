"""Native offscreen dialogs and plugin import-boundary checks; no plugin executes."""

import asyncio
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from AgentCrew.app import _gui_application
from AgentCrew.modules.config import project_trust as trust
from AgentCrew.modules.events.plugin_system import PluginManager
from AgentCrew.modules.skills.service import SkillsService


@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    original = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = _gui_application()
    yield application
    if original is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = original


@pytest.mark.parametrize("choice", ["accept", "decline", "close"])
def test_native_dialog_is_plain_text_and_default_safe(qt_app, tmp_path, choice):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    root = tmp_path / "project <not markup>"
    root.mkdir()

    def inspect_and_choose(dialog):
        assert dialog.textFormat() == Qt.TextFormat.PlainText
        assert dialog.text() == trust.project_trust_message(root)
        assert "project plugins can run local code" in dialog.text().casefold()
        assert dialog.defaultButton().text() == "Continue without trust"
        assert dialog.escapeButton() is dialog.defaultButton()
        if choice == "close":
            dialog.reject()
        else:
            label = (
                "Trust this project" if choice == "accept" else "Continue without trust"
            )
            next(
                button for button in dialog.buttons() if button.text() == label
            ).click()
        return 0

    with patch.object(QMessageBox, "exec", inspect_and_choose):
        assert trust.confirm_project_trust_qt(root) is (choice == "accept")
    assert _gui_application() is qt_app


def test_native_offscreen_event_loop_closes_without_trust(qt_app, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    def close_dialog():
        for window in QApplication.topLevelWidgets():
            if isinstance(window, QMessageBox):
                window.reject()

    QTimer.singleShot(0, close_dialog)
    QTimer.singleShot(2000, close_dialog)
    assert trust.confirm_project_trust_qt(tmp_path) is False
    assert QApplication.instance() is qt_app
    assert _gui_application() is qt_app


def test_gui_unavailable_does_not_construct_application(monkeypatch, qt_app):
    from PySide6 import QtWidgets

    application = Mock()
    application.instance.return_value = None
    monkeypatch.setattr(QtWidgets, "QApplication", application)
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(name, raising=False)
    with (
        patch("AgentCrew.app.sys.platform", "linux"),
        pytest.raises(RuntimeError, match="display"),
    ):
        _gui_application()
    application.assert_not_called()


@pytest.mark.parametrize("accepted", [False, True])
def test_consent_gates_skills_and_plugin_import_without_executing_code(
    tmp_path, accepted
):
    root = tmp_path / "project"
    skill = root / ".agents/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: local\ndescription: Local project skill\n---\nPrivate instructions\n"
    )
    plugin = root / ".agentcrew/plugins/local_plugin.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        "raise AssertionError('Plugin code must never execute in this test')\n"
    )
    store = trust.ProjectTrustStore(tmp_path / "state")
    resolver = trust.ProjectTrustResolver(root, None, store=store)
    trusted = resolver.resolve(
        interactive=True, prompt=lambda _: accepted, diagnostic=lambda _: None
    )
    skills = SkillsService(
        project_root=root, user_root=tmp_path / "user", trusted_project=trusted
    )
    assert ("local" in skills.get_skill_names()) is accepted
    manager = PluginManager(
        project_plugins_dir=str(root / ".agentcrew/plugins"),
        global_plugins_dir=str(tmp_path / "user-plugins"),
        trusted_project_plugins=trusted,
    )
    manager.discover()
    with patch.object(
        manager, "_load_module_from_source", return_value=None
    ) as importer:
        assert asyncio.run(manager.load("local_plugin")) is None
        assert importer.call_count == int(accepted)
    assert plugin.read_text().startswith("raise AssertionError")


def test_prompt_path_representation_cannot_control_terminal(tmp_path):
    root = Path(str(tmp_path) + "/project\x1b[2J\nnext")
    message = trust.project_trust_message(root)
    assert "\x1b" not in message
    assert "\\u001b" in message
    assert "\\nnext" in message
