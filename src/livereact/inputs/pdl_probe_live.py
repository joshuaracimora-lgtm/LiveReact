"""Live Pro DJ Link probe: announce, answer hellos, print status, fetch phrase tables.

Run via ``python -m livereact --pdl-probe`` or ``tools/pdl_probe.py --live``.
This is the Gate-1 hardware check for the rekordbox-phrase plan: it proves (or
disproves) that the XDJ-XZ unicasts CDJ status packets once we announce as a
rekordbox-class peer, and that the loaded track's phrase table can be pulled
off the USB over NFS and lined up with the live beat counter.

It runs the real ``ProDjLinkListener`` and the real ``PhraseService`` (same
sockets, same parser, same fetch pipeline and planner as the app) but no
metronome, no engines, no DMX. ``--dump`` saves the raw status packets and the
fetched export.pdb / ANLZ files for fixtures.
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
from ..core.messages import BeatMessage, DeckStatusMessage, TrackPhraseMessage
from ..network import link_local_routes
from ..rekordbox import mapping
from ..rekordbox.pssi import PhraseTable, format_table
from .prodj import ProDjLinkError, ProDjLinkListener

log = logging.getLogger(__name__)

NO_STATUS_CHECKLIST_AFTER_S = 15.0
MAX_DUMPED_PACKETS = 600


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


def _dump_sink(dump_dir: str):
    root = Path(dump_dir)

    def sink(kind: str, ip: str, slot: str, track_id: int, data: bytes) -> None:
        if kind == "pdb":
            path = root / "pdb" / f"{ip}-{slot}.pdb"
        else:
            path = root / "anlz" / f"{track_id}.{kind.upper()}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"[dump] {path} ({len(data)} bytes)")

    return sink


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

    service = None
    if fetch:
        from ..rekordbox.service import PhraseService

        service = PhraseService(q, clock, stop_event=stop, cache_dir=(Path(dump_dir) / "pdb") if dump_dir else None,
                                raw_sink=_dump_sink(dump_dir) if dump_dir else None)
    tables: dict[tuple[str, int], PhraseTable | None] = {}
    titles: dict[tuple[str, int], str] = {}
    last_line: dict[int, str] = {}
    last_print: dict[int, float] = {}
    lock = threading.Lock()

    def on_status(msg: DeckStatusMessage, raw: bytes) -> None:
        nonlocal dumped
        if dump_fh is not None and dumped < MAX_DUMPED_PACKETS:
            dump_fh.write(json.dumps({"t": round(time.time(), 3), "ip": msg.source_ip, "player": msg.player,
                                      "hex": raw.hex()}) + "\n")
            dumped += 1
        key = (msg.slot, msg.track_id)
        line = _fmt_status(msg, titles.get(key, ""), tables.get(key))
        now = clock.monotonic()
        with lock:
            changed = line.split("beat_count=")[0] != last_line.get(msg.player, "").split("beat_count=")[0]
            if changed or now - last_print.get(msg.player, 0.0) >= 1.0:
                print(f"{time.strftime('%H:%M:%S')} {line}")
                last_line[msg.player] = line
                last_print[msg.player] = now

    listener = ProDjLinkListener(q, clock, stop, interface=interface, announce=announce, status_callback=on_status,
                                 phrase_service=service)
    try:
        listener.start()
    except ProDjLinkError as exc:
        print(f"cannot start: {exc}")
        return 3
    except OSError as exc:
        print(f"cannot bind Pro DJ Link ports: {exc} -- is LiveReact (or another probe) already running? "
              f"Only one process can own UDP 50000-50002 (lsof -nP -iUDP:50000-50002).")
        return 3
    if service is not None:
        try:
            service.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch] phrase service unavailable ({exc}); continuing without --fetch")
            service = None

    ident = listener.identity
    print("=== Pro DJ Link live probe ===")
    print(f"announce: {'ON (rekordbox-class, player 17)' if announce and listener._announcer else 'OFF (passive)'}"
          + ("" if announce and listener._announcer or not announce else "  <- announce FAILED, see log"))
    if ident:
        print(f"identity: {ident.name} {ident.ip} mac={ident.mac} broadcast={ident.broadcast} ifindex={ident.ifindex} "
              f"candidates={ident.candidates}{' (explicit)' if ident.explicit else ''}")
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
            except queue.Empty:
                msg = None
            if isinstance(msg, BeatMessage):
                beats += 1
            elif isinstance(msg, TrackPhraseMessage):
                key = (msg.slot, msg.track_id)
                tables[key] = msg.table
                titles[key] = msg.title
                if msg.table is None:
                    print(f"[fetch] track {msg.track_id} {msg.title!r} (slot {msg.slot}): no phrase table -- {msg.error}")
                else:
                    print(f"[fetch] track {msg.track_id} {msg.title!r} (slot {msg.slot}) bpm={msg.bpm}")
                    print(format_table(msg.table, bpm=msg.bpm or None))
                    print("        sections:", " > ".join(f"{s[2]}={s[3]}" for s in mapping.sections_of(msg.table)))
                    if msg.plan is not None:
                        print("        " + msg.plan.summary())
            now = clock.monotonic()
            if (not checklist_printed and announce and listener.status_count == 0
                    and now - t0 > NO_STATUS_CHECKLIST_AFTER_S):
                checklist_printed = True
                print(f"--- no status packets after {NO_STATUS_CHECKLIST_AFTER_S:.0f}s "
                      f"(beats seen: {beats}, hellos: {listener.hello_count}, peers: "
                      f"{sorted(listener._announcer.peers) if listener._announcer else '-'}). Checklist:")
                print("    1. two 169.254 routes above?  sudo route delete 169.254.0.0/16 -ifscope en0")
                print("    2. this Mac's NIC must hold a 169.254.x.x address on the cable to the XZ (--pdl-interface <nic>)")
                print("    3. macOS firewall: allow incoming UDP 50000-50002 for python")
                print("    4. XZ: LINK enabled, a track loaded; keepalives from it = 'peer seen' in the log")
                print("    5. no hellos answered -> the XZ never trusted us: compare with --pdl-no-announce")
            if seconds is not None and now - t0 >= seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        announcer = listener._announcer   # stop() drops it; keep the stats for the summary
        listener.stop()
        if service is not None:
            service.stop()
        if dump_fh is not None:
            dump_fh.close()

    st = announcer.stats if announcer is not None else {}
    elapsed = clock.monotonic() - t0
    print("=== summary ===")
    print(f"ran {elapsed:.0f}s  beats={beats}  status_packets={listener.status_count}  hellos={listener.hello_count}"
          f"  unparsed_status={listener.status_parse_errors}")
    if st:
        print(f"keepalives_sent={st['keepalives_sent']} status_sent={st['status_sent']} "
              f"hellos_answered={st['hellos_answered']} send_errors={st['errors']} last_error={st['last_error'] or '-'}")
    if listener.first_status_at is not None:
        print(f"time to first status: {listener.first_status_at - t0:.1f}s")
    for p, m in sorted(listener.last_status.items()):
        print(f"last P{p}: track={m.track_id} slot={m.slot} {m.play_state} beat_count={m.beat_count}")
    if service is not None:
        print(f"phrase service: {service.stats}")
    if dump_dir:
        print(f"dump: {dump_dir} ({dumped} status packets)")
    return 0 if listener.status_count or not announce else 1
