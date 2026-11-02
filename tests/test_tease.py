"""P-24 (2026-10-30): the Tease -- the kick-free bars into a drop, planned from the colour waveform
(rekordbox/planner.py) and lit by engines/tease.py: dim start, follows the music, punches on stabs with a
smooth shutter-down, song colour -> the flash tint, and the wash held under the roll that follows."""

from __future__ import annotations

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines.reactive import ReactiveLayerEngine, flash_colour
from livereact.engines.tease import (
    TEASE_BRIGHT, TEASE_CEIL, TEASE_FLOOR, TEASE_HOLD_OFF_SHARE, TEASE_NUDGE, TeaseLook, apply_tease,
)
from livereact.fixtures.rig import load_rig
from livereact.rekordbox.look_notes import from_dict
from livereact.rekordbox.planner import (
    TEASE_MAX_BEATS, TEASE_MIN_SHARE, TEASE_SONG_MIN_DROP_ENERGY, PlanRules, plan_track,
)
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
from livereact.rekordbox.waveform import ColorWaveform

PER_BEAT = 75   # 120 bpm, 150 entries/s -> 75 per beat; beat 1 at t=0


def _table(spec, end):
    phrases = []
    for i, (beat, kind, label) in enumerate(spec):
        nxt = spec[i + 1][0] if i + 1 < len(spec) else end
        phrases.append(Phrase(index=i + 1, beat=beat, end_beat=nxt, kind=kind, label=label))
    return PhraseTable(mood=MOOD_HIGH, end_beat=end, bank=0, phrases=tuple(phrases))


def _wave(end, kicks, loud=(), mid=(), loud_level=28, mid_level=14, quiet_level=6):
    """``kicks``/``loud``/``mid``: lists of (beat0, beat1). A kick beat has red 7 in its first eighth."""
    def inside(b, spans):
        return any(a <= b < z for a, z in spans)
    heights, reds = [], []
    for i in range(int((end - 1) * PER_BEAT)):
        beat = 1 + i / PER_BEAT
        frac = beat - int(beat)
        reds.append(7 if inside(beat, kicks) and frac < 0.12 else 0)
        heights.append(loud_level if inside(beat, loud) else mid_level if inside(beat, mid) else quiet_level)
    z = tuple(0 for _ in heights)
    return ColorWaveform(heights=tuple(heights), reds=tuple(reds), greens=z, blues=z)


# intro 1-17, up 17-65, chorus 65-129, outro 129-161
SPEC = [(1, 1, "intro"), (17, 2, "up"), (65, 5, "chorus"), (129, 6, "outro")]
NO_ROLL = PlanRules(roll_beats=0)


def test_the_kick_free_bars_into_a_drop_are_a_tease_and_never_in_the_outro():
    wave = _wave(161, kicks=[(17, 49), (65, 129)], loud=[(65, 129)], mid=[(17, 65)])
    plan = plan_track(_table(SPEC, 161), track_id=1, bpm=120.0, waveform=wave, rules=NO_ROLL)
    assert plan.teases == ((49, 65),)
    assert plan.tease_at(49) == 0.0 and plan.tease_at(57) == 0.5
    assert plan.tease_at(65) == -1.0 and plan.tease_at(140) == -1.0   # the kick-free outro is not a Tease
    assert plan.tease_at(30) == -1.0


def test_one_drum_bar_inside_is_a_hit_two_end_the_tease():
    """Tongue Tied 1:22.8: one bar with a hit between kick-free bars; the Tease runs through it."""
    one = _wave(161, kicks=[(17, 49), (53, 57), (65, 129)], loud=[(65, 129)], mid=[(17, 65)])
    assert plan_track(_table(SPEC, 161), track_id=1, bpm=120.0, waveform=one, rules=NO_ROLL).teases == ((49, 65),)
    two = _wave(161, kicks=[(17, 49), (53, 61), (65, 129)], loud=[(65, 129)], mid=[(17, 65)])
    assert plan_track(_table(SPEC, 161), track_id=1, bpm=120.0, waveform=two, rules=NO_ROLL).teases == ((61, 65),)


