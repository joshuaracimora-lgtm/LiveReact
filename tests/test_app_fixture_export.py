"""Roadmap item 47: export / import a fixture definition file (share a fixture without
sharing a whole rig). Imports are always saved unconfirmed and never under a built-in id."""
from __future__ import annotations

import json
import os

import pytest

from livereact import userdata
from livereact.fixtures import library
from livereact.fixtures.definition import SCHEMA, DefinitionError, load_definition


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _raw(fid="acme_par", **source_extra):
    return {"schema": SCHEMA, "id": fid, "manufacturer": "Acme", "model": "Par", "category": "par",
            "source": {"kind": "user", **source_extra},
            "modes": [{"name": "4ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                   {"offset": 1, "function": "red"}]}]}


# --- fixtures/library.py ---

def test_export_then_import_round_trips_unconfirmed(tmp_path):
    d = load_definition(_raw(confirmed=True, confirmed_at="2026-10-28"))
    f = library.export_definition(d, tmp_path / "share.json")
    assert json.loads(f.read_text(encoding="utf-8"))["id"] == "acme_par"
    p = library.import_definition_file(f)
    assert p == userdata.paths().fixtures / "acme_par.json"
    got, _ = library.resolve("acme_par")
    assert got.modes == d.modes and got.model == d.model
    assert got.source.confirmed is False and got.source.confirmed_at is None


def test_import_refuses_builtin_ids_and_bad_files(tmp_path):
    f = tmp_path / "laser.json"
    f.write_text(json.dumps(_raw(fid="shehds_3w_laser")), encoding="utf-8")
    with pytest.raises(ValueError):
        library.import_definition_file(f)
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(DefinitionError):
        library.import_definition_file(bad)


# --- ui/fixture_library.py ---

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _browser(save_path=None, open_path=None, confirm=None, error=None):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    return FixtureLibraryBrowser(get_save_path=save_path or (lambda title, suggested: None),
                                 get_open_path=open_path or (lambda title: None),
                                 confirm=confirm or (lambda title, text: True),
                                 error=error or (lambda title, text: None))


def _row_of(browser, fid):
    return [e.definition.id for e in browser.entries].index(fid)


def test_export_writes_the_selected_definition(qapp, tmp_path):
    out = tmp_path / "rgb_par.json"
    suggested = []
    b = _browser(save_path=lambda title, s: suggested.append(s) or str(out))
    b.table.selectRow(_row_of(b, "rgb_par"))
    assert b.b_export.isEnabled()
    assert b.export_selected() is True
    assert suggested == ["rgb_par.json"]
    assert json.loads(out.read_text(encoding="utf-8"))["id"] == "rgb_par"


def test_export_cancel_and_no_selection(qapp, tmp_path):
    b = _browser(save_path=lambda title, s: None)
    b.table.clearSelection()
    assert not b.b_export.isEnabled()
    assert b.export_selected() is False
    b.table.selectRow(_row_of(b, "rgb_par"))
    assert b.export_selected() is False                  # dialog cancelled


def test_import_adds_the_fixture_unconfirmed(qapp, tmp_path):
    f = tmp_path / "share.json"
    f.write_text(json.dumps(_raw(confirmed=True)), encoding="utf-8")
    b = _browser(open_path=lambda title: str(f))
    b.b_import.click()
    e = b.entries[_row_of(b, "acme_par")]
    assert e.origin == "user" and e.definition.source.confirmed is False


def test_import_over_an_existing_id_asks_first(qapp, tmp_path):
    library.save_user_definition(load_definition(_raw()))
    f = tmp_path / "share.json"
    f.write_text(json.dumps({**_raw(), "model": "Par v2"}), encoding="utf-8")
    asked = []
    b = _browser(open_path=lambda title: str(f),
                 confirm=lambda title, text: asked.append(text) or False)
    assert b.import_file() is False                      # user said no
    assert asked and "acme_par" in asked[0]
    assert library.resolve("acme_par")[0].model == "Par"
    b2 = _browser(open_path=lambda title: str(f), confirm=lambda title, text: True)
    assert b2.import_file() is True
    assert library.resolve("acme_par")[0].model == "Par v2"


def test_import_errors_are_reported(qapp, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    errors = []
    b = _browser(open_path=lambda title: str(bad), error=lambda title, text: errors.append(text))
    before = [e.definition.id for e in b.entries]
    assert b.import_file() is False
    assert errors
    assert [e.definition.id for e in b.entries] == before
