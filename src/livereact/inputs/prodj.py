"""Pro DJ Link input from the XDJ-XZ — direct raw-socket listener.

VERIFIED ON HARDWARE (real XDJ-XZ, 2026-08-31): this does **not** use
`python-prodj-link`'s own runtime (`core.prodj.ProDj` / `core.clientlist.ClientList`
/ `core.vcdj.Vcdj`) — only its packet-format definitions (`prodj.network.packets`),
which parse real captured bytes from this unit correctly.

Why: the library's `ClientList` keys its client table by **IP address**. The
XDJ-XZ is an all-in-one box that broadcasts as *three* distinct devices — Deck 1,
Deck 2, and its internal mixer — each with its own MAC address, but all sharing
one physical IP (one Ethernet port). The library folds all three into a single
client record, and each new keepalive stomps that record's `player_number` with
whichever sub-device just spoke (confirmed by raw capture: player_number cycling
1 -> 2 -> 33 every ~2s, `33` + `is_mixer` flag being the unit's internal mixer).
So when a beat packet arrives carrying its own real player_number, the library's
`getClient(player_number)` lookup misses against the stomped record and the
packet is silently dropped — even though it's genuinely on the wire.

STATUS PACKETS (port 50002) — corrected 2026-10-25. The 2026-08-31 capture saw
only keepalive (50000) and beat (50001) traffic and this module recorded "this
unit never broadcasts status packets" as a hardware fact. That inference was
wrong: CDJ status packets are never broadcast — a player *unicasts* them only to
peers it has seen announce themselves on port 50000 (Deep Symmetry's protocol
analysis), and our own announce had failed outbound that day ("No route to
host": two 169.254/16 routes on the Mac). Status packets carry the loaded
track's rekordbox id, slot, play state, on-air/master bits and an absolute beat
counter — the key to real song-structure awareness (rekordbox phrase tables on
the USB, `rekordbox/`). `pdl_announce.py` now announces as a rekordbox-class peer
and answers the XZ's hello handshake; parsed status arrives here as
`DeckStatusMessage`. Until verified live, the passive beat path below is still
the only thing anything downstream depends on, and announce failure degrades to
exactly the pre-2026-10-25 behaviour.

Single-device assumption for the *beat* path is unchanged: whichever player is
currently sending beat packets is treated as the (de facto) master; a stopped
deck simply stops sending beat packets, so "is a beat stream live" doubles as
"is playing" for this rig.

`python-prodj-link` is git-only (not on PyPI) — install it into the venv before
using hardware mode:
    pip install "git+https://github.com/flesniak/python-prodj-link@<pinned-sha>"
(vendoring / pinning a SHA is recommended — docs/06 §4, docs/10 D5. The copy in
this Mac's .venv has no dist-info; treat it as the reference copy.)
"""

from __future__ import annotations

import logging
import select
import socket
import threading
from typing import Callable

from ..clock import Clock
from ..core.messages import BeatMessage, DeckStatusMessage, StatusMessage
from .base import InputSource

log = logging.getLogger(__name__)

NO_DEVICE_WARN_AFTER = 5.0  # seconds
KEEPALIVE_PORT = 50000
BEAT_PORT = 50001
STATUS_PORT = 50002
NO_TRACK_BEAT_COUNT = 0xFFFFFFFF  # beat_count when no rekordbox-analyzed track is loaded

StatusCallback = Callable[[DeckStatusMessage, bytes], None]


class ProDjLinkError(RuntimeError):
    pass


