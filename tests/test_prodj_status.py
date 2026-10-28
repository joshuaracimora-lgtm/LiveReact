"""ProDjLinkListener's port-50002 path: CDJ status -> DeckStatusMessage, hello -> reply,
and the untouched beat path, driven with bytes built by the vendored library."""

from __future__ import annotations

import queue
import threading

import pytest

packets = pytest.importorskip("prodj.network.packets")

from livereact.clock import FakeClock  # noqa: E402
from livereact.core.messages import BeatMessage, DeckStatusMessage, StatusMessage  # noqa: E402
from livereact.inputs.pdl_announce import IfaceIdentity, RekordboxAnnouncer  # noqa: E402
from livereact.inputs.prodj import ProDjLinkListener  # noqa: E402

XZ = ("169.254.20.137", 50002)
IDENT = IfaceIdentity(name="en5", ip="169.254.50.50", mac="aa:bb:cc:dd:ee:ff", broadcast="169.254.255.255", ifindex=None)


class FakeSock:
    def __init__(self, port: int) -> None:
        self.port = port
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def getsockname(self):
        return ("0.0.0.0", self.port)

    def sendto(self, data, addr):
        self.sent.append((bytes(data), addr))
        return len(data)


def cdj_status(*, player=1, track_id=175, slot="sd", play_state="playing", beat_count=1234, beat=2,
               on_air=True, master=False, bpm=125, pitch=2, analyze="rekordbox") -> bytes:
    return packets.StatusPacket.build(dict(
        type="cdj", model="XDJ-XZ", u2=4, player_number=player,
        extra=dict(remaining_bytes=0xF8, u4=0),
        content=dict(
            activity=1, loaded_player_number=1, loaded_slot=slot, track_analyze_type=analyze,
            track_id=track_id, track_number=3, firmware="1.26",
            state=dict(on_air=on_air, master=master, sync=False, play=play_state == "playing"),
            play_state=play_state, play_state2=0xFA, physical_pitch=pitch, bpm=bpm, actual_pitch=pitch,
            play_state3=9, beat_count=beat_count, cue_distance=0x1FF, beat=beat,
            physical_pitch2=pitch, actual_pitch2=pitch, packet_count=1, is_nexus=0x1F,
        ),
    ))


def hello(player=1) -> bytes:
    return packets.StatusPacket.build(dict(type="rekordbox_hello", model="XDJ-XZ", u2=4, player_number=player,
                                           extra=dict(payload_size=0), content=None))


def beat_packet(player=1, beat=1, bpm=125, pitch=2) -> bytes:
    return packets.BeatPacket.build(dict(
        type="type_beat", model="XDJ-XZ", player_number=player, subtype="stype_beat",
        content=dict(distances=dict(next_beat=480, **{"2nd_beat": 960}, next_bar=1920, **{"4th_beat": 1920},
                                    **{"2nd_bar": 3840}, **{"8th_beat": 3840}),
                     pitch=pitch, bpm=bpm, beat=beat, player_number2=player),
    ))


def make_listener(*, with_announcer=True, emit_status=False):
    q: queue.Queue = queue.Queue()
    clock = FakeClock(start=100.0)
    seen: list[tuple[DeckStatusMessage, bytes]] = []
    src = ProDjLinkListener(q, clock, threading.Event(), status_callback=lambda m, raw: seen.append((m, raw)),
                            emit_status=emit_status)
    src._packets = packets
    socks = {p: FakeSock(p) for p in (50000, 50001, 50002)}
    src._socks = [socks[50000], socks[50001], socks[50002]]
    if with_announcer:
        src._announcer = RekordboxAnnouncer(packets, socks[50000], socks[50002], IDENT, clock, threading.Event())
    return src, q, socks, seen


def test_cdj_status_becomes_deck_status_message():
    src, q, socks, seen = make_listener()
    raw = cdj_status()
    src._handle_packet(socks[50002], raw, XZ)
    assert len(seen) == 1
    msg, got_raw = seen[0]
    assert got_raw == raw
    assert msg.player == 1 and msg.track_id == 175 and msg.slot == "sd" and msg.loaded_player == 1
    assert msg.analyze_type == "rekordbox" and msg.play_state == "playing" and msg.playing
    assert msg.on_air and not msg.master and not msg.sync
    assert abs(msg.bpm - 125.0) < 1e-6 and abs(msg.pitch - 2.0) < 1e-6  # pitch must be int-valued to build (library adapter)
    assert msg.beat_count == 1234 and msg.beat_in_bar == 2 and msg.source_ip == XZ[0]
    assert msg.timestamp == 100.0
    assert src.last_status[1] is msg and src.status_count == 1 and src.first_status_at == 100.0
    assert q.empty()                       # emit_status False: nothing reaches the metronome queue
    assert src._announcer.peers == {XZ[0]}  # a status sender is a peer worth sending status to


