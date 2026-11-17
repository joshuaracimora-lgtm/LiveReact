"""Roadmap item 64 [from UX_BACKLOG 3]: a numeric --audio-device that points at a device with no inputs.

Device indexes move when a phone mic or headset appears; `--audio-device 1` was the laptop mic one minute
and the speakers (0 inputs) the next, and start() died on `max() of an empty iterable`, a message no
operator can act on. Now start() raises AudioInputError naming the device and listing the input-capable
devices by name, which the app's failed-inputs path shows in the NO AUDIO INPUT banner.
"""
from __future__ import annotations

import queue
import sys
import threading
import types

import pytest

from livereact.clock import RealClock
from livereact.inputs.audio import AudioAnalyzer, AudioInputError

MIC = {"name": "MacBook Pro Microphone", "max_input_channels": 1, "default_samplerate": 48000.0}
SPEAKERS = {"name": "MacBook Pro Speakers", "max_input_channels": 0, "default_samplerate": 48000.0}
XDJ = {"name": "XDJ-XZ", "max_input_channels": 8, "default_samplerate": 44100.0}


class _FakeSd(types.ModuleType):
    def __init__(self, devices) -> None:
        super().__init__("sounddevice")
        self.devices = devices
        self.opened = []
        sd = self

        class InputStream:
            def __init__(self, device, callback, **kw):
                sd.opened.append(device)
                self.latency = 0.03

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        self.InputStream = InputStream

    def query_devices(self, device=None):
        return list(self.devices) if device is None else self.devices[device]


@pytest.fixture
def sd(monkeypatch):
    fake = _FakeSd([MIC, SPEAKERS, XDJ])
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def _analyzer(device):
    stop = threading.Event()
    return AudioAnalyzer(queue.Queue(), RealClock(), stop, device=device), stop


def test_an_index_with_no_inputs_says_so_and_lists_the_inputs_by_name(sd):
    a, stop = _analyzer("1")
    try:
        with pytest.raises(AudioInputError) as exc:
            a.start()
        msg = str(exc.value)
        assert "MacBook Pro Speakers" in msg and "no input" in msg
        assert "MacBook Pro Microphone" in msg and "XDJ-XZ" in msg
        assert "Speakers" not in msg.split("available", 1)[-1]   # the list holds only devices that can record
        assert "name" in msg.lower()                              # and recommends a name over an index
        assert sd.opened == []
    finally:
        stop.set()
        a.stop()


def test_an_index_with_inputs_still_opens(sd):
    a, stop = _analyzer("0")
    try:
        a.start()
        assert sd.opened == [0]
    finally:
        stop.set()
        a.stop()
