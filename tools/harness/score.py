"""Score harness runs against the lighting rules.

    python tools/harness/score.py results/<batch>            # every run dir in a batch
    python tools/harness/score.py results/<batch>/<track>    # one run

Each run dir gets ``score.json``; a batch gets ``scorecard.md``. Needs the
run's ``frames.npz`` + ``signals.csv`` (run_batch.py without --no-frames) and
the track's ``tracks/<name>.timeline.json`` with ear-verified markers.
Exit code 2 if any run is BLOCKED (a critical FAIL), else 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from livereact.sim.score import load_run, score_run, scorecard_md
from livereact.sim.timeline import Timeline


def find_timeline(run_dir: Path, tracks_dir: Path) -> Path | None:
    cand = tracks_dir / f"{run_dir.name}.timeline.json"
    if cand.exists():
        return cand
    hits = [p for p in tracks_dir.glob("*.timeline.json") if p.name.startswith(run_dir.name[:20])]
    return hits[0] if len(hits) == 1 else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--tracks", default="tracks")
    a = p.parse_args()
    root = Path(a.path)
    runs = [root] if (root / "frames.npz").exists() else sorted(d for d in root.iterdir() if (d / "frames.npz").exists())
    if not runs:
        print(f"no runs with frames.npz under {root} (run_batch.py without --no-frames)")
        return 1
    batch, blocked = [], 0
    for d in runs:
        tp = find_timeline(d, Path(a.tracks))
        if tp is None:
            print(f"{d.name}: no matching timeline in {a.tracks}/ -- skipped")
            continue
        run = load_run(d, Timeline.load(tp), a.rig)
        sc = score_run(run)
        sc["run"] = str(d)
        sc["timeline"] = str(tp)
        (d / "score.json").write_text(json.dumps(sc, indent=2), encoding="utf-8")
        batch.append((d.name, sc))
        blocked += sc["status"] == "BLOCKED"
        c = sc["counts"]
        print(f"{d.name[:40]:40} {sc['status']:7} compliance={sc['compliance']}  P{c['PASS']} F{c['FAIL']} U{c['UNVERIFIABLE_IN_SIM']} -{c['NOT_SCORED']}"
              + (f"  critical: {', '.join(sc['critical_fails'])}" if sc["critical_fails"] else ""))
    if len(runs) > 1 or root != runs[0]:
        (root / "scorecard.md").write_text(scorecard_md(batch), encoding="utf-8")
        print(f"scorecard -> {root / 'scorecard.md'}")
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
