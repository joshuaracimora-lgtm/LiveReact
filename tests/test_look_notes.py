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
