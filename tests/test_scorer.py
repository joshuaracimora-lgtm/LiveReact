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


def _fx(type_name):
    return next(fx for fx in RIG.fixtures if fx.type_name == type_name)


PAR, BAR, MH, LASER = _fx("rgb_par"), _fx("ok103_wash_bar"), _fx("shehds_180w_mh"), _fx("shehds_3w_laser")
PAR_DIM, PAR_RGB = PAR.offset, [PAR.offset + 1, PAR.offset + 2, PAR.offset + 3]
BAR_DIM, BAR_STROBE, BAR_RGB, BAR_W = BAR.offset, BAR.offset + 1, [BAR.offset + 2, BAR.offset + 3, BAR.offset + 4], BAR.offset + 5
MH_PAN, MH_TILT, MH_DIM = MH.offset, MH.offset + 2, MH.offset + 5     # pan/tilt are 16-bit pairs
LASER_MODE, LASER_COLOR = LASER.offset, LASER.offset + 11
CLAMP_PAN = MH.PAN_MAX_DEG_PER_S * DT / MH.PAN_RANGE_DEG                # profiles.py slew limit per tick, 0..1
CLAMP_TILT = MH.TILT_MAX_DEG_PER_S * DT / MH.TILT_RANGE_DEG
DJ_POSE = (0.3, 0.7)


def _rig_with_dj_pose():
    rig = load_rig("rigs/josh_rig.json")
    rig.fixture("mh").dj_pan_tilt = DJ_POSE
    return rig


RIG_DJ = _rig_with_dj_pose()


def make_run(seconds=80.0, bucket="BREAK", scene="Break Low Wash", markers=(), lit=(DIMMERS[0],), level=128):
    n = int(seconds / DT)
    t = np.arange(1, n + 1) * DT
    frames = np.zeros((n, 512), np.uint8)
    for c in lit:
        frames[:, c] = level
    signals = [{"bucket": bucket, "scene": scene, "band_low": 0.0, "band_mid": 0.0, "band_high": 0.0, "onset_rate": 0.0}
               for _ in range(n)]
    tl = Timeline(bpm=BPM, first_downbeat_s=0.0, markers=list(markers))
    return frames, t, signals, tl


def score(frames, t, signals, tl, rig=RIG):
    return score_run(Run(frames, t, signals, tl, rig))


def write16(frames, i, chan, v01):
    v = int(round(max(0.0, min(1.0, v01)) * 65535))
    frames[i, chan], frames[i, chan + 1] = v >> 8, v & 0xFF


def write16_all(frames, idx, chan, v01):
    for i in idx:
        write16(frames, i, chan, v01)


def sweep_heads(frames, idx):
    """Pan 0.2..0.8 and tilt 0.3..0.7 as triangle waves at exactly the slew clamp."""
    pan, tilt, dp, dtl = 0.2, 0.3, CLAMP_PAN, CLAMP_TILT
    for i in idx:
        write16(frames, i, MH_PAN, pan)
        write16(frames, i, MH_TILT, tilt)
        pan += dp
        tilt += dtl
        if not 0.2 <= pan <= 0.8:
            dp, pan = -dp, pan + 2 * (-dp)
        if not 0.3 <= tilt <= 0.7:
            dtl, tilt = -dtl, tilt + 2 * (-dtl)


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


# ----------------------------------------------------------------- scorer v2: Josh's roll / cut / groove vocabulary
#
# Each scenario has a clean variant that fails NOTHING (so the new rule is
# exercised and PASSes, not NOT_SCORED) and planted variants that fail only
# the rule under test.

def test_clean_run_leaves_v2_rules_not_scored():
    sc = score(*make_run(markers=[Marker(20.0, "fill", "")]))
    for rid in ("STRUCT-04", "STRUCT-05", "STRUCT-06", "STRUCT-07", "STRUCT-08", "STRUCT-09"):
        assert verdict(sc, rid)["status"] == "NOT_SCORED", rid


# --- STRUCT-04 whiteout

