"""rekordbox/song_style.py: one look set per song, held for the whole song, different from the song
before it (Josh, 2026-10-28) -- and the engines that play it (strobe rhythm, wash accent, movement)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines.reactive import ReactiveLayerEngine
from livereact.fixtures.rig import load_rig
from livereact.rekordbox import song_style as ss
from livereact.rekordbox.planner import plan_track
from livereact.scenes import movement as mv
from livereact.scenes.catalog import show_catalog

from test_planner import HIGH, _wave


def _f(**kw) -> ss.SongFeatures:
    base = dict(punch=0.36, brightness=0.04, spread=0.28, drop_energy=0.48, rise=0.17, break_energy=0.17)
    base.update(kw)
    return ss.SongFeatures(**base)


def test_the_same_song_always_gets_the_same_style():
    assert ss.choose(_f()) == ss.choose(_f())
    assert ss.choose(_f(), ss.choose(_f(punch=0.5))) == ss.choose(_f(), ss.choose(_f(punch=0.5)))


def test_the_sound_picks_the_style():
    loud, quiet = ss.choose(_f(drop_energy=0.7)), ss.choose(_f(drop_energy=0.28))
    assert loud.strobe == "downbeat" and quiet.strobe == "none"
    assert ss.choose(_f(brightness=-0.12)).accent == "4" and ss.choose(_f(brightness=0.22)).accent == "24"
    assert ss.choose(_f(rise=0.38)).build == "Chase Wash L/R"


def test_a_song_differs_from_the_one_before_it():
    prev = ss.choose(_f())
    nxt = ss.choose(_f(), prev)      # the same sound twice in a row
    assert nxt.differences(prev) >= ss.MIN_DIFFERENCES


def test_no_waveform_keeps_the_old_show():
    assert ss.choose(None) == ss.DEFAULT_STYLE
    assert ss.choose(None, ss.DEFAULT_STYLE).differences(ss.DEFAULT_STYLE) >= ss.MIN_DIFFERENCES


def test_the_plan_carries_the_style_and_its_build_and_break_looks():
    plan = plan_track(HIGH, track_id=7, bpm=120.0, waveform=_wave())
    assert {s.scene for s in plan.sections if s.bucket == "build"} == {plan.style.build}
    assert {s.scene for s in plan.sections if s.bucket == "break"} == {plan.style.brk}
    again = plan_track(HIGH, track_id=7, bpm=120.0, waveform=_wave(), previous_style=plan.style)
    assert again.style.differences(plan.style) >= ss.MIN_DIFFERENCES
    assert "style[" in plan.summary()


def test_without_a_waveform_the_plan_keeps_the_pool_looks():
    plan = plan_track(HIGH, track_id=7, bpm=120.0)
    assert plan.style == ss.DEFAULT_STYLE and plan.sections[1].scene == "Rising Tide"


def test_the_show_catalog_holds_every_style_look():
    cat = show_catalog()
    for name in (*ss.BUILD_LOOKS, *ss.BREAK_LOOKS):
        assert cat.find(name) is not None, name


# --- the engines ---------------------------------------------------------------------------------

def _snap(**kw) -> ShowSnapshot:
    base = dict(bpm=128.0, energy=1.0, energy_norm=1.0, drop_hit_intensity=1.0)
    base.update(kw)
    return ShowSnapshot(**base)


def _strobe_beats(style: str, bars: int = 16) -> list[tuple[int, int]]:
    rig, eng = load_rig(), ReactiveLayerEngine(FakeClock())
    eng.tick(_snap(bar=0, beat_in_bar=1, beat_phase=0.0, bucket="drop", planned_strobe_style=style), rig)
    lit = []
    for b in range(bars):
        for beat in (1, 2, 3, 4):
            eng.tick(_snap(bar=b, beat_in_bar=beat, beat_phase=0.0, bucket="drop", planned_strobe_style=style), rig)
            if eng.strobe_level > 0:
                lit.append((b, beat))
    return lit


@pytest.mark.parametrize("style, first", [
    ("tail", [(7, 3), (7, 4)]),          # the phrase tail, unchanged
    ("stab", [(7, 4)]),                  # beat 4 alone
    ("downbeat", [(8, 1), (8, 2)]),      # on the next phrase's first beats, not the drop's own bar
    ("sparse", [(15, 3), (15, 4)]),      # every second phrase
    ("none", []),
])
def test_each_strobe_style_has_its_own_rhythm(style, first):
    assert _strobe_beats(style)[:2] == first


def test_the_accent_beat_follows_the_song():
    rig = load_rig()
    for accent, beats in (("4", {4}), ("24", {2, 4})):
        eng = ReactiveLayerEngine(FakeClock())
        flashed = set()
        for beat in (1, 2, 3, 4):
            eng.tick(_snap(bar=1, beat_in_bar=beat, beat_phase=0.0, bucket="drop", planned_accent=accent), rig)
            if eng.wash_level >= 0.99:
                flashed.add(beat)
        assert flashed == beats, accent


def test_the_song_picks_the_opening_shape_and_speed():
    s = _snap(bar=20, drop_start_bar=20)
    assert mv.drop_movement_name(s) == "fan"
    assert mv.drop_movement_name(replace(s, planned_move_start=3)) == "kick_lift"
    assert mv.drop_movement_name(replace(s, bar=28, planned_move_start=3)) == "crowd_sweep"
    a = mv.FALLBACK_ANCHORS
    slow = replace(_snap(bar=21, beat_in_bar=1, drop_start_bar=20), planned_move_speed=0.5)
    assert mv.drop_movement(slow, "left", a) == pytest.approx(mv.drop_movement(
        _snap(bar=20, beat_in_bar=3, drop_start_bar=20), "left", a))
