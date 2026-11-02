"""Roadmap item 54: the patch view's type picker is searchable — type a few letters of the
id, manufacturer or model to jump to a definition — without changing what a row saves."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.fixtures.definition import SCHEMA, load_definition  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _definition(fid: str, manufacturer: str, model: str):
    return load_definition({
        "schema": SCHEMA, "id": fid, "manufacturer": manufacturer, "model": model,
        "category": "par", "source": {"kind": "user", "confirmed": True},
        "modes": [{"name": "3ch", "channels": [{"offset": i, "function": f}
                                               for i, f in enumerate(["red", "green", "blue"])]}],
    })


def _tab(definitions=()):
    from livereact.fixtures.library import save_user_definition
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui.rig_config import RigConfigTab
    for d in definitions:
        save_user_definition(d)
    rig = _rig_from_dict({"name": "t", "slots": [
        {"slot_id": "p1", "fixture_type": "rgb_par", "dmx_address": 1, "label": "Par"}]})
    return RigConfigTab(rig, None)


def _type_combo(tab, row=0):
    from livereact.ui.rig_config import C_TYPE
    return tab.table.cellWidget(row, C_TYPE)


def test_type_combo_is_searchable_but_takes_no_new_entries(qapp):
    from PySide6 import QtCore, QtWidgets
    combo = _type_combo(_tab([_definition("acme_beam", "Acme", "Beam 60")]))
    assert combo.isEditable()
    assert combo.insertPolicy() == QtWidgets.QComboBox.InsertPolicy.NoInsert
    c = combo.completer()
    assert c is not None
    assert c.filterMode() == QtCore.Qt.MatchFlag.MatchContains
    assert c.caseSensitivity() == QtCore.Qt.CaseSensitivity.CaseInsensitive


def test_a_few_letters_of_the_model_find_the_definition(qapp):
    combo = _type_combo(_tab([_definition("acme_beam", "Acme", "Beam 60"),
                              _definition("other_par", "Other", "Par 12")]))
    c = combo.completer()
    c.setCompletionPrefix("beam 6")
    assert c.completionCount() == 1
    assert "Acme Beam 60" in c.currentCompletion()


def test_picking_a_completion_selects_that_item(qapp):
    tab = _tab([_definition("acme_beam", "Acme", "Beam 60")])
    combo = _type_combo(tab)
    c = combo.completer()
    c.setCompletionPrefix("acme_beam")
    text = c.currentCompletion()
    c.activated[str].emit(text)
    assert combo.currentText() == text
    assert tab._row_type(0) == ("def", "acme_beam@3ch")


def test_junk_text_keeps_the_last_valid_type(qapp):
    tab = _tab()
    combo = _type_combo(tab)
    before = tab._row_type(0)
    combo.lineEdit().setText("no such fixture")
    assert tab._row_type(0) == before


def test_definitions_added_later_are_searchable_too(qapp):
    tab = _tab()
    combo = _type_combo(tab)
    from livereact.fixtures.library import save_user_definition
    save_user_definition(_definition("late_wash", "Late", "Wash 7"))
    tab.reload_definitions()
    c = combo.completer()
    c.setCompletionPrefix("late")
    assert c.completionCount() == 1
