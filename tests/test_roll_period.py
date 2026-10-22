"""core/roll_period.py: a snare roll's START as a step in sub-beat periodicity
of the snare-body band (see the module docstring for the study behind it)."""
from __future__ import annotations

import random

import pytest

from livereact.core import roll_period as rp
from livereact.core.roll_period import RollPeriodDetector

BEAT = 60.0 / 130.0          # 0.4615 s
DT = 1024 / 44100            # one audio block
BLOCKS_PER_16TH = round(BEAT / 4 / DT)   # 5


def _feed(det, t, seconds, level_fn):
    """Advance `seconds` of blocks; level_fn(block_index) -> band level. Returns
    (t, first start time or None)."""
    first = None
    n = int(round(seconds / DT))
    for i in range(n):
        t += DT
        if det.on_block(t, level_fn(i), BEAT) and first is None:
            first = t
    return t, first


def _noise(rng, base=1.0, spread=0.3):
    return lambda i: max(0.0, base + rng.uniform(-spread, spread))


def _roll(rng, base=1.0, hit=3.0, every=BLOCKS_PER_16TH):
    """16th-note snare hits on top of the same noise floor."""
    return lambda i: (hit if i % every == 0 else base) + rng.uniform(-0.2, 0.2)


def test_a_roll_after_a_steady_section_starts_within_about_a_beat_and_a_half():
    rng = random.Random(1)
    det = RollPeriodDetector()
    t, s = _feed(det, 0.0, 12 * 4 * BEAT, _noise(rng))      # 12 bars of non-periodic music
    assert s is None and det.base is not None and det.base < rp.ROLL_PERIOD_BASE_MAX
    t0 = t
    t, s = _feed(det, t, 4 * 4 * BEAT, _roll(rng))
    assert s is not None, "a 16th-note roll never read as a roll start"
    latency_beats = (s - t0) / BEAT
    # window (2 beats) must mostly contain the roll, then the hold (1 beat):
    # the study's real rolls landed at +1.2..+2.2 beats; synthetic is cleaner.
    assert 0.5 <= latency_beats <= 2.5, latency_beats
    assert det.score >= rp.ROLL_PERIOD_ON


def test_8th_note_rolls_count_too():
    rng = random.Random(2)
    det = RollPeriodDetector()
    t, _ = _feed(det, 0.0, 12 * 4 * BEAT, _noise(rng))
    t, s = _feed(det, t, 4 * 4 * BEAT, _roll(rng, every=2 * BLOCKS_PER_16TH))
    assert s is not None


def test_a_texture_that_is_periodic_from_the_start_never_starts_a_roll():
    """Sweet Nothing's piano-house 8th bassline sits at 0.3-0.7 for twenty bars:
    no step up, so no roll start -- ever."""
    rng = random.Random(3)
    det = RollPeriodDetector()
    t, s = _feed(det, 0.0, 24 * 4 * BEAT, _roll(rng, every=2 * BLOCKS_PER_16TH))
    assert s is None
    assert det.score >= rp.ROLL_PERIOD_ON          # it IS periodic ...
    assert det.base is not None and det.base >= rp.ROLL_PERIOD_BASE_MAX   # ... and so was the reference


def test_a_track_is_blind_for_its_first_bars():
    """A roll is relative to what the track has been doing: with less than
    ROLL_PERIOD_BASE_MIN_BARS of reference nothing can start (a song that
    opens on a roll is the D key's job, like a first build is the C key's)."""
    rng = random.Random(4)
    det = RollPeriodDetector()
    t, s = _feed(det, 0.0, 2 * 4 * BEAT, _noise(rng))
    t, s = _feed(det, t, 2 * 4 * BEAT, _roll(rng))
    assert s is None and det.base is None


def test_near_silent_periodicity_is_ignored():
    """Namerakana's three-beat gap before its fill read 0.56: autocorrelation is
    scale-free, so the band must carry real level against its recent median."""
    rng = random.Random(5)
    det = RollPeriodDetector()
    t, _ = _feed(det, 0.0, 12 * 4 * BEAT, _noise(rng, base=2.0, spread=0.5))
    quiet_roll = lambda i: (0.08 if i % BLOCKS_PER_16TH == 0 else 0.02) + rng.uniform(-0.004, 0.004)  # periodic, ~1/50 of the level
    t, s = _feed(det, t, 3 * 4 * BEAT, quiet_roll)
    assert s is None
    assert det.score >= rp.ROLL_PERIOD_ON and not det.hot


def test_one_roll_starts_once_and_a_later_roll_starts_again():
    rng = random.Random(6)
    det = RollPeriodDetector()
    starts = []
    t = 0.0
    t, _ = _feed(det, t, 12 * 4 * BEAT, _noise(rng))
    for _ in range(1):
        pass
    # roll 1 (4 bars) -> collect every start
    n = int(round(4 * 4 * BEAT / DT)); f = _roll(rng)
    for i in range(n):
        t += DT
        if det.on_block(t, f(i), BEAT):
            starts.append(t)
    assert len(starts) == 1, "the same roll must not re-trigger while it holds"
    # 10 bars of plain music (score falls, reference recovers), then roll 2
    t, s = _feed(det, t, 10 * 4 * BEAT, _noise(rng))
    assert s is None
    t, s = _feed(det, t, 4 * 4 * BEAT, _roll(rng))
    assert s is not None


def test_knobs_are_read_at_call_time(monkeypatch):
    """livereact.params overwrites the module constants between runs; the
    detector must see the new value without being rebuilt."""
    rng = random.Random(7)
    det = RollPeriodDetector()
    t, _ = _feed(det, 0.0, 12 * 4 * BEAT, _noise(rng))
    monkeypatch.setattr(rp, "ROLL_PERIOD_ON", 0.999)     # unreachable
    t, s = _feed(det, t, 4 * 4 * BEAT, _roll(rng))
    assert s is None


def test_score_is_zero_with_too_little_data_or_a_flat_signal():
    det = RollPeriodDetector()
    for i in range(5):
        assert det.on_block(i * DT, 1.0, BEAT) is False
    assert det.score == 0.0
    det2 = RollPeriodDetector()
    for i in range(200):
        det2.on_block(i * DT, 1.0, BEAT)
    assert det2.score == pytest.approx(0.0)
