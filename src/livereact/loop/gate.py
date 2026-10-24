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


def _key(track: str) -> str:
    return unicodedata.normalize("NFC", track)


def compare(baseline: Evaluation, candidate: Evaluation) -> dict:
    reasons: list[str] = []
    b, c = baseline.summary(), candidate.summary()

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

    return {"accept": not reasons, "reasons": reasons, "baseline": b, "candidate": c,
            "rule_regressions": len(regressions)}
