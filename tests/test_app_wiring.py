"""The app seams that make the agents' features reach the running show (2026-10-30, roadmap items 3/4/42).

Each test starts from a real App + ConsoleWindow, uses the real control, and asserts that the RUNNING app
changed -- not just that a dialog opened. Before this, Rig manager "Use selected", presets and the
first-run setup all ended in "applies at the next start" or nowhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from livereact.app import App, AppConfig
from livereact.clock import FakeClock


def _app(**kw) -> App:
    return App(AppConfig(demo=True, config_path=None, **kw), clock=FakeClock())


def _two_fixture_rig(tmp_path: Path, profile: str = "trigger") -> Path:
    p = tmp_path / "small.json"
    p.write_text(json.dumps({
        "name": "Small", "laser_profile": profile,
        "slots": [{"slot_id": "bar", "fixture_type": "ok103_wash_bar", "dmx_address": 101, "label": "Bar",
                   "role": "wash", "side": "left"},
                  {"slot_id": "las", "fixture_type": "shehds_3w_laser", "dmx_address": 201, "label": "Laser",
                   "role": "laser", "side": "left"}],
    }), encoding="utf-8")
    return p


def test_switch_rig_swaps_the_rig_the_show_plays_and_disarms_the_lasers(tmp_path):
    from livereact.engines.laser_trigger import LaserTriggerEngine
    app = _app()
    assert app.manual.laser_enable            # demo pre-arms
    before = set(app.rig._by_slot)
    app.switch_rig(str(_two_fixture_rig(tmp_path)))
    frame = app.show_engine.tick()
    assert set(app.show_engine.rig._by_slot) == {"bar", "las"} != before
    assert isinstance(app.show_engine.laser_engine, LaserTriggerEngine) and app.laser_engine is app.show_engine.laser_engine
    assert app.show_engine.laser_engine in app.show_engine.engines
    assert not app.manual.laser_enable                       # always disarmed by a switch
    assert app.config.rig_path.endswith("small.json")
    assert frame is not None and len(frame) == 512


def test_switch_rig_with_a_bad_file_changes_nothing(tmp_path):
    app = _app()
    rig = app.rig
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception):
        app.switch_rig(str(bad))
    assert app.rig is rig and app.show_engine.rig is rig


def test_the_rig_manager_switches_the_running_show_and_the_console_redraws(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    from livereact.ui.console import ConsoleWindow
    app = _app()
    win = ConsoleWindow(app)
    old_venue = win.venue_tab
    dlg = win.open_rig_manager()
    dlg.choose(str(_two_fixture_rig(tmp_path)))          # the same path "Use selected" / "Open…" take
    app.show_engine.tick()
    assert set(app.show_engine.rig._by_slot) == {"bar", "las"}
    assert win.venue_tab is not old_venue and win.venue_tab.rig is app.rig
    assert win.tabs.indexOf(win.venue_tab) >= 0 and win.tabs.indexOf(win.rig_config) >= 0
    assert "Now playing rig" in win.statusBar().currentMessage()
    win.close()


def test_apply_preset_reaches_manual_params_and_reports_what_is_not_supported():
    from livereact import presets
    app = _app()
    eff = presets.resolve(presets.PresetEvent("x", scheme="warm", genre="auto", event_type="auto"))
    missing = app.apply_preset(eff)
    assert app.manual.warm_cool_bias == pytest.approx(-0.4)
    assert any("colour scheme" in m for m in missing)
    # an event type that forbids lasers disarms them
    no_laser = next((k for k, v in presets.catalog()["event_types"].items() if not v["lasers_allowed"]), None)
    if no_laser:
        app.manual.laser_enable = True
        app.apply_preset(presets.resolve(presets.PresetEvent("y", event_type=no_laser)))
        assert not app.manual.laser_enable


def test_the_override_panel_preset_changes_the_running_show(qapp):
    from livereact import presets
    from livereact.ui.console import ConsoleWindow
    app = _app()
    win = ConsoleWindow(app)
    panel = win.override_panel
    panel.set_event(presets.PresetEvent("x", scheme="ice"))
    assert app.manual.warm_cool_bias == pytest.approx(0.5)
    assert "Not supported yet" in panel.l_preset_msg.text()
    win.close()


def test_first_run_offers_only_outputs_this_build_has(qapp, tmp_path):
    from livereact.settings import Settings
    from livereact.ui.first_run import FirstRunDialog
    d = FirstRunDialog(None, settings=Settings(path=tmp_path / "s.json"), audio_provider=lambda: [],
                       interface_provider=lambda: [])
    assert d.output_buttons["enttec"].isEnabled() and d.output_buttons["demo"].isEnabled()
    assert not d.output_buttons["artnet"].isEnabled() and not d.output_buttons["sacn"].isEnabled()
    assert "coming later" in d.output_buttons["artnet"].text()
    d.close()


def test_first_run_choices_become_the_next_launch_defaults(tmp_path):
    import argparse
    from livereact.launch import apply_to_args, from_settings
    from livereact.settings import Settings
    rig = _two_fixture_rig(tmp_path)
    s = Settings(path=tmp_path / "s.json")
    s.extra["setup"] = {"output": "enttec", "audio_device": "XDJ-XZ", "pdl_interface": "en8", "start_with": "demo_rig"}
    s.add_recent_rig(rig)
    d = from_settings(s)
    args = argparse.Namespace(hardware=False, audio_device=None, pdl_interface=None, rig=None)
    applied = apply_to_args(args, d, hardware_flag_given=False)
    assert args.hardware and args.audio_device == "XDJ-XZ" and args.pdl_interface == "en8" and args.rig == str(rig)
    assert len(applied) == 4
    # an explicit flag always wins
    args2 = argparse.Namespace(hardware=False, audio_device="Other", pdl_interface=None, rig="x.json")
    apply_to_args(args2, d, hardware_flag_given=True)
    assert not args2.hardware and args2.audio_device == "Other" and args2.rig == "x.json"
    # a saved output this build lacks falls back to demo
    s.extra["setup"] = {"output": "artnet"}
    assert from_settings(s).hardware is False
