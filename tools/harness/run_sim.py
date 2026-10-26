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
    p.add_argument("--pssi", default=None, help="rekordbox ANLZ .EXT for this track: feed its phrase table (+ .DAT grid, waveform) as the XDJ would")
    p.add_argument("--drive", action="store_true", help="the track plan drives the sections (--phrases drive); default = shadow")
    p.add_argument("--track-id", type=int, default=1, help="with --pssi: rekordbox id used for the plan's palette/hook seed")
    p.add_argument("--usb", default=None, help="rekordbox USB export copy (default: usb_export/ or $LIVEREACT_USB; 'none' to skip): "
                                                "pulls the track's phrase table, grid, waveform and id by file name")
    a = p.parse_args()

    tl = Timeline(bpm=a.bpm) if a.bpm else Timeline.load(timeline_path_for(a.track))
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for
    phrases = grid = waveform = None
    track_id = a.track_id
    look = load_look_notes(look_notes_path_for(a.track))
    if a.pssi:
        from livereact.rekordbox.pssi import parse_pqtz, parse_pssi
        from livereact.rekordbox.waveform import parse_pwv5
        ext = Path(a.pssi).read_bytes()
        phrases = parse_pssi(ext)
        if phrases is None:
            print(f"{a.pssi}: no PSSI phrase table"); return 2
        dat_path = Path(a.pssi).with_suffix(".DAT")
        grid = parse_pqtz(dat_path.read_bytes()) if dat_path.exists() else None
        waveform = parse_pwv5(ext)
    elif (a.usb or "").lower() != "none":
        from livereact.rekordbox.usb_export import UsbExport, default_usb_root
        root = Path(a.usb) if a.usb else default_usb_root()
        if root is not None:
            ut = UsbExport(root).find(a.track)
            if ut is None:
                print(f"usb: {Path(a.track).name} not found in {root}; running without a plan")
            else:
                phrases, grid, waveform = UsbExport(root).load_analysis(ut)
                track_id = ut.info.id
                print(f"usb: track {ut.info.id} {ut.info.title!r} ({'phrases' if phrases else 'NO phrase table'})")
    if phrases is not None:
        from livereact.rekordbox.planner import plan_track
        print(plan_track(phrases, track_id=track_id, title=Path(a.track).stem, bpm=tl.bpm, grid=grid,
                         waveform=waveform, look=look).summary())
    r = run_sim(a.track, tl, a.rig, gestures=a.gestures, duration=a.duration, gain=a.gain,
                seed_range=None if a.cold_start else default_seed_range(),
                phrases=phrases, drive=a.drive, grid=grid, waveform=waveform, track_id=track_id, look=look)
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
