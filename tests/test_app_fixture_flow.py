"""Adding a fixture as a new user would (User-Tester walk, 2026-11-06): what the library, the wizard
and the channel-test screen say and do once the console is running. Offscreen."""
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


@pytest.fixture
def console(qapp):
    from livereact.app import App, AppConfig
    from livereact.settings import Settings
    from livereact.ui.console import ConsoleWindow
    s = Settings.load()
    s.onboarding_done = True
    s.save()
    win = ConsoleWindow(App(AppConfig(demo=True, config_path=None)))
    yield win
    win.close()


def _library(**kw):
    from livereact.ui.fixture_library import FixtureLibraryBrowser
    return FixtureLibraryBrowser(**kw)


def _row(lib, fid):
    return next(i for i, e in enumerate(lib.entries) if e.definition.id == fid)


def _hand_entry(wizard, functions, manufacturer="Acme", model="Beam"):
    from livereact.ui.fixture_wizard import PAGE_HAND
    wizard.go(PAGE_HAND)
    h = wizard.hand
    h.e_manufacturer.setText(manufacturer)
    h.e_model.setText(model)
    while h.table.rowCount():
        h.remove_last()
    for f in functions:
        h.add_channel(f)
    return wizard.to_channel_test()


def _pass_all(panel):
    for m in panel.marks:
        m.setCurrentText("pass")


# --- the channel-test screen is not wired to the running show


def test_channel_test_from_the_library_says_it_does_not_reach_the_lights(console, qapp):
    """Reproduces the walk: the sliders publish to a private buffer nothing reads. Until Josh decides how
    it should reach the show, the screen has to say so, and the DMX range must not read as a live patch."""
    lib = console.open_fixture_library()
    lib.table.selectRow(_row(lib, "rgb_par"))
    panel = lib.test_selected()
    before = console.app.frame_buffer.latest()
    panel.sliders[0].setValue(200)
    assert console.app.frame_buffer.latest() == before     # what the note below is telling the user
    assert not panel.l_offline.isHidden()
    assert "do not reach your lights" in panel.l_offline.text()
    assert "--fixture-test" in panel.l_offline.text()
    assert "DMX 1-" not in panel.head.text()
    lib.close()


def test_a_panel_given_a_real_buffer_shows_no_such_note(qapp):
    from livereact.core.frame_buffer import DmxFrameBuffer
    from livereact.fixtures.library import list_definitions
    from livereact.ui.channel_test import ChannelTestPanel
    d = next(e.definition for e in list_definitions() if e.definition.id == "rgb_par")
    panel = ChannelTestPanel(d, address=10, frame_buffer=DmxFrameBuffer())
    assert panel.l_offline.isHidden()
    assert "DMX 10-" in panel.head.text()


def test_the_laser_output_box_is_off_on_a_screen_that_reaches_nothing(qapp):
    from livereact.fixtures.library import list_definitions
    from livereact.ui.channel_test import ChannelTestPanel
    d = next(e.definition for e in list_definitions() if e.definition.category == "laser")
    panel = ChannelTestPanel(d, laser_armed=lambda: True)
    assert not panel.cb_laser_out.isEnabled()
    assert "reach" in panel.cb_laser_out.toolTip()
    assert "--fixture-test" not in panel.l_offline.text()    # raw DMX has no arm switch: never for a laser


# --- a built-in model cannot be confirmed: say so before the work, not after


def test_a_built_in_says_up_front_that_it_cannot_be_confirmed(qapp):
    """It used to let the user mark every channel, then answer "Not saved" on Confirm."""
    lib = _library()
    lib.table.selectRow(_row(lib, "rgb_par"))
    panel = lib.test_selected()
    assert not panel.b_confirm.isEnabled()
    _pass_all(panel)
    assert not panel.b_confirm.isEnabled()
    assert "Duplicate" in panel.status.text()
    assert "Duplicate" in panel.b_confirm.toolTip()


