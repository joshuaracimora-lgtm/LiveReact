"""engines/laser_show.py -- the SHOW profile (2026-10-08, Sophia's Thai)."""
from __future__ import annotations

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines.laser_show import (
    DEFAULT_SCENE, FAN_BARS, LASER_SCENES, LOOK_ARMED, LOOK_DARK, LOOK_FAN, LOOK_PINGPONG, LOOK_TENSION, LOOKS,
    ROTATE_BARS, ROTATE_FALLBACK_S, LaserShowEngine, look_names, scene_names,
)
from livereact.fixtures.profiles import Laser
from livereact.fixtures.rig import load_rig
from livereact.merge.merge import ManualParams, finalize, merge_contributions

RIG = "rigs/sophias_thai.json"
L, R = slice(6, 18), slice(18, 30)   # laser_l @7, laser_r @19 (0-based universe slices)


def _snap(**kw) -> ShowSnapshot:
    base = dict(bpm=130.0, bar=1, pdl_fresh=True, audio_fresh=True, bucket="break")
    base.update(kw)
    return ShowSnapshot(**base)


def _laser_bytes(frame: bytes, rig, slot: str) -> tuple[int, ...]:
    fx = rig.fixture(slot)
    return tuple(frame[fx.offset:fx.offset + 12])


def test_rig_loads_with_show_profile():
    rig = load_rig(RIG)
    assert rig.laser_profile == "show"
    assert [fx.address for fx in rig.by_role("laser")] == [7, 19]
    assert rig.fixture("bar").address == 1


def test_rainbow_bytes_are_the_screenshot_values():
    assert LASER_SCENES["Rainbow"] == (80, 119, 0, 0, 0, 0, 255, 71, 255, 255, 127, 255)
    assert scene_names()[0] == DEFAULT_SCENE == "Rainbow"
    assert all(len(v) == 12 for v in LASER_SCENES.values())
    assert look_names() == list(LOOKS) and set(scene_names()) < set(look_names())


def test_lit_by_default_without_audio_or_pdl_and_both_lasers_get_the_scene():
    rig = load_rig(RIG)
    manual = ManualParams(laser_enable=True, laser_mode="hold")
    eng = LaserShowEngine(FakeClock(), manual)
    c = eng.tick(_snap(pdl_fresh=False, audio_fresh=False), rig)
    assert eng.active is True
    frame = finalize(merge_contributions([c], rig), rig, manual, laser_on=eng.active)
    for slot in ("laser_l", "laser_r"):
        assert _laser_bytes(frame, rig, slot) == LASER_SCENES["Rainbow"]


def test_follow_without_grid_falls_back_to_rotation_not_dark():
    rig = load_rig(RIG)
    eng = LaserShowEngine(FakeClock(), ManualParams(laser_mode="follow"))
    c = eng.tick(_snap(pdl_fresh=False), rig)
    assert eng.active and eng.rotating and c.values[6] == 80
    assert "no beat grid" in eng.reason


def test_blackout_and_disarm_kill_the_lasers():
    rig = load_rig(RIG)
    manual = ManualParams(laser_enable=True, laser_mode="hold")
    eng = LaserShowEngine(FakeClock(), manual)
    merged = merge_contributions([eng.tick(_snap(), rig)], rig)
    manual.blackout = True
    assert finalize(merged, rig, manual, laser_on=True) == bytes(512)
    manual.blackout = False
    manual.laser_enable = False
    assert _laser_bytes(finalize(merged, rig, manual, laser_on=True), rig, "laser_l") == (0,) * 12


def test_hold_mode_shows_the_picked_look_and_nothing_else():
    rig = load_rig(RIG)
    manual = ManualParams(laser_mode="hold", laser_scene="Blue Sweep")
    eng = LaserShowEngine(FakeClock(), manual)
    for bucket in ("break", "build", "drop", "peak"):
        c = eng.tick(_snap(bucket=bucket), rig)
        assert eng.scene_name == "Blue Sweep" and tuple(c.values[L]) == LASER_SCENES["Blue Sweep"]
    manual.laser_scene = "no such look"
    eng.tick(_snap(), rig)
    assert eng.scene_name == DEFAULT_SCENE
    manual.laser_scene = LOOK_FAN                   # dynamic looks can be held too
    c = eng.tick(_snap(palette="toxic_green"), rig)
    assert eng.scene_name == LOOK_FAN
    assert c.values[7] == 118 and c.values[16] == 190 and c.values[17] == Laser.COLOR_GREEN


