"""Parse rekordbox ANLZ files: the PSSI song-structure table and the PQTZ beat grid.

Format reference: Deep Symmetry's crate-digger ``rekordbox_anlz.ksy`` (the
reverse-engineered, widely used description; Beat Link Trigger's phrase
triggers are built on it). Everything is big-endian. Only ``struct`` is used so
this module imports with no third-party dependency.

ANLZ container
  ``PMAI`` | head_size u4 | file_size u4 | 4 x u4 unknown | tags...
  tag: fourcc(4) | len_header u4 | len_tag u4 (incl. this 12-byte header) | payload

PSSI tag payload
  len_entry_bytes u4 (24) | len_entries u2 | body
  body (XOR-masked in some exports -- detected when the stored mood u2 > 20;
  valid moods are 1..3): mood u2 | 6 skip | end_beat u2 | 2 skip | raw_bank u1 |
  1 skip | len_entries x 24-byte entries:
    index u2 | beat u2 | kind u2 | x | k1 u1 | x | k2 u1 | x | b u1 |
    beat2 u2 | beat3 u2 | beat4 u2 | x | k3 u1 | x | fill u1 | beat_fill u2
  mask: body[i] ^= (PSSI_MASK[i % 19] + len_entries) & 0xff

Beats are indices on the rekordbox beat grid (the same grid the CDJ status
packet's ``beat_count`` walks). Whether the two agree exactly or differ by one
is a hardware measurement (plan: Gate 1, ``PSSI_BEAT_OFFSET``) -- this module
does not apply any offset.

Verified 2026-10-25 against the 340 PSSI-bearing ``.EXT`` files in the local
rekordbox library on this Mac (all unmasked, entry size 24). The masked path is
covered by a synthetic round-trip test only until a masked USB export is seen.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Iterator

log = logging.getLogger(__name__)

PSSI_MASK = bytes.fromhex("CBE1EEFAE5EEADEEE9D2E9EBE1E9F3E8E9F4E1")  # 19 bytes

MOOD_HIGH, MOOD_MID, MOOD_LOW = 1, 2, 3
MOOD_NAMES = {MOOD_HIGH: "high", MOOD_MID: "mid", MOOD_LOW: "low"}

# Phrase kind -> label, per mood. Unknown kinds come back as "kindN".
_KINDS_HIGH = {1: "intro", 2: "up", 3: "down", 5: "chorus", 6: "outro"}
_KINDS_MID = {1: "intro", 2: "verse1", 3: "verse2", 4: "verse3", 5: "verse4", 6: "verse5",
              7: "verse6", 8: "bridge", 9: "chorus", 10: "outro"}
_KINDS_LOW = {1: "intro", 2: "verse1", 3: "verse1b", 4: "verse1c", 5: "verse2", 6: "verse2b",
              7: "verse2c", 8: "bridge", 9: "chorus", 10: "outro"}
_KINDS_BY_MOOD = {MOOD_HIGH: _KINDS_HIGH, MOOD_MID: _KINDS_MID, MOOD_LOW: _KINDS_LOW}

# rekordbox "bank" (the user's lighting style choice) -- informational only.
BANK_NAMES = {0: "default", 1: "cool", 2: "natural", 3: "hot", 4: "subtle", 5: "warm",
              6: "vivid", 7: "club1", 8: "club2"}

_FILE_MAGIC = b"PMAI"
_TAG_HEADER = struct.Struct(">4sII")
_PSSI_HEAD = struct.Struct(">IH")             # len_entry_bytes, len_entries
_PSSI_BODY_HEAD = struct.Struct(">H6xH2xBx")  # mood, end_beat, raw_bank  (14 bytes)
_PSSI_ENTRY = struct.Struct(">HHHxBxBxBHHHxBxBH")  # 24 bytes
assert _PSSI_ENTRY.size == 24
assert _PSSI_BODY_HEAD.size == 14


def kind_label(mood: int, kind: int) -> str:
    return _KINDS_BY_MOOD.get(mood, _KINDS_MID).get(kind, f"kind{kind}")


@dataclass(frozen=True)
class Phrase:
    index: int        # 1-based phrase number
    beat: int         # first beat of the phrase (rekordbox beat-grid index)
    end_beat: int     # first beat of the next phrase, or the table's end_beat for the last one
    kind: int         # raw PSSI kind (meaning depends on mood)
    label: str        # kind_label(mood, kind)
    fill: int = 0     # nonzero: a fill-in sits at the end of this phrase
    beat_fill: int = 0
    k1: int = 0
    k2: int = 0
    k3: int = 0
    b: int = 0
    beat2: int = 0    # high-mood "Up 3" phrases: extra beat markers inside the phrase
    beat3: int = 0
    beat4: int = 0

    @property
    def length(self) -> int:
        return max(0, self.end_beat - self.beat)


@dataclass(frozen=True)
class PhraseTable:
    mood: int
    end_beat: int
    bank: int
    phrases: tuple[Phrase, ...]

    @property
    def mood_name(self) -> str:
        return MOOD_NAMES.get(self.mood, f"mood{self.mood}")

    @property
    def bank_name(self) -> str:
        return BANK_NAMES.get(self.bank, f"bank{self.bank}")

    def at_beat(self, beat: int) -> tuple[Phrase | None, Phrase | None]:
        """(current phrase, next phrase) for a beat-grid index.

        Before the first phrase: (None, first). Past ``end_beat``: (None, None).
        A phrase owns beats ``[beat, end_beat)`` so a boundary beat belongs to the
        new phrase.
        """
        if not self.phrases:
            return None, None
        if beat < self.phrases[0].beat:
            return None, self.phrases[0]
        if beat >= self.end_beat:
            return None, None
        for i, p in enumerate(self.phrases):
            if p.beat <= beat < p.end_beat:
                nxt = self.phrases[i + 1] if i + 1 < len(self.phrases) else None
                return p, nxt
        return None, None

    def beats_to_next(self, beat: int) -> int:
        """Beats until the next section boundary; -1 when there is none ahead.

        Inside the last phrase this counts down to the table's end (the end of
        the analyzed structure), since that is still a change the lights care about.
        """
        cur, nxt = self.at_beat(beat)
        if nxt is not None:
            return nxt.beat - beat
        if cur is not None:
            return cur.end_beat - beat
        return -1


def iter_anlz_tags(data: bytes) -> Iterator[tuple[str, int, int, bytes]]:
    """Yield (fourcc, len_header, len_tag, payload) for each tag in a PMAI file.

    Tolerant: stops (never raises) on a truncated or malformed tail.
    """
    if len(data) < 8 or data[:4] != _FILE_MAGIC:
        return
    head_size = struct.unpack_from(">I", data, 4)[0]
    pos = head_size if 8 <= head_size <= len(data) else 28
    while pos + _TAG_HEADER.size <= len(data):
        fourcc, len_header, len_tag = _TAG_HEADER.unpack_from(data, pos)
        if len_tag < _TAG_HEADER.size:
            return
        end = min(pos + len_tag, len(data))
        yield fourcc.decode("ascii", errors="replace"), len_header, len_tag, data[pos + 12:end]
        pos += len_tag


def find_tag(data: bytes, fourcc: str) -> bytes | None:
    for name, _lh, _lt, payload in iter_anlz_tags(data):
        if name == fourcc:
            return payload
    return None


def unmask_pssi_body(body: bytes, len_entries: int) -> bytes:
    return bytes(b ^ ((PSSI_MASK[i % len(PSSI_MASK)] + len_entries) & 0xFF)
                 for i, b in enumerate(body))


def parse_pssi_payload(payload: bytes) -> PhraseTable | None:
    """Parse the payload of a PSSI tag (bytes after the 12-byte tag header)."""
    if len(payload) < _PSSI_HEAD.size + _PSSI_BODY_HEAD.size:
        log.debug("PSSI payload too short (%d bytes)", len(payload))
        return None
    len_entry_bytes, len_entries = _PSSI_HEAD.unpack_from(payload, 0)
    body = payload[_PSSI_HEAD.size:]
    stored_mood = struct.unpack_from(">H", body, 0)[0]
    if stored_mood > 20:
        body = unmask_pssi_body(body, len_entries)
    mood, end_beat, raw_bank = _PSSI_BODY_HEAD.unpack_from(body, 0)
    if mood not in MOOD_NAMES:
        log.debug("PSSI mood %d not in 1..3 after unmask -- unknown layout or mask", mood)
        return None
    if len_entry_bytes < _PSSI_ENTRY.size:
        log.debug("PSSI entry size %d unsupported", len_entry_bytes)
        return None
    need = _PSSI_BODY_HEAD.size + len_entries * len_entry_bytes
    if len(body) < need:
        log.debug("PSSI truncated: %d entries need %d bytes, have %d", len_entries, need, len(body))
        return None

    raw: list[tuple] = []
    off = _PSSI_BODY_HEAD.size
    for _ in range(len_entries):
        raw.append(_PSSI_ENTRY.unpack_from(body, off))
        off += len_entry_bytes
    raw.sort(key=lambda e: (e[1], e[0]))  # by beat, then index (normally already ordered)

    phrases: list[Phrase] = []
    for i, (index, beat, kind, k1, k2, b, beat2, beat3, beat4, k3, fill, beat_fill) in enumerate(raw):
        nxt_beat = raw[i + 1][1] if i + 1 < len(raw) else end_beat
        phrases.append(Phrase(index=index, beat=beat, end_beat=max(beat, nxt_beat), kind=kind,
                              label=kind_label(mood, kind), fill=fill, beat_fill=beat_fill,
                              k1=k1, k2=k2, k3=k3, b=b, beat2=beat2, beat3=beat3, beat4=beat4))
    return PhraseTable(mood=mood, end_beat=end_beat, bank=raw_bank, phrases=tuple(phrases))


def parse_pssi(anlz_bytes: bytes) -> PhraseTable | None:
    """Phrase table from a whole ANLZ file (normally the ``.EXT``), or None."""
    try:
        payload = find_tag(anlz_bytes, "PSSI")
        if payload is None:
            return None
        return parse_pssi_payload(payload)
    except (struct.error, ValueError) as exc:
        log.debug("PSSI parse failed: %s", exc)
        return None


def parse_pqtz(anlz_bytes: bytes) -> list[tuple[int, float, int]] | None:
    """Beat grid from the ``.DAT``: list of (beat_in_bar 1-4, bpm, time_ms), one per beat.

    PQTZ payload: 4 skip | u4 unknown (0x80000) | count u4 | count x (beat u2, bpm_100 u2, time u4).
    Entry N (1-based) is beat-grid index N.
    """
    try:
        payload = find_tag(anlz_bytes, "PQTZ")
        if payload is None or len(payload) < 12:
            return None
        count = struct.unpack_from(">I", payload, 8)[0]
        entry = struct.Struct(">HHI")
        if len(payload) < 12 + count * entry.size:
            return None
        out = []
        off = 12
        for _ in range(count):
            beat, bpm_100, time_ms = entry.unpack_from(payload, off)
            out.append((beat, bpm_100 / 100.0, time_ms))
            off += entry.size
        return out
    except (struct.error, ValueError) as exc:
        log.debug("PQTZ parse failed: %s", exc)
        return None


def beat_to_seconds(beat: int, grid: list[tuple[int, float, int]] | None, *,
                    bpm: float | None = None, first_downbeat_s: float = 0.0) -> float:
    """Seconds into the track for a beat-grid index (1-based).

    Prefers the real grid (handles tempo changes); falls back to a constant grid.
    """
    if grid and 1 <= beat <= len(grid):
        return grid[beat - 1][2] / 1000.0
    if grid and beat > len(grid):
        last_t = grid[-1][2] / 1000.0
        last_bpm = grid[-1][1] or (bpm or 120.0)
        return last_t + (beat - len(grid)) * 60.0 / last_bpm
    if not bpm or bpm <= 0:
        raise ValueError("beat_to_seconds needs a beat grid or a bpm")
    return first_downbeat_s + (beat - 1) * 60.0 / bpm


def format_table(table: PhraseTable, *, grid: list[tuple[int, float, int]] | None = None,
                 bpm: float | None = None) -> str:
    """Human-readable dump for the probe/CLI."""
    lines = [f"mood={table.mood_name} bank={table.bank_name} end_beat={table.end_beat} "
             f"phrases={len(table.phrases)}"]
    for p in table.phrases:
        t = ""
        if grid or bpm:
            try:
                t = f"  @{beat_to_seconds(p.beat, grid, bpm=bpm):7.2f}s"
            except ValueError:
                t = ""
        extra = f"  fill@{p.beat_fill}" if p.fill else ""
        lines.append(f"  {p.index:2d}  beat {p.beat:5d}-{p.end_beat:5d} ({p.length:3d})  {p.label:8s}{t}{extra}")
    return "\n".join(lines)