class ProDjLinkListener(InputSource):
    def __init__(
        self,
        output_queue,
        clock: Clock,
        stop_event: threading.Event,
        interface: str | None = None,
        *,
        announce: bool = True,
        status_callback: StatusCallback | None = None,
        emit_status: bool = False,
        phrase_service=None,
    ) -> None:
        self.queue = output_queue
        self.clock = clock
        self.stop_event = stop_event
        self.interface = interface          # NIC name or IP to announce on (default: the 169.254 one)
        self.announce = announce
        self.status_callback = status_callback
        self.emit_status = emit_status
        self.phrase_service = phrase_service  # rekordbox/service.py: asked for a phrase table + plan on track change
        self._packets = None
        self._socks: list[socket.socket] = []
        self._thread: threading.Thread | None = None
        self._last_beat_in_bar: int | None = None
        self._seen_device = False
        self._started_at = 0.0
        self._announcer = None
        self.identity = None
        self.last_status: dict[int, DeckStatusMessage] = {}   # per player number
        self.status_count = 0
        self.hello_count = 0
        self.first_status_at: float | None = None
        self._last_track_key: dict[int, tuple] = {}
        self._prefetched: set[tuple[str, str]] = set()   # (ip, slot) whose export.pdb we asked for
        self.status_parse_errors = 0
        self._last_parse_error = ""

    @property
    def name(self) -> str:
        return "prodj-link"

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._packets = self._import_packets()
        self._socks = [self._bind(p) for p in (KEEPALIVE_PORT, BEAT_PORT, STATUS_PORT)]
        self._started_at = self.clock.monotonic()
        if self.announce:
            self._start_announcer()
        self._thread = threading.Thread(target=self._run, name="prodj-link", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        if self._announcer is not None:
            try:
                self._announcer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._announcer = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        self._socks = []

    @staticmethod
    def _bind(port: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Deliberately NO SO_REUSEPORT: on macOS it load-balances unicast UDP between the
        # sharing sockets, so a probe running beside the app would steal half the status
        # packets from both. A second binder fails loudly (EADDRINUSE) instead.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("0.0.0.0", port))
        return s

    def _start_announcer(self) -> None:
        """Announce as a rekordbox-class peer so the XZ unicasts status to us.
        Any failure here logs a warning and leaves the passive beat path untouched."""
        try:
            from .pdl_announce import RekordboxAnnouncer, interface_identity, pin_to_interface

            self.identity = interface_identity(self.interface)
            # IP_BOUND_IF also filters INBOUND datagrams on macOS: pinning the listening
            # sockets to the wrong NIC would silently kill the beat path the show runs on.
            # Pin only when the choice is unambiguous (named by the operator, or exactly one
            # link-local NIC); otherwise rely on routing and say so.
            safe_to_pin = self.identity.explicit or (self.identity.link_local and self.identity.candidates == 1)
            pinned = [pin_to_interface(s, self.identity.ifindex) for s in self._socks] if safe_to_pin else []
            if not safe_to_pin:
                log.warning("Pro DJ Link: %d candidate NICs (%s chosen); not pinning sockets -- pass "
                            "--pdl-interface <nic> to pin, and check --net-check for a second 169.254 route.",
                            self.identity.candidates, self.identity.name)
            self._announcer = RekordboxAnnouncer(
                self._packets, self._socks[0], self._socks[2], self.identity, self.clock, self.stop_event)
            self._announcer.start()
            log.info("Pro DJ Link announcing as rekordbox (player %d) on %s %s mac=%s bcast=%s pinned=%s",
                     self._announcer.player_number, self.identity.name, self.identity.ip,
                     self.identity.mac, self.identity.broadcast, any(pinned))
            if not self.identity.link_local:
                log.warning("Announcing from a non-link-local address (%s); the XDJ-XZ lives on 169.254.x.x — "
                            "status packets are unlikely to arrive (see HARDWARE.md §3).", self.identity.ip)
        except Exception as exc:  # noqa: BLE001 - never fail hardware start over the announce
            self._announcer = None
            log.warning("Pro DJ Link announce unavailable (%s); listening passively — "
                        "beat packets still work, status packets will not arrive.", exc)

    # ------------------------------------------------------------------ library glue (VERIFY ON HW)
    def _import_packets(self):
        """Import the library's packet-format definitions. Isolated so the (git-only)
        dependency lives here — no runtime state machine from the library is used."""
        try:
            from prodj.network import packets  # type: ignore
        except ImportError as exc:  # pragma: no cover - lib not installed in dev
            raise ProDjLinkError(
                "python-prodj-link is not installed. Install it (git-only) before "
                "using hardware mode; see this module's docstring."
            ) from exc
        return packets

    def _make_prodj(self):
        """Back-compat alias for the (pre-rewrite) library-glue entry point that
        tests/tooling reach for to check the "not installed" error path."""
        return self._import_packets()

    # ------------------------------------------------------------------ receive loop
    def _run(self) -> None:
        warned = False
        while not self.stop_event.is_set():
            try:
                ready, _, _ = select.select(self._socks, [], [], 0.2)
            except OSError:
                break  # sockets closed under us during shutdown
            for s in ready:
                try:
                    data, addr = s.recvfrom(2048)
                except OSError:
                    continue
                self._handle_packet(s, data, addr)
            if (not self._seen_device and not warned
                    and self.clock.monotonic() - self._started_at > NO_DEVICE_WARN_AFTER):
                warned = True
                log.warning(
                    "No DJ-Link device seen yet. Check: Ethernet to the XDJ-XZ, host NIC on "
                    "169.254.x.y/16, Link enabled on the XZ, and the firewall allows UDP "
                    "50000-50002 (see HARDWARE.md)."
                )

    def _handle_packet(self, sock: socket.socket, data: bytes, addr: tuple[str, int] = ("", 0)) -> None:
        port = sock.getsockname()[1]
        try:
            if port == BEAT_PORT:
                self._handle_beat(data)
            elif port == KEEPALIVE_PORT:
                self._seen_device = True
                if self._announcer is not None and addr[0]:
                    self._announcer.note_peer(addr[0])
            elif port == STATUS_PORT:
                self._handle_status(data, addr)
        except Exception:  # noqa: BLE001 - never let the input thread die on one bad packet
            log.exception("Failed to parse Pro DJ Link packet on port %d", port)

    def _handle_beat(self, data: bytes) -> None:
        packet = self._packets.BeatPacket.parse(data)
        if packet.type != "type_beat":
            return
        self._seen_device = True
        now = self.clock.monotonic()
        player = int(packet.player_number)
        # VERIFIED ON HARDWARE: `content.bpm` is the track's fixed analyzed tempo —
        # it does NOT move with the physical tempo slider (confirmed: stayed pinned
        # at 125.00 while the slider sat at +1.55%). `content.pitch` is the slider's
        # live multiplier (1.0 = neutral); the actual live tempo the board displays
        # is bpm * pitch (confirmed: 125.00 * 1.0155 = 126.94, matching the board).
        bpm = float(packet.content.bpm) * float(packet.content.pitch)
        beat = int(packet.content.beat)  # 1-4 within the bar

        if beat and beat != self._last_beat_in_bar:
            self._last_beat_in_bar = beat
            self.queue.put(BeatMessage(now, beat, bpm, is_master=True, player=player))
        # Transport for the engines still comes from the live beat stream itself
        # (a deck that isn't playing doesn't send beat packets at all) — the
        # status-packet path is additive until Stage 2/3 verify it live.
        self.queue.put(StatusMessage(now, on_air=(player,), master_deck=player,
                                     playing=True, loop_active=False))

    # ------------------------------------------------------------------ status (port 50002)
    def _handle_status(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet = self._packets.StatusPacket.parse(data)
        except Exception as exc:  # noqa: BLE001 - construct raises many types; unknown 50002 traffic is normal
            self.status_parse_errors += 1
            text = f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
            if text != self._last_parse_error:
                self._last_parse_error = text
                log.info("Pro DJ Link: unparsed status packet (%d bytes from %s): %s (identical errors suppressed)",
                         len(data), addr[0], text)
            return
        ptype = str(packet.type)
        if ptype == "rekordbox_hello":
            self._seen_device = True
            self.hello_count += 1
            if self._announcer is not None:
                self._announcer.reply_hello(addr)
            return
        if ptype != "cdj":
            return
        self._seen_device = True
        now = self.clock.monotonic()
        c = packet.content
        beat_count = int(c.beat_count)
        state = c.state
        play_state = str(c.play_state)
        msg = DeckStatusMessage(
            timestamp=now,
            player=int(packet.player_number),
            track_id=int(c.track_id),
            loaded_player=int(c.loaded_player_number),
            slot=str(c.loaded_slot),
            analyze_type=str(c.track_analyze_type),
            play_state=play_state,
            playing=play_state in self._packets.PlayStatePlaying,
            on_air=bool(state.on_air),
            master=bool(state.master),
            sync=bool(state.sync),
            bpm=float(c.bpm),
            pitch=float(c.actual_pitch),
            beat_count=None if beat_count == NO_TRACK_BEAT_COUNT else beat_count,
            beat_in_bar=int(c.beat),
            source_ip=addr[0],
            looping=play_state == "looping",
        )
        self.status_count += 1
        if self.first_status_at is None:
            self.first_status_at = now
        self.last_status[msg.player] = msg
        if self._announcer is not None and addr[0]:
            self._announcer.note_peer(addr[0])
        if self.phrase_service is not None and msg.slot in ("sd", "usb") and (msg.source_ip, msg.slot) not in self._prefetched:
            # warm export.pdb for this stick before the first track is asked about (seconds over NFS)
            self._prefetched.add((msg.source_ip, msg.slot))
            try:
                self.phrase_service.prefetch_pdb(msg.source_ip, msg.slot)
            except Exception:  # noqa: BLE001
                log.exception("phrase service prefetch failed")
        key = (msg.source_ip, msg.slot, msg.track_id)
        if self.phrase_service is not None and msg.track_id and key != self._last_track_key.get(msg.player):
            self._last_track_key[msg.player] = key
            try:
                self.phrase_service.request(msg.source_ip, msg.slot, msg.player, msg.track_id)
            except Exception:  # noqa: BLE001
                log.exception("phrase service request failed")
        if self.status_callback is not None:
            self.status_callback(msg, data)
        if self.emit_status:
            self.queue.put(msg)