def test_rotation_on_the_bar_grid_and_on_the_clock():
    rig = load_rig(RIG)
    clock = FakeClock()
    manual = ManualParams(laser_mode="rotate")
    eng = LaserShowEngine(clock, manual)
    from livereact.engines.laser_show import rotation_scene_names
    names = rotation_scene_names()          # the seen scenes only; composed ones never rotate in by themselves
    assert names == scene_names()[:4]
    eng.tick(_snap(bar=10), rig)
    assert eng.scene_name == names[0] and eng.bars_to_next == ROTATE_BARS
    eng.tick(_snap(bar=10 + ROTATE_BARS - 1), rig)
    assert eng.scene_name == names[0]
    eng.tick(_snap(bar=10 + ROTATE_BARS), rig)
    assert eng.scene_name == names[1]
    eng.tick(_snap(bar=10 + 2 * ROTATE_BARS), rig)
    assert eng.scene_name == names[2]
    eng.tick(_snap(bar=1), rig)                     # new track: re-anchor, no jump
    assert eng.scene_name == names[2]
    manual.laser_scene = names[3]                   # picking restarts from it
    eng.tick(_snap(bar=1), rig)
    assert eng.scene_name == names[3]
    eng.tick(_snap(bar=1 + ROTATE_BARS), rig)
    assert eng.scene_name == names[0]               # wraps
    manual.laser_scene = names[0]
    eng.tick(_snap(pdl_fresh=False), rig)           # no grid: wall clock
    assert eng.scene_name == names[0]
    clock.advance(ROTATE_FALLBACK_S + 0.1)
    eng.tick(_snap(pdl_fresh=False), rig)
    assert eng.scene_name == names[1]
    manual.laser_mode = "hold"
    eng.tick(_snap(pdl_fresh=False), rig)
    assert eng.scene_name == names[0]


def test_follow_mode_maps_the_buckets():
    rig = load_rig(RIG)
    clock = FakeClock()
    eng = LaserShowEngine(clock, ManualParams(laser_mode="follow"))
    c = eng.tick(_snap(bucket="break"), rig)
    assert eng.scene_name == "Slow Rainbow" and tuple(c.values[L]) == LASER_SCENES["Slow Rainbow"]
    eng.tick(_snap(bucket="outro"), rig)
    assert eng.scene_name == "Slow Rainbow"
    # build: tension ramps with bars into the build, blue dotted line
    c = eng.tick(_snap(bucket="build", bar=20), rig)
    assert eng.scene_name == LOOK_TENSION and c.values[17] == Laser.COLOR_BLUE and c.values[12] == 128
    c = eng.tick(_snap(bucket="build", bar=28), rig)
    assert 180 <= c.values[12] <= 200                # half-way through the 16-bar ramp
    c = eng.tick(_snap(bucket="build", bar=40), rig)
    assert c.values[12] == 255
    c = eng.tick(_snap(bucket="build", bar=21, rise_index=2.0), rig)
    assert c.values[12] == 255                      # the rise index can get there first
    # armed: same bytes, CH1 chopped on the beat
    seen = set()
    for _ in range(30):
        clock.advance(0.05)
        c = eng.tick(_snap(bucket="build", bar=22, drop_armed=True), rig)
        assert eng.scene_name == LOOK_ARMED and c.values[7] == 118
        seen.add(c.values[6])
    assert seen == {0, 60}
    # the bar before the drop: dark (primed)
    c = eng.tick(_snap(bucket="build", bar=23, drop_armed=True, drop_pending=True), rig)
    assert eng.scene_name == LOOK_DARK and c.values[6] == 0 and c.values[7] == 119 and eng.primed
    # drop: the fan in the palette colour for FAN_BARS bars, then Rainbow
    c = eng.tick(_snap(bucket="drop", bar=24, drop_start_bar=24, palette="hot_magenta"), rig)
    assert eng.scene_name == LOOK_FAN and c.values[17] == Laser.COLOR_PURPLE and c.values[12] >= 191
    assert tuple(c.values[L]) == tuple(c.values[R])
    eng.tick(_snap(bucket="drop", bar=24 + FAN_BARS - 1, drop_start_bar=24), rig)
    assert eng.scene_name == LOOK_FAN
    c = eng.tick(_snap(bucket="drop", bar=24 + FAN_BARS, drop_start_bar=24), rig)
    assert eng.scene_name == DEFAULT_SCENE and tuple(c.values[L]) == LASER_SCENES["Rainbow"]
    assert eng.accent is False                      # follow mode: no CH1 accent on top
    # peak: ping-pong per bar, both on the block boundary, L blue / R red
    c = eng.tick(_snap(bucket="peak", bar=24 + 16, drop_start_bar=24), rig)
    assert eng.scene_name == LOOK_PINGPONG and c.values[6] == 60 and c.values[18] == 60
    assert c.values[17] == Laser.COLOR_BLUE and c.values[29] == Laser.COLOR_RED
    c = eng.tick(_snap(bucket="peak", bar=24 + 17, drop_start_bar=24), rig)
    assert c.values[6] == 0 and c.values[18] == 60   # odd bar: R only
    c = eng.tick(_snap(bucket="peak", bar=24 + 18, drop_start_bar=24), rig)
    assert c.values[6] == 60 and c.values[18] == 0   # even bar: L only
    assert eng.active is True


