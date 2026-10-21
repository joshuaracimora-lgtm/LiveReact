"""Run every track in a folder through the harness; one JSON summary per run.

    python tools/harness/run_batch.py [--tracks tracks] [--rig rigs/josh_rig.json]
        [--gestures gestures/static.json] [--out results/<timestamp>]

Writes ``<out>/<track>/{frames.npz,signals.csv,summary.json}`` and
``<out>/summary.json`` (one entry per track plus a batch digest). Exit code 1
if any track failed, so a scheduled agent can tell. Tracks without a
``.timeline.json`` are skipped and listed -- run analyze_track.py first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from livereact.sim.harness import default_seed_range, run_sim
from livereact.sim.timeline import Timeline, timeline_path_for

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tracks", default="tracks")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--gestures", default=None)
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--cold-start", action="store_true", help="Cold-start the energy normalizer (live --no-norm-seed); default seeds from config.json")
    p.add_argument("--no-frames", action="store_true", help="Skip frames.npz/signals.csv; summary only")
    a = p.parse_args()

    out = Path(a.out or f"results/{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    tracks = sorted(x for x in Path(a.tracks).iterdir() if x.suffix.lower() in AUDIO_EXT)
    batch = {"started": datetime.now().isoformat(timespec="seconds"), "rig": a.rig,
             "gestures": a.gestures, "runs": [], "skipped": [], "failed": []}
    digests = []
    for track in tracks:
        tpath = timeline_path_for(track)
        if not tpath.exists():
            batch["skipped"].append({"track": track.name, "reason": "no timeline.json"})
            continue
        t0 = time.time()
        try:
            r = run_sim(track, Timeline.load(tpath), a.rig, gestures=a.gestures, duration=a.duration,
                        seed_range=None if a.cold_start else default_seed_range())
        except Exception as exc:  # keep going; the agent needs the whole picture
            batch["failed"].append({"track": track.name, "error": repr(exc),
                                    "traceback": traceback.format_exc()[-2000:]})
            continue
        r.summary["track"] = track.name
        r.summary["frames_digest"] = r.frames_digest()
        r.summary["sim_seconds"] = round(time.time() - t0, 2)
        d = out / track.stem
        if a.no_frames:
            d.mkdir(parents=True, exist_ok=True)
            (d / "summary.json").write_text(json.dumps(r.summary, indent=2), encoding="utf-8")
        else:
            r.save(d)
        digests.append(r.summary["frames_digest"])
        batch["runs"].append(r.summary)
        print(f"{track.name}: {r.summary['total_seconds']}s in {r.summary['sim_seconds']}s "
              f"| flips/min {r.summary['bucket_flips_per_min']} | scenes {r.summary['scene_changes']} "
              f"| {r.summary['frames_digest'][:12]}")
    batch["batch_digest"] = hashlib.sha256("".join(digests).encode()).hexdigest()
    batch["finished"] = datetime.now().isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")
    print(f"\n{len(batch['runs'])} ran, {len(batch['skipped'])} skipped, {len(batch['failed'])} failed -> {out}/")
    for s in batch["skipped"]:
        print(f"  skipped {s['track']}: {s['reason']}")
    for f in batch["failed"]:
        print(f"  FAILED {f['track']}: {f['error']}")
    return 1 if batch["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