def test_only_a_drop_near_the_loudest_gets_a_tease():
    """'Tease loose, strobe strict': 70 % of the loudest drop, looser than the roll strobe's 90 %."""
    spec = [(1, 1, "intro"), (17, 2, "up"), (65, 5, "chorus"), (97, 2, "up"), (145, 5, "chorus"), (209, 6, "outro")]
    table = _table(spec, 241)
    kicks = [(17, 49), (65, 97), (97, 129), (145, 209)]
    weak = _wave(241, kicks=kicks, loud=[(145, 209)], mid=[(65, 97)], mid_level=10)   # first chorus ~1/3
    plan = plan_track(table, track_id=1, bpm=120.0, waveform=weak, rules=NO_ROLL)
    assert plan.teases == ((129, 145),)
    strong = _wave(241, kicks=kicks, loud=[(65, 97), (145, 209)])
    assert plan_track(table, track_id=1, bpm=120.0, waveform=strong, rules=NO_ROLL).teases == ((49, 65), (129, 145))
    assert TEASE_MIN_SHARE == 0.7


def test_only_an_intense_song_gets_a_tease_but_an_ear_note_cue_always_does():
    """Josh: "the justification for a tease [is] the intensity of the song" (drops' mean waveform height)."""
    quiet_song = _wave(161, kicks=[(17, 49), (65, 129)], loud=[(65, 129)], mid=[(17, 65)], loud_level=12)
    plan = plan_track(_table(SPEC, 161), track_id=1, bpm=120.0, waveform=quiet_song, rules=NO_ROLL)
    assert plan.teases == ()
    look = from_dict({"schema": 1, "cues": [{"look": "tease", "beat": 57}]})
    cued = plan_track(_table(SPEC, 161), track_id=1, bpm=120.0, waveform=quiet_song, rules=NO_ROLL, look=look)
    assert cued.teases == ((57, 65),)
    assert 12 / 31 < TEASE_SONG_MIN_DROP_ENERGY < 28 / 31


def test_a_cue_that_starts_inside_a_drop_is_a_bright_tease():
    """Thriller's 63 s cue sits in drop 1 and runs to the roll: bright. A cue in a build is a normal Tease."""
    spec = [(1, 1, "intro"), (17, 5, "chorus"), (49, 2, "up"), (65, 5, "chorus"), (129, 6, "outro")]
    def plan_with(beat):
        look = from_dict({"schema": 1, "cues": [{"look": "tease", "beat": beat}]})
        return plan_track(_table(spec, 161), track_id=1, bpm=120.0, look=look, rules=NO_ROLL)
    in_drop = plan_with(41)
    assert in_drop.teases == ((41, 65),) and in_drop.tease_bright(41) and in_drop.tease_bright(60)
    in_build = plan_with(57)
    assert in_build.teases == ((57, 65),) and not in_build.tease_bright(57)


def test_a_tease_is_the_last_sixteen_bars_at_most():
    spec = [(1, 1, "intro"), (17, 2, "up"), (129, 5, "chorus"), (193, 6, "outro")]
    wave = _wave(225, kicks=[(17, 25), (129, 193)], loud=[(129, 193)])
    plan = plan_track(_table(spec, 225), track_id=1, bpm=120.0, waveform=wave, rules=NO_ROLL)
    assert plan.teases == ((129 - TEASE_MAX_BEATS, 129),)


def test_a_tease_into_a_roll_lead_in_ends_where_the_roll_starts_and_the_roll_holds_it():
    spec = [(1, 1, "intro"), (17, 2, "up"), (97, 5, "chorus"), (161, 6, "outro")]
    wave = _wave(193, kicks=[(17, 49), (97, 161)], loud=[(97, 161)], mid=[(17, 97)])
    plan = plan_track(_table(spec, 193), track_id=1, bpm=120.0, waveform=wave)
    roll = next(s for s in plan.sections if s.roll)
    assert plan.teases == ((49, roll.beat),)
    assert plan.tease_hold(roll.beat) and plan.tease_hold(roll.end_beat - 1)
    assert not plan.tease_hold(roll.end_beat) and not plan.tease_hold(40)


