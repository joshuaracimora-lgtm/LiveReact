"""rekordbox/planner.py: one scheme per track, the loudest drop is the big moment, quiet edges,
count-in targets; plus the PWV5 waveform parser."""

from __future__ import annotations

import struct

from livereact.rekordbox.planner import BIG_MOMENT_HIT, REGULAR_DROP_HIT, PlanRules, plan_track
from livereact.rekordbox.pssi import MOOD_HIGH, MOOD_MID, Phrase, PhraseTable
from livereact.rekordbox.waveform import ColorWaveform, parse_pwv5


def _table(spec, mood=MOOD_HIGH, end=None):
    phrases = []
    for i, (beat, kind, label) in enumerate(spec):
        nxt = spec[i + 1][0] if i + 1 < len(spec) else (end or beat + 32)
        phrases.append(Phrase(index=i + 1, beat=beat, end_beat=nxt, kind=kind, label=label))
    return PhraseTable(mood=mood, end_beat=phrases[-1].end_beat, bank=0, phrases=tuple(phrases))


# intro 1-17, up 17-49, chorus 49-81, down 81-113, up 113-145, chorus 145-209 (2 phrases), outro 209-241
HIGH = _table([(1, 1, "intro"), (17, 2, "up"), (49, 5, "chorus"), (81, 3, "down"), (113, 2, "up"),
               (145, 5, "chorus"), (177, 5, "chorus"), (209, 6, "outro")], end=241)


def _wave(bpm=120.0, loud_beats=(145, 209), quiet_level=4, loud_level=28):
    # 120 bpm -> 0.5 s/beat -> 75 entries per beat; beat 1 at t=0
    n = int(241 * 0.5 * 150)
    heights = []
    for i in range(n):
        beat = 1 + i / 75.0
        loud = loud_beats[0] <= beat < loud_beats[1]
        heights.append(loud_level if loud else (14 if 49 <= beat < 81 else quiet_level))
    z = tuple(0 for _ in heights)
    return ColorWaveform(heights=tuple(heights), reds=z, greens=z, blues=z)


def test_one_scheme_and_repeated_hook():
    plan = plan_track(HIGH, track_id=7, bpm=120.0)
    kinds = [s.kind for s in plan.sections]
    # the two back-to-back chorus phrases (145-177, 177-209) merge into ONE drop section
    assert kinds == ["intro", "build", "drop", "breakdown", "build", "drop", "outro"]
    assert plan.sections[5].beat == 145 and plan.sections[5].end_beat == 209
    drops = plan.drops
    assert len(drops) == 2 and all(d.scene == plan.hook_scene for d in drops) and all(d.is_hook for d in drops)
    assert plan.hook_scene == "Drop Strobe Stab" and plan.palette_seed == 7
    assert {s.scene for s in plan.sections if s.bucket == "break"} == {"Deep Wash Breathe"}
    assert plan.sections[1].scene == "Rising Tide" and plan.sections[-1].scene == "Outro Fade Wash"


def test_loudest_drop_run_is_the_only_big_moment():
    plan = plan_track(HIGH, track_id=7, bpm=120.0, waveform=_wave())
    big = [s for s in plan.sections if s.big_moment]
    assert [(s.beat, s.end_beat) for s in big] == [(145, 209)]   # the whole second chorus
    assert plan.big_moment_index == 5
    first = plan.sections[2]
    assert not first.big_moment and first.hit == REGULAR_DROP_HIT and first.energy < big[0].energy
    assert all(s.hit == BIG_MOMENT_HIT for s in big)
    assert plan.sections[2].energy > plan.sections[0].energy   # measured, not the kind default


def test_without_waveform_longest_latest_drop_run_wins():
    plan = plan_track(HIGH, track_id=7, bpm=120.0)
    assert [s.beat for s in plan.sections if s.big_moment] == [145]


def test_quiet_edges_and_count_in_targets():
    plan = plan_track(HIGH, track_id=7, bpm=120.0)
    intro, outro = plan.sections[0], plan.sections[-1]
    assert not intro.strobe_allowed and not intro.laser_allowed
    assert not outro.strobe_allowed and not outro.laser_allowed and outro.bucket == "outro"
    assert plan.sections[1].strobe_allowed and plan.sections[1].laser_allowed
    assert not plan.sections[3].strobe_allowed                  # a breakdown never strobes
    assert plan.beats_to_drop(40) == 9 and plan.beats_to_drop(150) == -1 and plan.beats_to_drop(144) == 1
    cur, nxt = plan.at_beat(100)
    assert cur.kind == "breakdown" and nxt.kind == "build"
    assert plan.at_beat(0) == (None, plan.sections[0]) and plan.at_beat(241) == (None, None)


def test_rules_can_be_relaxed():
    rules = PlanRules(one_big_moment=False, quiet_edges=False, builds_count_in=False)
    plan = plan_track(HIGH, track_id=7, bpm=120.0, rules=rules)
    assert all(s.big_moment for s in plan.drops) and plan.sections[0].laser_allowed


