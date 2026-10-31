"""Roadmap item 52: the console's Rig menu lists the recent rigs directly."""
from __future__ import annotations

import os
from pathlib import Path

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


def _console():
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    app = App(AppConfig(demo=True, config_path=None))
    return app, ConsoleWindow(app)


def _remember(*paths):
    from livereact.settings import Settings
    s = Settings.load()
    for p in paths:
        s.add_recent_rig(p)
    s.save()


def _texts(win):
    return [a.text() for a in win.rig_menu.actions() if not a.isSeparator()]


def test_rig_menu_lists_recent_rigs_above_the_manager(qapp):
    from livereact.ui.rig_manager import new_rig_file
    a, b = new_rig_file("Garage"), new_rig_file("Loft")
    _remember(a, b)                       # b is most recent
    app, win = _console()
    texts = _texts(win)
    assert texts[:2] == ["Loft", "Garage"]
    assert texts[-1] == "Rig manager…"
    assert any(x.isSeparator() for x in win.rig_menu.actions())


def test_missing_recent_files_are_hidden_and_menu_refreshes(qapp):
    from livereact.ui.rig_manager import new_rig_file
    _remember("C:/nowhere/gone.json")
    app, win = _console()
    assert _texts(win) == ["Rig manager…"]
    p = new_rig_file("Fresh")             # saved after the console opened
    _remember(p)
    win.rig_menu.aboutToShow.emit()       # opening the menu rebuilds it
    assert _texts(win)[:1] == ["Fresh"]


def test_choosing_a_recent_rig_switches_live_when_the_app_can(qapp):
    from livereact.settings import Settings
    from livereact.ui.rig_manager import new_rig_file
    a, b = new_rig_file("Garage"), new_rig_file("Loft")
    _remember(a, b)
    app, win = _console()
    switched: list[str] = []
    app.switch_rig = switched.append
    win.rig_menu.actions()[1].trigger()   # "Garage", the older one
    assert switched == [str(a)]
    assert Settings.load().recent_rigs[0] == str(a)   # moved to the top


def test_choosing_a_recent_rig_says_restart_without_switch_rig(qapp):
    from livereact.ui.rig_manager import new_rig_file
    _remember(new_rig_file("Garage"))
    app, win = _console()
    win.rig_menu.actions()[0].trigger()
    assert "restart" in win.statusBar().currentMessage().lower()


def test_a_rig_gone_bad_is_refused_from_the_menu(qapp):
    from livereact.ui.rig_manager import new_rig_file
    p = new_rig_file("Flaky")
    _remember(p)
    app, win = _console()
    Path(p).write_text('{"slots": [{"fixture_type": "nope", "dmx_address": 1}]}', encoding="utf-8")
    switched: list[str] = []
    app.switch_rig = switched.append
    win.rig_menu.actions()[0].trigger()
    assert switched == []
    assert "cannot use" in win.statusBar().currentMessage().lower()
