"""The scorer must catch planted violations -- and ONLY the planted one.

Frame logs are built by hand (no engine) so each test controls exactly what
the DMX port 'said'. A judge that rubber-stamps, or that fails neighbouring
rules on a clean run, is worse than no judge: the improvement loop obeys it.
"""
from __future__ import annotations

import numpy as np
import pytest

from livereact.fixtures.profiles import ChannelKind as K
from livereact.fixtures.rig import load_rig
from livereact.sim.score import RULES, Run, score_run
from livereact.sim.timeline import Marker, Timeline

RIG = load_rig("rigs/josh_rig.json")
BPM = 120.0                      # beat 0.5 s, bar 2.0 s -- easy arithmetic
DT = 1024 / 44100


def _chan(kind, which=0):
    """(fixture index, absolute channel) for the which-th channel of `kind` across the rig."""
    out = []
    for j, fx in enumerate(RIG.fixtures):
        for i, k in enumerate(fx.channel_kinds()):
            if k == kind:
                out.append((j, fx.offset + i))
    return out


DIMMERS = [c for _, c in _chan(K.DIMMER)]
STROBES = [c for _, c in _chan(K.STROBE)]


def make_run(seconds=80.0, bucket="BREAK", scene="Break Low Wash", markers=(), lit=(DIMMERS[0],), level=128):
    n = int(seconds / DT)
    t = np.arange(1, n + 1) * DT
    frames = np.zeros((n, 512), np.uint8)
    for c in lit:
        frames[:, c] = level
    signals = [{"bucket": bucket, "scene": scene} for _ in range(n)]
    tl = Timeline(bpm=BPM, first_downbeat_s=0.0, markers=list(markers))
    return frames, t, signals, tl


def score(frames, t, signals, tl):
    return score_run(Run(frames, t, signals, tl, RIG))


def verdict(sc, rid):
    return next(v for v in sc["verdicts"] if v["rule"] == rid)


def fails(sc):
    return sorted(v["rule"] for v in sc["verdicts"] if v["status"] == "FAIL")


def set_window(arr, t, t0, t1, fn):
    w = (t >= t0) & (t < t1)
    fn(arr, w)


# ----------------------------------------------------------------- clean run

def test_clean_run_fails_nothing():
    sc = score(*make_run(markers=[Marker(20.0, "fill", "")]))
    assert fails(sc) == []
    assert sc["status"] == "OK"
    assert verdict(sc, "STRUCT-01")["status"] == "PASS"
    assert verdict(sc, "SAFE-04")["status"] == "PASS"
    assert verdict(sc, "SAFE-01")["status"] == "UNVERIFIABLE_IN_SIM"


def test_unverified_markers_are_not_scored():
    sc = score(*make_run(markers=[Marker(20.0, "fill", "est: guess")]))
    assert verdict(sc, "STRUCT-01")["status"] == "NOT_SCORED"


# ----------------------------------------------------------------- planted violations

def test_long_strobe_burst_fails_only_safe_04():
    frames, t, sig, tl = make_run()
    set_window(frames, t, 30.0, 33.0, lambda a, w: a.__setitem__((w, STROBES[0]), 60))   # 3 s burst
    sc = score(frames, t, sig, tl)
    assert fails(sc) == ["SAFE-04"]
    assert verdict(sc, "SAFE-04")["metric"]["longest_s"] == pytest.approx(3.0, abs=0.1)
    assert sc["status"] == "BLOCKED"


def test_two_bursts_too_close_fail_safe_04():
    frames, t, sig, tl = make_run()
    set_window(frames, t, 30.0, 31.0, lambda a, w: a.__setitem__((w, STROBES[0]), 60))
    set_window(frames, t, 33.0, 34.0, lambda a, w: a.__setitem__((w, STROBES[0]), 60))   # 2 s gap
    sc = score(frames, t, sig, tl)
    assert fails(sc) == ["SAFE-04"]
    assert "gap" in verdict(sc, "SAFE-04")["evidence"][0][1]


def test_strobe_on_a_fill_fails_struct_01():
    frames, t, sig, tl = make_run(markers=[Marker(20.0, "fill", "")])
    set_window(frames, t, 21.0, 22.0, lambda a, w: a.__setitem__((w, STROBES[0]), 60))
    sc = score(frames, t, sig, tl)
    assert fails(sc) == ["STRUCT-01"]


