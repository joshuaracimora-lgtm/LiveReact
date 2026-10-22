"""Parameter search over the tunable knobs, gated by the baseline.

Plain random-perturbation hill climbing: each trial perturbs 1-3 tunable
knobs of the current best (gaussian, scaled to the knob's range; ints
rounded), evaluates every labeled track, and asks the gate whether the result
may replace the best. No LLM, no tokens. Every trial is one JSON line in the
log; the run stops on a wall-clock budget, a trial budget, a patience budget
(no acceptance in N trials) or any crash, and writes summary.md.
"""
from __future__ import annotations

import json
import random
import time
import traceback
from datetime import datetime
from pathlib import Path

from .. import params as knobs
from .evaluate import Evaluation, evaluate, labeled_tracks
from .gate import compare


def perturb(best: dict, spec: dict, rng: random.Random, k_max: int = 3, scale: float = 0.25) -> dict:
    tun = knobs.tunable(spec)
    names = rng.sample(sorted(tun), k=rng.randint(1, min(k_max, len(tun))))
    cand = dict(best)
    for n in names:
        meta = tun[n]
        lo, hi = meta["range"]
        cur = cand.get(n, meta["default"])
        v = cur + rng.gauss(0.0, scale * (hi - lo))
        v = min(hi, max(lo, v))
        cand[n] = int(round(v)) if meta["type"] == "int" else round(v, 4)
    return cand


def run_search(out_dir: str | Path, baseline_path: str | Path, hours: float = 6.0, max_trials: int = 5000,
               patience: int = 200, seed: int = 0, rig: str = "rigs/josh_rig.json", gestures: str | None = None,
               log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec = knobs.load_spec()
    rng = random.Random(seed)
    tracks = labeled_tracks()
    baseline = Evaluation.from_json(json.loads(Path(baseline_path).read_text(encoding="utf-8")))
    base_names = {t.track for t in baseline.tracks}
    cur_names = {p.name for p in tracks}
    from .evaluate import rules_fingerprint
    if baseline.rules != rules_fingerprint():
        log(f"scorer rule set changed since the baseline ({baseline.rules} -> {rules_fingerprint()}); re-evaluating baseline")
        baseline = evaluate(None, tracks, rig=rig, gestures=gestures)
        (out / "baseline.json").write_text(json.dumps(baseline.to_json(), indent=2), encoding="utf-8")
    elif cur_names != base_names:
        log(f"labeled set changed since the baseline (+{sorted(cur_names - base_names)} -{sorted(base_names - cur_names)}); re-evaluating baseline")
        baseline = evaluate(None, tracks, rig=rig, gestures=gestures)
        (out / "baseline.json").write_text(json.dumps(baseline.to_json(), indent=2), encoding="utf-8")
    best, best_params = baseline, {}
    trials_log = open(out / "trials.jsonl", "a", encoding="utf-8")
    t_start = time.time()
    deadline = t_start + hours * 3600
    accepted, since_accept, n = 0, 0, 0
    stop = "trial budget"
    log(f"search: {len(tracks)} tracks, baseline tuning={baseline.summary()['tuning']['compliance']} holdout={baseline.summary()['holdout']['compliance']}")
    try:
        while n < max_trials:
            if time.time() > deadline:
                stop = "wall-clock budget"
                break
            if since_accept >= patience:
                stop = f"no acceptance in {patience} trials"
                break
            n += 1
            cand_params = perturb(best_params, spec, rng)
            t0 = time.time()
            ev = evaluate(cand_params, tracks, rig=rig, gestures=gestures)
            verdict = compare(best, ev)
            rec = {"trial": n, "t": datetime.now().isoformat(timespec="seconds"), "secs": round(time.time() - t0, 1),
                   "params": cand_params, "changed": sorted(k for k in cand_params if cand_params[k] != best_params.get(k, spec[k]["default"])),
                   "tuning": ev.summary()["tuning"]["compliance"], "holdout": ev.summary()["holdout"]["compliance"],
                   "critical_fails": ev.summary()["tuning"]["critical_fails"] + ev.summary()["holdout"]["critical_fails"],
                   "accept": verdict["accept"], "reasons": verdict["reasons"]}
            trials_log.write(json.dumps(rec) + "\n")
            trials_log.flush()
            if verdict["accept"]:
                best, best_params = ev, cand_params
                accepted += 1
                since_accept = 0
                (out / "best_params.json").write_text(json.dumps(best_params, indent=2), encoding="utf-8")
                (out / "best_evaluation.json").write_text(json.dumps(best.to_json(), indent=2), encoding="utf-8")
                log(f"trial {n}: ACCEPT tuning={rec['tuning']} holdout={rec['holdout']} changed={rec['changed']}")
            else:
                since_accept += 1
                if n % 25 == 0:
                    log(f"trial {n}: {accepted} accepted so far; best tuning={best.summary()['tuning']['compliance']}")
    except Exception:
        stop = "crash: " + traceback.format_exc()[-600:]
    trials_log.close()
    summary = {"stopped": stop, "trials": n, "accepted": accepted, "hours": round((time.time() - t_start) / 3600, 2),
               "baseline": baseline.summary(), "best": best.summary(), "best_params": best_params,
               "tracks": [p.name for p in tracks]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(summary_md(summary, baseline, best), encoding="utf-8")
    return summary


def summary_md(s: dict, baseline: Evaluation, best: Evaluation) -> str:
    L = [f"# Search summary — {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- stopped: {s['stopped'].splitlines()[0]}", f"- trials: {s['trials']}, accepted: {s['accepted']}, {s['hours']} h",
         f"- tuning compliance: {s['baseline']['tuning']['compliance']} → **{s['best']['tuning']['compliance']}**",
         f"- hold-out compliance: {s['baseline']['holdout']['compliance']} → **{s['best']['holdout']['compliance']}**",
         f"- critical fails (tuning): {s['baseline']['tuning']['critical_fails']} → {s['best']['tuning']['critical_fails']}", ""]
    if s["best_params"]:
        L += ["## Best parameters (vs default)", ""]
        spec = knobs.load_spec()
        for k, v in sorted(s["best_params"].items()):
            L.append(f"- `{k}`: {spec[k]['default']} → **{v}**")
        L.append("")
    base_by = {t.track: t for t in baseline.tracks}
    L += ["## Rule changes per track", ""]
    for t in best.tracks:
        b = base_by.get(t.track)
        if not b:
            continue
        ch = [f"{r}: {b.verdicts.get(r)}→{st}" for r, st in t.verdicts.items() if b.verdicts.get(r) != st]
        L.append(f"- **{t.track[:40]}** ({'hold-out' if t.holdout else 'tuning'}) {b.compliance}→{t.compliance}" + (": " + "; ".join(ch) if ch else ": no change"))
    L += ["", "## Still failing (best)", ""]
    for t in best.tracks:
        fails = [r for r, st in t.verdicts.items() if st == "FAIL"]
        if fails:
            L.append(f"- {t.track[:40]}: {', '.join(fails)}")
    return "\n".join(L) + "\n"
