"""ShowState: the single shared bus between the metronome and the engines/UI.

Modeled as a single-slot publisher of an immutable ``ShowSnapshot`` (the same
atomic-reference-swap pattern as the DMX frame buffer). The metronome is the sole
writer; it builds a complete frozen snapshot each tick and publishes it. Readers
get the latest snapshot with no lock held afterwards and no possibility of a torn
read — every field they see came from the same instant (docs/05 §4.1, §4.2).

Field provenance (docs/04 §4.3) is documented per field below:
  measured      = comes straight off PDL/MIDI
  interpolated  = computed locally between events (beat_phase)
  inferred      = derived heuristically (bar, energy, in_transition)
Note: absolute track position is intentionally absent — the XDJ-XZ does not
transmit it (docs/04 §4.1), so no feature may depend on it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ShowSnapshot:
    """An immutable, tear-free view of the show at one instant."""

    t: float = 0.0                       # monotonic time this snapshot was built

    # --- musical clock ---
    bpm: float = 124.0                   # measured (default until first beat)
    beat_phase: float = 0.0              # interpolated, 0.0-1.0 within the beat
    beat_in_bar: int = 1                 # measured, 1-4
    bar: int = 0                         # inferred (counts downbeats)

    # --- transport / decks ---
    playing: bool = False                # measured
    loop_active: bool = False            # measured
    on_air: tuple[int, ...] = ()         # measured
    master_deck: int | None = None       # measured

    # --- MIDI intent (measured, normalized 0.0-1.0) ---
    crossfader: float = 0.5
    fader_a: float = 0.0
    fader_b: float = 0.0
    eq_low_a: float = 0.5
    eq_mid_a: float = 0.5
    eq_high_a: float = 0.5
    eq_low_b: float = 0.5
    eq_mid_b: float = 0.5
    eq_high_b: float = 0.5

    # --- real audio (measured, docs/14) — kept SEPARATE from the MIDI-intent
    # fields above deliberately (provenance): these come from actually analyzing
    # the sound, not mixer-knob position. band_low/mid/high are normalized
    # fractions for color; band_low_abs is the low band's un-normalized energy,
    # for the laser onset trigger specifically — VERIFIED these must stay two
    # separate signals (tools/simulations/audio_pipeline_sim_v2.py), not one
    # derived from the other. band_mid_abs/band_high_abs (2026-09-14,
    # build_snare_accent) extend the same idea to the other two bands and
    # feed engines/reactive.py's Synth Sync (mid) and core/snare_trigger.py's
    # RollRiseDetector (high).
    audio_energy: float = 0.0
    audio_band_low: float = 0.333
    audio_band_mid: float = 0.333
    audio_band_high: float = 0.334
    audio_band_low_abs: float = 0.0
    audio_band_mid_abs: float = 0.0
    audio_band_high_abs: float = 0.0
    # Block-rate onset density in the high band (inputs/audio.py ONSET_*,
    # 2026-09-17): onsets/s and the spectral flux behind it. A snare roll is
    # a rate, not a level -- core/snare_trigger.py reads this.
    audio_high_onset_rate: float = 0.0
    audio_high_flux: float = 0.0
    # 150-500 Hz magnitude (inputs/audio.py SNARE_HZ, 2026-10-22) and the
    # roll-start detector's periodicity score on it (core/roll_period.py):
    # a snare roll is 8th/16th-note structure in the snare's body band. The
    # metronome commits DROP on a confirmed roll start (Josh's drop begins at
    # the roll, spec/PENDING_RULES.md); exposed for the recorder/harness.
    audio_band_snare_abs: float = 0.0
    roll_period_score: float = 0.0

    # --- derived ---
    energy: float = 0.0                  # inferred 0.0-1.0
    # inferred 0.0-1.0: `energy` rescaled against this SET's own recent
    # loudness range (a rolling percentile window), not a fixed absolute
    # threshold — bucket_for_energy() should read this, not raw `energy`,
    # so "DROP" means "near the top of what this set has actually contained,"
    # independent of mixer gain staging or a given track's mastering (2026-09-07,
    # see livereact-project memory). Real-audio only; passes raw `energy`
    # through unscaled on the MIDI-fallback path (see metronome.py).
    energy_norm: float = 0.0
    # inferred: bucket_for_energy(energy_norm), DEBOUNCED (2026-09-07, see
    # metronome.py's BUCKET_MIN_DWELL_S) -- a candidate bucket must persist for
    # a real stretch of time before this changes, so it doesn't flip every time
    # energy_norm brushes a boundary. Read this, not a fresh bucket_for_energy
    # call, wherever "what bucket are we in" matters (reactive.py, phrase.py,
    # engines/laser.py all do) -- one shared, already-debounced answer, not
    # three independent (and previously drift-prone) ones. Plain str, not the
    # EnergyBucket enum itself: this module can't import scenes/base.py (it
    # imports scenes/base.py's own Scene.render(snap: ShowSnapshot) type hint,
    # so the reverse import would be circular) -- reconstruct via
    # EnergyBucket(snap.bucket) at call sites that already import it.
    bucket: str = "break"
    # What the loudness classifier itself says (core/metronome.py's
    # _bucket_committed). `bucket` above is the LOOK bucket: an unconfirmed
    # BUILD guess renders as BREAK (2026-09-17, run 12) -- the two differ only
    # then. Recorder/replay field; engines read `bucket`.
    classifier_bucket: str = "break"
    # Bible-integration Phase 2 (2026-09-11): loud-to-quiet-to-loud drop
    # marker (core/metronome.py's `_derive_drop_marker`) -- a corroborating
    # signal alongside bucket classification, not a replacement for it.
    # `drop_marker` is True for exactly one tick, the moment a marker
    # confirms; `drop_cycle` is how many have fired since the last detected
    # track transition (see `in_transition` below) -- an approximate,
    # same-track occurrence count ("this is drop #2"), not true song-
    # structure knowledge.
    drop_cycle: int = 0
    drop_marker: bool = False
    # Captured once per real DROP commitment (core/metronome.py's
    # _derive_bucket), held static from there, not chased continuously --
    # how loud THIS hit actually measured (2026-09-11, Josh's correction:
    # drop intensity should reflect the real sound, not a fixed constant).
    # Default matches DROP_INTENSITY_FLOOR for callers/tests predating this.
    drop_hit_intensity: float = 0.85
    # The bar the current DROP's energy rise was FIRST seen in (the raw
    # classifier's candidate start), not the bar it was committed in --
    # 2026-09-15, recordings/session-20260915-121503: smoothing + dwell put
    # the commit a full bar (sometimes several) after the audible hit, so
    # anything counting phrases from the commit bar landed on the wrong bar.
    # Still a lagging estimate, not a true downbeat detection (there is no
    # song-structure signal here, see LIGHTING_BIBLE.md §9.9) -- just the
    # earliest bar the sound itself supports. None until the first drop.
    drop_start_bar: int | None = None
    # True while the DJ's build cue is holding BUILD (core/metronome.py
    # cue_build, 2026-09-16) -- for the recorder/console, no engine reads it.
    build_cue: bool = False
    # Who raised the held BUILD: "dj" (the C key) or "auto" (core/build_rule.py's
    # kick-level rule, 2026-09-17); "" when no cue is held.
    build_cue_source: str = ""
    # True while the drop is ARMED (core/metronome.py arm_drop / note_roll,
    # 2026-09-17): the next drop-level kick commits it instantly. Source is
    # "dj" (D key) or "roll" (the snare-roll accent).
    drop_armed: bool = False
    drop_arm_source: str = ""
    # True between a second D press and the downbeat that cuts the drop
    # (core/metronome.py HARD_CUT_LATE_PHASE, 2026-09-19). Console/recorder only.
    drop_cut_pending: bool = False
    # The rise phase (core/rise.py, 2026-09-17): `rising` while the high band's
    # level + onset density are climbing against the bars before; `rise_index`
    # is the intensity ratio (1.0 = no change). engines/reactive.py runs the
    # build strobe only while armed AND rising, at a rate from the index.
    rising: bool = False
    rise_index: float = 1.0
    # True while energy_norm is still (partly) measured against the LAST
    # session's persisted range rather than this set's own window
    # (core/metronome.py seed_range, config.py energy_norm seed, 2026-09-17)
    # -- so a capture shows which regime an early classification came from.
    norm_seeded: bool = False
    # True while the classifier has a DROP candidate under confirmation
    # (core/metronome.py's _derive_bucket: the smoothed level has crossed
    # the DROP threshold and the FAST_ENTRY_DWELL_S clock is running). The
    # hit itself has already sounded by now -- engines/reactive.py uses this
    # to end the build strobe on the hit rather than ~2 beats later at the
    # commit (2026-09-15, recordings/session-20260915-140630, Josh: "the
    # strobing continued after the beat drop"). Cleared on commit or if the
    # level backs off before confirming.
    drop_candidate: bool = False
    # Phrase-frozen palette entries (engines/palette.py, LIGHTING_BIBLE.md
    # §3.2/§3.8), picked by core/metronome.py's `_derive_palette` on 16-bar
    # phrase boundaries and bucket-family changes only -- 2026-09-15, third
    # live capture: per-tick hue from the audio bands read as random color
    # flicker. Every scene and the Reactive layer read these (scenes/base.py's
    # scene_color/spot_color); nothing derives wash hue from audio anymore.
    palette: str = "deep_blue"
    palette_spot: str = "deep_blue"
    # Bible-integration Phase 3 heuristic (core/metronome.py's
    # _apply_bucket_overlay) -- a plain rule, NOT prediction or ML: true when
    # a sustained DROP/PEAK has run past a typical duration, or the track's
    # already had several drops. Not consumed by anything yet -- see
    # docs/bible_integration/SPEC_DEVIATIONS.md's D1 entry.
    transition_likely: bool = False
    # `build_to_drop_bridge` (Bible-integration Phase 5): true while a DROP
    # is confirmed but held for beat-quantized release (core/metronome.py's
    # `_pending_drop_release`, Phase 3) -- "holds dark on the final bar,
    # releases to the DROP look on the downbeat." See merge/merge.py's
    # finalize() for where this actually forces the hard cut. Since
    # 2026-09-15 the energy classifier never sets this (its confirmation
    # always trails the audible hit, so the "final bar" was the drop's own
    # first bar -- measured live); it stays for a future predictive cue.
    drop_pending: bool = False
    in_transition: bool = False          # inferred / synthetic

    # --- health / confidence ---
    free_running: bool = True            # clock free-running (no recent master beat)
    pdl_fresh: bool = False              # a PDL message arrived recently
    midi_fresh: bool = False             # a MIDI message arrived recently
    audio_fresh: bool = False            # an AudioLevelMessage arrived recently

    def summary(self) -> str:
        """One-line human summary for periodic logging."""
        clock = "free-run" if self.free_running else "locked"
        return (
            f"bpm={self.bpm:5.1f} bar={self.bar:>4} beat={self.beat_in_bar} "
            f"phase={self.beat_phase:.2f} energy={self.energy:.2f} "
            f"transition={'Y' if self.in_transition else 'n'} "
            f"clock={clock} pdl={'fresh' if self.pdl_fresh else 'STALE'} "
            f"midi={'fresh' if self.midi_fresh else 'stale'} "
            f"audio={'fresh' if self.audio_fresh else 'stale'}"
        )

    def replace(self, **changes) -> "ShowSnapshot":
        return replace(self, **changes)


class ShowState:
    """Thread-safe holder of the latest immutable :class:`ShowSnapshot`."""

    def __init__(self, initial: ShowSnapshot | None = None) -> None:
        self._lock = threading.Lock()
        self._current = initial or ShowSnapshot()

    def publish(self, snapshot: ShowSnapshot) -> None:
        """Atomically make ``snapshot`` current. Called only by the metronome."""
        with self._lock:
            self._current = snapshot

    def snapshot(self) -> ShowSnapshot:
        """Return the latest snapshot (immutable; safe to read without the lock)."""
        with self._lock:
            return self._current
