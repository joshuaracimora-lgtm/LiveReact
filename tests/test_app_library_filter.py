"""Roadmap item 49: a filter box above the fixture library table (ui/fixture_library.py).

Typing hides every row whose manufacturer, model, id or category does not contain the text
(case-insensitive); clearing it shows everything; a selection that gets filtered away is
dropped, so the details pane and the buttons never act on a row the user cannot see.
"""
from __future__ import annotations

import os

import pytest

from livereact.fixtures import library
from livereact.fixtures.definition import SCHEMA, load_definition

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _save(id_, manufacturer, model, category):
    library.save_user_definition(load_definition({
        "schema": SCHEMA, "id": id_, "manufacturer": manufacturer, "model": model,
        "category": category, "source": {"kind": "user"},
        "modes": [{"name": "1ch", "channels": [{"offset": 0, "function": "dimmer"}]}]}))


def _visible_ids(b):
    return [e.definition.id for r, e in enumerate(b.entries) if not b.table.isRowHidden(r)]


def _browser():
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    _save("acme_par", "Acme", "Par 64", "par")
    _save("zeta_wash", "Zeta", "Wash Bar", "wash_bar")
    return FixtureLibraryBrowser()


def test_the_filter_box_is_above_the_table_and_empty(qapp):
    b = _browser()
    assert b.filter.text() == "" and b.filter.placeholderText()
    assert len(_visible_ids(b)) == len(b.entries)


@pytest.mark.parametrize("text, want", [("acme", "acme_par"), ("WASH BAR", "zeta_wash"),
                                        ("zeta_w", "zeta_wash"), ("Par 6", "acme_par")])
def test_typing_matches_manufacturer_model_id_or_category(qapp, text, want):
    b = _browser()
    b.filter.setText(text)
    assert want in _visible_ids(b)
    other = {"acme_par": "zeta_wash", "zeta_wash": "acme_par"}[want]
    assert other not in _visible_ids(b)


def test_clearing_shows_everything_again(qapp):
    b = _browser()
    b.filter.setText("acme")
    assert len(_visible_ids(b)) < len(b.entries)
    b.filter.clear()
    assert len(_visible_ids(b)) == len(b.entries)


def test_a_filtered_away_selection_is_dropped(qapp):
    b = _browser()
    ids = [e.definition.id for e in b.entries]
    b.table.selectRow(ids.index("acme_par"))
    assert b.b_test.isEnabled() and "Acme" in b.details.toPlainText()
    b.filter.setText("acme")                      # still visible: selection kept
    assert b.b_test.isEnabled()
    b.filter.setText("zeta")                      # filtered away: nothing selected
    assert not b.b_test.isEnabled() and b.details.toPlainText() == ""


def test_the_filter_survives_a_refresh(qapp):
    b = _browser()
    b.filter.setText("zeta")
    _save("acme_spot", "Acme", "Spot", "moving_head")
    b.reload()
    assert _visible_ids(b) == ["zeta_wash"]


def test_no_match_says_so(qapp):
    b = _browser()
    b.filter.setText("no such fixture xyz")
    assert _visible_ids(b) == []
    assert "No fixture matches" in b.l_filter.text()
    b.filter.clear()
    assert b.l_filter.text() == ""
