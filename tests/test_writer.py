"""Tests for the DMX writer thread.

The writer is driven deterministically with a FakeClock: ``sleep`` advances
simulated time instantly, so the loop runs as fast as the CPU allows while
reporting exact, drift-free timestamps. A counting backend stops the loop after a
fixed number of frames, and a failing backend proves the writer never crashes.
"""

from __future__ import annotations

import threading

import pytest

from livereact.clock import FakeClock
from livereact.core.frame_buffer import UNIVERSE_SIZE, DmxFrameBuffer
from livereact.output.base import OutputBackend
from livereact.output.writer import DmxWriter


class CountingBackend(OutputBackend):
    """Stops the writer after ``stop_after`` frames so tests terminate."""

    def __init__(self, stop_event: threading.Event, stop_after: int) -> None:
        self._stop = stop_event
        self._stop_after = stop_after
        self.sent = 0
        self.opened = False
        self.closed = False

    @property
    def name(self) -> str:
        return "counting"

    def open(self) -> None:
        self.opened = True

    def send(self, universe: bytes) -> None:
        assert len(universe) == UNIVERSE_SIZE
        self.sent += 1
        if self.sent >= self._stop_after:
            self._stop.set()

    def close(self) -> None:
        self.closed = True


class FlakyBackend(CountingBackend):
    """Raises on the first ``fail_first`` sends, then succeeds."""

    def __init__(self, stop_event, stop_after, fail_first) -> None:
        super().__init__(stop_event, stop_after)
        self._fail_first = fail_first
        self._attempts = 0

    def send(self, universe: bytes) -> None:
        self._attempts += 1
        if self._attempts <= self._fail_first:
            raise RuntimeError("simulated device failure")
        super().send(universe)


def _make_writer(backend, clock, stop, hz=40.0):
    return DmxWriter(DmxFrameBuffer(), backend, clock, stop, hz=hz)


def test_writer_sends_exact_frame_count_and_advances_clock_on_grid():
    clock = FakeClock()
    stop = threading.Event()
    backend = CountingBackend(stop, stop_after=10)
    writer = _make_writer(backend, clock, stop, hz=40)

    writer._run_loop()  # run synchronously in the test thread

    assert backend.sent == 10
    assert backend.opened and backend.closed
    assert writer.frames_sent == 10
    # 10 ticks at 25 ms, drift-free.
    assert clock.monotonic() == pytest.approx(10 * 0.025)


def test_writer_survives_backend_failures_and_keeps_going():
    clock = FakeClock()
    stop = threading.Event()
    backend = FlakyBackend(stop, stop_after=5, fail_first=3)
    writer = _make_writer(backend, clock, stop, hz=40)

    writer._run_loop()  # must not raise despite 3 failed sends

    assert backend.sent == 5  # eventually delivered 5 good frames
    assert writer.frames_sent == 5


def test_writer_stops_promptly_when_stop_event_set_before_start():
    clock = FakeClock()
    stop = threading.Event()
    stop.set()
    backend = CountingBackend(stop, stop_after=1000)
    writer = _make_writer(backend, clock, stop, hz=40)

    writer._run_loop()

    assert backend.sent == 0
    assert backend.opened and backend.closed  # opened/closed cleanly even with no frames


def test_writer_runs_as_a_real_thread_and_joins():
    """End-to-end with a RealClock for a short burst, then clean shutdown."""
    from livereact.clock import RealClock

    clock = RealClock()
    stop = threading.Event()
    backend = CountingBackend(stop, stop_after=3)
    writer = _make_writer(backend, clock, stop, hz=200)  # fast so the test is quick

    writer.start()
    writer.join(timeout=5.0)

    assert not writer.is_alive()
    assert backend.sent >= 3


class UnpluggedBackend(CountingBackend):
    """2026-10-31 23:36 (2026-10-31 show): the Enttec dropped off USB -- sends fail and reopening fails
    ("no FTDI device on the bus") until it is plugged back in, ``gone_for`` open attempts later."""

    def __init__(self, stop_event, stop_after, fail_at, gone_for) -> None:
        super().__init__(stop_event, stop_after)
        self._fail_at, self._gone_for = fail_at, gone_for
        self.open_attempts = 0
        self.unplugged = False

    def open(self) -> None:
        self.open_attempts += 1
        if self.unplugged:
            self._gone_for -= 1
            if self._gone_for >= 0:
                raise RuntimeError("No Enttec DMX USB Pro found (no FTDI device on the bus)")
            self.unplugged = False
        self.opened = True

    def send(self, universe: bytes) -> None:
        if self.unplugged or (self.sent == self._fail_at and self.open_attempts == 1):
            self.unplugged = True
            raise RuntimeError("write failed: [Errno 6] Device not configured")
        super().send(universe)


def test_writer_reopens_an_adapter_that_left_the_bus_and_came_back():
    clock = FakeClock()
    stop = threading.Event()
    backend = UnpluggedBackend(stop, stop_after=20, fail_at=5, gone_for=8)
    writer = _make_writer(backend, clock, stop, hz=40)

    writer._run_loop()

    assert backend.sent == 20            # frames flow again after the replug
    assert backend.open_attempts >= 10   # it kept trying through the outage
    assert writer.frames_sent == 20 and not backend.unplugged


def test_hardware_mode_keeps_the_enttec_backend_when_none_is_plugged_in(monkeypatch):
    # 2026-11-02 stress test: it used to fall back to log-only for the whole session, so an Enttec
    # plugged in after launch was never used; now the writer's reopen loop picks it up.
    from serial.tools import list_ports

    from livereact.app import App, AppConfig
    from livereact.output.enttec import EnttecSerialBackend
    monkeypatch.setattr(list_ports, "comports", lambda: [])
    app = App.__new__(App)
    app.config = AppConfig(demo=False)
    assert isinstance(app._build_backend(), EnttecSerialBackend)
