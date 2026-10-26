"""The regression gate: may a candidate replace the baseline?

Three conditions, all required (see the plan): tuning-set score up, hold-out
not worse, and NO individual rule regresses on ANY track (PASS -> FAIL, or a
scored verdict becoming unscored). Show-quality metrics are gated too so
"improve dwell by never changing looks" is caught, and both sides must cover
the same labeled tracks. Returns the decision and every reason, so the log
explains rejections.
"""
from __future__ import annotations

import unicodedata

from .evaluate import Evaluation

RANK = {"PASS": 3, "UNVERIFIABLE_IN_SIM": 2, "NOT_SCORED": 1, "FAIL": 0}
QUALITY_FLOOR = {"distinct_looks": 0.8, "brightness_dynamic_range": 0.85,
                 "fixture_utilization": 0.9, "drop_response_mean": 0.8}   # fraction of baseline allowed
MIN_IMPROVEMENT = 0.005                                                    # compliance points on the tuning set
DROP_OFFSET_TOLERANCE_BARS = 0.25   # drive mode: mean |ear drop - DROP commit| may not grow by more than this


def _key(track: str) -> str:
    return unicodedata.normalize("NFC", track)


def mode_mismatch(baseline: Evaluation, candidate: Evaluation) -> str | None:
    """A comparison across modes or across different rekordbox analysis of the scored tracks is meaningless:
    the caller (gate_head.py) treats this like RULE SET CHANGED and asks for a new baseline."""
    if baseline.mode != candidate.mode:
        return (f"mode differs (baseline {baseline.mode}, candidate {candidate.mode}); regenerate the baseline "
                f"in the mode you are gating")
    if (baseline.analysis_fingerprint and candidate.analysis_fingerprint
            and baseline.analysis_fingerprint != candidate.analysis_fingerprint):
        return (f"rekordbox analysis of the scored tracks changed ({baseline.analysis_fingerprint} -> "
                f"{candidate.analysis_fingerprint}); regenerate the baseline")
    return None


def compare(baseline: Evaluation, candidate: Evaluation) -> dict:
    reasons: list[str] = []
    notes: list[str] = []
    b, c = baseline.summary(), candidate.summary()
    mm = mode_mismatch(baseline, candidate)
    if mm:
        reasons.append(mm)

    # Both sides must have simulated from the same normalizer seed, or every frame differs for
    # reasons that have nothing to do with the engine (see sim/harness.py SIM_SEED_RANGE). An older
    # baseline without the field is taken at its word.
    if baseline.seed_range is not None and candidate.seed_range is not None \
            and [round(float(x), 6) for x in baseline.seed_range] != [round(float(x), 6) for x in candidate.seed_range]:
        reasons.append(f"normalizer seed differs (baseline {baseline.seed_range} vs candidate {candidate.seed_range}); "
                       "simulate both from the same seed")

    bt, ct = b["tuning"], c["tuning"]
    improved = ct["compliance"] is not None and bt["compliance"] is not None and ct["compliance"] >= bt["compliance"] + MIN_IMPROVEMENT
    if not improved:
        reasons.append(f"tuning compliance {bt['compliance']} -> {ct['compliance']} (needs +{MIN_IMPROVEMENT})")

    bh, ch = b["holdout"], c["holdout"]
    if bh["n"] and ch["compliance"] is not None and bh["compliance"] is not None and ch["compliance"] < bh["compliance"] - 1e-9:
        reasons.append(f"hold-out regressed {bh['compliance']} -> {ch['compliance']}")

    # Same tracks on both sides, or the comparison means nothing: a track that drops out of the
    # labeled set (a timeline re-marked "est:") would otherwise vanish from every check below and
    # can raise compliance on its own. Names are compared in NFC: audio copied from the Mac is NFD,
    # and Windows keeps whichever form the file arrived in (see sim/timeline.py).
    base_by = {_key(t.track): t for t in baseline.tracks}
    cand_keys = {_key(t.track) for t in candidate.tracks}
    if cand_keys != set(base_by):
        added, dropped = sorted(cand_keys - set(base_by)), sorted(set(base_by) - cand_keys)
        reasons.append(f"labeled track set changed (+{added} -{dropped}); regenerate the baseline first")
    regressions = []
    for t in candidate.tracks:
        bt_ = base_by.get(_key(t.track))
        if bt_ is None:
            continue
        for rule, status in t.verdicts.items():
            old = bt_.verdicts.get(rule)
            if old is not None and RANK[status] < RANK[old]:
                regressions.append(f"{t.track[:28]} {rule} {old}->{status}")
    if regressions:
        reasons.append("rule regressions: " + "; ".join(regressions[:6]) + (" ..." if len(regressions) > 6 else ""))

    for key, floor in QUALITY_FLOOR.items():
        for subset in ("tuning", "holdout"):
            bq, cq = b[subset]["quality"].get(key), c[subset]["quality"].get(key)
            if bq and cq is not None and cq < bq * floor:
                reasons.append(f"{subset} {key} fell {bq} -> {cq} (floor {floor:.0%} of baseline)")

    # drive mode: the lights must not land further from Josh's drops than before
    for subset in ("tuning", "holdout"):
        bo, co = b[subset].get("drop_offset_bars_mean"), c[subset].get("drop_offset_bars_mean")
        if bo is not None and co is not None and co > bo + DROP_OFFSET_TOLERANCE_BARS:
            reasons.append(f"{subset} drop placement worse: {bo} -> {co} bars from the ear drops "
                           f"(tolerance +{DROP_OFFSET_TOLERANCE_BARS})")

    # generalisation: the marker-free sample must not get worse, track by track
    bs, cs = b.get("sweep", {}), c.get("sweep", {})
    if baseline.sweep and candidate.sweep:
        if baseline.sweep_set == candidate.sweep_set:
            if bs.get("compliance") is not None and cs.get("compliance") is not None \
                    and cs["compliance"] < bs["compliance"] - 1e-9:
                reasons.append(f"sweep set (marker-free rules, {cs['n']} USB tracks) regressed {bs['compliance']} -> {cs['compliance']}")
            sweep_base = {_key(t.track): t for t in baseline.sweep}
            sweep_regs = []
            for t in candidate.sweep:
                bt_ = sweep_base.get(_key(t.track))
                if bt_ is None:
                    continue
                for rule, status in t.verdicts.items():
                    old = bt_.verdicts.get(rule)
                    if old is not None and RANK[status] < RANK[old]:
                        sweep_regs.append(f"{t.track[:28]} {rule} {old}->{status}")
            if sweep_regs:
                reasons.append("sweep rule regressions: " + "; ".join(sweep_regs[:6]) + (" ..." if len(sweep_regs) > 6 else ""))
        else:
            notes.append("sweep set changed since the baseline; marker-free generalisation not compared")
    elif candidate.sweep and not baseline.sweep:
        notes.append("baseline has no sweep set; generalisation not compared")

    return {"accept": not reasons, "reasons": reasons, "notes": notes, "baseline": b, "candidate": c,
            "rule_regressions": len(regressions)}
