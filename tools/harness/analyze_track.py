"""Generate ``<track>.timeline.json`` next to an audio file.

    python tools/harness/analyze_track.py tracks/song.mp3 --bpm 128
    python tools/harness/analyze_track.py tracks/*.mp3          # estimate BPM too

Re-run with ``--force`` to overwrite. Hand-edit the JSON afterwards if the
downbeat is off; the harness trusts the file, not this script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from livereact.sim.analyze import analyze
from livereact.sim.timeline import Timeline, timeline_path_for


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tracks", nargs="+")
    p.add_argument("--bpm", type=float, default=None, help="Known BPM (applies to every track given)")
    p.add_argument("--analyze-seconds", type=float, default=90.0, help="Seconds of audio to analyze (default 90)")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    for track in a.tracks:
        out = timeline_path_for(track)
        if out.exists() and not a.force:
            tl = Timeline.load(out)
            print(f"{out.name}: exists (bpm={tl.bpm}, downbeat={tl.first_downbeat_s}s) -- use --force to redo")
            continue
        tl = analyze(track, a.bpm, a.analyze_seconds)
        tl.save(out)
        print(f"{out.name}: bpm={tl.bpm} first_downbeat={tl.first_downbeat_s}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
