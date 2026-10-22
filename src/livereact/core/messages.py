"""Immutable messages that flow from input sources to the metronome.

Input callbacks (real PDL/MIDI listeners, or the demo source) never touch ShowState
directly — they push these onto a ``queue.Queue`` that the metronome drains at the
top of each tick. This is the clean producer/consumer boundary that makes input
handling unit-testable and eliminates input-side locking bugs (docs/05 §4.3).

Every message carries a monotonic ``timestamp`` (from the source's injected clock)
so the metronome can interpolate beat phase and track per-source staleness.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeatMessage:
    """A beat boundary from Pro DJ Link (or the demo).

    ``beat_in_bar`` is the measured 1-4 counter. Only the *master* deck's beats
    drive the local clock/bar inference; non-master beats are informational.
    """

    timestamp: float
    beat_in_bar: int      # 1..4 (measured)
    bpm: float            # measured tempo (incl. pitch)
    is_master: bool
    player: int


@dataclass(frozen=True)
class StatusMessage:
    """Periodic transport/deck status from PDL (measured)."""

    timestamp: float
    on_air: tuple[int, ...]
    master_deck: int | None
    playing: bool
    loop_active: bool


@dataclass(frozen=True)
class MidiControlMessage:
    """One normalized mixer control. ``control`` is a semantic name (e.g.
    ``"crossfader"``, ``"fader_a"``, ``"eq_low_b"``); ``value`` is 0.0-1.0.

    The raw CC->name/normalization mapping happens in the MIDI listener / demo
    source, so everything downstream is hardware-agnostic.
    """

    timestamp: float
    control: str
    value: float


@dataclass(frozen=True)
class AudioLevelMessage:
    """One analysis block from the real audio pipeline (docs/14).

    ``band_low/mid/high`` are normalized fractions (sum to ~1.0) for color —
    band dominance, same shape as the MIDI-derived EQ concept.
    ``band_low_abs`` is the SAME low band's *absolute* (non-normalized) energy,
    for the laser onset trigger specifically — VERIFIED these need to be two
    separate signals (tools/simulations/audio_pipeline_sim_v2.py): the
    normalized fraction saturates near-constant whenever any continuous bass
    is present, losing the contrast an onset trigger needs, while the
    absolute version keeps it. Don't derive one from the other downstream.
    ``band_mid_abs``/``band_high_abs`` are the same absolute-energy idea for
    the other two bands (2026-09-14, build_snare_accent Step 0) -- a snare/
    hi-hat onset detector needs these for the identical structural reason.
    ``high_flux``/``high_onset_rate`` (2026-09-17) are the block-rate onset
    measurement for core/snare_trigger.py's roll accent -- spectral flux over
    the high band and the trailing onsets/s (inputs/audio.py's ONSET_*); a
    roll is a rate the 10 Hz recorder can't see, so it's measured at the
    source. Default 0.0 so other producers (demo, replay) needn't set them.
    """

    timestamp: float
    rms: float           # smoothed, VU-meter-style loudness (0..~1, unbounded above clipping)
    band_low: float
    band_mid: float
    band_high: float
    band_low_abs: float
    band_mid_abs: float
    band_high_abs: float
    high_flux: float = 0.0
    high_onset_rate: float = 0.0
    band_snare_abs: float = 0.0  # 150-500 Hz absolute magnitude (SNARE_HZ, 2026-10-22): the snare's body,
                                 # for core/roll_period.py's roll-start detector. Default 0.0 like the onset
                                 # fields so other producers (demo, replay, tests) needn't set it.


@dataclass(frozen=True)
class TransitionMessage:
    """A synthetic transition marker from the demo source.

    Real transition *detection* (from crossfader/fader motion) is the Transition
    Engine's job in Phase 6; until then the demo asserts it directly so the rest of
    the pipeline can be exercised.
    """

    timestamp: float
    active: bool
