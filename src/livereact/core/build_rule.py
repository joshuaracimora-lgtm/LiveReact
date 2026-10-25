"""Automatic build cue from the kick's level -- the "mix-in build" the
loudness classifier can't see (2026-09-17).

The problem (recordings/session-20260916-110050, run 3): the next track's
build was mixed in under the previous track's breakdown at the SAME loudness
-- energy, low, mid and high all within a few percent -- so `energy_norm`
read BREAK/OUTRO until the hit. Run 4/5's track (112651/114402) is the
other shape: a kick-only intro that reads LOUDER than the mid-heavy build.
The DJ build cue (C, `Metronome.cue_build`) exists for exactly this, and
stays the primary tool. This module is the automatic part, ruled by Josh's
call that a FALSE build start (a strobe ramp during something that isn't a
build) is worse than a missed one: it ships only because it fires on zero
non-builds across every real capture in `recordings/` (scored with
`tools/simulations/replay_state.py --no-cue`), and it is deliberately
narrow.

What every one of those builds had in common, per bar: THE KICK CAME BACK
AT ROUGHLY HALF THE DROP'S LEVEL. Bar-mean low band 4.5-7.5 against drops
at 12-13 (run 3: 5-6.5 for 16 bars; run 5's second build: 4.6-7.5 for 16
bars; run 4's: 4.9-7.2 for 10 bars), while breakdowns sat at 1-2.5, drops
at 11-13, and that track's loud intro at 7-11. So: once a drop has been
heard (its first four bars set the reference kick level), four consecutive
bars with the low band inside [KICK_BAND_LO, KICK_BAND_HI] x that reference
-- while the loudness classifier still says BREAK and the music isn't
silent -- is a build. The first build of a set has no reference and is the
C key's job; that's the disclosed cost of the zero-false-start bar.

Release guard: the cue holds BUILD against the BREAK reading (same
mechanism as the C key) but for AUTO_HOLD_BARS, not the DJ cue's 32, and it
lets go early if the kick falls under the band for KICK_GONE_BARS bars without
a drop landing (a kick rising above it is the drop arriving: see on_bar) -- the pre-drop breath is one or two bars; anything longer
was a breakdown with a soft kick, and the ramp must not sit on it.

Per-bar features only (the bar's mean low-band level and mean energy);
no onset density, no spectral slope -- a "high band rising over bars" rule
was tried on 2026-09-16 and false-started on two intros. Thresholds are
[CONVENTION] against those eight captures, not a general truth; re-score
every new capture with the replay tool before touching them.
"""
from __future__ import annotations

KICK_BAND_LO = 0.30      # low-band bar mean, as a fraction of the last drop's kick level
KICK_BAND_HI = 0.65
BUILD_BARS = 4           # consecutive bars inside the band before firing
REF_BARS = 4             # bars of the drop that set the reference
MIN_ENERGY = 0.15        # bar-mean raw energy under this is silence/fade, never a build
AUTO_HOLD_BARS = 24      # the auto cue's own hold (the DJ's is BUILD_CUE_HOLD_BARS = 32)
KICK_GONE_BARS = 4       # kick out of the band this long without a drop -> release


class KickLevelBuildRule:
    """Feed it one bar at a time; `on_bar(...)` returns True on the bar the
    rule fires. `note_drop_bar(low)` during a drop's first bars sets the
    reference. `release_due()` says the hold should end (kick gone)."""

    def __init__(self) -> None:
        self.ref: float | None = None   # the last drop's kick level (bar-mean low band)
        self._ref_bars: list[float] = []
        self._streak = 0
        self._gone = 0
        self.armed = False               # True while an auto cue this rule raised is held

    # --- reference ------------------------------------------------------
    def note_drop_bar(self, low: float) -> None:
        """Call once per bar while the base state is DROP; the first REF_BARS
        set (or re-set) the reference so the next track's drop re-calibrates."""
        if len(self._ref_bars) < REF_BARS:
            self._ref_bars.append(low)
            self.ref = sum(self._ref_bars) / len(self._ref_bars)

    def drop_ended(self) -> None:
        """The base left DROP: the next drop will set a fresh reference."""
        self._ref_bars = []
        self.armed = False
        self._gone = 0

    # --- the rule -------------------------------------------------------
    def in_band(self, low: float) -> bool:
        return self.ref is not None and KICK_BAND_LO * self.ref <= low <= KICK_BAND_HI * self.ref

    def on_bar(self, low: float, energy: float, base_is_break: bool) -> bool:
        """One completed bar. Returns True on the bar the build cue fires."""
        if self.armed:
            # Holding: watch for the kick leaving the band without a drop --
            # DOWNWARD only (2026-10-25). A kick climbing above the band is
            # the drop arriving, not the kick going: on Somebody To Love the
            # cue raised at 177 s (kick 3.0 vs ref 6.1) was released on the
            # labeled 184.63 s drop itself, the kick at 4-6 (0.65-1.0x ref)
            # through the roll, and the show fell back to BREAK on the drop.
            self._gone = self._gone + 1 if low < KICK_BAND_LO * self.ref else 0
            return False
        if self.ref is None or not base_is_break or energy < MIN_ENERGY or not self.in_band(low):
            self._streak = 0
            return False
        self._streak += 1
        if self._streak >= BUILD_BARS:
            self._streak = 0
            self._gone = 0
            self.armed = True
            return True
        return False

    def release_due(self) -> bool:
        return self.armed and self._gone >= KICK_GONE_BARS

    def released(self) -> None:
        self.armed = False
        self._gone = 0
        self._streak = 0
