"""inputs/audio.py dead-stream recovery (2026-11-02, 2026-10-31 show post-mortem: the XDJ's capture stream died
at 22:45 and the show ran deaf for 44 minutes)."""
from __future__ import annotations

import queue
import sys
import threading
import time
import types

import numpy as np
import pytest

from livereact.clock import RealClock
from livereact.inputs import audio as audio_mod
from livereact.inputs.audio import BLOCK, AudioAnalyzer

XDJ = {"name": "XDJ-XZ", "max_input_channels": 8, "default_samplerate": 44100.0}
MIC = {"name": "MacBook Pro Microphone", "max_input_channels": 1, "default_samplerate": 48000.0}


class _FakeSd(types.ModuleType):
    """Just enough sounddevice: a device list that can change, streams whose callback the test drives."""

    def __init__(self) -> None:
        super().__init__("sounddevice")
        self.devices = [MIC, XDJ]
        self.visible = list(self.devices)   # what PortAudio reports until _initialize rescans
        self.streams: list = []
        self.rescans = 0
        sd = self

        class InputStream:
            def __init__(self, device, callback, **kw):
                if sd.visible[device] is None:
                    raise RuntimeError("device unavailable")
                self.device, self.callback, self.latency, self.alive = device, callback, 0.03, False
                sd.streams.append(self)

            def start(self):
                self.alive = True

            def stop(self):
                self.alive = False

            def close(self):
                self.alive = False

        self.InputStream = InputStream

    def query_devices(self, device=None):
        listed = [d if d is not None else {"name": "", "max_input_channels": 0} for d in self.visible]
        return listed if device is None else listed[device]

    def _terminate(self):
        pass

    def _initialize(self):
        self.rescans += 1
        self.visible = list(self.devices)


def _feed(stream, n: int = 5) -> None:
    block = np.zeros((BLOCK, 8), dtype="float32")
    block[:, 4:6] = 0.1
    for _ in range(n):
        stream.callback(block, BLOCK, None, None)


def _wait(cond, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def sd(monkeypatch):
    fake = _FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    monkeypatch.setattr(audio_mod, "STALL_S", 0.6)
    monkeypatch.setattr(audio_mod, "REOPEN_BACKOFF_S", (0.2,))
    return fake


def _analyzer():
    q, stop = queue.Queue(), threading.Event()
    return AudioAnalyzer(q, RealClock(), stop), q, stop


def test_a_dead_stream_is_reopened_on_the_xdj_even_at_a_new_index(sd):
    a, q, stop = _analyzer()
    a.start()
    try:
        first = sd.streams[-1]
        assert first.device == 1
        _feed(first)
        assert _wait(lambda: not q.empty())
        # the stream dies and the XDJ re-enumerates at another index
        sd.devices = [MIC, {"name": "Some Other Input", "max_input_channels": 2, "default_samplerate": 44100.0}, XDJ]
        assert _wait(lambda: a.reopens == 1)
        second = sd.streams[-1]
        assert second is not first and second.device == 2 and sd.rescans >= 1
        while not q.empty():
            q.get_nowait()
        _feed(second)
        assert _wait(lambda: not q.empty())          # levels flow again
    finally:
        stop.set()
        a.stop()


def test_keeps_retrying_while_the_xdj_is_off_the_bus(sd):
    a, q, stop = _analyzer()
    a.start()
    try:
        _feed(sd.streams[-1])
        sd.devices = [MIC, None]                     # unplugged: the XDJ is gone
        time.sleep(1.5)
        assert a.reopens == 0 and sd.rescans >= 2    # tried, still trying
        assert all(s.device != 0 for s in sd.streams)   # never fell back to the laptop mic
        sd.devices = [MIC, XDJ]                      # plugged back in
        assert _wait(lambda: a.reopens == 1)
    finally:
        stop.set()
        a.stop()


def test_a_live_but_silent_stream_is_not_reopened(sd):
    a, q, stop = _analyzer()
    a.start()
    try:
        s = sd.streams[-1]
        silent = np.zeros((BLOCK, 8), dtype="float32")
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            s.callback(silent, BLOCK, None, None)
            time.sleep(0.02)
        assert a.reopens == 0 and len(sd.streams) == 1
    finally:
        stop.set()
        a.stop()


def test_an_xdj_plugged_in_after_launch_is_picked_up(sd):
    sd.devices = sd.visible = [MIC, None]               # launched with the XDJ unplugged
    a, q, stop = _analyzer()
    a.start()                                           # must not raise: auto-detect waits for it
    try:
        assert a._stream is None
        sd.devices = [MIC, XDJ]
        assert _wait(lambda: a._stream is not None and a._stream.device == 1)
        _feed(a._stream)
        assert _wait(lambda: not q.empty())
    finally:
        stop.set()
        a.stop()


def test_an_explicit_device_that_is_missing_still_fails_at_launch(sd):
    sd.devices = sd.visible = [MIC, None]
    a = AudioAnalyzer(queue.Queue(), RealClock(), threading.Event(), device="XDJ-XZ")
    with pytest.raises(Exception):
        a.start()


def test_shutdown_during_an_outage_is_prompt_and_leaves_no_stream(sd):
    a, q, stop = _analyzer()
    a.start()
    _feed(sd.streams[-1])
    sd.devices = [MIC, None]
    time.sleep(1.0)                                     # stalled, reopening
    t0 = time.monotonic()
    stop.set()
    a.stop()
    assert time.monotonic() - t0 < 3.0
    assert a._stream is None and not any(s.alive for s in sd.streams)
