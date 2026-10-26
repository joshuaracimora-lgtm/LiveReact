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

from livereact.loop.evaluate import evaluate, labeled_tracks, resolve_usb, sweep_sample


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="results/baseline/evaluation.json")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--usb", default=None, help="rekordbox USB export copy (default: usb_export/ or $LIVEREACT_USB; 'none' = no plans)")
    p.add_argument("--drive", dest="drive", action="store_true", default=None, help="drive mode (default when a USB export is found)")
    p.add_argument("--no-drive", dest="drive", action="store_false")
    p.add_argument("--sweep-n", type=int, default=None, help="marker-free USB sample size (default 20 with a USB export)")
    a = p.parse_args()
    usb = resolve_usb(a.usb)
    drive = a.drive if a.drive is not None else usb is not None
    sweep_n = a.sweep_n if a.sweep_n is not None else (20 if usb is not None else 0)
    tracks = labeled_tracks(usb=usb)
    sweep = sweep_sample(usb, sweep_n, exclude={t.name for t in tracks}) if usb is not None else []
    print(f"{len(tracks)} labeled tracks, {len(sweep)} sweep tracks, mode={'drive' if drive else 'classifier'}"
          + (f", usb={usb.root}" if usb is not None else ""))
    ev = evaluate(None, tracks, rig=a.rig, usb=usb, drive=drive, sweep_tracks=sweep,
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
