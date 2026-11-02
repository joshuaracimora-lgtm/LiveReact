"""AEPI rig's latched laser override looks (2026-10-31): engines/laser_fan.py + rig.laser_overrides."""
from __future__ import annotations

import json

import pytest

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines import laser_fan as lf
from livereact.engines.laser_fan import LaserFanEngine
from livereact.fixtures.profiles import Laser
from livereact.fixtures.rig import RigError, load_rig, rig_to_dict
from livereact.merge.merge import ManualParams

BPM = 120.0
BAR_S = 4 * 60.0 / BPM


def _snap(**kw) -> ShowSnapshot:
    base = dict(bpm=BPM, audio_fresh=True, pdl_fresh=True, bucket="break", drop_hit_intensity=1.0,
                energy=0.5, energy_norm=0.5, palette="blood_red", audio_band_low_abs=1.0,
                drop_start_bar=None, bar=0, beat_in_bar=1)
    base.update(kw)
    return ShowSnapshot(**base)


def _engine(rig_path: str = "rigs/aepi.json"):
    clock, manual = FakeClock(), ManualParams()
    return LaserFanEngine(clock, manual=manual), clock, load_rig(rig_path), manual


def _run(eng, clock, rig, snap, seconds: float, step: float = 0.05):
    c = None
    for _ in range(int(round(seconds / step))):
        clock.advance(step)
        c = eng.tick(snap, rig)
    return c


def _drop(eng, clock, rig, bar: int = 12):
    _run(eng, clock, rig, _snap(bucket="build", bar=bar - 2), 2 * BAR_S)
    clock.advance(0.05)
    return eng.tick(_snap(bucket="drop", bar=bar, drop_start_bar=bar), rig)


def test_only_the_aepi_rig_offers_the_overrides():
    assert load_rig("rigs/aepi.json").laser_overrides == ("Rainbow", "Green", "White")
    assert load_rig("rigs/junior_crib_fan.json").laser_overrides == ()


@pytest.mark.parametrize("look,ch12", [("Rainbow", 255), ("Green", Laser.COLOR_GREEN), ("White", Laser.COLOR_WHITE)])
def test_a_look_lights_the_fan_scene_in_its_colour_outside_any_drop(look, ch12):
    eng, clock, rig, m = _engine()
    m.laser_override, m.laser_auto_off = look, True
    c = _run(eng, clock, rig, _snap(bucket="break"), BAR_S)
    laser = rig.fixture("laser_l")
    assert eng.active and eng.trigger == "cue"
    assert c.values[laser.offset] == lf.FAN_SCENE[0]
    assert c.values[laser.offset + 11] == ch12


def test_switching_looks_changes_colour_at_once():
    eng, clock, rig, m = _engine()
    m.laser_override, m.laser_auto_off = "Green", True
    _run(eng, clock, rig, _snap(), BAR_S)
    m.laser_override = "White"
    c = _run(eng, clock, rig, _snap(), 0.05)
    assert c.values[rig.fixture("laser_l").offset + 11] == Laser.COLOR_WHITE


def test_turning_the_look_off_stays_dark_through_a_big_drop_until_resume():
    eng, clock, rig, m = _engine()
    m.laser_override, m.laser_auto_off = "Green", True
    _run(eng, clock, rig, _snap(), BAR_S)
    m.laser_override = ""                                    # button off
    _run(eng, clock, rig, _snap(), 0.1)
    assert not eng.active
    c = _drop(eng, clock, rig)                               # a big drop: auto would fire
    assert not eng.active and c.values[rig.fixture("laser_l").offset] == 0
    m.laser_auto_off = False                                 # RESUME AUTO LASERS
    _drop(eng, clock, rig, bar=40)
    assert eng.active and eng.trigger == "drop"


def test_still_dark_without_music():
    eng, clock, rig, m = _engine()
    m.laser_override, m.laser_auto_off = "White", True
    _run(eng, clock, rig, _snap(audio_fresh=False, pdl_fresh=False), BAR_S)
    assert not eng.active


def test_a_rig_without_the_key_ignores_stale_override_state():
    eng, clock, rig, m = _engine("rigs/junior_crib_fan.json")
    m.laser_override, m.laser_auto_off = "Green", True       # left over from the AEPI rig
    _run(eng, clock, rig, _snap(), BAR_S)
    assert not eng.active
    _drop(eng, clock, rig)
    assert eng.active and eng.trigger == "drop"              # Junior's auto lasers unaffected


def test_rig_validation_and_round_trip(tmp_path):
    data = json.load(open("rigs/aepi.json"))
    assert rig_to_dict(load_rig("rigs/aepi.json"))["laser_overrides"] == ["Rainbow", "Green", "White"]
    for bad in ({"laser_overrides": ["Purple"]}, {"laser_profile": "show"}):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({**data, **bad}))
        with pytest.raises(RigError):
            load_rig(str(p))
