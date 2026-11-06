"""The Venue tab's calibration beam on a head with no colour wheel (User-Tester walk, 2026-11-06).

"Light the selected head" pinned the SHEHDS wheel's open slot at address+7. On the RGBW beam head that
channel is Red, so the pin held Red at 0 and left G/B/W at whatever the show had: with no music, a dark
head on the screen that exists to find its beam."""
from __future__ import annotations

import json
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


def _console(tmp_path, head_type):
    from livereact.app import App, AppConfig
    from livereact.clock import FakeClock
    from livereact.settings import Settings
    from livereact.ui.console import ConsoleWindow
    s = Settings.load()
    s.onboarding_done = True
    s.save()
    rig = tmp_path / "rig.json"
    rig.write_text(json.dumps({"name": "Heads", "slots": [
        {"slot_id": "head", "fixture_type": head_type, "dmx_address": 21, "label": "Head",
         "role": "moving_head", "side": "left"}]}), encoding="utf-8")
    app = App(AppConfig(demo=True, config_path=None, rig_path=str(rig)), clock=FakeClock())
    return app, ConsoleWindow(app)


def _select_head(win):
    win.tabs.setCurrentWidget(win.venue_tab)   # pins only while the Venue tab is on screen
    win.venue_tab._load_fixture("head")


def test_selecting_an_rgbw_head_pins_it_open_white(qapp, tmp_path):
    app, win = _console(tmp_path, "xfalights_90w_beam")
    _select_head(win)
    a = app.rig.fixture("head").address
    pins = app.manual.calibration.channels
    assert [pins.get(a + k) for k in (7, 8, 9, 10)] == [255, 255, 255, 255]  # R G B W
    assert pins[a + 5] == 255 and pins[a + 6] == 0                           # dimmer full, strobe off
    win.close()


def test_the_rgbw_calibration_beam_is_lit_in_the_next_frame_with_no_music(qapp, tmp_path):
    app, win = _console(tmp_path, "xfalights_90w_beam")
    _select_head(win)
    frame = app.show_engine.tick()
    fx = app.rig.fixture("head")
    state = fx.visual_state(frame)
    assert state.brightness > 0 and (state.r, state.g, state.b) == (255, 255, 255)
    win.close()


def test_a_wheel_head_keeps_the_same_calibration_pins(qapp, tmp_path):
    app, win = _console(tmp_path, "shehds_180w_mh")
    _select_head(win)
    a = app.rig.fixture("head").address
    pins = app.manual.calibration.channels
    assert pins == {a + 4: 128, a + 5: 255, a + 6: 0, a + 7: 0}
    win.close()


def _generic_head_console(tmp_path):
    from livereact.app import App, AppConfig
    from livereact.clock import FakeClock
    from livereact.fixtures.definition import SCHEMA, load_definition
    from livereact.fixtures.library import save_user_definition
    from livereact.settings import Settings
    from livereact.ui.console import ConsoleWindow
    s = Settings.load()
    s.onboarding_done = True
    s.save()
    save_user_definition(load_definition({
        "schema": SCHEMA, "id": "acme_head", "manufacturer": "Acme", "model": "Head", "category": "moving_head",
        "source": {"kind": "user", "confirmed": True},
        "modes": [{"name": "3ch", "channels": [{"offset": 0, "function": "pan"}, {"offset": 1, "function": "tilt"},
                                               {"offset": 2, "function": "dimmer"}]}]}))
    rig = tmp_path / "rig.json"
    rig.write_text(json.dumps({"name": "Heads", "slots": [
        {"slot_id": "head", "fixture": "acme_head@3ch", "dmx_address": 21, "label": "Head",
         "role": "spot", "side": "left"}]}), encoding="utf-8")
    app = App(AppConfig(demo=True, config_path=None, rig_path=str(rig)), clock=FakeClock())
    return app, ConsoleWindow(app)


def test_a_library_head_says_why_it_has_no_calibrate_panel(qapp, tmp_path):
    """The Calibrate group just was not there: a new user with an imported head looks for it forever."""
    app, win = _generic_head_console(tmp_path)
    _select_head(win)
    tab = win.venue_tab
    assert tab.grp_head.isHidden()
    assert not tab.l_no_cal.isHidden()
    assert "built-in" in tab.l_no_cal.text()
    win.close()


def test_a_built_in_head_shows_no_such_note(qapp, tmp_path):
    app, win = _console(tmp_path, "shehds_180w_mh")
    _select_head(win)
    assert win.venue_tab.l_no_cal.isHidden()
    win.close()
