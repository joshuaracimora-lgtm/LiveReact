"""Roadmap item 43: Help -> Keyboard shortcuts, listed from the same table the console installs."""
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


def test_help_menu_lists_keyboard_shortcuts(qapp):
    win, _ = _win()
    menus = {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions()}
    assert "Keyboard shortcuts…" in [a.text() for a in menus["Help"].actions()]


def test_the_table_is_what_the_console_installs(qapp):
    from PySide6 import QtGui
    from livereact.ui.console import SHORTCUTS
    win, _ = _win()
    installed = sorted(s.key().toString() for s in win.findChildren(QtGui.QShortcut))
    assert installed == sorted(QtGui.QKeySequence(k).toString() for k, _t, _d in SHORTCUTS)
    assert {k for k, _t, _d in SHORTCUTS} >= {"B", "F", "Right", "T", "S", "L", "K", "J", "C", "D", "Space"}


def test_installed_keys_still_drive_their_controls(qapp):
    from PySide6 import QtGui
    win, app = _win()
    by_key = {s.key().toString(): s for s in win.findChildren(QtGui.QShortcut)}
    by_key["B"].activated.emit()
    assert win.b_black.isChecked() and app.manual.blackout
    by_key["L"].activated.emit()
    assert win.cb_laser.isChecked() == app.manual.laser_enable


def test_dialog_lists_every_key_with_what_it_does(qapp):
    from livereact.ui.console import SHORTCUTS
    win, _ = _win()
    dlg = win.open_shortcuts()
    _OPEN.append(dlg)
    text = dlg.text.toPlainText()
    for key, _target, what in SHORTCUTS:
        assert key in text and what in text
    assert "Blackout" in text and "Arm" in text
