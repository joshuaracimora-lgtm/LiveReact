"""PhraseService: one TrackPhraseMessage per requested track, never an exception out of the worker."""

from __future__ import annotations

import importlib.util
import queue
import struct
import threading
import time
from pathlib import Path

import pytest

from livereact.clock import FakeClock
from livereact.core.messages import TrackPhraseMessage
from livereact.rekordbox.library import RekordboxLibrary
from livereact.rekordbox.service import PhraseService

PDB = Path(__file__).resolve().parents[1] / "databases" / "player-1-sd.pdb"
HAVE_PRODJ = importlib.util.find_spec("prodj") is not None


def _ext_with_pssi() -> bytes:
    entry = struct.Struct(">HHHxBxBxBHHHxBxBH")
    entries = [entry.pack(1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0), entry.pack(2, 33, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    body = struct.pack(">H6xH2xBx", 1, 97, 0) + b"".join(entries)
    payload = struct.pack(">IH", 24, len(entries)) + body
    tag = b"PSSI" + struct.pack(">II", 12, 12 + len(payload)) + payload
    head = b"PMAI" + struct.pack(">II", 28, 28 + len(tag)) + b"\x00" * 16
    return head + tag


class FakeNfs:
    def __init__(self, pdb: bytes | None, ext: bytes | None, raise_on_ext: bool = False) -> None:
        self.pdb, self.ext, self.raise_on_ext = pdb, ext, raise_on_ext
        self.calls: list[tuple] = []
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def fetch_pdb(self, ip, slot, timeout_s=60.0):
        self.calls.append(("pdb", ip, slot))
        return self.pdb

    def fetch(self, ip, slot, path, timeout_s=30.0):
        self.calls.append(("file", ip, slot, path))
        if self.raise_on_ext:
            raise OSError("nfs exploded")
        return self.ext


def _drain(q: queue.Queue, n: int, timeout: float = 5.0) -> list[TrackPhraseMessage]:
    out: list[TrackPhraseMessage] = []
    deadline = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < deadline:
        try:
            out.append(q.get(timeout=0.1))
        except queue.Empty:
            pass
    return out


def _service(nfs, tmp_path):
    q: queue.Queue = queue.Queue()
    svc = PhraseService(q, FakeClock(), nfs=nfs, stop_event=threading.Event(), cache_dir=tmp_path)
    svc.start()
    return svc, q


@pytest.mark.skipif(not (HAVE_PRODJ and PDB.exists()), reason="local XDJ export.pdb not present")
def test_real_pdb_plus_synthetic_ext_yields_one_table_per_track(tmp_path):
    nfs = FakeNfs(PDB.read_bytes(), _ext_with_pssi())
    svc, q = _service(nfs, tmp_path)
    try:
        svc.request("169.254.20.137", "sd", 1, 4)
        svc.request("169.254.20.137", "sd", 1, 4)       # duplicate: ignored until answered
        msgs = _drain(q, 1)
        assert len(msgs) == 1
        m = msgs[0]
        assert m.track_id == 4 and m.player == 1 and m.title == "Lift Me Up (Extended)" and m.bpm == 132.0
        assert m.table is not None and [p.label for p in m.table.phrases] == ["intro", "chorus"]
        assert m.source == "rekordbox" and m.error == ""
        assert [c[0] for c in nfs.calls] == ["pdb", "file", "file"]      # export.pdb, .EXT, .DAT (beat grid)
        assert nfs.calls[1][3] == "/PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.EXT"
        assert nfs.calls[2][3] == "/PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.DAT"
        assert m.plan is not None and m.plan.track_id == 4 and [s.kind for s in m.plan.sections] == ["intro", "drop"]
        # a second deck loading the same track gets the cached table without another fetch
        svc.request("169.254.20.137", "sd", 2, 4)
        again = _drain(q, 1)
        assert len(again) == 1 and again[0].player == 2 and again[0].table is m.table and again[0].plan is m.plan
        assert len(nfs.calls) == 3
        # unknown id: the pdb is refetched once (stale export?) then answered with an error
        svc.request("169.254.20.137", "sd", 1, 999999)
        miss = _drain(q, 1)
        assert len(miss) == 1 and miss[0].table is None and "not in export.pdb" in miss[0].error
    finally:
        svc.stop()
    assert not nfs.started


def test_pdb_fetch_failure_answers_with_error(tmp_path):
    nfs = FakeNfs(None, None)
    svc, q = _service(nfs, tmp_path)
    try:
        svc.request("1.2.3.4", "sd", 1, 42)
        msgs = _drain(q, 1)
        assert len(msgs) == 1 and msgs[0].table is None and msgs[0].source == ""
        assert "export.pdb unavailable" in msgs[0].error
        svc.request("1.2.3.4", "cd", 1, 43)     # not a fetchable slot
        msgs = _drain(q, 1)
        assert len(msgs) == 1 and msgs[0].track_id == 43 and msgs[0].table is None
    finally:
        svc.stop()


@pytest.mark.skipif(not (HAVE_PRODJ and PDB.exists()), reason="local XDJ export.pdb not present")
def test_exception_in_fetch_is_contained(tmp_path):
    nfs = FakeNfs(PDB.read_bytes(), None, raise_on_ext=True)
    svc, q = _service(nfs, tmp_path)
    try:
        svc.request("169.254.20.137", "sd", 1, 4)
        msgs = _drain(q, 1)
        assert len(msgs) == 1 and msgs[0].table is None and "nfs exploded" in msgs[0].error
        assert svc.stats["errors"] == 1
        svc.request("169.254.20.137", "sd", 1, 1)   # worker still alive afterwards
        assert len(_drain(q, 1)) == 1
    finally:
        svc.stop()


def test_zero_track_id_is_ignored(tmp_path):
    svc, q = _service(FakeNfs(None, None), tmp_path)
    try:
        svc.request("1.2.3.4", "sd", 1, 0)
        assert not _drain(q, 1, timeout=0.3)
        assert isinstance(svc.library, RekordboxLibrary) and svc.name == "rekordbox-phrases"
    finally:
        svc.stop()


def test_live_plans_read_josh_s_look_notes_like_the_simulator(tmp_path):
    """2026-10-26: only the simulator passed look notes to the planner, so the gate scored looks the
    live show never planned. The service now loads tracks/<stem>.look.json; a bad file is not fatal."""
    from livereact.rekordbox.library import TrackInfo
    (tmp_path / "Song A.look.json").write_text(
        '{"schema": 1, "track": "Song A", "strobe": "soft"}', encoding="utf-8")
    (tmp_path / "Song B.look.json").write_text('{"schema": 1, "strobe": "loud"}', encoding="utf-8")
    (tmp_path / "Song C.look.json").write_text('{"schema": 1, "_todo": true}', encoding="utf-8")
    svc = PhraseService(queue.Queue(), FakeClock(), stop_event=threading.Event(), cache_dir=tmp_path,
                        tracks_dir=tmp_path)

    def info(name):
        return TrackInfo(id=1, title=name, artist="", bpm=120.0, duration_s=200, analyze_path="",
                         path=f"/Contents/x/{name}.mp3", filename=f"{name}.mp3")
    assert svc._look(info("Song A")).strobe == "soft"
    assert svc._look(info("Song B")) is None            # invalid: logged, planned without it
    assert svc._look(info("Song C")) is None            # still a _todo scaffold
    assert svc._look(info("Song D")) is None            # none written
    assert PhraseService(queue.Queue(), FakeClock(), tracks_dir=None)._look(info("Song A")) is None
