"""Builders for synthetic rekordbox ANLZ files (DAT/EXT) used by several test modules."""

from __future__ import annotations

import struct

ENTRY = struct.Struct(">HHHxBxBxBHHHxBxBH")


def entry(index, beat, kind, fill=0, beat_fill=0, beat2=0, beat3=0, beat4=0):
    return ENTRY.pack(index, beat, kind, 0, 0, 0, beat2, beat3, beat4, 0, fill, beat_fill)


def pssi_payload(mood, end_beat, bank, entries, *, masked=False):
    from livereact.rekordbox.pssi import unmask_pssi_body

    body = struct.pack(">H6xH2xBx", mood, end_beat, bank) + b"".join(entries)
    if masked:
        body = unmask_pssi_body(body, len(entries))
    return struct.pack(">IH", 24, len(entries)) + body


def pqtz_payload(bpm: float, n_beats: int, first_ms: int = 100):
    beat_ms = 60000.0 / bpm
    ticks = [(1 + i % 4, int(round(bpm * 100)), int(first_ms + i * beat_ms)) for i in range(n_beats)]
    return b"\x00" * 4 + struct.pack(">II", 0x80000, len(ticks)) + b"".join(struct.pack(">HHI", *t) for t in ticks)


def pwv5_payload(heights, reds=None):
    reds = reds or [0] * len(heights)
    ents = [((r & 7) << 13) | ((h & 0x1F) << 2) for h, r in zip(heights, reds)]
    return struct.pack(">III", 2, len(ents), 0x960305) + b"".join(struct.pack(">H", e) for e in ents)


def tag(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack(">II", 12, 12 + len(payload)) + payload


def pmai(*tags: bytes) -> bytes:
    head = b"PMAI" + struct.pack(">II", 28, 0) + b"\x00" * 16
    body = head + b"".join(tags)
    return body[:8] + struct.pack(">I", len(body)) + body[12:]


def high_track(bpm=125.0, duration_s=240.0):
    """A plausible high-mood track: intro 32, up 32, chorus 64, down 32, up 32, chorus 64, outro 32 beats.
    Returns (ext_bytes, dat_bytes)."""
    spec = [(1, 1, 1), (2, 33, 2), (3, 65, 5), (4, 129, 3), (5, 161, 2), (6, 193, 5), (7, 257, 6)]
    entries = [entry(i, b, k) for i, b, k in spec]
    n_beats = int(duration_s * bpm / 60)
    heights = []
    for i in range(int(duration_s * 150)):
        beat = 1 + (i / 150.0 - 0.1) * bpm / 60.0
        in_chorus = 65 <= beat < 129 or 193 <= beat < 257
        heights.append(28 if (193 <= beat < 257) else (20 if in_chorus else 4))
    ext = pmai(tag(b"PPTH", b"\x00" * 8), tag(b"PSSI", pssi_payload(1, 289, 1, entries)),
               tag(b"PWV5", pwv5_payload(heights, [7 if h > 10 else 0 for h in heights])))
    dat = pmai(tag(b"PQTZ", pqtz_payload(bpm, n_beats)))
    return ext, dat
