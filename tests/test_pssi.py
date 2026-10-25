"""rekordbox ANLZ / PSSI parser: synthetic containers, masked and unmasked."""

from __future__ import annotations

import struct

from livereact.rekordbox import mapping
from livereact.rekordbox.pssi import (
    PSSI_MASK,
    MOOD_HIGH,
    MOOD_LOW,
    MOOD_MID,
    beat_to_seconds,
    format_table,
    iter_anlz_tags,
    kind_label,
    parse_pqtz,
    parse_pssi,
    unmask_pssi_body,
)

ENTRY = struct.Struct(">HHHxBxBxBHHHxBxBH")


def _entry(index, beat, kind, fill=0, beat_fill=0):
    return ENTRY.pack(index, beat, kind, 0, 0, 0, 0, 0, 0, 0, fill, beat_fill)


def _pssi_payload(mood, end_beat, bank, entries, *, masked=False):
    body = struct.pack(">H6xH2xBx", mood, end_beat, bank) + b"".join(entries)
    if masked:
        body = unmask_pssi_body(body, len(entries))  # XOR is its own inverse
    return struct.pack(">IH", 24, len(entries)) + body


def _tag(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack(">II", 12, 12 + len(payload)) + payload


def _pmai(*tags: bytes) -> bytes:
    head = b"PMAI" + struct.pack(">II", 28, 0) + b"\x00" * 16
    body = head + b"".join(tags)
    return body[:8] + struct.pack(">I", len(body)) + body[12:]


HIGH_ENTRIES = [_entry(1, 1, 1), _entry(2, 33, 2), _entry(3, 65, 5, fill=1, beat_fill=125), _entry(4, 129, 6)]


def _high_file(masked=False):
    return _pmai(_tag(b"PPTH", b"\x00" * 8), _tag(b"PSSI", _pssi_payload(MOOD_HIGH, 161, 1, HIGH_ENTRIES, masked=masked)))


def test_iter_tags_walks_container():
    names = [t[0] for t in iter_anlz_tags(_high_file())]
    assert names == ["PPTH", "PSSI"]


def test_parse_unmasked_high_table():
    table = parse_pssi(_high_file())
    assert table is not None
    assert table.mood == MOOD_HIGH and table.mood_name == "high"
    assert table.end_beat == 161 and table.bank == 1 and table.bank_name == "cool"
    labels = [p.label for p in table.phrases]
    assert labels == ["intro", "up", "chorus", "outro"]
    assert [p.beat for p in table.phrases] == [1, 33, 65, 129]
    assert [p.end_beat for p in table.phrases] == [33, 65, 129, 161]
    assert table.phrases[2].fill == 1 and table.phrases[2].beat_fill == 125


def test_masked_and_unmasked_give_identical_tables():
    assert parse_pssi(_high_file(masked=True)) == parse_pssi(_high_file(masked=False))


def test_mask_is_derived_from_entry_count():
    body = bytes(range(40))
    m2 = unmask_pssi_body(body, 2)
    m3 = unmask_pssi_body(body, 3)
    assert m2 != m3
    assert unmask_pssi_body(m2, 2) == body
    assert len(PSSI_MASK) == 19


def test_at_beat_boundaries():
    table = parse_pssi(_high_file())
    cur, nxt = table.at_beat(0)
    assert cur is None and nxt.label == "intro"
    cur, nxt = table.at_beat(32)
    assert cur.label == "intro" and nxt.label == "up"
    cur, nxt = table.at_beat(33)          # boundary beat belongs to the new phrase
    assert cur.label == "up" and nxt.label == "chorus"
    cur, nxt = table.at_beat(160)
    assert cur.label == "outro" and nxt is None
    assert table.at_beat(161) == (None, None)
    assert table.at_beat(999) == (None, None)


def test_beats_to_next():
    table = parse_pssi(_high_file())
    assert table.beats_to_next(33) == 32       # up -> chorus at 65
    assert table.beats_to_next(64) == 1
    assert table.beats_to_next(129) == 32      # last phrase counts down to end_beat
    assert table.beats_to_next(161) == -1
    assert table.beats_to_next(0) == 1         # before the first phrase: 1 beat to intro


def test_truncated_table_returns_none():
    good = _high_file()
    cut = good[: len(good) - 30]
    assert parse_pssi(cut) is None


def test_not_an_anlz_file_returns_none():
    assert parse_pssi(b"hello") is None
    assert parse_pssi(b"") is None
    assert list(iter_anlz_tags(b"PMAI")) == []


def test_file_without_pssi_returns_none():
    assert parse_pssi(_pmai(_tag(b"PPTH", b"\x00" * 8))) is None


def test_kind_labels_per_mood():
    assert kind_label(MOOD_HIGH, 5) == "chorus" and kind_label(MOOD_HIGH, 2) == "up"
    assert kind_label(MOOD_MID, 9) == "chorus" and kind_label(MOOD_MID, 8) == "bridge" and kind_label(MOOD_MID, 3) == "verse2"
    assert kind_label(MOOD_LOW, 3) == "verse1b" and kind_label(MOOD_LOW, 10) == "outro"
    assert kind_label(MOOD_HIGH, 4) == "kind4"


def test_mid_mood_table_and_section_mapping():
    entries = [_entry(1, 1, 1), _entry(2, 17, 2), _entry(3, 49, 8), _entry(4, 65, 9), _entry(5, 129, 10)]
    table = parse_pssi(_pmai(_tag(b"PSSI", _pssi_payload(MOOD_MID, 145, 0, entries))))
    assert [p.label for p in table.phrases] == ["intro", "verse1", "bridge", "chorus", "outro"]
    kinds = [s[3] for s in mapping.sections_of(table)]
    assert kinds == ["intro", "verse", "build", "drop", "outro"]
    assert mapping.bucket_for_section("drop") == "drop"
    assert mapping.bucket_for_section("verse") == "break"
    assert mapping.timeline_kind_for_section("verse") == "groove"
    assert mapping.section_for(MOOD_HIGH, "down") == "breakdown"
    assert mapping.section_for(MOOD_HIGH, "up") == "build"
    assert mapping.section_for(MOOD_LOW, "verse2c") == "verse"


def test_pqtz_and_beat_to_seconds():
    ticks = [(1 + i % 4, 12500, 1000 + i * 480) for i in range(8)]
    payload = b"\x00" * 4 + struct.pack(">II", 0x80000, len(ticks)) + b"".join(struct.pack(">HHI", *t) for t in ticks)
    grid = parse_pqtz(_pmai(_tag(b"PQTZ", payload)))
    assert len(grid) == 8 and grid[0] == (1, 125.0, 1000)
    assert beat_to_seconds(1, grid) == 1.0
    assert abs(beat_to_seconds(10, grid) - (1.0 + 7 * 0.48 + 2 * 0.48)) < 1e-6   # past the grid: extrapolate
    assert abs(beat_to_seconds(5, None, bpm=120.0, first_downbeat_s=0.5) - 2.5) < 1e-9
    assert parse_pqtz(_pmai(_tag(b"PPTH", b""))) is None


def test_format_table_is_printable():
    text = format_table(parse_pssi(_high_file()), bpm=125.0)
    assert "chorus" in text and "fill@125" in text and "mood=high" in text
