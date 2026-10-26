"""Packet builders for the rekordbox-class announce + the announcer's reply path.

Round-trips through the vendored library's own parsers; skipped when it is absent.
"""

from __future__ import annotations

import re
import threading

import pytest

packets = pytest.importorskip("prodj.network.packets")

from livereact.clock import FakeClock  # noqa: E402
from livereact.inputs.pdl_announce import (  # noqa: E402
    IfaceIdentity,
    RekordboxAnnouncer,
    build_djm_status,
    build_keepalive,
    build_rekordbox_reply,
    interface_identity,
    synthetic_mac,
)


class FakeSock:
    def __init__(self, port: int) -> None:
        self.port = port
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.fail: OSError | None = None

    def getsockname(self):
        return ("0.0.0.0", self.port)

    def sendto(self, data, addr):
        if self.fail is not None:
            raise self.fail
        self.sent.append((bytes(data), addr))
        return len(data)


IDENT = IfaceIdentity(name="en5", ip="169.254.50.50", mac="aa:bb:cc:dd:ee:ff", broadcast="169.254.255.255", ifindex=7)


def test_keepalive_announces_rekordbox_player_17():
    raw = build_keepalive(packets, ip=IDENT.ip, mac=IDENT.mac)
    assert len(raw) == 0x36
    p = packets.KeepAlivePacket.parse(raw)
    assert str(p.type) == "type_status" and str(p.device_type) == "rekordbox"
    assert p.model == "rekordbox"
    assert int(p.content.player_number) == 17
    assert p.content.mac_addr == IDENT.mac and p.content.ip_addr == IDENT.ip
    assert p.content.flags.is_rekordbox and not p.content.flags.is_player_or_mixer
    assert raw[0x21] == 3 and raw[0x34] == 4   # device_type byte, flags byte


def test_djm_status_shape():
    raw = build_djm_status(packets, bpm=128.0, beat=3)
    assert len(raw) == 36 + 0x14
    p = packets.StatusPacket.parse(raw)
    assert str(p.type) == "djm" and int(p.player_number) == 17 and int(p.extra.remaining_bytes) == 0x14
    assert abs(float(p.content.bpm) - 128.0) < 1e-6 and int(p.content.beat) == 3
    assert raw[0x20] == 1 and raw[0x21] == 17   # u2 = 1 for rekordbox/djm class, then our player number


def test_rekordbox_reply_shape():
    raw = build_rekordbox_reply(packets, name="LiveReact")
    assert len(raw) == 36 + 0x104
    p = packets.StatusPacket.parse(raw)
    assert str(p.type) == "rekordbox_reply" and int(p.player_number) == 17
    assert int(p.extra.remaining_bytes) == 0x104
    assert p.content.name == "LiveReact"
    assert raw[0x0a] == 0x11


def test_announcer_replies_from_the_status_socket():
    ka, st = FakeSock(50000), FakeSock(50002)
    ann = RekordboxAnnouncer(packets, ka, st, IDENT, FakeClock(), threading.Event())
    ann.reply_hello(("169.254.20.137", 50002))
    assert len(st.sent) == 1 and not ka.sent
    data, addr = st.sent[0]
    assert addr == ("169.254.20.137", 50002) and len(data) == 36 + 0x104
    assert ann.stats["hellos_answered"] == 1
    assert ann.peers == {"169.254.20.137"}
    ann.note_peer(IDENT.ip)            # never our own address
    assert ann.peers == {"169.254.20.137"}


def test_announcer_send_failure_is_counted_not_raised():
    ka, st = FakeSock(50000), FakeSock(50002)
    st.fail = OSError(65, "No route to host")
    ann = RekordboxAnnouncer(packets, ka, st, IDENT, FakeClock(), threading.Event())
    ann.reply_hello(("169.254.20.137", 50002))
    ann.reply_hello(("169.254.20.137", 50002))
    assert ann.stats["hellos_answered"] == 0 and ann.stats["errors"] == 2
    assert "No route to host" in ann.stats["last_error"]


def test_announcer_thread_sends_keepalive_and_stops():
    ka, st = FakeSock(50000), FakeSock(50002)
    stop = threading.Event()
    ann = RekordboxAnnouncer(packets, ka, st, IDENT, FakeClock(), stop, keepalive_s=0.01, status_s=0.01)
    ann.note_peer("169.254.20.137")
    ann.start()
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and (ann.stats["keepalives_sent"] < 1 or ann.stats["status_sent"] < 1):
        time.sleep(0.01)
    ann.stop()
    assert not ann.is_alive()
    assert ann.stats["keepalives_sent"] >= 1 and ka.sent[0][1] == ("169.254.255.255", 50000)
    assert ann.stats["status_sent"] >= 1 and st.sent[0][1] == ("169.254.20.137", 50002)


def test_interface_identity_never_raises_on_this_host():
    ident = interface_identity(None)
    assert re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", ident.mac)
    assert ident.ip.count(".") == 3 and ident.broadcast.count(".") == 3
    assert synthetic_mac("169.254.50.50") == "02:4c:52:fe:32:32"
    with pytest.raises(Exception):
        interface_identity("no-such-interface-xyz")
