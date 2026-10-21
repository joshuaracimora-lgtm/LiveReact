"""Run the parameter search (the first autonomous piece).

    python tools/loop/search.py --hours 6 --patience 200
    python tools/loop/search.py --max-trials 20 --hours 0.2     # smoke test

Preflight: the determinism test must pass, or the run aborts -- every
comparison the search makes assumes identical inputs give identical frames.
Writes results/search/<timestamp>/{trials.jsonl,best_params.json,summary.md}.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def preflight() -> bool:
    from livereact.sim.harness import run_sim
    from livereact.sim.timeline import Timeline
    from livereact.sim.synth import synth_house
    audio = synth_house(seconds=20)
    tl = Timeline(bpm=128.0)
    a = run_sim("synth", tl, "rigs/josh_rig.json", audio=audio)
    b = run_sim("synth", tl, "rigs/josh_rig.json", audio=audio)
    return a.frames_digest() == b.frames_digest()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hours", type=float, default=6.0)
    p.add_argument("--max-trials", type=int, default=5000)
    p.add_argument("--patience", type=int, default=200)
    p.add_argument("--seed", type=int, default=None, help="RNG seed (default: date-based)")
    p.add_argument("--baseline", default="results/baseline/evaluation.json")
    p.add_argument("--out", default=None)
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--gestures", default=None)
    a = p.parse_args()
    if not Path(a.baseline).exists():
        print(f"no baseline at {a.baseline}; run tools/loop/baseline.py first")
        return 1
    if not preflight():
        print("PREFLIGHT FAILED: harness is not deterministic; aborting")
        return 3
    from livereact.loop.search import run_search
    out = a.out or f"results/search/{datetime.now():%Y%m%d-%H%M%S}"
    seed = a.seed if a.seed is not None else int(datetime.now().strftime("%Y%m%d"))
    s = run_search(out, a.baseline, hours=a.hours, max_trials=a.max_trials, patience=a.patience, seed=seed,
                   rig=a.rig, gestures=a.gestures)
    print(f"\n{s['stopped'].splitlines()[0]} | {s['trials']} trials, {s['accepted']} accepted | tuning {s['baseline']['tuning']['compliance']} -> {s['best']['tuning']['compliance']} | hold-out {s['baseline']['holdout']['compliance']} -> {s['best']['holdout']['compliance']}")
    print(f"-> {out}/summary.md")
    return 0 if not s["stopped"].startswith("crash") else 2


if __name__ == "__main__":
    raise SystemExit(main())
