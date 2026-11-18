"""2026-11-18 (UX_BACKLOG Open 10): deleting the rig that is PLAYING said nothing about it.

Rig manager -> Delete… on the rig the show is playing removed the file with the same confirm text as any
other rig. The show keeps playing it from memory, and the next Venue / Rig Config "Save rig…" offers the
deleted name and writes it back, so the user who meant "get rid of this rig" finds it again later.
The confirm box now says it is the playing rig and what happens next; other rigs keep the plain text.
"""
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


def _playing_user_rig():
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    from livereact.ui.rig_manager import copy_demo_rig
    app = App(AppConfig(demo=True, config_path=None))
    win = ConsoleWindow(app)
    dlg = win.open_rig_manager()
    playing = copy_demo_rig()
    dlg.choose(playing)                              # the show now plays the user's copy
    assert Path(app.config.rig_path) == playing
    return app, win, dlg, playing


def test_deleting_the_playing_rig_says_it_is_playing_before_it_deletes(qapp):
    app, win, dlg, playing = _playing_user_rig()
    asked: list[str] = []
    dlg._confirm = lambda text: asked.append(text) or False
    assert dlg._selected_path() and Path(dlg._selected_path()) == playing
    dlg.delete_selected()
    assert len(asked) == 1
    assert "playing now" in asked[0], asked[0]
    assert "Save rig" in asked[0], asked[0]          # the way the deleted file comes back
    assert playing.is_file(), "declining must delete nothing"
    win.close()


def test_deleting_the_playing_rig_keeps_the_show_running_on_it(qapp):
    app, win, dlg, playing = _playing_user_rig()
    rig_before, n_before = app.rig, len(app.rig.fixtures)
    dlg._confirm = lambda text: True
    dlg.delete_selected()
    assert not playing.exists()
    assert app.rig is rig_before and len(app.rig.fixtures) == n_before   # what the confirm box promised
    win.close()


def test_deleting_a_rig_that_is_not_playing_keeps_the_plain_text(qapp):
    from livereact.ui.rig_manager import new_rig_file
    app, win, dlg, playing = _playing_user_rig()
    spare = new_rig_file("Spare")
    dlg.settings.add_recent_rig(str(spare))
    dlg.settings.save()
    dlg.reload_recent()
    dlg.recent.setCurrentRow(0)                      # "Spare", the most recent
    assert Path(dlg._selected_path()) == spare
    asked: list[str] = []
    dlg._confirm = lambda text: asked.append(text) or True
    dlg.delete_selected()
    assert len(asked) == 1 and "playing now" not in asked[0]
    assert not spare.exists() and playing.is_file()
    win.close()


def test_a_rig_manager_opened_on_its_own_still_deletes(qapp):
    """No console, so nothing is playing: the dialog must not need the new argument."""
    from livereact.settings import Settings
    from livereact.ui.rig_manager import RigManagerDialog, new_rig_file
    spare = new_rig_file("Alone")
    s = Settings.load()
    s.add_recent_rig(str(spare))
    asked: list[str] = []
    dlg = RigManagerDialog(settings=s, confirm=lambda text: asked.append(text) or True)
    dlg.delete_selected()
    assert asked and "playing now" not in asked[0]
    assert not spare.exists()
