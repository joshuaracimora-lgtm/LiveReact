"""Simulate one track through the real pipeline and save the frame log.

    python tools/harness/run_sim.py tracks/song.mp3 [--rig rigs/josh_rig.json]
        [--gestures gestures/clean_mix.json] [--out results/<auto>] [--duration 90]

Needs ``tracks/song.timeline.json`` (make it with analyze_track.py, or pass
``--bpm`` for a flat grid starting at t=0, as replay_song.py did).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from livereact.sim.harness import default_seed_range, run_sim
from livereact.sim.timeline import Timeline, timeline_path_for


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("track")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--gestures", default=None)
    p.add_argument("--bpm", type=float, default=None, help="Skip the timeline file; flat grid from t=0")
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--gain", type=float, default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--cold-start", action="store_true", help="Cold-start the energy normalizer (live --no-norm-seed); default seeds from config.json")
    a = p.parse_args()

    tl = Timeline(bpm=a.bpm) if a.bpm else Timeline.load(timeline_path_for(a.track))
    r = run_sim(a.track, tl, a.rig, gestures=a.gestures, duration=a.duration, gain=a.gain,
                seed_range=None if a.cold_start else default_seed_range())
    out = a.out or f"results/{datetime.now():%Y%m%d-%H%M%S}/{Path(a.track).stem}"
    r.summary["track"] = a.track
    r.summary["frames_digest"] = r.frames_digest()
    r.save(out)
    print(json.dumps({k: r.summary[k] for k in ("total_seconds", "bucket_distribution_pct",
                                                 "bucket_flips_per_min", "laser_active_pct",
                                                 "scene_changes", "scene_change_offset_from_downbeat_ms",
                                                 "frames_digest")}, indent=1))
    print(f"wrote {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
