"""Roadmap item 46: duplicate a fixture definition (fixtures/library.py copy_definition +
"Duplicate…" in ui/fixture_library.py). A copy is always saved unconfirmed, so an edited copy
of a confirmed model goes back through the channel-test screen before a laser may use it."""
from __future__ import annotations

import os

import pytest

from livereact import userdata
from livereact.fixtures import library
from livereact.fixtures.definition import SCHEMA, load_definition


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _raw(fid="acme_par", kind="user", **source_extra):
    return {"schema": SCHEMA, "id": fid, "manufacturer": "Acme", "model": "Par", "category": "par",
            "source": {"kind": kind, **source_extra},
            "modes": [{"name": "4ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                   {"offset": 1, "function": "red"}]}]}


# --- fixtures/library.py ---

def test_copy_a_builtin_into_the_user_folder():
    p = library.copy_definition("rgb_par", "my_par")
    assert p == userdata.paths().fixtures / "my_par.json"
    d, _ = library.resolve("my_par")
    src, _ = library.resolve("rgb_par")
    assert d.id == "my_par" and d.modes == src.modes and d.model == src.model
    assert d.source.kind == "user"                       # a copy is the user's, not "builtin"
    assert d.source.confirmed is False and d.source.confirmed_at is None


def test_copy_keeps_the_import_provenance_but_not_the_confirmation():
    library.save_user_definition(load_definition(_raw(
        kind="ofl", ofl_key="acme/par", attribution="OFL", confirmed=True, confirmed_at="2026-10-28")))
    library.copy_definition("acme_par", "acme_par_copy")
    d, _ = library.resolve("acme_par_copy")
    assert d.source.kind == "ofl" and d.source.ofl_key == "acme/par" and d.source.attribution == "OFL"
    assert d.source.confirmed is False and d.source.confirmed_at is None


def test_copy_refuses_taken_or_bad_ids():
    library.save_user_definition(load_definition(_raw()))
    with pytest.raises(ValueError):
        library.copy_definition("rgb_par", "shehds_3w_laser")    # a built-in id
    with pytest.raises(ValueError):
        library.copy_definition("rgb_par", "acme_par")           # an existing user id
    with pytest.raises(ValueError):
        library.copy_definition("rgb_par", "Bad Id")
    with pytest.raises(KeyError):
        library.copy_definition("nope", "fine_id")


# --- ui/fixture_library.py ---

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _browser(ask_text, error=None):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    return FixtureLibraryBrowser(ask_text=ask_text, error=error or (lambda title, text: None))


def _row_of(browser, fid):
    return [e.definition.id for e in browser.entries].index(fid)


def test_duplicate_suggests_id_copy_and_adds_the_copy(qapp):
    asked = []
    b = _browser(ask_text=lambda title, label, default: asked.append(default) or "my_par")
    b.table.selectRow(_row_of(b, "rgb_par"))
    assert b.b_duplicate.isEnabled()
    changed = []
    b.library_changed.connect(lambda: changed.append(1))
    b.b_duplicate.click()
    assert asked == ["rgb_par_copy"]
    ids = [e.definition.id for e in b.entries]
    assert "my_par" in ids and changed
    e = b.entries[_row_of(b, "my_par")]
    assert e.origin == "user" and e.definition.source.confirmed is False


def test_duplicate_cancel_adds_nothing(qapp):
    b = _browser(ask_text=lambda title, label, default: None)
    before = [e.definition.id for e in b.entries]
    b.table.selectRow(_row_of(b, "rgb_par"))
    b.b_duplicate.click()
    assert [e.definition.id for e in b.entries] == before


def test_duplicate_bad_id_reports_and_keeps_the_library(qapp):
    errors = []
    b = _browser(ask_text=lambda title, label, default: "rgb_par",   # a built-in id: refused
                 error=lambda title, text: errors.append(text))
    before = [e.definition.id for e in b.entries]
    b.table.selectRow(_row_of(b, "rgb_par"))
    assert b.duplicate_selected() is False
    assert errors and "rgb_par" in errors[0]
    assert [e.definition.id for e in b.entries] == before


def test_duplicate_disabled_with_no_selection(qapp):
    b = _browser(ask_text=lambda title, label, default: "x")
    b.table.clearSelection()
    assert not b.b_duplicate.isEnabled()
    assert b.duplicate_selected() is False
