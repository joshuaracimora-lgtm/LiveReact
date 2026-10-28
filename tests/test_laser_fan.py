"""engines/laser_fan.py -- the Junior Crib fan on the music, colour by frequency (2026-10-28)."""
from __future__ import annotations

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines import laser_fan as lf
from livereact.engines.laser_fan import LaserFanEngine, fan_color
from livereact.fixtures.profiles import Laser
from livereact.fixtures.rig import load_rig

BPM = 120.0
BAR_S = 4 * 60.0 / BPM


def _snap(**kw) -> ShowSnapshot:
    base = dict(bpm=BPM, audio_fresh=True, pdl_fresh=True, bucket="break", drop_hit_intensity=1.0,
                energy=0.5, energy_norm=0.5, palette="blood_red", audio_band_low_abs=1.0,
                drop_start_bar=None, bar=0, beat_in_bar=1)
    base.update(kw)
    return ShowSnapshot(**base)


def _engine():
    clock = FakeClock()
    rig = load_rig("rigs/junior_crib_fan.json")
    return LaserFanEngine(clock), clock, rig


def _run(eng, clock, rig, snap, seconds: float, step: float = 0.05):
    for _ in range(int(round(seconds / step))):
        clock.advance(step)
        c = eng.tick(snap, rig)
    return c


def _drop(eng, clock, rig, hit: float = 1.0, bar: int = 12):
    _run(eng, clock, rig, _snap(bucket="build", bar=bar - 2), 2 * BAR_S)
    clock.advance(0.05)
    return eng.tick(_snap(bucket="drop", bar=bar, drop_start_bar=bar, drop_hit_intensity=hit), rig)


def test_rig_loads_the_fan_profile():
    _, _, rig = _engine()
    assert rig.laser_profile == "fan"


def test_colour_by_band():
    assert fan_color(1.0, 0.2, 0.1) == Laser.COLOR_RED
    assert fan_color(0.2, 0.9, 0.3) == Laser.COLOR_GREEN
    assert fan_color(0.2, 0.3, 0.9) == Laser.COLOR_BLUE
    assert fan_color(0.5, 0.9, 0.85) == lf.RAINBOW          # broad: mids and highs both strong


def test_dark_through_the_build_and_primed_with_the_fan_scene():
    eng, clock, rig = _engine()
    c = _run(eng, clock, rig, _snap(bucket="build", bar=10), 2 * BAR_S)
    laser = rig.fixture("laser_l")
    assert not eng.active
    assert c.values[laser.offset] == 0                       # CH1 off: nothing lit before the drop
    assert tuple(c.values[laser.offset + 1:laser.offset + 11]) == lf.FAN_SCENE[1:11]


def test_a_big_drop_fires_the_scene_with_a_colour():
    eng, clock, rig = _engine()
    c = _drop(eng, clock, rig, hit=1.0)
    laser = rig.fixture("laser_l")
    assert eng.active and eng.trigger == "drop"
    assert c.values[laser.offset] == lf.FAN_SCENE[0]
    assert c.values[laser.offset + 11] in lf.COLOR_NAMES
    assert eng.candidates[-1]["verdict"] == "fired"


def test_a_soft_drop_does_not_fire_and_is_logged():
    eng, clock, rig = _engine()
    _drop(eng, clock, rig, hit=0.9)
    assert not eng.active
    assert eng.candidates[-1]["trigger"] == "drop" and eng.candidates[-1]["verdict"] == "soft drop"


def test_plan_forbidden_drop_does_not_fire():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10), 2 * BAR_S)
    clock.advance(0.05)
    eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=12, plan_active=True, planned_laser_allowed=False), rig)
    assert not eng.active and eng.candidates[-1]["verdict"] == "plan forbids"


def test_a_drop_the_plan_still_calls_a_build_does_not_fire():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10), 2 * BAR_S)
    clock.advance(0.05)
    eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=12, plan_active=True, planned_laser_allowed=True,
                   section_kind="build"), rig)
    assert not eng.active and eng.candidates[-1]["verdict"] == "plan says build"
    _run(eng, clock, rig, _snap(bucket="build", bar=14), 2 * BAR_S)
    clock.advance(0.05)
    eng.tick(_snap(bucket="drop", bar=16, drop_start_bar=16, plan_active=True, planned_laser_allowed=True,
                   section_kind="drop"), rig)
    assert eng.active


def test_a_moment_stops_at_the_plans_laser_free_edge():
    eng, clock, rig = _engine()
    _drop(eng, clock, rig)
    assert eng.active
    clock.advance(0.05)
    c = eng.tick(_snap(bucket="drop", bar=13, drop_start_bar=12, plan_active=True, planned_laser_allowed=False), rig)
    assert not eng.active and c.values[rig.fixture("laser_l").offset] == 0


def test_colour_held_for_the_whole_moment():
    eng, clock, rig = _engine()
    _drop(eng, clock, rig)
    first = eng.color
    # the band mix swings during the moment; the colour picked at the hit stays
    _run(eng, clock, rig, _snap(bucket="drop", bar=12, drop_start_bar=12, audio_band_mid_abs=5.0,
                                audio_band_high_abs=5.0, audio_band_low_abs=0.0), BAR_S)
    assert eng.active and eng.color == first


def test_audio_loss_goes_dark():
    eng, clock, rig = _engine()
    _drop(eng, clock, rig)
    clock.advance(0.05)
    c = eng.tick(_snap(bucket="drop", bar=12, audio_fresh=False), rig)
    assert not eng.active
