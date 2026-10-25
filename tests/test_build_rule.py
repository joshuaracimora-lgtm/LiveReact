"""Tests for core/build_rule.py's KickLevelBuildRule (2026-09-17) and its
wiring in core/metronome.py. Numbers are the real per-bar low-band means
from the captures the rule was scored on (see the module docstring)."""

from __future__ import annotations

import queue
import threading

from livereact.clock import FakeClock
from livereact.core.build_rule import AUTO_HOLD_BARS, BUILD_BARS, KICK_GONE_BARS, KickLevelBuildRule
from livereact.core.messages import AudioLevelMessage
from livereact.core.metronome import Metronome
from livereact.core.show_state import ShowState
from livereact.scenes.base import EnergyBucket


def _rule_with_ref(ref: float = 12.5) -> KickLevelBuildRule:
    r = KickLevelBuildRule()
    for _ in range(4):
        r.note_drop_bar(ref)
    r.drop_ended()
    return r


def test_no_reference_means_no_fire_ever():
    """A set's first build has no drop to measure against: the C key's job."""
    r = KickLevelBuildRule()
    assert not any(r.on_bar(6.0, 0.4, True) for _ in range(16))


def test_the_kick_back_at_half_the_drops_level_fires_after_four_bars():
    """Run 3's mix-in build: low 5-6.5 against a 12-13 drop, energy ~0.4,
    loudness classifier stuck on BREAK."""
    r = _rule_with_ref(12.5)
    fired = [r.on_bar(low, 0.40, True) for low in (5.1, 5.1, 6.8, 6.5, 5.9)]
    assert fired == [False, False, False, True, False]
    assert r.armed


def test_a_breakdown_kick_level_or_a_drop_level_never_fires():
    r = _rule_with_ref(12.5)
    assert not any(r.on_bar(low, 0.35, True) for low in (1.5, 1.1, 1.8, 1.4, 1.7, 2.3))  # breakdown, 1-2.5
    assert not any(r.on_bar(low, 0.55, True) for low in (9.4, 11.4, 9.3, 11.0, 10.0))  # the loud intro, 7-11
    assert not any(r.on_bar(low, 0.60, True) for low in (12.3, 12.8, 12.4, 13.1))     # a drop


def test_the_streak_must_be_consecutive_and_the_classifier_must_say_break():
    r = _rule_with_ref(12.5)
    assert not any(r.on_bar(low, 0.4, True) for low in (5.0, 5.5, 1.0, 5.0, 5.5, 1.0))  # broken by kick-outs
    assert not any(r.on_bar(5.5, 0.4, False) for _ in range(8))  # base already BUILD/DROP: not ours to raise
    assert not any(r.on_bar(5.5, 0.05, True) for _ in range(8))  # silence-level energy


def test_the_hold_releases_when_the_kick_leaves_the_band_without_a_drop():
    """A breakdown with a soft kick that then thins out: the ramp must not
    sit on it. The pre-drop breath (1-2 bars out of band) is tolerated."""
    r = _rule_with_ref(12.5)
    for _ in range(BUILD_BARS):
        r.on_bar(6.0, 0.4, True)
    assert r.armed
    r.on_bar(2.0, 0.3, False)  # one bar out: the breath
    assert not r.release_due()
    r.on_bar(6.0, 0.4, False)  # back in band resets the count
    for _ in range(KICK_GONE_BARS):
        r.on_bar(1.5, 0.3, False)
    assert r.release_due()
    r.released()
    assert not r.armed


def test_a_kick_climbing_above_the_band_is_the_drop_arriving_not_the_kick_going():
    """2026-10-25, Somebody To Love 177-185 s: the kick rose from ~0.5x to
    ~0.65-1.0x the drop's level through the roll into the 184.63 s drop, and
    the old in-band check released the cue on the drop itself."""
    r = _rule_with_ref(12.5)
    for _ in range(BUILD_BARS):
        r.on_bar(6.0, 0.4, True)
    for low in (9.0, 10.5, 12.0, 12.5, 13.0, 12.8):  # above KICK_BAND_HI x ref
        r.on_bar(low, 0.5, False)
    assert not r.release_due()
    for _ in range(KICK_GONE_BARS):
        r.on_bar(1.5, 0.3, False)  # under the band: gone
    assert r.release_due()


def test_the_next_drop_resets_the_reference():
    r = KickLevelBuildRule()
    for _ in range(4):
        r.note_drop_bar(12.5)
    r.note_drop_bar(30.0)  # ignored: the reference is full until the drop ends
    assert r.ref == 12.5
    r.drop_ended()
    for _ in range(4):
        r.note_drop_bar(20.0)
    assert r.ref == 20.0


# --- metronome wiring ---------------------------------------------------------

def _metronome(clock):
    return Metronome(queue.Queue(), ShowState(), clock, threading.Event(), log_interval=1e9)


def _bars(m, clock, n, low, rms=0.4, seconds_per_bar=1.875, dt=0.1):
    """Free-run at 128 BPM with real audio at a given low-band bar level."""
    for _ in range(int(n * seconds_per_bar / dt)):
        m.queue.put(AudioLevelMessage(clock.monotonic(), rms=rms, band_low=0.5, band_mid=0.3, band_high=0.2,
                                       band_low_abs=low, band_mid_abs=2.0, band_high_abs=0.3))
        m.tick()
        clock.advance(dt)


