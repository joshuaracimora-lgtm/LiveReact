"""UX_BACKLOG UX 9 (2026-11-07): the Fixture library's "Confirmed" column said "no" on a built-in model, which
reads as a job to do -- but a built-in can never be confirmed (the channel test refuses, channel_test.BUILT_IN).
A user's own definition keeps "no" with a tooltip naming the button that confirms it."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from livereact.fixtures import library  # noqa: E402
from livereact.fixtures.definition import SCHEMA, load_definition  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _confirmed_cell(browser, fid):
    from livereact.ui.fixture_library import _COLUMNS
    row = [e.definition.id for e in browser.entries].index(fid)
    return browser.table.item(row, _COLUMNS.index("Confirmed"))


def test_an_unconfirmed_builtin_does_not_say_no(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    b = FixtureLibraryBrowser(confirm=lambda *_: True)
    unconfirmed = [e.definition.id for e in b.entries
                   if e.origin == "builtin" and not e.definition.source.confirmed]
    assert unconfirmed, "the packaged library has an unconfirmed built-in to show"
    for fid in unconfirmed:
        cell = _confirmed_cell(b, fid)
        assert cell.text() != "no"
        assert "built-in" in cell.text()
        assert "Duplicate" in cell.toolTip()        # says how to get a copy you can confirm


def test_a_confirmed_builtin_still_says_yes(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    b = FixtureLibraryBrowser(confirm=lambda *_: True)
    confirmed = [e.definition.id for e in b.entries if e.origin == "builtin" and e.definition.source.confirmed]
    assert confirmed
    for fid in confirmed:
        assert _confirmed_cell(b, fid).text() == "yes"


def test_a_users_unconfirmed_definition_says_no_and_how_to_confirm_it(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    library.save_user_definition(load_definition(
        {"schema": SCHEMA, "id": "acme_par", "manufacturer": "Acme", "model": "Par", "category": "par",
         "source": {"kind": "user"},
         "modes": [{"name": "2ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                {"offset": 1, "function": "red"}]}]}))
    b = FixtureLibraryBrowser(confirm=lambda *_: True)
    cell = _confirmed_cell(b, "acme_par")
    assert cell.text() == "no"
    assert "Test / confirm" in cell.toolTip()
