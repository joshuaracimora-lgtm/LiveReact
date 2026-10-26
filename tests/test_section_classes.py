"""Section classes (rekordbox/section_classes.py), section features, and the shared show catalog."""
from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from livereact.rekordbox import section_classes
from livereact.rekordbox.planner import plan_track
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
from livereact.rekordbox.sections import FEATURE_NAMES, section_features, track_profile
from livereact.rekordbox.waveform import ColorWaveform
from livereact.scenes.catalog import SceneCatalog, show_catalog
from livereact.scenes.base import EnergyBucket


def _table():
    spec = [(1, 1, "intro"), (17, 2, "up"), (49, 5, "chorus"), (81, 3, "down"), (113, 2, "up"),
            (145, 5, "chorus"), (209, 6, "outro")]
    phrases = []
    for i, (beat, kind, label) in enumerate(spec):
        nxt = spec[i + 1][0] if i + 1 < len(spec) else 241
        phrases.append(Phrase(index=i + 1, beat=beat, end_beat=nxt, kind=kind, label=label,
                              beat2=150 if label == "chorus" and beat == 145 else 0))
    return PhraseTable(mood=MOOD_HIGH, end_beat=241, bank=0, phrases=tuple(phrases))


def _wave(red=1, green=1, blue=1):
    n = int(241 * 0.5 * 150)          # 120 bpm: beat 1 at t=0, 75 entries per beat
    heights = tuple(28 if 145 <= 1 + i / 75.0 < 209 else 10 for i in range(n))
    return ColorWaveform(heights=heights, reds=(red,) * n, greens=(green,) * n, blues=(blue,) * n)


def _book(classes, palette=None):
    return section_classes.from_dict({"schema": 1, "classes": classes, **({"palette_by_profile": palette} if palette else {})})


def test_the_shipped_file_is_valid_and_approves_nothing():
    d = json.loads(section_classes.CLASSES_PATH.read_text(encoding="utf-8"))
    assert section_classes.validate(d) == []
    book = section_classes.load()
    assert book.classes and book.approved_scenes() == [] and not book.palette_approved


def test_night_one_the_shipped_classes_change_nothing_but_the_tags():
    """With nothing approved the plan is today's plan: same scenes, hits, flags, palette -- only cls added."""
    t, w = _table(), _wave()
    before = plan_track(t, track_id=3, bpm=120.0, waveform=w)
    after = plan_track(t, track_id=3, bpm=120.0, waveform=w, classes=section_classes.load())
    assert [replace(s, cls="") for s in after.sections] == list(before.sections)
    assert replace(after, sections=before.sections) == before
    assert any(s.cls for s in after.sections)


def test_an_approved_class_changes_only_its_own_sections():
    t, w = _table(), _wave()
    book = _book([{"name": "big", "kind": "drop", "rule": {"energy": [0.8, 1.0]}, "scene": "Drop Beam Fan",
                   "approved": True}])
    plan = plan_track(t, track_id=3, bpm=120.0, waveform=w, classes=book)
    drops = plan.drops
    assert [s.cls for s in drops] == ["", "big"]                        # only the loud chorus matches
    assert drops[0].scene == plan.hook_scene and drops[1].scene == "Drop Beam Fan"
    assert all(s.scene != "Drop Beam Fan" for s in plan.sections if s.kind != "drop")
    unapproved = _book([dict(book.classes[0].__dict__, rule={"energy": [0.8, 1.0]}, approved=False)])
    assert plan_track(t, track_id=3, bpm=120.0, waveform=w, classes=unapproved).drops[1].scene == plan.hook_scene


def test_a_look_note_hook_scene_beats_a_class():
    from livereact.rekordbox.look_notes import from_dict
    book = _book([{"name": "big", "kind": "drop", "rule": {"energy": [0.8, 1.0]}, "scene": "Drop Beam Fan",
                   "approved": True}])
    look = from_dict({"schema": 1, "track": "x", "hook_scene": "Drop Wash Slam Clear"})
    plan = plan_track(_table(), track_id=3, bpm=120.0, waveform=_wave(), classes=book, look=look)
    assert {s.scene for s in plan.drops} == {"Drop Wash Slam Clear"}


