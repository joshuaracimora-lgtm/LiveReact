"""Roadmap item 45: delete a fixture from the library (fixtures/library.py delete +
"Delete…" in ui/fixture_library.py, behind a confirm box that names the user rigs still using it)."""
from __future__ import annotations

import json
import os

import pytest

from livereact import userdata
from livereact.fixtures import library
from livereact.fixtures.definition import SCHEMA, load_definition


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _raw(fid="acme_par", model="Par"):
    return {"schema": SCHEMA, "id": fid, "manufacturer": "Acme", "model": model, "category": "par",
            "source": {"kind": "user"},
            "modes": [{"name": "4ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                   {"offset": 1, "function": "red"}]}]}


def _rig(name, ref):
    return {"name": name, "slots": [{"slot_id": "s1", "fixture": ref, "dmx_address": 1}]}


# --- fixtures/library.py ---

def test_delete_user_definition():
    p = library.save_user_definition(load_definition(_raw()))
    assert library.delete_user_definition("acme_par") == p
    assert not p.exists()
    with pytest.raises(KeyError):
        library.resolve("acme_par")


def test_delete_builtin_is_refused():
    with pytest.raises(ValueError):
        library.delete_user_definition("rgb_par")
    assert library.resolve("rgb_par")[0].id == "rgb_par"


@pytest.mark.parametrize("fid", ["nope", "../evil", "a/b"])
def test_delete_missing_or_unsafe_id(fid):
    with pytest.raises(KeyError):
        library.delete_user_definition(fid)


def test_rigs_using_names_only_the_rigs_with_the_id():
    rigs = userdata.paths().rigs
    (rigs / "bar.json").write_text(json.dumps(_rig("Bar rig", "acme_par@4ch")), encoding="utf-8")
    (rigs / "other.json").write_text(json.dumps(_rig("Other", "rgb_par@4ch")), encoding="utf-8")
    (rigs / "v1.json").write_text(json.dumps(
        {"name": "V1", "slots": [{"slot_id": "s1", "fixture_type": "rgb_par", "dmx_address": 1}]}),
        encoding="utf-8")
    (rigs / "broken.json").write_text("{nope", encoding="utf-8")
    assert library.rigs_using("acme_par") == ["Bar rig"]
    assert library.rigs_using("rgb_par") == ["Other"]
    assert library.rigs_using("never_used") == []


def test_rigs_using_falls_back_to_the_filename():
    rig = _rig("", "acme_par")          # a bare id (no @mode) and no name
    del rig["name"]
    (userdata.paths().rigs / "home_rig.json").write_text(json.dumps(rig), encoding="utf-8")
    assert library.rigs_using("acme_par") == ["home_rig"]


# --- ui/fixture_library.py ---

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _browser(confirm):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    return FixtureLibraryBrowser(confirm=confirm)


def _row_of(browser, fid):
    return [e.definition.id for e in browser.entries].index(fid)


def test_delete_button_enables_only_for_non_builtins(qapp):
    library.save_user_definition(load_definition(_raw()))
    b = _browser(confirm=lambda title, text: True)
    assert not b.b_delete.isEnabled()                    # nothing selected
    b.table.selectRow(_row_of(b, "rgb_par"))             # a built-in
    assert not b.b_delete.isEnabled()
    assert b.delete_selected() is False                  # even called directly, it refuses
    b.table.selectRow(_row_of(b, "acme_par"))
    assert b.b_delete.isEnabled()


def test_delete_confirm_names_the_rigs_then_deletes(qapp):
    p = library.save_user_definition(load_definition(_raw()))
    (userdata.paths().rigs / "bar.json").write_text(json.dumps(_rig("Bar rig", "acme_par@4ch")),
                                                    encoding="utf-8")
    asked = []
    b = _browser(confirm=lambda title, text: asked.append((title, text)) or True)
    b.table.selectRow(_row_of(b, "acme_par"))
    changed = []
    b.library_changed.connect(lambda: changed.append(1))
    b.b_delete.click()
    assert len(asked) == 1
    title, text = asked[0]
    assert "Acme Par" in text and "acme_par" in text and "Bar rig" in text
    assert not p.exists()
    assert "acme_par" not in [e.definition.id for e in b.entries]   # reloaded
    assert changed


def test_delete_cancel_keeps_the_file(qapp):
    p = library.save_user_definition(load_definition(_raw()))
    b = _browser(confirm=lambda title, text: False)
    b.table.selectRow(_row_of(b, "acme_par"))
    b.b_delete.click()
    assert p.exists()
    assert "acme_par" in [e.definition.id for e in b.entries]


def test_delete_confirm_is_quiet_when_no_rig_uses_it(qapp):
    library.save_user_definition(load_definition(_raw()))
    asked = []
    b = _browser(confirm=lambda title, text: asked.append(text) or True)
    b.table.selectRow(_row_of(b, "acme_par"))
    assert b.delete_selected() is True
    assert "used by" not in asked[0].lower()