def test_a_copy_of_a_built_in_can_still_be_confirmed(qapp):
    from livereact.fixtures.library import copy_definition
    copy_definition("rgb_par", "my_par")
    lib = _library()
    lib.table.selectRow(_row(lib, "my_par"))
    panel = lib.test_selected()
    _pass_all(panel)
    assert panel.b_confirm.isEnabled()
    assert panel.confirm() is not None


# --- a second entry under the same name must not eat the first


def test_entering_the_same_name_again_does_not_silently_replace_a_saved_fixture(qapp):
    """Same make and model, different channels: Confirm used to overwrite the saved file without a word,
    and every rig using that id would have shifted to the new channel layout."""
    from livereact.fixtures.library import list_definitions
    from livereact.ui.fixture_wizard import FixtureWizard
    asked = []
    w = FixtureWizard(confirm=lambda title, text: asked.append(text) or False)
    first = _hand_entry(w, ["dimmer"])
    _pass_all(first)
    assert first.confirm() is not None
    assert asked == []                               # nothing to replace yet
    second = _hand_entry(w, ["red", "green", "blue"])
    _pass_all(second)
    assert second.confirm() is None
    assert "acme_beam" in asked[0]
    assert "Not saved" in second.status.text()
    kept = next(e.definition for e in list_definitions() if e.definition.id == "acme_beam")
    assert len(kept.modes[0].channels) == 1


def test_replacing_a_saved_fixture_on_purpose_works_and_names_the_rigs_using_it(qapp, home):
    import json

    from livereact import userdata
    from livereact.fixtures.library import list_definitions
    from livereact.ui.fixture_wizard import FixtureWizard
    asked = []
    w = FixtureWizard(confirm=lambda title, text: asked.append(text) or True)
    first = _hand_entry(w, ["dimmer"])
    _pass_all(first)
    first.confirm()
    (userdata.paths().rigs / "club.json").write_text(
        json.dumps({"slots": [{"fixture": "acme_beam@1ch"}]}), encoding="utf-8")
    second = _hand_entry(w, ["red", "green", "blue"])
    _pass_all(second)
    assert second.confirm() is not None
    assert "club" in asked[0]
    kept = next(e.definition for e in list_definitions() if e.definition.id == "acme_beam")
    assert len(kept.modes[0].channels) == 3


def test_confirming_the_same_channels_again_asks_nothing(qapp):
    from livereact.ui.fixture_wizard import FixtureWizard
    asked = []
    w = FixtureWizard(confirm=lambda title, text: asked.append(text) or False)
    for _ in range(2):
        panel = _hand_entry(w, ["dimmer"])
        _pass_all(panel)
        assert panel.confirm() is not None
    assert asked == []


# --- what happens after Confirm, and around it


def test_a_fixture_added_after_a_rig_switch_reaches_the_new_rig_config_tab(console, qapp):
    """The library held the OLD tab's reload: switch rig with the library open and a new fixture never
    showed in Rig Config's type list until the next launch."""
    from livereact import userdata
    from livereact.fixtures.definition import SCHEMA, load_definition
    from livereact.fixtures.library import save_user_definition
    from livereact.fixtures.rig import save_rig
    lib = console.open_fixture_library()
    path = userdata.paths().rigs / "other.json"
    save_rig(console.app.rig, path, "another rig")
    console._rig_chosen(str(path))
    save_user_definition(load_definition({
        "schema": SCHEMA, "id": "acme_new", "manufacturer": "Acme", "model": "New", "category": "par",
        "source": {"kind": "user", "confirmed": True},
        "modes": [{"name": "1ch", "channels": [{"offset": 0, "function": "dimmer"}]}]}))
    lib.reload()
    assert "acme_new" in console.rig_config.definitions
    lib.close()


def test_confirm_in_the_wizard_says_where_the_fixture_goes_next(qapp):
    from livereact.ui.fixture_wizard import FixtureWizard
    w = FixtureWizard()
    panel = _hand_entry(w, ["dimmer"])
    _pass_all(panel)
    panel.confirm()
    assert "Rig Config" in panel.status.text()
    assert "Add fixture" in panel.status.text()


