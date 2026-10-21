"""Scripted mixer gestures: what the real MIDI listener would have sent.

A gesture file is a JSON list of events. Two shapes:

    {"t": 60.0, "control": "crossfader", "value": 1.0}                 # step
    {"t": 60.0, "control": "crossfader", "from": 0.0, "to": 1.0, "dur": 2.0}   # linear ramp

``control`` names are the semantic ones from inputs/midi_map.py
("crossfader", "fader_a", "eq_low_b", ...). Values are 0.0-1.0. The harness
samples the script once per audio block (~23 ms) and emits a
``MidiControlMessage`` whenever a control's value changes, stamped with the
simulation's virtual clock -- the same thing core/metronome.py sees live.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.messages import MidiControlMessage


@dataclass(frozen=True)
class GestureEvent:
    t: float
    control: str
    start: float
    end: float
    dur: float = 0.0   # 0 -> step

    def value_at(self, now: float) -> float | None:
        if now < self.t:
            return None
        if self.dur <= 0 or now >= self.t + self.dur:
            return self.end
        return self.start + (self.end - self.start) * (now - self.t) / self.dur


class GestureScript:
    def __init__(self, events: list[GestureEvent]) -> None:
        self.events = sorted(events, key=lambda e: e.t)
        self._last: dict[str, float] = {}

    @classmethod
    def load(cls, path: str | Path | None) -> "GestureScript":
        if path is None:
            return cls([])
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        events = []
        for e in raw:
            if "value" in e:
                events.append(GestureEvent(float(e["t"]), e["control"], float(e["value"]), float(e["value"])))
            else:
                events.append(GestureEvent(float(e["t"]), e["control"], float(e["from"]), float(e["to"]),
                                           float(e.get("dur", 0.0))))
        return cls(events)

    def messages_at(self, now: float) -> list[MidiControlMessage]:
        """Messages for any control whose scripted value changed since last call.

        Later events for the same control win (a step after a ramp overrides it).
        """
        current: dict[str, float] = {}
        for e in self.events:
            v = e.value_at(now)
            if v is not None:
                current[e.control] = v          # sorted by t -> latest wins
        out = []
        for control, v in current.items():
            v = max(0.0, min(1.0, v))
            if abs(self._last.get(control, -1.0) - v) > 1e-6:
                self._last[control] = v
                out.append(MidiControlMessage(now, control, v))
        return out
