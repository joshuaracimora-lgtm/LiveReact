"""Roadmap item 48: fixture details pane — selecting a row in the library browser shows the
definition read-only (modes, channels, capability ranges, source/license, confirmed date)."""
from __future__ import annotations

import os

import pytest

from livereact.fixtures import library
from livereact.fixtures.definition import SCHEMA, load_definition


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _raw():
    return {"schema": SCHEMA, "id": "acme_par", "manufacturer": "Acme", "model": "Par",
            "category": "par",
            "source": {"kind": "ofl", "ofl_key": "acme/par", "license": "CC-BY-SA",
                       "attribution": "OFL contributors", "confirmed": True,
                       "confirmed_at": "2026-10-28"},
            "modes": [{"name": "4ch", "channels": [
                {"offset": 0, "function": "dimmer"},
                {"offset": 1, "function": "strobe", "capabilities": [
                    {"range": [0, 9], "type": "shutter", "label": "open"},
                    {"range": [10, 255], "type": "strobe", "label": "strobe slow-fast"}]}]},
                {"name": "2ch", "channels": [{"offset": 0, "function": "dimmer"},
                                             {"offset": 1, "function": "red"}]}]}


# --- the pure summary (ui/fixture_library.py definition_summary) ---

def test_summary_names_modes_channels_and_capabilities():
    from livereact.ui.fixture_library import definition_summary
    text = definition_summary(load_definition(_raw()))
    assert "Acme Par" in text and "acme_par" in text and "par" in text
    assert "4ch" in text and "2ch" in text
    assert "dimmer" in text and "strobe" in text
    assert "10-255" in text and "strobe slow-fast" in text     # a capability range + label
    assert "ofl" in text and "CC-BY-SA" in text and "OFL contributors" in text
    assert "2026-10-28" in text                                # the confirmed date


def test_summary_says_not_confirmed():
    from livereact.ui.fixture_library import definition_summary
    raw = _raw()
    raw["source"] = {"kind": "user"}
    text = definition_summary(load_definition(raw))
    assert "not confirmed" in text


def test_summary_marks_fine_channels():
    from livereact.ui.fixture_library import definition_summary
    d, _ = library.resolve("shehds_180w_mh")                   # builtin with pan/tilt fine
    text = definition_summary(d)
    assert "fine" in text


# --- the pane ---

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_selecting_a_row_fills_the_details_pane(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser, definition_summary
    library.save_user_definition(load_definition(_raw()))
    b = FixtureLibraryBrowser()
    assert b.details.toPlainText() == ""                       # nothing selected yet
    rows = [e.definition.id for e in b.entries]
    b.table.selectRow(rows.index("acme_par"))
    assert b.details.toPlainText() == definition_summary(b.entries[rows.index("acme_par")].definition)
    b.table.clearSelection()
    assert b.details.toPlainText() == ""
