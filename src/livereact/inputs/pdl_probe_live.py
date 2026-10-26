"""Live Pro DJ Link probe: announce, answer hellos, print status, fetch phrase tables.

Run via ``python -m livereact --pdl-probe`` or ``tools/pdl_probe.py --live``.
This is the Gate-1 hardware check for the rekordbox-phrase plan: it proves (or
disproves) that the XDJ-XZ unicasts CDJ status packets once we announce as a
rekordbox-class peer, and that the loaded track's phrase table can be pulled
off the USB over NFS and lined up with the live beat counter.

It runs the real ``ProDjLinkListener`` (same sockets, same parser as the app) but
no metronome, no engines, no DMX.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from ..clock import RealClock
from ..core.messages import BeatMessage, DeckStatusMessage
from ..network import link_local_routes
from ..rekordbox import mapping
from ..rekordbox.library import RekordboxLibrary, RekordboxLibraryError
from ..rekordbox.pssi import PhraseTable, format_table, parse_pqtz, parse_pssi
from .prodj import ProDjLinkError, ProDjLinkListener

log = logging.getLogger(__name__)

NO_STATUS_CHECKLIST_AFTER_S = 15.0
MAX_DUMPED_PACKETS = 600


class _Fetcher(threading.Thread):
    """Pulls export.pdb once per (ip, slot) and the .EXT/.DAT per track, off the main thread."""

    def __init__(self, dump_dir: str | None) -> None:
        super().__init__(name="pdl-probe-fetch", daemon=True)
        from ..rekordbox.fetch import XdjNfs

        self.nfs = XdjNfs()
        self.lib = RekordboxLibrary(cache_dir=Path(dump_dir) / "pdb" if dump_dir else None)
        self.dump_dir = Path(dump_dir) if dump_dir else None
        self.requests: queue.Queue = queue.Queue()
        self.loaded_pdb: set[tuple[str, str]] = set()
        self.tables: dict[int, PhraseTable | None] = {}
        self.grids: dict[int, list | None] = {}
        self.titles: dict[int, str] = {}
        self._stop = threading.Event()

    def start(self) -> None:
        self.nfs.start()
        super().start()

    def stop(self) -> None:
        self._stop.set()
        self.requests.put(None)
        self.join(timeout=2.0)
        self.nfs.stop()

    def request(self, ip: str, slot: str, track_id: int) -> None:
        self.requests.put((ip, slot, track_id))

    def run(self) -> None:
        while not self._stop.is_set():
            item = self.requests.get()
            if item is None:
                break
            ip, slot, track_id = item
            try:
                self._fetch_one(ip, slot, track_id)
            except Exception:  # noqa: BLE001
                log.exception("probe fetch failed for %s:%s track %d", ip, slot, track_id)

    def _fetch_one(self, ip: str, slot: str, track_id: int) -> None:
        if track_id in self.tables:
            return
        key = (ip, slot)
        if key not in self.loaded_pdb:
            t0 = time.monotonic()
            data = self.nfs.fetch_pdb(ip, slot)
            if data is None:
                print(f"[fetch] export.pdb from {ip} slot={slot}: FAILED (see log)")
                self.tables[track_id] = None
                return
            try:
                n = self.lib.load_pdb_bytes(data, f"{ip}-{slot}")
            except RekordboxLibraryError as exc:
                print(f"[fetch] export.pdb parse failed: {exc}")
                self.tables[track_id] = None
                return
            print(f"[fetch] export.pdb from {ip} slot={slot}: {len(data)} bytes, {n} tracks, {time.monotonic() - t0:.1f}s")
            self.loaded_pdb.add(key)
        track = self.lib.track(track_id)
        if track is None:
            print(f"[fetch] track id {track_id} not in export.pdb ({ip} {slot})")
            self.tables[track_id] = None
            return
        self.titles[track_id] = f"{track.artist} - {track.title}" if track.artist else track.title
        ext = self.nfs.fetch(ip, slot, track.ext_path)
        dat = self.nfs.fetch(ip, slot, track.analyze_path)
        if self.dump_dir:
            d = self.dump_dir / "anlz"
            d.mkdir(parents=True, exist_ok=True)
            if ext:
                (d / f"{track_id}.EXT").write_bytes(ext)
            if dat:
                (d / f"{track_id}.DAT").write_bytes(dat)
        table = parse_pssi(ext) if ext else None
        grid = parse_pqtz(dat) if dat else None
        self.tables[track_id] = table
        self.grids[track_id] = grid
        if table is None:
            print(f"[fetch] track {track_id} {self.titles[track_id]!r}: no phrase table "
                  f"({'EXT fetched' if ext else 'EXT fetch failed'}; phrase analysis off for this track?)")
            return
        print(f"[fetch] track {track_id} {self.titles[track_id]!r} bpm={track.bpm} duration={track.duration_s}s "
              f"grid={len(grid) if grid else 0} beats")
        print(format_table(table, grid=grid, bpm=track.bpm))
        print("        sections:", " > ".join(f"{s[2]}={s[3]}" for s in mapping.sections_of(table)))


def _fmt_status(msg: DeckStatusMessage, title: str, table: PhraseTable | None) -> str:
    pitch_pct = (msg.pitch - 1.0) * 100.0
    flags = f"{'ONAIR ' if msg.on_air else ''}{'MASTER ' if msg.master else ''}{'sync ' if msg.sync else ''}".strip()
    bc = "-" if msg.beat_count is None else str(msg.beat_count)
    line = (f"P{msg.player} {msg.slot:3s} track={msg.track_id:<5d} {msg.play_state:8s} {flags:12s} "
            f"bpm={msg.bpm:6.2f} pitch={pitch_pct:+5.2f}% beat_count={bc:>6s} beat={msg.beat_in_bar}")
    if title:
        line += f"  {title[:40]!r}"
    if table is not None and msg.beat_count is not None:
        cur, nxt = table.at_beat(msg.beat_count)
        n = table.beats_to_next(msg.beat_count)
        cur_s = f"{cur.label}({mapping.section_for(table.mood, cur.label)})" if cur else "pre-intro"
        nxt_s = f"{nxt.label}({mapping.section_for(table.mood, nxt.label)})" if nxt else "end"
        line += f"  | {cur_s} -> {nxt_s} in {n} beats"
    return line


def run_live(interface: str | None = None, announce: bool = True, seconds: float | None = None,
             fetch: bool = False, dump_dir: str | None = None) -> int:
    clock = RealClock()
    stop = threading.Event()
    q: queue.Queue = queue.Queue()
    dump_fh = None
    dumped = 0
    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        dump_fh = open(os.path.join(dump_dir, "status_packets.jsonl"), "a", encoding="utf-8")

    fetcher = _Fetcher(dump_dir) if fetch else None
    last_line: dict[int, str] = {}
    last_print: dict[int, float] = {}
    seen_tracks: set[tuple] = set()
    lock = threading.Lock()

    def on_status(msg: DeckStatusMessage, raw: bytes) -> None:
        nonlocal dumped
        if dump_fh is not None and dumped < MAX_DUMPED_PACKETS:
            dump_fh.write(json.dumps({"t": round(time.time(), 3), "ip": msg.source_ip, "player": msg.player,
                                      "hex": raw.hex()}) + "\n")
            dumped += 1
        key = (msg.source_ip, msg.slot, msg.track_id)
        if fetcher is not None and msg.track_id and msg.analyze_type == "rekordbox" and key not in seen_tracks:
            seen_tracks.add(key)
            fetcher.request(msg.source_ip, msg.slot, msg.track_id)
        table = fetcher.tables.get(msg.track_id) if fetcher else None
        title = fetcher.titles.get(msg.track_id, "") if fetcher else ""
        line = _fmt_status(msg, title, table)
        now = clock.monotonic()
        with lock:
            changed = line.split("beat_count=")[0] != last_line.get(msg.player, "").split("beat_count=")[0]
            if changed or now - last_print.get(msg.player, 0.0) >= 1.0:
                print(f"{time.strftime('%H:%M:%S')} {line}")
                last_line[msg.player] = line
                last_print[msg.player] = now

    listener = ProDjLinkListener(q, clock, stop, interface=interface, announce=announce, status_callback=on_status)
    try:
        listener.start()
    except ProDjLinkError as exc:
        print(f"cannot start: {exc}")
        return 3
    except OSError as exc:
        print(f"cannot bind Pro DJ Link ports: {exc} (another LiveReact/probe running? lsof -nP -iUDP:50000-50002)")
        return 3
    if fetcher is not None:
        try:
            fetcher.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch] NFS client unavailable ({exc}); continuing without --fetch")
            fetcher = None

    ident = listener.identity
    print("=== Pro DJ Link live probe ===")
    print(f"announce: {'ON (rekordbox-class, player 17)' if announce and listener._announcer else 'OFF (passive)'}"
          + ("" if announce and listener._announcer or not announce else "  <- announce FAILED, see log"))
    if ident:
        print(f"identity: {ident.name} {ident.ip} mac={ident.mac} broadcast={ident.broadcast} ifindex={ident.ifindex}")
    routes = link_local_routes()
    print(f"169.254/16 routes: {routes if routes else 'none'}"
          + ("   <- more than one: run  sudo route delete 169.254.0.0/16 -ifscope en0" if len(routes) > 1 else ""))
    print("waiting for the XDJ-XZ ... (Ctrl-C to stop)")

    t0 = clock.monotonic()
    beats = 0
    checklist_printed = False
    try:
        while not stop.is_set():
            try:
                msg = q.get(timeout=0.25)
                if isinstance(msg, BeatMessage):
                    beats += 1
            except queue.Empty:
                pass
            now = clock.monotonic()
            if (not checklist_printed and announce and listener.status_count == 0
                    and now - t0 > NO_STATUS_CHECKLIST_AFTER_S):
                checklist_printed = True
                print(f"--- no status packets after {NO_STATUS_CHECKLIST_AFTER_S:.0f}s "
                      f"(beats seen: {beats}, hellos: {listener.hello_count}, peers: "
                      f"{sorted(listener._announcer.peers) if listener._announcer else '-'}). Checklist:")
                print("    1. two 169.254 routes above?  sudo route delete 169.254.0.0/16 -ifscope en0")
                print("    2. this Mac's NIC must hold a 169.254.x.x address on the cable to the XZ (--interface <nic>)")
                print("    3. macOS firewall: allow incoming UDP 50000-50002 for python")
                print("    4. XZ: LINK enabled, a track loaded; keepalives from it = 'peer seen' in the log")
                print("    5. no hellos answered -> the XZ never trusted us: compare with --no-announce")
            if seconds is not None and now - t0 >= seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        announcer = listener._announcer   # stop() drops it; keep the stats for the summary
        listener.stop()
        if fetcher is not None:
            fetcher.stop()
        if dump_fh is not None:
            dump_fh.close()

    st = announcer.stats if announcer is not None else {}
    elapsed = clock.monotonic() - t0
    print("=== summary ===")
    print(f"ran {elapsed:.0f}s  beats={beats}  status_packets={listener.status_count}  hellos={listener.hello_count}")
    if st:
        print(f"keepalives_sent={st['keepalives_sent']} status_sent={st['status_sent']} "
              f"hellos_answered={st['hellos_answered']} send_errors={st['errors']} last_error={st['last_error'] or '-'}")
    if listener.first_status_at is not None:
        print(f"time to first status: {listener.first_status_at - t0:.1f}s")
    for p, m in sorted(listener.last_status.items()):
        print(f"last P{p}: track={m.track_id} slot={m.slot} {m.play_state} beat_count={m.beat_count}")
    if dump_dir:
        print(f"dump: {dump_dir} ({dumped} status packets)")
    return 0 if listener.status_count or not announce else 1