def _whiteout_run(variant="ok"):
    """A whiteout at 40 s (no next marker -> 2-bar window). Washes white and lit,
    no strobe, the head glides from (0.1, 0.1) to the DJ pose at its slew clamp."""
    frames, t, sig, tl = make_run(markers=[Marker(40.0, "whiteout", "drums cut, stabs carry it")])
    w = (t >= 40.0) & (t < 44.0)
    for c in DIMMERS:
        frames[w, c] = 220
    if variant == "stays_red":
        frames[w, PAR_RGB[0]] = 255
        frames[w, BAR_RGB[0]] = 255
    else:
        for c in PAR_RGB:
            frames[w, c] = 255
        frames[w, BAR_W] = 255
    if variant == "strobe":
        frames[(t >= 41.0) & (t < 42.0), BAR_STROBE] = 60        # 1 s: fine for SAFE-04, wrong for a whiteout
    idx = np.where(w)[0]
    write16_all(frames, range(len(t)), MH_PAN, 0.1)
    write16_all(frames, range(len(t)), MH_TILT, 0.1)
    if variant != "heads_static":
        pan, tilt = 0.1, 0.1
        for i in idx:
            pan, tilt = min(DJ_POSE[0], pan + CLAMP_PAN), min(DJ_POSE[1], tilt + CLAMP_TILT)
            write16(frames, i, MH_PAN, pan)
            write16(frames, i, MH_TILT, tilt)
        write16_all(frames, range(idx[-1] + 1, len(t)), MH_PAN, DJ_POSE[0])
        write16_all(frames, range(idx[-1] + 1, len(t)), MH_TILT, DJ_POSE[1])
    return frames, t, sig, tl


def test_whiteout_clean_passes_struct_04_with_dj_pose():
    sc = score(*_whiteout_run(), rig=RIG_DJ)
    assert fails(sc) == []
    v = verdict(sc, "STRUCT-04")
    assert v["status"] == "PASS"
    assert v["metric"]["dj_pose"] == "configured"
    assert "heads to DJ" in v["evidence"][0][1]


def test_whiteout_without_dj_pose_skips_the_head_subcheck():
    sc = score(*_whiteout_run("heads_static"))              # default rig: no dj_pan_tilt, no venue
    assert fails(sc) == []
    v = verdict(sc, "STRUCT-04")
    assert v["status"] == "PASS"
    assert v["metric"]["dj_pose"].startswith("not configured")
    assert "not scored" in v["note"]


def test_whiteout_heads_static_fails_only_struct_04():
    sc = score(*_whiteout_run("heads_static"), rig=RIG_DJ)
    assert fails(sc) == ["STRUCT-04"]
    assert "from the DJ pose" in verdict(sc, "STRUCT-04")["evidence"][0][1]


def test_whiteout_staying_red_fails_only_struct_04():
    sc = score(*_whiteout_run("stays_red"), rig=RIG_DJ)
    assert fails(sc) == ["STRUCT-04"]
    assert "white+lit only" in verdict(sc, "STRUCT-04")["evidence"][0][1]


def test_whiteout_with_strobe_fails_only_struct_04():
    sc = score(*_whiteout_run("strobe"), rig=RIG_DJ)
    assert fails(sc) == ["STRUCT-04"]
    assert "strobe" in verdict(sc, "STRUCT-04")["evidence"][0][1]


# --- STRUCT-07 groove

def _groove_run(variant="ok"):
    """A 20-bar groove (20-60 s). band_low pulses on every beat, band_mid is a
    free-running synth LFO. Wash bar follows the synth, head sweeps at the slew
    clamp, laser fires 3 beats every other bar."""
    frames, t, sig, tl = make_run(markers=[Marker(20.0, "groove", "main groove", 60.0)])
    kick = np.where((t % 0.5) / 0.5 < 0.15, 1.0, 0.1)
    synth = 0.5 + 0.45 * np.sin(2 * np.pi * t / 0.37)
    for i, r in enumerate(sig):
        r["band_low"], r["band_mid"], r["band_high"] = round(float(kick[i]), 4), round(float(synth[i]), 4), 0.2
    w = (t >= 20.0) & (t < 60.0)
    idx = np.where(w)[0]
    src = kick if variant == "bar_follows_kick" else synth
    frames[w, BAR_DIM] = (40 + 200 * src[w]).astype(np.uint8)
    frames[w, MH_DIM] = 200
    write16_all(frames, range(len(t)), MH_PAN, 0.5)
    write16_all(frames, range(len(t)), MH_TILT, 0.5)
    if variant != "heads_static":
        sweep_heads(frames, idx)
    if variant == "laser_steady":
        frames[w, LASER_MODE], frames[w, LASER_COLOR] = 60, 100
    elif variant != "no_laser":
        for start in np.arange(20.0, 60.0, 4.0):                 # every other bar, 3 beats long
            b = (t >= start) & (t < start + 1.5)
            frames[b, LASER_MODE], frames[b, LASER_COLOR] = 60, 100
    return frames, t, sig, tl


