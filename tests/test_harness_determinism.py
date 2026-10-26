"""The replay harness must be bit-for-bit reproducible.

Every before/after comparison the improvement loop makes rests on this: the
same audio + timeline + gestures through the same code must yield the same
512-channel frame log, run after run. Uses a synthetic four-on-the-floor
signal so no audio file or ffmpeg is needed.
"""
from __future__ import annotations

import numpy as np
import pytest

from livereact.sim.harness import SR, run_sim
from livereact.sim.synth import synth_house
from livereact.sim.timeline import Timeline

RIG = "rigs/josh_rig.json"


def test_same_input_same_frames_twice():
    tl = Timeline(bpm=128.0, first_downbeat_s=0.0)
    audio = synth_house()
    a = run_sim("synth", tl, RIG, audio=audio)
    b = run_sim("synth", tl, RIG, audio=audio)
    assert a.frames.shape == b.frames.shape
    assert a.frames_digest() == b.frames_digest()
    assert np.array_equal(a.t, b.t)
    assert a.summary == b.summary


def test_frames_are_not_trivially_empty():
    tl = Timeline(bpm=128.0, first_downbeat_s=0.0)
    r = run_sim("synth", tl, RIG, audio=synth_house())
    assert r.frames.shape[1] == 512
    assert r.frames.max() > 0, "engine produced no DMX output at all"
    assert r.summary["ticks"] == r.frames.shape[0]


def test_gestures_change_output():
    """A scripted mixer move must reach the metronome and show up in the snapshot."""
    tl = Timeline(bpm=128.0, first_downbeat_s=0.0)
    audio = synth_house()
    base = run_sim("synth", tl, RIG, audio=audio)
    moved = run_sim("synth", tl, RIG, audio=audio, gestures="gestures/clean_mix.json")
    assert base.signals[-1]["fader_a"] == 0.0          # ShowSnapshot default
    assert moved.signals[-1]["fader_a"] == 1.0         # clean_mix steps fader_a to 1.0 at t=0
    assert moved.signals[-1]["crossfader"] == 0.0      # clean_mix's first move is at 64 s, past this clip


def test_timeline_grid_phases_downbeat():
    tl = Timeline(bpm=120.0, first_downbeat_s=1.0)   # beat = 0.5 s -> downbeat is beat #2 of the grid
    t0, bib = tl.grid_start()
    assert t0 == pytest.approx(0.0)
    assert bib == 3   # beats at 0.0 (3), 0.5 (4), 1.0 (1) -> downbeat lands on 1.0


def test_drive_mode_actually_engages_in_the_sim():
    """Guards the (slot, track_id) key: the sim's phrase message and status stream must agree."""
    import glob
    from pathlib import Path

    from livereact.rekordbox.planner import plan_track
    from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
    from livereact.sim.harness import run_sim
    from livereact.sim.timeline import Timeline

    tls = sorted(glob.glob("tracks/*.timeline.json"))
    tls = [t for t in tls if Path(t.replace(".timeline.json", ".mp3")).exists()]
    if not tls:
        import pytest
        pytest.skip("no local track audio")
    tl = Timeline.load(tls[0])
    spec = [(1, 1, "intro"), (33, 2, "up"), (65, 5, "chorus"), (129, 6, "outro")]
    phrases = tuple(Phrase(index=i + 1, beat=b, end_beat=(spec[i + 1][0] if i + 1 < len(spec) else 161), kind=k, label=lb)
                    for i, (b, k, lb) in enumerate(spec))
    table = PhraseTable(mood=MOOD_HIGH, end_beat=161, bank=0, phrases=phrases)
    r = run_sim(tls[0].replace(".timeline.json", ".mp3"), tl, "rigs/testing_setup.json", duration=45,
                phrases=table, drive=True, plan=plan_track(table, track_id=1, bpm=tl.bpm))
    assert any(row["plan_active"] for row in r.signals)
    assert any(row["section"] == "drop" and row["bucket"] == "DROP" for row in r.signals)