def test_a_drop_rekordbox_left_inside_an_up_phrase_is_split_out():
    """Tongue Tied: the kick returns at 1:28.7, mid-phrase, and the planner held one 50 s build."""
    spec = [(1, 1, "intro"), (17, 2, "up"), (129, 5, "chorus"), (193, 6, "outro")]
    wave = _wave(225, kicks=[(17, 49), (81, 113), (129, 193)], loud=[(81, 113), (129, 193)], mid=[(17, 49)])
    plan = plan_track(_table(spec, 225), track_id=1, bpm=120.0, waveform=wave, rules=NO_ROLL)
    kinds = [(s.kind, s.beat, s.end_beat) for s in plan.sections]
    assert ("drop", 81, 113) in kinds and ("build", 17, 81) in kinds and ("build", 113, 129) in kinds
    assert plan.teases == ((49, 81), (113, 129))
    # a build with kicks all the way through is left alone
    steady = _wave(225, kicks=[(17, 193)], loud=[(81, 113), (129, 193)])
    assert not any(s.kind == "drop" and s.beat == 81
                   for s in plan_track(_table(spec, 225), track_id=1, bpm=120.0, waveform=steady, rules=NO_ROLL).sections)


def test_an_ear_note_tease_runs_to_the_next_drop_and_is_not_a_frame_cue():
    look = from_dict({"schema": 1, "cues": [{"look": "tease", "beat": 57}, {"look": "whiteout", "beat": 100}]})
    plan = plan_track(_table(SPEC, 161), track_id=1, bpm=120.0, look=look, rules=NO_ROLL)   # no waveform
    assert plan.teases == ((57, 65),)
    assert [c[2] for c in plan.cues] == ["whiteout"]


def _snap(**kw) -> ShowSnapshot:
    base = dict(t=0.0, bpm=120.0, palette="deep_amber", planned_song_palette="deep_amber",
                planned_flash_white=0.6, audio_fresh=True, energy=0.2, audio_band_low_abs=1.0)
    base.update(kw)
    return ShowSnapshot(**base)


def _run(look, t0, t1, dt=0.023, **kw):
    out = []
    t = t0
    while t < t1:
        out.append((t, look.update(_snap(t=t, **{k: (v(t) if callable(v) else v) for k, v in kw.items()}))))
        t += dt
    return out


def test_the_tease_climbs_on_bar_position_the_music_only_nudges_it_and_it_drifts_to_the_tint():
    """Josh: "The Bible is probably correct" (bar position, LOOK-04) -- "Ramp + music nudges it"."""
    def level_at(progress, energy_fn, t1=4.0):
        look = TeaseLook()
        _run(look, 0.0, 30.0, planned_tease_progress=-1.0, energy=0.8)      # a loud drop sets the peak
        return _run(look, 30.0, 30.0 + t1, planned_tease_progress=progress, energy=energy_fn)[-1][1]
    start = level_at(0.0, 0.3)
    assert abs(start[0] - TEASE_FLOOR) < 0.01                               # dim at its start ...
    end = level_at(1.0, 0.3)
    assert abs(end[0] - TEASE_CEIL) < 0.01                                   # ... its ceiling at its end
    loud_start = level_at(0.0, lambda t: 0.3 if t < 32.0 else 0.8)
    assert TEASE_FLOOR < loud_start[0] <= TEASE_FLOOR + TEASE_NUDGE + 1e-6   # louder music: a nudge, not a jump
    quiet_end = level_at(1.0, lambda t: 0.5 if t < 32.0 else 0.2)
    assert TEASE_CEIL - TEASE_NUDGE - 1e-6 <= quiet_end[0] < TEASE_CEIL      # quieter: a nudge down
    assert all(abs(a - b) < 1e-6 for a, b in zip(end[1], flash_colour("deep_amber", 0.6)))


def test_a_bright_tease_follows_the_stab_bursts_and_dips_between_them():
    """Thriller 1:03-1:17 (an ear-note cue inside drop 1): "it can be bright there, and when the intensity
    drops in the periods between stabs (short ~1 sec bursts), the brightness can decrease with it"."""
    look = TeaseLook()
    _run(look, 0.0, 30.0, planned_tease_progress=-1.0, energy=0.8)
    burst = lambda t: 0.8 if (t % 2.0) < 1.0 else 0.4
    out = _run(look, 30.0, 40.0, planned_tease_progress=0.3, planned_tease_bright=True, energy=burst)
    levels = [x[1][0] for x in out if x[0] > 34.0]
    assert max(levels) > TEASE_BRIGHT + 0.1 and min(levels) < TEASE_BRIGHT - 0.1
    assert sum(levels) / len(levels) > TEASE_CEIL                            # bright overall


