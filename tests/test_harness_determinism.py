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
