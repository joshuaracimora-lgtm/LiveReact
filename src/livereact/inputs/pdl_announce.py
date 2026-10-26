"""Announce LiveReact on the Pro DJ Link network the way the XDJ-XZ expects.

WHY: CDJ *status* packets (port 50002: loaded track id, play state, absolute
beat counter) are not broadcast. A player unicasts them only to peers it has
seen announce themselves with keepalives on port 50000 (Deep Symmetry's protocol
analysis). LiveReact's 2026-08-31 "the XDJ-XZ never sends status" finding was
made with a failed announce ("No route to host"), so nothing ever arrived.

The recipe below is what houseworthe/Pabst-DJ-Lighting got working on an XDJ-XZ
(firmware 1.24) with this same python-prodj-link packet library -- every point
marked VERIFY is to be confirmed on Josh's unit with ``tools/pdl_probe.py --live``:

* keepalive (type 0x06) every ~1.6 s, ``device_type="rekordbox"``, player 17,
  ``flags`` = is_rekordbox. VERIFY: CDJ-class announces (player 5) are dropped.
* the XZ then sends ``rekordbox_hello`` (status type 0x10) every ~5 s; it MUST be
  answered with ``rekordbox_reply`` (0x11) unicast *from source port 50002* or the
  XZ de-trusts the peer after ~60 s. VERIFY.
* a DJM-class status (0x29) every 200 ms unicast from port 50002 to each peer.
  VERIFY whether the XZ needs this at all.
* outbound sockets pinned to the XDJ's NIC (macOS ``IP_BOUND_IF``) because a Mac
  with Wi-Fi up often holds a second 169.254/16 route on en0.

Everything here is best-effort and gig-safe: a failed send is logged once per
distinct error and the thread keeps going; the passive beat path in
``inputs/prodj.py`` never depends on it.
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass

from ..network import LINK_LOCAL_PREFIX, list_ipv4_addresses

log = logging.getLogger(__name__)

KEEPALIVE_PORT = 50000
STATUS_PORT = 50002

REKORDBOX_PLAYER_NUMBER = 17      # 0x11, what real rekordbox announces as
REKORDBOX_MODEL = "rekordbox"     # the 20-byte model string in every packet
DEFAULT_NAME = "LiveReact"        # shown in the rekordbox_reply name field
KEEPALIVE_INTERVAL_S = 1.6
STATUS_INTERVAL_S = 0.2

IP_BOUND_IF_DARWIN = 25           # <netinet/in.h> IP_BOUND_IF; Python's socket module lacks the constant

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


class AnnounceError(RuntimeError):
    pass


@dataclass(frozen=True)
class IfaceIdentity:
    name: str
    ip: str
    mac: str          # "aa:bb:cc:dd:ee:ff" (what the library's MacAddrAdapter wants)
    broadcast: str
    ifindex: int | None

    @property
    def link_local(self) -> bool:
        return self.ip.startswith(LINK_LOCAL_PREFIX)


# ------------------------------------------------------------------ interface discovery
def _ifconfig(name: str) -> str:
    try:
        return subprocess.run(["ifconfig", name], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001 - no ifconfig (Windows) or timeout
        return ""


def synthetic_mac(ip: str) -> str:
    """A locally-administered MAC derived from the IP, for hosts where we can't read one."""
    parts = [int(x) for x in ip.split(".")] if ip.count(".") == 3 else [0, 0, 0, 0]
    return "02:4c:52:{:02x}:{:02x}:{:02x}".format(parts[1] & 0xFF, parts[2] & 0xFF, parts[3] & 0xFF)


def _mac_for(name: str, ip: str) -> str:
    text = _ifconfig(name)
    m = re.search(r"\bether\s+([0-9a-fA-F:]{17})", text)
    if m:
        mac = m.group(1).lower()
        if _MAC_RE.match(mac):
            return mac
    try:
        import psutil  # optional

        for a in psutil.net_if_addrs().get(name, []):
            if getattr(a, "family", None) == getattr(psutil, "AF_LINK", object()) and _MAC_RE.match(str(a.address).lower()):
                return str(a.address).lower()
    except Exception:  # noqa: BLE001
        pass
    return synthetic_mac(ip)