def test_mid_mood_verses_and_scene_pool_choice():
    mid = _table([(1, 1, "intro"), (17, 2, "verse1"), (49, 8, "bridge"), (65, 9, "chorus"), (129, 10, "outro")],
                 mood=MOOD_MID, end=161)
    pool = {"drop": ["Drop A", "Drop B"], "break": ["Break X"], "build": ["Build Y"], "outro": ["Out Z"]}
    p7 = plan_track(mid, track_id=7, bpm=128.0, scene_pool=pool)
    p8 = plan_track(mid, track_id=8, bpm=128.0, scene_pool=pool)
    assert [s.kind for s in p7.sections] == ["intro", "verse", "build", "drop", "outro"]
    assert p7.hook_scene == "Drop B" and p8.hook_scene == "Drop A"     # deterministic per track id
    assert p7.sections[1].scene == "Break X" and p7.sections[1].bucket == "break"
    assert "Drop B" in p7.summary() and "chorus=drop**" in p7.summary()


def test_parse_pwv5_bit_layout():
    def entry(r, g, b, h):
        return (r << 13) | (g << 10) | (b << 7) | (h << 2)
    ents = [entry(7, 1, 0, 31), entry(0, 0, 7, 0), entry(3, 3, 3, 15)]
    payload = struct.pack(">III", 2, len(ents), 0x960305) + b"".join(struct.pack(">H", e) for e in ents)
    tag = b"PWV5" + struct.pack(">II", 12, 12 + len(payload)) + payload
    data = b"PMAI" + struct.pack(">II", 28, 28 + len(tag)) + b"\x00" * 16 + tag
    w = parse_pwv5(data)
    assert w.heights == (31, 0, 15) and w.reds == (7, 0, 3) and w.blues == (0, 7, 3)
    assert abs(w.duration_s - 3 / 150) < 1e-9
    assert abs(w.energy(0, 3 / 150) - (46 / 93)) < 1e-9
    assert w.low_share(0, 1 / 150) == 7 / 8
    assert parse_pwv5(b"PMAI" + b"\x00" * 24) is None


def test_micro_phrases_are_absorbed():
    # chorus 2 bars, up 2 bars, chorus 16 bars (SWEETER-style): the micro phrases join the build
    spec = [(1, 1, "intro"), (17, 2, "up"), (65, 5, "chorus"), (73, 2, "up"), (81, 5, "chorus"), (145, 6, "outro")]
    plan = plan_track(_table(spec, end=177), track_id=1, bpm=120.0)
    assert [(s.kind, s.beat, s.end_beat) for s in plan.sections] == [
        ("intro", 1, 17), ("build", 17, 81), ("drop", 81, 145), ("outro", 145, 177)]
    # a 4-bar chorus between two builds (TIME-11 wants 8 bars of DROP) is absorbed; the first
    # section is never absorbed even when short; a 4-bar build is fine
    spec2 = [(1, 1, "intro"), (9, 2, "up"), (41, 5, "chorus"), (57, 2, "up"), (73, 5, "chorus"), (137, 6, "outro")]
    plan2 = plan_track(_table(spec2, end=169), track_id=1, bpm=120.0)
    assert [(s.kind, s.beat, s.end_beat) for s in plan2.sections] == [
        ("intro", 1, 9), ("build", 9, 73), ("drop", 73, 137), ("outro", 137, 169)]


def test_short_breakdown_after_a_drop_joins_its_successor():
    # chorus 16 bars, down 4 bars (the kick leaves), up 8 bars, chorus: the breakdown must not be
    # absorbed into the drop (its look would play through the breakdown, D-23) -- the build starts
    # where the drop ends
    spec = [(1, 1, "intro"), (33, 5, "chorus"), (97, 3, "down"), (113, 2, "up"), (145, 5, "chorus"),
            (209, 6, "outro")]
    plan = plan_track(_table(spec, end=241), track_id=1, bpm=120.0)
    assert [(s.kind, s.beat, s.end_beat) for s in plan.sections] == [
        ("intro", 1, 33), ("drop", 33, 97), ("build", 97, 145), ("drop", 145, 209), ("outro", 209, 241)]
    # before an outro: the outro starts on the breakdown
    spec2 = [(1, 1, "intro"), (33, 5, "chorus"), (97, 3, "down"), (113, 6, "outro")]
    plan2 = plan_track(_table(spec2, end=145), track_id=1, bpm=120.0)
    assert [(s.kind, s.beat, s.end_beat) for s in plan2.sections] == [
        ("intro", 1, 33), ("drop", 33, 97), ("outro", 97, 145)]
    # between two drops it stays inside the drop (no 4-bar break run, TIME-11)
    spec3 = [(1, 1, "intro"), (33, 5, "chorus"), (97, 3, "down"), (113, 5, "chorus"), (177, 6, "outro")]
    plan3 = plan_track(_table(spec3, end=209), track_id=1, bpm=120.0)
    assert [(s.kind, s.beat, s.end_beat) for s in plan3.sections] == [
        ("intro", 1, 33), ("drop", 33, 177), ("outro", 177, 209)]


def test_planner_knobs_reach_plan_rules_through_params():
    from livereact import params
    from livereact.core import metronome
    from livereact.rekordbox import planner

    before = params.apply({"MIN_SECTION_BEATS": 16, "REGULAR_DROP_HIT": 0.9})
    try:
        assert planner.PlanRules().min_section_beats == 16
        assert planner.REGULAR_DROP_HIT == 0.9 and metronome.REGULAR_PLAN_HIT == 0.9
        plan = plan_track(HIGH, track_id=7, bpm=120.0)
        assert plan.rules.min_section_beats == 16 and all(d.hit in (0.9, 1.0) for d in plan.drops)
    finally:
        params.restore(before)
    assert planner.PlanRules().min_section_beats == 32 and metronome.REGULAR_PLAN_HIT == 0.92
