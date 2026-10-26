"""gate.compare in drive mode: mode / analysis fingerprint refusal, drop placement tolerance, sweep set."""

from __future__ import annotations

from livereact.loop.evaluate import Evaluation, TrackEval
from livereact.loop.gate import DROP_OFFSET_TOLERANCE_BARS, compare, mode_mismatch


def _te(name, comp, offsets=(), holdout=False, verdicts=None, driven=True):
    return TrackEval(track=name, holdout=holdout, status="OK", compliance=comp,
                     verdicts=verdicts or {"TIME-11": "PASS", "STRUCT-03": "PASS"},
                     quality={"distinct_looks": 3, "brightness_dynamic_range": 0.5, "fixture_utilization": 0.9, "drop_response_mean": 0.8},
                     frames_digest="x", driven=driven, track_id=1,
                     drop_offsets=[{"t": 10.0, "commit_t": 10.0 + o * 2, "offset_bars": abs(o), "signed_bars": o, "missed": False} for o in offsets])


def _ev(comp, offsets, mode="drive", fp="abc", sweep=None, sweep_set=None, rules="r"):
    ev = Evaluation(params={}, rules=rules, seed_range=[0.1, 0.9], mode=mode, analysis_fingerprint=fp)
    ev.tracks = [_te("a.mp3", comp, offsets), _te("b.mp3", comp, offsets, holdout=True)]
    ev.sweep = sweep or []
    ev.sweep_set = sweep_set
    return ev


def test_mode_and_fingerprint_mismatch_refuse():
    base = _ev(0.5, [1.0], mode="classifier", fp=None)
    cand = _ev(0.6, [1.0], mode="drive", fp="abc")
    assert "mode differs" in mode_mismatch(base, cand)
    assert not compare(base, cand)["accept"]
    base2 = _ev(0.5, [1.0], fp="abc")
    cand2 = _ev(0.6, [1.0], fp="def")
    assert "analysis of the scored tracks changed" in mode_mismatch(base2, cand2)
    assert mode_mismatch(_ev(0.5, [1.0]), _ev(0.6, [1.0])) is None


def test_drop_placement_tolerance():
    base = _ev(0.5, [1.0, 1.0])
    better = _ev(0.6, [0.5, 1.0])
    assert compare(base, better)["accept"], compare(base, better)["reasons"]
    slightly_worse = _ev(0.6, [1.2, 1.0])         # +0.1 mean: inside tolerance
    assert compare(base, slightly_worse)["accept"]
    worse = _ev(0.6, [1.0 + DROP_OFFSET_TOLERANCE_BARS * 2 + 0.1, 1.0])
    r = compare(base, worse)
    assert not r["accept"] and any("drop placement worse" in x for x in r["reasons"])


def test_sweep_set_regression_and_notes():
    sw_base = [_te("u1.mp3", 0.8, verdicts={"TIME-11": "PASS", "TIME-02": "PASS"}), _te("u2.mp3", 0.9, verdicts={"TIME-11": "PASS"})]
    base = _ev(0.5, [1.0], sweep=sw_base, sweep_set="S1")
    sw_bad = [_te("u1.mp3", 0.6, verdicts={"TIME-11": "FAIL", "TIME-02": "PASS"}), _te("u2.mp3", 0.9, verdicts={"TIME-11": "PASS"})]
    r = compare(base, _ev(0.6, [1.0], sweep=sw_bad, sweep_set="S1"))
    assert not r["accept"] and any("sweep" in x for x in r["reasons"])
    sw_ok = [_te("u1.mp3", 0.85, verdicts={"TIME-11": "PASS", "TIME-02": "PASS"}), _te("u2.mp3", 0.9, verdicts={"TIME-11": "PASS"})]
    r2 = compare(base, _ev(0.6, [1.0], sweep=sw_ok, sweep_set="S1"))
    assert r2["accept"] and not r2["notes"]
    r3 = compare(base, _ev(0.6, [1.0], sweep=sw_ok, sweep_set="S2"))
    assert r3["accept"] and any("sweep set changed" in n for n in r3["notes"])
    r4 = compare(_ev(0.5, [1.0]), _ev(0.6, [1.0], sweep=sw_ok, sweep_set="S2"))
    assert r4["accept"] and any("no sweep set" in n for n in r4["notes"])


def test_json_round_trip_keeps_the_new_fields():
    ev = _ev(0.5, [1.0, -0.5], sweep=[_te("u1.mp3", 0.8)], sweep_set="S1")
    d = ev.to_json()
    back = Evaluation.from_json(d)
    assert back.mode == "drive" and back.analysis_fingerprint == "abc" and back.sweep_set == "S1"
    assert back.summary() == ev.summary()
    assert back.summary()["tuning"]["drop_offset_bars_mean"] == 0.75 and back.summary()["sweep"]["n"] == 1
    # an old baseline without the new keys still loads
    old = {k: v for k, v in d.items() if k in ("params", "rules", "seed_range", "tracks")}
    for t in old["tracks"]:
        for k in ("driven", "track_id", "drop_offsets"):
            t.pop(k, None)
    legacy = Evaluation.from_json(old)
    assert legacy.mode == "classifier" and legacy.sweep == [] and legacy.tracks[0].drop_offsets == []
