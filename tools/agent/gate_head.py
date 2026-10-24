"""Evaluate the engine as it is in the working tree and gate it against a baseline.

    python tools/agent/gate_head.py                         # vs results/baseline/evaluation.json
    python tools/agent/gate_head.py --baseline other.json   # vs any evaluation (e.g. harness's)
    python tools/agent/gate_head.py --write-baseline        # on ACCEPT, overwrite the baseline file
    python tools/agent/gate_head.py --out results/agent/<date>/gate.json

Prints the gate verdict and every per-track rule flip (PASS->FAIL and FAIL->PASS),
exit 0 on ACCEPT, 1 on REJECT, 2 on error. This is the acceptance test for an
engine change: the agent runs it before committing, the orchestrator runs it
again afterwards against harness's baseline so the night's claim is verified
independently of the agent's own report.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def flips(base: dict, cand: dict) -> list[dict]:
    """Per-track rule status changes between two evaluation jsons."""
    bt = {t["track"]: t for t in base["tracks"]}
    out = []
    for t in cand["tracks"]:
        b = bt.get(t["track"])
        if not b:
            out.append({"track": t["track"], "rule": "*", "from": None, "to": "new track"})
            continue
        for r in sorted(set(b["verdicts"]) | set(t["verdicts"])):
            if b["verdicts"].get(r) != t["verdicts"].get(r):
                out.append({"track": t["track"], "rule": r, "from": b["verdicts"].get(r), "to": t["verdicts"].get(r)})
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline", default="results/baseline/evaluation.json")
    p.add_argument("--baseline-ref", default=None,
                   help="git ref whose committed results/baseline/evaluation.json is the baseline (e.g. harness); overrides --baseline")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--write-baseline", action="store_true", help="on ACCEPT, write the candidate as results/baseline/evaluation.json")
    p.add_argument("--out", default=None, help="also write the candidate evaluation + gate to this json")
    p.add_argument("--out-eval", default=None,
                   help="also write the bare candidate evaluation (same shape as results/baseline/evaluation.json), "
                        "usable as --baseline for tools/loop/search.py")
    a = p.parse_args()
    from livereact.loop.evaluate import Evaluation, evaluate, labeled_tracks
    from livereact.loop.gate import compare
    if a.baseline_ref:
        import subprocess
        bp = f"{a.baseline_ref}:results/baseline/evaluation.json"
        got = subprocess.run(["git", "show", bp], cwd=ROOT, capture_output=True)
        if got.returncode != 0:
            print(f"no committed baseline at {bp}: {got.stderr.decode(errors='replace').strip()}")
            return 2
        base_json = json.loads(got.stdout.decode("utf-8"))
    else:
        bp = Path(a.baseline)
        if not bp.exists():
            print(f"no baseline at {bp}")
            return 2
        base_json = json.loads(bp.read_text(encoding="utf-8"))
    base = Evaluation.from_json(base_json)
    tracks = labeled_tracks()
    print(f"evaluating {len(tracks)} labeled tracks against {bp} ...", flush=True)
    cand = evaluate(None, tracks, rig=a.rig,
                    on_track=lambda t: print(f"  {'holdout' if t.holdout else 'tuning '} {t.status:7} {t.compliance}  {t.track[:50]}", flush=True))
    if base.rules != cand.rules:
        print(f"RULE SET CHANGED ({base.rules} -> {cand.rules}): the baseline is from another scorer; regenerate it first")
        return 2
    verdict = compare(base, cand)
    cj = cand.to_json()
    fl = flips(base_json, cj)
    bs, cs = base.summary(), cand.summary()
    print(f"\ntuning  compliance {bs['tuning']['compliance']} -> {cs['tuning']['compliance']}   critical fails {bs['tuning']['critical_fails']} -> {cs['tuning']['critical_fails']}")
    print(f"holdout compliance {bs['holdout']['compliance']} -> {cs['holdout']['compliance']}   critical fails {bs['holdout']['critical_fails']} -> {cs['holdout']['critical_fails']}")
    print("flips:" if fl else "flips: none")
    for f in fl:
        print(f"  {f['track'][:40]:40} {f['rule']:9} {f['from']} -> {f['to']}")
    print(f"\nGATE: {'ACCEPT' if verdict['accept'] else 'REJECT'}")
    for r in verdict["reasons"]:
        print(f"  - {r}")
    if a.out:
        op = Path(a.out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps({"gate": verdict, "flips": fl, "candidate": cj}, indent=1), encoding="utf-8")
        print(f"-> {op}")
    if a.out_eval:
        ep = Path(a.out_eval)
        ep.parent.mkdir(parents=True, exist_ok=True)
        ej = dict(cj)
        ej["generated"] = datetime.now().isoformat(timespec="seconds")
        ep.write_text(json.dumps(ej, indent=2), encoding="utf-8")
        print(f"-> {ep}")
    if verdict["accept"] and a.write_baseline:
        cj["generated"] = datetime.now().isoformat(timespec="seconds")
        out = ROOT / "results" / "baseline" / "evaluation.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cj, indent=2), encoding="utf-8")
        print(f"baseline written -> {out} (commit it with: git add -f results/baseline/evaluation.json)")
    return 0 if verdict["accept"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
