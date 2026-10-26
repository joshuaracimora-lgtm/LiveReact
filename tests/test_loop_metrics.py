"""loop/metrics.py: drop placement error and the marker-free rule set."""

from __future__ import annotations

from livereact.loop.metrics import MARKER_FREE_RULES, drop_entries, drop_offsets, marker_free_rules, offsets_summary
from livereact.sim.timeline import Marker, Timeline


def _rows(seq, dt=0.5):
    return [{"t": i * dt, "bucket": b} for i, b in enumerate(seq)]


def test_drop_entries_and_offsets_early_late_missed():
    # 120 bpm -> bar = 2 s. BREAK until t=10, DROP 10..20, BREAK, PEAK at 40
    seq = ["BREAK"] * 20 + ["DROP"] * 20 + ["BREAK"] * 40 + ["PEAK"] * 10
    rows = _rows(seq)
    assert drop_entries(rows) == [10.0, 40.0]
    tl = Timeline(bpm=120.0, markers=[Marker(12.0, "drop"), Marker(38.0, "drop"), Marker(60.0, "drop"), Marker(5.0, "breakdown")])
    offs = drop_offsets(rows, tl, window_bars=8.0)
    assert [o["signed_bars"] for o in offs] == [-1.0, 1.0, None]       # early by a bar, late by a bar, missed
    assert [o["missed"] for o in offs] == [False, False, True]
    assert offs[2]["offset_bars"] == 8.0
    s = offsets_summary([offs])
    assert s == {"drop_offset_bars_mean": round((1 + 1 + 8) / 3, 3), "drops_missed": 1, "drops": 3}
    assert offsets_summary([]) == {"drop_offset_bars_mean": None, "drops_missed": 0, "drops": 0}


def test_marker_free_rules_score_a_marker_less_run():
    from livereact.fixtures.rig import load_rig
    from livereact.sim.harness import run_sim
    from livereact.sim.score import RULES, Run, score_run
    from livereact.sim.synth import synth_house

    audio = synth_house(seconds=30)
    tl = Timeline(bpm=128.0, markers=[], source="rekordbox")
    r = run_sim("synth", tl, "rigs/josh_rig.json", audio=audio)
    sc = score_run(Run(r.frames, r.t, r.signals, tl, load_rig("rigs/josh_rig.json")))
    by = {v["rule"]: v["status"] for v in sc["verdicts"]}
    ids = {rid for rid, _, _ in RULES}
    assert set(marker_free_rules()) <= ids
    for rule in MARKER_FREE_RULES:
        # LOOK-03 / TIME-21 are NOT_SCORED when the run never used the looks they judge; the others always score
        allowed = ("PASS", "FAIL", "UNVERIFIABLE_IN_SIM") + (("NOT_SCORED",) if rule in ("LOOK-03", "TIME-21") else ())
        assert by[rule] in allowed, (rule, by[rule])
    # every other rule either needs markers (NOT_SCORED without them) or cannot be judged in the sim at all
    marker_rules = ids - set(MARKER_FREE_RULES)
    assert marker_rules and all(by[r] in ("NOT_SCORED", "UNVERIFIABLE_IN_SIM") for r in marker_rules), \
        {r: by[r] for r in marker_rules if by[r] not in ("NOT_SCORED", "UNVERIFIABLE_IN_SIM")}
    assert all(by[r] == "NOT_SCORED" for r in ("STRUCT-03", "TIME-04", "TIME-05", "TIME-06", "LOOK-05", "STRUCT-01"))
