"""UX 10 (2026-11-16): double-clicking a Fixture library row opens the channel test for that fixture,
the way a new user expects a list of things to open; before, only the Test / confirm button did."""
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


def _raw(fid: str, model: str):
    return {"schema": SCHEMA, "id": fid, "manufacturer": "Acme", "model": model, "category": "par",
            "source": {"kind": "user"},
            "modes": [{"name": "2ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                   {"offset": 1, "function": "red"}]}]}


def _double_click_row(browser, row: int) -> None:
    from PySide6 import QtCore
    from PySide6.QtTest import QTest
    browser.resize(900, 500)
    browser.show()
    rect = browser.table.visualRect(browser.table.model().index(row, 0))
    assert rect.isValid()
    # A real double-click is press/release then a double-click press; QTest.mouseDClick alone
    # sends only the second half, which Qt's item views ignore.
    for send in (QTest.mouseClick, QTest.mouseDClick):
        send(browser.table.viewport(), QtCore.Qt.MouseButton.LeftButton,
             QtCore.Qt.KeyboardModifier.NoModifier, rect.center())


def test_double_clicking_a_row_opens_the_channel_test_for_that_fixture(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    library.save_user_definition(load_definition(_raw("acme_one", "One")))
    library.save_user_definition(load_definition(_raw("acme_two", "Two")))
    b = FixtureLibraryBrowser()
    ids = [e.definition.id for e in b.entries]
    b.table.selectRow(ids.index("acme_one"))                 # a different row was selected before
    assert b.test_panel is None
    _double_click_row(b, ids.index("acme_two"))
    assert b.test_panel is not None, "double-click opened nothing"
    assert b.test_panel.isVisible()
    assert b.test_panel.windowTitle() == "Test Acme Two"
    b.test_panel.close()
    b.close()

