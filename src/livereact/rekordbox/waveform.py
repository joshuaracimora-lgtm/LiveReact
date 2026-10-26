"""rekordbox colour waveform (PWV5 in the .EXT): a loudness + low/mid/high profile of the whole track.

rekordbox precomputes this for the player's display: one u16 per half-frame
(150 per second) -- bits 15-13 red, 12-10 green, 9-7 blue (0-7 each; red tracks
the low band), bits 6-2 height (0-31, the drawn bar height = loudness). Layout
per Deep Symmetry's crate-digger / Beat Link ``WaveformDetail``; CONFIRMED on
this Mac's rekordbox library 2026-10-25: the entry count equals duration x 150,
heights span 0-31, and phrase sections read sensibly (one track: intro mean
0.9/31, chorus 15.8/31, red 0.18 vs 5.7).

This is what lets the planner rank a track's sections at load time without
touching the audio: the file is ~70 KB and already on the USB.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .pssi import find_tag

RATE_HZ = 150.0


@dataclass(frozen=True)
class ColorWaveform:
    heights: tuple[int, ...]   # 0..31 per entry
    reds: tuple[int, ...]      # 0..7 (low band)
    greens: tuple[int, ...]    # 0..7 (mids)
    blues: tuple[int, ...]     # 0..7 (highs)
    rate_hz: float = RATE_HZ

    def __len__(self) -> int:
        return len(self.heights)

    @property
    def duration_s(self) -> float:
        return len(self.heights) / self.rate_hz

    def window(self, t0_s: float, t1_s: float) -> tuple[int, int]:
        i0 = max(0, min(len(self.heights), int(t0_s * self.rate_hz)))
        i1 = max(i0, min(len(self.heights), int(t1_s * self.rate_hz)))
        return i0, i1

    def energy(self, t0_s: float, t1_s: float) -> float:
        """Mean height over [t0, t1) as 0..1; 0.0 for an empty window."""
        i0, i1 = self.window(t0_s, t1_s)
        if i1 <= i0:
            return 0.0
        return sum(self.heights[i0:i1]) / (31.0 * (i1 - i0))

    def peak(self, t0_s: float, t1_s: float, quantile: float = 0.9) -> float:
        """A high quantile of height over the window as 0..1 (the hits, not the average)."""
        i0, i1 = self.window(t0_s, t1_s)
        if i1 <= i0:
            return 0.0
        seg = sorted(self.heights[i0:i1])
        return seg[min(len(seg) - 1, int(len(seg) * quantile))] / 31.0

    def low_share(self, t0_s: float, t1_s: float) -> float:
        """Red / (red+green+blue) over the window: how bass-led the section is (0..1)."""
        i0, i1 = self.window(t0_s, t1_s)
        if i1 <= i0:
            return 0.0
        r = sum(self.reds[i0:i1])
        tot = r + sum(self.greens[i0:i1]) + sum(self.blues[i0:i1])
        return r / tot if tot else 0.0


def parse_pwv5(ext_bytes: bytes) -> ColorWaveform | None:
    """The colour waveform detail from a whole ``.EXT`` file, or None."""
    payload = find_tag(ext_bytes, "PWV5")
    if payload is None or len(payload) < 12:
        return None
    try:
        word, count, _unknown = struct.unpack_from(">III", payload, 0)
    except struct.error:
        return None
    if word != 2 or count <= 0 or len(payload) < 12 + 2 * count:
        return None
    entries = struct.unpack_from(f">{count}H", payload, 12)
    return ColorWaveform(
        heights=tuple((e >> 2) & 0x1F for e in entries),
        reds=tuple((e >> 13) & 0x7 for e in entries),
        greens=tuple((e >> 10) & 0x7 for e in entries),
        blues=tuple((e >> 7) & 0x7 for e in entries),
    )
