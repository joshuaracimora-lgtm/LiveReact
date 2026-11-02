"""Roadmap item 50: rename or delete a user rig from the rig manager."""
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


def _dialog(ask_text=None, confirm=None):
    from livereact.ui.rig_manager import RigManagerDialog
    d = RigManagerDialog(ask_text=ask_text, confirm=confirm)
    got: list[str] = []
    d.rig_chosen.connect(got.append)
    return d, got


# --------------------------------------------------------------- the functions

def test_rename_moves_the_file_and_updates_the_rig_name(qapp):
    from livereact.fixtures.rig import load_rig
    from livereact.ui.rig_manager import new_rig_file, rename_rig_file
    old = new_rig_file("Garage")
    new = rename_rig_file(old, "Loft party")
    assert not old.exists() and new.is_file()
    assert new.parent == old.parent and new.stem == "Loft party"
    assert json.loads(new.read_text(encoding="utf-8"))["name"] == "Loft party"
    assert load_rig(str(new)).fixtures == []


def test_rename_refuses_the_demo_rig_bad_names_and_existing_targets(qapp):
    from livereact.ui.rig_manager import demo_rig_path, new_rig_file, rename_rig_file
    with pytest.raises(ValueError):
        rename_rig_file(demo_rig_path(), "mine")
    a = new_rig_file("A")
    new_rig_file("B")
    with pytest.raises(ValueError):
        rename_rig_file(a, "B")        # the target already exists
    for bad in ("", "  ", "a/b", "a\\b", "x:y"):
        with pytest.raises(ValueError):
            rename_rig_file(a, bad)
    assert a.is_file()                 # nothing happened


def test_rename_refuses_files_outside_the_user_rigs_folder(qapp, tmp_path):
    from livereact.ui.rig_manager import rename_rig_file
    outside = tmp_path / "elsewhere.json"
    outside.write_text('{"name": "x", "slots": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        rename_rig_file(outside, "renamed")
    assert outside.is_file()


def test_delete_removes_a_user_rig_and_refuses_everything_else(qapp, tmp_path):
    from livereact.ui.rig_manager import delete_rig_file, demo_rig_path, new_rig_file
    p = new_rig_file("Doomed")
    delete_rig_file(p)
    assert not p.exists()
    with pytest.raises(ValueError):
        delete_rig_file(demo_rig_path())
    assert demo_rig_path().is_file()
    outside = tmp_path / "elsewhere.json"
    outside.write_text('{"name": "x", "slots": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        delete_rig_file(outside)
    assert outside.is_file()


# ------------------------------------------------------------------ the dialog

def test_rename_button_renames_the_selected_recent_rig_in_place(qapp):
    from livereact.settings import Settings
    d, got = _dialog(ask_text=lambda current: "First renamed")
    d.b_new.click()      # recent: [got0]
    d.b_new.click()      # recent: [got1, got0]
    d.recent.setCurrentRow(1)
    d.b_rename.click()
    recents = Settings.load().recent_rigs
    assert len(recents) == 2
    assert recents[0] == got[1]                       # position kept
    assert Path(recents[1]).stem == "First renamed"
    assert not Path(got[0]).exists() and Path(recents[1]).is_file()
    shown = [d.recent.item(i).text() for i in range(d.recent.count())]
    assert any("First renamed" in t for t in shown)


def test_cancelled_rename_changes_nothing(qapp):
    from livereact.settings import Settings
    d, got = _dialog(ask_text=lambda current: "")
    d.b_new.click()
    d.b_rename.click()
    assert Settings.load().recent_rigs == [got[0]] and Path(got[0]).is_file()


def test_delete_button_asks_then_deletes_and_forgets(qapp):
    from livereact.settings import Settings
    asked: list[str] = []
    d, got = _dialog(confirm=lambda text: asked.append(text) or True)
    d.b_new.click()
    d.recent.setCurrentRow(0)
    d.b_delete.click()
    assert len(asked) == 1 and Path(got[0]).stem in asked[0]
    assert not Path(got[0]).exists()
    assert Settings.load().recent_rigs == []
    assert d.recent.count() == 0


def test_declined_delete_changes_nothing(qapp):
    from livereact.settings import Settings
    d, got = _dialog(confirm=lambda text: False)
    d.b_new.click()
    d.b_delete.click()
    assert Path(got[0]).is_file() and Settings.load().recent_rigs == [got[0]]


def test_buttons_follow_what_the_selection_allows(qapp):
    d, got = _dialog()
    assert not d.b_rename.isEnabled() and not d.b_delete.isEnabled()   # nothing listed
    d.b_demo.click()     # demo rig: listed but not editable
    d.recent.setCurrentRow(0)
    assert not d.b_rename.isEnabled() and not d.b_delete.isEnabled()
    d.b_new.click()      # a user rig: editable
    d.recent.setCurrentRow(0)
    assert d.b_rename.isEnabled() and d.b_delete.isEnabled()
    d.recent.setCurrentRow(1)
    assert not d.b_rename.isEnabled() and not d.b_delete.isEnabled()
