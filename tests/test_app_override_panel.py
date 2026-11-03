"""Roadmap item 18: the Manual Override panel widget (ui/override_panel.py), offscreen."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.app import App, AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_opening_the_panel_creates_no_folders(qapp, home):
    _panel()
    assert not home.exists()


def _panel(app=None):
    from livereact.ui.override_panel import OverridePanel
    if app is None:
        app = App(AppConfig(demo=True, config_path=None))
        app.manual.laser_enable = False   # some laser profiles arm at launch; start disarmed
    return app, OverridePanel(app)


def _select(combo, key):
    combo.setCurrentIndex(combo.findData(key))


def test_builds_with_lasers_disarmed(qapp):
    from livereact.ui.override_panel import STATUS_DISARMED
    app, p = _panel()
    assert app.manual.laser_enable is False
    assert not p.b_laser_arm.isChecked()
    assert p.laser_status() == STATUS_DISARMED == p.l_laser_status.text()


def test_mirrors_an_app_armed_at_launch(qapp):
    from livereact.ui.override_panel import OverridePanel, STATUS_ARMED
    app = App(AppConfig(demo=True, config_path=None))
    app.manual.laser_enable = True
    p = OverridePanel(app)
    assert p.b_laser_arm.isChecked() and p.laser_status() == STATUS_ARMED


def test_arm_button_writes_laser_enable(qapp):
    from livereact.ui.override_panel import STATUS_ARMED, STATUS_DISARMED
    app, p = _panel()
    p.b_laser_arm.click()
    assert app.manual.laser_enable is True
    assert p.l_laser_status.text() == STATUS_ARMED
    p.b_laser_arm.click()
    assert app.manual.laser_enable is False
    assert p.l_laser_status.text() == STATUS_DISARMED


def test_refresh_mirrors_an_arm_from_elsewhere(qapp):
    app, p = _panel()
    app.manual.laser_enable = True          # the console's L key / checkbox
    app.manual.laser_cue = True             # a MIDI-learned laser_cue
    p.refresh()
    assert p.b_laser_arm.isChecked() and p.b_laser_cue.isChecked()


def test_event_that_forbids_lasers_disarms_and_blocks(qapp):
    from livereact.ui.override_panel import STATUS_BLOCKED
    app, p = _panel()
    p.b_laser_arm.click()
    assert app.manual.laser_enable is True
    _select(p.cmb_event_type, "wedding")
    assert app.manual.laser_enable is False
    assert p.l_laser_status.text() == STATUS_BLOCKED
    assert not p.b_laser_arm.isEnabled()
    p.b_laser_arm.setChecked(True)          # even forced, it cannot arm
    assert app.manual.laser_enable is False
    _select(p.cmb_event_type, "club")
    assert p.b_laser_arm.isEnabled() and app.manual.laser_enable is False


def test_overrides_collapsed_by_default_and_expand(qapp):
    _app, p = _panel()
    assert p.override_body.isHidden()
    p.b_overrides.click()
    assert not p.override_body.isHidden()
    p.b_overrides.click()
    assert p.override_body.isHidden()


def test_cue_buttons_call_what_the_console_calls(qapp):
    app, p = _panel()
    calls = []
    app.metronome.cue_build = lambda: calls.append("cue")
    app.metronome.arm_drop = lambda: calls.append("arm")
    _app2, p = _panel(app)
    p.b_cue.click()
    p.b_arm.click()
    assert calls == ["cue", "arm"]
    assert app.phrase_engine._skip_requested is False
    p.b_skip.click()
    assert app.phrase_engine._skip_requested is True


def test_laser_cue_hit_and_burst(qapp):
    app, p = _panel()
    p.b_laser_cue.click()
    assert app.manual.laser_cue is True
    hits = app.manual.laser_hit
    p.b_laser_hit.click()
    assert app.manual.laser_hit == hits + 1
    p.b_burst.click()
    assert app.manual.strobe_burst is True


def test_tap_injects_a_master_beat(qapp):
    from livereact.clock import FakeClock
    clock = FakeClock()
    app = App(AppConfig(demo=True, config_path=None), clock=clock)
    _app, p = _panel(app)
    before = app.input_queue.qsize()
    p.do_tap()
    clock.advance(0.5)
    p.do_tap()
    assert app.input_queue.qsize() > before


def test_laser_show_controls_only_for_show_rigs(qapp):
    app, p = _panel()
    if getattr(app.rig, "laser_profile", "") != "show":
        assert p.laser_mode_buttons == {} and p.laser_look_buttons == {}
    app.rig.laser_profile = "show"
    _app, p = _panel(app)
    assert set(p.laser_mode_buttons) == {"follow", "rotate", "hold"}
    assert p.laser_look_buttons
    name = next(iter(p.laser_look_buttons))
    p.laser_look_buttons[name].click()
    assert app.manual.laser_scene == name and app.manual.laser_mode == "hold"
    p.laser_mode_buttons["rotate"].setChecked(True)
    assert app.manual.laser_mode == "rotate"


def test_presets_without_apply_preset_only_store(qapp, monkeypatch):
    # App gained apply_preset (D-38, 2026-10-30); the panel's fallback for an app without it still matters
    from livereact.merge.merge import ManualParams
    monkeypatch.delattr(App, "apply_preset")
    app, p = _panel()
    assert not hasattr(app, "apply_preset")
    before = ManualParams(**vars(app.manual))
    _select(p.cmb_scheme, "warm")
    _select(p.cmb_genre, "techno")
    _select(p.cmb_event_type, "bar")
    assert p.preset_event.scheme == "warm" and p.preset_event.genre == "techno" and p.preset_event.event_type == "bar"
    assert p.effect.palette == "warm"
    assert vars(app.manual) == vars(before)     # nothing reached the show
    assert p.l_preset_msg.text()


def test_presets_call_apply_preset_when_the_app_has_it(qapp):
    app, _ = _panel()
    got = []
    app.apply_preset = got.append
    _app, p = _panel(app)
    _select(p.cmb_genre, "house")
    assert got and got[-1].catalog_kwargs == {"genre": "house"}


def test_save_and_load_an_event(qapp):
    _app, p = _panel()
    _select(p.cmb_scheme, "ice")
    _select(p.cmb_event_type, "festival")
    p.e_event_name.setText("Saturday")
    p.b_save_event.click()
    assert p.cmb_events.findText("Saturday") >= 0
    _select(p.cmb_scheme, "auto")
    _select(p.cmb_event_type, "auto")
    p.cmb_events.setCurrentIndex(p.cmb_events.findText("Saturday"))
    p.b_load_event.click()
    assert p.cmb_scheme.currentData() == "ice" and p.cmb_event_type.currentData() == "festival"
    assert p.preset_event.name == "Saturday"


def test_save_without_a_name_reports(qapp):
    _app, p = _panel()
    p.e_event_name.setText("")
    p.b_save_event.click()
    assert "name" in p.l_preset_msg.text()
    assert p.cmb_events.count() == 0


def _override_panel():
    import json
    import pathlib
    import tempfile
    rig = pathlib.Path(tempfile.gettempdir()) / "livereact_test_override_panel_rig.json"
    data = json.load(open("rigs/junior_crib_fan.json"))
    data["laser_overrides"] = ["Rainbow", "Green", "White"]
    rig.write_text(json.dumps(data))
    app = App(AppConfig(demo=True, config_path=None, rig_path=str(rig)))
    app.manual.laser_enable = False
    return _panel(app)


def test_override_looks_only_on_a_rig_that_lists_them(qapp):
    _, p = _panel()
    assert p.laser_override_buttons == {} and p.b_laser_resume is None
    _, p = _override_panel()
    assert list(p.laser_override_buttons) == ["Rainbow", "Green", "White"]
    assert p.b_laser_resume is not None and not p.b_laser_resume.isEnabled()


def test_override_latches_auto_off_until_resume(qapp):
    app, p = _override_panel()
    m = app.manual
    p.laser_override_buttons["Green"].click()
    assert (m.laser_override, m.laser_auto_off) == ("Green", True)
    p.laser_override_buttons["White"].click()              # one look at a time
    assert m.laser_override == "White" and not p.laser_override_buttons["Green"].isChecked()
    p.laser_override_buttons["White"].click()              # off: dark, auto still off
    assert (m.laser_override, m.laser_auto_off) == ("", True)
    assert p.b_laser_resume.isEnabled()
    p.b_laser_resume.click()
    assert (m.laser_override, m.laser_auto_off) == ("", False)
    assert not p.b_laser_resume.isEnabled()


def test_switching_to_a_rig_without_overrides_clears_them(qapp):
    app, p = _override_panel()
    p.laser_override_buttons["Rainbow"].click()
    app.rig.laser_overrides = ()
    p.build_laser_overrides()
    assert p.laser_override_buttons == {}
    assert (app.manual.laser_override, app.manual.laser_auto_off) == ("", False)
