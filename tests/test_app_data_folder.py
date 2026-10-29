"""Roadmap item 44: Help -> Open my LiveReact folder (the user data folder, in Explorer / Finder)."""
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


def _win(opened, ok=True):
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    win = ConsoleWindow(App(AppConfig(demo=True, config_path=None)))
    win.open_url = lambda url: opened.append(url) or ok      # never the real file browser in a test
    return win


def _help(win):
    menus = {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions()}
    return {a.text(): a for a in menus["Help"].actions()}


def test_help_menu_has_the_folder_entry(qapp):
    assert "Open my LiveReact folder…" in _help(_win([]))


def test_the_entry_opens_the_user_data_folder(qapp, home):
    opened = []
    win = _win(opened)
    _help(win)["Open my LiveReact folder…"].trigger()
    assert len(opened) == 1 and opened[0].isLocalFile()
    assert Path(opened[0].toLocalFile()).resolve() == home.resolve()
    assert home.is_dir()


def test_a_failed_open_says_where_the_folder_is(qapp, home):
    win = _win([], ok=False)
    assert win.open_data_folder() == home
    msg = win.statusBar().currentMessage()
    assert "could not open" in msg.lower() and str(home) in msg