def test_metronome_raises_an_auto_cue_after_a_drop_when_the_kick_comes_back_at_half_level():
    clock = FakeClock()
    m = _metronome(clock)
    m._bpm = 128.0
    m._energy_norm = 0.9  # a drop, by the loudness classifier
    m.request_manual_bucket(EnergyBucket.DROP)
    _bars(m, clock, 5, low=12.5, rms=0.6)  # the drop's first bars set the reference
    assert m._build_rule.ref and 11.0 < m._build_rule.ref < 14.0
    m.request_manual_bucket(EnergyBucket.BREAK)  # the classifier reads a breakdown...
    _bars(m, clock, 2, low=1.5, rms=0.3)
    assert m._base_committed == EnergyBucket.BREAK
    m._energy_norm = 0.1  # ...and keeps reading it while the kick comes back at half level
    _bars(m, clock, BUILD_BARS + 1, low=6.0, rms=0.4)
    snap = m.tick()
    assert snap.bucket == "build"
    assert snap.build_cue and snap.build_cue_source == "auto"


def test_a_c_press_during_an_auto_cue_upgrades_it_instead_of_cancelling():
    clock = FakeClock()
    m = _metronome(clock)
    m._build_cue_until = clock.monotonic() + 10.0
    m._build_cue_source = "auto"
    m._build_rule.armed = True
    m._base_committed = EnergyBucket.BUILD
    m.cue_build()
    assert m._build_cue_source == "dj"
    assert m._build_cue_until > clock.monotonic() + AUTO_HOLD_BARS  # the DJ's longer hold
    assert not m._build_rule.armed
    m.cue_build()  # a second press on a DJ cue still cancels
    assert m._build_cue_until is None and m._build_cue_source == ""


def test_the_auto_cue_source_clears_when_the_drop_lands():
    clock = FakeClock()
    m = _metronome(clock)
    m._build_cue_until = clock.monotonic() + 10.0
    m._build_cue_source = "auto"
    m._base_committed = EnergyBucket.BUILD
    m._commit_drop(clock.monotonic())
    assert m._build_cue_until is None
    assert m.tick().build_cue_source == ""



def _rule_ticks(m, clock, seconds, low, base, dt=0.1, bar_s=1.875):
    """Drive _derive_build_rule directly: base/exposed state set by hand, bar
    from the clock at 128 BPM, fresh audio at a given low-band level."""
    for _ in range(int(round(seconds / dt))):
        now = clock.monotonic()
        m._bar = int(now / bar_s)
        m._base_committed = m._bucket_committed = base
        m._last_audio_time = now
        m._audio_band_low_abs = low
        m._energy = 0.4
        m._derive_build_rule(now)
        clock.advance(dt)


def _drop_then_breakdown_with_the_kick_at_half(m, clock):
    m._bpm = 128.0
    _rule_ticks(m, clock, 5 * 1.875, low=12.5, base=EnergyBucket.DROP)  # the drop sets the reference
    assert m._build_rule.ref and 11.0 < m._build_rule.ref < 14.0
    _rule_ticks(m, clock, 0.5 * 1.875, low=6.0, base=EnergyBucket.DROP)  # its last half bar: kick already at half
    break_t = clock.monotonic()
    _rule_ticks(m, clock, BUILD_BARS * 1.875, low=6.0, base=EnergyBucket.BREAK)
    return break_t


def test_an_auto_cue_whose_kick_streak_began_in_the_drop_waits_for_breaks_floor():
    """2026-10-25 (TIME-11), One More Time 140.85 s: the kick never left the
    band after the drop, so the four kick bars began with the drop's own last
    bar and the cue landed 3.2 bars into the breakdown. Now it waits until
    the shown BREAK has held its 8-bar dwell, then commits BUILD."""
    clock = FakeClock()
    m = _metronome(clock)
    break_t = _drop_then_breakdown_with_the_kick_at_half(m, clock)
    assert m._auto_build_pending  # the rule fired; the cue waits
    assert m._manual_bucket_override is None and not m.build_cue_active
    _rule_ticks(m, clock, break_t + m._dwell_s(EnergyBucket.BREAK) - 0.2 - clock.monotonic(),
                low=6.0, base=EnergyBucket.BREAK)
    assert m._manual_bucket_override is None  # still inside BREAK's floor
    _rule_ticks(m, clock, 0.4, low=6.0, base=EnergyBucket.BREAK)
    assert m._manual_bucket_override == EnergyBucket.BUILD
    assert m._build_cue_source == "auto" and not m._auto_build_pending


def test_an_auto_cue_whose_kick_bars_all_sat_in_the_break_fires_at_once():
    """Somebody To Love 177 s: four kick bars inside the break -- the shape the
    rule exists for, cued on the bar it fires."""
    clock = FakeClock()
    m = _metronome(clock)
    m._bpm = 128.0
    _rule_ticks(m, clock, 5 * 1.875, low=12.5, base=EnergyBucket.DROP)
    _rule_ticks(m, clock, 2 * 1.875, low=1.5, base=EnergyBucket.BREAK)  # kick gone ...
    _rule_ticks(m, clock, (BUILD_BARS + 1) * 1.875, low=6.0, base=EnergyBucket.BREAK)  # ... and back
    assert m._manual_bucket_override == EnergyBucket.BUILD
    assert not m._auto_build_pending


def test_a_drop_landing_while_the_auto_cue_waits_cancels_it():
    clock = FakeClock()
    m = _metronome(clock)
    _drop_then_breakdown_with_the_kick_at_half(m, clock)
    assert m._auto_build_pending
    _rule_ticks(m, clock, 0.2, low=12.5, base=EnergyBucket.DROP)
    assert not m._auto_build_pending
    assert m._manual_bucket_override is None and not m.build_cue_active
