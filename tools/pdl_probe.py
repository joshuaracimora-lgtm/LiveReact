"""Probe the XDJ-XZ's Pro DJ Link status + rekordbox phrase data (offline and live).

WHY (2026-10-25): LiveReact only ever read *beat* packets from the XDJ-XZ and
recorded "this unit never sends status packets" as a hardware fact. It is not:
CDJ status packets (track ID, play state, absolute beat counter) are unicast
only to peers that announce themselves, and our announce failed that day. This
tool is the empirical check -- it announces as rekordbox the way the XZ expects,
answers its handshake, prints every status packet, and (with --fetch) pulls the
loaded track's rekordbox phrase table off the USB over NFS and shows a live
"-> chorus in 12 beats" countdown. No engine code runs; nothing is written to
the deck.

OFFLINE (no hardware; works today on this Mac):
  python tools/pdl_probe.py --ext ~/Library/Pioneer/rekordbox/share/PIONEER/USBANLZ/<x>/<y>/ANLZ0000.EXT
  python tools/pdl_probe.py --ext FILE.EXT --dat FILE.DAT          # adds seconds via the beat grid
  python tools/pdl_probe.py --pdb databases/player-1-sd.pdb --track-id 4
  python tools/pdl_probe.py --corpus ~/Library/Pioneer/rekordbox/share/PIONEER/USBANLZ   # parse every .EXT, report

LIVE (Stage 1; needs the XDJ on the USB-Ethernet NIC with a 169.254.x.x address):
  python tools/pdl_probe.py --live [--interface en5] [--no-announce] [--seconds 120] [--fetch] [--dump DIR]

Exit codes: 0 ok, 2 parse/lookup failed, 3 live mode unavailable.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from livereact.rekordbox import mapping  # noqa: E402
from livereact.rekordbox.pssi import format_table, parse_pqtz, parse_pssi  # noqa: E402


def _print_table(ext_path: str, dat_path: str | None) -> int:
    data = Path(ext_path).read_bytes()
    table = parse_pssi(data)
    if table is None:
        print(f"{ext_path}: no PSSI phrase table (track not phrase-analyzed, or unknown layout)")
        return 2
    grid = None
    if dat_path:
        grid = parse_pqtz(Path(dat_path).read_bytes())
        if grid is None:
            print(f"{dat_path}: no PQTZ beat grid found")
    print(f"{ext_path}")
    print(format_table(table, grid=grid))
    print("sections:", " > ".join(f"{s[2]}={s[3]}" for s in mapping.sections_of(table)))
    return 0


def _corpus(root: str, limit: int | None) -> int:
    files = sorted(Path(os.path.expanduser(root)).rglob("*.EXT"))
    if limit:
        files = files[:limit]
    n_pssi = 0
    moods: dict[str, int] = {}
    bad: list[str] = []
    monotonic_fail: list[str] = []
    labels: dict[str, int] = {}
    for f in files:
        data = f.read_bytes()
        if b"PSSI" not in data:
            continue
        table = parse_pssi(data)
        if table is None:
            bad.append(str(f))
            continue
        n_pssi += 1
        moods[table.mood_name] = moods.get(table.mood_name, 0) + 1
        beats = [p.beat for p in table.phrases]
        if beats != sorted(beats) or (beats and beats[-1] > table.end_beat):
            monotonic_fail.append(str(f))
        for p in table.phrases:
            labels[f"{table.mood_name}:{p.label}"] = labels.get(f"{table.mood_name}:{p.label}", 0) + 1
    print(f"EXT files: {len(files)}  with PSSI tag: {n_pssi + len(bad)}  parsed: {n_pssi}  failed: {len(bad)}")
    print(f"moods: {moods}")
    print(f"non-monotonic / past-end tables: {len(monotonic_fail)}")
    for k in sorted(labels):
        print(f"  {k:16s} {labels[k]}")
    for f in bad[:10]:
        print("  FAILED:", f)
    for f in monotonic_fail[:10]:
        print("  ORDER:", f)
    return 0 if not bad else 2


def _pdb_lookup(pdb_path: str, track_id: int | None) -> int:
    from livereact.rekordbox.library import RekordboxLibrary, RekordboxLibraryError

    lib = RekordboxLibrary()
    try:
        n = lib.load_pdb_file(pdb_path)
    except RekordboxLibraryError as exc:
        print(exc)
        return 2
    print(f"{pdb_path}: {n} tracks")
    if track_id is None:
        for t in lib.tracks()[:25]:
            print(f"  {t.id:5d}  {t.bpm:6.2f}  {t.duration_s:4d}s  {t.artist} - {t.title}")
        if n > 25:
            print(f"  ... ({n - 25} more; use --track-id N)")
        return 0
    t = lib.track(track_id)
    if t is None:
        print(f"track id {track_id} not in this database")
        return 2
    print(f"  id={t.id} title={t.title!r} artist={t.artist!r} bpm={t.bpm} duration={t.duration_s}s")
    print(f"  analyze_path={t.analyze_path}")
    print(f"  ext_path    ={t.ext_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ext", help="offline: parse this ANLZ .EXT and print its phrase table")
    p.add_argument("--dat", help="offline: the matching .DAT (beat grid -> seconds)")
    p.add_argument("--pdb", help="offline: index this export.pdb")
    p.add_argument("--track-id", type=int, default=None, help="with --pdb: resolve one rekordbox track id")
    p.add_argument("--corpus", help="offline: parse every .EXT under this directory and report")
    p.add_argument("--limit", type=int, default=None, help="with --corpus: only the first N files")
    p.add_argument("--live", action="store_true", help="listen to the XDJ-XZ on the wire (Stage 1)")
    p.add_argument("--interface", default=None, help="live: NIC name or IP to pin to (default: the 169.254 one)")
    p.add_argument("--no-announce", action="store_true", help="live: passive only (A/B against announcing)")
    p.add_argument("--seconds", type=float, default=None, help="live: stop after N seconds")
    p.add_argument("--fetch", action="store_true", help="live: pull export.pdb + the loaded track's ANLZ over NFS")
    p.add_argument("--dump", default=None, help="live: save raw status packets + fetched files under DIR")
    args = p.parse_args(argv)

    if args.ext:
        return _print_table(args.ext, args.dat)
    if args.corpus:
        return _corpus(args.corpus, args.limit)
    if args.pdb:
        return _pdb_lookup(args.pdb, args.track_id)
    if args.live:
        try:
            from livereact.inputs.pdl_probe_live import run_live  # Stage 1
        except ImportError:
            print("live mode is not built yet (Stage 1); offline modes: --ext / --pdb / --corpus")
            return 3
        return run_live(interface=args.interface, announce=not args.no_announce, seconds=args.seconds,
                        fetch=args.fetch, dump_dir=args.dump)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
