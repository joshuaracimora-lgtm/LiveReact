"""PhraseEngine follows the track plan's look (TIME-20) and holds it for the section."""

from __future__ import annotations

from livereact.core.show_state import ShowSnapshot
from livereact.engines.phrase import ROTATION_BARS, PhraseEngine
from livereact.fixtures.rig import load_rig
from livereact.scenes.catalog import SceneCatalog


def _snap(bar, bucket="break", planned="", active=True, **kw):
    return ShowSnapshot(bar=bar, bucket=bucket, classifier_bucket=bucket, pdl_fresh=True,
                        plan_active=active, planned_scene=planned, beat_in_bar=1, **kw)


def _engine(full=False):
    return PhraseEngine(SceneCatalog(full=full)), load_rig(None)


def test_planned_scene_is_picked_on_the_bucket_change():
    eng, rig = _engine(full=True)
    eng.tick(_snap(0, "break", "Deep Wash Breathe"), rig)
    for bar in range(1, 6):
        eng.tick(_snap(bar, "break", "Deep Wash Breathe"), rig)
    eng.tick(_snap(6, "drop", "Drop Wash Slam Clear"), rig)   # hard cut on the drop-family entry
    assert eng._current.name == "Drop Wash Slam Clear" or (eng._next and eng._next.name == "Drop Wash Slam Clear")


def test_planned_look_holds_past_the_scheduled_rotation():
    eng, rig = _engine(full=True)
    for bar in range(0, 3 * ROTATION_BARS):
        eng.tick(_snap(bar, "drop", "Drop Strobe Stab"), rig)
    assert eng._current.name == "Drop Strobe Stab"
    assert eng.rotations <= 1          # at most the initial rotation into the planned look


def test_unplanned_scheduled_rotation_still_happens_without_a_plan():
    eng, rig = _engine(full=True)
    for bar in range(0, 3 * ROTATION_BARS):
        eng.tick(_snap(bar, "drop", "", active=False), rig)
    assert eng.rotations >= 2


def test_plan_scene_outside_the_bucket_family_is_ignored():
    eng, rig = _engine(full=True)
    for bar in range(0, 8):
        eng.tick(_snap(bar, "break", "Drop Strobe Stab"), rig)   # a drop look while BREAK shows: not used
    assert eng._current.bucket.value in ("break", "outro")


def test_unknown_planned_name_falls_back_to_the_catalog():
    eng, rig = _engine()
    for bar in range(0, ROTATION_BARS + 4):           # the scheduler still runs when the plan names nothing usable
        eng.tick(_snap(bar, "drop", "No Such Look"), rig)
    assert eng._current.name == "Drop Strobe Stab"
