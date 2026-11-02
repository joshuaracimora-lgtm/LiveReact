"""Look notes: schema, loader, and how the planner honours them."""

from __future__ import annotations

import json

import pytest

from livereact.rekordbox.look_notes import (
    TEMPLATE,
    LookNotes,
    from_dict,
    load_look_notes,
    look_notes_path_for,
    write_template,
)
from livereact.rekordbox.planner import BIG_MOMENT_HIT, REGULAR_DROP_HIT, plan_track
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable


def _table():
    spec = [(1, 1, "intro"), (33, 2, "up"), (65, 5, "chorus"), (129, 3, "down"), (161, 2, "up"), (193, 5, "chorus"), (257, 6, "outro")]
    ph = []
    for i, (b, k, lb) in enumerate(spec):
        ph.append(Phrase(index=i + 1, beat=b, end_beat=(spec[i + 1][0] if i + 1 < len(spec) else 289), kind=k, label=lb))
    return PhraseTable(mood=MOOD_HIGH, end_beat=289, bank=0, phrases=tuple(ph))


def test_template_validates_and_todo_is_ignored(tmp_path):
    assert from_dict(dict(TEMPLATE)).strobe == "hard"
    p = write_template(tmp_path / "x.look.json", "x")
    assert load_look_notes(p) is None                        # scaffold, not filled in
    d = json.loads(p.read_text())
    d.pop("_todo")
    d["hook_palette"] = "blood_red"
    p.write_text(json.dumps(d))
    ln = load_look_notes(p)
    assert isinstance(ln, LookNotes) and ln.hook_palette == "blood_red" and ln.track == "x"
    assert load_look_notes(tmp_path / "missing.look.json") is None


@pytest.mark.parametrize("bad", [
    {"hook_colour": "red"},                       # typo'd key
    {"strobe": "medium"},
    {"big_moment": "second"},
    {"big_moment": {"drop": 2, "beat": 3}},
    {"big_moment": {"drop": 0}},
    {"intro": {"laser": "no"}},
    {"intro": {"dim": True}},
    {"hook_palette": "crimson"},
    {"mood": "dark"},
    {"schema": 2},
])
def test_bad_notes_raise(bad):
    with pytest.raises(ValueError):
        from_dict(dict(TEMPLATE, **bad))


def test_path_lookup_is_nfc_tolerant(tmp_path):
    (tmp_path / "Café.look.json").write_text("{}")
    p = look_notes_path_for("Café.mp3", tmp_path)
    assert p.exists()
    assert look_notes_path_for(tmp_path / "other.mp3").name == "other.look.json"


def test_planner_honours_hook_big_moment_strobe_and_edges():
    look = from_dict(dict(TEMPLATE, hook_scene="Drop Wash Slam Clear", hook_palette="cold_steel",
                          big_moment={"drop": 1}, strobe="soft", intro={"laser": True, "strobe": False},
                          outro={"laser": None, "strobe": None}, mood=["dark"]))
    plan = plan_track(_table(), track_id=3, bpm=125.0, look=look)
    assert plan.hook_scene == "Drop Wash Slam Clear" and plan.palette == "cold_steel" and plan.mood_words == ("dark",)
    drops = plan.drops
    assert [d.big_moment for d in drops] == [True, False]                 # named drop, not the loudest
    assert drops[0].hit == BIG_MOMENT_HIT and drops[1].hit == REGULAR_DROP_HIT
    build = plan.sections[1]
    assert build.kind == "build" and not build.strobe_allowed             # soft: no count-in strobe
    assert all(d.strobe_allowed for d in drops)
    assert plan.sections[0].laser_allowed and not plan.sections[0].strobe_allowed   # intro override
    assert not plan.sections[-1].laser_allowed                            # outro: planner default (quiet)


def test_big_moment_by_beat_time_and_none():
    tbl = _table()
    by_beat = plan_track(tbl, track_id=1, bpm=125.0, look=from_dict(dict(TEMPLATE, big_moment={"beat": 200})))
    assert [d.big_moment for d in by_beat.drops] == [False, True]
    by_t = plan_track(tbl, track_id=1, bpm=120.0, look=from_dict(dict(TEMPLATE, big_moment={"t": 40.0})))  # beat 81
    assert [d.big_moment for d in by_t.drops] == [True, False]
    none = plan_track(tbl, track_id=1, bpm=125.0, look=from_dict(dict(TEMPLATE, big_moment="none")))
    assert not any(d.big_moment for d in none.drops) and none.big_moment_index is None
    assert all(d.hit == REGULAR_DROP_HIT for d in none.drops)


def test_no_drop_mood_disables_drop_strobe_and_big_moment():
    plan = plan_track(_table(), track_id=1, bpm=125.0, look=from_dict(dict(TEMPLATE, mood=["tool-track"])))
    assert not any(d.big_moment for d in plan.drops) and not any(d.strobe_allowed for d in plan.sections)
    strobe_none = plan_track(_table(), track_id=1, bpm=125.0, look=from_dict(dict(TEMPLATE, strobe="none")))
    assert not any(s.strobe_allowed for s in strobe_none.sections)


# --- laser_scene / laser_cues (2026-10-29): a track picks the fan's look per moment ---------------

