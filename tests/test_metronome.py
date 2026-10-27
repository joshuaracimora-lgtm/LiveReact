"""Tests for the Metronome — driven deterministically with a FakeClock.

The metronome's work is in ``tick()``; tests advance the FakeClock and call
``tick()`` directly, so beat interpolation, bar inference, free-run, and energy
derivation are all asserted with no threads and no real time passing.
"""

from __future__ import annotations

import queue
import threading

import pytest

from livereact.clock import FakeClock
from livereact.core.messages import (
    AudioLevelMessage,
    BeatMessage,
    MidiControlMessage,
    StatusMessage,
    TransitionMessage,
)
from livereact.core.metronome import (
    CUT_EXIT_CONFIRM_S,
    EXIT_CONFIRM_DWELL_S,
    AUTO_DROP_CONFIRM_S,
    FAST_ENTRY_DWELL_S,
    Metronome,
)
from livereact.core.show_state import ShowState
from livereact.scenes.base import EnergyBucket


def _metronome(clock: FakeClock):
    return Metronome(queue.Queue(), ShowState(), clock, threading.Event())


def test_beat_phase_interpolates_between_master_beats():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(BeatMessage(timestamp=0.0, beat_in_bar=1, bpm=120.0, is_master=True, player=1))
    snap = m.tick()
    assert snap.bpm == 120.0
    assert snap.beat_phase == pytest.approx(0.0, abs=1e-6)
    assert not snap.free_running and snap.pdl_fresh

    clock.advance(0.25)  # halfway through a 0.5 s beat
    snap = m.tick()
    assert snap.beat_phase == pytest.approx(0.5, abs=1e-3)

    clock.advance(0.24)  # near the end of the beat
    snap = m.tick()
    assert snap.beat_phase == pytest.approx(0.98, abs=0.02)


def test_bar_increments_on_the_downbeat_wrap():
    clock = FakeClock()
    m = _metronome(clock)
    # A full bar of master beats: 2,3,4,1 -> the trailing '1' completes a bar.
    for beat in (2, 3, 4, 1):
        m.queue.put(BeatMessage(0.0, beat, 120.0, True, 1))
        m.tick()
    assert m.tick().bar == 1
    for beat in (2, 3, 4, 1):
        m.queue.put(BeatMessage(0.0, beat, 120.0, True, 1))
        m.tick()
    assert m.tick().bar == 2


def test_free_runs_when_master_beats_stop():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(BeatMessage(0.0, 1, 120.0, True, 1))  # beat_dur = 0.5 s
    m.tick()
    assert not m.tick().free_running  # fresh right after a beat

    # No more beats; jump well past the staleness window.
    clock.advance(1.5)
    snap = m.tick()
    assert snap.free_running and not snap.pdl_fresh
    # Phase kept advancing (beats rolled over from the clock, not stuck at 0).
    assert snap.beat_in_bar != 1 or snap.bar > 0


def test_energy_follows_fader_and_eq():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(MidiControlMessage(0.0, "fader_a", 1.0))
    for ctrl in ("eq_low_a", "eq_mid_a", "eq_high_a"):
        m.queue.put(MidiControlMessage(0.0, ctrl, 0.5))
    # raw = 0.6*1.0 + 0.4*0.5 = 0.8; EMA converges over many ticks.
    snap = None
    for _ in range(300):
        snap = m.tick()
    assert snap.energy == pytest.approx(0.8, abs=0.01)
    assert snap.midi_fresh


def test_energy_prefers_fresh_audio_over_midi_proxy():
    """docs/14: real audio should win over the MIDI fader/EQ estimate when
    both are present — it's the actual sound, not a mixer-knob proxy."""
    clock = FakeClock()
    m = _metronome(clock)
    # MIDI would derive energy=0.8 (see test_energy_follows_fader_and_eq)...
    m.queue.put(MidiControlMessage(0.0, "fader_a", 1.0))
    for ctrl in ("eq_low_a", "eq_mid_a", "eq_high_a"):
        m.queue.put(MidiControlMessage(0.0, ctrl, 0.5))
    # ...but real audio says the room is much quieter -> that should win.
    m.queue.put(AudioLevelMessage(0.0, rms=0.2, band_low=0.5, band_mid=0.3, band_high=0.2,
                                   band_low_abs=10.0, band_mid_abs=6.0, band_high_abs=4.0))
    snap = None
    for _ in range(300):
        snap = m.tick()
    assert snap.audio_fresh
    assert snap.energy == pytest.approx(0.2, abs=0.01)  # audio's 0.2, not MIDI's 0.8
    assert snap.audio_band_low == 0.5
    assert snap.audio_band_low_abs == 10.0
    # build_snare_accent Step 0 (2026-09-14): the same plumbing for mid/high
    assert snap.audio_band_mid_abs == 6.0
    assert snap.audio_band_high_abs == 4.0


def test_energy_falls_back_to_midi_when_audio_goes_stale():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(AudioLevelMessage(0.0, rms=0.2, band_low=0.5, band_mid=0.3, band_high=0.2,
                                   band_low_abs=10.0, band_mid_abs=6.0, band_high_abs=4.0))
    m.queue.put(MidiControlMessage(0.0, "fader_a", 1.0))
    for ctrl in ("eq_low_a", "eq_mid_a", "eq_high_a"):
        m.queue.put(MidiControlMessage(0.0, ctrl, 0.5))
    for _ in range(50):
        m.tick()
    assert m.tick().audio_fresh

    clock.advance(2.0)  # past AUDIO_STALE_AFTER, MIDI stays fresh (no separate staleness test needed here)
    snap = None
    for _ in range(300):
        snap = m.tick()
    assert not snap.audio_fresh
    assert snap.energy == pytest.approx(0.8, abs=0.01)  # falls back to the MIDI proxy


# --- adaptive energy normalization (2026-09-07, found live) ------------------
# `bucket_for_energy` should read `energy_norm`, not raw `energy` — see
# show_state.py's energy_norm docstring / livereact-project memory for why a
# fixed absolute threshold against raw RMS doesn't generalize across sessions.


def _pump_audio(m: Metronome, clock: FakeClock, rms: float, seconds: float, step: float = 0.2) -> None:
    """Feed a steady real-audio rms for `seconds` of simulated time, keeping
    audio_fresh continuously true (messages every `step`s, well under
    AUDIO_STALE_AFTER) — the shape a real live audio stream would take."""
    elapsed = 0.0
    while elapsed < seconds:
        m.queue.put(AudioLevelMessage(clock.monotonic(), rms=rms, band_low=0.5,
                                       band_mid=0.3, band_high=0.2,
                                       band_low_abs=rms * 20, band_mid_abs=rms * 12, band_high_abs=rms * 8))
        m.tick()
        clock.advance(step)
        elapsed += step


def test_energy_norm_stays_zero_during_warmup():
    """Not enough history yet (< ENERGY_NORM_MIN_SAMPLES) -> report 0.0 (the
    safe/quiet default), not a ratio computed from almost nothing."""
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.6, seconds=5.0)
    assert m.tick().energy_norm == 0.0


def test_energy_norm_reaches_high_values_for_a_sustained_loud_passage():
    """The exact bug found live (2026-09-07): a sustained passage well below
    the OLD fixed DROP threshold (0.80 on raw energy) should still normalize
    high once it's genuinely the loudest thing this set has recently
    contained — that's what actually generalizes across tracks/gain staging."""
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.1, seconds=60.0)   # a quiet baseline first
    _pump_audio(m, clock, rms=0.5, seconds=30.0)   # then a sustained louder passage
    snap = m.tick()
    assert snap.energy == pytest.approx(0.5, abs=0.01)  # raw energy: well under the old 0.80 cutoff
    assert snap.energy_norm > 0.8  # normalized against ITS OWN recent range: reads as a drop


def test_energy_norm_smooths_out_beat_to_beat_oscillation():
    """FOUND BY TESTING (offline replay against a real captured session):
    normalizing raw, unsmoothed per-tick energy directly flipped the bucket
    several times a second, since ordinary beat-to-beat swings are a big
    fraction of any short window's own range. Pin down the fix: alternating
    high/low rms (simulating kick pulsing) should NOT make energy_norm swing
    wildly tick to tick once warmed up."""
    clock = FakeClock()
    m = _metronome(clock)
    # a realistic quiet baseline with natural micro-variation, not a perfectly
    # flat signal -- a perfectly flat 60s input degenerately collapses this
    # project's own MIN_SPAN floor to its minimum, which isn't how real audio
    # behaves (even a "flat" bassline has some tick-to-tick variation).
    baseline = [0.25, 0.30, 0.28, 0.32, 0.27]
    for i in range(120):
        rms = baseline[i % len(baseline)]
        m.queue.put(AudioLevelMessage(clock.monotonic(), rms=rms, band_low=0.5,
                                       band_mid=0.3, band_high=0.2,
                                       band_low_abs=rms * 20, band_mid_abs=rms * 12, band_high_abs=rms * 8))
        m.tick()
        clock.advance(0.5)
    samples = []
    for i in range(40):  # ~8s of alternating high/low, faster than a real kick
        rms = 0.6 if i % 2 == 0 else 0.15
        m.queue.put(AudioLevelMessage(clock.monotonic(), rms=rms, band_low=0.5,
                                       band_mid=0.3, band_high=0.2,
                                       band_low_abs=rms * 20, band_mid_abs=rms * 12, band_high_abs=rms * 8))
        samples.append(m.tick().energy_norm)
        clock.advance(0.2)
    settled = samples[15:]  # after the asymmetric attack (ENERGY_NORM_ATTACK_TAU_S) has settled
    deltas = [abs(b - a) for a, b in zip(settled, settled[1:])]
    assert max(deltas) < 0.15  # smoothed -- doesn't chase every toggle