def test_drop_look_on_a_fill_fails_struct_01():
    frames, t, sig, tl = make_run(markers=[Marker(40.0, "fill", "")])
    for i, r in enumerate(sig):
        if 40.0 <= t[i] < 56.0:
            r["bucket"], r["scene"] = "DROP", "Drop Strobe Stab"
    # keep TIME-11/TIME-02 quiet: BREAK look held 20 bars (needs 16), DROP held 8 bars (needs 8)
    sc = score(frames, t, sig, tl)
    assert fails(sc) == ["STRUCT-01"]


def test_illegal_transition_fails_time_13():
    frames, t, sig, tl = make_run()
    for i, r in enumerate(sig):            # BREAK 0-40 s (20 bars), BUILD 40-60 (10 bars), PEAK 60-80
        if 40.0 <= t[i] < 60.0:
            r["bucket"], r["scene"] = "BUILD", "Rising Tide"
        elif t[i] >= 60.0:
            r["bucket"], r["scene"] = "PEAK", "Peak Full Rig"
    sc = score(frames, t, sig, tl)
    assert fails(sc) == ["TIME-13"]
    assert "BUILD -> PEAK" in verdict(sc, "TIME-13")["evidence"][0][1]


def test_short_dwell_fails_time_11_and_look_change_fails_time_02():
    frames, t, sig, tl = make_run()
    for i, r in enumerate(sig):            # BUILD for 2 bars only (needs 4), then back to BREAK
        if 40.0 <= t[i] < 44.0:
            r["bucket"], r["scene"] = "BUILD", "Rising Tide"
    sc = score(frames, t, sig, tl)
    assert fails(sc) == ["TIME-02", "TIME-11"]
    assert any("BUILD held 2.0 bars" in e[1] for e in verdict(sc, "TIME-11")["evidence"])


def _drop_run(snap: bool):
    """A labeled drop at 40 s. Dark for the beat before, lit after -- as a snap or a 1-beat fade."""
    frames, t, sig, tl = make_run(markers=[Marker(40.0, "drop", "", 60.0)], level=200)
    for c in DIMMERS:
        frames[:, c] = 200
    w_dark = (t >= 39.5) & (t < 40.0)
    for c in DIMMERS:
        frames[w_dark, c] = 0
    if not snap:
        w = np.where((t >= 40.0) & (t < 40.5))[0]          # fade back up over one beat
        for k, i in enumerate(w):
            for c in DIMMERS:
                frames[i, c] = int(200 * k / len(w))
    for i, r in enumerate(sig):
        if t[i] >= 40.0:
            r["bucket"], r["scene"] = "DROP", "Drop Beam Fan"
    return frames, t, sig, tl


def test_predrop_snap_passes_and_fade_fails_time_04():
    ok = score(*_drop_run(snap=True))
    bad = score(*_drop_run(snap=False))
    assert verdict(ok, "TIME-04")["status"] == "PASS"
    assert verdict(bad, "TIME-04")["status"] == "FAIL"
    assert "snap=False" in verdict(bad, "TIME-04")["evidence"][0][1]
    # the only difference between the two runs is the snap, so nothing else may move
    assert set(fails(bad)) - set(fails(ok)) == {"TIME-04"}


def test_drop_latency_measured_in_beats():
    frames, t, sig, tl = _drop_run(snap=True)
    for i, r in enumerate(sig):            # engine wakes up 3 beats (1.5 s) late
        if 40.0 <= t[i] < 41.5:
            r["bucket"], r["scene"] = "BUILD", "Rising Tide"
    sc = score(frames, t, sig, tl)
    v = verdict(sc, "STRUCT-03")
    assert v["status"] == "FAIL"
    assert v["metric"]["latency_beats"][0] == pytest.approx(3.0, abs=0.1)


def test_every_rule_returns_a_verdict():
    sc = score(*make_run())
    assert len(sc["verdicts"]) == len(RULES)
    assert all(v["status"] in ("PASS", "FAIL", "UNVERIFIABLE_IN_SIM", "NOT_SCORED") for v in sc["verdicts"])