def test_test_confirm_is_off_until_a_fixture_is_selected(qapp):
    lib = _library()
    assert not lib.b_test.isEnabled()
    assert "Select" in lib.b_test.toolTip()
    lib.table.selectRow(0)
    assert lib.b_test.isEnabled()
    assert lib.b_test.toolTip() == ""


def test_hand_entry_explains_its_errors_in_plain_words(qapp):
    from livereact.ui.fixture_wizard import FixtureWizard
    w = FixtureWizard()
    assert _hand_entry(w, ["dimmer"], manufacturer="***", model="///") is None
    assert "letter or digit" in w.hand.l_errors.text()
    assert _hand_entry(w, ["pan_fine", "dimmer"]) is None
    assert "pan fine" in w.hand.l_errors.text()
    assert "no fine_of" not in w.hand.l_errors.text()


def test_hand_entry_refuses_a_moving_head_the_rig_loader_would_refuse(qapp):
    """It confirmed and saved, then failed at Rig Config's Save with a message about the definition."""
    from livereact.ui.fixture_wizard import FixtureWizard
    w = FixtureWizard()
    w.go(3)
    w.hand.cmb_category.setCurrentText("moving_head")
    assert _hand_entry(w, ["dimmer", "pan"]) is None
    assert "pan and tilt" in w.hand.l_errors.text()
    assert _hand_entry(w, ["pan", "tilt", "dimmer"]) is not None


def test_a_fixture_added_by_hand_warns_about_the_wash_role(qapp):
    """Scenes write the wash role only through isinstance(fx, (Par, WashBar)) (roadmap item 15), so a
    library fixture on that role is dark in the show, and wash is what a new Rig Config row starts on."""
    from livereact.ui.fixture_wizard import FixtureWizard
    w = FixtureWizard()
    w.hand.cmb_category.setCurrentText("par")
    panel = _hand_entry(w, ["dimmer", "red", "green", "blue"], model="Wash")
    _pass_all(panel)
    panel.confirm()
    assert "wash role" in panel.status.text()
    w.hand.cmb_category.setCurrentText("moving_head")
    panel = _hand_entry(w, ["pan", "tilt", "dimmer"], model="Spot")
    _pass_all(panel)
    panel.confirm()
    assert "Role to spot" in panel.status.text()


def test_rig_config_save_to_a_folder_it_cannot_write_says_so(console, qapp, monkeypatch):
    """The OSError left the slot uncaught: the button did nothing and the status line stayed empty."""
    import builtins

    from PySide6 import QtWidgets

    def denied(*a, **k):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("x/rig.json", "")))
    monkeypatch.setattr("os.makedirs", denied)
    monkeypatch.setattr(builtins, "open", denied)
    console.rig_config._save()
    monkeypatch.undo()
    assert console.rig_config.status.text().startswith("Not saved")
    assert "denied" in console.rig_config.status.text()


def test_venue_save_to_a_folder_it_cannot_write_says_so(console, qapp, monkeypatch):
    from PySide6 import QtWidgets

    from livereact.ui import venue_tab

    def denied(*a, **k):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("x/rig.json", "")))
    monkeypatch.setattr(venue_tab, "save_rig", denied)
    console.venue_tab._save()
    assert console.venue_tab.status.text().startswith("Not saved")


def test_the_manual_page_points_at_the_menu_that_exists(console, qapp):
    """It said "in Settings"; the console has no Settings menu. The key lives under Fixtures."""
    from PySide6 import QtWidgets
    menus = [a.text().replace("&", "") for a in console.menuBar().actions()]
    assert "Fixtures" in menus and "Settings" not in menus
    from livereact.ui.fixture_wizard import FixtureWizard
    w = FixtureWizard()
    w.b_manual.click()
    text = " ".join(lbl.text() for lbl in w.manual_page.findChildren(QtWidgets.QLabel))
    assert "Fixtures menu" in text and "in Settings" not in text