def test_energy_norm_ignores_a_tiny_blip_after_extended_silence():
    """FOUND BY TESTING: without an absolute-ceiling guard, a long silent
    stretch (a track ending) followed by any small residual noise spiked
    energy_norm straight to ~1.0 ("DROP") against a near-zero range."""
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.0, seconds=60.0)
    m.queue.put(AudioLevelMessage(clock.monotonic(), rms=0.08, band_low=0.5,
                                   band_mid=0.3, band_high=0.2,
                                   band_low_abs=1.6, band_mid_abs=1.0, band_high_abs=0.6))
    assert m.tick().energy_norm == 0.0


def test_a_quiet_intro_after_silence_is_not_a_drop():
    """FOUND LIVE (recordings/session-20260915-114303): with silence counted
    as the bottom of the set's range, a track intro at raw 0.14 (the
    quietest audible content of the night) normalized to 1.00 and committed
    a full DROP -- a bigger show than the real drop (raw 0.55) got. Same
    thing at a track restart after ~2min of silence. Silence is not part of
    the range: the window must hold music only."""
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.0, seconds=45.0)   # session start: nothing playing
    _pump_audio(m, clock, rms=0.14, seconds=60.0)  # a quiet intro, well past the warmup
    snap = m.tick()
    assert snap.energy == pytest.approx(0.14, abs=0.01)
    assert snap.energy_norm < 0.5, snap.energy_norm  # nowhere near DROP


def test_a_restart_after_long_silence_compares_against_the_previous_track():
    """The 396s event from the same capture: after a full track and ~2min of
    silence, the next track's quiet intro must be judged against the LAST
    music that played (which it is far below), not against the silence."""
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.30, seconds=40.0)  # a track's break section
    _pump_audio(m, clock, rms=0.55, seconds=40.0)  # its drop
    _pump_audio(m, clock, rms=0.0, seconds=130.0)  # silence between tracks
    _pump_audio(m, clock, rms=0.16, seconds=10.0)  # next track's quiet intro
    assert m.tick().energy_norm < 0.3  # BREAK, not DROP
    _pump_audio(m, clock, rms=0.55, seconds=15.0)  # its drop lands
    assert m.tick().energy_norm > 0.8  # still recognised as a drop


def test_one_isolated_hit_in_silence_does_not_start_a_build():
    """2026-09-15 replay of recordings/session-20260915-121503 with the
    faster attack (ENERGY_NORM_ATTACK_TAU_S): a single cue-tap hit (raw
    0.51 for ~0.5s, everything else ~0) rode the slow release into a 9s
    BUILD with the build strobe running. An upward move now also needs
    the UNSMOOTHED level to agree at confirmation time -- a real section
    keeps its level up; one hit is gone by then."""
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.30, seconds=40.0)  # a track's break
    _pump_audio(m, clock, rms=0.55, seconds=40.0)  # its drop
    _pump_audio(m, clock, rms=0.0, seconds=90.0)   # track over, silence (past the OUTRO lookback)
    assert m.tick().bucket == "break"
    _pump_audio(m, clock, rms=0.51, seconds=0.6)   # one tap
    seen = set()
    for _ in range(50):                            # the next 10s of silence
        _pump_audio(m, clock, rms=0.0, seconds=0.2)
        seen.add(m.tick().bucket)
    assert seen == {"break"}, seen


def test_energy_norm_passes_raw_energy_through_when_audio_is_stale():
    """The MIDI-fallback proxy is already a hand-built 0..1 signal (fader/EQ
    position), not a loudness measure -- adaptive rescaling doesn't apply."""
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(MidiControlMessage(0.0, "fader_a", 1.0))
    for ctrl in ("eq_low_a", "eq_mid_a", "eq_high_a"):
        m.queue.put(MidiControlMessage(0.0, ctrl, 0.5))
    snap = None
    for _ in range(300):
        snap = m.tick()
    assert not snap.audio_fresh
    assert snap.energy_norm == pytest.approx(snap.energy, abs=1e-9)


# --- bucket debounce + hysteresis (2026-09-07, found live + via offline
# replay on a real track; RE-TUNED 2026-09-07 evening after a real
# --hardware --record capture, see [[livereact-project]] memory) ---
# bucket_for_energy flapped near its own boundaries; a candidate bucket must
# persist for BUCKET_MIN_DWELL_S before it's committed to. The dwell alone
# turned out not to be enough once tested against a real ~4min song (26
# flips, several under BUCKET_MIN_DWELL_S's old 1.0s value once compounded
# across a boundary crossed repeatedly) -- BUCKET_MIN_DWELL_S was raised
# 1.0->3.0, and `bucket_for_energy_with_hysteresis` now also requires
# clearing a boundary by HYSTERESIS_MARGIN before a candidate even starts.
# Drives _derive_bucket directly (bypassing tick()'s own _derive_energy_norm,
# which would otherwise overwrite the manually-set energy_norm each call) --
# same white-box style as the laser trigger's duty-cap tests.


def test_bucket_starts_at_break():
    clock = FakeClock()
    m = _metronome(clock)
    assert m.tick().bucket == "break"


def test_bucket_ignores_a_brief_upward_blip_below_fast_entry_dwell():
    """Bible-integration Phase 2: entering a higher-energy bucket uses the
    short FAST_ENTRY_DWELL_S (~0.94s at the nominal 128 BPM), not the old
    flat 3.0s -- but a blip shorter than even that should still never
    commit."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9  # DROP-range
    m._derive_bucket(clock.monotonic())
    clock.advance(0.3)  # well under FAST_ENTRY_DWELL_S -- a blip, not a real change
    m._derive_bucket(clock.monotonic())
    m._energy_norm = 0.0  # back to BREAK-range before the blip could commit
    clock.advance(0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed.value == "break"  # never actually changed


def test_bucket_commits_after_persisting_past_fast_entry_dwell():
    """BUILD enters on FAST_ENTRY_DWELL_S; an unarmed DROP is the safety net
    and takes AUTO_DROP_CONFIRM_S (two bars) -- see DROP_ARM_HOLD_BARS."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.5  # BUILD-range
    m._derive_bucket(clock.monotonic())
    clock.advance(1.0)  # past FAST_ENTRY_DWELL_S (~0.94s), still BUILD-range the whole time
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed.value == "build"
    m._energy_norm = 0.9  # DROP-range
    m._derive_bucket(clock.monotonic())
    clock.advance(1.0)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed.value == "build"  # not yet: unarmed drops wait two bars
    clock.advance(AUTO_DROP_CONFIRM_S)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed.value == "drop"


def test_bucket_candidate_restarts_if_a_different_bucket_appears_first():
    """A candidate that gets pre-empted by a THIRD bucket before committing
    must restart its own dwell timer, not inherit the first candidate's
    elapsed time."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.5  # BUILD-range candidate begins (base starts at BREAK)
    m._derive_bucket(clock.monotonic())
    clock.advance(0.5)  # close to FAST_ENTRY_DWELL_S (~0.94s), but not quite
    m._energy_norm = 0.9  # DROP-range -- pre-empts the BUILD candidate; still "up" from
                           # base=BREAK (hasn't committed to BUILD yet), so still fast-entry
    m._derive_bucket(clock.monotonic())
    clock.advance(0.5)  # would have been enough time for the ORIGINAL (BUILD) candidate
    m._derive_bucket(clock.monotonic())
    assert m._base_committed.value == "break"  # DROP candidate restarted, hasn't dwelled long enough yet
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)  # now past the (two-bar) DROP confirmation since it became the candidate
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed.value == "drop"


def test_bucket_hovering_within_the_margin_of_a_boundary_never_becomes_a_candidate():
    """The actual live bug (2026-09-07 real capture): energy_norm can sit
    within a hair of a boundary for a real stretch of a song. Once settled in
    BUILD, hovering just above/below BUILD_MAX (0.80) without ever clearing
    it by HYSTERESIS_MARGIN_UP must never commit to DROP -- the committed
    bucket should just stay put, however long real time passes."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.65  # squarely BUILD, nowhere near a boundary
    m._derive_bucket(clock.monotonic())
    clock.advance(1.0)  # past FAST_ENTRY_DWELL_S -- BUILD is now the committed bucket
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed.value == "build"

    for energy in (0.78, 0.81, 0.79, 0.82, 0.80):  # all within +-0.03 of BUILD_MAX=0.80,
                                                     # i.e. inside the +-0.05 HYSTERESIS_MARGIN_UP
        m._energy_norm = energy
        m._derive_bucket(clock.monotonic())
        clock.advance(1.0)
    assert m._bucket_committed.value == "build"  # never even started a real DROP candidate


