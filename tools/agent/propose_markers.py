"""Propose markers (for Josh to confirm by ear) for every USB track without an ear-verified timeline.

    python tools/agent/propose_markers.py [--usb usb_export] [--out tracks/PROPOSED_MARKERS.md] [--sim]
    python tools/agent/propose_markers.py --scaffold-look-notes      # tracks/<stem>.look.json with "_todo": true

For each unlabelled track: the plan's sections as timeline kinds with times, the big moment,
the Up-phrase inner beats as candidate roll starts (rekordbox's chorus boundary is the bass
return; Josh's drop is usually the roll 4-8 bars earlier), and with ``--sim`` the beats where a
drive-mode run actually entered DROP. Every line is tagged ``agent:``. Nothing here writes a
timeline: a proposal becomes a marker only when Josh types it into ``tracks/<stem>.timeline.json``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"




def build(usb, tdir: Path, *, sim: bool = False, rig: str = "rigs/josh_rig.json", limit: int | None = None,
          scaffold: bool = False) -> tuple[list[str], int, int]:
    """The report lines for every unlabelled, complete USB track (+ how many, + scaffolds written)."""
    from livereact.loop.evaluate import is_ear_verified
    from livereact.loop.metrics import drop_entries
    from livereact.rekordbox import mapping
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for, write_template
    from livereact.rekordbox.planner import plan_track
    from livereact.rekordbox.pssi import beat_to_seconds
    from livereact.sim.timeline import Timeline

    tdir = Path(tdir)
    tdir.mkdir(parents=True, exist_ok=True)
    L = ["# Proposed markers (agent) -- confirm by ear, then type them into tracks/<stem>.timeline.json", "",
         "rekordbox's `chorus` boundary is where the bass returns; your drop is usually the roll start 4-8 bars",
         "earlier (measured on the 10 labelled tracks). `roll?` lines are the Up-phrase inner beats rekordbox marks.",
         "Nothing here is verified. Lines are `agent:` proposals.", ""]
    n_tracks = scaffolded = 0
    for t in usb.tracks():
        if limit and n_tracks >= limit:
            break
        ear = usb.ear_timeline(t, tdir, check_duration=False)
        if ear is not None and is_ear_verified(Timeline.load(ear)):
            continue
        if not t.complete:
            continue
        table, grid, wave = usb.load_analysis(t)
        if scaffold:
            lp = look_notes_path_for(t.stem, tdir)
            if not lp.exists():
                write_template(lp, t.stem, todo=True)
                scaffolded += 1
        if table is None:
            L += [f"## {t.name}  (id {t.info.id}, {t.info.bpm} bpm, {t.info.duration_s}s)", "", "- agent: no phrase table on the stick (rekordbox Phrase analysis off for this track)", ""]
            n_tracks += 1
            continue
        look = None
        try:
            look = load_look_notes(look_notes_path_for(t.stem, tdir))
        except ValueError:
            pass
        plan = plan_track(table, track_id=t.info.id, title=t.info.title, bpm=t.info.bpm, grid=grid, waveform=wave, look=look)
        tl = usb.timeline(t, grid)
        T = lambda b: beat_to_seconds(b, grid, bpm=tl.bpm or None, first_downbeat_s=tl.first_downbeat_s)  # noqa: E731
        L += [f"## {t.name}  (id {t.info.id}, {tl.bpm:.2f} bpm, downbeat {tl.first_downbeat_s:.3f}s, mood {table.mood_name})", ""]
        prev_kind = None
        for s in plan.sections:
            kind = mapping.timeline_kind_for_section(s.kind)
            if s.kind == "drop" and prev_kind == "drop":
                kind = "groove"
            tag = "  **big moment**" if s.big_moment else ""
            L.append(f"- agent: {mmss(T(s.beat))}  t={T(s.beat):.2f}  **{kind}**  (rekordbox {s.label}, {s.length // 4} bars){tag}")
            prev_kind = s.kind
        inner = []
        for ph in table.phrases:
            if ph.label == "up":
                for b in (ph.beat2, ph.beat3, ph.beat4):
                    if b and ph.beat < b < ph.end_beat:
                        inner.append(b)
        if inner:
            L.append("- agent: roll? candidates (Up-phrase inner beats): " + ", ".join(f"{mmss(T(b))}" for b in sorted(set(inner))))
        if sim:
            from livereact.sim.harness import default_seed_range, run_sim
            try:
                r = run_sim(t.audio, tl, rig, seed_range=default_seed_range(), phrases=table, drive=True, grid=grid,
                            waveform=wave, track_id=t.info.id, look=look)
                ents = drop_entries(r.signals)
                L.append("- agent: drive-mode DROP entries: " + (", ".join(mmss(e) for e in ents) if ents else "none"))
            except Exception as exc:  # noqa: BLE001
                L.append(f"- agent: sim failed: {exc}")
        L.append("")
        n_tracks += 1
    return L, n_tracks, scaffolded


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--usb", default=None)
    p.add_argument("--tracks", default="tracks")
    p.add_argument("--out", default=None, help="default tracks/PROPOSED_MARKERS.md (gitignored)")
    p.add_argument("--sim", action="store_true", help="also run each track in drive mode and list the DROP entries (slow)")
    p.add_argument("--scaffold-look-notes", action="store_true", help="write a _todo look-notes file per track that has none")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()

    from livereact.loop.evaluate import resolve_usb

    usb = resolve_usb(a.usb)
    if usb is None:
        print("no rekordbox export (usb_export/ or $LIVEREACT_USB)")
        return 2
    tdir = Path(a.tracks)
    out = Path(a.out) if a.out else tdir / "PROPOSED_MARKERS.md"
    L, n_tracks, scaffolded = build(usb, tdir, sim=a.sim, rig=a.rig, limit=a.limit, scaffold=a.scaffold_look_notes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{n_tracks} unlabelled tracks -> {out}" + (f"; {scaffolded} look-notes scaffolds written" if a.scaffold_look_notes else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
