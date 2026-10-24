"""Per-beat study table of what the engine saw and did around the labeled markers.

    python tools/agent/dump_signals.py tracks/SWEETER_KLICKAUD.mp3
    python tools/agent/dump_signals.py tracks/<track>.mp3 --around drop --before 4 --after 8
    python tools/agent/dump_signals.py tracks/<track>.mp3 --all > results/agent/study/sweeter.tsv

Runs the real pipeline on the track (same as the scorer sees) and prints one
row per BEAT: time, bar, beat, bucket, energy_norm, the four bands, the
roll-periodicity score, onset rate, wash level, per-fixture brightness, laser,
max strobe byte on the rig, scene — with a column marking which marker window
the beat sits in. This is what the family-1 session rebuilt by hand in
scratch scripts; the agent should start here instead of re-deriving it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("track")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--around", default=None, help="marker kind(s) to window on, comma-separated (drop,fill,breakdown,build,whiteout,groove)")
    p.add_argument("--before", type=float, default=4.0, help="bars before each marker")
    p.add_argument("--after", type=float, default=8.0, help="bars after each marker (or to its end)")
    p.add_argument("--all", action="store_true", help="print every beat of the track")
    a = p.parse_args()
    import numpy as np
    from livereact.fixtures.rig import load_rig
    from livereact.loop.evaluate import default_seed_range
    from livereact.sim.harness import run_sim
    from livereact.sim.score import Run
    from livereact.sim.timeline import Timeline, timeline_path_for

    tp = Path(a.track)
    tl = Timeline.load(timeline_path_for(tp))
    r = run_sim(tp, tl, a.rig, seed_range=default_seed_range())
    run = Run(r.frames, r.t, r.signals, tl, load_rig(a.rig))
    t = np.asarray(r.t, dtype=float)
    beat = tl.beat_dur
    bar = tl.bar_dur
    t0, _ = tl.grid_start()
    kinds = set(a.around.split(",")) if a.around else None
    markers = [m for m in tl.markers if (kinds is None or m.kind in kinds)]
    print(f"# {tp.name}  bpm={tl.bpm} first_downbeat={tl.first_downbeat_s} markers={len(tl.markers)}  notes={tl.notes!r}")
    for m in tl.markers:
        print(f"# marker {m.kind:9} t={m.t:7.2f}" + (f" end={m.end:7.2f}" if m.end else "") + f"  {m.note}")
    windows = []
    for m in markers:
        end = m.end if m.end else m.t + a.after * bar
        windows.append((m.t - a.before * bar, max(end, m.t + a.after * bar), m))
    if not a.all and not windows:
        print("no markers match; use --all or another --around kind")
        return 1
    keys = ["energy_norm", "band_low", "band_mid", "band_high", "band_snare", "roll_period", "onset_rate", "wash_level",
            "par_brightness", "bar_brightness", "mh_brightness"]
    have = [k for k in keys if r.signals and k in r.signals[0]]
    hdr = ["t", "bar", "beat", "in", "bucket", *have, "laser", "strobe", "drop_mk", "scene"]
    print("\t".join(hdr))
    n_beats = int((t[-1] - t0) / beat) + 1
    for b in range(n_beats):
        bt0 = t0 + b * beat
        bt1 = bt0 + beat
        tag = ""
        for lo, hi, m in windows:
            if lo <= bt0 < hi:
                tag = m.kind + ("+" if bt0 >= m.t else "-")
                break
        if not a.all and not tag:
            continue
        idx = np.where((t >= bt0) & (t < bt1))[0]
        if idx.size == 0:
            continue
        rows = [r.signals[i] for i in idx]
        bucket = max(set(x["bucket"] for x in rows), key=[x["bucket"] for x in rows].count)
        vals = []
        for k in have:
            xs = [float(x[k]) for x in rows if x.get(k) not in (None, "")]
            vals.append(f"{(max(xs) if k == 'roll_period' else sum(xs) / len(xs)):.3f}" if xs else "")
        laser = "L" if bool(run.laser_any[idx].any()) else ""
        strobe = int(round(float(run.strobe[idx].max()) * (255 if run.strobe.max() <= 1.0 else 1)))
        drop_mk = "D" if any(str(x.get("drop_marker")) == "True" for x in rows) else ""
        scene = rows[-1].get("scene", "")
        print("\t".join([f"{bt0:.2f}", str(b // tl.beats_per_bar), str(b % tl.beats_per_bar + 1), tag, bucket, *vals,
                         laser, str(strobe), drop_mk, str(scene)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
