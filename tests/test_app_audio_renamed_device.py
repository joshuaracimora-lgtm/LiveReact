"""inputs/audio.py: an audio device that comes back from a USB re-enumeration under another NAME (2026-11-18).

After the first open the analyzer looks the device up again by its exact name. Windows renames a USB audio
device that re-enumerates (often on another port) -- "XDJ-XZ" comes back as "2- XDJ-XZ" -- so the exact
lookup failed on every retry and LiveReact stayed deaf until a restart, with only the NO AUDIO banner.
The fakes are test_audio_recovery.py's.
"""
from __future__ import annotations

import queue
import threading
import time

from test_audio_recovery import MIC, XDJ, _feed, _wait, sd  # noqa: F401 - sd is the fixture

from livereact.clock import RealClock
from livereact.inputs.audio import AudioAnalyzer

RENAMED_XDJ = {"name": "2- XDJ-XZ", "max_input_channels": 8, "default_samplerate": 44100.0}
OTHER = {"name": "USB Audio CODEC", "max_input_channels": 2, "default_samplerate": 44100.0}


def _analyzer(device=None):
    q, stop = queue.Queue(), threading.Event()
    return AudioAnalyzer(q, RealClock(), stop, device=device), q, stop


def test_an_auto_detected_xdj_that_comes_back_renamed_is_reopened(sd):
    a, q, stop = _analyzer()
    a.start()
    try:
        first = sd.streams[-1]
        _feed(first)
        assert _wait(lambda: not q.empty())
        sd.devices = [MIC, OTHER, RENAMED_XDJ]       # stream dies; the XDJ re-enumerates under a new name
        assert _wait(lambda: a.reopens == 1)
        second = sd.streams[-1]
        assert second is not first and second.device == 2
        while not q.empty():
            q.get_nowait()
        _feed(second)
        assert _wait(lambda: not q.empty())          # levels flow again
    finally:
        stop.set()
        a.stop()


def test_a_renamed_device_never_falls_back_to_a_non_xdj_input(sd):
    a, q, stop = _analyzer()
    a.start()
    try:
        _feed(sd.streams[-1])
        sd.devices = [MIC, OTHER]                    # the XDJ is gone; only other inputs remain
        time.sleep(1.5)
        assert a.reopens == 0
        assert all(s.device == 1 for s in sd.streams)   # only the first (XDJ) stream, never MIC / OTHER
        sd.devices = [MIC, OTHER, RENAMED_XDJ]
        assert _wait(lambda: a.reopens == 1) and sd.streams[-1].device == 2
    finally:
        stop.set()
        a.stop()


def test_an_explicit_index_is_not_re_resolved_to_another_device(sd):
    # `--audio-device 1` named a position, not a device: a renamed device elsewhere is not assumed to be it
    a, q, stop = _analyzer(device=1)
    a.start()
    try:
        _feed(sd.streams[-1])
        sd.devices = [MIC, None, RENAMED_XDJ]
        time.sleep(1.5)
        assert a.reopens == 0 and len(sd.streams) == 1
    finally:
        stop.set()
        a.stop()


def test_a_healthy_device_is_still_found_by_its_exact_name_first(sd):
    # two XDJs, the opened one moves index: the exact name wins over "the first XDJ on the bus"
    second_xdj = {"name": "XDJ-RX3", "max_input_channels": 8, "default_samplerate": 44100.0}
    a, q, stop = _analyzer()
    a.start()
    try:
        _feed(sd.streams[-1])
        sd.devices = [MIC, second_xdj, XDJ]
        assert _wait(lambda: a.reopens == 1)
        assert sd.streams[-1].device == 2
    finally:
        stop.set()
        a.stop()