def test_midi_and_transition_and_status_update_state():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(MidiControlMessage(0.0, "crossfader", 0.8))
    m.queue.put(TransitionMessage(0.0, active=True))
    m.queue.put(StatusMessage(0.0, on_air=(2,), master_deck=2, playing=True, loop_active=True))
    snap = m.tick()
    assert snap.crossfader == pytest.approx(0.8)
    assert snap.in_transition is True
    assert snap.on_air == (2,) and snap.master_deck == 2 and snap.loop_active

    m.queue.put(TransitionMessage(0.0, active=False))
    assert m.tick().in_transition is False


# --- Bible-integration Phase 2: legal transition matrix, PEAK/OUTRO overlay,
# manual override, drop marker (2026-09-11) -----------------------------------

def test_drop_falls_back_to_build_only_after_two_bars_of_build_range_energy():
    """LIGHTING_BIBLE.md §9.8 forbids DROP -> BUILD; D3 (2026-09-17) allows it
    as a CONFIRMED EXIT only -- the same two-bar CUT_EXIT_CONFIRM_S as
    DROP -> BREAK, floor or no floor. A shorter BUILD-range dip (a fill, a
    between-kick lull riding the release) holds DROP; two bars of it is a
    post-drop section that never got quiet enough to read BREAK, and the
    build ramp must start there instead of the drop look sitting on it."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9  # commit to DROP
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP

    m._energy_norm = 0.5  # BUILD-range, well clear of the downward margin
    clock.advance(0.1)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_candidate == EnergyBucket.BUILD  # entertained (legal), not yet committed
    clock.advance(CUT_EXIT_CONFIRM_S - 0.5)  # one-and-a-bit bars: still a dip
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    m._energy_norm = 0.9  # the drop comes back -> the candidate is dropped
    m._derive_bucket(clock.monotonic())
    assert m._bucket_candidate is None
    assert m._base_committed == EnergyBucket.DROP

    m._energy_norm = 0.5  # now a real post-drop section
    clock.advance(0.1)
    m._derive_bucket(clock.monotonic())
    clock.advance(CUT_EXIT_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD  # well before DROP's own 8-bar floor
    assert clock.monotonic() < m._dwell_s(EnergyBucket.DROP)


def test_a_drop_dip_shorter_than_the_cut_confirmation_never_becomes_a_build():
    """The other half of D3: repeated BUILD-range dips that each end before
    CUT_EXIT_CONFIRM_S (the release tau riding through a breakdown-then-
    kick pattern) never accumulate into a BUILD -- every return to DROP
    range clears the candidate."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    for _ in range(6):
        m._energy_norm = 0.5
        clock.advance(0.1)
        m._derive_bucket(clock.monotonic())
        clock.advance(CUT_EXIT_CONFIRM_S * 0.6)
        m._derive_bucket(clock.monotonic())
        m._energy_norm = 0.9
        clock.advance(0.1)
        m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP


def test_drop_escalates_to_peak_after_sustained_dwell():
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.DROP  # not yet -- hasn't sustained DROP's own dwell

    clock.advance(m._dwell_s(EnergyBucket.DROP))  # now past DROP's own floor, still DROP-range
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.PEAK
    assert m._base_committed == EnergyBucket.DROP  # base classifier itself is untouched by the overlay


