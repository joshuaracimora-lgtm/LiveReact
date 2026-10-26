"""Index a copy of the rekordbox USB export and say what the harness can do with it.

    python tools/harness/import_usb.py                     # usb_export/ (or $LIVEREACT_USB)
    python tools/harness/import_usb.py --root /Volumes/MYUSB --summary
    python tools/harness/import_usb.py --verify            # + ffprobe durations and grid-vs-ear downbeat deltas
    python tools/harness/import_usb.py --json > results/usb_index.json

Per track: id, title, audio found, DAT/EXT found, phrase table present, ear-verified
timeline paired (the SCORED set), duration mismatch. Exit 0 when every track has
audio + analysis, 1 when something is missing (listed), 2 when there is no export.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=None, help="USB export copy (default: usb_export/ or $LIVEREACT_USB)")
    p.add_argument("--tracks", default="tracks", help="where Josh's ear timelines + look notes live")
    p.add_argument("--verify", action="store_true", help="ffprobe every audio file; compare durations and the ear downbeat")
    p.add_argument("--summary", action="store_true", help="counts only")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    from livereact.loop.evaluate import is_ear_verified
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for
    from livereact.rekordbox.usb_export import UsbExport, audio_duration_s, default_usb_root
    from livereact.sim.timeline import Timeline

    root = Path(a.root) if a.root else default_usb_root()
    if root is None or not (root / "PIONEER" / "rekordbox" / "export.pdb").exists():
        print(f"no rekordbox export found ({root or 'usb_export/ and $LIVEREACT_USB'}): copy the stick to usb_export/ first")
        return 2
    usb = UsbExport(root)
    tracks = usb.tracks()
    rows = []
    total_bytes = 0
    for t in tracks:
        table, grid, wave = (None, None, None)
        if t.ext is not None or t.dat is not None:
            table, grid, wave = usb.load_analysis(t)
        ear = usb.ear_timeline(t, a.tracks, check_duration=a.verify)
        verified = bool(ear) and is_ear_verified(Timeline.load(ear))
        look = None
        try:
            look = load_look_notes(look_notes_path_for(t.stem, a.tracks))
        except ValueError as exc:
            look = f"INVALID: {exc}"
        row = {"id": t.info.id, "title": t.info.title, "file": t.name, "audio": t.audio is not None,
               "dat": t.dat is not None, "ext": t.ext is not None, "pssi": table is not None,
               "phrases": len(table.phrases) if table else 0, "grid_beats": len(grid) if grid else 0,
               "waveform": wave is not None, "bpm": t.info.bpm, "duration_s": t.info.duration_s,
               "ear_timeline": ear.name if ear else None, "scored": verified,
               "look_notes": ("invalid" if isinstance(look, str) else bool(look))}
        if t.audio is not None:
            try:
                total_bytes += t.audio.stat().st_size
            except OSError:
                pass
        if a.verify and t.audio is not None:
            dur = audio_duration_s(t.audio)
            row["probe_duration_s"] = round(dur, 2) if dur is not None else None
            row["duration_mismatch"] = dur is not None and abs(dur - t.info.duration_s) > 2.0
            if ear and grid:
                tl = Timeline.load(ear)
                first_down = next((g[2] / 1000.0 for g in grid if g[0] == 1), None)
                if first_down is not None:
                    beat = 60.0 / tl.bpm
                    delta = (first_down - tl.first_downbeat_s) % beat
                    delta = min(delta, beat - delta)
                    row["grid_vs_ear_downbeat_ms"] = round(delta * 1000)
        rows.append(row)

    n = len(rows)
    counts = {"tracks": n, "audio": sum(r["audio"] for r in rows), "analysis": sum(r["dat"] and r["ext"] for r in rows),
              "pssi": sum(r["pssi"] for r in rows), "scored": sum(r["scored"] for r in rows),
              "look_notes": sum(1 for r in rows if r["look_notes"] is True),
              "invalid_look_notes": sum(1 for r in rows if r["look_notes"] == "invalid"),
              "audio_gb": round(total_bytes / 1e9, 2)}
    if a.json:
        print(json.dumps({"root": str(root), "counts": counts, "tracks": rows}, indent=1, ensure_ascii=False))
    else:
        print(f"rekordbox export at {root}")
        print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
        if not a.summary:
            print(f"  {'id':>4} {'aud':3} {'anlz':4} {'pssi':4} {'score':5} {'look':4}  file")
            for r in rows:
                flags = f"{r['id']:>4} {'y' if r['audio'] else '-':3} {'y' if r['dat'] and r['ext'] else '-':4} " \
                        f"{r['phrases'] if r['pssi'] else '-':>4} {'EAR' if r['scored'] else ('tl' if r['ear_timeline'] else '-'):5} " \
                        f"{'y' if r['look_notes'] is True else ('!!' if r['look_notes'] == 'invalid' else '-'):4}"
                extra = ""
                if r.get("duration_mismatch"):
                    extra += f"  DURATION {r['probe_duration_s']}s vs pdb {r['duration_s']}s"
                if r.get("grid_vs_ear_downbeat_ms", 0) > 60:
                    extra += f"  GRID/EAR downbeat off by {r['grid_vs_ear_downbeat_ms']} ms"
                print(f"  {flags}  {r['file'][:60]}{extra}")
        missing = [r["file"] for r in rows if not (r["audio"] and r["dat"] and r["ext"])]
        if missing:
            print(f"  MISSING audio or analysis for {len(missing)} tracks: " + ", ".join(m[:40] for m in missing[:8])
                  + (" ..." if len(missing) > 8 else ""))
    return 1 if any(not (r["audio"] and r["dat"] and r["ext"]) for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