def test_groove_clean_passes_struct_07():
    sc = score(*_groove_run())
    assert fails(sc) == []
    v = verdict(sc, "STRUCT-07")
    assert v["status"] == "PASS"
    sub = v["metric"]["sub"][0]
    assert sub["bar_corr_synth"] > 0.9 and abs(sub["bar_corr_kick"]) < 0.2
    assert sub["head_frames_at_speed"] > 0.9
    assert sub["laser_bursts"] == 10
    assert sub["laser_burst_beats_median"] == pytest.approx(3.0, abs=0.15)
    assert sub["laser_period_beats_median"] == pytest.approx(8.0, abs=0.15)


@pytest.mark.parametrize("variant, words", [
    ("bar_follows_kick", "not synth-synced"),
    ("heads_static", "heads at speed"),
    ("laser_steady", "burst(s)"),
    ("no_laser", "0 burst(s)"),
])
def test_groove_violations_fail_only_struct_07(variant, words):
    sc = score(*_groove_run(variant))
    assert fails(sc) == ["STRUCT-07"], variant
    assert words in verdict(sc, "STRUCT-07")["evidence"][0][1]


# --- drops with rolls: STRUCT-08 weak roll, STRUCT-09 clap-driven drop

def _drop_skeleton(seconds, drops):
    """Drops that satisfy every existing drop rule: 16-bar dimmer ramp into each
    (TIME-05), dark beat + snap (TIME-04), DROP bucket from t0 (STRUCT-03), looks
    held long enough (TIME-02/11), nothing strobing before the drop (LOOK-05)."""
    frames, t, sig, tl = make_run(seconds=seconds, markers=drops, lit=())
    n = len(t)
    par = np.full(n, 60.0)
    for m in drops:
        b = (t >= m.t - 32.0) & (t < m.t)
        par[b] = 60 + 100 * (t[b] - (m.t - 32.0)) / 32.0
    frames[:, PAR_DIM] = par.astype(np.uint8)
    for m in drops:
        w = (t >= m.t) & (t < m.end)
        for c in DIMMERS:
            frames[w, c] = 220
        pre = (t >= m.t - 0.5) & (t < m.t)
        for c in DIMMERS:
            frames[pre, c] = 0
        for i in np.where(w)[0]:
            sig[i]["bucket"], sig[i]["scene"] = "DROP", "Drop Beam Fan"
    return frames, t, sig, tl


def _roll_run(weak_peak=100, strong_peak=200, weak_note="drop 2, less of a drop: weaker snare roll"):
    drops = [Marker(40.0, "drop", "in-the-roll kick", 60.0), Marker(100.0, "drop", weak_note, 120.0)]
    frames, t, sig, tl = _drop_skeleton(160.0, drops)
    frames[(t >= 45.0) & (t < 46.0), BAR_STROBE] = strong_peak
    frames[(t >= 105.0) & (t < 106.0), BAR_STROBE] = weak_peak
    for i, r in enumerate(sig):
        r["band_high"] = 0.8 if 40.0 <= t[i] < 60.0 else 0.3 if 100.0 <= t[i] < 120.0 else 0.1
    return frames, t, sig, tl