def _to_peak(clock, m):
    """Commit DROP, hold it for its dwell, escalate to PEAK, then sit past
    PEAK's own dwell so only the exit confirmation holds it."""
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    clock.advance(m._dwell_s(EnergyBucket.DROP) + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.PEAK
    clock.advance(m._dwell_s(EnergyBucket.PEAK))
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.PEAK


def _run(clock, m, seconds, dt=0.1):
    seen = set()
    for _ in range(int(round(seconds / dt))):
        clock.advance(dt)
        m._derive_bucket(clock.monotonic())
        seen.add(m._bucket_committed)
    return seen


def test_dwell_floors_are_bars_at_the_live_tempo():
    """2026-10-24 (TIME-11): 8 bars at 122 BPM is longer than at 128 BPM."""
    clock = FakeClock()
    m = _metronome(clock)
    m._bpm = 122.0
    assert m._dwell_s(EnergyBucket.DROP) == pytest.approx(8 * 4 * 60.0 / 122.0)
    m._bpm = 0.0  # no tempo yet: the nominal one
    assert m._dwell_s(EnergyBucket.DROP) == pytest.approx(8 * 4 * 60.0 / 128.0)


def test_peak_holds_through_a_short_dip_instead_of_blipping_to_drop():
    """Latch 94.8 s / Thriller 114.1 s: a one-bar dip under the PEAK exit
    line used to expose DROP for under a bar between two PEAKs."""
    clock = FakeClock()
    m = _metronome(clock)
    _to_peak(clock, m)
    m._energy_norm = 0.67  # DROP range for the base (floor ~0.65), under the PEAK exit line (0.70)
    assert _run(clock, m, m._bar_s()) == {EnergyBucket.PEAK}
    m._energy_norm = 0.9
    assert _run(clock, m, 0.1) == {EnergyBucket.PEAK}


def test_peak_de_escalates_to_drop_only_after_a_full_drop_dwell_of_lower_level():
    """2026-10-25: the lower section must last as long as the DROP it would
    enter has to hold (PEAK_EXIT_CONFIRM_BARS = 8)."""
    from livereact.core.metronome import PEAK_EXIT_CONFIRM_BARS
    clock = FakeClock()
    m = _metronome(clock)
    _to_peak(clock, m)
    m._energy_norm = 0.67
    m._derive_bucket(clock.monotonic())
    assert _run(clock, m, PEAK_EXIT_CONFIRM_BARS * m._bar_s() - 0.3) == {EnergyBucket.PEAK}
    _run(clock, m, 0.4)
    assert m._bucket_committed == EnergyBucket.DROP
    # ... and DROP must now be on show for its own dwell before PEAK again
    m._energy_norm = 0.9
    assert _run(clock, m, 1.0) == {EnergyBucket.DROP}


def test_a_break_range_reading_under_peak_is_a_cut_and_restarts_the_count():
    """2026-10-25 (TIME-11): every PEAK -> DROP on the ear-verified tracks
    was the energy fading into a breakdown (One More Time 133.5 s, messy
    93.8 s, Latch 253.1 s ...). A reading in BREAK range holds PEAK and
    restarts the de-escalation count."""
    from livereact.core.metronome import PEAK_EXIT_CONFIRM_BARS
    clock = FakeClock()
    m = _metronome(clock)
    _to_peak(clock, m)
    confirm_s = PEAK_EXIT_CONFIRM_BARS * m._bar_s()
    m._energy_norm = 0.67
    assert _run(clock, m, confirm_s - 1.0) == {EnergyBucket.PEAK}
    m._energy_norm = 0.2  # one tick in BREAK range: a cut under way, not a lower drop
    assert _run(clock, m, 0.1) == {EnergyBucket.PEAK}
    m._energy_norm = 0.67
    assert _run(clock, m, confirm_s - 0.3) == {EnergyBucket.PEAK}  # the count started over
    _run(clock, m, 0.5)
    assert m._bucket_committed == EnergyBucket.DROP


def test_a_breakdown_goes_peak_to_break_without_a_drop_blip():
    """SWEETER 131 s / One More Time 59.9 s: the base's 2-bar cut exit wins
    over PEAK's exit confirmation, so DROP never shows in between."""
    clock = FakeClock()
    m = _metronome(clock)
    _to_peak(clock, m)
    m._energy_norm = 0.05
    seen = _run(clock, m, CUT_EXIT_CONFIRM_S + 1.0)
    assert m._base_committed == EnergyBucket.BREAK
    assert seen == {EnergyBucket.PEAK, EnergyBucket.BREAK}


def test_peak_never_exposes_an_illegal_move_to_build():
    """PEAK -> BUILD is not in the bible's matrix: a base DROP -> BUILD under
    a held PEAK steps through DROP (legal both ways)."""
    from livereact.scenes.catalog import LEGAL_TRANSITIONS
    clock = FakeClock()
    m = _metronome(clock)
    _to_peak(clock, m)
    m._base_committed = EnergyBucket.BUILD  # the base's confirmed DROP -> BUILD exit (D3)
    m._apply_bucket_overlay(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.DROP
    assert EnergyBucket.DROP in LEGAL_TRANSITIONS[EnergyBucket.PEAK]
    assert EnergyBucket.BUILD in LEGAL_TRANSITIONS[EnergyBucket.DROP]


def test_break_promotes_to_outro_after_a_recent_drop_then_reverts():
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9  # commit to DROP -- sets _last_drop_or_peak_t
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 1.0)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.DROP

    m._energy_norm = 0.1  # fall legally to BREAK
    clock.advance(m._dwell_s(EnergyBucket.DROP))  # DROP's own floor must clear before any exit
    m._derive_bucket(clock.monotonic())
    clock.advance(CUT_EXIT_CONFIRM_S + 0.1)  # DROP -> BREAK always takes the 2-bar confirmation (2026-09-16)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK
    # Sustained-dwell requirement (2026-09-13, found via the first real
    # hardware session): OUTRO promotion now requires BREAK to have ALREADY
    # sat past its own PER_STATE_DWELL_S first (same symmetry as DROP->PEAK
    # above), so a break barely a moment old doesn't get immediately
    # mislabeled OUTRO just because a drop happened recently -- confirmed
    # NOT promoted yet, right at BREAK's commit.
    assert m._bucket_committed == EnergyBucket.BREAK

    clock.advance(m._dwell_s(EnergyBucket.BREAK))  # BREAK's own floor now clears too
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.OUTRO  # NOW promoted -- sustained AND a drop was recent


def test_a_fast_recovering_break_never_gets_promoted_to_outro_at_all():
    """The actual bug found live, 2026-09-13: on a real energetic track,
    drops recur every 15-20 bars, so most breaks recover into a new build
    well before BREAK's own 8-bar dwell floor -- those must never be
    mislabeled OUTRO at all, not just delayed. Confirmed against the real
    capture: OUTRO's share of one real session dropped from 31.4% to
    16.5%, BREAK correctly rose to 45.4%, once this fix landed."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9  # commit to DROP
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 1.0)
    m._derive_bucket(clock.monotonic())

    m._energy_norm = 0.1  # fall to BREAK
    clock.advance(m._dwell_s(EnergyBucket.DROP))
    m._derive_bucket(clock.monotonic())
    clock.advance(CUT_EXIT_CONFIRM_S + 0.1)  # DROP -> BREAK always takes the 2-bar confirmation (2026-09-16)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK
    assert m._bucket_committed == EnergyBucket.BREAK  # not OUTRO, correctly

    # Recovers into a new build well before BREAK's 8-bar floor -- a real,
    # ordinary breather between drops, not the set ending.
    clock.advance(1.0)
    m._energy_norm = 0.5
    m._derive_bucket(clock.monotonic())
    clock.advance(FAST_ENTRY_DWELL_S + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD
    assert m._bucket_committed == EnergyBucket.BUILD  # never touched OUTRO at any point


def test_outro_does_not_illegally_block_a_real_second_build():
    """Deliberate deviation from the Bible's literal "OUTRO has no legal
    exit" (see the module-level comment in metronome.py): this system can't
    tell a real ending from a mid-song breather, so a genuine energy rise is
    allowed to simply un-promote OUTRO rather than illegally blocking a real
    second build/drop in the same track."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 1.0)
    m._derive_bucket(clock.monotonic())
    m._energy_norm = 0.1
    clock.advance(m._dwell_s(EnergyBucket.DROP))  # DROP's own floor must clear before any exit
    m._derive_bucket(clock.monotonic())
    clock.advance(CUT_EXIT_CONFIRM_S + 0.1)  # DROP -> BREAK always takes the 2-bar confirmation (2026-09-16)
    m._derive_bucket(clock.monotonic())
    clock.advance(m._dwell_s(EnergyBucket.BREAK))  # BREAK's own sustained-dwell floor (2026-09-13 fix)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.OUTRO

    m._energy_norm = 0.5  # a real second build starting, well within the lookback window
    m._derive_bucket(clock.monotonic())  # registers the candidate
    clock.advance(FAST_ENTRY_DWELL_S + 0.5)
    m._derive_bucket(clock.monotonic())  # confirms it
    assert m._base_committed == EnergyBucket.BUILD
    assert m._bucket_committed == EnergyBucket.BUILD  # not stuck in OUTRO


def test_manual_bucket_override_takes_priority_and_is_consumed_once():
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.0  # would otherwise settle at BREAK
    m.request_manual_bucket(EnergyBucket.DROP)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.DROP
    assert m._manual_bucket_override is None  # consumed

    # a further tick with no new override falls back to the automated classifier,
    # which holds at DROP (just committed, hasn't dwelled long enough to move again)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP


@pytest.mark.parametrize("cue", [EnergyBucket.PEAK, EnergyBucket.OUTRO])
def test_manual_override_to_an_overlay_state_holds_and_never_crashes(cue):
    """2026-09-15 polish pass: PEAK/OUTRO are overlays on a DROP/BREAK base,
    and the old override path stored them straight into `_base_committed`
    -- the next tick's `_BUCKET_ORDER.index()` raised ValueError and killed
    the metronome thread. The cue must land AND survive further ticks."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.0
    m.request_manual_bucket(cue)
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == cue
    for _ in range(3):
        clock.advance(0.1)
        m._derive_bucket(clock.monotonic())  # used to raise here
        assert m._base_committed in (EnergyBucket.BREAK, EnergyBucket.BUILD, EnergyBucket.DROP)
    # Both hold: OUTRO's overlay on its own (a recent drop + a sustained break,
    # both set by the cue); PEAK since 2026-10-24 holds its own dwell (the cue
    # used to fall back to DROP on the next tick), and energy_norm 0 reads as
    # a cut under way, which waits for the base's exit (2026-10-25).
    assert m._bucket_committed == cue


def test_outro_cue_shows_outro_on_its_first_tick_whatever_the_float_rounding():
    """2026-10-26 (TIME-11): back-dating the BREAK base by exactly its dwell read a few 1e-15 s
    short of the floor at 132 BPM, t = 238.26 s, so a planned OUTRO showed BREAK for one tick
    (a 0.0-bar BREAK run) before the overlay agreed on the next."""
    clock = FakeClock(start=238.26)
    m = _metronome(clock)
    m._bpm = 132.0
    m._energy_norm = 0.0
    now = clock.monotonic()
    assert now - (now - m._dwell_s(EnergyBucket.BREAK)) < m._dwell_s(EnergyBucket.BREAK)   # the rounding is real
    m._apply_manual_override(EnergyBucket.OUTRO, now, peak_allowed=False, outro_allowed=True)
    assert m._bucket_committed == EnergyBucket.OUTRO


def test_manual_drop_override_captures_the_drop_bookkeeping():
    clock = FakeClock()
    m = _metronome(clock)
    m._bar = 7
    m.request_manual_bucket(EnergyBucket.DROP)
    m._derive_bucket(clock.monotonic())
    assert m.drop_hit_intensity == 1.0  # a manual "drop now" is the real thing
    assert m.drop_start_bar == 7        # phrase origin for the strobe/palette


def test_classifier_uses_the_declared_legal_transition_matrix():
    """scenes/catalog.py's LEGAL_TRANSITIONS is the single source for which
    base moves the classifier may consider (2026-09-15 polish pass). With
    DROP -> BUILD struck from the table (the Bible's own matrix, before D3)
    a committed DROP must ignore a BUILD-range reading outright (no
    candidate ever forms), while a BREAK-range one is entertained."""
    from livereact.scenes.catalog import LEGAL_TRANSITIONS
    assert EnergyBucket.BUILD in LEGAL_TRANSITIONS[EnergyBucket.DROP]  # D3's edge is declared there
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    clock.advance(m._dwell_s(EnergyBucket.DROP))
    LEGAL_TRANSITIONS[EnergyBucket.DROP].discard(EnergyBucket.BUILD)
    try:
        m._energy_norm = 0.5  # BUILD range, well clear of the downward margin
        m._derive_bucket(clock.monotonic())
        assert m._bucket_candidate is None  # struck edge: never even a candidate
        m._energy_norm = 0.05  # BREAK range
        m._derive_bucket(clock.monotonic())
        assert m._bucket_candidate == EnergyBucket.BREAK
    finally:
        LEGAL_TRANSITIONS[EnergyBucket.DROP].add(EnergyBucket.BUILD)


def test_drop_marker_fires_after_a_qualifying_dip_and_rebound():
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_drop_marker(clock.monotonic())
    assert m.drop_cycle == 0

    m._energy_norm = 0.02  # dip below DIP_THRESHOLD
    m._derive_drop_marker(clock.monotonic())
    clock.advance(1.5)  # >= MIN_DIP_S -- a qualifying dip, not just a normal beat gap
    m._derive_drop_marker(clock.monotonic())

    m._energy_norm = 0.85  # rebound, above REBOUND_THRESHOLD, within the rebound window
    m._derive_drop_marker(clock.monotonic())
    assert m.drop_cycle == 1
    assert m._drop_marker_pulse is True

    m._derive_drop_marker(clock.monotonic())  # next tick -- the pulse is one-shot
    assert m._drop_marker_pulse is False
    assert m.drop_cycle == 1  # doesn't keep re-firing


def test_drop_marker_path1_ignores_a_short_gap_but_path2_still_catches_the_real_rise():
    """A short gap (well under MIN_DIP_S) correctly doesn't qualify for the
    quiet-then-rebound path (path 1) -- but Josh's correction means that's
    NOT the only path: a genuinely sharp rise counts on its own (path 2),
    regardless of whether the dip before it was long enough to "count" as a
    real cut. This is intentional, not a bug -- see _derive_drop_marker's
    own docstring (an OR, not an AND, between the two paths)."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_drop_marker(clock.monotonic())

    m._energy_norm = 0.02
    m._derive_drop_marker(clock.monotonic())
    clock.advance(0.2)  # well under MIN_DIP_S -- doesn't qualify path 1's dip requirement
    m._derive_drop_marker(clock.monotonic())

    m._energy_norm = 0.85  # still a genuinely sharp rise from 0.02 -- path 2 catches it
    m._derive_drop_marker(clock.monotonic())
    assert m.drop_cycle == 1
    assert m._drop_marker_pulse is True


def test_drop_marker_does_not_fire_on_an_ordinary_gradual_rise():
    """Neither path should fire for energy that just gradually climbs --
    path 1 needs a real qualifying dip first, and path 2 needs the rise
    itself to clear SHARP_RISE_DELTA within SHARP_RISE_WINDOW_S, not just
    any increase."""
    clock = FakeClock()
    m = _metronome(clock)
    for energy in (0.3, 0.4, 0.5, 0.6, 0.7):  # a gradual build, well under SHARP_RISE_DELTA per step
        m._energy_norm = energy
        m._derive_drop_marker(clock.monotonic())
        clock.advance(1.0)
    assert m.drop_cycle == 0
    assert m._drop_marker_pulse is False


# --- Bible-integration Phase 3: beat-quantized DROP release -----------------

def test_drop_commits_immediately_when_already_on_a_downbeat():
    clock = FakeClock()
    m = _metronome(clock)
    m._beat_in_bar = 1  # _metronome()'s own default, spelled out here for clarity
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP  # no wait needed -- already aligned
    assert not m._pending_drop_release


def test_drop_commits_immediately_mid_bar_and_backdates_its_start_bar():
    """SUPERSEDES the beat-quantized release (Phase 3 D1) for the energy
    classifier -- 2026-09-15, recordings/session-20260915-121503: the
    audible hit was a one-tick step ON a downbeat, and by the time the
    smoothed classifier confirmed it (~3-4 beats later) waiting for the
    NEXT downbeat pushed the commit a full bar late, with the blackout
    bridge landing inside the drop. Now: commit the moment the candidate
    confirms, and publish the bar the candidate FIRST appeared in as the
    phrase origin (drop_start_bar), not the commit bar."""
    clock = FakeClock()
    m = _metronome(clock)
    m._bar = 40
    m._beat_in_bar = 2
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())  # candidate appears in bar 40
    m._bar = 41                          # the confirmation lands in the next bar
    m._beat_in_bar = 3                   # mid-bar -- would previously have waited for beat 1
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    assert not m._pending_drop_release
    assert m.drop_start_bar == 40  # backdated to where the sound first supported it


def test_a_sustained_cut_exits_the_drop_before_its_dwell_floor():
    """Same capture: the music dropped out mid-drop and the 8-bar DROP dwell
    floor held full drop lighting over ~8s of quiet ("break setting didn't
    work"). A BREAK-range reading that persists CUT_EXIT_CONFIRM_S is a
    real breakdown and may leave DROP early; a shorter dip may not."""
    from livereact.core.metronome import CUT_EXIT_CONFIRM_S
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    clock.advance(2.0)  # well inside PER_STATE_DWELL_S[DROP] (~15s)
    m._energy_norm = 0.05  # the kick drops out
    m._derive_bucket(clock.monotonic())
    clock.advance(CUT_EXIT_CONFIRM_S * 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP  # a short dip is still a fill, not a breakdown
    clock.advance(CUT_EXIT_CONFIRM_S * 0.5 + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK  # sustained -> out, ~4s in, not 15s
    assert clock.monotonic() < m._dwell_s(EnergyBucket.DROP)


# --- Bible-integration Phase 3: transition_likely heuristic -----------------

def test_transition_likely_after_a_typically_long_peak():
    from livereact.core.metronome import TYPICAL_EXTENDED_PEAK_S
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    assert not m.transition_likely  # hasn't been dwelling long yet

    clock.advance(TYPICAL_EXTENDED_PEAK_S + 1.0)
    m._derive_bucket(clock.monotonic())
    assert m.transition_likely


def test_transition_likely_after_several_drops_in_one_track():
    from livereact.core.metronome import TRANSITION_LIKELY_DROP_CYCLE_THRESHOLD
    clock = FakeClock()
    m = _metronome(clock)
    m.drop_cycle = TRANSITION_LIKELY_DROP_CYCLE_THRESHOLD
    m._energy_norm = 0.1  # squarely BREAK -- not dwelling in DROP/PEAK at all
    m._derive_bucket(clock.monotonic())
    assert m.transition_likely  # drop_cycle alone is enough, independent of dwell time


def test_drop_cycle_resets_on_a_track_transition():
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_drop_marker(clock.monotonic())
    m._energy_norm = 0.02
    m._derive_drop_marker(clock.monotonic())
    clock.advance(1.5)
    m._derive_drop_marker(clock.monotonic())
    m._energy_norm = 0.85
    m._derive_drop_marker(clock.monotonic())
    assert m.drop_cycle == 1

    m.queue.put(TransitionMessage(clock.monotonic(), active=True))
    m.tick()
    assert m.drop_cycle == 0


# --- phrase-frozen palette (engines/palette.py, 2026-09-15) ------------------

def _bars(m: Metronome, n: int, bpm: float = 120.0) -> None:
    """Advance the metronome's bar counter by n full bars of master beats."""
    for _ in range(n):
        for beat in (2, 3, 4, 1):
            m.queue.put(BeatMessage(0.0, beat, bpm, True, 1))
            m.tick()


def test_palette_holds_within_a_phrase_and_can_change_at_the_boundary():
    from livereact.engines.palette import BREAK_WASH, PALETTE_PHRASE_BARS
    clock = FakeClock()
    m = _metronome(clock)
    first = m.tick().palette
    assert first in BREAK_WASH
    _bars(m, PALETTE_PHRASE_BARS - 2)  # still inside phrase 0
    assert m.tick().palette == first
    _bars(m, 2)  # bar 16 -> phrase 1
    assert m.tick().bar == PALETTE_PHRASE_BARS
    assert m.tick().palette == BREAK_WASH[1 % len(BREAK_WASH)]  # deterministic walk, next entry


def test_palette_repicks_on_a_bucket_family_change_but_not_on_a_peak_overlay():
    from livereact.engines.palette import BREAK_WASH, BUILD_WASH, DROP_WASH
    clock = FakeClock()
    m = _metronome(clock)
    m.tick()

    def with_bucket(bucket: EnergyBucket) -> str:
        # drive _derive_palette directly off a committed bucket (the same
        # way the PEAK/OUTRO overlay tests above poke the classifier state)
        m._bucket_committed = bucket
        m._derive_palette()
        return m.palette

    m._build_cue_until = clock.monotonic() + 60.0  # a confirmed build (an unconfirmed one renders as BREAK)
    assert with_bucket(EnergyBucket.BUILD) in BUILD_WASH  # white wash for the build strobe
    m._build_cue_until = None
    drop_pick = with_bucket(EnergyBucket.DROP)
    assert drop_pick in DROP_WASH
    assert with_bucket(EnergyBucket.PEAK) == drop_pick  # an overlay on the same drop -- same family
    assert with_bucket(EnergyBucket.BREAK) in BREAK_WASH  # downward change re-picks too: no red breaks


def test_a_track_transition_shifts_the_palette_walk():
    from livereact.engines.palette import BREAK_WASH
    clock = FakeClock()
    m = _metronome(clock)
    before = m.tick().palette
    m.queue.put(TransitionMessage(timestamp=clock.monotonic(), active=True))
    m.tick()
    assert m._track_seed == 1
    # same phrase index, same bucket -- a different entry purely because the track changed
    assert m.tick().palette == BREAK_WASH[1 % len(BREAK_WASH)]
    assert m.tick().palette != before


def test_palette_boundary_cannot_repick_a_color_that_just_landed():
    """session-20260915-144431: a set-anchored 16-bar boundary landed 0.7s
    after a drop commit and swapped the drop's color within one bar.
    Phrases now count from the bar the family was entered, a boundary
    never re-picks a pick younger than PALETTE_MIN_HOLD_BARS, and the drop
    family holds one color for its whole stay."""
    from livereact.engines.palette import BREAK_WASH, PALETTE_PHRASE_BARS
    clock = FakeClock()
    m = _metronome(clock)
    m.tick()
    m._bar = PALETTE_PHRASE_BARS - 1  # one bar before a set-anchored boundary
    m._bucket_committed = EnergyBucket.DROP
    m._derive_palette()
    drop_pick = m.palette
    for bar in range(PALETTE_PHRASE_BARS, PALETTE_PHRASE_BARS + 3 * PALETTE_PHRASE_BARS):
        m._bar = bar  # across the old boundary and the drop's own boundaries
        m._derive_palette()
        assert m.palette == drop_pick, bar
    m._bucket_committed = EnergyBucket.BREAK  # family change re-picks at once...
    m._derive_palette()
    first_break = m.palette
    assert first_break in BREAK_WASH
    m._bar += PALETTE_PHRASE_BARS // 2  # ...a boundary from bar 0's point of view, but too young
    m._derive_palette()
    assert m.palette == first_break
    m._bar += PALETTE_PHRASE_BARS // 2  # the break's own first phrase boundary
    m._derive_palette()
    assert m.palette != first_break


def test_a_one_bar_gap_past_the_drop_floor_does_not_exit_the_drop():
    """2026-09-16, recordings/session-20260916-110050: drums out for one bar
    between a track's first and second drop, then straight back in. Past
    the DROP dwell floor the ordinary 4-beat exit fired and BREAK showed
    for a bar of full-energy music. A DROP -> BREAK exit now always needs
    CUT_EXIT_CONFIRM_S (two bars) of BREAK-range reading, floor or not."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    clock.advance(m._dwell_s(EnergyBucket.DROP) + 1.0)  # well past the floor
    m._derive_bucket(clock.monotonic())

    m._energy_norm = 0.05  # the gap
    m._derive_bucket(clock.monotonic())
    clock.advance(EXIT_CONFIRM_DWELL_S + 0.5)  # longer than the old 4-beat exit, shorter than 2 bars
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP  # a one-bar gap is a fill

    m._energy_norm = 0.9  # drums back in
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP  # no re-commit needed, no bar-late cut

    m._energy_norm = 0.05  # a real breakdown
    m._derive_bucket(clock.monotonic())
    clock.advance(CUT_EXIT_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK


def test_build_cue_holds_build_against_a_quiet_reading_until_the_drop():
    """2026-09-16 (recordings/session-20260916-112651): a build the loudness
    can't see. The DJ's cue commits BUILD and holds it through energy_norm
    reading 0.0; a real drop still commits from energy; the cue clears."""
    from livereact.core.metronome import BUILD_CUE_HOLD_S
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.0
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK
    m.cue_build()
    assert m.build_cue_active
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.BUILD
    for _ in range(20):  # 20 s of "silence" by the classifier's reading
        clock.advance(1.0)
        m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD  # held
    assert m.tick().build_cue is True or m.build_cue_active  # exposed on the snapshot
    m._energy_norm = 0.95  # the drop
    m._energy_norm_inst = 0.95
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    assert not m.build_cue_active
    assert clock.monotonic() < BUILD_CUE_HOLD_S


def test_build_cue_second_press_cancels_and_the_hold_times_out():
    from livereact.core.metronome import BUILD_CUE_HOLD_S
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.0
    m._derive_bucket(clock.monotonic())
    m.cue_build()
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.BUILD
    m.cue_build()  # cancel
    assert not m.build_cue_active
    m._derive_bucket(clock.monotonic())
    assert m._bucket_committed == EnergyBucket.BREAK
    m.cue_build()
    m._derive_bucket(clock.monotonic())
    clock.advance(BUILD_CUE_HOLD_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert not m.build_cue_active  # timed out: the energy classifier is back in charge
    clock.advance(EXIT_CONFIRM_DWELL_S + m._dwell_s(EnergyBucket.BUILD) + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK


def _build_with_kickless_bars(clock, low=1.5, inst=0.2):
    """A committed BUILD whose low band sits at `low` and whose instantaneous
    level reads well under DROP."""
    from livereact.core.metronome import DROP_HIT_LOW_TAU_S
    m = _metronome(clock)
    m._energy_norm = 0.5
    m._derive_bucket(clock.monotonic())
    clock.advance(FAST_ENTRY_DWELL_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD
    m._low_avg = low
    for _ in range(int(3 * DROP_HIT_LOW_TAU_S / 0.05)):
        clock.advance(0.05)
        m._low_recent.append((clock.monotonic(), low))
        m._inst_recent.append((clock.monotonic(), inst))
        m._derive_bucket(clock.monotonic())
    return m


def _feed(m, clock, low, inst, seconds, dt=0.05):
    from livereact.core.metronome import DROP_HIT_LOW_HOLD_S, INST_PEAK_HOLD_S
    for _ in range(int(seconds / dt)):
        clock.advance(dt)
        now = clock.monotonic()
        m._low_recent.append((now, low)); m._inst_recent.append((now, inst))
        while now - m._low_recent[0][0] > DROP_HIT_LOW_HOLD_S: m._low_recent.popleft()
        while now - m._inst_recent[0][0] > INST_PEAK_HOLD_S: m._inst_recent.popleft()
        m._derive_bucket(now)


def _kicks(m, clock, n, low=18.0, inst=0.95, gap=0.30):
    """`n` distinct kicks: raw low band up for 150 ms, down for `gap`."""
    for _ in range(n):
        _feed(m, clock, low=low, inst=inst, seconds=0.15)
        _feed(m, clock, low=1.5, inst=inst, seconds=gap)


def test_an_armed_drop_commits_on_the_kick_pattern():
    """Josh, 2026-09-17: the drop is cued (D) or armed by a roll/rise; the
    first kick PATTERN after that -- two distinct kicks within a beat, what a
    4/4 drop supplies -- commits instantly, intensity pinned to 1.0. One
    kick alone never does (run 10, 439.6 s: a breakdown kick 4.5 s after D
    cut a false drop)."""
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m._bar = 116
    m.arm_drop()
    assert m.drop_armed and m._drop_arm_source == "dj"
    _kicks(m, clock, 1)
    _feed(m, clock, low=1.5, inst=0.95, seconds=1.0)
    assert m._base_committed == EnergyBucket.BUILD  # a lone kick
    t_hit = clock.monotonic()
    _kicks(m, clock, 2)
    assert m._base_committed == EnergyBucket.DROP
    assert clock.monotonic() - t_hit <= 1.0  # on the second kick
    assert m.drop_start_bar == 116
    assert m.drop_hit_intensity >= 0.95
    assert not m.drop_armed  # consumed by the drop it was armed for


def test_a_roll_arms_the_drop_without_touching_the_look():
    """Run 10 (2026-09-17): the roll's build cue rotated the look under the
    accent (blue -> white). Now a roll only ARMS; the bucket, and so the
    look, stay until the drop cuts."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.1  # the classifier says BREAK
    m._derive_bucket(clock.monotonic())
    from livereact.core.metronome import ROLL_ARM_MIN_S
    m.note_roll(True)
    m._derive_bucket(clock.monotonic())
    assert not m.drop_armed  # a roll must HOLD before it means anything (strays last under a second)
    clock.advance(ROLL_ARM_MIN_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK
    assert not m.build_cue_active
    assert m.drop_armed and m._drop_arm_source == "roll"
    m.note_roll(False)
    m._low_avg = 1.5
    _kicks(m, clock, 2)  # the hit after the roll
    assert m._base_committed == EnergyBucket.DROP


def test_arming_times_out_and_a_third_d_press_cancels():
    from livereact.core.metronome import DROP_ARM_HOLD_S
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m.arm_drop()
    m.arm_drop()           # second press: hard cut pending (see below), still armed
    assert m.drop_armed and m.drop_cut_pending
    m.arm_drop()           # third press: everything off
    assert not m.drop_armed and not m.drop_cut_pending
    m.arm_drop()
    clock.advance(DROP_ARM_HOLD_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert not m.drop_armed
    _kicks(m, clock, 4)  # kicks now are just kicks
    assert m._base_committed == EnergyBucket.BUILD


# --- Hard cut (Josh, 2026-09-19, recordings/session-20260919-104825): a kick
# re-entry at full drop level spent the arm 37 s before the real drop. D while
# armed by D cuts the drop on the next downbeat, whatever the audio says.

def _grid(m, clock, beat, phase):
    """Put the metronome on a live PDL grid at this beat/phase."""
    m._last_beat_time = clock.monotonic()
    m._beat_in_bar = beat
    m._beat_phase = phase


def test_a_second_d_press_cuts_the_drop_on_the_next_downbeat():
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m._bar = 130
    _grid(m, clock, beat=3, phase=0.2)
    m.arm_drop()
    m.arm_drop()
    assert m.drop_armed and m.drop_cut_pending
    for beat, phase in ((3, 0.6), (4, 0.1), (4, 0.9)):
        clock.advance(0.2)
        _grid(m, clock, beat, phase)
        m._derive_bucket(clock.monotonic())
        assert m._base_committed == EnergyBucket.BUILD  # nothing until the one
    clock.advance(0.2)
    m._bar = 131
    _grid(m, clock, beat=1, phase=0.05)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    assert m.drop_hit_intensity >= 0.99  # a counted-in drop is a full hit
    assert m.drop_start_bar == 131
    assert not m.drop_armed and not m.drop_cut_pending


def test_a_press_early_in_beat_one_is_that_downbeat_and_a_late_one_waits():
    from livereact.core.metronome import HARD_CUT_LATE_PHASE
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    _grid(m, clock, beat=1, phase=HARD_CUT_LATE_PHASE + 0.1)  # late in the one
    m.arm_drop(); m.arm_drop()
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD  # too late: next bar
    clock.advance(0.3)
    _grid(m, clock, beat=2, phase=0.1)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD
    # a fresh engine, pressed just after the one: cuts on that downbeat
    clock2 = FakeClock()
    m2 = _build_with_kickless_bars(clock2)
    _grid(m2, clock2, beat=1, phase=0.2)
    m2.arm_drop(); m2.arm_drop()
    m2._derive_bucket(clock2.monotonic())
    assert m2._base_committed == EnergyBucket.DROP


def test_a_hard_cut_without_a_pdl_grid_is_immediate():
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m._last_beat_time = None
    m._beat_in_bar = 3
    m.arm_drop(); m.arm_drop()
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP


def test_d_upgrades_a_roll_arm_instead_of_cutting():
    from livereact.core.metronome import ROLL_ARM_MIN_S
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m.note_roll(True)
    m._derive_bucket(clock.monotonic())
    clock.advance(ROLL_ARM_MIN_S + 0.1)
    m._derive_bucket(clock.monotonic())
    assert m.drop_armed and m._drop_arm_source == "roll"
    m.arm_drop()  # first D on a roll arm: a DJ arm, not a cut
    assert m.drop_armed and m._drop_arm_source == "dj" and not m.drop_cut_pending
    m.arm_drop()
    assert m.drop_cut_pending


def test_a_hard_cut_shows_in_the_snapshot():
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    _grid(m, clock, beat=2, phase=0.5)
    assert m._build_snapshot(clock.monotonic()).drop_cut_pending is False
    m.arm_drop(); m.arm_drop()
    assert m._build_snapshot(clock.monotonic()).drop_cut_pending is True


def test_d_changes_nothing_but_the_arming():
    """Run 11 (2026-09-17): D used to cue the build look ("the lights turn
    white"). D is preparation only: armed, bucket and look untouched."""
    clock = FakeClock()
    m = _metronome(clock)
    m.arm_drop()
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.BREAK
    assert not m.build_cue_active and m.drop_armed


def test_an_armed_cut_must_reach_the_last_drops_kick_level():
    """Run 11 at 177.6 s: armed by D, the kick coming back at build level
    (3-5 against the last drop's 8-12) made a pattern and cut a false drop.
    Once a drop has been heard, the armed cut must reach
    ARMED_KICK_VS_LAST_DROP of its kick peak."""
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m._drop_kick_ref = 10.0  # the last drop's kicks peaked at 10
    m.arm_drop()
    _kicks(m, clock, 3, low=4.0)   # a build-level kick pattern
    assert m._base_committed == EnergyBucket.BUILD
    _kicks(m, clock, 2, low=8.0)   # the drop's kicks
    assert m._base_committed == EnergyBucket.DROP


def test_a_drops_first_bars_set_the_kick_reference():
    from livereact.core.metronome import DROP_KICK_REF_BARS
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    m._bar = 10
    m.arm_drop()
    _kicks(m, clock, 2, low=12.0)
    assert m._base_committed == EnergyBucket.DROP and m._drop_kick_ref is None
    for bar in range(11, 11 + DROP_KICK_REF_BARS + 1):
        m._bar = bar
        _kicks(m, clock, 4, low=12.0)
    assert m._drop_kick_ref is not None and 6.0 <= m._drop_kick_ref <= 12.0


def test_a_builds_own_kicks_do_not_trip_the_hit_lead():
    """The build's kicks ARE its low-band level: instantaneous DROP readings
    on them (the reason the RMS-only lead is off for first drops) never
    clear the ratio, so the smoothed path keeps its job."""
    clock = FakeClock()
    m = _build_with_kickless_bars(clock, low=12.0, inst=0.9)
    _feed(m, clock, low=14.0, inst=0.95, seconds=2.0)
    assert m._base_committed == EnergyBucket.BUILD


def test_a_riser_with_no_low_band_does_not_trip_the_hit_lead():
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    _feed(m, clock, low=1.5, inst=0.95, seconds=2.0)  # loud but no kick
    assert m._base_committed == EnergyBucket.BUILD


def test_a_single_kick_that_does_not_hold_a_beat_does_not_commit():
    from livereact.core.metronome import DROP_HIT_CONFIRM_S
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    _feed(m, clock, low=18.0, inst=0.95, seconds=DROP_HIT_CONFIRM_S * 0.4)
    _feed(m, clock, low=1.5, inst=0.2, seconds=1.0)  # gone again
    assert m._base_committed == EnergyBucket.BUILD


# --- persisted normalizer seed (2026-09-17, config.py energy_norm) -----------

def _seeded(clock, seed):
    return Metronome(queue.Queue(), ShowState(), clock, threading.Event(), seed_range=seed)


def test_a_seeded_normalizer_classifies_from_the_first_second():
    """The 40 s warm-up (ENERGY_NORM_MIN_SAMPLES) is the reason a track that
    started 46 s before its drop got no build on 2026-09-15: energy_norm is
    0.0 until the window fills. Seeded with the last session's range, the
    same sustained loud passage reads high immediately -- and the flag says
    the seed is what it's reading against."""
    clock = FakeClock()
    m = _seeded(clock, (0.10, 0.50))
    _pump_audio(m, clock, rms=0.5, seconds=3.0)
    snap = m.tick()
    assert snap.energy_norm > 0.8
    assert snap.norm_seeded is True
    assert m.norm_warm is False


def test_an_unseeded_normalizer_is_unchanged():
    clock = FakeClock()
    m = _metronome(clock)
    _pump_audio(m, clock, rms=0.5, seconds=3.0)
    snap = m.tick()
    assert snap.energy_norm == 0.0
    assert snap.norm_seeded is False


def test_the_seed_hands_over_to_the_live_window_without_a_jump():
    """The seed is blended toward the live window by n/ENERGY_NORM_MIN_SAMPLES,
    so a seed that's wrong for tonight (0.1-0.5 saved, the set actually sits
    at 0.2-0.4) converges smoothly instead of snapping at sample 40. Pin: no
    single 1 s step moves energy_norm by more than a small amount, the seed
    flag drops exactly when the window is trusted on its own, and after
    that the value is what an unseeded metronome would produce."""
    from livereact.core.metronome import ENERGY_NORM_MIN_SAMPLES
    clock = FakeClock()
    m = _seeded(clock, (0.10, 0.50))
    _pump_audio(m, clock, rms=0.4, seconds=2.0)
    values = []
    for _ in range(ENERGY_NORM_MIN_SAMPLES + 5):
        _pump_audio(m, clock, rms=0.4, seconds=1.0)
        values.append((m.tick().energy_norm, m.norm_seeded))
    steps = [abs(b[0] - a[0]) for a, b in zip(values, values[1:])]
    assert max(steps) < 0.08, steps
    seeded_flags = [s for _, s in values]
    assert seeded_flags[0] is True and seeded_flags[-1] is False
    assert m.norm_warm is True
    ref_clock = FakeClock()
    ref = _metronome(ref_clock)
    _pump_audio(ref, ref_clock, rms=0.4, seconds=2.0 + ENERGY_NORM_MIN_SAMPLES + 5)
    assert m.tick().energy_norm == pytest.approx(ref.tick().energy_norm, abs=0.02)


def test_a_silent_seed_is_ignored():
    """A saved range whose ceiling is under ENERGY_NORM_MIN_CEILING is not a
    range (the last session never had music) -- behave as unseeded."""
    clock = FakeClock()
    m = _seeded(clock, (0.0, 0.01))
    _pump_audio(m, clock, rms=0.5, seconds=3.0)
    snap = m.tick()
    assert snap.energy_norm == 0.0
    assert snap.norm_seeded is False


def test_energy_norm_range_is_the_live_window_not_the_seed():
    """What gets persisted for the NEXT launch is the window this set
    actually measured, never the seed passed in."""
    clock = FakeClock()
    m = _seeded(clock, (0.10, 0.50))
    _pump_audio(m, clock, rms=0.3, seconds=50.0)
    floor, ceiling = m.energy_norm_range
    assert floor == pytest.approx(0.3, abs=0.02)
    assert ceiling == pytest.approx(0.3, abs=0.02)
    assert m.norm_warm is True


def test_a_drop_tail_with_gaps_between_hits_holds_the_drop():
    """The thinning tail of a drop (recordings/session-20260916-114402,
    355-372 s): sparse kicks over a bed, smoothed level in BUILD range
    (0.42-0.50) but the instantaneous level dropping to ~0 between hits.
    The 'sound right now' lead flips `raw` to BREAK on each gap, which
    restarts the BUILD candidate -- deliberately: only a section whose sound
    stays continuously above the break line (pads, the kick gone) is a
    post-drop build; this holds DROP until the real cut. Pooling both exits
    into one clock turned this tail into a 12 s BUILD with a strobe ramp."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._energy_norm_inst = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    assert m._base_committed == EnergyBucket.DROP
    m._energy_norm = 0.5
    t_end = clock.monotonic() + 4 * CUT_EXIT_CONFIRM_S
    i = 0
    while clock.monotonic() < t_end:
        m._energy_norm_inst = 0.05 if i % 8 == 0 else 0.5  # a gap under the break line every ~bar
        m._derive_bucket(clock.monotonic())
        clock.advance(0.2)
        i += 1
    assert m._base_committed == EnergyBucket.DROP


def test_a_sustained_post_drop_bed_becomes_a_build():
    """The case D3 exists for: after the drop the kick leaves but pads/vocal
    keep the instantaneous level above the break line the whole time --
    two bars of that is a build, and it commits before DROP's 8-bar floor."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.9
    m._energy_norm_inst = 0.9
    m._derive_bucket(clock.monotonic())
    clock.advance(AUTO_DROP_CONFIRM_S + 0.5)
    m._derive_bucket(clock.monotonic())
    m._energy_norm = 0.5
    m._energy_norm_inst = 0.4
    t_end = clock.monotonic() + CUT_EXIT_CONFIRM_S + 0.3
    while clock.monotonic() < t_end:
        m._derive_bucket(clock.monotonic())
        clock.advance(0.1)
    assert m._base_committed == EnergyBucket.BUILD
    assert clock.monotonic() < m._dwell_s(EnergyBucket.DROP)


def test_on_audio_copies_the_onset_rate_to_the_snapshot():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(AudioLevelMessage(clock.monotonic(), rms=0.3, band_low=0.5, band_mid=0.3, band_high=0.2,
                                   band_low_abs=6.0, band_mid_abs=3.6, band_high_abs=2.4,
                                   high_flux=0.2, high_onset_rate=8.5))
    snap = m.tick()
    assert snap.audio_high_onset_rate == 8.5
    assert snap.audio_high_flux == 0.2


def test_a_warm_range_is_handed_to_the_callback_periodically():
    """The seed must survive a kill -9 (how live runs end under --visualize):
    the metronome hands its warm range out every RANGE_PERSIST_INTERVAL_S."""
    from livereact.core.metronome import RANGE_PERSIST_INTERVAL_S
    clock = FakeClock()
    got = []
    m = Metronome(queue.Queue(), ShowState(), clock, threading.Event(), log_interval=1e9,
                  range_callback=lambda f, c: got.append((f, c)))
    _pump_audio(m, clock, rms=0.3, seconds=30.0)
    assert got == []  # not warm yet
    _pump_audio(m, clock, rms=0.3, seconds=RANGE_PERSIST_INTERVAL_S + 15.0)
    assert len(got) >= 1
    assert got[-1][1] == pytest.approx(0.3, abs=0.02)


def test_unarmed_kicks_never_lead_the_drop():
    """Runs 8/9 (2026-09-17): one drop-level kick committed a DROP 15 s early.
    Unarmed, kicks are just kicks -- the drop is the smoothed level holding
    drop range for AUTO_DROP_CONFIRM_S, nothing faster."""
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    for _ in range(4):
        _feed(m, clock, low=18.0, inst=0.95, seconds=0.15)
        _feed(m, clock, low=1.5, inst=0.95, seconds=0.30)
    assert m._base_committed == EnergyBucket.BUILD

def test_a_strong_rise_arms_the_drop_by_itself():
    """The autonomous path: core/rise.py's index held over RISE_ARM for
    RISE_ARM_BARS arms the drop (source 'rise'); the kick pattern then cuts."""
    from livereact.core.rise import RISE_ARM_BARS, RISE_BASE_BARS, RISE_BASE_GAP_BARS
    clock = FakeClock()
    m = _build_with_kickless_bars(clock)
    for _ in range((RISE_BASE_BARS + RISE_BASE_GAP_BARS) * 4):
        m._rise.on_beat(0.10, 4.0)  # flat hats
    for _ in range(RISE_ARM_BARS * 4 + 8):
        m._rise.on_beat(0.30, 10.0)  # the rise
        m._derive_bucket(clock.monotonic())
        clock.advance(0.47)
    assert m.drop_armed and m._drop_arm_source == "rise"
    assert m.tick().rising
    _kicks(m, clock, 2)
    assert m._base_committed == EnergyBucket.DROP


def test_an_unconfirmed_build_renders_as_the_break_it_came_from():
    """Run 12 (2026-09-17): the loudness classifier's BUILD brought Rising
    Tide (white) and the wash ramp with no cue and no arming -- "the lights
    turned completely white and slowly started increasing in brightness".
    The look follows the classifier's BUILD only once a build CUE (C, or
    the kick-level rule) confirms it -- arming never changes the look; the
    classifier itself is unchanged."""
    clock = FakeClock()
    m = _metronome(clock)
    m._energy_norm = 0.5
    m._derive_bucket(clock.monotonic())
    clock.advance(FAST_ENTRY_DWELL_S + 0.5)
    m._derive_bucket(clock.monotonic())
    snap = m._build_snapshot(clock.monotonic())
    assert m._base_committed == EnergyBucket.BUILD
    assert snap.classifier_bucket == "build" and snap.bucket == "break"
    m.arm_drop()
    assert m._build_snapshot(clock.monotonic()).bucket == "break"  # arming changes nothing on stage
    m.arm_drop()  # disarm
    m.cue_build()
    assert m._build_snapshot(clock.monotonic()).bucket == "build"  # C confirms the build look


def test_unmapped_operator_controls_reach_the_control_callback_not_the_mixer_intent():
    """2026-09-30: MIDI controls learned as operator cues ("laser_cue") are
    handed to control_callback; mixer intents keep driving energy as before."""
    from livereact.core.show_state import ShowState
    import threading
    seen = []
    clock = FakeClock()
    m = Metronome(queue.Queue(), ShowState(), clock, threading.Event(), hz=60.0,
                  control_callback=lambda name, v: seen.append((name, v)))
    m.queue.put(MidiControlMessage(0.0, "laser_cue", 1.0))
    m.queue.put(MidiControlMessage(0.0, "laser_cue", 1.5))   # clamped like every control
    m.queue.put(MidiControlMessage(0.0, "fader_a", 1.0))
    m.tick()
    assert seen == [("laser_cue", 1.0), ("laser_cue", 1.0)]
    assert m._midi["fader_a"] == 1.0


# ---------------------------------------------------------------- roll START (core/roll_period.py, 2026-10-22)

def _snare_blocks(m, clock, seconds, level_fn, rms=0.05):
    """Feed audio one BLOCK at a time (the roll detector's lags are in blocks),
    quiet in loudness -- the labeled rolls sit at energy_norm ~0.1 -- with the
    150-500 Hz band following `level_fn(block_index)`. Ticks the metronome per
    block and returns the first tick DROP was committed on, or None."""
    from livereact.inputs.audio import BLOCK
    dt = BLOCK / 44100.0
    first = None
    for i in range(int(seconds / dt)):
        clock.advance(dt)
        now = clock.monotonic()
        m.queue.put(AudioLevelMessage(now, rms=rms, band_low=0.5, band_mid=0.3, band_high=0.2,
                                      band_low_abs=3.0, band_mid_abs=2.0, band_high_abs=0.3,
                                      band_snare_abs=level_fn(i)))
        snap = m.tick()
        if first is None and snap.classifier_bucket == "drop":
            first = now
    return first


def test_a_snare_roll_start_commits_the_drop_unarmed_and_quiet():
    """Josh's drop begins at the roll (spec/PENDING_RULES.md); STRUCT-03 wants
    DROP within two beats of it. The real rolls are quiet, so no loudness
    path can see them -- the 150-500 Hz band's 16th-note periodicity does."""
    import random
    rng = random.Random(11)
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(BeatMessage(clock.monotonic(), 1, 130.0, True, 1))
    beat = 60.0 / 130.0
    every = round(beat / 4 / (1024 / 44100.0))      # blocks per 16th
    assert _snare_blocks(m, clock, 12 * 4 * beat, lambda i: 1.0 + rng.uniform(-0.3, 0.3)) is None
    t0 = clock.monotonic()
    first = _snare_blocks(m, clock, 4 * 4 * beat, lambda i: (3.0 if i % every == 0 else 1.0) + rng.uniform(-0.2, 0.2))
    assert first is not None, "the roll never committed a DROP"
    assert (first - t0) / beat <= 2.5
    assert m.tick().drop_start_bar is not None
    assert not m.drop_armed  # the commit consumed any arming, like every other commit


def test_a_periodic_texture_from_the_start_never_commits_a_drop():
    """An 8th-note bassline in the same band (Sweet Nothing's intro) is a
    texture, not a roll: no step up over the reference, so no DROP."""
    import random
    rng = random.Random(12)
    clock = FakeClock()
    m = _metronome(clock)
    beat = 60.0 / 124.0
    every = round(beat / 2 / (1024 / 44100.0))
    first = _snare_blocks(m, clock, 24 * 4 * beat, lambda i: (3.0 if i % every == 0 else 1.0) + rng.uniform(-0.2, 0.2))
    assert first is None
    assert m.tick().roll_period_score >= 0.5   # it is periodic -- the detector saw it and declined


def test_the_roll_start_commit_can_be_switched_off(monkeypatch):
    import random
    from livereact.core import metronome as met
    monkeypatch.setattr(met, "ROLL_START_COMMITS", False)
    rng = random.Random(13)
    clock = FakeClock()
    m = _metronome(clock)
    beat = 60.0 / 124.0
    every = round(beat / 4 / (1024 / 44100.0))
    _snare_blocks(m, clock, 12 * 4 * beat, lambda i: 1.0 + rng.uniform(-0.3, 0.3))
    first = _snare_blocks(m, clock, 4 * 4 * beat, lambda i: (3.0 if i % every == 0 else 1.0) + rng.uniform(-0.2, 0.2))
    assert first is None


def test_snare_band_and_roll_score_reach_the_snapshot():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(AudioLevelMessage(clock.monotonic(), rms=0.3, band_low=0.5, band_mid=0.3, band_high=0.2,
                                  band_low_abs=10.0, band_mid_abs=6.0, band_high_abs=4.0, band_snare_abs=2.5))
    snap = m.tick()
    assert snap.audio_band_snare_abs == 2.5
    assert snap.roll_period_score == 0.0  # one block is not a window
