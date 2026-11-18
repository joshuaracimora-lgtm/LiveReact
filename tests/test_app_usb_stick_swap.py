"""A USB stick pulled from the XDJ and another put in its slot (B2B handover, a DJ swapping sticks).

rekordbox track ids are per export and start at 1, so the new stick's "track 4" is almost always a
different song from the old stick's "track 4". Before 2026-11-17 nothing noticed the swap: the listener's
export.pdb prefetch was once per (ip, slot) for the session, and the phrase service answered the new
stick's track ids from the OLD stick's export.pdb and plans -- the show would place the drops of a song
that is not playing, with no warning. The listener now reads the decks' media-slot state from the CDJ
status packets (UNVERIFIED ON HARDWARE: which values the XDJ-XZ sends), forgets the slot on removal, and
says so on the health banner while the phrase service cannot forget it.
"""
from __future__ import annotations

import queue
import threading
from types import SimpleNamespace

import pytest

packets = pytest.importorskip("prodj.network.packets")

from livereact.clock import FakeClock  # noqa: E402
from livereact.inputs.prodj import ProDjLinkListener  # noqa: E402
from livereact.ui.health import health_warnings  # noqa: E402

XZ = ("169.254.20.137", 50002)


class FakeSock:
    def __init__(self, port: int) -> None:
        self.port = port

    def getsockname(self):
        return ("0.0.0.0", self.port)


def status(*, player=1, track_id=4, slot="usb", usb_state="loaded", sd_state="not_loaded") -> bytes:
    return packets.StatusPacket.build(dict(
        type="cdj", model="XDJ-XZ", u2=4, player_number=player,
        extra=dict(remaining_bytes=0xF8, u4=0),
        content=dict(
            activity=1, loaded_player_number=player, loaded_slot=slot if track_id else "empty",
            track_analyze_type="rekordbox" if track_id else "unknown",
            track_id=track_id, track_number=3, firmware="1.26", usb_state=usb_state, sd_state=sd_state,
            state=dict(on_air=True, master=False, sync=False, play=False),
            play_state="paused" if track_id else "no_track", play_state2=0xFE, physical_pitch=2, bpm=125,
            actual_pitch=2, play_state3=1, beat_count=10, cue_distance=0x1FF, beat=1,
            physical_pitch2=2, actual_pitch2=2, packet_count=1, is_nexus=0x1F,
        ),
    ))


class Svc:
    def __init__(self, with_forget: bool) -> None:
        self.calls: list[tuple] = []
        if with_forget:
            self.forget = lambda ip, slot: self.calls.append(("forget", ip, slot))

    def request(self, ip, slot, player, track_id):
        self.calls.append(("request", ip, slot, player, track_id))

    def prefetch_pdb(self, ip, slot):
        self.calls.append(("prefetch", ip, slot))


def _listener(svc):
    src = ProDjLinkListener(queue.Queue(), FakeClock(start=100.0), threading.Event(), phrase_service=svc)
    src._packets = packets
    socks = {p: FakeSock(p) for p in (50000, 50001, 50002)}
    src._socks = [socks[50000], socks[50001], socks[50002]]
    return src, socks[50002]


def _swap(src, sock):
    src._handle_packet(sock, status(track_id=4), XZ)                        # stick A, track 4 loaded
    src._handle_packet(sock, status(track_id=0, usb_state="not_loaded"), XZ)  # stick A pulled
    src._handle_packet(sock, status(track_id=0, usb_state="loaded"), XZ)      # stick B in
    src._handle_packet(sock, status(track_id=4), XZ)                        # B's track 4: another song


def test_a_swapped_stick_is_forgotten_refetched_and_its_track_asked_for_again():
    svc = Svc(with_forget=True)
    src, sock = _listener(svc)
    _swap(src, sock)
    ip = XZ[0]
    assert svc.calls == [
        ("prefetch", ip, "usb"), ("request", ip, "usb", 1, 4),
        ("forget", ip, "usb"),
        ("prefetch", ip, "usb"), ("request", ip, "usb", 1, 4),
    ]
    assert src.stale_media == set()


def test_without_a_forgetting_phrase_service_the_banner_says_the_plans_may_be_stale():
    svc = Svc(with_forget=False)
    src, sock = _listener(svc)
    _swap(src, sock)
    assert src.stale_media == {(XZ[0], "usb")}
    app = SimpleNamespace(config=SimpleNamespace(demo=False), writer=SimpleNamespace(is_open=True),
                          failed_inputs={}, started_at=0.0, sources=[src])
    snap = SimpleNamespace(pdl_fresh=True, audio_fresh=True)
    src.last_packet_time = 60.0
    w = health_warnings(app, snap, 60.0)
    assert len(w) == 1 and w[0].startswith("USB STICK CHANGED") and "Restart LiveReact" in w[0]


def test_a_healthy_session_with_the_stick_in_place_is_unchanged():
    svc = Svc(with_forget=True)
    src, sock = _listener(svc)
    for tid in (4, 4, 5, 0, 5):
        src._handle_packet(sock, status(track_id=tid), XZ)
    ip = XZ[0]
    assert svc.calls == [("prefetch", ip, "usb"), ("request", ip, "usb", 1, 4), ("request", ip, "usb", 1, 5)]
    assert src.stale_media == set()


def test_two_decks_reporting_different_slot_states_do_not_flap():
    """The XZ is two players behind one address; if each reports its own slot, alternating packets
    must not read as the stick going in and out."""
    svc = Svc(with_forget=True)
    src, sock = _listener(svc)
    for _ in range(3):
        src._handle_packet(sock, status(player=1, track_id=4, usb_state="loaded"), XZ)
        src._handle_packet(sock, status(player=2, track_id=0, usb_state="not_loaded"), XZ)
    assert not any(c[0] == "forget" for c in svc.calls)


def test_a_stick_seen_only_as_empty_from_launch_is_not_a_removal():
    svc = Svc(with_forget=True)
    src, sock = _listener(svc)
    src._handle_packet(sock, status(track_id=0, usb_state="not_loaded"), XZ)
    src._handle_packet(sock, status(track_id=4, usb_state="loaded"), XZ)
    assert not any(c[0] == "forget" for c in svc.calls) and src.stale_media == set()


@pytest.mark.xfail(strict=True, reason="needs rekordbox/service.py change -- see REPORT (PhraseService.forget)")
def test_phrase_service_forget_drops_the_slot_so_the_next_request_refetches(tmp_path):
    from livereact.rekordbox.service import PhraseService

    class NoPdbNfs:          # the XDJ answers nothing: enough to count export.pdb fetches
        calls: list = []

        def start(self):
            pass

        def stop(self):
            pass

        def fetch_pdb(self, ip, slot, timeout_s=60.0):
            self.calls.append(("pdb", ip, slot))
            return None

    def _drain(q, n):
        return [q.get(timeout=5.0) for _ in range(n)]

    nfs = NoPdbNfs()
    q: queue.Queue = queue.Queue()
    svc = PhraseService(q, FakeClock(), nfs=nfs, stop_event=threading.Event(), cache_dir=tmp_path)
    svc.start()
    try:
        svc.request(XZ[0], "usb", 1, 4)
        assert len(_drain(q, 1)) == 1
        svc.forget(XZ[0], "usb")
        svc.request(XZ[0], "usb", 1, 4)
        assert len(_drain(q, 1)) == 1
        assert [c for c in nfs.calls if c[0] == "pdb"] == [("pdb", XZ[0], "usb")] * 2
    finally:
        svc.stop()
