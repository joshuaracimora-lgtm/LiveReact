"""Recordings disk filling up (2026-11-07 resilience map): before, the recorder caught the write error
every tick, the recording silently stopped growing and nothing told the operator; --preflight had no
free-space check. Now --preflight checks the disk the session will be written to, and the console
banner says so mid-set when a recording session's disk runs low. Free space is faked here."""
from __future__ import annotations

from types import SimpleNamespace

from livereact import preflight as pf
from livereact.ui import health

GB = 1024 ** 3
ADDRS = [("en8", "169.254.116.220")]


def _run(free):
    return pf.run("rigs/junior_crib_fan.json", "en8", running=False, bind=lambda p: None,
                  holder=lambda p: "", addresses=ADDRS, audio=lambda: ("XDJ-XZ", 43, 0.05),
                  midi=["XDJ-XZ (2)"], dmx=lambda: "COM3", disk=lambda: ("C:\\", free))


def _disk(checks):
    return next(c for c in checks if c.name == "Recordings disk")


def test_plenty_of_space_passes_and_says_how_much():
    c = _disk(_run(50 * GB))
    assert c.status == pf.PASS and "50.0 GB free" in c.detail


def test_low_space_warns_and_nearly_full_fails_in_words():
    warn = _disk(_run(1 * GB))
    assert warn.status == pf.WARN and "free up space" in warn.detail
    fail = _disk(_run(100 * 1024 ** 2))
    assert fail.status == pf.FAIL and "recording" in fail.detail.lower()
    assert pf.exit_code(_run(100 * 1024 ** 2)) == 1


def test_a_disk_that_cannot_be_read_only_warns():
    def broken():
        raise OSError("device not ready")
    c = pf.check_disk(broken)
    assert c.status == pf.WARN and "device not ready" in c.detail


def test_free_bytes_walks_up_to_a_folder_that_exists(tmp_path):
    where, free = pf.free_bytes(str(tmp_path / "recordings" / "not-yet"))
    assert where == str(tmp_path) and free > 0


def _app(session_dir, demo=False):
    return SimpleNamespace(config=SimpleNamespace(demo=demo, session_dir=session_dir),
                           writer=SimpleNamespace(is_open=True), failed_inputs={}, started_at=0.0,
                           sources=[], recorder=object())


def _snap():
    return SimpleNamespace(pdl_fresh=True, audio_fresh=True)


def test_banner_warns_when_the_recording_disk_runs_low(monkeypatch):
    monkeypatch.setattr(health, "_free_bytes", lambda path: 100 * 1024 ** 2)
    health._DISK_CACHE.clear()
    w = health.health_warnings(_app("recordings/session-x"), _snap(), 60.0)
    assert len(w) == 1 and w[0].startswith("RECORDING DISK FULL") and "show itself is not affected" in w[0]


def test_banner_quiet_with_space_or_without_recording_and_checks_at_most_every_30s(monkeypatch):
    calls = []
    monkeypatch.setattr(health, "_free_bytes", lambda path: calls.append(path) or 50 * GB)
    health._DISK_CACHE.clear()
    app = _app("recordings/session-y")
    assert health.health_warnings(app, _snap(), 60.0) == []
    assert health.health_warnings(app, _snap(), 70.0) == []
    assert len(calls) == 1                                   # cached: no disk syscall per UI tick
    health.health_warnings(app, _snap(), 60.0 + health.DISK_CHECK_S + 1)
    assert len(calls) == 2
    no_rec = _app(None)
    no_rec.recorder = None
    assert health.health_warnings(no_rec, _snap(), 200.0) == []
    assert len(calls) == 2                                   # no recording: the disk is never asked


def test_banner_survives_a_disk_that_cannot_be_read(monkeypatch):
    def broken(path):
        raise OSError("gone")
    monkeypatch.setattr(health, "_free_bytes", broken)
    health._DISK_CACHE.clear()
    assert health.health_warnings(_app("recordings/session-z"), _snap(), 60.0) == []
