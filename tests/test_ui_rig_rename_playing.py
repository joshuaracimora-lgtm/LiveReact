"""2026-11-17 (UX_BACKLOG Open 10): renaming the rig that is PLAYING left the running show on the old file.

Rig manager -> Rename… on the rig the show is playing moved the file, but the console kept the old path:
Venue / Rig Config "Save rig…" offered the deleted file name and wrote it back (the old name returned
next to the renamed one, with the edits in the old one), and the old rig name was written into the file.
"""
from __future__ import annotations

import json
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


def _playing_user_rig(qapp):
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    from livereact.ui.rig_manager import copy_demo_rig
    app = App(AppConfig(demo=True, config_path=None))
    win = ConsoleWindow(app)
    dlg = win.open_rig_manager()
    old = copy_demo_rig()
    dlg.choose(old)                                  # the show now plays the user's copy
    assert Path(app.config.rig_path) == old
    return app, win, dlg, old


def test_renaming_the_playing_rig_moves_the_running_show_and_its_save_dialogs(qapp, monkeypatch):
    from PySide6 import QtWidgets
    app, win, dlg, old = _playing_user_rig(qapp)
    dlg._ask_text = lambda _current: "Loft party"
    dlg.rename_selected()
    new = old.with_name("Loft party.json")
    assert new.is_file() and not old.exists()

    assert Path(app.config.rig_path) == new          # the running app knows its rig's file
    assert app.rig.name == "Loft party"              # status bar, delete box and Save write this name
    assert Path(win.venue_tab.rig_path) == new
    assert Path(win.rig_config.rig_path) == new

    offered: list[str] = []

    def fake_save(_parent, _title, default, _filter):
        offered.append(default)
        return default, "JSON (*.json)"

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(fake_save))
    win.venue_tab._save()                            # the user saves a Venue edit with the default name
    assert Path(offered[-1]) == new
    assert not old.exists(), "Save rig brought the old file name back"
    assert json.loads(new.read_text(encoding="utf-8"))["name"] == "Loft party"
    assert "Loft party" in dlg.l_msg.text()
    win.close()


def test_renaming_a_rig_that_is_not_playing_leaves_the_show_alone(qapp):
    from livereact.ui.rig_manager import new_rig_file
    app, win, dlg, old = _playing_user_rig(qapp)
    other = new_rig_file("Spare")
    dlg.settings.add_recent_rig(str(other))
    dlg.settings.save()
    dlg.reload_recent()
    dlg.recent.setCurrentRow(0)                      # "Spare", the most recent
    dlg._ask_text = lambda _current: "Spare two"
    name_before = app.rig.name
    dlg.rename_selected()
    assert other.with_name("Spare two.json").is_file()
    assert Path(app.config.rig_path) == old
    assert app.rig.name == name_before
    assert Path(win.venue_tab.rig_path) == old
    win.close()