def test_drop_accent_in_hold_mode_chops_ch1_then_returns():
    rig = load_rig(RIG)
    clock = FakeClock()
    manual = ManualParams(laser_mode="hold", laser_drop_accent=True)
    eng = LaserShowEngine(clock, manual)
    eng.tick(_snap(bucket="build"), rig)
    assert eng.accent is False
    c = eng.tick(_snap(bucket="drop"), rig)        # entered DROP from below
    assert eng.accent is True and c.values[6] == 80
    seen = set()
    for _ in range(40):
        clock.advance(0.05)
        c = eng.tick(_snap(bucket="drop"), rig)
        seen.add(c.values[6])
        assert c.values[7] == 119                  # pattern channels untouched
    assert seen == {0, 80}
    clock.advance(10.0)
    c = eng.tick(_snap(bucket="drop"), rig)
    assert eng.accent is False and tuple(c.values[L]) == LASER_SCENES["Rainbow"]
    manual.laser_drop_accent = False
    eng.tick(_snap(bucket="build"), rig)
    c = eng.tick(_snap(bucket="drop"), rig)
    assert eng.accent is False and c.values[6] == 80


def test_fan_counts_from_the_drop_entry_not_drop_start_bar():
    """Replay (2026-10-08): drop_start_bar is 2-3 bars BEFORE the bucket commits
    DROP, so counting from it cut the fan to 5-6 bars."""
    rig = load_rig(RIG)
    eng = LaserShowEngine(FakeClock(), ManualParams(laser_mode="follow"))
    eng.tick(_snap(bucket="break", bar=20), rig)
    for bar in range(24, 24 + FAN_BARS):            # entry at 24, rise first seen at 21
        eng.tick(_snap(bucket="drop", bar=bar, drop_start_bar=21), rig)
        assert eng.scene_name == LOOK_FAN, bar
    eng.tick(_snap(bucket="drop", bar=24 + FAN_BARS, drop_start_bar=21), rig)
    assert eng.scene_name == DEFAULT_SCENE


def test_ping_pong_is_sticky_through_a_peak_to_drop_dip_and_look_dwell_smooths_flaps():
    rig = load_rig(RIG)
    eng = LaserShowEngine(FakeClock(), ManualParams(laser_mode="follow"))
    eng.tick(_snap(bucket="break", bar=1), rig)
    for bar in range(10, 10 + FAN_BARS):
        eng.tick(_snap(bucket="drop", bar=bar), rig)
    eng.tick(_snap(bucket="drop", bar=20), rig)
    assert eng.scene_name == DEFAULT_SCENE
    eng.tick(_snap(bucket="peak", bar=22), rig)
    assert eng.scene_name == LOOK_PINGPONG
    eng.tick(_snap(bucket="drop", bar=23), rig)      # PEAK<->DROP flap: no Rainbow blip
    assert eng.scene_name == LOOK_PINGPONG
    eng.tick(_snap(bucket="build", bar=23), rig)     # same bar: dwell holds the look
    assert eng.scene_name == LOOK_PINGPONG and "holding" in eng.reason
    eng.tick(_snap(bucket="build", bar=24), rig)     # 2 bars since Ping-Pong began: allowed
    assert eng.scene_name == LOOK_TENSION
    eng.tick(_snap(bucket="drop", bar=25), rig)      # INTO Fan is always immediate
    assert eng.scene_name == LOOK_FAN
    eng.tick(_snap(bucket="build", bar=26), rig)     # the fan holds through a bucket flap...
    assert eng.scene_name == LOOK_FAN
    eng.tick(_snap(bucket="drop", bar=27), rig)      # ...and a re-entry inside its window is the same drop
    assert eng.scene_name == LOOK_FAN and eng._drop_entry_bar == 25
    eng.tick(_snap(bucket="drop", bar=27, drop_pending=True), rig)   # INTO Dark is immediate too
    assert eng.scene_name == LOOK_DARK
    eng.tick(_snap(bucket="drop", bar=27), rig)      # and OUT of Dark
    assert eng.scene_name == LOOK_FAN


def test_follow_holds_the_last_look_through_a_short_signal_blip():
    rig = load_rig(RIG)
    clock = FakeClock(100.0)
    eng = LaserShowEngine(clock, ManualParams(laser_mode="follow"))
    eng.tick(_snap(bucket="break", bar=1), rig)
    eng.tick(_snap(bucket="peak", bar=2), rig)       # a hot entry is a drop hit: the fan first
    assert eng.scene_name == LOOK_FAN
    eng.tick(_snap(bucket="peak", bar=2 + FAN_BARS), rig)
    assert eng.scene_name == LOOK_PINGPONG
    clock.advance(1.0)
    eng.tick(_snap(bucket="peak", bar=2 + FAN_BARS, pdl_fresh=False), rig)
    assert eng.scene_name == LOOK_PINGPONG and not eng.rotating and "blip" in eng.reason
    clock.advance(10.0)
    eng.tick(_snap(bucket="peak", bar=2 + FAN_BARS, pdl_fresh=False), rig)
    assert eng.rotating and eng.scene_name == DEFAULT_SCENE