def test_laser_scene_and_cues_load_and_reach_the_plan():
    d = dict(TEMPLATE, laser_scene="Blue Sweep",
             laser_cues=[{"on": "big_moment", "scene": "Spinning Line"}, {"on": "wub", "scene": "Breathing Dots"}])
    ln = from_dict(d)
    assert ln.laser_scene == "Blue Sweep"
    assert ln.laser_cues == (("big_moment", "Spinning Line"), ("wub", "Breathing Dots"))
    plan = plan_track(_table(), track_id=1, bpm=120.0, look=ln)
    assert plan.laser_scene == "Blue Sweep" and dict(plan.laser_cues)["wub"] == "Breathing Dots"
    drops = [s for s in plan.sections if s.kind == "drop"]
    assert drops
    for s in plan.sections:
        if s.kind != "drop":
            assert s.laser_scene == "Blue Sweep"          # the track's scene everywhere the lasers may fire
    assert {s.laser_scene for s in drops if s.big_moment} == {"Spinning Line"}   # the big moment's cue wins
    assert {s.laser_scene for s in drops if not s.big_moment} <= {"Blue Sweep"}
    # no notes at all: nothing picked, the fan keeps its default
    plan = plan_track(_table(), track_id=1, bpm=120.0)
    assert plan.laser_scene == "" and plan.laser_cues == () and all(s.laser_scene == "" for s in plan.sections)


@pytest.mark.parametrize("bad", [
    {"laser_scene": "Laser Fan Deluxe"},                          # not a library scene
    {"laser_cues": {"on": "drop", "scene": "Rainbow"}},            # not a list
    {"laser_cues": [{"on": "chorus", "scene": "Rainbow"}]},        # not a trigger
    {"laser_cues": [{"on": "drop", "scene": "Nope"}]},
    {"laser_cues": [{"on": "drop", "scene": "Rainbow", "color": "blue"}]},   # extra key
    {"laser_cues": [{"on": "drop", "scene": "Rainbow"}, {"on": "drop", "scene": "Blue Sweep"}]},  # twice
])
def test_bad_laser_notes_raise(bad):
    with pytest.raises(ValueError):
        from_dict(dict(TEMPLATE, **bad))


def test_timed_cues_load_validate_and_reach_the_plan():
    """2026-10-29: Josh's ear notes ("drums cut ... straight to white out") as timed cues."""
    from livereact.rekordbox.look_notes import Cue, from_dict
    from livereact.rekordbox.planner import plan_track
    from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
    d = {"schema": 1, "cues": [{"look": "whiteout", "t": 10.0}, {"look": "whiteout", "beat": 40, "beats": 2}]}
    notes = from_dict(d)
    assert notes.cues == (Cue(look="whiteout", t=10.0, beat=None, beats=4), Cue(look="whiteout", t=None, beat=40, beats=2))
    spec = [(1, 1, "intro", 17), (17, 2, "up", 49), (49, 5, "chorus", 81), (81, 6, "outro", 113)]
    table = PhraseTable(mood=MOOD_HIGH, end_beat=113, bank=0,
                        phrases=tuple(Phrase(index=i + 1, beat=b, end_beat=e, kind=k, label=l) for i, (b, k, l, e) in enumerate(spec)))
    plan = plan_track(table, track_id=1, bpm=120.0, look=notes)   # 120 bpm, beat 1 at 0 s -> 10 s = beat 21
    assert plan.cues == ((21, 25, "whiteout"), (40, 42, "whiteout"))
    assert plan.cue_look(20) == "" and plan.cue_look(21) == "whiteout" and plan.cue_look(24) == "whiteout"
    assert plan.cue_look(25) == "" and plan.cue_look(41) == "whiteout" and plan.cue_look(42) == ""
    for bad in ({"cues": [{"look": "sparkle", "t": 1}]}, {"cues": [{"look": "whiteout"}]},
                {"cues": [{"look": "whiteout", "t": 1, "beat": 2}]}, {"cues": [{"look": "whiteout", "t": -1}]},
                {"cues": [{"look": "whiteout", "t": 1, "beats": 0}]}, {"cues": "whiteout"}):
        with pytest.raises(ValueError):
            from_dict({"schema": 1, **bad})


def test_path_lookup_finds_notes_named_after_a_shortened_file(tmp_path):
    """Run 6 (2026-10-30): Thriller's notes were "..._Haunted_R.look.json", the USB track
    "..._Haunted_Remix_KLICKAUD": the live show never loaded them."""
    (tmp_path / "Michael_Jackson_-_Thriller_BVRNOUT_Haunted_R.look.json").write_text("{}")
    p = look_notes_path_for("Michael_Jackson_-_Thriller_BVRNOUT_Haunted_Remix_KLICKAUD", tmp_path)
    assert p.exists()
    assert not look_notes_path_for("Michael", tmp_path).exists()            # too short to count
    (tmp_path / "Michael_Jackson_-_Thriller_BVRNOUT_Haunted_Re.look.json").write_text("{}")
    assert not look_notes_path_for("Michael_Jackson_-_Thriller_BVRNOUT_Haunted_Remix_KLICKAUD", tmp_path).exists()   # ambiguous
