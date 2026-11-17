"""Resilience 2026-11-16: an audio stream that is alive but carries no sound.

The 2026-11-02 fix reopens a stream that stops delivering blocks. A stream can also keep delivering blocks
that are pure digital silence: the XDJ's USB send routed elsewhere, another interface whose channels 5-6
are unused, or macOS handing an app without Microphone permission zeros instead of an error. Then the
analyzer's messages stay fresh, the show reads energy 0 and sits in its dim look, and the health banner
said nothing. Now the analyzer tracks when it last heard sound, and the banner names the fault while a
deck is playing. Separately, one non-finite block (a driver glitch) used to poison the smoothed level
with NaN for the rest of the night; it is now read as silence.
"""
from __future__ import annotations

import math
import queue
import threading
from types import SimpleNamespace

import numpy as np

from livereact.inputs.audio import BLOCK, SILENCE_PEAK, AudioAnalyzer
from livereact.ui.health import AUDIO_SILENT_S, GRACE_S, health_warnings


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def monotonic(self) -> float:
        return self.t


def _analyzer():
    clock = _Clock()
    a = AudioAnalyzer(queue.Queue(), clock, threading.Event())
    a._setup_bands(np)
    return a, clock


def _feed(a, clock, block, seconds: float) -> None:
    dt = BLOCK / a._sr
    for _ in range(max(1, int(seconds / dt))):
        clock.t += dt
        a._analyze_block(block, 0.1, 0.01, np)


_MUSIC = (0.3 * np.sin(np.linspace(0, 200 * np.pi, BLOCK))).astype(np.float32)
_ZEROS = np.zeros(BLOCK, dtype=np.float32)


def test_the_analyzer_reports_how_long_its_input_has_been_silent():
    a, clock = _analyzer()
    assert a.silent_for(clock.t) == 0.0                 # no block yet: nothing to say
    _feed(a, clock, _MUSIC, 1.0)
    assert a.silent_for(clock.t) < 0.1
    _feed(a, clock, _ZEROS, 20.0)
    assert 19.0 <= a.silent_for(clock.t) <= 21.0
    _feed(a, clock, _MUSIC, 0.1)
    assert a.silent_for(clock.t) < 0.1


def test_a_stream_silent_from_the_first_block_counts_from_that_block():
    a, clock = _analyzer()
    _feed(a, clock, _ZEROS, 12.0)
    assert 11.0 <= a.silent_for(clock.t) <= 13.0


def test_a_very_quiet_but_real_signal_is_not_silence():
    a, clock = _analyzer()
    quiet = (SILENCE_PEAK * 5 * np.sin(np.linspace(0, 200 * np.pi, BLOCK))).astype(np.float32)
    _feed(a, clock, quiet, 20.0)
    assert a.silent_for(clock.t) < 0.1


def test_one_garbled_block_does_not_poison_the_level_for_the_night():
    a, clock = _analyzer()
    _feed(a, clock, _MUSIC, 1.0)
    bad = _MUSIC.copy()
    bad[10] = np.nan
    bad[20] = np.inf
    _feed(a, clock, bad, 0.01)
    _feed(a, clock, _MUSIC, 1.0)
    msgs = []
    while not a.queue.empty():
        msgs.append(a.queue.get_nowait())
    last = msgs[-1]
    for v in (a._rms_smooth, a._peak_hold, a._gain_eff, a._flux_mean, last.rms, last.band_low, last.high_flux):
        assert math.isfinite(v)
    assert 0.0 < last.rms <= 1.0
    assert a.bad_blocks == 1


def test_healthy_blocks_analyse_exactly_as_before():
    """The silence watch and the finite check only read the block: a healthy stream's messages are
    unchanged (same numbers as a second analyzer fed the same blocks)."""
    a, ca = _analyzer()
    b, cb = _analyzer()
    _feed(a, ca, _MUSIC, 0.5)
    _feed(b, cb, _MUSIC, 0.5)
    ma, mb = a.queue.get_nowait(), b.queue.get_nowait()
    assert ma == mb and a.bad_blocks == 0


# ---------------------------------------------------------------- the banner

def _app(silent_for: float | None, pdl=True):
    srcs = []
    if silent_for is not None:
        srcs.append(SimpleNamespace(name="audio", silent_for=lambda now: silent_for))
    if pdl:
        srcs.append(SimpleNamespace(name="prodj-link", last_packet_time=59.0))
    return SimpleNamespace(config=SimpleNamespace(demo=False), writer=SimpleNamespace(is_open=True),
                           failed_inputs={}, started_at=0.0, sources=srcs)


def _snap(pdl=True, audio=True):
    return SimpleNamespace(pdl_fresh=pdl, audio_fresh=audio)


def test_a_playing_deck_with_a_silent_input_raises_the_banner_with_the_fix():
    w = health_warnings(_app(AUDIO_SILENT_S + 1), _snap(), 60.0)
    assert len(w) == 1 and w[0].startswith("AUDIO SILENT")
    assert "USB" in w[0] and "Microphone" in w[0]


def test_short_silence_or_no_deck_playing_says_nothing():
    assert health_warnings(_app(AUDIO_SILENT_S - 1), _snap(), 60.0) == []
    # nothing playing (no beats): a silent master is expected between sets
    assert health_warnings(_app(AUDIO_SILENT_S + 100), _snap(pdl=False), 60.0) == []
    # inside the start-up grace period
    assert health_warnings(_app(AUDIO_SILENT_S + 1), _snap(), GRACE_S - 1) == []


def test_a_dead_stream_still_reads_no_audio_not_silent():
    w = health_warnings(_app(AUDIO_SILENT_S + 1), _snap(audio=False), 60.0)
    assert len(w) == 1 and w[0].startswith("NO AUDIO")


def test_an_audio_source_without_the_watch_is_ignored():
    app = _app(None)
    app.sources.append(SimpleNamespace(name="audio"))
    assert health_warnings(app, _snap(), 60.0) == []
