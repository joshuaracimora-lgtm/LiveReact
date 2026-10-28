"""Every USB track's song style (rekordbox/song_style.py), played as a set in USB order.

    python tools/agent/song_styles.py [--usb usb_export] [--json out.json]

Prints the feature percentiles (song_style.FEATURE_SPANS is set from them), how the styles spread
over the library, and the back-to-back check Josh asked for (2026-10-28): how many ways each song
differs from the one before it, with and without the "differ from the previous song" rule.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from livereact.loop.evaluate import resolve_usb  # noqa: E402
from livereact.rekordbox.planner import plan_track  # noqa: E402
from livereact.rekordbox.pssi import beat_to_seconds  # noqa: E402
from livereact.rekordbox.song_style import (FEATURE_SPANS, MIN_DIFFERENCES, SongStyle, choose,  # noqa: E402
                                            song_features)


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--usb", default=None)
    p.add_argument("--json", default=None)
    a = p.parse_args()
    usb = resolve_usb(a.usb)
    if usb is None:
        print("no USB export")
        return 1
    rows = []
    prev_chained: SongStyle | None = None
    for t in usb.tracks():
        if not t.complete:
            continue
        table, grid, wave = usb.load_analysis(t)
        if table is None or wave is None:
            continue
        plan = plan_track(table, track_id=t.info.id, title=t.info.title, bpm=t.info.bpm, grid=grid, waveform=wave)

        def seconds(beat: int, grid=grid, bpm=t.info.bpm):
            try:
                return beat_to_seconds(beat, grid, bpm=bpm or None)
            except ValueError:
                return None
        feats = song_features(plan.sections, seconds=seconds, waveform=wave)
        if feats is None:
            continue
        alone = choose(feats)
        chained = choose(feats, prev_chained)
        rows.append(dict(title=t.info.title, features=asdict(feats), alone=alone, chained=chained,
                         prev=prev_chained))
        prev_chained = chained
    print(f"{len(rows)} tracks with a waveform and a drop\n")
    print("feature      p5      p50     p95     span used")
    for name in FEATURE_SPANS:
        xs = [r["features"][name] for r in rows]
        print(f"{name:12} {pct(xs, .05):7.3f} {pct(xs, .5):7.3f} {pct(xs, .95):7.3f}  {FEATURE_SPANS[name]}")
    print("\nstyle spread (with the back-to-back rule):")
    for f in fields(SongStyle):
        c = Counter(getattr(r["chained"], f.name) for r in rows)
        print(f"  {f.name:10} " + "  ".join(f"{k}:{v}" for k, v in c.most_common()))
    distinct = len({r["chained"] for r in rows})
    print(f"\ndistinct styles: {distinct} of {len(rows)} songs")
    alone_diff = [rows[i]["alone"].differences(rows[i - 1]["alone"]) for i in range(1, len(rows))]
    chained_diff = [rows[i]["chained"].differences(rows[i - 1]["chained"]) for i in range(1, len(rows))]
    n = len(alone_diff)
    for label, d in (("from the sound alone", alone_diff), ("with the back-to-back rule", chained_diff)):
        same = sum(1 for x in d if x == 0)
        weak = sum(1 for x in d if x < MIN_DIFFERENCES)
        print(f"back-to-back, {label:27}: identical {same}/{n}, under {MIN_DIFFERENCES} differences {weak}/{n}, "
              f"mean differences {sum(d) / max(1, n):.2f} of {len(fields(SongStyle))}")
    old = SongStyle()
    print(f"back-to-back, before (every song the same) : identical {n}/{n}, mean differences 0.00")
    moved = sum(1 for r in rows if r["chained"] != r["alone"])
    print(f"songs the rule moved off their best fit: {moved}/{len(rows)}")
    print("\nfirst 12 in set order:")
    for r in rows[:12]:
        print(f"  {r['title'][:34]:34} {r['chained'].summary()}")
    if a.json:
        Path(a.json).write_text(json.dumps([dict(title=r["title"], features=r["features"],
                                                 alone=asdict(r["alone"]), chained=asdict(r["chained"]))
                                            for r in rows], indent=1), encoding="utf-8")
    del old
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