def test_emit_status_puts_message_on_queue():
    src, q, socks, _ = make_listener(emit_status=True)
    src._handle_packet(socks[50002], cdj_status(player=2, play_state="cued", beat_count=0xFFFFFFFF, track_id=0,
                                                 slot="empty", analyze="unknown", on_air=False), XZ)
    msg = q.get_nowait()
    assert isinstance(msg, DeckStatusMessage)
    assert msg.player == 2 and not msg.playing and msg.beat_count is None and msg.track_id == 0
    assert msg.slot == "empty" and msg.analyze_type == "unknown"


def test_hello_is_answered_from_port_50002():
    src, q, socks, seen = make_listener()
    src._handle_packet(socks[50002], hello(), XZ)
    assert src.hello_count == 1 and not seen
    assert len(socks[50002].sent) == 1
    data, addr = socks[50002].sent[0]
    assert addr == XZ and len(data) == 36 + 0x104 and data[0x0A] == 0x11
    assert not socks[50000].sent


def test_hello_without_announcer_is_harmless():
    src, q, socks, seen = make_listener(with_announcer=False)
    src._handle_packet(socks[50002], hello(), XZ)
    assert src.hello_count == 1 and not socks[50002].sent and q.empty()


def test_keepalive_registers_peer():
    src, q, socks, _ = make_listener()
    src._handle_packet(socks[50000], b"Qspt1WmJOL" + b"\x06" + b"\x00" * 43, XZ)
    assert src._seen_device and src._announcer.peers == {XZ[0]}


def test_beat_path_unchanged():
    src, q, socks, seen = make_listener()
    src._handle_packet(socks[50001], beat_packet(beat=1), ("169.254.20.137", 50001))
    src._handle_packet(socks[50001], beat_packet(beat=1), ("169.254.20.137", 50001))  # same beat: no 2nd BeatMessage
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    beats = [m for m in msgs if isinstance(m, BeatMessage)]
    statuses = [m for m in msgs if isinstance(m, StatusMessage)]
    assert len(beats) == 1 and len(statuses) == 2
    assert beats[0].beat_in_bar == 1 and beats[0].player == 1 and beats[0].is_master
    assert abs(beats[0].bpm - 125.0 * 2.0) < 1e-3
    assert statuses[0].playing and statuses[0].master_deck == 1 and statuses[0].on_air == (1,)
    assert not seen


def test_garbage_on_status_port_is_logged_not_raised():
    src, q, socks, seen = make_listener()
    src._handle_packet(socks[50002], b"\x00\x01\x02 not a packet", XZ)
    src._handle_packet(socks[50002], b"", XZ)
    assert not seen and q.empty()
    assert src.status_parse_errors == 2       # counted, logged once per distinct error, never raised
    src._handle_packet(socks[50002], cdj_status(), XZ)  # still alive afterwards
    assert len(seen) == 1


def test_phrase_service_is_asked_once_per_track_change():
    calls = []

    prefetches = []

    class Svc:
        def request(self, ip, slot, player, track_id):
            calls.append((ip, slot, player, track_id))

        def prefetch_pdb(self, ip, slot):
            prefetches.append((ip, slot))

    src, q, socks, _ = make_listener()
    src.phrase_service = Svc()
    for _ in range(3):
        src._handle_packet(socks[50002], cdj_status(track_id=175), XZ)
    src._handle_packet(socks[50002], cdj_status(track_id=176), XZ)
    src._handle_packet(socks[50002], cdj_status(player=2, track_id=175), XZ)
    src._handle_packet(socks[50002], cdj_status(player=2, track_id=0), XZ)   # nothing loaded: no request
    assert calls == [(XZ[0], "sd", 1, 175), (XZ[0], "sd", 1, 176), (XZ[0], "sd", 2, 175)]
    assert prefetches == [(XZ[0], "sd")]          # export.pdb warmed once per (ip, slot), on the first status


def test_looping_play_state_is_reported_as_a_loop():
    src, q, socks, seen = make_listener()
    src._handle_status(cdj_status(play_state="looping", beat_count=64), XZ)
    src._handle_status(cdj_status(play_state="playing", beat_count=65), XZ)
    looped, played = seen[0][0], seen[1][0]
    assert looped.looping and looped.playing and looped.play_state == "looping"
    assert not played.looping and played.playing
