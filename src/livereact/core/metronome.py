"""Metronome: the single writer of the musical clock and ShowState.

It drains the input queue, holds the authoritative clock fields (bpm, beat phase,
beat-in-bar, bar), and publishes a fresh immutable snapshot every tick (~100 Hz).
This is the component that turns jittery beat *events* into a smooth, interpolated
beat *clock* — engines must read phase from here, never off raw packet arrival
(docs/05 §4.2, §7; docs/04 §4.1).

Behaviors:
  - Interpolate beat_phase between master beats from the monotonic clock.
  - Re-anchor to each master beat (hard resync); infer the bar from downbeats.
  - Free-run at the last known BPM if master beats stop arriving (graceful
    degradation, NFR-5) and flag low confidence.
  - Derive an energy estimate: real audio RMS passed through as-is (AudioAnalyzer
    already applies its own tuned attack/release envelope — smoothing it again
    here just adds latency, found by measuring real lights-vs-audio lag, docs/14
    §5), MIDI fader/EQ intent smoothed here instead, since that raw proxy is
    genuinely noisy/jumpy and has no smoothing of its own (docs/10 B8).
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from collections import deque

from ..clock import Clock, LoopTimer
from ..engines import palette as palette_mod
from ..scenes.base import EnergyBucket
from .build_rule import AUTO_HOLD_BARS, BUILD_BARS as AUTO_BUILD_BARS, KickLevelBuildRule
from .rise import RiseDetector
from .roll_period import RollPeriodDetector
from ..scenes import catalog as _catalog
from ..scenes.catalog import _BUCKET_ORDER, BUILD_MAX, LEGAL_TRANSITIONS, bucket_for_energy_with_hysteresis
from .messages import AudioLevelMessage, BeatMessage, MidiControlMessage, StatusMessage, TransitionMessage
from .show_state import ShowSnapshot, ShowState

log = logging.getLogger(__name__)

PDL_STALE_AFTER = 1.0    # seconds without a PDL message -> not fresh
MIDI_STALE_AFTER = 1.0   # seconds without a MIDI message -> not fresh
AUDIO_STALE_AFTER = 1.0  # seconds without an AudioLevelMessage -> not fresh
ENERGY_SMOOTHING = 0.05  # EMA factor per tick (~0.2 s time constant at 100 Hz) —
                         # MIDI-proxy fallback only; real audio bypasses this,
                         # see _derive_energy

# Transition detection (Phase 6): a crossfader that moves more than CROSS_MOVE
# within CROSS_WINDOW seconds — or a master-deck handoff — marks a transition,
# which then holds for TRANSITION_COOLDOWN after motion stops.
CROSS_WINDOW = 0.25
CROSS_MOVE = 0.08
TRANSITION_COOLDOWN = 0.5

# Adaptive energy normalization (2026-09-07, FOUND LIVE, see [[livereact-project]]
# memory): `bucket_for_energy` used to be fed raw `energy` directly, compared
# against FIXED absolute thresholds (0.30/0.55/0.80). Real audio's `energy` is
# raw mic RMS run through `ENERGY_GAIN`, a manually hand-tuned constant
# calibrated against one specific night's mixer gain staging (inputs/audio.py) —
# it does NOT adapt to a different session's gain staging or a different
# track's mastering loudness. Confirmed live (2026-09-07): a whole real ~5.5min
# session's energy topped out at 0.666 — the 0.80 DROP threshold never fired
# ONCE, even during what was audibly the drop. `energy_norm` instead rescales
# `energy` against THIS SET's own recent loudness range (a rolling percentile
# window), so "DROP" means "near the top of what's actually been playing,"
# genre/mastering/gain-staging independent — this is what actually answers
# "how do we recognize the drop in every song."
ENERGY_NORM_SMOOTH_TAU_S = 3.0        # EMA smoothing BEFORE normalizing -- roughly
                                       # a beat or two at house/techno tempo.
                                       # FOUND BY TESTING (offline replay against
                                       # a real captured session, 2026-09-07):
                                       # normalizing the raw, unsmoothed per-tick
                                       # energy directly flipped the bucket several
                                       # times PER SECOND -- ordinary beat-to-beat
                                       # amplitude swings are a big fraction of any
                                       # short window's own range. Smoothing first
                                       # makes the classification reflect a song
                                       # SECTION's character, not one kick.
ENERGY_NORM_ATTACK_TAU_S = 0.4        # smoothing tau for a RISE only (2026-09-15,
                                       # recordings/session-20260915-121503): the
                                       # flapping the 3s tau exists to stop is the
                                       # DOWNWARD kind -- beat-to-beat dips between
                                       # kicks -- while a section getting louder is
                                       # exactly the thing to react to quickly. With
                                       # one symmetric 3s tau the DROP commit trailed
                                       # the audible hit by ~4 beats (the hit was
                                       # a one-tick 0.2 -> 0.55 step on a downbeat;
                                       # energy_norm crossed 0.80 ~3 beats later).
                                       # Falls still use ENERGY_NORM_SMOOTH_TAU_S.
                                       # Validated by replaying both 2026-09-15
                                       # captures: no extra bucket transitions.
                                       # 1.0 -> 0.4 later the same day (session-
                                       # 20260915-140630): a track's SECOND drop
                                       # is judged against a window ceiling its
                                       # first drop already raised, so the smoothed
                                       # value only approaches ~0.92 -- with a 1s
                                       # tau it took 2 full bars to cross 0.85 and
                                       # the build strobe ran through the drop's
                                       # first two bars. At 0.4s: 1 bar. Replayed
                                       # all four of the day's captures: no new
                                       # transitions, builds start 0.1-0.5s
                                       # earlier. (A 1.5s RELEASE tau was tried
                                       # too -- exits 3s sooner, but the intro's
                                       # dips then delayed the first build ~2s.)
INST_PEAK_HOLD_S = 0.6                # peak-hold on the unsmoothed level for the
                                       # BUILD -> DROP lead (see _derive_bucket):
                                       # longer than one beat at any club tempo
                                       # (0.3s was tried: the dip between two kicks
                                       # at 133 BPM emptied it and reset the
                                       # candidate), still shorter than the dwell.
DROP_LEAD_CEILING_FRACTION = 0.9      # _lead_next_drop: the ceiling counts as
                                       # "set by a drop" once it is within this
                                       # fraction of the last drop's own level.
ENERGY_NORM_WINDOW_S = 90.0           # how far back to look for "this set's recent
                                       # range" -- roughly 2-3 phrases: long enough
                                       # that one kick/riser can't move it, short
                                       # enough to adapt within a single track.
ENERGY_NORM_SAMPLE_INTERVAL_S = 1.0   # how often to add to/recompute the rolling
                                       # window -- doesn't need per-tick precision,
                                       # and keeps the window small (~90 samples)
                                       # so sorting it every time stays cheap.
ENERGY_NORM_MIN_SAMPLES = 40          # require this many seconds of actual MUSIC
                                       # (silence isn't sampled, see SILENCE_FLOOR)
                                       # before trusting the normalized value at
                                       # all -- same warmup principle as the laser
                                       # trigger's WARMUP_S. Report 0.0 (BREAK, the
                                       # safe default) before this, not a
                                       # meaningless ratio. 20 -> 40 (2026-09-15,
                                       # replaying BOTH of the day's captures):
                                       # at 20 the window is still just the intro
                                       # when it wakes up, and the intro's own
                                       # louder section (raw ~0.3 vs 0.12) read as
                                       # a full DROP in both sessions -- at 40 the
                                       # window spans the intro AND that section,
                                       # both read BREAK/BUILD, and every real
                                       # drop (raw ~0.5) still committed in the
                                       # bar it landed. Cost, disclosed: the first
                                       # ~40s of music in a set can only ever be
                                       # BREAK. Tried and rejected instead: gating
                                       # DROP on the window's natural p10-p90 span
                                       # -- it delayed the first real drop by 10s
                                       # (the 90-sample window's p90 takes ~9
                                       # drop-seconds to move).
                                       # SEEDED as of 2026-09-17 (Josh's call;
                                       # config.py's energy_norm seed): a
                                       # Metronome built with `seed_range` uses
                                       # the last real session's p10/p90 as the
                                       # range while the window fills, blending
                                       # toward the live window by n/MIN_SAMPLES
                                       # so there's no jump at sample 40. Same
                                       # venue/gain = same range (the seed carries
                                       # a gain+device fingerprint and is dropped
                                       # when either differs); a different room
                                       # gets ~40 s of slightly-off classification
                                       # instead of 40 s of BREAK. The warm-up
                                       # count still gates persisting a NEW seed.
ENERGY_NORM_MIN_SPAN = 0.20          # floor on (ceiling - floor) -- avoids the
                                       # ratio blowing up when the set's been flat.
                                       # 0.10 -> 0.20 (2026-09-15, replaying
                                       # recordings/session-20260915-114303): with
                                       # silence excluded from the window (see
                                       # ENERGY_NORM_SILENCE_FLOOR), a quiet intro
                                       # (raw 0.10-0.17) leaves a FLAT window whose
                                       # span sits at this floor -- and at 0.10 a
                                       # single 0.2 blip in that intro was "0.1
                                       # above the floor" = 1.0 = DROP, hit 0.94,
                                       # 6s before the track's real content began.
                                       # At 0.20 a section must be at least ~0.16
                                       # above the set's floor to read as a drop
                                       # (the same track's real drop, raw 0.30,
                                       # still does; a 0.55 drop against a 0.30
                                       # break has a real span above this floor
                                       # and is unaffected). Raw-energy units, so
                                       # tied to inputs/audio.py's ENERGY_GAIN
                                       # calibration like every threshold here.
ENERGY_NORM_MIN_SPAN_FLOOR = 0.12     # ...and never under this, whatever the ceiling:
                                       # run 8 (2026-09-17, recordings/session-20260917-
                                       # 151218) -- a cued BUILD 70 s into a quiet set,
                                       # ceiling ~0.10, so 0.6 x ceiling let a 0.13 kick
                                       # read 1.0 and the hit lead committed a DROP on
                                       # it (Josh: "it was on an 8-bar drop"). At 0.12 a
                                       # hit must sit ~0.12 above the set's floor: run
                                       # 7/8's real drops (0.22-0.27 over 0.05-0.09) do,
                                       # that kick (0.13) and the 2026-09-15 intro blip
                                       # (0.20 over 0.10) don't.
ENERGY_NORM_MIN_SPAN_OF_CEILING = 0.6  # ...but never more than this fraction of the
                                       # ceiling itself: run 7 (2026-09-17, recordings/
                                       # session-20260917-145419) came in at about half
                                       # the level every earlier session had (raw drop
                                       # 0.24 vs ~0.6), so the whole set's real range
                                       # (0.09-0.23) sat under the 0.20 floor and the
                                       # drop could only reach 0.79 -- read as BUILD,
                                       # build strobe ramping through the drop. In raw
                                       # units the floor is a gain-staging assumption;
                                       # scaling it with the ceiling keeps its intent
                                       # (a flat window can't turn a blip into a drop)
                                       # at any input level. On the ten captures
                                       # replayed, only run 7's transitions change.
RANGE_PERSIST_INTERVAL_S = 60.0       # how often a warm window's range is handed to
                                       # the range_callback (app.py saves it as the
                                       # next launch's seed) -- see __init__.
ENERGY_NORM_MIN_CEILING = 0.05        # if nothing above this has happened
                                       # recently, the set is genuinely silent --
                                       # report 0.0 rather than let a tiny residual
                                       # blip normalize to ~1.0 against a near-zero
                                       # range. FOUND BY TESTING: without this, a
                                       # track ending (a long stretch at energy=0)
                                       # followed by any small noise spiked
                                       # energy_norm straight to ~1.0 ("DROP").
ENERGY_NORM_SILENCE_FLOOR = 0.03      # smoothed energy below this is SILENCE, not
                                       # part of the set's loudness range, and is
                                       # never added to the rolling window. FOUND
                                       # LIVE (recordings/session-20260915-114303):
                                       # the window is mostly zeros after any
                                       # silence, so its p10 floor is 0 and its p90
                                       # ceiling is whatever quiet intro just
                                       # started -- a track intro at raw 0.14 (the
                                       # quietest audible content of the night)
                                       # normalized to 1.00 and committed a full
                                       # DROP with drop_hit_intensity=1.0, bigger
                                       # than the real drop (raw 0.55) got. Same
                                       # thing again at a track restart after ~2min
                                       # of silence. ENERGY_NORM_MIN_CEILING alone
                                       # can't catch this (0.14 clears 0.05
                                       # easily). Keeping silence out of the window
                                       # means the range is always music-vs-music:
                                       # a cold start reports 0.0 (BREAK) until
                                       # ENERGY_NORM_MIN_SAMPLES of actual music
                                       # has played, and a restart after silence
                                       # still compares against the previous
                                       # track's real range. Session silence
                                       # measured 0.00-0.01; quietest music ~0.10.

# Bucket debounce (2026-09-07, FOUND LIVE + via tools/simulations/replay_song.py
# on a real track): `bucket_for_energy` is a stateless boundary check on
# `energy_norm`, and energy_norm -- even smoothed -- can sit within a hair of
# 0.30/0.55/0.80 for a real stretch of a song. Measured values a THOUSANDTH
# apart (0.2992 vs 0.3009) crossing a boundary repeatedly, and it visibly broke
# the reactive layer's own output: a real DROP passage dropping back to
# BREAKDOWN for a couple of ticks then jumping straight back, and a BUILD ramp
# (meant to run ~14s/8 bars) getting pre-empted by HOLD/DROP after under 2
# seconds -- Josh's own live report: "no buildup... the wash bar would turn off
# and be on its own random rhythm." Same "make ONE decision, then hold it"
# principle as reactive.py's beat-locked kick pass 4 and the laser's own
# spacing/duty gate, applied to bucket classification itself: a candidate
# bucket must PERSIST for BUCKET_MIN_DWELL_S before it's actually committed to,
# not flip the instant a boundary is crossed. Computed HERE (not independently
# by each of reactive.py/engines/laser.py/phrase.py) for the same reason
# energy_norm itself is centralized -- one shared notion of "what bucket are we
# in," not three copies that could drift apart.
#
# LIMIT, stated plainly: this kills RAPID flapping (the diagnosed failure mode
# -- blips of a few ticks up to ~2s). It does NOT guarantee a real drop can
# never be interrupted -- if energy_norm itself genuinely dips for several real
# seconds, this will still (correctly) commit to a lower bucket. Re-check
# against real playback after this lands rather than assuming it's fully solved.
#
# RAISED 1.0 -> 3.0 (2026-09-07, first real --hardware --record capture, see
# [[livereact-project]] memory): 1.0s stopped sub-second flapping but a real
# song's ordinary dynamics still cross a boundary on a several-second cadence
# -- measured BUILD dwelling only 1.2-4.3s in one real ~4min capture, nowhere
# near completing its own 8-bar/~14s ramp before being pre-empted. Paired with
# `bucket_for_energy_with_hysteresis` (scenes/catalog.py) so a candidate now
# has to both clear the boundary by a margin AND persist -- two independent
# defenses against the same "reads as random flutter" failure mode.
#
# Bible-integration Phase 2 (2026-09-11): replaced by a PER-STATE dwell dict,
# converted from LIGHTING_BIBLE.md §9.8's bar counts (BREAK 8, BUILD 4, DROP
# 8, PEAK 8, OUTRO 16) at a nominal tempo -- real sets in this project's own
# captures have run 124-138 BPM, so NOMINAL_BPM is a rough middle, not a
# measurement, exactly like every other [CONVENTION] number here. Flat
# BUCKET_MIN_DWELL_S=3.0 (close to old BUILD's 3.0s) is kept only as the
# floor for EXITING a state quickly is not what changed here -- see
# EXIT_CONFIRM_DWELL_S/FAST_ENTRY_DWELL_S below for the actual asymmetric
# entry/exit split (TIME-12).
NOMINAL_BPM = 128.0
SECONDS_PER_BAR = 4.0 * 60.0 / NOMINAL_BPM  # ~1.875s at 128 BPM

PER_STATE_DWELL_BARS = {
    EnergyBucket.BREAK: 8.0,
    EnergyBucket.BUILD: 4.0,
    EnergyBucket.DROP: 8.0,
    EnergyBucket.PEAK: 8.0,
    EnergyBucket.OUTRO: 16.0,
}
PER_STATE_DWELL_S = {b: bars * SECONDS_PER_BAR for b, bars in PER_STATE_DWELL_BARS.items()}

# Asymmetric entry/exit (LIGHTING_BIBLE.md §9.8, TIME-12): entering a
# higher-energy state needs only a fast confirmation (~2 beats); exiting to a
# lower-energy state needs a slower, more-sustained confirmation (~4 beats) --
# ON TOP OF the per-state floor above (a state's own PER_STATE_DWELL_S must
# already have elapsed before an exit is even considered at all; these two
# numbers gate the CANDIDATE's own persistence once that floor is cleared).
FAST_ENTRY_DWELL_S = 2.0 * 60.0 / NOMINAL_BPM   # ~0.94s at 128 BPM -- a 1-beat
                                                 # version was tried 2026-09-15
                                                 # (builds/drops ~0.5s sooner) and
                                                 # rejected: one isolated cue-tap
                                                 # in silence (session-20260915-
                                                 # 121503 @844s, raw 0.51 for 0.5s)
                                                 # became a 9s BUILD, exactly the
                                                 # false start the instantaneous
                                                 # re-check exists to block.
EXIT_CONFIRM_DWELL_S = 4.0 * 60.0 / NOMINAL_BPM  # ~1.88s at 128 BPM

# DJ build cue (2026-09-16, recordings/session-20260916-112651): a UK garage
# track whose kick-only intro reads LOUDER in RMS than its mid-heavy build,
# and whose build sits at the same loudness as its breakdown, never showed
# a BUILD at all -- no strobe ramp, no drop cue. The run before it caught
# the same build only on a smoothing lag. A loudness classifier cannot see
# that build (the 2026-09-16 offline check of a "high band rising over
# bars" rule caught it but started 20 bars early on the other test track
# and false-started once -- not shippable blind). The person who CAN see
# it is the DJ: `cue_build()` commits BUILD now and HOLDS it against the
# energy classifier's exit-to-BREAK for up to BUILD_CUE_HOLD_BARS, or
# until the drop commits (energy still owns DROP), or a second press
# cancels. This is the "manual 'drop is coming' cue" the retired beat-
# quantization note in _derive_bucket anticipated.
BUILD_CUE_HOLD_BARS = 32.0
BUILD_CUE_HOLD_S = BUILD_CUE_HOLD_BARS * SECONDS_PER_BAR
AUTO_BUILD_HOLD_S = AUTO_HOLD_BARS * SECONDS_PER_BAR  # core/build_rule.py's automatic cue, shorter

# The HIT lead out of a BUILD (2026-09-16, fifth live run, recordings/
# session-20260916-114402): the audible drop -- the kick returning after a
# kick-less build, low band 1.3 -> 18.6 on the downbeat -- committed a
# full bar (2.0 s) later: ~1 s for the smoothed energy_norm to climb past
# the DROP line, then FAST_ENTRY_DWELL_S. Josh: "stops too late... cuts
# 1.5 seconds late... feels like a bug." The instantaneous lead above
# (_lead_next_drop) is deliberately off for a track's FIRST drop, because
# an RMS-only instantaneous reading against a low ceiling calls a build's
# own kicks "DROP". This lead is stricter and works for any drop: the
# instantaneous level must read DROP (peak-held) AND the low band must
# jump to DROP_HIT_LOW_RATIO x its own rolling level during the build,
# and both must still hold DROP_HIT_CONFIRM_S later (one beat). A build
# with continuous kicks never clears the ratio (its kicks ARE the level),
# a riser has no low band, so both stay on the smoothed path unchanged.
DROP_HIT_LOW_RATIO = 3.0
DROP_HIT_LOW_FLOOR = 1.0          # abs low-band units; below this a build is kick-less, ratio against the floor
DROP_HIT_LOW_TAU_S = 2.0          # rolling level of the low band, frozen once a hit is under confirmation
DROP_HIT_LOW_HOLD_S = 0.3         # peak-hold on the low band: bridges the dip between two kicks, not a whole beat
DROP_HIT_CONFIRM_S = 60.0 / NOMINAL_BPM  # one beat: the hit must be seen again a beat after it was first seen
# ARMED DROPS (Josh, 2026-09-17, after runs 7-9): "if our strobing mechanism
# can never fully detect a proper beat drop accurately, make the beat drop be
# cued manually usually, and have the snare roll accent be another factor."
# The loudness classifier gets the MOMENT of a drop right only on tracks
# shaped the way it expects; on today's UKG it committed a false drop on one
# kick and read a loud intro as the build. So:
#   - D (arm_drop) or a snare roll (note_roll) ARMS the drop for
#     DROP_ARM_HOLD_BARS; while armed, the FIRST drop-level kick (low band
#     over DROP_HIT_LOW_RATIO x its rolling level) commits instantly.
#   - unarmed, the loudness path still commits a DROP, but only after the
#     smoothed level holds drop range for AUTO_DROP_CONFIRM_S (two bars) --
#     a safety net that is late rather than wrong. The old one-beat hit
#     lead and the RMS lead are gone with it.
DROP_ARM_HOLD_BARS = 32.0              # the D key's window (16 -> 32, 2026-09-17 run 10:
                                       # Josh arms 8-16 bars out and wants slack)
DROP_ARM_HOLD_S = DROP_ARM_HOLD_BARS * SECONDS_PER_BAR
ROLL_ARM_HOLD_BARS = 4.0               # a roll's window: its drop lands within a bar or two
ROLL_ARM_HOLD_S = ROLL_ARM_HOLD_BARS * SECONDS_PER_BAR
ROLL_ARM_MIN_S = 1.5                   # a roll must have HELD this long before it arms --
                                       # the detector's strays last 0.2-1 s, real rolls 4-6 s
                                       # (replaying 12 captures: strays armed 8 false drops)
ARMED_HIT_MIN_INST = 0.65              # and the sound RIGHT NOW must be at least upper-BUILD
                                       # loud (instantaneous energy_norm peak) -- a lone kick
                                       # in a quiet intro is not the drop even when armed
ARMED_KICK_VS_LAST_DROP = 0.6          # ... and, once a drop has been heard, the kick must
                                       # reach this fraction of the last drop's kick peak
                                       # (p90 of the raw low band over its first 4 bars).
                                       # Run 11 (2026-09-17, recordings/session-20260917-
                                       # 161315 at 177.6 s): armed by D, the kick coming
                                       # back at BUILD level (3-5 against the drop's 8-12)
                                       # made a pattern and cut a false drop. The set's
                                       # first drop has no reference and keeps the 3x rule.
DROP_KICK_REF_BARS = 4
DROP_HIT_PATTERN_S = 0.6               # ... and it must be a PATTERN: two distinct kicks
                                       # (rising edges of the raw low band over the ratio)
                                       # within this window -- one beat at 128 BPM with
                                       # slack. Run 10 (2026-09-17, recordings/session-
                                       # 20260917-154312 at 439.6 s): ONE breakdown kick
                                       # 4.5 s after D passed the level tests and cut a
                                       # false drop. A 4/4 drop's second kick lands at
                                       # +0.47 s, so a real drop still cuts within a beat.
# HARD CUT (Josh, 2026-09-19, recordings/session-20260919-104825): armed by D
# at 223 s, the kick RE-ENTERED after a breakdown at 233 s at full drop level
# in every band (low p90 7.1 vs the real drop's 7.8, mids and energy equal) --
# a drop by every measure the system has, but not the drop; the arm was spent
# 37 s before the real one and no strobe ran. The audio can't carry what the
# DJ knows about the song, so: D while armed by D = the drop cuts on the NEXT
# DOWNBEAT (the DJ counts it in), hit intensity 1.0. "Next" includes the
# downbeat in progress if the press lands within its first
# HARD_CUT_LATE_PHASE of a beat (a human presses ~0.1-0.3 s after the one);
# later than that waits for the next bar. Without a live PDL grid the cut is
# immediate. A third press cancels (arm and cut both). The exit is the
# ordinary DROP -> BREAK confirmation.
HARD_CUT_LATE_PHASE = 0.75
# ROLL START (2026-10-22, engine family 1 / STRUCT-03, see core/roll_period.py):
# Josh's drop begins at the snare roll, and the labeled rolls are quiet --
# nothing level-based in this file sees them. A confirmed roll start (8th/
# 16th-note periodicity stepping up in the 150-500 Hz band) commits DROP on
# its own, unarmed: the roll IS the cue. Guards live in the detector
# (reference span, step, level floor, hold, cooldown); the thresholds are
# params.json knobs. Disable by setting ROLL_START_COMMITS = False.
ROLL_START_COMMITS = True
RISE_AUTO_ARM = True                   # core/rise.py: a strong rise (RISE_ARM held
                                       # RISE_ARM_BARS) arms the drop by itself for
                                       # RISE_ARM_HOLD_BARS -- the autonomous path.
                                       # Scored with replay_state.py before shipping.
RISE_ARM_HOLD_BARS = 8.0
RISE_ARM_HOLD_S = RISE_ARM_HOLD_BARS * SECONDS_PER_BAR
AUTO_DROP_CONFIRM_S = 2.0 * SECONDS_PER_BAR
DROP_HIT_MIN_KICKS = 2                 # (unarmed hit lead, retired 2026-09-17 -- see above; kept for the record)
                                       # the hit must be a PATTERN: two distinct kicks
                                       # (rising edges of the raw low band over the
                                       # ratio) or one held DROP_HIT_SUSTAIN_S. Run 9
                                       # (2026-09-17, recordings/session-20260917-
                                       # 152218 at 198.0 s): ONE kick, 15 s before the
                                       # real drop, committed a DROP -- the 0.3 s low
                                       # peak-hold and the 0.6 s inst peak-hold both
                                       # outlast the one-beat confirmation, so a lone
                                       # hit "re-saw" itself. A 4/4 drop's second kick
                                       # lands within the same beat, so a real drop
                                       # still commits at +1 beat.
DROP_HIT_SUSTAIN_S = 0.4               # a raw low band held over the ratio this long
                                       # counts as the second kick (an 808/bass drop)
DROP_HIT_GAP_S = 0.6              # a hit that hasn't been re-seen for this long was a one-shot, not the drop
CUT_EXIT_CONFIRM_S = 2.0 * SECONDS_PER_BAR       # ~3.75s: a BREAK-range reading that
                                                 # persists this long mid-DROP is a
                                                 # real breakdown and may exit early
                                                 # (2026-09-15, see _derive_bucket)

# PEAK escalation / OUTRO promotion (Bible-integration Phase 2): neither is
# reachable from a raw energy level (see EnergyBucket's own docstring) --
# both are overlays computed from how long the BASE (BREAK/BUILD/DROP)
# classifier has been sitting in a state. A sustained DROP (past its own
# dwell floor, energy still high) escalates the FINAL bucket to PEAK without
# disturbing the base classifier underneath. A BREAK that followed a DROP/
# PEAK within OUTRO_LOOKBACK_S gets promoted to OUTRO the same way.
#
# Deliberately NOT treated as a truly terminal/blocking state the way
# LIGHTING_BIBLE.md's legal-transition matrix describes OUTRO (no legal
# exit): this system has no real way to know a set is actually ending vs.
# just between two builds in the same track, so a genuine energy rise is
# allowed to simply un-promote OUTRO back to whatever the base classifier
# says, rather than illegally blocking a real second build/drop in the same
# track because a `EnergyBucket.OUTRO` currently non-empty. See _base_committed
# and _last_drop_or_peak_t below.
OUTRO_LOOKBACK_S = 32.0 * SECONDS_PER_BAR  # ~60s at 128 BPM -- [CONVENTION], tunable
PEAK_EXIT_MARGIN = 0.10  # once PEAK, stay PEAK until energy_norm falls this far
                         # below BUILD_MAX (see _apply_bucket_overlay) -- this
                         # project's own number, not the Bible's; same size as
                         # the base classifier's downward margin.
# PEAK's own dwell (2026-10-24, TIME-11): PEAK used to fall back to DROP the
# tick energy_norm dipped under the exit line, and re-escalate the tick it
# came back -- on the ten ear-verified tracks that produced DROP runs of
# 0.1-2 bars between two PEAKs (Latch 94.8/110.4 s, Thriller 114.1 s, messy
# 185.6 s) and a 2-bar DROP blip on every PEAK -> breakdown (SWEETER 131 s,
# One More Time 59.9/286 s): the overlay de-escalated at once while the base
# was still confirming its 2-bar cut exit. Now the EXPOSED state keeps its
# own clock: DROP must have been exposed for its full dwell before PEAK
# escalates, PEAK holds its dwell, and a de-escalation needs the low reading
# to persist PEAK_EXIT_CONFIRM_BARS -- longer than CUT_EXIT_CONFIRM_S, so a
# real breakdown goes PEAK -> BREAK directly (legal) without the blip.
#
# Fade vs. de-escalation (2026-10-25, TIME-11): with a 4-bar confirmation,
# all nine PEAK -> DROP de-escalations on the ten ear-verified tracks were
# followed by a BREAK, seven of them within 8 bars (messy 93.8 s 2.8 bars,
# Latch 253.1 s 0.7, Somebody To Love 45.1 s 1.5, Beautiful Now 164.9 s 1.2,
# Namerakana 111.6 s 4.8, One More Time 133.5 s 5.6, Thriller 203.7 s 7.3):
# the energy was FADING INTO the breakdown, not settling at a lower drop
# level, and the DROP look showed for a few bars before the break look.
# Two changes: (1) a reading in BREAK range (energy_norm < catalog.BREAK_MAX)
# is a cut under way -- the base's cut exit will take PEAK -> BREAK directly
# -- so it holds PEAK and restarts the confirmation; (2) the confirmation is
# a full DROP dwell (8 bars): the lower section must prove it is a section
# as long as the DROP state it would enter has to hold. Measured with both:
# no PEAK -> DROP -> BREAK run under 8 bars remains on the labeled set.
PEAK_EXIT_CONFIRM_BARS = 8.0  # [CONVENTION], tunable (params.json)

# `transition_likely` heuristic (Bible-integration Phase 3, D1 in
# docs/bible_integration/SPEC_DEVIATIONS.md's "partial mitigation" note --
# Josh, 2026-09-12): a plain heuristic, NOT prediction or machine learning,
# using signals already computed elsewhere in this file as soft leading
# indicators that a transition out of a sustained DROP/PEAK is statistically
# due soon -- real dance tracks don't hold a peak forever, and a track that's
# already had several drops is more likely winding down than starting a
# fresh one. Exposed on ShowSnapshot; NOT consumed by anything yet (same
# unused-seam pattern as request_manual_bucket in Phase 2) -- a future phase
# can use it to bias look selection away from starting something elaborate
# right as the set seems likely to turn a corner. [CONVENTION], tunable.
TYPICAL_EXTENDED_PEAK_BARS = 16.0
TYPICAL_EXTENDED_PEAK_S = TYPICAL_EXTENDED_PEAK_BARS * SECONDS_PER_BAR
TRANSITION_LIKELY_DROP_CYCLE_THRESHOLD = 2

# Loud-to-quiet-to-loud drop marker (Josh's idea, 2026-09-11 -- a real,
# already-observed pattern: the first real --hardware --record capture
# showed the drums cutting to near-total silence for ~4.3s immediately
# before the first drop, see [[livereact-project]] memory). This is a
# CORROBORATING signal, not a gate on bucket classification itself -- DROP
# still commits off the energy-threshold+dwell machinery above regardless,
# so a real drop with no preceding hush still gets classified correctly.
# What this adds: a `drop_marker` pulse + a same-track occurrence counter
# (`drop_cycle`) that other consumers (e.g. a future beat-locked flash
# effect, or TIME-20's look-history) can condition on for higher confidence
# than "energy crossed a threshold" alone.
DIP_THRESHOLD = 0.08       # energy_norm below this counts as "near silence"
MIN_DIP_S = 1.0            # must stay under DIP_THRESHOLD at least this long -- rules out an
                           # ordinary quiet beat/gap, not a real "cut." [CONVENTION], tunable.
REBOUND_THRESHOLD = 0.70   # energy_norm at or above this, after a qualifying dip, confirms the marker
MAX_REBOUND_WINDOW_S = 8.0  # how long after a qualifying dip ends a rebound still counts

# Second, INDEPENDENT drop-marker path (2026-09-11, Josh's correction: "not
# all drops go quiet... it should only signal drop when its intensity is
# sharply rising"): the quiet-then-rebound path above only catches the
# build-quiet-drop pattern. A drop that goes straight from building to
# hitting, with no cut in between, needs its own detector -- a fast, large
# rise in energy_norm on its own, no preceding silence required. Per Josh's
# explicit instruction, this is NOT an additional requirement layered onto
# the quiet-then-rebound path (an AND) -- it's a second, independent, either-
# one-is-sufficient path (an OR): whichever condition is met first signals
# the drop. MARKER_COOLDOWN_S is shared across both paths so a classic
# build-quiet-drop (which can satisfy BOTH conditions at once) doesn't get
# double-counted as two separate drop_cycle increments.
SHARP_RISE_WINDOW_S = 3.0  # look back this far for the rise comparison
SHARP_RISE_DELTA = 0.5     # energy_norm must have climbed at least this much within the window
MARKER_COOLDOWN_S = 4.0    # shared across both paths -- one real event, one count

# Drop/PEAK intensity now reflects how loud the actual hit was (Josh, same
# correction), not a fixed constant regardless of the real sound -- see
# _drop_hit_intensity below and engines/reactive.py's DROP/PEAK branches.
# Range chosen to stay within LIGHTING_BIBLE.md's own stated 85-100% for
# `peak_full_rig`, just no longer flat within it.
DROP_INTENSITY_FLOOR = 0.85
DROP_INTENSITY_CEIL = 1.0


class Metronome(threading.Thread):
    """Drains input messages and publishes the live ShowState at a fixed rate."""

    def __init__(
        self,
        input_queue: "queue.Queue",
        show_state: ShowState,
        clock: Clock,
        stop_event: threading.Event,
        hz: float = 100.0,
        log_interval: float = 1.0,
        seed_range: tuple[float, float] | None = None,
        range_callback=None,
        control_callback=None,
    ) -> None:
        super().__init__(name="metronome", daemon=False)
        self.queue = input_queue
        self.show_state = show_state
        self.clock = clock
        self.stop_event = stop_event
        self.hz = hz
        self._log_interval = log_interval
        # (floor, ceiling) from the last real session -- see ENERGY_NORM_MIN_SAMPLES
        self._seed_range = seed_range if seed_range and seed_range[1] >= ENERGY_NORM_MIN_CEILING else None
        # Called with (floor, ceiling) every RANGE_PERSIST_INTERVAL_S once the
        # window is warm, so the seed survives a kill -9 (the only way this app
        # gets stopped under --visualize; a clean shutdown never happens live).
        self._range_callback = range_callback
        self._last_range_persist = clock.monotonic()
        # Called with (control_name, value 0..1) for every MIDI control that
        # is NOT one of the mixer intents in `_midi` below -- operator cue
        # controls learned into midi_map.json (e.g. "laser_cue", 2026-09-30).
        # Runs on this thread; keep it a plain attribute write.
        self._control_callback = control_callback

        # --- authoritative working state (owned solely by this thread) ---
        self._bpm = 124.0
        self._beat_anchor = clock.monotonic()  # monotonic time of the current beat
        self._beat_phase = 0.0
        self._beat_in_bar = 1
        self._bar = 0
        self._playing = False
        self._loop_active = False
        self._on_air: tuple[int, ...] = ()
        self._master_deck: int | None = None

        # transition detection
        self._cross_history: deque[tuple[float, float]] = deque()
        self._transition_until = 0.0
        self._prev_master: int | None = None

        # MIDI intent
        self._midi = {
            "crossfader": 0.5, "fader_a": 0.0, "fader_b": 0.0,
            "eq_low_a": 0.5, "eq_mid_a": 0.5, "eq_high_a": 0.5,
            "eq_low_b": 0.5, "eq_mid_b": 0.5, "eq_high_b": 0.5,
        }
        self._energy = 0.0

        # adaptive energy normalization state (see ENERGY_NORM_* above) — owned
        # solely by this thread, same as everything else here.
        self._energy_smooth = 0.0
        self._energy_history: deque[float] = deque(
            maxlen=int(ENERGY_NORM_WINDOW_S / ENERGY_NORM_SAMPLE_INTERVAL_S)
        )
        self._energy_floor = 0.0
        self._energy_ceiling = 0.0
        self._last_norm_tick_t: float | None = None
        self._last_energy_sample_t: float | None = None
        self._energy_norm = 0.0
        self._energy_norm_inst: float | None = None  # unsmoothed twin of _energy_norm, see
                                                     # _derive_energy_norm; None until first derived
        self._inst_recent: deque[tuple[float, float]] = deque()  # (t, inst) over INST_PEAK_HOLD_S, see _inst_peak
        self._drop_commit_energy: float | None = None  # smoothed raw energy at the last DROP commit, see _lead_next_drop

        # bucket debounce state (Bible-integration Phase 2 -- see PER_STATE_DWELL_S above)
        self._base_committed = EnergyBucket.BREAK  # BREAK/BUILD/DROP only -- see EnergyBucket's docstring
        self._base_committed_since = clock.monotonic()
        self._bucket_committed = EnergyBucket.BREAK  # the FULL exposed state (may be PEAK/OUTRO overlay)
        self._exposed_since = clock.monotonic()      # when _bucket_committed last changed (PEAK's own dwell)
        self._peak_low_since: float | None = None    # PEAK exposed, energy under its exit line since then
        self._bucket_candidate: EnergyBucket | None = None
        self._bucket_candidate_since: float | None = None
        self._bucket_candidate_bar: int | None = None  # bar the candidate first appeared in -> drop_start_bar
        self._last_drop_or_peak_t: float | None = None  # for OUTRO promotion, see OUTRO_LOOKBACK_S
        self._manual_bucket_override: EnergyBucket | None = None  # see request_manual_bucket() / cue_build()
        self._build_cue_until: float | None = None  # while set, a cued BUILD can't fall to BREAK -- see cue_build()
        self._build_cue_source = ""                  # "dj" (C key) or "auto" (core/build_rule.py) while held
        # automatic build cue (core/build_rule.py): per-bar means fed on each bar change
        self._build_rule = KickLevelBuildRule()
        self._rule_bar: int | None = None
        self._rule_low_sum = 0.0
        self._rule_energy_sum = 0.0
        self._rule_n = 0
        self._rule_prev_base = EnergyBucket.BREAK
        self._auto_build_pending = False             # rule fired inside BREAK's dwell floor; commit when it clears
        self._auto_build_floor_t = 0.0               # ... at this time (the shown BREAK's start + its dwell)
        self._auto_build_rule_t = 0.0                # when the rule fired (the cue's hold counts from here)
        self._rule_bar_t = clock.monotonic()         # when the rule's current bar started
        self._rule_bars_in_break: deque[bool] = deque(maxlen=AUTO_BUILD_BARS)  # completed bars wholly SHOWN as BREAK
        self._look_prev = EnergyBucket.BREAK         # look_bucket last tick, and since when (the floor above
        self._look_since = clock.monotonic()         # counts from the BREAK the SHOW entered, not the base's)
        # hit lead state (see DROP_HIT_LOW_RATIO)
        self._low_avg = 0.0
        self._low_recent: deque[tuple[float, float]] = deque()  # (t, band_low_abs) over INST_PEAK_HOLD_S
        self._hit_since: float | None = None
        self._hit_last_seen: float | None = None
        self._hit_bar: int | None = None
        self._hit_ref = 0.0
        self._drop_armed_until: float | None = None  # see DROP_ARM_HOLD_BARS: D key or a roll
        self._drop_arm_source = ""                    # "dj" / "roll" while armed
        self._drop_cut_pending = False                # see HARD_CUT_LATE_PHASE: D again = cut on the downbeat
        self._roll_active = False                     # written by the reactive engine via note_roll
        self._roll_period = RollPeriodDetector()      # roll START from sub-beat periodicity (core/roll_period.py)
        self._roll_start_pending = False              # set by _on_audio on the block a start confirms, consumed in _derive_bucket
        self._roll_since: float | None = None         # when the current roll began (ROLL_ARM_MIN_S)
        self._kick_edges: deque[float] = deque()      # times of raw low-band rising edges (DROP_HIT_PATTERN_S)
        self._drop_kick_ref: float | None = None      # last drop's kick peak (ARMED_KICK_VS_LAST_DROP)
        self._drop_kick_samples: list[float] = []     # raw low band over a drop's first DROP_KICK_REF_BARS
        self._drop_kick_until_bar: int | None = None
        self._kick_prev_above = False
        # the rise phase (core/rise.py): per-beat means of the high band + onset rate
        self._rise = RiseDetector()
        self._rise_beat: tuple[int, int] | None = None
        self._rise_hi_sum = 0.0
        self._rise_on_sum = 0.0
        self._rise_n = 0
        self._pending_drop_release = False  # Phase 3: confirmed DROP, waiting for the next downbeat
        self.transition_likely = False  # Phase 3 heuristic, see TYPICAL_EXTENDED_PEAK_S above

        # loud-to-quiet-to-loud drop marker (see DIP_THRESHOLD etc. above)
        self._dip_started_at: float | None = None
        self._awaiting_rebound_until: float | None = None
        self.drop_cycle = 0           # same-track drop-marker occurrence count; reset on a track transition
        self._drop_marker_pulse = False  # True only on the exact tick a marker confirms
        self._prev_in_transition = False  # rising-edge detector for the drop_cycle reset
        # sharp-rise-alone path (see SHARP_RISE_WINDOW_S etc. above) -- an
        # independent OR, not an AND, with the dip/rebound path above
        self._recent_energy: deque[tuple[float, float]] = deque()  # (t, energy_norm) within SHARP_RISE_WINDOW_S
        self._marker_cooldown_until = 0.0  # shared between both paths, see MARKER_COOLDOWN_S
        # captured once per real DROP commitment, held static (see DROP_INTENSITY_FLOOR/CEIL
        # above and engines/reactive.py) -- how loud THIS hit actually was, not a fixed constant
        self.drop_hit_intensity = DROP_INTENSITY_FLOOR
        self.drop_start_bar: int | None = None  # see ShowSnapshot.drop_start_bar

        # phrase-frozen palette (engines/palette.py, see ShowSnapshot.palette)
        self.palette, self.palette_spot = palette_mod.pick(EnergyBucket.BREAK.value, 0, 0, 0)
        self._palette_phrase: int | None = None   # phrase index (from _palette_origin_bar) the current pick was made in
        self._palette_family: str | None = None   # bucket family it was made for
        self._palette_seed: int | None = None     # track seed it was made with
        self._palette_origin_bar = 0              # bar the current family was entered in -- phrases count from here
        self._palette_pick_bar = 0                # bar the current pick was made in (PALETTE_MIN_HOLD_BARS)
        self._track_seed = 0                      # bumped per detected track transition -> per-song variety

        # real audio (docs/14) — kept separate from _midi's fields; see
        # AudioLevelMessage's docstring for why band_low_abs stays un-normalized
        self._audio_band_low = 0.333
        self._audio_band_mid = 0.333
        self._audio_band_high = 0.334
        self._audio_band_low_abs = 0.0
        self._audio_band_mid_abs = 0.0    # feed core/snare_trigger.py's SnareOnsetDetector (2026-09-14)
        self._audio_band_high_abs = 0.0   # same
        self._audio_high_onset_rate = 0.0  # inputs/audio.py ONSET_*: onsets/s in the high band (roll accent)
        self._audio_high_flux = 0.0
        self._audio_band_snare_abs = 0.0  # 150-500 Hz (SNARE_HZ), the roll-start detector's input
        self._audio_rms = 0.0

        # freshness tracking
        self._last_beat_time: float | None = None  # PDL freshness keys off master BEATS, not status packets
        self._last_midi_time: float | None = None
        self._last_audio_time: float | None = None
        self._last_log = clock.monotonic()

    # ------------------------------------------------------------------ run loop
    def run(self) -> None:  # pragma: no cover - thin wrapper around tick()
        timer = LoopTimer(self.clock, self.hz)
        try:
            while not self.stop_event.is_set():
                self.tick()
                timer.wait()
        except Exception:  # noqa: BLE001 - never let the clock thread die silently
            log.exception("Metronome loop crashed")

    def tick(self) -> ShowSnapshot:
        """One iteration: drain queue, advance clock, derive, publish. Returns the
        snapshot it published (handy for tests)."""
        now = self.clock.monotonic()
        self._drain_queue()
        self._advance_clock(now)
        self._derive_energy(now)
        self._derive_energy_norm(now)
        self._derive_bucket(now)
        self._derive_build_rule(now)
        self._derive_rise(now)
        self._derive_drop_marker(now)
        self._derive_palette()
        snap = self._build_snapshot(now)
        self.show_state.publish(snap)
        self._maybe_log(now, snap)
        return snap

    # ------------------------------------------------------------------ inputs
    def _drain_queue(self) -> None:
        while True:
            try:
                msg = self.queue.get_nowait()
            except queue.Empty:
                break
            self._handle_message(msg)

    def _handle_message(self, msg) -> None:  # was _handle: collides with threading.Thread._handle on Python 3.13
        if isinstance(msg, BeatMessage):
            self._on_beat(msg)
        elif isinstance(msg, StatusMessage):
            self._on_status(msg)
        elif isinstance(msg, MidiControlMessage):
            self._on_midi(msg)
        elif isinstance(msg, AudioLevelMessage):
            self._on_audio(msg)
        elif isinstance(msg, TransitionMessage):
            # An explicit transition signal (demo/manual). Detection is the main
            # path; this just nudges the same timer so both sources agree.
            if msg.active:
                self._transition_until = max(self._transition_until, msg.timestamp + TRANSITION_COOLDOWN)
            else:
                self._transition_until = msg.timestamp
        else:  # pragma: no cover - defensive
            log.debug("Metronome ignoring unknown message: %r", msg)

    def _on_beat(self, msg: BeatMessage) -> None:
        if not msg.is_master:
            return  # only the master deck drives the clock
        if msg.bpm > 0:
            self._bpm = msg.bpm
        # Infer bar from downbeats; guard against double-counting if the free-run
        # advance already rolled us into beat 1 this cycle.
        if msg.beat_in_bar == 1 and self._beat_in_bar != 1:
            self._bar += 1
        self._beat_in_bar = msg.beat_in_bar
        self._beat_anchor = msg.timestamp  # hard resync: phase = 0 at this beat
        self._beat_phase = 0.0
        self._last_beat_time = msg.timestamp

    def _on_status(self, msg: StatusMessage) -> None:
        self._on_air = msg.on_air
        # A master-deck handoff is a transition.
        if self._prev_master is not None and msg.master_deck != self._prev_master:
            self._transition_until = max(self._transition_until, msg.timestamp + TRANSITION_COOLDOWN)
        self._prev_master = msg.master_deck
        self._master_deck = msg.master_deck
        self._playing = msg.playing
        self._loop_active = msg.loop_active

    def _on_midi(self, msg: MidiControlMessage) -> None:
        self._last_midi_time = msg.timestamp
        value = max(0.0, min(1.0, msg.value))
        if msg.control in self._midi:
            self._midi[msg.control] = value
            if msg.control == "crossfader":
                self._note_crossfader(msg.timestamp, value)
        elif self._control_callback is not None:
            self._control_callback(msg.control, value)

    def _on_audio(self, msg: AudioLevelMessage) -> None:
        self._last_audio_time = msg.timestamp
        self._audio_band_low = msg.band_low
        self._audio_band_mid = msg.band_mid
        self._audio_band_high = msg.band_high
        self._audio_band_low_abs = msg.band_low_abs
        self._audio_band_mid_abs = msg.band_mid_abs
        self._audio_band_high_abs = msg.band_high_abs
        self._audio_high_onset_rate = msg.high_onset_rate
        self._audio_high_flux = msg.high_flux
        self._audio_rms = msg.rms
        self._audio_band_snare_abs = msg.band_snare_abs
        # One sample per audio BLOCK (the lags are in blocks) -- not per tick.
        beat_dur = 60.0 / self._bpm if self._bpm > 0 else 60.0 / NOMINAL_BPM
        if self._roll_period.on_block(msg.timestamp, msg.band_snare_abs, beat_dur):
            self._roll_start_pending = True

    def _note_crossfader(self, t: float, value: float) -> None:
        """Detect a transition from crossfader motion over a rolling window."""
        history = self._cross_history
        history.append((t, value))
        while history and t - history[0][0] > CROSS_WINDOW:
            history.popleft()
        if len(history) >= 2:
            values = [v for _, v in history]
            if max(values) - min(values) >= CROSS_MOVE:
                self._transition_until = max(self._transition_until, t + TRANSITION_COOLDOWN)

    # ------------------------------------------------------------------ clock
    def _advance_clock(self, now: float) -> None:
        if self._bpm <= 0:
            return
        beat_dur = 60.0 / self._bpm
        elapsed = now - self._beat_anchor
        if self._is_pdl_fresh(now) and elapsed < beat_dur:
            # Locked: interpolate within the beat (clamped so we don't roll past).
            self._beat_phase = min(0.999, elapsed / beat_dur)
            return
        # Free-run: master beats are late/absent — advance beats from the clock so
        # phase keeps moving smoothly instead of stalling.
        while elapsed >= beat_dur:
            self._beat_anchor += beat_dur
            elapsed -= beat_dur
            self._advance_one_beat()
        self._beat_phase = elapsed / beat_dur

    def _advance_one_beat(self) -> None:
        self._beat_in_bar += 1
        if self._beat_in_bar > 4:
            self._beat_in_bar = 1
        if self._beat_in_bar == 1:
            self._bar += 1

    # ------------------------------------------------------------------ derive
    def _derive_energy(self, now: float) -> None:
        # VERIFIED preference order (docs/14): real audio loudness when fresh —
        # it's actually measuring the sound, not a mixer-knob proxy. Falls back
        # to the MIDI fader/EQ estimate when audio isn't available, same
        # graceful-degradation pattern as every other input in this project.
        #
        # FOUND BY LIVE MEASUREMENT (docs/14 §5, 2026-09-01): real audio was
        # getting smoothed TWICE — once by AudioAnalyzer's own attack/release
        # envelope (already tuned to feel responsive to real transients), then
        # again here by ENERGY_SMOOTHING's ~0.2s time constant. That stacked
        # smoothing showed up as a measured ~120ms of extra lag on the moving
        # head's dimmer (which reads snap.energy directly, no envelope of its
        # own) beyond the audio path's already-unavoidable capture latency.
        # Fix: real audio is already appropriately smoothed by the time it gets
        # here — pass it through as-is. Only the MIDI proxy (raw fader/EQ
        # positions, genuinely noisy/jumpy, no smoothing of its own) still
        # needs ENERGY_SMOOTHING applied here.
        if self._is_audio_fresh(now):
            self._energy = self._audio_rms
        else:
            # Louder deck's fader + how "open" its EQ is -> a coarse energy proxy.
            if self._midi["fader_a"] >= self._midi["fader_b"]:
                fader, prefix = self._midi["fader_a"], "a"
            else:
                fader, prefix = self._midi["fader_b"], "b"
            eq_open = (
                self._midi[f"eq_low_{prefix}"]
                + self._midi[f"eq_mid_{prefix}"]
                + self._midi[f"eq_high_{prefix}"]
            ) / 3.0
            raw = 0.6 * fader + 0.4 * eq_open
            self._energy += ENERGY_SMOOTHING * (raw - self._energy)

    def _derive_energy_norm(self, now: float) -> None:
        """Rescale `self._energy` against this SET's own recent loudness range
        (see the ENERGY_NORM_* constants above for the full "why"). Real-audio
        only: the MIDI fallback proxy (fader/EQ position) is already a
        hand-built 0..1 signal, not a loudness measure, so it passes through
        unscaled — adaptive rescaling doesn't mean anything for that path."""
        if not self._is_audio_fresh(now):
            self._energy_norm = self._energy
            self._energy_norm_inst = self._energy
            return

        dt = (now - self._last_norm_tick_t) if self._last_norm_tick_t is not None else 0.0
        self._last_norm_tick_t = now
        # Asymmetric: a rise is followed on the faster ENERGY_NORM_ATTACK_TAU_S,
        # a fall on the slower ENERGY_NORM_SMOOTH_TAU_S (see ATTACK's comment).
        tau = ENERGY_NORM_ATTACK_TAU_S if self._energy > self._energy_smooth else ENERGY_NORM_SMOOTH_TAU_S
        coeff = 1 - math.exp(-dt / tau) if dt > 0 else 0.0
        self._energy_smooth += coeff * (self._energy - self._energy_smooth)
        # Low band rolling level + short peak-hold for the hit lead (see
        # DROP_HIT_LOW_RATIO); the level freezes while a hit is confirming.
        low = float(self._audio_band_low_abs)
        if self._hit_since is None and dt > 0:
            self._low_avg += (1 - math.exp(-dt / DROP_HIT_LOW_TAU_S)) * (low - self._low_avg)
        self._low_recent.append((now, low))
        while self._low_recent and now - self._low_recent[0][0] > DROP_HIT_LOW_HOLD_S:
            self._low_recent.popleft()

        if (self._last_energy_sample_t is None
                or now - self._last_energy_sample_t >= ENERGY_NORM_SAMPLE_INTERVAL_S):
            self._last_energy_sample_t = now
            # Silence is the absence of a set, not the bottom of its range
            # (see ENERGY_NORM_SILENCE_FLOOR) -- the window only ever holds
            # music, so floor/ceiling stay wherever the last music left them.
            if self._energy_smooth >= ENERGY_NORM_SILENCE_FLOOR:
                self._energy_history.append(self._energy_smooth)
                vals = sorted(self._energy_history)
                n = len(vals)
                self._energy_floor = vals[int(0.10 * (n - 1))]
                self._energy_ceiling = vals[int(0.90 * (n - 1))]

        floor, ceiling = self._energy_floor, self._energy_ceiling
        n = len(self._energy_history)
        if n < ENERGY_NORM_MIN_SAMPLES:
            if self._seed_range is None:
                self._energy_norm = 0.0  # not enough history yet
                self._energy_norm_inst = 0.0
                return
            # Warm-up with a seed: the last session's range, handing over to
            # the live window as it fills (see ENERGY_NORM_MIN_SAMPLES).
            w = n / ENERGY_NORM_MIN_SAMPLES
            floor = (1.0 - w) * self._seed_range[0] + w * floor
            ceiling = (1.0 - w) * self._seed_range[1] + w * ceiling
        if ceiling < ENERGY_NORM_MIN_CEILING:
            self._energy_norm = 0.0  # genuinely silent recently
            self._energy_norm_inst = 0.0
            return

        span = max(ceiling - floor,
                   max(ENERGY_NORM_MIN_SPAN_FLOOR, min(ENERGY_NORM_MIN_SPAN, ENERGY_NORM_MIN_SPAN_OF_CEILING * ceiling)))
        self._energy_norm = max(0.0, min(1.0, (self._energy_smooth - floor) / span))
        # The same rescaling on the UNSMOOTHED per-tick energy -- "is the
        # sound still there right now" -- used only to confirm an upward
        # bucket move (see _derive_bucket), never to classify directly.
        self._energy_norm_inst = max(0.0, min(1.0, (self._energy - floor) / span))
        self._inst_recent.append((now, self._energy_norm_inst))
        while self._inst_recent and now - self._inst_recent[0][0] > INST_PEAK_HOLD_S:
            self._inst_recent.popleft()

    @property
    def build_cue_source(self) -> str:
        """"dj" / "auto" while a build cue is held, else "" (console)."""
        return self._build_cue_source if self._build_cue_until is not None else ""

    @property
    def norm_seeded(self) -> bool:
        """True while the persisted seed still carries weight in the range
        (window not yet at ENERGY_NORM_MIN_SAMPLES) -- recorder/console."""
        return self._seed_range is not None and len(self._energy_history) < ENERGY_NORM_MIN_SAMPLES

    @property
    def norm_warm(self) -> bool:
        """True once the live window alone is trusted -- the gate for saving a
        NEW seed at shutdown (a half-warmed window is not a range)."""
        return len(self._energy_history) >= ENERGY_NORM_MIN_SAMPLES

    @property
    def energy_norm_range(self) -> tuple[float, float]:
        """The live window's (p10, p90) in raw energy units -- what gets
        persisted as the next launch's seed (app.py, config.py)."""
        return self._energy_floor, self._energy_ceiling

    def request_manual_bucket(self, bucket: EnergyBucket) -> None:
        """Bible-integration Phase 2 hook: a future DJ-triggered cue ("the
        drop is now," "the drop is coming") can call this to force an
        immediate commitment on the NEXT tick, taking priority over the
        automated classifier entirely -- a manual, in-the-moment call from
        the person who actually knows the track beats a guess from energy
        levels alone. Nothing calls this yet (Phase 5's job); the seam just
        needs to exist now so that work doesn't require reopening this
        file. Consumed once, then cleared. (Since 2026-09-16 the console's
        build cue is the first real caller -- see cue_build.)"""
        self._manual_bucket_override = bucket

    @property
    def build_cue_active(self) -> bool:
        return self._build_cue_until is not None

    def cue_build(self) -> None:
        """The DJ's "the build is happening now" (see BUILD_CUE_HOLD_BARS).
        Toggle: a first press commits BUILD on the next tick and holds it;
        a second press while held cancels back to BREAK. Safe to call from
        the console thread -- plain attribute writes, consumed on the
        metronome thread like request_manual_bucket."""
        if self._build_cue_until is not None and self._build_cue_source == "auto":
            # The rule got there first; the DJ agreeing makes it a DJ cue
            # (full hold, no kick-gone release), never a cancel.
            self._build_cue_until = self.clock.monotonic() + BUILD_CUE_HOLD_S
            self._build_cue_source = "dj"
            self._build_rule.released()
            return
        if self._build_cue_until is not None:
            self._build_cue_until = None
            self._build_cue_source = ""
            self.request_manual_bucket(EnergyBucket.BREAK)
            return
        self._build_cue_until = self.clock.monotonic() + BUILD_CUE_HOLD_S
        self._build_cue_source = "dj"
        self.request_manual_bucket(EnergyBucket.BUILD)

    @property
    def drop_armed(self) -> bool:
        return self._drop_armed_until is not None

    @property
    def drop_arm_source(self) -> str:
        """"dj" / "roll" while armed, else "" (console)."""
        return self._drop_arm_source if self.drop_armed else ""

    def arm_drop(self) -> None:
        """The DJ's "the drop is next" (D key), three states:
          1. not armed -> ARMED for DROP_ARM_HOLD_BARS: the next drop-level
             kick PATTERN commits it instantly, and the strobe runs while the
             rise is on. Arming changes NOTHING about the current look (run
             11, 2026-09-17: D used to cue the build look, "the lights turn
             white... D should just be preparation"); C is the build cue. A
             roll-/rise-armed drop is upgraded to a DJ arm, not cancelled.
          2. armed by D -> HARD CUT pending (see HARD_CUT_LATE_PHASE): the
             drop commits on the next downbeat whatever the audio says.
          3. cut pending -> cancelled: disarmed, nothing pending.
        Console-thread safe: plain attribute writes, like cue_build."""
        now = self.clock.monotonic()
        if self._drop_armed_until is not None and self._drop_arm_source == "dj":
            if self._drop_cut_pending:
                self._drop_cut_pending = False
                self._drop_armed_until = None
                self._drop_arm_source = ""
            else:
                self._drop_cut_pending = True
            return
        self._drop_armed_until = now + DROP_ARM_HOLD_S
        self._drop_arm_source = "dj"
        self._drop_cut_pending = False

    @property
    def drop_cut_pending(self) -> bool:
        """True between the second D press and the downbeat that cuts the drop."""
        return self._drop_cut_pending and self.drop_armed

    def note_roll(self, active: bool) -> None:
        """Called every tick by engines/reactive.py with the roll accent's
        state (the detector lives there). A roll arms the drop and cues the
        build; consumed on the metronome thread in _derive_bucket."""
        self._roll_active = bool(active)

    def _commit_drop(self, now: float, level: float | None = None, raw_energy: float | None = None) -> None:
        """Actually commit to DROP -- called either immediately (already on
        a downbeat when confirmed) or once a pending beat-quantized release
        arrives (see _pending_drop_release in _derive_bucket). Captures how
        loud THIS hit actually was (2026-09-11, Josh's correction) -- held
        static from here (see engines/reactive.py's DROP/PEAK branches), not
        chased continuously; scaled within [DROP_INTENSITY_FLOOR,
        DROP_INTENSITY_CEIL] against energy_norm's real range at the moment
        of commitment."""
        # `level` / `raw_energy`: the hit lead (DROP_HIT_LOW_RATIO) commits a
        # beat after the kick, before the smoothed values have caught up --
        # it passes the peak-held instantaneous level and the louder of the
        # smoothed/instantaneous raw energy, so a real 1.0 hit isn't booked
        # as a 0.85 (which would also keep the laser off it).
        level = self._energy_norm if level is None else level
        span = 1.0 - BUILD_MAX
        progress = max(0.0, min(1.0, (level - BUILD_MAX) / span)) if span > 0 else 1.0
        self.drop_hit_intensity = DROP_INTENSITY_FLOOR + (DROP_INTENSITY_CEIL - DROP_INTENSITY_FLOOR) * progress
        self._drop_commit_energy = self._energy_smooth if raw_energy is None else raw_energy
        self._drop_armed_until = None  # the drop it was armed for has landed
        self._drop_arm_source = ""
        self._drop_cut_pending = False
        self._kick_edges.clear()
        self._rise.drop_landed()
        self._drop_kick_samples = []
        self._drop_kick_until_bar = self._bar + DROP_KICK_REF_BARS
        # Phrase origin for anything counting bars from the drop: the bar
        # the DROP candidate first appeared in, not this (later) commit bar
        # -- see ShowSnapshot.drop_start_bar. A manual override has no
        # candidate history, so it falls back to the current bar.
        self.drop_start_bar = self._bucket_candidate_bar if self._bucket_candidate_bar is not None else self._bar
        self._bucket_candidate_bar = None
        self._base_committed = EnergyBucket.DROP
        self._base_committed_since = now
        self._build_cue_until = None  # the drop the cue was for has landed

    def _bar_s(self) -> float:
        """One bar at the live tempo (2026-10-24, TIME-11): the dwell floors
        were bars converted at NOMINAL_BPM, so on a 122-123 BPM track (Latch,
        One More Time) "8 bars" ran out after 7.6-7.7 real bars."""
        return 4.0 * 60.0 / (self._bpm if self._bpm > 0 else NOMINAL_BPM)

    def _dwell_s(self, bucket: EnergyBucket) -> float:
        """PER_STATE_DWELL_BARS[bucket] in seconds at the live tempo."""
        return PER_STATE_DWELL_BARS[bucket] * self._bar_s()

    def _apply_manual_override(self, bucket: EnergyBucket, now: float) -> None:
        """Commit a manual cue (see request_manual_bucket) so that the NEXT
        tick's automated classifier + overlay agree with it, instead of
        crashing or immediately undoing it -- found in the 2026-09-15 polish
        pass: the old inline version stored PEAK/OUTRO straight into
        `_base_committed`, and `_BUCKET_ORDER.index(...)` on the following
        tick raised ValueError (the base walk is BREAK/BUILD/DROP only).

        PEAK/OUTRO are overlays on a DROP/BREAK base (see _apply_bucket_
        overlay), so the base is set to that anchor and back-dated past its
        own dwell floor so the overlay condition holds right away; a DROP or
        PEAK cue goes through _commit_drop's usual bookkeeping (drop_start_
        bar, commit energy) with the hit intensity pinned to the ceiling --
        a manual "drop now" is by definition the real thing."""
        base = {EnergyBucket.PEAK: EnergyBucket.DROP, EnergyBucket.OUTRO: EnergyBucket.BREAK}.get(bucket, bucket)
        self._bucket_candidate = None
        self._bucket_candidate_since = None
        if base == EnergyBucket.DROP:
            self._commit_drop(now)
            self.drop_hit_intensity = DROP_INTENSITY_CEIL
        else:
            self._base_committed = base
            self._base_committed_since = now
        if bucket == EnergyBucket.PEAK:
            self._base_committed_since = now - self._dwell_s(EnergyBucket.DROP)
        elif bucket == EnergyBucket.OUTRO:
            self._base_committed_since = now - self._dwell_s(EnergyBucket.BREAK)
            self._last_drop_or_peak_t = now  # "after a recent drop" -- the operator says so
        self._apply_bucket_overlay(now)
        if bucket == EnergyBucket.PEAK and self._bucket_committed != EnergyBucket.PEAK:
            # The overlay also wants energy_norm near the top; the cue overrides
            # that reading for this tick so the look actually lands -- and,
            # since 2026-10-24, holds it for PEAK's own dwell like any PEAK.
            self._bucket_committed = EnergyBucket.PEAK
            self._exposed_since = now
            self._peak_low_since = None

    def _derive_bucket(self, now: float) -> None:
        """Debounce bucket_for_energy's output against the base 3-state
        classifier (BREAK/BUILD/DROP -- see EnergyBucket's own docstring for
        why PEAK/OUTRO are handled separately, below), then apply the
        PEAK-escalation/OUTRO-promotion overlay every tick regardless of
        whether the base itself changed this tick.

        A manual override (see request_manual_bucket above) short-circuits
        all of this and commits directly, on priority alone -- unused today,
        but the classifier defers to it unconditionally once something calls it.
        """
        if self._manual_bucket_override is not None:
            self._apply_manual_override(self._manual_bucket_override, now)
            self._manual_bucket_override = None
            return

        # ROLL START (see ROLL_START_COMMITS / core/roll_period.py): the snare
        # roll is where Josh's drop begins, so a confirmed start commits DROP
        # now, whatever the loudness reads (the labeled rolls sit at
        # energy_norm ~0.1). The detector is one-shot per roll; a roll that
        # starts while already in DROP changes nothing.
        if self._roll_start_pending:
            self._roll_start_pending = False
            if ROLL_START_COMMITS and self._base_committed != EnergyBucket.DROP and self._is_audio_fresh(now):
                self._bucket_candidate = None
                self._bucket_candidate_since = None
                self._bucket_candidate_bar = self._bar  # the roll's first bar is the phrase origin
                log.info("Drop committed on a snare-roll start (periodicity %.2f, energy_norm %.2f)",
                         self._roll_period.score, self._energy_norm)
                self._commit_drop(now, level=max(self._energy_norm, self._inst_peak() or 0.0),
                                  raw_energy=max(self._energy_smooth, self._energy))
                self._apply_bucket_overlay(now)
                return

        raw = bucket_for_energy_with_hysteresis(self._energy_norm, self._base_committed)

        # ARMED DROP (see DROP_ARM_HOLD_BARS): D arms it; a held roll arms it;
        # a strong rise (RISE_AUTO_ARM) arms it. Arming never changes the
        # look (run 10: the roll's build cue turned the accent blue -> white
        # mid-roll) -- only D cues the build. While armed, a kick PATTERN
        # (DROP_HIT_PATTERN_S) at drop level commits instantly.
        if not self._roll_active:
            self._roll_since = None
        elif self._roll_since is None:
            self._roll_since = now
        if (self._roll_active and self._base_committed != EnergyBucket.DROP
                and now - self._roll_since >= ROLL_ARM_MIN_S and self._drop_arm_source != "dj"):
            self._drop_armed_until = now + ROLL_ARM_HOLD_S
            self._drop_arm_source = "roll"
        if (RISE_AUTO_ARM and self._rise.arm_ready and self._base_committed != EnergyBucket.DROP
                and self._drop_arm_source not in ("dj", "roll")):
            self._drop_armed_until = now + RISE_ARM_HOLD_S
            self._drop_arm_source = "rise"
            log.info("Drop armed by the rise (index %.2f)", self._rise.index)
        if self._drop_armed_until is not None and now >= self._drop_armed_until:
            self._drop_armed_until = None  # nothing landed: disarm
            self._drop_arm_source = ""
            self._drop_cut_pending = False
        # HARD CUT (see HARD_CUT_LATE_PHASE): the DJ counted it in -- commit on
        # the downbeat, whatever the audio reads. Ahead of the kick-pattern
        # path below so a hard cut is never turned into a kick-timed one.
        if self._drop_cut_pending and self._drop_armed_until is not None:
            if self._base_committed == EnergyBucket.DROP:
                self._drop_cut_pending = False  # already there: nothing to cut
            elif (not self._is_pdl_fresh(now)
                  or (self._beat_in_bar == 1 and self._beat_phase < HARD_CUT_LATE_PHASE)):
                self._bucket_candidate = None
                self._bucket_candidate_since = None
                self._bucket_candidate_bar = self._bar  # the downbeat is the phrase origin
                log.info("Drop cut by the DJ on the downbeat (bar %d)", self._bar)
                self._commit_drop(now, level=1.0, raw_energy=max(self._energy_smooth, self._energy))
                self._apply_bucket_overlay(now)
                return
        # Kick edges on the RAW low band (not the peak-hold), kept for the pattern window.
        ref = max(self._low_avg, DROP_HIT_LOW_FLOOR)
        low_now = self._low_recent[-1][1] if self._low_recent else 0.0
        if self._drop_kick_until_bar is not None:
            # The last drop's kick peak becomes the bar the next armed cut must clear.
            if self._base_committed == EnergyBucket.DROP and self._bar < self._drop_kick_until_bar:
                self._drop_kick_samples.append(low_now)
            else:
                if len(self._drop_kick_samples) >= 20:
                    ordered = sorted(self._drop_kick_samples)
                    self._drop_kick_ref = ordered[int(0.9 * (len(ordered) - 1))]
                self._drop_kick_samples = []
                self._drop_kick_until_bar = None
        above = low_now >= DROP_HIT_LOW_RATIO * ref
        if above and not self._kick_prev_above:
            self._kick_edges.append(now)
        self._kick_prev_above = above
        while self._kick_edges and now - self._kick_edges[0] > DROP_HIT_PATTERN_S:
            self._kick_edges.popleft()
        if self._drop_armed_until is not None and self._base_committed != EnergyBucket.DROP:
            peak = self._inst_peak()
            low_peak = max((v for _, v in self._low_recent), default=0.0)
            kick_ok = self._drop_kick_ref is None or low_peak >= ARMED_KICK_VS_LAST_DROP * self._drop_kick_ref
            if len(self._kick_edges) >= 2 and kick_ok and peak is not None and peak >= ARMED_HIT_MIN_INST:
                self._bucket_candidate = None
                self._bucket_candidate_since = None
                self._bucket_candidate_bar = self._bar  # the kick's own bar is the phrase origin
                self._commit_drop(now, level=max(1.0, peak), raw_energy=max(self._energy_smooth, self._energy))
                self._apply_bucket_overlay(now)
                return
        # A DROP/PEAK breakdown exits on the sound RIGHT NOW (2026-09-15,
        # recordings/session-20260915-140630): the music dropped out at
        # 158.6s but the smoothed value (3s release tau, which exists to
        # ride out beat-to-beat dips) took until 164s to fall under the
        # BREAK boundary, so the break look arrived ~10s after the
        # breakdown. The unsmoothed twin was already there. Only the
        # DROP -> BREAK direction reads it: a mid-drop dip between kicks
        # can't fake this, because the candidate below must then persist
        # for a full confirmation (EXIT_CONFIRM_DWELL_S / CUT_EXIT_CONFIRM_S)
        # with the instantaneous level staying under the boundary the whole
        # time -- i.e. real quiet. Upward moves stay on the smoothed value
        # (leading them with the instantaneous level was tried the same day
        # and made builds start LATER: kick-by-kick spikes kept resetting
        # the candidate).
        if (self._base_committed == EnergyBucket.DROP and self._energy_norm_inst is not None
                and bucket_for_energy_with_hysteresis(self._energy_norm_inst, self._base_committed) == EnergyBucket.BREAK):
            raw = EnergyBucket.BREAK
        # A cued BUILD (see cue_build) holds against the energy reading's
        # exit to BREAK -- the whole point is a build the loudness can't see
        # -- until the drop commits, the hold runs out, or it's cancelled.
        if self._build_cue_until is not None:
            if now >= self._build_cue_until or self._base_committed != EnergyBucket.BUILD:
                self._build_cue_until = None
            elif raw == EnergyBucket.BREAK:
                raw = EnergyBucket.BUILD

        # Beat-quantized DROP release (Bible-integration Phase 3, D1 in
        # docs/bible_integration/SPEC_DEVIATIONS.md) -- RETIRED for the
        # energy classifier, 2026-09-15 (recordings/session-20260915-121503):
        # "wait for the next downbeat so the hit lands on a clean boundary"
        # assumed confirmation could arrive BEFORE the hit. Measured: the
        # audible hit (low band 0.5 -> 15 in one tick, on a downbeat) came a
        # full bar before the commit on both drops -- smoothing + dwell alone
        # is ~3-4 beats -- so the wait added up to another whole bar, and the
        # `drop_pending` blackout bridge landed INSIDE the drop's first bar.
        # A reactive classifier is always late; quantizing forward only makes
        # it later. The machinery stays for a future predictive cue (a manual
        # "drop is coming" or a riser detector), where it would be correct.
        if self._pending_drop_release:
            if raw != EnergyBucket.DROP:
                self._pending_drop_release = False  # energy backed off before the downbeat arrived
            elif self._beat_in_bar == 1:
                self._commit_drop(now)
                self._pending_drop_release = False
            self._apply_bucket_overlay(now)
            return

        if raw == self._base_committed:
            self._bucket_candidate = None
            self._bucket_candidate_since = None
        else:
            # LIGHTING_BIBLE.md §9.8's legal-transition matrix
            # (scenes/catalog.py's LEGAL_TRANSITIONS -- the single declared
            # source since the 2026-09-15 polish pass; it used to be a
            # hardcoded copy of its one forbidden edge here), restricted to
            # this module's 3-value base (PEAK/OUTRO are overlays that never
            # block the base -- see the OUTRO_LOOKBACK_S comment above for
            # why NOT doing that matters). Every BREAK/BUILD/DROP pair is
            # legal as of 2026-09-17 -- DROP -> BUILD was the Bible's one
            # forbidden edge and is now allowed as a confirmed exit only
            # (see LEGAL_TRANSITIONS' D3 note and the CUT_EXIT_CONFIRM_S
            # branch below); the matrix check stays so the declared table
            # remains the single source of truth.
            illegal = raw not in LEGAL_TRANSITIONS[self._base_committed]
            if illegal:
                self._bucket_candidate = None
                self._bucket_candidate_since = None
            else:
                direction_up = _BUCKET_ORDER.index(raw) > _BUCKET_ORDER.index(self._base_committed)
                if direction_up:
                    floor_ok = True  # fast entry bypasses the outgoing state's own dwell floor
                    # An unarmed DROP is the safety net (see DROP_ARM_HOLD_BARS):
                    # two bars of drop-range level, late rather than wrong.
                    confirm_s = AUTO_DROP_CONFIRM_S if raw == EnergyBucket.DROP else FAST_ENTRY_DWELL_S
                else:
                    floor_ok = now - self._base_committed_since >= self._dwell_s(self._base_committed)
                    confirm_s = EXIT_CONFIRM_DWELL_S
                    if self._base_committed == EnergyBucket.DROP:
                        # Sustained-cut exit (2026-09-15, same capture): the
                        # music dropped out mid-drop and the 8-bar DROP dwell
                        # floor held full drop lighting over ~8s of quiet.
                        # A BREAK-range reading that persists a couple of
                        # bars is a real breakdown, not a one-bar fill --
                        # let it through on its own longer confirmation.
                        #
                        # ... and ONLY on that longer confirmation, floor or
                        # no floor (2026-09-16, recordings/session-20260916-
                        # 110050): past the DROP dwell the ordinary 4-beat
                        # exit applied, and a one-beat silence gap between a
                        # track's first and second drop (drums out for one
                        # bar, then straight back in) committed BREAK -- the
                        # break look showed for a bar of full-energy music
                        # and the drop re-cut a bar late. A one-bar gap is
                        # a fill in every genre this runs on; a breakdown
                        # is two bars or more. Cost: a real breakdown's
                        # break look lands ~1 bar later than before.
                        #
                        # DROP -> BUILD takes the same exit (2026-09-17,
                        # D3): a pad-heavy post-drop section that never
                        # reads BREAK used to hold DROP through the next
                        # build. Two bars of the smoothed level sitting
                        # under the DROP band is a section change, not a
                        # dip -- the build ramp/strobe start there and the
                        # second drop gets its RMS lead.
                        floor_ok = True
                        confirm_s = CUT_EXIT_CONFIRM_S
                # Note on the two DROP exits (2026-09-17, replaying
                # recordings/session-20260916-114402 at 355-372 s): the
                # "sound right now" lead above flips `raw` to BREAK on every
                # between-hit gap, and each flip restarts the candidate
                # below. That is deliberate, not a gap: a BUILD candidate
                # only confirms when the instantaneous level stays ABOVE the
                # break line for two whole bars (sustained pads/vocal, the
                # kick gone), a BREAK candidate only when it stays UNDER
                # (a real cut), and a thinning drop tail -- sparse kicks
                # over a bed, smoothed 0.42-0.50 with gaps to 0.00 -- keeps
                # flickering and HOLDS DROP. Pooling the two into one clock
                # was tried and turned that 12 s tail into a BUILD with a
                # strobe ramp; a false build is the worse failure (Josh).
                if not floor_ok:
                    self._bucket_candidate = None
                    self._bucket_candidate_since = None
                elif raw != self._bucket_candidate:
                    self._bucket_candidate = raw
                    self._bucket_candidate_since = now
                    self._bucket_candidate_bar = self._bar  # where the sound first supported this
                elif now - self._bucket_candidate_since >= confirm_s:
                    inst_now = self._energy_norm_inst
                    if direction_up and inst_now is not None and (
                        _BUCKET_ORDER.index(bucket_for_energy_with_hysteresis(inst_now, self._base_committed))
                        < _BUCKET_ORDER.index(raw)
                    ):
                        # The smoothed signal says "up" but the sound RIGHT
                        # NOW doesn't (2026-09-15, replaying recordings/
                        # session-20260915-121503 with the faster attack):
                        # one isolated cue-tap hit (raw 0.51 for ~0.5s in
                        # silence) rode the slow release into a 9s BUILD.
                        # A real section keeps its instantaneous level up;
                        # keep the candidate and re-check next tick. Only
                        # the led BUILD -> DROP case reads the peak-hold
                        # here (so a between-kick dip on the commit tick
                        # doesn't push the commit out); everywhere else the
                        # plain instantaneous level keeps its job of
                        # rejecting a lone cue-tap whose spike has passed
                        # (the peak-hold outlives the dwell by 0.1s and
                        # let one through when tried unconditionally).
                        pass
                    elif raw == EnergyBucket.DROP:
                        self._bucket_candidate = None
                        self._bucket_candidate_since = None
                        self._commit_drop(now)  # immediately -- see the retired quantization note above
                    else:
                        self._base_committed = raw
                        self._base_committed_since = now
                        self._bucket_candidate = None
                        self._bucket_candidate_since = None

        self._apply_bucket_overlay(now)

    def _derive_rise(self, now: float) -> None:
        """core/rise.py: hand the completed beat's mean high-band level and
        onset rate to the RiseDetector on every beat change."""
        beat = (self._bar, self._beat_in_bar)
        if self._rise_beat is None:
            self._rise_beat = beat
        if beat != self._rise_beat:
            # Only MUSIC feeds the reference: no silent beats (a track starting
            # from silence would read as a 10x "rise"), and no drop/peak beats
            # (the drop's own bars would read as a rise against the build, and
            # would then become the reference the next build is judged
            # against). A rise is relative to what this track has been doing.
            if (self._rise_n > 0 and self._is_audio_fresh(now)
                    and self._energy_smooth >= ENERGY_NORM_SILENCE_FLOOR
                    and self._base_committed != EnergyBucket.DROP):
                self._rise.on_beat(self._rise_hi_sum / self._rise_n, self._rise_on_sum / self._rise_n)
            self._rise_beat = beat
            self._rise_hi_sum = self._rise_on_sum = 0.0
            self._rise_n = 0
        self._rise_hi_sum += float(self._audio_band_high_abs)
        self._rise_on_sum += float(self._audio_high_onset_rate)
        self._rise_n += 1

    def _fire_auto_build(self, now: float) -> None:
        """Raise core/build_rule.py's automatic cue: BUILD on the next tick.

        Deferred past BREAK's own dwell floor when the rule's kick streak
        began before the shown BREAK did (2026-10-25, TIME-11): the rule
        counted its four kick bars from the last bar of the drop it follows,
        so on One More Time it cued BUILD 3.2 bars into the 140.85 s
        breakdown (its kick sits at ~0.4x the drop's through the whole
        breakdown; the next drop is at 232.55 s) -- a BREAK run under its
        8-bar minimum, LIGHTING_BIBLE.md §9.8 "minimum dwell (before any
        exit)". Somebody To Love's 177 s cue (four kick bars all inside the
        break, drop at 184.63 s) is the shape the rule exists for and still
        fires at once, as do the DJ's C key and every DROP path. The hold
        still counts from the bar the rule fired: waiting for the floor must
        not lengthen a build the kick evidence no longer supports."""
        self._auto_build_pending = False
        self._build_cue_until = self._auto_build_rule_t + AUTO_BUILD_HOLD_S
        self._build_cue_source = "auto"
        self.request_manual_bucket(EnergyBucket.BUILD)

    def _derive_build_rule(self, now: float) -> None:
        """core/build_rule.py: feed the completed bar's means on every bar
        change; raise/release the automatic build cue. Runs after
        _derive_bucket so it sees this tick's committed base."""
        rule = self._build_rule
        look = self.look_bucket
        if look != self._look_prev:
            self._look_prev, self._look_since = look, now
        if self._rule_bar is None:
            self._rule_bar = self._bar
        if self._bar != self._rule_bar and self._rule_n > 0:
            self._rule_bars_in_break.append(look == EnergyBucket.BREAK and self._look_since <= self._rule_bar_t)
            low = self._rule_low_sum / self._rule_n
            energy = self._rule_energy_sum / self._rule_n
            base = self._base_committed
            if self._rule_prev_base == EnergyBucket.DROP and base != EnergyBucket.DROP:
                rule.drop_ended()
            if base == EnergyBucket.DROP:
                rule.note_drop_bar(low)
            manual_cue = self._build_cue_until is not None and self._build_cue_source == "dj"
            fresh = self._is_audio_fresh(now)
            if rule.armed and self._build_cue_until is None and not self._auto_build_pending:
                rule.released()  # the drop landed (or the hold ran out): the cue is over
            if fresh and not manual_cue and rule.on_bar(low, energy, base == EnergyBucket.BREAK):
                log.info("Auto build cue: kick at %.1f vs drop ref %.1f for %d bars", low, rule.ref, self._rule_bar)
                # Kick bars that all sat inside the shown BREAK are a build's
                # kick coming back: cue now. A streak that began in the drop's
                # own last bars is (partly) the drop's kick fading: wait for
                # BREAK's floor (see _fire_auto_build).
                clean = len(self._rule_bars_in_break) == AUTO_BUILD_BARS and all(self._rule_bars_in_break)
                floor_t = (self._look_since + self._dwell_s(EnergyBucket.BREAK)
                           if look == EnergyBucket.BREAK and not clean else now)
                self._auto_build_rule_t = now
                if now >= floor_t:
                    self._fire_auto_build(now)
                else:
                    self._auto_build_pending = True  # see _fire_auto_build
                    self._auto_build_floor_t = floor_t
            elif rule.release_due():
                log.info("Auto build cue released: kick gone %d bars without a drop", self._rule_bar)
                self._build_cue_until = None
                self._build_cue_source = ""
                self._auto_build_pending = False
                rule.released()
            self._rule_prev_base = base
            self._rule_bar = self._bar
            self._rule_bar_t = now
            self._rule_low_sum = self._rule_energy_sum = 0.0
            self._rule_n = 0
        elif self._bar != self._rule_bar:
            self._rule_bar = self._bar
            self._rule_bar_t = now
        if self._auto_build_pending:
            # An unconfirmed classifier BUILD is still SHOWN as the BREAK
            # (see look_bucket), so it keeps the cue pending; a drop, a DJ
            # cue or a release ends it.
            if (self._base_committed == EnergyBucket.DROP or not rule.armed
                    or self._build_cue_source == "dj"):
                self._auto_build_pending = False
            elif now >= self._auto_build_floor_t and self._is_audio_fresh(now):
                self._fire_auto_build(now)
        self._rule_low_sum += float(self._audio_band_low_abs)
        self._rule_energy_sum += float(self._energy)
        self._rule_n += 1

    def _apply_bucket_overlay(self, now: float) -> None:
        """PEAK escalation and OUTRO promotion (see the module-level comment
        above PER_STATE_DWELL_S) -- computed fresh every tick from the base
        classifier's own state, never itself gating the base."""
        exposed = self._bucket_committed
        if self._base_committed == EnergyBucket.DROP:
            sustained = now - self._base_committed_since >= self._dwell_s(EnergyBucket.DROP)
            # Hysteresis on the escalation (2026-09-15, replaying recordings/
            # session-20260915-114303 through this code): energy_norm sat
            # within a hair of BUILD_MAX for a real ~10s stretch and the
            # overlay toggled DROP<->PEAK five times in it. Visually near-
            # identical (PEAK_BOOST is tiny), but every toggle is a bucket
            # change PhraseEngine treats as a reason to consider rotating
            # the look. Same fix shape as the base classifier's own margins.
            if exposed == EnergyBucket.PEAK:
                # PEAK's own dwell + a confirmed exit (see PEAK_EXIT_CONFIRM_BARS).
                if self._energy_norm >= BUILD_MAX - PEAK_EXIT_MARGIN:
                    self._peak_low_since = None
                    final = EnergyBucket.PEAK
                elif self._energy_norm < _catalog.BREAK_MAX:
                    # A cut under way, not a de-escalation: hold PEAK for the
                    # base's cut exit (PEAK -> BREAK) and restart the count.
                    self._peak_low_since = None
                    final = EnergyBucket.PEAK
                else:
                    if self._peak_low_since is None:
                        self._peak_low_since = now
                    held = now - self._exposed_since >= self._dwell_s(EnergyBucket.PEAK)
                    confirmed = now - self._peak_low_since >= PEAK_EXIT_CONFIRM_BARS * self._bar_s()
                    final = EnergyBucket.DROP if (held and confirmed) else EnergyBucket.PEAK
            else:
                # DROP itself must have been ON SHOW for its dwell -- after a
                # de-escalation the base's own clock is long past it.
                drop_held = exposed == EnergyBucket.DROP and now - self._exposed_since >= self._dwell_s(EnergyBucket.DROP)
                final = EnergyBucket.PEAK if (sustained and drop_held and self._energy_norm >= BUILD_MAX) else EnergyBucket.DROP
        elif self._base_committed == EnergyBucket.BREAK:
            # Sustained-dwell requirement added 2026-09-13 (first real
            # --hardware --record session): PEAK escalation above already
            # requires the base to have sat past its own PER_STATE_DWELL_S
            # before escalating -- OUTRO promotion had no equivalent floor
            # at all, so it fired on the VERY FIRST tick of a fresh BREAK
            # if a drop had happened recently. On a real energetic track
            # (drops recurring every 15-20 bars), that meant almost every
            # ordinary breather between drops got immediately mislabeled
            # OUTRO -- confirmed live: 61.6% of one real session's bars
            # landed in BREAK/OUTRO combined, most of them OUTRO. Requiring
            # BREAK's own dwell floor first (same symmetry as DROP->PEAK)
            # gives a fresh break a real chance to just be BREAK before
            # this heuristic even considers promoting it.
            sustained = now - self._base_committed_since >= self._dwell_s(EnergyBucket.BREAK)
            recent_drop = (
                self._last_drop_or_peak_t is not None
                and now - self._last_drop_or_peak_t <= OUTRO_LOOKBACK_S
            )
            # A pending automatic build cue (see _fire_auto_build) means the
            # kick is back at build level: whatever this is, it isn't an outro.
            final = (EnergyBucket.OUTRO if (sustained and recent_drop and not self._auto_build_pending)
                     else EnergyBucket.BREAK)
        elif exposed == EnergyBucket.PEAK:
            # BUILD has no overlay, but PEAK -> BUILD is not a legal move
            # (LIGHTING_BIBLE.md's matrix): step through DROP, which is.
            final = EnergyBucket.DROP
        else:  # BUILD has no overlay
            final = self._base_committed

        if final in (EnergyBucket.DROP, EnergyBucket.PEAK):
            self._last_drop_or_peak_t = now
        if final != exposed:
            self._exposed_since = now
        if final != EnergyBucket.PEAK:
            self._peak_low_since = None
        self._bucket_committed = final

        dwelling_long = (
            self._base_committed == EnergyBucket.DROP
            and now - self._base_committed_since >= TYPICAL_EXTENDED_PEAK_S
        )
        many_drops = self.drop_cycle >= TRANSITION_LIKELY_DROP_CYCLE_THRESHOLD
        self.transition_likely = dwelling_long or many_drops

    def _derive_drop_marker(self, now: float) -> None:
        """Two INDEPENDENT drop-signal paths (2026-09-11, Josh's correction:
        "not all drops go quiet... [this should be] criteria that can help
        signify a drop, not criteria that must be met"): quiet-then-rebound
        (the classic build-quiet-drop pattern, see DIP_THRESHOLD etc. above)
        and a sharp-rise-alone path (a drop with no preceding cut, see
        SHARP_RISE_WINDOW_S etc. above). Either ALONE is sufficient -- this
        is an OR, not an AND. MARKER_COOLDOWN_S is shared between them (via
        `_fire_marker`) so a classic drop, which can satisfy both paths at
        once, only counts as one `drop_cycle` increment.

        Both are corroborating signals alongside bucket classification, not
        a gate on it -- DROP still commits off the energy-threshold+dwell
        machinery in `_derive_bucket` regardless of whether either path here
        ever fires. Also resets `drop_cycle` on a track transition (rising
        edge of `in_transition`) -- see the module-level comment for the
        known limitation (relies on the same transition detection everything
        else here does, so a very smooth beatmatched blend with no clean cut
        might not reset it)."""
        self._drop_marker_pulse = False
        in_transition_now = now < self._transition_until
        if in_transition_now and not self._prev_in_transition:
            self.drop_cycle = 0
            self._track_seed += 1  # next track walks the palettes from a different start
        self._prev_in_transition = in_transition_now

        self._recent_energy.append((now, self._energy_norm))
        while self._recent_energy and now - self._recent_energy[0][0] > SHARP_RISE_WINDOW_S:
            self._recent_energy.popleft()

        # --- path 1: quiet, then a rebound ---
        if self._energy_norm < DIP_THRESHOLD:
            if self._dip_started_at is None:
                self._dip_started_at = now
            self._awaiting_rebound_until = None  # still dipping -- not armed for rebound yet
        else:
            if self._dip_started_at is not None:
                dip_duration = now - self._dip_started_at
                self._dip_started_at = None
                if dip_duration >= MIN_DIP_S:
                    self._awaiting_rebound_until = now + MAX_REBOUND_WINDOW_S

            if self._awaiting_rebound_until is not None:
                if now > self._awaiting_rebound_until:
                    self._awaiting_rebound_until = None  # window expired, no rebound in time
                elif self._energy_norm >= REBOUND_THRESHOLD:
                    self._awaiting_rebound_until = None
                    self._fire_marker(now)

        # --- path 2: a sharp rise, on its own -- no preceding quiet required ---
        if now >= self._marker_cooldown_until and self._energy_norm >= REBOUND_THRESHOLD and self._recent_energy:
            window_min = min(e for _, e in self._recent_energy)
            if self._energy_norm - window_min >= SHARP_RISE_DELTA:
                self._fire_marker(now)

    def _fire_marker(self, now: float) -> None:
        """Shared by both drop-marker paths -- see _derive_drop_marker."""
        if now < self._marker_cooldown_until:
            return  # this real event was already counted via the other path
        self.drop_cycle += 1
        self._drop_marker_pulse = True
        self._marker_cooldown_until = now + MARKER_COOLDOWN_S

    def _inst_peak(self) -> float | None:
        """Max of the unsmoothed energy_norm over the last INST_PEAK_HOLD_S --
        the level of the loudest recent kick, immune to the dip between
        kicks. See _derive_bucket's BUILD -> DROP note."""
        if not self._inst_recent:
            return None
        return max(v for _, v in self._inst_recent)

    @property
    def look_bucket(self) -> EnergyBucket:
        """The bucket the SHOW renders (Josh, run 12, 2026-09-17: "the lights
        turned completely white and slowly started increasing in brightness
        ... we want the drop only to be armed by specific cues"). A BUILD the
        loudness classifier guessed on its own is shown as the BREAK it came
        from -- no Rising Tide, no wash ramp -- until a build CUE confirms
        it: C, or core/build_rule.py's kick-level rule. Arming (D, a roll,
        a strong rise) deliberately does NOT confirm it: arming must change
        nothing on stage (run 10's roll cue turned the accent blue -> white
        mid-roll; run 11's D turned the lights white) -- the pre-drop strobe
        runs over whatever look is on. The classifier's own state is
        untouched (`classifier_bucket` in the recorder)."""
        if self._bucket_committed == EnergyBucket.BUILD and not self.build_confirmed:
            return EnergyBucket.BREAK
        return self._bucket_committed

    @property
    def build_confirmed(self) -> bool:
        return self._build_cue_until is not None

    def _derive_palette(self) -> None:
        """Re-pick the phrase-frozen palette entries ONLY when a new
        PALETTE_PHRASE_BARS phrase starts or the bucket FAMILY changes
        (break/outro, build, drop/peak -- a family change is already
        debounced by _derive_bucket, so this never flickers); hold otherwise.
        A downward change (drop -> break) re-picks too, deliberately: leaving
        a break under blood_red until the next 16-bar boundary is not what a
        break should look like. See engines/palette.py."""
        bucket = self.look_bucket.value
        fam = palette_mod.family(bucket)
        family_changed = fam != self._palette_family or self._track_seed != self._palette_seed
        if family_changed:
            # Phrases count from the bar the family was entered (the drop's
            # own phrases from the drop, like the strobe's), not from bar 0
            # of the set -- 2026-09-15, session-20260915-144431: a bar-0-
            # anchored 16-bar boundary landed 0.7s after a drop commit
            # (cold_steel -> blood_red within one bar) and 0.5s before the
            # second drop's cut (heads cyan -> amber -> red). Josh: "it'll
            # hit one scene, another one, and then the scene it should be at."
            self._palette_origin_bar = self._bar
        phrase = (self._bar - self._palette_origin_bar) // palette_mod.PALETTE_PHRASE_BARS
        if not family_changed:
            if phrase == self._palette_phrase or fam not in palette_mod.PHRASE_REPICK_FAMILIES:
                return  # drop/peak and build hold one color for the whole stay, see palette.py
            if self._bar - self._palette_pick_bar < palette_mod.PALETTE_MIN_HOLD_BARS:
                return  # a boundary can't re-pick a color that just landed
        self._palette_phrase = phrase
        self._palette_family = fam
        self._palette_seed = self._track_seed  # a new track re-picks right away, not at the next boundary
        self._palette_pick_bar = self._bar
        self.palette, self.palette_spot = palette_mod.pick(bucket, phrase, self.drop_cycle, self._track_seed)

    # ------------------------------------------------------------------ health
    def _is_pdl_fresh(self, now: float) -> bool:
        return self._last_beat_time is not None and (now - self._last_beat_time) < PDL_STALE_AFTER

    def _is_midi_fresh(self, now: float) -> bool:
        return self._last_midi_time is not None and (now - self._last_midi_time) < MIDI_STALE_AFTER

    def _is_audio_fresh(self, now: float) -> bool:
        return self._last_audio_time is not None and (now - self._last_audio_time) < AUDIO_STALE_AFTER

    # ------------------------------------------------------------------ output
    def _build_snapshot(self, now: float) -> ShowSnapshot:
        return ShowSnapshot(
            t=now,
            bpm=self._bpm,
            beat_phase=self._beat_phase,
            beat_in_bar=self._beat_in_bar,
            bar=self._bar,
            playing=self._playing,
            loop_active=self._loop_active,
            on_air=self._on_air,
            master_deck=self._master_deck,
            crossfader=self._midi["crossfader"],
            fader_a=self._midi["fader_a"],
            fader_b=self._midi["fader_b"],
            eq_low_a=self._midi["eq_low_a"],
            eq_mid_a=self._midi["eq_mid_a"],
            eq_high_a=self._midi["eq_high_a"],
            eq_low_b=self._midi["eq_low_b"],
            eq_mid_b=self._midi["eq_mid_b"],
            eq_high_b=self._midi["eq_high_b"],
            audio_energy=self._audio_rms,
            audio_band_low=self._audio_band_low,
            audio_band_mid=self._audio_band_mid,
            audio_band_high=self._audio_band_high,
            audio_band_low_abs=self._audio_band_low_abs,
            audio_band_mid_abs=self._audio_band_mid_abs,
            audio_band_high_abs=self._audio_band_high_abs,
            audio_high_onset_rate=self._audio_high_onset_rate,
            audio_high_flux=self._audio_high_flux,
            audio_band_snare_abs=self._audio_band_snare_abs,
            roll_period_score=self._roll_period.score,
            energy=self._energy,
            energy_norm=self._energy_norm,
            bucket=self.look_bucket.value,
            classifier_bucket=self._bucket_committed.value,
            drop_cycle=self.drop_cycle,
            drop_hit_intensity=self.drop_hit_intensity,
            drop_start_bar=self.drop_start_bar,
            build_cue=self.build_cue_active,
            drop_armed=self.drop_armed,
            drop_arm_source=self._drop_arm_source if self.drop_armed else "",
            drop_cut_pending=self.drop_cut_pending,
            rising=self._rise.rising,
            rise_index=self._rise.index,
            build_cue_source=self._build_cue_source if self._build_cue_until is not None else "",
            norm_seeded=self.norm_seeded,
            drop_candidate=self._bucket_candidate == EnergyBucket.DROP,
            palette=self.palette,
            palette_spot=self.palette_spot,
            transition_likely=self.transition_likely,
            drop_pending=self._pending_drop_release,
            drop_marker=self._drop_marker_pulse,
            in_transition=now < self._transition_until,
            free_running=not self._is_pdl_fresh(now),
            pdl_fresh=self._is_pdl_fresh(now),
            midi_fresh=self._is_midi_fresh(now),
            audio_fresh=self._is_audio_fresh(now),
        )

    def _maybe_log(self, now: float, snap: ShowSnapshot) -> None:
        if now - self._last_log >= self._log_interval:
            self._last_log = now
            log.info("ShowState | %s", snap.summary())
        if (self._range_callback is not None and self.norm_warm
                and now - self._last_range_persist >= RANGE_PERSIST_INTERVAL_S):
            self._last_range_persist = now
            try:
                self._range_callback(*self.energy_norm_range)
            except Exception:  # noqa: BLE001 - persistence must never touch the clock thread
                log.exception("energy_norm range callback failed")