def test_palette_by_profile_only_once_approved_and_a_look_note_wins():
    from livereact.rekordbox.look_notes import from_dict
    pal = {"approved": True, "high": ["acid_yellow"], "mid": ["deep_blue"], "low": ["blood_red"]}
    assert plan_track(_table(), track_id=3, bpm=120.0, waveform=_wave(1, 1, 6), classes=_book([], pal)).palette == "acid_yellow"
    assert plan_track(_table(), track_id=3, bpm=120.0, waveform=_wave(6, 1, 1), classes=_book([], pal)).palette == "blood_red"
    assert plan_track(_table(), track_id=3, bpm=120.0, waveform=_wave(), classes=_book([], pal)).palette == "deep_blue"
    off = dict(pal, approved=False)
    assert plan_track(_table(), track_id=3, bpm=120.0, waveform=_wave(1, 1, 6), classes=_book([], off)).palette == ""
    look = from_dict({"schema": 1, "track": "x", "hook_palette": "uv_violet"})
    assert plan_track(_table(), track_id=3, bpm=120.0, waveform=_wave(1, 1, 6), classes=_book([], pal),
                      look=look).palette == "uv_violet"


def test_validation_catches_typos_and_josh_only_fields():
    bad = {"schema": 1, "classes": [
        {"name": "a", "kind": "drop", "rule": {"bras": [0, 8]}},
        {"name": "b", "kind": "drop", "rule": {"bars": [9, 1]}},
        {"name": "c", "kind": "drop", "rule": None, "scene": "Deep Wash Breathe"},   # a BREAK look on a drop
        {"name": "d", "kind": "drop", "rule": None, "approved": True},               # approved, no scene
        {"name": "e", "kind": "drip", "rule": None}]}
    errs = " | ".join(section_classes.validate(bad))
    for want in ("unknown feature 'bras'", "bars must be [min, max]", "is a break look", "approved without a scene",
                 "kind must be one of"):
        assert want in errs


def test_a_broken_file_never_takes_the_show_down(tmp_path, caplog):
    p = tmp_path / "section_classes.json"
    p.write_text('{"schema": 1, "classes": [{"name": "x", "kind": "drop", "rule": {"nope": [0, 1]}}]}', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="livereact.rekordbox.section_classes"):
        assert section_classes.load(p) is section_classes.EMPTY
    assert "ignored" in caplog.text
    assert section_classes.load(tmp_path / "missing.json") is section_classes.EMPTY


def test_section_features_measure_each_planned_section():
    t, w = _table(), _wave(1, 2, 5)
    plan = plan_track(t, track_id=1, bpm=120.0, waveform=w)
    feats = section_features(plan, t, waveform=w, bpm=120.0)
    assert [f.index for f in feats] == [s.index for s in plan.sections]
    big = next(f for f in feats if f.kind == "drop" and f.beat == 145)
    assert big.bars == pytest.approx(16.0) and big.energy > 0.8 and big.inner_beats == 1
    assert big.high_share == pytest.approx(5 / 8) and big.energy_delta > 0
    assert 0 < big.rel_position < 1
    assert set(FEATURE_NAMES) <= set(big.__dataclass_fields__)
    assert track_profile(w) == "high" and track_profile(_wave(5, 2, 1)) == "low" and track_profile(None) == ""


def test_reserved_scenes_are_playable_but_never_rotated():
    plain = SceneCatalog()
    cat = show_catalog(extra=["Drop Beam Fan", "No Such Scene"])
    assert cat.names_by_bucket() == plain.names_by_bucket()
    assert cat.find("Drop Beam Fan") is not None and plain.find("Drop Beam Fan") is None
    picks = {cat.pick(EnergyBucket.DROP).name for _ in range(6)}
    assert "Drop Beam Fan" not in picks


def test_sim_and_live_build_the_same_catalog_and_pool():
    """app.py and sim/harness.py both call show_catalog(...).names_by_bucket() for the planner pool."""
    import inspect
    from livereact import app
    from livereact.sim import harness
    assert "show_catalog(" in inspect.getsource(app.App.__init__)
    src = inspect.getsource(harness._run_sim)
    assert "show_catalog(" in src and "scene_pool=phrase.catalog.names_by_bucket()" in src