def _broadcast_for(name: str, ip: str) -> str:
    if ip.startswith(LINK_LOCAL_PREFIX):
        return "169.254.255.255"
    m = re.search(r"inet " + re.escape(ip) + r".*?broadcast\s+(\d+\.\d+\.\d+\.\d+)", _ifconfig(name))
    return m.group(1) if m else "255.255.255.255"


def interface_identity(interface: str | None = None) -> IfaceIdentity:
    """Pick the NIC to announce on: ``interface`` (name or IP) if given, else the one
    holding a 169.254.x.x address, else the first non-loopback IPv4."""
    addrs = [(n, ip) for n, ip in list_ipv4_addresses() if not ip.startswith("127.")]
    if not addrs:
        raise AnnounceError("no IPv4 interface found to announce on")
    chosen: tuple[str, str] | None = None
    if interface:
        for n, ip in addrs:
            if interface in (n, ip):
                chosen = (n, ip)
                break
        if chosen is None:
            raise AnnounceError(f"interface {interface!r} has no IPv4 address (have: {addrs})")
    else:
        link_local = [a for a in addrs if a[1].startswith(LINK_LOCAL_PREFIX)]
        chosen = link_local[0] if link_local else addrs[0]
    name, ip = chosen
    try:
        ifindex: int | None = socket.if_nametoindex(name)
    except (OSError, AttributeError, ValueError):
        ifindex = None
    return IfaceIdentity(name=name, ip=ip, mac=_mac_for(name, ip), broadcast=_broadcast_for(name, ip),
                         ifindex=ifindex)


def pin_to_interface(sock: socket.socket, ifindex: int | None) -> bool:
    """macOS: force this socket's outbound packets onto one NIC (IP_BOUND_IF).
    No-op elsewhere / without an index. Returns True when applied."""
    if not ifindex or sys.platform != "darwin":
        return False
    try:
        sock.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_BOUND_IF", IP_BOUND_IF_DARWIN), int(ifindex))
        return True
    except OSError as exc:
        log.debug("IP_BOUND_IF not applied: %s", exc)
        return False


# ------------------------------------------------------------------ packet builders
def build_keepalive(packets, *, ip: str, mac: str, player_number: int = REKORDBOX_PLAYER_NUMBER,
                    model: str = REKORDBOX_MODEL) -> bytes:
    """Type 0x06 keepalive announcing a rekordbox-class peer (54 bytes)."""
    return packets.KeepAlivePacket.build(dict(
        type="type_status", model=model, device_type="rekordbox", subtype="stype_status",
        content=dict(player_number=player_number, u2=1, mac_addr=mac, ip_addr=ip,
                     device_count=2, flags=4, u4=0),   # flags 4 = is_rekordbox
    ))


def build_djm_status(packets, *, player_number: int = REKORDBOX_PLAYER_NUMBER, model: str = REKORDBOX_MODEL,
                     bpm: float = 128.0, beat: int = 1) -> bytes:
    """Type 0x29 mixer-class status (56 bytes) -- what rekordbox emits every 200 ms."""
    return packets.StatusPacket.build(dict(
        type="djm", model=model, u2=1, player_number=player_number,
        extra=dict(remaining_bytes=0x14, u4=0),
        # pitch 1 (int, neutral): the library's PitchAdapter multiplies by 0x100000 and
        # construct's Int32ub refuses a float, so this must stay an int.
        content=dict(state=0, physical_pitch=1, u5=0x8000, bpm=int(round(bpm)), beat=int(beat)),
    ))


def build_rekordbox_reply(packets, *, player_number: int = REKORDBOX_PLAYER_NUMBER, model: str = REKORDBOX_MODEL,
                          name: str = DEFAULT_NAME) -> bytes:
    """Type 0x11 reply to a player's rekordbox_hello (296 bytes: 36 + 0x104)."""
    return packets.StatusPacket.build(dict(
        type="rekordbox_reply", model=model, u2=1, player_number=player_number,
        extra=dict(remaining_bytes=0x104, u4=0),
        content=dict(name=name),
    ))


