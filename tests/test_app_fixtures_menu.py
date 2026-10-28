"""Roadmap item 36: the console's Fixtures menu (library browser, API key) and Rig Config picking
up a newly added fixture."""
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


def _win():
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    return ConsoleWindow(App(AppConfig(demo=True, config_path=None)))


def test_fixtures_menu_entries(qapp):
    win = _win()
    menus = {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions()}
    assert "Fixtures" in menus
    entries = [a.text() for a in menus["Fixtures"].actions()]
    assert entries == ["Fixture library…", "Anthropic API key…"]


def test_library_opens_and_a_new_fixture_reaches_rig_config(qapp):
    from livereact.fixtures.definition import SCHEMA, load_definition
    from livereact.fixtures import library
    from livereact.ui import rig_config as rc
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    win = _win()
    browser = win.open_fixture_library()
    assert isinstance(browser, FixtureLibraryBrowser)
    tc = win.rig_config.table.cellWidget(0, rc.C_TYPE)
    assert tc.findData(rc._DEF + "acme_new") < 0
    library.save_user_definition(load_definition({
        "schema": SCHEMA, "id": "acme_new", "manufacturer": "Acme", "model": "New", "category": "par",
        "source": {"kind": "user"}, "modes": [{"name": "1ch", "channels": [{"offset": 0, "function": "dimmer"}]}]}))
    browser.reload()
    assert "acme_new" in win.rig_config.definitions
    assert tc.findData(rc._DEF + "acme_new") >= 0


def test_api_key_entry_opens_the_dialog(qapp, monkeypatch):
    from livereact.ui import api_key
    from livereact.ui.api_key import ApiKeyDialog

    class Fake:
        def get_password(self, s, u):
            return None
    monkeypatch.setattr(api_key, "_keyring", lambda module=None: Fake())
    dlg = _win().open_api_key()
    assert isinstance(dlg, ApiKeyDialog) and dlg.l_status.text() == "No key saved."
