"""Laser report: what the FAN profile's lasers do on every track of the USB export (2026-10-28).

    python tools/agent/laser_report.py [--usb usb_export] [--minutes 45] [--out results/agent/<date>]
                                       [--rig rigs/junior_crib_fan.json] [--limit N]

Runs each track through the real pipeline in drive mode (the sweep's path) with the fan rig and
writes ``LASERS.md`` + ``lasers.json``: every drop the lasers fired on or passed, with the verdict
(fired / soft drop / too soon / duty limit / plan forbids), the hit, the three band envelopes and
the colour that sound picks; per track, the moments, colours and lit share. It also checks the
rules Josh set for Saturday (2026-10-31): nothing lit before a drop (an auto moment lit outside
DROP/PEAK is an invariant finding), nothing where the plan forbids the laser, and not every drop
fires. Josh reads the per-drop table to tune engines/laser_fan.py's criteria. Exit 0 ok, 1
invariant findings, 2 no export. Simulation only: the hardware check is TESTING.md's bench test.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

AUTO = ("drop", "peak", "wub")
HOT = ("DROP", "PEAK")


def track_record(name: str, rows: list[dict], candidates: list[dict]) -> dict:
    """Per-track laser facts from the sim's rows and the engine's candidate log (pure; tested)."""
    by_t = {round(r["t"], 2): r for r in rows}
    times = sorted(by_t)
    for c in candidates:   # the plan's section at each candidate, for the reader
        i = min(bisect.bisect_left(times, c["t"]), len(times) - 1) if times else None
        r = by_t[times[i]] if i is not None else {}
        c["section"] = r.get("section") or ""
    moments, prev, findings, forbidden = [], "", [], False
    for r in rows:
        trig = r.get("laser_trigger", "")
        if trig and trig != prev:
            moments.append({"t": round(r["t"], 1), "trigger": trig, "color": r.get("laser_color", ""),
                            "bucket": r["bucket"]})
        lit_auto = bool(r.get("laser_on")) and trig in AUTO
        if lit_auto and r["bucket"] not in HOT and trig != prev:   # a moment that STARTS outside the drop
            findings.append(f"invariant:lit_before_drop@{r['t']:.1f}s({r['bucket']})")
        now_forbidden = lit_auto and bool(r.get("plan_active")) and r.get("laser_allowed") is False
        if now_forbidden and not forbidden:                         # once per stretch, not per tick
            findings.append(f"invariant:lit_where_plan_forbids@{r['t']:.1f}s")
        forbidden = now_forbidden
        prev = trig
    n = len(rows) or 1
    drops = [c for c in candidates if c["trigger"] == "drop"]
    return {
        "track": name,
        "lit_pct": round(100 * sum(1 for r in rows if r.get("laser_on")) / n, 1),
        "moments": moments,
        "colors": dict(Counter(m["color"] for m in moments)),
        "drops": len(drops),
        "drops_fired": sum(1 for c in drops if c["verdict"] == "fired"),
        "candidates": candidates,
        "findings": sorted(set(findings)),
    }


def report_md(recs: list[dict], minutes: float, rig: str, total: int) -> str:
    cands = [c for r in recs for c in r["candidates"]]
    moments = [m for r in recs for m in r["moments"]]
    drops = [c for c in cands if c["trigger"] == "drop"]
    inv = [(r["track"], f) for r in recs for f in r["findings"]]
    silent = [r["track"] for r in recs if not r["moments"]]
    out = [f"# Lasers {datetime.now():%Y-%m-%d %H:%M} -- fan profile, {len(recs)}/{total} USB tracks "
           f"in {minutes:.0f} min ({rig})", ""]
    out.append(f"- invariant findings (lit before a drop / where the plan forbids): **{len(inv)}**")
    for t, f in inv[:20]:
        out.append(f"  - `{t}`: {f}")
    out.append(f"- drops: {len(drops)}, lasers fired on **{sum(1 for c in drops if c['verdict'] == 'fired')}**; "
               f"verdicts: {dict(Counter(c['verdict'] for c in drops))}")
    out.append(f"- moments: {len(moments)}; by trigger {dict(Counter(m['trigger'] for m in moments))}; "
               f"by colour {dict(Counter(m['color'] for m in moments))}")
    out.append(f"- tracks where the lasers never fired: {len(silent)}")
    lit = sorted(r["lit_pct"] for r in recs)
    if lit:
        out.append(f"- lit share per track: median {lit[len(lit) // 2]} %, max {lit[-1]} %")
    out += ["", "## Every drop (tune engines/laser_fan.py from this)", "",
            "| track | t (s) | section | hit | low | mid | high | colour | verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        for c in r["candidates"]:
            if c["trigger"] != "drop":
                continue
            out.append(f"| {r['track'][:40]} | {c['t']:.0f} | {c.get('section', '')} | {c['hit']} | {c['low']} | "
                       f"{c['mid']} | {c['high']} | {c['color']} | {c['verdict']} |")
    out += ["", "## Per track", "", "| track | lit % | drops fired | moments (t s trigger colour) |", "|---|---|---|---|"]
    for r in recs:
        ms = ", ".join(f"{m['t']:.0f} {m['trigger']} {m['color']}" for m in r["moments"][:8])
        out.append(f"| {r['track'][:40]} | {r['lit_pct']} | {r['drops_fired']}/{r['drops']} | {ms} |")
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--usb", default=None)
    p.add_argument("--minutes", type=float, default=45.0)
    p.add_argument("--out", default=None)
    p.add_argument("--rig", default="rigs/junior_crib_fan.json")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = p.parse_args()

    from livereact.loop.evaluate import resolve_usb
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for
    from livereact.sim.harness import default_seed_range, run_sim

    out_dir = Path(a.out) if a.out else ROOT / "results" / "agent" / a.date
    out_dir.mkdir(parents=True, exist_ok=True)
    usb = resolve_usb(a.usb)
    if usb is None:
        print("no rekordbox export (usb_export/ or $LIVEREACT_USB); no laser report")
        return 2
    tracks = [t for t in usb.tracks() if t.complete]
    tracks.sort(key=lambda t: hashlib.sha256((a.date + "laser" + t.name).encode("utf-8")).hexdigest())
    if a.limit:
        tracks = tracks[: a.limit]
    deadline = time.monotonic() + a.minutes * 60.0
    recs: list[dict] = []
    for t in tracks:
        if time.monotonic() > deadline:
            break
        try:
            table, grid, wave = usb.load_analysis(t)
            try:
                look = load_look_notes(look_notes_path_for(t.stem, "tracks"))
            except ValueError:
                look = None
            r = run_sim(t.audio, usb.timeline(t, grid), a.rig, seed_range=default_seed_range(), phrases=table,
                        drive=table is not None, grid=grid, waveform=wave, track_id=t.info.id, look=look)
            recs.append(track_record(t.name, r.signals, r.summary.get("laser_candidates", [])))
        except Exception as exc:   # one bad track must not cost the night its report
            recs.append({"track": t.name, "lit_pct": 0.0, "moments": [], "colors": {}, "drops": 0, "drops_fired": 0,
                         "candidates": [], "findings": [f"crash:{type(exc).__name__}:{str(exc)[:80]}"]})
    md = report_md(recs, a.minutes, a.rig, len(tracks))
    (out_dir / "LASERS.md").write_text(md, encoding="utf-8")
    (out_dir / "lasers.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
    print("\n".join(md.splitlines()[:8]))
    print(f"-> {out_dir / 'LASERS.md'}")
    return 1 if any(f.startswith("invariant") for r in recs for f in r["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
