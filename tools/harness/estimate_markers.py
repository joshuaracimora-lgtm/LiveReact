"""Guess drop/breakdown markers for each track and write them into the timeline.

    python tools/harness/estimate_markers.py tracks/*.mp3

Writes ``markers`` into ``<track>.timeline.json`` (replacing any marker whose
note starts with "est:", keeping hand-written ones) and prints a review list
in mm:ss. Open the track, scrub to each time, and fix the JSON by ear: move
``t``, change ``kind`` (drop | breakdown | build), delete the wrong ones, add
the missing ones. Clear the ``note`` on anything you've confirmed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np

from livereact.sim.analyze import estimate_markers, fmt_mmss
from livereact.sim.harness import decode_audio
from livereact.sim.timeline import Timeline, timeline_path_for


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tracks", nargs="+")
    p.add_argument("--review", default="tracks/MARKERS_TO_REVIEW.md")
    a = p.parse_args()
    lines = ["# Marker estimates to verify by ear", "",
             "Each line is a GUESS snapped to a bar line. Fix `tracks/<name>.timeline.json`:",
             "move `t`, change `kind`, delete wrong ones, add missing ones, and clear the",
             "`note` once a marker is confirmed. Times are seconds into the file.", ""]
    for track in a.tracks:
        tp = timeline_path_for(track)
        if not tp.exists():
            print(f"{Path(track).name}: no timeline.json -- run analyze_track.py first")
            continue
        tl = Timeline.load(tp)
        audio = decode_audio(track)
        est = estimate_markers(audio, tl, np)
        kept = [m for m in tl.markers if not m.note.startswith("est:")]
        tl.markers = sorted(kept + est, key=lambda m: m.t)
        tl.save(tp)
        lines.append(f"## {Path(track).name}  ({tl.bpm} BPM, downbeat {tl.first_downbeat_s}s)")
        for m in tl.markers:
            tag = "" if m.note.startswith("est:") else "  (confirmed)"
            lines.append(f"- {fmt_mmss(m.t):>8}  `{m.t}`  **{m.kind}**{tag}")
        lines.append("")
        print(f"{Path(track).name}: {len(est)} estimated markers")
        for m in est:
            print(f"   {fmt_mmss(m.t):>8}  {m.kind:10} {m.note}")
    Path(a.review).write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreview list -> {a.review}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
