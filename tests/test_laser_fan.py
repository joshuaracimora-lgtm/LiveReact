"""engines/laser_fan.py -- the Junior Crib fan on the music, colour by frequency (2026-10-28)."""
from __future__ import annotations

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines import laser_fan as lf
from livereact.engines.laser_fan import LaserFanEngine
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


def test_the_laser_takes_the_palette_the_other_lights_show():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10, palette="acid_yellow"), 2 * BAR_S)
    clock.advance(0.05)
    c = eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=12, palette="acid_yellow"), rig)
    assert eng.active and eng.color == Laser.COLOR_YELLOW and eng.color_name == "yellow"
    assert c.values[rig.fixture("laser_l").offset + 11] == Laser.COLOR_YELLOW


def test_colour_held_for_the_whole_moment_even_if_the_palette_turns():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10, palette="deep_blue"), 2 * BAR_S)
    clock.advance(0.05)
    eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=12, palette="deep_blue"), rig)
    _run(eng, clock, rig, _snap(bucket="drop", bar=12, drop_start_bar=12, palette="blood_red"), BAR_S)
    assert eng.active and eng.color == Laser.COLOR_BLUE


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
    assert c.values[laser.offset + 11] == Laser.COLOR_RED            # the snapshot's blood_red palette
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


def test_audio_loss_goes_dark():
    eng, clock, rig = _engine()
    _drop(eng, clock, rig)
    clock.advance(0.05)
    c = eng.tick(_snap(bucket="drop", bar=12, audio_fresh=False), rig)
    assert not eng.active


def test_a_planned_roll_holds_the_fan_for_the_bass_hit_after_it():
    """2026-10-28: the roll is the start of the drop, but the fan belongs to the hit after the roll."""
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10), 2 * BAR_S)
    roll = dict(bucket="drop", drop_start_bar=12, plan_active=True, section_kind="drop", planned_laser_allowed=True)
    for i, p in enumerate((0.0, 0.25, 0.5, 0.75)):
        clock.advance(0.05)
        c = eng.tick(_snap(bar=12 + 2 * i, planned_roll_progress=p, **roll), rig)
        assert not eng.active and c.values[rig.fixture("laser_l").offset] == 0
    clock.advance(0.05)
    eng.tick(_snap(bar=20, planned_roll_progress=-1.0, **roll), rig)        # the chorus: the bass is back
    assert eng.active and eng.trigger == "drop"
