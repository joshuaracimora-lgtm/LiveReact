"""Roadmap item 42: the console's Help menu -- About, and "Run first-time setup again" (saving only)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


_OPEN: list = []


@pytest.fixture(autouse=True)
def _delete_dialogs(qapp):
    # a dialog still alive when Python exits crashes the interpreter (see test_app_first_run.py)
    yield
    from PySide6 import QtCore
    while _OPEN:
        d = _OPEN.pop()
        d.close()
        d.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def _win():
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    app = App(AppConfig(demo=True, config_path=None))
    return ConsoleWindow(app), app


def _menus(win):
    return {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions()}


def test_help_menu_entries(qapp):
    win, _ = _win()
    menus = _menus(win)
    assert "Help" in menus
    assert [a.text() for a in menus["Help"].actions()] == ["About LiveReact…", "Run first-time setup again…"]


def test_about_opens_the_about_dialog(qapp):
    from livereact.ui.about import AboutDialog
    win, _ = _win()
    dlg = win.open_about()
    _OPEN.append(dlg)
    assert isinstance(dlg, AboutDialog) and dlg.parent() is win
    assert "LiveReact" in dlg.text.toPlainText()


def test_help_about_action_triggers_the_dialog(qapp):
    win, _ = _win()
    action = _menus(win)["Help"].actions()[0]
    action.trigger()
    _OPEN.append(win.about_dialog)
    assert win.about_dialog.isVisible()


def test_first_run_again_shows_the_saved_choices_and_saves_only(qapp):
    from livereact.settings import Settings
    from livereact.ui.first_run import SETUP_KEY, FirstRunDialog
    s = Settings.load()
    s.extra[SETUP_KEY] = {"output": "artnet", "audio_device": "Mic", "pdl_interface": "none",
                          "start_with": "demo_rig"}
    s.onboarding_done = True
    s.save()
    win, app = _win()
    before = app.config
    dlg = win.open_first_run(audio_provider=lambda: ["Mic"], interface_provider=lambda: ["Ethernet"])
    _OPEN.append(dlg)
    assert isinstance(dlg, FirstRunDialog)
    assert dlg.choices()["output"] == "artnet" and dlg.choices()["audio_device"] == "Mic"
    dlg.output_buttons["sacn"].setChecked(True)
    dlg.finish()
    assert Settings.load().extra[SETUP_KEY]["output"] == "sacn"
    assert app.config is before                      # nothing applied live (item 40)
    assert "next start" in win.statusBar().currentMessage().lower()
