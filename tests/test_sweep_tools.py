"""tools/agent/sweep.py pure checks and tools/agent/propose_markers.py on a synthetic export."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

from livereact.rekordbox.look_notes import TEMPLATE, from_dict
from livereact.rekordbox.planner import plan_track
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
from test_usb_export import _export

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "agent" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table(spec, end):
    ph = []
    for i, (b, k, lb) in enumerate(spec):
        ph.append(Phrase(index=i + 1, beat=b, end_beat=(spec[i + 1][0] if i + 1 < len(spec) else end), kind=k, label=lb))
    return PhraseTable(mood=MOOD_HIGH, end_beat=end, bank=0, phrases=tuple(ph))


def test_plan_checks_flag_the_right_things():
    sweep = _load("sweep")
    good = plan_track(_table([(1, 1, "intro"), (33, 2, "up"), (65, 5, "chorus"), (129, 6, "outro")], 161), track_id=1, bpm=120.0)
    assert sweep.plan_checks(good, None, [{"plan_active": True}] * 10) == []
    assert sweep.plan_checks(good, None, [{"plan_active": False}] * 8 + [{"plan_active": True}] * 2) == ["plan_inactive:0.20"]
    no_drop = plan_track(_table([(1, 1, "intro"), (33, 2, "up"), (65, 6, "outro")], 97), track_id=1, bpm=120.0)
    assert "no_drop_section" in sweep.plan_checks(no_drop, None, None)
    tool = from_dict(dict(TEMPLATE, mood=["tool-track"]))
    assert sweep.plan_checks(plan_track(no_drop and _table([(1, 1, "intro"), (33, 2, "up"), (65, 6, "outro")], 97),
                                        track_id=1, bpm=120.0, look=tool), tool, None) == []
    none_bm = plan_track(_table([(1, 1, "intro"), (33, 5, "chorus"), (97, 6, "outro")], 129), track_id=1, bpm=120.0,
                         look=from_dict(dict(TEMPLATE, big_moment="none")))
    assert "no_big_moment" not in sweep.plan_checks(none_bm, from_dict(dict(TEMPLATE, big_moment="none")), None)
    assert sweep.plan_checks(None, None, None) == ["no_plan"]


def test_invariant_checks_catch_forbidden_laser_and_strobe():
    sweep = _load("sweep")

    class FakeRun:
        n = 6
        laser_any = np.array([False, True, True, False, False, False])
        strobe_any = np.array([False, False, False, True, True, False])

    rows = [{"plan_active": True, "laser_allowed": False, "strobe_allowed": True}] * 3 + \
           [{"plan_active": True, "laser_allowed": True, "strobe_allowed": False}] * 2 + [{"plan_active": False}]
    out = sweep.invariant_checks(FakeRun(), rows)
    assert out == ["laser_where_forbidden:2_ticks", "strobe_where_forbidden:2_ticks"]
    ok_rows = [{"plan_active": True, "laser_allowed": True, "strobe_allowed": True}] * 6
    assert sweep.invariant_checks(FakeRun(), ok_rows) == []


def test_propose_markers_build_on_a_synthetic_export(tmp_path):
    pm = _load("propose_markers")
    usb = _export(tmp_path, [(4, "Lift", "Lift.mp3"), (5, "Other", "Other.mp3")])
    tdir = tmp_path / "tracks"
    tdir.mkdir()
    (tdir / "Lift.timeline.json").write_text(json.dumps({"bpm": 125.0, "first_downbeat_s": 0.1, "source": "manual",
                                                         "markers": [{"t": 30.8, "kind": "drop", "note": ""}]}))
    lines, n, scaffolded = pm.build(usb, tdir, scaffold=True)
    text = "\n".join(lines)
    assert n == 1 and scaffolded == 1                          # Lift is ear-verified: skipped; Other gets a scaffold
    assert "## Other.mp3" in text and "**drop**" in text and "big moment" in text and "agent:" in text
    assert "Lift" not in text.split("## Other.mp3")[1]
    sc = json.loads((tdir / "Other.look.json").read_text())
    assert sc["_todo"] is True and sc["track"] == "Other"
    assert not (tdir / "Lift.look.json").exists()
    assert not list(tdir.glob("Other.timeline.json"))         # never writes a timeline


def test_classify_library_counts_classes_and_caches(tmp_path):
    cl = _load("classify_library")
    from livereact.rekordbox import section_classes
    usb = _export(tmp_path, [(4, "Lift", "Lift.mp3"), (5, "Other", "Other.mp3")])
    tdir = tmp_path / "tracks"
    tdir.mkdir()
    book = section_classes.from_dict({"schema": 1, "classes": [
        {"name": "drop_full", "kind": "drop", "rule": {"bars": [7.75, 10000]}},
        {"name": "drop_clap", "kind": "drop", "rule": None}]})
    cache = tmp_path / "cache"
    lines, data = cl.build(usb, book, tdir, cache)
    text = "\n".join(lines)
    assert data["tracks"] == 2 and data["classes"]["drop_full"] >= 2 and data["classes"]["drop_clap"] == 0
    assert "### drop_full" in text and "not defined yet" in text and "## Not matched by any class" in text
    assert len(list(cache.glob("*.json"))) == 2
    _, again = cl.build(usb, book, tdir, cache)                # second run reads the cache
    assert again["classes"] == data["classes"]
