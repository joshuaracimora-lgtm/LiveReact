"""Is the local baseline still a description of the code and labels on this checkout?

    python tools/agent/baseline_fresh.py [--baseline results/baseline/evaluation.json] [--usb usb_export]

Exit 0 = fresh; 1 = stale (each reason printed); 2 = no baseline. The nightly orchestrator runs it after
pulling ``harness`` and regenerates the baseline (``tools/loop/baseline.py``) when stale, BEFORE taking the
night's ``baseline_before.json`` snapshot -- otherwise the gate would credit or blame an agent for changes
that arrived with the pull (2026-10-26). Stale means any of:

- the scorer changed (``rules_fingerprint``: score.py + drop_class.py);
- Josh's labels on the scored tracks changed (markers, drop classes, look notes) or the scored set did;
- the rekordbox analysis of the scored tracks changed (a re-export);
- not drive mode while a USB export is present;
- engine code, params, section classes or rigs changed since the commit the baseline was made at
  (``git diff <commit> HEAD -- src params.json section_classes.json rigs``), or the baseline predates
  recording its commit.

Cheap: no simulation, only file reads and hashes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
CODE_PATHS = ("src", "params.json", "section_classes.json", "rigs")


def reasons(base: dict, usb, tracks_dir: Path) -> list[str]:
    from livereact.loop.evaluate import ear_timeline_for, labeled_tracks, labels_fingerprint, rules_fingerprint
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for
    out = []
    if base.get("rules") != rules_fingerprint():
        out.append(f"scorer changed ({base.get('rules')} -> {rules_fingerprint()})")
    tracks = labeled_tracks(tracks_dir, usb)
    names = sorted(p.name for p in tracks)
    if sorted(t["track"] for t in base.get("tracks", [])) != names:
        out.append("the scored track set changed")
    items = [(p.name, ear_timeline_for(p, usb, tracks_dir), load_look_notes(look_notes_path_for(p.stem, tracks_dir)))
             for p in tracks]
    lf = labels_fingerprint(items) if items else None
    if base.get("labels_fingerprint") != lf:
        out.append(f"Josh's labels changed ({base.get('labels_fingerprint')} -> {lf})")
    if usb is not None:
        uts = [u for u in (usb.find(p) for p in tracks) if u is not None]
        af = usb.analysis_fingerprint(uts) if uts else None
        if base.get("analysis_fingerprint") != af:
            out.append(f"rekordbox analysis changed ({base.get('analysis_fingerprint')} -> {af})")
        if base.get("mode") != "drive":
            out.append(f"mode is {base.get('mode')}, not drive")
    commit = base.get("commit")
    if not commit:
        out.append("baseline does not record the commit it describes")
    else:
        r = subprocess.run(["git", "diff", "--quiet", commit, "HEAD", "--", *CODE_PATHS], cwd=ROOT)
        if r.returncode == 1:
            out.append(f"engine code changed since {commit[:10]}")
        elif r.returncode != 0:
            out.append(f"commit {commit[:10]} unknown here")
    return out


def main(argv: list[str] | None = None) -> int:
    from livereact.loop.evaluate import resolve_usb
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", default=str(ROOT / "results" / "baseline" / "evaluation.json"))
    ap.add_argument("--usb", default=None)
    ap.add_argument("--tracks", default=str(ROOT / "tracks"))
    a = ap.parse_args(argv)
    bp = Path(a.baseline)
    if not bp.exists():
        print(f"no baseline at {bp}")
        return 2
    rs = reasons(json.loads(bp.read_text(encoding="utf-8")), resolve_usb(a.usb), Path(a.tracks))
    if rs:
        print("baseline STALE: " + "; ".join(rs))
        return 1
    print("baseline fresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