# ------------------------------------------------------------------ the announcer thread
class RekordboxAnnouncer(threading.Thread):
    """Sends keepalives (broadcast, port 50000) and DJM-class status (unicast to each
    known peer, port 50002) on a schedule, and answers rekordbox_hello packets.

    Uses the listener's already-bound sockets so replies leave from source port
    50002 (peers ignore replies from ephemeral ports).
    """

    def __init__(self, packets, keepalive_sock: socket.socket, status_sock: socket.socket,
                 identity: IfaceIdentity, clock, stop_event: threading.Event, *,
                 player_number: int = REKORDBOX_PLAYER_NUMBER, name: str = DEFAULT_NAME,
                 keepalive_s: float = KEEPALIVE_INTERVAL_S, status_s: float = STATUS_INTERVAL_S,
                 send_status: bool = True) -> None:
        super().__init__(name="pdl-announce", daemon=True)
        self.packets = packets
        self.keepalive_sock = keepalive_sock
        self.status_sock = status_sock
        self.identity = identity
        self.clock = clock
        self.stop_event = stop_event
        self.player_number = player_number
        self.peer_name = name
        self.keepalive_s = keepalive_s
        self.status_s = status_s
        self.send_status = send_status
        self._own_stop = threading.Event()
        self._lock = threading.Lock()
        self._peers: set[str] = set()
        self.stats: dict = {"keepalives_sent": 0, "status_sent": 0, "hellos_answered": 0,
                            "errors": 0, "last_error": "", "started_at": None}
        self._keepalive = build_keepalive(packets, ip=identity.ip, mac=identity.mac, player_number=player_number)
        self._reply = build_rekordbox_reply(packets, player_number=player_number, name=name)
        self._djm_status = build_djm_status(packets, player_number=player_number)

    # -------------------------------------------------------------- peers / replies
    def note_peer(self, ip: str) -> None:
        if not ip or ip == self.identity.ip:
            return
        with self._lock:
            if ip not in self._peers:
                self._peers.add(ip)
                log.info("Pro DJ Link peer seen: %s", ip)

    @property
    def peers(self) -> set[str]:
        with self._lock:
            return set(self._peers)

    def reply_hello(self, addr: tuple[str, int]) -> None:
        """Answer a rekordbox_hello: unicast the 0x11 reply from port 50002 to the sender."""
        self.note_peer(addr[0])
        if self._send(self.status_sock, self._reply, (addr[0], STATUS_PORT)):
            self.stats["hellos_answered"] += 1

    # -------------------------------------------------------------- loop
    def run(self) -> None:
        self.stats["started_at"] = self.clock.monotonic()
        next_keepalive = 0.0
        next_status = 0.0
        while not (self.stop_event.is_set() or self._own_stop.is_set()):
            now = self.clock.monotonic()
            if now >= next_keepalive:
                if self._send(self.keepalive_sock, self._keepalive, (self.identity.broadcast, KEEPALIVE_PORT)):
                    self.stats["keepalives_sent"] += 1
                next_keepalive = now + self.keepalive_s
            if self.send_status and now >= next_status:
                for ip in self.peers:
                    if self._send(self.status_sock, self._djm_status, (ip, STATUS_PORT)):
                        self.stats["status_sent"] += 1
                next_status = now + self.status_s
            wait = max(0.02, min(next_keepalive, next_status if self.send_status else next_keepalive) - now)
            self._own_stop.wait(min(wait, 0.25))

    def stop(self, timeout: float = 1.0) -> None:
        self._own_stop.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def _send(self, sock: socket.socket, data: bytes, addr: tuple[str, int]) -> bool:
        try:
            sock.sendto(data, addr)
            return True
        except OSError as exc:
            self.stats["errors"] += 1
            text = f"{type(exc).__name__}: {exc}"
            if text != self.stats["last_error"]:
                self.stats["last_error"] = text
                log.warning("Pro DJ Link announce send to %s failed: %s (further identical errors suppressed)",
                            addr, text)
            return False