def test_drop_skeleton_is_clean():
    sc = score(*_roll_run())
    assert fails(sc) == [], fails(sc)
    assert verdict(sc, "TIME-04")["status"] == "PASS"
    assert verdict(sc, "STRUCT-03")["status"] == "PASS"


def test_weak_roll_lower_ceiling_passes_struct_08():
    v = verdict(score(*_roll_run()), "STRUCT-08")
    assert v["status"] == "PASS"
    assert v["metric"]["ordered_by"] == "note"


def test_weak_roll_same_ceiling_fails_only_struct_08():
    sc = score(*_roll_run(weak_peak=200))
    assert fails(sc) == ["STRUCT-08"]
    assert "weaker roll 100.0s (peak 200)" in verdict(sc, "STRUCT-08")["evidence"][0][1]


def test_weak_roll_ordered_by_high_band_when_the_note_is_silent():
    ok = verdict(score(*_roll_run(weak_note="drop 2")), "STRUCT-08")
    assert ok["status"] == "PASS" and ok["metric"]["ordered_by"] == "high_band"
    sc = score(*_roll_run(weak_peak=200, weak_note="drop 2"))
    assert fails(sc) == ["STRUCT-08"]


def test_weak_roll_needs_two_rolls():
    frames, t, sig, tl = _drop_skeleton(100.0, [Marker(40.0, "drop", "", 60.0)])
    assert verdict(score(frames, t, sig, tl), "STRUCT-08")["status"] == "NOT_SCORED"


def _clap_run(variant="ok"):
    """One clap-driven drop (40-60 s): claps on beats 2 and 4, dimmer hits on
    them, no strobe."""
    drops = [Marker(40.0, "drop", "no snare roll (synths + claps): react to the claps", 60.0)]
    frames, t, sig, tl = _drop_skeleton(100.0, drops)
    w = (t >= 40.0) & (t < 60.0)
    for c in DIMMERS:
        frames[w, c] = 170
    for i, r in enumerate(sig):
        r["band_high"] = 0.2 if w[i] else 0.1
    for clap in np.arange(40.5, 60.0, 1.0):
        hit = np.where((t >= clap) & (t < clap + 3 * DT))[0]
        for i in hit:
            sig[i]["band_high"] = 0.9
            if variant != "no_hits":
                for c in DIMMERS:
                    frames[i, c] = 250
    if variant == "roll_strobe":
        frames[(t >= 45.0) & (t < 46.0), BAR_STROBE] = 200
    elif variant == "escalating_strobe":
        idx = np.where((t >= 50.0) & (t < 51.4))[0]            # one 1.4 s burst, ramping like a roll
        frames[idx, BAR_STROBE] = np.linspace(10, 90, len(idx)).astype(np.uint8)
        idx = np.where((t >= 58.0) & (t < 59.4))[0]
        frames[idx, BAR_STROBE] = np.linspace(10, 90, len(idx)).astype(np.uint8)
    return frames, t, sig, tl


def test_clap_driven_drop_clean_passes_struct_09():
    sc = score(*_clap_run())
    assert fails(sc) == [], fails(sc)
    v = verdict(sc, "STRUCT-09")
    assert v["status"] == "PASS"
    assert v["metric"]["sub"][0]["clap_hit_corr"] > 0.5
    assert verdict(sc, "STRUCT-08")["status"] == "NOT_SCORED"      # a clap drop is not a roll


@pytest.mark.parametrize("variant, words", [
    ("roll_strobe", "soft ceiling"),
    ("no_hits", "don't follow the claps"),
])
def test_clap_driven_drop_violations_fail_only_struct_09(variant, words):
    sc = score(*_clap_run(variant))
    assert fails(sc) == ["STRUCT-09"], variant
    assert words in verdict(sc, "STRUCT-09")["evidence"][0][1]


def test_clap_driven_drop_escalating_strobe_reads_as_a_roll():
    sc = score(*_clap_run("escalating_strobe"))
    assert fails(sc) == ["STRUCT-09"]
    assert "like a roll" in verdict(sc, "STRUCT-09")["evidence"][0][1]


# --- STRUCT-06 fading build + STRUCT-05 power-off (Beautiful Now 0:30 -> 1:04)

