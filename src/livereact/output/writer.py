"""The DMX writer thread — the one thread that must never stop.

It reads the last complete frame from the double buffer and pushes it to the output
backend at a steady rate (~40 Hz). Every per-iteration failure is contained: on a
backend error it logs, attempts a close+reopen with capped backoff, and keeps
ticking on the last good frame. No exception is allowed to escape the loop, so the
lights keep running even when everything upstream has failed. (Analysis: docs/05
§5.1, NFR-5.)
"""

from __future__ import annotations

import logging
import threading

from ..clock import Clock, LoopTimer
from ..core.frame_buffer import DmxFrameBuffer
from .base import OutputBackend

log = logging.getLogger(__name__)



LOG_EVERY_ERRORS = 100   # while an output stays down, log one failure in this many (~every 3 min)

class DmxWriter(threading.Thread):
    """Pulls published frames and writes them to a backend at a fixed rate."""

    def __init__(
        self,
        frame_buffer: DmxFrameBuffer,
        backend: OutputBackend,
        clock: Clock,
        stop_event: threading.Event,
        hz: float = 40.0,
        max_backoff: float = 2.0,
    ) -> None:
        super().__init__(name="dmx-writer", daemon=False)
        self.frame_buffer = frame_buffer
        self.backend = backend
        self.clock = clock
        self.stop_event = stop_event
        self.hz = hz
        self._max_backoff = max_backoff
        self._frames_sent = 0
        self._consecutive_errors = 0
        self._is_open = False

    @property
    def frames_sent(self) -> int:
        return self._frames_sent

    @property
    def is_open(self) -> bool:
        """True once the backend's ``open()`` has succeeded (console health indicator)."""
        return self._is_open

    def run(self) -> None:  # pragma: no cover - thin wrapper around _run_loop
        # Outer guard: even an unexpected error in the loop scaffolding itself
        # must not kill the thread silently without a trace.
        try:
            self._run_loop()
        except Exception:  # noqa: BLE001 - last-resort safety net
            log.exception("DMX writer loop crashed unexpectedly")
        finally:
            self._safe_close()

    def _run_loop(self) -> None:
        timer = LoopTimer(self.clock, self.hz)
        self._safe_open()
        while not self.stop_event.is_set():
            try:
                frame = self.frame_buffer.latest()
                self.backend.send(frame)
                self._frames_sent += 1
                if self._consecutive_errors:
                    log.warning("Output backend %r is back after %d failed sends",
                                self.backend.name, self._consecutive_errors)
                self._consecutive_errors = 0
            except Exception:  # noqa: BLE001 - writer survives all backend faults
                self._handle_send_error()
            timer.wait()
        self._safe_close()

    def _loud(self) -> bool:
        """Log this failure? The first few and then one in LOG_EVERY_ERRORS: an adapter that is
        unplugged for the whole set retries every ~2 s (2026-11-02 stress test)."""
        return self._consecutive_errors <= 3 or self._consecutive_errors % LOG_EVERY_ERRORS == 0

    def _safe_open(self) -> None:
        try:
            self.backend.open()
            self._is_open = True
        except Exception:  # noqa: BLE001
            self._is_open = False
            if self._loud():
                log.exception("Failed to open output backend %r", self.backend.name)

    def _safe_close(self) -> None:
        if not self._is_open:
            return
        try:
            self.backend.close()
        except Exception:  # noqa: BLE001
            log.exception("Error closing output backend %r", self.backend.name)
        finally:
            self._is_open = False

    def _handle_send_error(self) -> None:
        self._consecutive_errors += 1
        if self._loud():
            log.warning(
                "Output send failed (%d in a row) on backend %r; attempting reopen",
                self._consecutive_errors,
                self.backend.name,
                exc_info=True,
            )
        # Close + reopen, then back off (capped) so a dead device doesn't spin hot.
        self._safe_close()
        self._safe_open()
        backoff = min(self._max_backoff, 0.05 * self._consecutive_errors)
        self.clock.sleep(backoff)
