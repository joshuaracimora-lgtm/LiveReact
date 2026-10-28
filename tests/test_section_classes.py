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


def test_the_shipped_file_is_valid_and_approves_only_the_profile_palette():
    d = json.loads(section_classes.CLASSES_PATH.read_text(encoding="utf-8"))
    assert section_classes.validate(d) == []
    book = section_classes.load()
    # Josh approved D-23 on 2026-10-28 ("approve D-23 and have the lasers follow the palette"); no class yet
    assert book.classes and book.approved_scenes() == [] and book.palette_approved


def test_the_shipped_classes_change_only_the_tags_and_the_profile_palette():
    """No class is approved, so the plan keeps today's scenes, hits and flags (only cls added); the palette
    is D-23's pick for the track's frequency profile (approved 2026-10-28)."""
    from livereact.rekordbox.sections import profile_position, track_profile
    t, w = _table(), _wave()
    before = plan_track(t, track_id=3, bpm=120.0, waveform=w)
    book = section_classes.load()
    after = plan_track(t, track_id=3, bpm=120.0, waveform=w, classes=book)
    assert [replace(s, cls="") for s in after.sections] == list(before.sections)
    assert after.palette and after.palette == book.palette_for(track_profile(w), 3, profile_position(w))
    assert replace(after, sections=before.sections, palette=before.palette) == before
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


def test_profile_position_runs_bassiest_to_brightest_inside_the_profile():
    from livereact.rekordbox.sections import profile_position, track_profile
    assert profile_position(None) is None
    dark, bright = _wave(1, 1, 2), _wave(1, 1, 6)        # both "high", the second much brighter
    assert track_profile(dark) == track_profile(bright) == "high"
    assert 0.0 <= profile_position(dark) < profile_position(bright) <= 1.0


def test_palette_for_picks_dark_to_bright_by_position_and_by_seed_without_one():
    pal = {"approved": True, "high": ["deep_amber", "toxic_green", "acid_yellow", "warm_white"],
           "mid": ["deep_blue"], "low": ["blood_red"]}
    book = _book([], pal)
    assert [book.palette_for("high", 0, p) for p in (0.0, 0.3, 0.6, 1.0)] == pal["high"]
    assert book.palette_for("high", 2) == "acid_yellow"
    assert _book([], dict(pal, approved=False)).palette_for("high", 0, 1.0) == ""


def test_the_planners_roll_lead_in_is_the_shipped_snare_roll_class():
    from livereact.rekordbox import section_classes
    from livereact.rekordbox.planner import plan_track
    from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
    from livereact.rekordbox.sections import section_features
    spec = [(1, 1, "intro"), (17, 2, "up"), (97, 5, "chorus"), (161, 6, "outro")]
    ends = [b for b, _, _ in spec[1:]] + [193]
    table = PhraseTable(mood=MOOD_HIGH, end_beat=193, bank=0, phrases=tuple(
        Phrase(index=i + 1, beat=b, end_beat=e, kind=k, label=lab) for i, ((b, k, lab), e) in enumerate(zip(spec, ends))))
    plan = plan_track(table, track_id=1, bpm=120.0, classes=section_classes.load())
    feats = section_features(plan, table, bpm=120.0)
    assert [f.roll for f in feats] == [0, 0, 1, 0, 0]
    assert [s.cls for s in plan.sections if s.kind == "drop"] == ["drop_snare_roll", "drop_full"]
