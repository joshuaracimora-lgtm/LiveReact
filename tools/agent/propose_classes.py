"""List every ear-verified drop with the class it gets, for Josh to confirm (2026-10-26).

    python tools/agent/propose_classes.py [--usb usb_export] [--out tracks/DROP_CLASSES_TO_REVIEW.md] [--time11]

For each drop on a scored track: time, length in bars, the class (``sim/drop_class.py``: Josh's explicit
``"class"`` if he set one, else inferred from his note and the length) and why. With ``--time11`` it also
simulates each scored track in drive mode and lists the TIME-11 (state dwell) violations that start inside
a drop, by class -- the evidence for whether snare-roll drops need their own planned sections (Josh's Q6).

The output names Josh's tracks, so it goes to ``tracks/`` (private, never committed; carried by
``tools/private_bundle.py``). Nothing here writes a timeline: a class becomes Josh's when he adds
``"class": "<name>"`` to that drop in ``tracks/<song>.timeline.json`` (or tells Claude to).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def why(m, bpm: float) -> str:
    from livereact.sim.drop_class import (ATMOSPHERIC_WORDS, NEGATED_ROLL_WORDS, ROLL_WORDS, drop_bars,
                                          is_inferred)
    from livereact.sim.score import CLAP_WORDS
    if not is_inferred(m):
        return "set by Josh"
    note = m.note.lower()
    for words, label in ((NEGATED_ROLL_WORDS, "note says no roll"), (ATMOSPHERIC_WORDS, "note says atmospheric"),
                         (CLAP_WORDS, "note names claps/crashes"), (ROLL_WORDS, "note names a roll")):
        hit = next((w for w in words if w in note), None)
        if hit:
            return f'{label} ("{hit}")'
    bars = drop_bars(m, bpm)
    return "no telling note; open-ended" if bars is None else f"no telling note; {bars:.1f} bars"


def time11_by_track(usb, tdir: Path, rig: str) -> dict[str, list[tuple[float, str]]]:
    """Track name -> TIME-11 evidence [(t, note)] from a drive-mode run, like the gate's."""
    from livereact.fixtures.rig import load_rig
    from livereact.loop.evaluate import _audio, ear_timeline_for, labeled_tracks
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for
    from livereact.sim.harness import run_sim
    from livereact.sim.score import Run, score_run
    rig_obj = load_rig(rig)
    out = {}
    for p in labeled_tracks(tdir, usb):
        tl = ear_timeline_for(p, usb, tdir)
        ut = usb.find(p) if usb is not None else None
        phrases = grid = wave = None
        if ut is not None:
            phrases, grid, wave = usb.load_analysis(ut)
        look = load_look_notes(look_notes_path_for(p.stem, tdir))
        r = run_sim(p, tl, rig, audio=_audio(p), phrases=phrases, drive=phrases is not None, grid=grid,
                    waveform=wave, track_id=ut.info.id if ut is not None else 1, look=look)
        sc = score_run(Run(r.frames, r.t, r.signals, tl, rig_obj))
        v = next(v for v in sc["verdicts"] if v["rule"] == "TIME-11")
        out[p.name] = [(float(t), str(n)) for t, n in v.get("evidence", [])]
        print(f"  simulated {p.name[:50]}: TIME-11 {v['status']}", flush=True)
    return out


def build(usb, tdir: Path, *, time11: bool = False, rig: str = "rigs/josh_rig.json") -> list[str]:
    from livereact.loop.evaluate import ear_timeline_for, is_holdout, labeled_tracks
    from livereact.sim.drop_class import drop_bars, infer_drop_class

    tracks = labeled_tracks(tdir, usb)
    t11 = time11_by_track(usb, tdir, rig) if time11 else {}
    L = ["# Drop classes to confirm by ear", "",
         "Every drop on your ear-verified tracks, with the class LiveReact gives it. A class you set yourself "
         "always wins: add `\"class\": \"snare_roll\"` (or `full`, `clap`, `atmospheric`) to that drop in "
         "`tracks/<song>.timeline.json`, or tell Claude which ones are wrong.", "",
         "- **snare_roll**: the short escalating roll that leads in (usually 3-7 bars)",
         "- **full**: the groove / bass hit; holds at least 8 bars",
         "- **clap**: driven by claps or crashes, no real roll",
         "- **atmospheric**: brightens and pulses, no strobe", ""]
    counts: dict[str, int] = {}
    inside: dict[str, int] = {}
    for p in tracks:
        tl = ear_timeline_for(p, usb, tdir)
        drops = [m for m in tl.markers if m.kind == "drop"]
        if not drops:
            continue
        L.append(f"## {p.stem}  ({tl.bpm:g} BPM{', holdout' if is_holdout(p) else ''})")
        for m in drops:
            c = infer_drop_class(m, tl.bpm, tl.beats_per_bar)
            counts[c] = counts.get(c, 0) + 1
            bars = drop_bars(m, tl.bpm, tl.beats_per_bar)
            span = f"{mmss(m.t)} -> {mmss(m.end)}" if m.end is not None else mmss(m.t)
            L.append(f"- {span}  {'' if bars is None else f'({bars:.1f} bars)  '}**{c}**  -- {why(m, tl.bpm)}"
                     + (f'  \n  note: "{m.note}"' if m.note else ""))
            if time11:
                end = m.end if m.end is not None else m.t + 8 * tl.bar_dur
                hits = [n for t, n in t11.get(p.name, []) if m.t - tl.bar_dur <= t < end]
                for n in hits:
                    L.append(f"  - TIME-11 inside this drop: {n}")
                inside[c] = inside.get(c, 0) + len(hits)
        L.append("")
    L += ["## Totals", "", "| class | drops |" + (" TIME-11 violations inside |" if time11 else ""),
          "|---|---|" + ("---|" if time11 else "")]
    for c in sorted(counts):
        L.append(f"| {c} | {counts[c]} |" + (f" {inside.get(c, 0)} |" if time11 else ""))
    return L


def main(argv: list[str] | None = None) -> int:
    from livereact.loop.evaluate import resolve_usb
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usb", default=None)
    ap.add_argument("--tracks", default=str(ROOT / "tracks"))
    ap.add_argument("--out", default=str(ROOT / "tracks" / "DROP_CLASSES_TO_REVIEW.md"))
    ap.add_argument("--time11", action="store_true", help="simulate each scored track and place TIME-11 violations")
    ap.add_argument("--rig", default="rigs/josh_rig.json")
    a = ap.parse_args(argv)
    usb = resolve_usb(a.usb)
    lines = build(usb, Path(a.tracks), time11=a.time11, rig=a.rig)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