def test_a_stab_punches_up_fast_and_shutters_down_smoothly():
    look = TeaseLook()
    base = _run(look, 0.0, 3.0, planned_tease_progress=0.5, audio_band_low_abs=10.0)
    level0 = base[-1][1][0]
    hit = _run(look, 3.0, 3.06, planned_tease_progress=0.5, audio_band_low_abs=40.0)
    after = _run(look, 3.06, 5.0, planned_tease_progress=0.5, audio_band_low_abs=10.0)
    peak = max(x[1][0] for x in hit + after)
    assert peak > level0 + 0.15                       # it punches
    levels = [x[1][0] for x in after if x[0] > 3.2]
    steps = [a - b for a, b in zip(levels, levels[1:])]
    assert all(s >= -1e-9 for s in steps)            # on the way down it only falls ...
    assert max(steps) < 0.03                          # ... smoothly, no jump from one value to the next
    assert levels[-1] < level0 + 0.03                 # and settles back on the music's level
    assert look.onsets == 1


def test_after_the_tease_the_roll_holds_its_level_in_the_tint():
    look = TeaseLook()
    _run(look, 0.0, 3.0, planned_tease_progress=0.9, energy=0.5)
    level, rgb, hold = look.update(_snap(t=3.1, planned_tease_progress=-1.0, planned_tease_hold=True))
    assert hold and level > TEASE_FLOOR and rgb == flash_colour("deep_amber", 0.6)
    assert look.update(_snap(t=3.2, planned_tease_progress=-1.0, planned_tease_hold=False)) is None


def test_apply_tease_overwrites_in_the_tease_and_is_a_floor_under_a_flash_or_in_the_hold():
    rig = load_rig("rigs/junior_crib_fan.json")
    bar = rig.fixture("bar")
    merged = bytearray(512)
    merged[bar.offset] = 200
    apply_tease(merged, rig, 0.42, (1.0, 0.4, 0.0), strobe_on=False, hold=False)
    assert merged[bar.offset] < 60                    # dim even over a brighter build wash
    merged[bar.offset] = 255
    apply_tease(merged, rig, 0.42, (1.0, 0.4, 0.0), strobe_on=True, hold=True)
    assert merged[bar.offset] == 255                  # a flash stays
    merged[bar.offset] = 0
    apply_tease(merged, rig, 0.8, (1.0, 0.4, 0.0), strobe_on=False, hold=True)
    off = merged[bar.offset]
    merged[bar.offset] = 60                           # an early, dim roll flash
    apply_tease(merged, rig, 0.8, (1.0, 0.4, 0.0), strobe_on=True, hold=True)
    on = merged[bar.offset]
    assert off > 31 and on > off                      # between flashes it dips but holds; a flash stays above it
    assert abs(off - on * TEASE_HOLD_OFF_SHARE) <= 1


def test_no_pre_drop_strobe_inside_a_tease_or_before_a_lesser_drop():
    rig = load_rig("rigs/junior_crib_fan.json")
    common = dict(t=0.0, bpm=120.0, energy=0.4, energy_norm=0.4, bucket="build", plan_active=True,
                  drop_armed=True, rising=True, rise_index=1.6, beats_to_drop=6)
    eng = ReactiveLayerEngine(FakeClock())
    eng.tick(ShowSnapshot(**common, planned_strobe_allowed=True, planned_tease_progress=-1.0), rig)
    assert eng.strobe_level > 0                       # the count-in as before
    eng = ReactiveLayerEngine(FakeClock())
    eng.tick(ShowSnapshot(**common, planned_strobe_allowed=True, planned_tease_progress=0.5), rig)
    assert eng.strobe_level == 0
    eng = ReactiveLayerEngine(FakeClock())
    eng.tick(ShowSnapshot(**common, planned_strobe_allowed=False, planned_tease_progress=-1.0), rig)
    assert eng.strobe_level == 0


def test_apply_tease_keeps_the_heads_in_the_tease_colour_under_a_flash():
    """Run 6: the heads flipped between the drop's magenta and the Tease's orange on every flash."""
    from livereact.merge.merge import Contribution
    rig = load_rig("rigs/junior_crib_fan.json")
    head = next(iter(rig.by_role("spot")))
    want = Contribution()
    head.set_color(want, 1.0, 0.4, 0.0, 0.0)
    merged = bytearray(512)
    apply_tease(merged, rig, 0.6, (1.0, 0.4, 0.0), strobe_on=True, hold=False)
    touched = [i for i in range(512) if want.touched[i]]
    assert touched and all(merged[i] == want.values[i] for i in touched)
