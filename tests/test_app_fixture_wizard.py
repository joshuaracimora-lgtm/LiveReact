"""Roadmap item 23: the fixture library browser and the add-fixture wizard (offscreen)."""
from __future__ import annotations

import os

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


def _enter_par(w):
    h = w.hand
    h.e_manufacturer.setText("Acme Lights")
    h.e_model.setText("Par 3")
    h.cmb_category.setCurrentText("par")
    h.e_mode.setText("4ch")
    for f in ("dimmer", "red", "green", "blue"):
        h.add_channel(f)


def test_three_routes_from_the_start_page(qapp):
    from livereact.ui import fixture_wizard as fw
    w = fw.FixtureWizard()
    assert w.stack.currentIndex() == fw.PAGE_START
    w.b_ofl.click()
    assert w.stack.currentIndex() == fw.PAGE_OFL
    w.b_back.click()
    w.b_manual.click()
    assert w.stack.currentIndex() == fw.PAGE_MANUAL
    w.b_back.click()
    w.b_hand.click()
    assert w.stack.currentIndex() == fw.PAGE_HAND and w.b_next.isVisibleTo(w)


def test_pages_without_a_backend_say_so(qapp):
    from PySide6 import QtWidgets
    from livereact.ui import fixture_wizard as fw
    w = fw.FixtureWizard()
    for page in (fw.PAGE_OFL, fw.PAGE_MANUAL):
        texts = [lbl.text() for lbl in w.stack.widget(page).findChildren(QtWidgets.QLabel)]
        assert fw.NOT_BUILT in texts


def test_hand_entry_needs_names_and_channels(qapp):
    from livereact.ui import fixture_wizard as fw
    w = fw.FixtureWizard()
    w.go(fw.PAGE_HAND)
    assert w.to_channel_test() is None
    assert "manufacturer" in w.hand.l_errors.text()
    assert w.stack.currentIndex() == fw.PAGE_HAND


def test_hand_entry_shows_validation_errors(qapp):
    from livereact.ui import fixture_wizard as fw
    w = fw.FixtureWizard()
    _enter_par(w)
    w.hand.add_channel("tilt_fine")                 # no tilt to pair with
    assert w.to_channel_test() is None and "fine" in w.hand.l_errors.text()


def test_hand_entry_builds_a_user_definition_and_pairs_fine_channels(qapp):
    from livereact.ui import fixture_wizard as fw
    w = fw.FixtureWizard()
    h = w.hand
    h.e_manufacturer.setText("Acme")
    h.e_model.setText("Spot")
    h.cmb_category.setCurrentText("moving_head")
    for f in ("pan", "pan_fine", "tilt", "tilt_fine", "dimmer"):
        h.add_channel(f)
    d = h.build()
    assert d is not None and d.id == "acme_spot" and d.source.kind == "user" and not d.source.confirmed
    chans = d.mode().channels
    assert d.mode().name == "5ch" and chans[1].fine_of == 0 and chans[3].fine_of == 2


def test_hand_entry_ends_on_the_channel_test_and_confirm_saves(qapp):
    from livereact.fixtures import library
    from livereact.ui import fixture_wizard as fw
    w = fw.FixtureWizard()
    added = []
    w.added.connect(added.append)
    _enter_par(w)
    panel = w.to_channel_test()
    assert panel is not None and w.stack.currentIndex() == fw.PAGE_TEST
    for m in panel.marks:
        m.setCurrentText("pass")
    panel.b_confirm.click()
    d, mode = library.resolve("acme_lights_par_3@4ch")
    assert d.source.confirmed and added and added[0].id == "acme_lights_par_3"


def test_library_browser_lists_builtin_and_user(qapp):
    from livereact.fixtures import library
    from livereact.ui import fixture_wizard as fw
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    w = fw.FixtureWizard()
    _enter_par(w)
    library.save_user_definition(w.hand.build())
    b = FixtureLibraryBrowser()
    rows = {b.table.item(r, 1).text(): [b.table.item(r, c).text() for c in range(6)]
            for r in range(b.table.rowCount())}
    assert rows["Par 3"][4] == "yours" and rows["Par 3"][5] == "no"
    assert rows["RGB Par 4ch"][4] == "built-in"
    assert len(rows) == 5


def test_browser_opens_the_wizard_and_reloads_after_add(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    b = FixtureLibraryBrowser()
    n = b.table.rowCount()
    w = b.open_wizard()
    _enter_par(w)
    panel = w.to_channel_test()
    for m in panel.marks:
        m.setCurrentText("pass")
    panel.b_confirm.click()
    assert b.table.rowCount() == n + 1


def test_browser_test_selected_opens_the_channel_test(qapp):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    b = FixtureLibraryBrowser(save=lambda d: "mem")
    assert b.test_selected() is None
    b.table.selectRow(0)
    p = b.test_selected()
    assert p is not None and p.definition.id == b.entries[0].definition.id


def test_origin_labels(qapp):
    from livereact.ui.fixture_library import origin_label
    assert origin_label("user", "ofl") == "imported (Open Fixture Library)"
    assert origin_label("user", "manual_ai") == "imported (manual reader)"
    assert origin_label("builtin", "builtin") == "built-in"