def _fading_build_run(variant="ok"):
    """Build 20-52 s on the par: red -> white, 200 -> 80 slow and smooth, heads
    still, no strobe. At the breakdown (52 s) a 4-step shutter fade to dark."""
    frames, t, sig, tl = make_run(seconds=100.0, lit=(),
                                  markers=[Marker(20.0, "build", "extended build"),
                                           Marker(52.0, "breakdown", "extended build fades out here: white, slow dim")])
    pre = t < 20.0
    frames[pre, PAR_DIM], frames[pre, PAR_RGB[0]] = 200, 255
    b = (t >= 20.0) & (t < 52.0)
    frac = (t[b] - 20.0) / 32.0
    end_level = 200 if variant == "no_dim" else 80
    frames[b, PAR_DIM] = np.round(200 + (end_level - 200) * frac).astype(np.uint8)
    frames[b, PAR_RGB[0]] = 255
    if variant != "stays_red":
        frames[b, PAR_RGB[1]] = frames[b, PAR_RGB[2]] = np.round(255 * frac).astype(np.uint8)
    write16_all(frames, range(len(t)), MH_PAN, 0.5)
    write16_all(frames, range(len(t)), MH_TILT, 0.5)
    for c in PAR_RGB:
        frames[t >= 52.0, c] = 255
    if variant == "smooth_off":
        off = np.where((t >= 52.0) & (t < 55.0))[0]
        frames[off, PAR_DIM] = np.round(end_level * (1 - np.arange(len(off)) / len(off))).astype(np.uint8)
    elif variant == "hard_cut":
        pass                                                      # 52 s onward is already 0
    else:
        for k in range(4):                                        # 4 steps, 0.75 s holds -> dark in 1.5 bars
            hold = (t >= 52.0 + 0.75 * k) & (t < 52.0 + 0.75 * (k + 1))
            frames[hold, PAR_DIM] = int(round(end_level * (3 - k) / 4))
    return frames, t, sig, tl


def test_fading_build_and_power_off_clean_pass():
    sc = score(*_fading_build_run())
    assert fails(sc) == [], fails(sc)
    assert verdict(sc, "STRUCT-06")["status"] == "PASS"
    v = verdict(sc, "STRUCT-05")
    assert v["status"] == "PASS"
    assert "4 steps" in v["evidence"][0][1]
    assert verdict(sc, "TIME-06")["status"] == "PASS"


@pytest.mark.parametrize("variant, words", [
    ("no_dim", "no slow dim"),
    ("stays_red", "hue not drifting to white"),
])
def test_fading_build_violations_fail_only_struct_06(variant, words):
    sc = score(*_fading_build_run(variant))
    assert fails(sc) == ["STRUCT-06"], variant
    assert words in verdict(sc, "STRUCT-06")["evidence"][0][1]


@pytest.mark.parametrize("variant, words", [
    ("smooth_off", "faded smoothly"),
    ("hard_cut", "hard cut"),
])
def test_power_off_violations_fail_only_struct_05(variant, words):
    sc = score(*_fading_build_run(variant))
    assert fails(sc) == ["STRUCT-05"], variant
    assert words in verdict(sc, "STRUCT-05")["evidence"][0][1]


def test_power_off_skips_a_drops_cut_and_an_already_dark_rig():
    frames, t, sig, tl = _drop_skeleton(100.0, [Marker(40.0, "drop", "", 60.0)])
    tl.markers.append(Marker(60.0, "breakdown", ""))               # the drop's own cut
    tl.markers.append(Marker(80.0, "breakdown", ""))               # par at 60/255 = 0.24 >= 0.15: lit, so a candidate
    v = verdict(score(frames, t, sig, tl), "STRUCT-05")
    assert any("drop's cut" in s for _, s in v["metric"]["skipped"])
    assert v["status"] == "FAIL"                                   # the 80 s one stays lit: no power-off
    frames[t >= 70.0, PAR_DIM] = 0
    v = verdict(score(frames, t, sig, tl), "STRUCT-05")
    assert v["status"] == "NOT_SCORED" and any("already dim" in s for _, s in v["metric"]["skipped"])
