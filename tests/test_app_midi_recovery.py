"""inputs/midi.py port-loss recovery (2026-11-03; 2026-10-31 show post-mortem: the XDJ dropped off
USB at 23:36 and the DMX writer reopened in 3 s -- the MIDI port, opened once at launch, would have
stayed dead for the rest of the set with nothing on the console saying so)."""
from __future__ import annotations

import queue
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from livereact.clock import RealClock
from livereact.inputs import midi as midi_mod
from livereact.inputs.midi import MidiListener
from livereact.ui.health import health_warnings

XDJ_1 = "PIONEER XDJ-XZ MIDI (1)"
XDJ_2 = "PIONEER XDJ-XZ MIDI (2)"


class _FakePort:
    def __init__(self, name, callback):
        self.name, self.callback, self.closed = name, callback, False

    def close(self):
        self.closed = True


class _FakeMido(types.ModuleType):
    """Just enough mido: a port list that can change, ports whose callback the test drives."""

    def __init__(self) -> None:
        super().__init__("mido")
        self.names: list[str] = [XDJ_1, XDJ_2]
        self.ports: list[_FakePort] = []

    def get_input_names(self):
        return list(self.names)

    def open_input(self, name, callback=None):
        if name not in self.names:
            raise OSError(f"MIDI port {name!r} unavailable")
        port = _FakePort(name, callback)
        self.ports.append(port)
        return port


def _fader(port) -> None:
    # (channel 4, cc 17) = fader_a in DEFAULT_CC_MAP
    port.callback(SimpleNamespace(type="control_change", channel=4, control=17, value=64))


def _wait(cond, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def mido(monkeypatch):
    fake = _FakeMido()
    monkeypatch.setitem(sys.modules, "mido", fake)
    monkeypatch.setattr(midi_mod, "POLL_S", 0.1)
    return fake


def _listener():
    q = queue.Queue()
    return MidiListener(q, RealClock()), q


def test_a_vanished_port_is_closed_and_reopened_when_it_is_back(mido):
    listener, q = _listener()
    listener.start()
    try:
        first = mido.ports[-1]
        assert first.name == XDJ_2            # the "(2)" port carries the data on hardware
        _fader(first)
        assert not q.empty()
        mido.names = []                       # the XDJ drops off USB
        assert _wait(lambda: first.closed)    # the dead port is closed, not left dangling
        assert listener.lost
        mido.names = [XDJ_1, XDJ_2]           # plugged back in
        assert _wait(lambda: listener.reopens == 1)
        assert not listener.lost
        second = mido.ports[-1]
        assert second is not first and second.name == XDJ_2
        while not q.empty():
            q.get_nowait()
        _fader(second)
        assert not q.empty()                  # controls flow again
    finally:
        listener.stop()


def test_a_loss_never_grabs_an_unrelated_controller(mido):
    listener, q = _listener()
    listener.start()
    try:
        mido.names = ["Some Synth"]           # the XDJ is gone; another device is not it
        assert _wait(lambda: mido.ports[0].closed)
        time.sleep(0.5)
        assert listener._port is None and all(p.name != "Some Synth" for p in mido.ports)
        mido.names = ["Some Synth", XDJ_2]
        assert _wait(lambda: listener.reopens == 1)
        assert mido.ports[-1].name == XDJ_2
    finally:
        listener.stop()


def test_an_xdj_plugged_in_after_launch_is_picked_up(mido):
    mido.names = []
    listener, q = _listener()
    listener.start()                          # must not raise: the watchdog waits for it
    try:
        assert listener._port is None
        mido.names = [XDJ_1, XDJ_2]
        assert _wait(lambda: listener._port is not None)
        _fader(mido.ports[-1])
        assert not q.empty()
    finally:
        listener.stop()


def test_shutdown_during_an_outage_is_prompt_and_closes_the_port(mido):
    listener, q = _listener()
    listener.start()
    mido.names = []
    assert _wait(lambda: mido.ports[0].closed)
    t0 = time.monotonic()
    listener.stop()
    assert time.monotonic() - t0 < 3.0
    assert listener._port is None and not any(t.name == "midi-watchdog" and t.is_alive()
                                              for t in threading.enumerate())


# ---------------------------------------------------------------- the operator must see it

def _app(midi_src):
    pdl = SimpleNamespace(name="prodj-link", last_packet_time=59.0)
    return SimpleNamespace(config=SimpleNamespace(demo=False), writer=SimpleNamespace(is_open=True),
                           failed_inputs={}, started_at=0.0, sources=[pdl, midi_src])


def test_a_lost_midi_port_shows_in_the_health_banner():
    snap = SimpleNamespace(pdl_fresh=True, audio_fresh=True)
    lost = SimpleNamespace(name="midi", lost=True)
    w = health_warnings(_app(lost), snap, 60.0)
    assert len(w) == 1 and "MIDI LOST" in w[0] and "USB" in w[0]
    healthy = SimpleNamespace(name="midi", lost=False)
    assert health_warnings(_app(healthy), snap, 60.0) == []


def test_a_setup_that_never_had_midi_is_not_nagged(mido):
    # no XDJ MIDI at launch: preflight already WARNs; the banner stays quiet
    mido.names = []
    listener, _ = _listener()
    listener.start()
    try:
        snap = SimpleNamespace(pdl_fresh=True, audio_fresh=True)
        assert not listener.lost
        assert health_warnings(_app(listener), snap, 60.0) == []
    finally:
        listener.stop()
