"""Evaluate the current engine (default params) on every labeled track and
save it as the baseline the loop gates against.

    python tools/loop/baseline.py [--out results/baseline/evaluation.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from livereact.loop.evaluate import evaluate, labeled_tracks


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="results/baseline/evaluation.json")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    a = p.parse_args()
    tracks = labeled_tracks()
    print(f"{len(tracks)} labeled tracks")
    ev = evaluate(None, tracks, rig=a.rig,
                  on_track=lambda t: print(f"  {'holdout' if t.holdout else 'tuning '} {t.status:7} {t.compliance}  {t.track[:50]}"))
    d = ev.to_json()
    d["generated"] = datetime.now().isoformat(timespec="seconds")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(json.dumps(ev.summary(), indent=1))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
