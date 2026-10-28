"""Reactive Layer — the always-on base of the show (docs/02 FR-2.1, docs/12 Phase 3).

Wash/par brightness follows LIGHTING_BIBLE.md's per-energy-state model (§5/
§9.1, 2026-09-08 full-alignment build) — a held constant at DROP/PEAK (with a
beat-4 accent), a structural bar-position ramp at BUILD, a slow non-pulsing
"breathing" envelope at BREAK, a one-way fade at OUTRO — plus every automated
strobe as one software dimmer toggle on par + wash bar (`_StrobeClock`).
BPM-locked symmetric movement on the moving heads and the phrase-frozen
palette color (engines/palette.py, §3.8 -- picked by the metronome, shared
with every scene) round out the always-on layer. This is what makes the rig
feel musical even before scenes and transitions exist; it reads only the
metronome's interpolated clock, never raw packet timing.

History: the 2026-09-03 kick-follow envelope (four live-verified passes,
`_kick_pulse`/`_beat_locked_kick`) was retired as wash's driver on
2026-09-08 (the Bible's drop looks are "static, nothing reacts") and deleted
outright in the 2026-09-15 polish pass. Recover it from commit `feeb543` or
earlier if a sparse kick-accent feature ever wants it; the design notes live
in the project memory.
"""

from __future__ import annotations

import math

from ..clock import Clock
from ..core.metronome import DROP_INTENSITY_CEIL, DROP_INTENSITY_FLOOR
from ..core.show_state import ShowSnapshot
from ..dynamics import musical_bar_position
from ..fixtures.profiles import Par, WashBar
from ..fixtures.rig import Rig
from ..merge.merge import Contribution, ManualParams
from ..scenes.base import EnergyBucket
from ..scenes.catalog import BREAK_MAX
from ..core.rise import RISE_FULL, RISE_ON
from ..core.snare_trigger import RollRiseDetector
from ..scenes.base import scene_color, spot_color
from .envelope import NORMALIZE_TAU_S, BandEnvelope

# Synth Sync (Josh, 2026-09-15): "in a lot of UKG songs the most prominent
# noise is the synths or the stabs... a feature on the UI I could click and
# then the par lights or the wash bar literally mimic the synths." While
# `ManualParams.synth_sync` is on, the wash dimmer follows a BandEnvelope on
# `audio_band_mid_abs` (250-2000 Hz, inputs/audio.py's MID_HZ -- where UKG
# chord stabs and synth lines sit) instead of the bucket model. Par + wash
# bar only; the moving head keeps its own dimmer and color.
# Originally every automated strobe was suppressed while on ("the synth OWNS
# the wash"). Reversed 2026-09-19 after a few sets with it on: Josh keeps
# Synth Sync on by default now ("more movement, more energy... more synced")
# and wants a full set that way, so the build ramp / roll accent / drop-tail
# strobes override the synth level while they run. Only the operator's
# manual burst (merge.py apply_strobe_burst) was ever independent of this.
# Checked against real captures
# before building: inside a drop the mid band swings ~2x more than the low
# band (p90/p50 2.7 vs 1.3), so there is contrast to show.
#
# Deliberately the retired kick-follow's PASS-2 shape (a continuous
# baseline-excess envelope), not its pass-4 beat-locked window: kicks sit on
# the grid, so one decision per beat was right for them; garage stabs are
# syncopated and off-grid, so a beat window would drop exactly the hits
# this mode exists for. Known limit, told to Josh: amplitude in a band can't
# separate instruments -- vocal chops and snare body share the mids and
# will move the lights too.
#
# Floor/ceiling are the contrast range Josh confirmed live on 2026-09-03 for
# the kick follower ("tighten it a tad, less strobe-like" -> a bigger floor
# was tried and read as MORE delayed: contrast is what makes timing look
# crisp). The envelope is self-normalized (engines/envelope.py's
# NORMALIZE_*): the loudest recent stab reads as full brightness on any
# song; `SYNTH_GAIN` is unused on that path and kept only for a plain-gain
# fallback.
SYNTH_FLOOR = 0.02
SYNTH_CEILING = 0.95
SYNTH_GAIN = 0.236
# Break breath (Josh, 2026-09-16, after the second laser run: breaks read
# "very stagnant... it can just look like it's breathing to the synths or
# stabs"): in BREAK/OUTRO -- and only when Synth Sync isn't already owning
# the wash -- the same normalized mid-band envelope is ADDED to the bucket
# model's slow breathing level at this depth. Subtle by design: a pad swell
# or a stab lifts the wash by at most this much on top of the 15-35% breath.
BREAK_SYNTH_DEPTH = 0.15

# STROBE_THRESHOLD (a fixed energy_norm > 0.85 gate, RETIRED 2026-09-14) used
# to be the sole gate on the reactive strobe, in ANY bucket. Two real problems
# with it, confirmed live: (1) it was raw-energy-only, no bucket awareness --
# a bass-heavy track sat above 0.85 96% of the whole song at one point (was
# raw snap.energy until 2026-09-07, same fixed-absolute-threshold flaw as the
# old bucket_for_energy call -- see show_state.py's energy_norm docstring),
# read live as "turning on and off randomly, fluttering," not a rare accent;
# (2) even switched to energy_norm, it still fired in PEAK/anywhere energy
# spiked, not specifically BUILD/DROP -- never a real "buildup" effect, just
# a "loud moment" effect (spec/conformance.md LOOK-05/TIME-05). Replaced by
# a bucket gate (BUILD/DROP only) inside `_strobe_rate` itself -- see there.
# Named Hz targets from LIGHTING_BIBLE.md §13 (SAFE-01..06, corrected
# 2026-09-08 evidence-verification pass) -- kept here as the DOCUMENTED
# TARGETS, not yet a working Hz clamp: nobody has measured this specific
# wash bar's real DMX-byte-to-Hz curve (photodiode/camera count, per §11's
# own method), and fabricating one would repeat the exact mistake the
# correction pass exists to catch (see spec/conformance.md SAFE-01..06,
# spec/FINDINGS.md). Reference these names once real calibration data
# lets STROBE_MAX below be derived from an actual Hz ceiling instead of
# the conservative placeholder it is now.
SAFETY_STROBE_SUSTAINED_MAX_HZ = 4.0
SAFETY_STROBE_ABSOLUTE_MAX_HZ = 8.0
SAFETY_STROBE_FORBIDDEN_BAND_HZ = (10.0, 25.0)
SAFETY_STROBE_STRICT_MODE_MAX_HZ = 2.0
SAFETY_STROBE_RED_MAX_HZ = 3.0

STROBE_MAX = 0.75       # CEILING, Josh's explicit call (2026-09-14, live on real
                        # hardware): raised from the old 0.15 placeholder after he
                        # personally watched a full 0-255 sweep of the wash bar's
                        # real strobe channel (white, full range) and confirmed it
                        # reads correctly the whole way through -- see
                        # tools/simulations or the session recording this came
                        # from (recordings/session-20260914-*). Still NOT a
                        # measured-safe Hz value -- nobody has run the actual
                        # byte-to-Hz calibration (photodiode/camera per §11's
                        # method), so SAFE-01/02/03/05/06 in spec/FINDINGS.md
                        # remain open. This is a deliberate, disclosed interim
                        # value like the 0.15 it replaces, just a different
                        # number Josh chose after seeing the real hardware run.
                        # Paired with WashBar.STROBE_BRIGHTNESS_CAP_DMX (a
                        # SEPARATE brightness clamp, not a strobe-rate one) so a
                        # strobe burst can't also run at full dimmer brightness.

# STROBE_DROP_REFERENCE_ENERGY_NORM (RETIRED same day, 2026-09-14) scaled
# strobe against raw per-tick snap.energy_norm -- confirmed live to be the
# wrong signal: Josh watched a real drop and reported the strobe "isn't a
# consistent value... it just keeps randomly changing" every beat/transient,
# reading as unpatterned flicker rather than a strobe. Raw energy_norm is
# exactly the "continuously audio-tracked" behavior this project already
# rejected for DROP's own wash_level (see the DROP section above: "chosen
# once, not chased," `snap.drop_hit_intensity` captured once at commit and
# held static) -- strobe was the one thing in this bucket still chasing
# per-tick energy. Replaced below: DROP/PEAK reuse that same held
# drop_hit_intensity, BUILD reuses the same bar-position ramp
# _bucket_wash_level's own BUILD branch computes -- both smooth, structural,
# and exactly as "linear throughout" as Josh asked for, not audio-reactive.

# EVERY automated strobe is a software-timed dimmer on/off toggle, on the
# par AND the wash bar, driven by ONE shared `_StrobeClock` (2026-09-15,
# third live capture, recordings/session-20260915-125433). History: the par
# has no native strobe channel (4CH dimmer/R/G/B), so 2026-09-14 gave it a
# software toggle while the wash bar kept using its native ch2 rate byte.
# Josh, live: "there's a strobe discrepancy between the wash bar and the par
# lights. They should be going at the exact same time." Structurally they
# never could -- two different clocks (ours vs. the fixture's own oscillator,
# whose byte-to-Hz curve nobody has measured). Now both fixtures flash from
# the same phase accumulator, so they are on/off on the same DMX frame, and
# the flash Hz is an exact known number capped at the Bible's
# SAFETY_STROBE_SUSTAINED_MAX_HZ -- the unmeasured fixture curve is out of the
# automated loop entirely (only the operator's manual burst, merge.py's
# `apply_strobe_burst`, still writes ch2). The same capture also showed the
# old `(t * hz) % 1` phase formula: with hz ramping every tick the phase
# jumped discontinuously -- measured par on/off durations of 0.1s and 0.7s
# where ~0.3s was expected -- read live as the build strobe "looking
# inconsistent, not synced with the music." `_StrobeClock` accumulates
# phase += hz * dt instead, so a ramping rate still flashes evenly.
STROBE_TOGGLE_MAX_HZ = SAFETY_STROBE_SUSTAINED_MAX_HZ  # 4.0 -- rate01 == STROBE_MAX maps here

# On-phase brightness of every strobe flash -- a BYTE, never the ambient
# wash level. The third capture showed the build's flashes riding the
# 20%-start wash ramp: 12-25% brightness for the first three bars,
# invisible. Josh's call (2026-09-15): flash at the cap, dark between, and
# let the build be a RATE ramp. After the fourth test ("it can reach a
# higher peak"): the cap went 140 -> 180, and the BUILD's flash now climbs
# from STROBE_FLASH_START_DMX (the old 140) to the cap alongside the rate
# ramp, so the top of a build is both faster AND brighter than its start
# without touching the 4 Hz line. DROP/PEAK tail bursts flash at the cap.
# Same bytes on the par -- not independently confirmed on that fixture.
STROBE_FLASH_DMX = WashBar.STROBE_BRIGHTNESS_CAP_DMX  # 180
STROBE_FLASH_START_DMX = 160

# Burst-duration limit (LIGHTING_BIBLE.md §13, 2026-09-08 audit): "assert
# strobe_burst_duration <= 1.5s then a mandatory gap." Previously the reactive
# strobe could stay continuously nonzero for as long as energy stayed above
# STROBE_THRESHOLD -- unbounded, possibly minutes during a sustained loud
# section. This IS enforceable without knowing the real Hz mapping (it's a
# software-side duty-cycle rule, not a hardware-calibration one), so fixed then.
STROBE_MAX_BURST_S = 1.5     # a single continuous strobe MOMENT runs at most this long

# STROBE_MIN_SPACING_S -- CONFIRMED BUGGY, then fixed, 2026-09-08 (real
# --hardware --record capture, see [[livereact-project]] memory): the original
# 1.5s "mandatory gap" was sized to match the burst DURATION, not to space
# separate strobe MOMENTS apart -- so during a sustained kick-heavy passage
# (energy_norm pinned above STROBE_THRESHOLD for 30+ real seconds), the very
# next tick after each 1.5s gap immediately re-triggered a new burst. Replayed
# against tonight's actual captured energy_norm sequence (not guessed): 11
# separate bursts crammed into one 36-second stretch, a continuous mechanical
# on/off/on/off cycle -- confirmed live as "the wash bar trying to keep up
# with the kicks... random stuttering," not the "rare accent" LIGHTING_BIBLE.md
# §13 wants. Second bug, same root cause, fixed here too: a burst that ended
# NATURALLY (energy dropped below threshold before hitting STROBE_MAX_BURST_S)
# used to earn NO gap at all -- only a burst that hit the duration cap did --
# so a moment ending naturally could restart on the very next tick. Both paths
# (hit-the-cap and ended-naturally) now enforce the SAME minimum spacing
# between the end of one moment and the start of the next.
STROBE_MIN_SPACING_S = 8.0    # NOT yet tuned by ear/measurement, this project's
                              # usual caveat -- long enough to break the
                              # confirmed-live chaining into visibly separate
                              # accents, short enough to still land more than
                              # once across a long loud passage.

# Phrase-aligned DROP/PEAK strobe (2026-09-15, from the first real
# --hardware --record capture after the held-value redesign: recordings/
# session-20260915-114303). The held drop_hit_intensity rate was CONSTANT for
# the whole drop, so the burst gate above was the only thing shaping it --
# measured: 21 bursts, every one 1.4-1.5s long, spaced 9.4-9.6s apart like a
# clock, landing on random beat positions. Josh, live: "start strobing
# randomly for maybe one beat and then just stop... multiple times." A timer,
# not music. Now the strobe only fires in the TAIL of each STROBE_PHRASE_BARS-
# bar phrase, counted from the drop's own downbeat (`bar` is a locally-
# reconstructed counter with no real phrase awareness -- the drop commit is
# beat-quantized to a downbeat in core/metronome.py, so "phrases since the
# drop landed" is the best alignment available; see the busking note in
# LIGHTING_BIBLE.md §9.9). The burst-cap/spacing gate above stays as a pure
# safety floor (8 bars at any plausible tempo is well past 8s), no longer
# the thing deciding WHEN a strobe happens.
STROBE_PHRASE_BARS = 8        # the Bible's own phrase length
STROBE_PHRASE_TAIL_BEATS = 2  # beats 3+4 of the phrase's last bar -- "strobe
                              # into the phrase change," ends exactly on the
                              # next downbeat. ~0.9s at 133bpm, under
                              # STROBE_MAX_BURST_S at anything above ~80bpm.

# The song's own strobe rhythm (rekordbox/song_style.py, 2026-10-28). Every style is ONE moment per
# phrase, so each stays inside the burst gate below (<= STROBE_MAX_BURST_S, then STROBE_MIN_SPACING_S
# dark -- a 4-bar rhythm or two separate hits in a bar would be eaten by the 8 s spacing):
# (phrase bars, bar of the phrase, beats that strobe). "tail" is the rule above; "stab" is beat 4
# alone; "downbeat" lands ON the next phrase's first two beats instead of leading into them (never
# on the drop's own first bar); "sparse" is the tail of every second phrase; "none" leaves the drop to
# the wash and the heads (TIME-21 restraint). The build's count-in strobe is the same for every style.
STROBE_STYLES: dict[str, tuple[int, int, tuple[int, ...]]] = {
    "tail": (STROBE_PHRASE_BARS, STROBE_PHRASE_BARS - 1, (3, 4)),
    "stab": (STROBE_PHRASE_BARS, STROBE_PHRASE_BARS - 1, (4,)),
    "downbeat": (STROBE_PHRASE_BARS, 0, (1, 2)),
    "sparse": (2 * STROBE_PHRASE_BARS, 2 * STROBE_PHRASE_BARS - 1, (3, 4)),
    "none": (0, 0, ()),
}

# Continuous BUILD strobe (Josh, 2026-09-15, second live test of the day:
# "the strobe should be strobing throughout the build up and the strobe
# should slowly get faster throughout the build up but currently it is not
# doing that, I don't even see the strobe"). Earlier the same day the BUILD
# strobe had been retired entirely (the burst-gated bar-ramp version fired
# one ~1s burst then went dark for 8s -- one of the "random strobe" moments).
# This is the un-gated version of that ramp: a SUSTAINED rate that starts at
# BUILD_STROBE_START of STROBE_MAX at build entry and climbs in two stages
# to STROBE_MAX by BUILD_STROBE_STAGE2_BARS. Safety: §13's burst/gap rule is
# for strobe BURSTS; a sustained rate is governed by
# SAFETY_STROBE_SUSTAINED_MAX_HZ (4 Hz), which the software toggle meets
# EXACTLY on both fixtures (its Hz is computed from this rate, capped at
# STROBE_TOGGLE_MAX_HZ = 4.0 -- see that constant). The floor exists because
# a toggle at a very low rate reads as the light going OUT for seconds, not
# as a strobe.
#
# Retuned 2026-09-15 after the fourth live test (session-20260915-140630,
# Josh: the strobe "started at too low of a value ... maybe like 30 higher
# ... a little slow, and it also can reach a higher peak"): start 0.25 ->
# 0.5 (1 Hz -> 2 Hz), and the ramp reaches the ceiling over 6 bars instead
# of 8 so the last bars of a typical build sit AT 4 Hz rather than arriving
# there on the drop. The ceiling itself stays 4 Hz: that is the Bible's
# sustained-strobe safety line (SAFE-01), not a taste number -- a faster
# peak needs the burst rules (<= 1.5s, then a gap) and there is no way to
# know which 1.5s of a build is the last. If the top still reads weak, the
# lever is flash brightness (STROBE_FLASH_DMX), Josh's own cap.
BUILD_STROBE_START = 0.5          # 2 Hz where the rise starts -- a pulse, not yet a strobe
BUILD_COUNTIN_BEATS = 8           # planned drop within this many beats (2 bars): the strobe counts in
                                  # (drive mode). Short on purpose: LIGHTING_BIBLE.md LOOK-05 wants the strobe held
                                  # back through the build; Josh's 'builds count in' rule (2026-10-25) takes the
                                  # last two bars. The scorer's LOOK-05/TIME-05 will flag those bars by design.
# THE RISE DRIVES THE STROBE (2026-09-17, run 10 -- recordings/session-
# 20260917-154312): the bar-count ramp from BUILD entry ran for 42 s before
# the first drop, because the loudness classifier's BUILD is a plateau
# guess; the audible rise (core/rise.py: high-band level + onset density
# climbing) began ~8 bars out. Josh: "arm the strobe and then it correctly
# identifies in the next however many bars when the strobe starts and when
# it ends." So the build strobe runs only while the drop is ARMED (D, a
# roll, or a strong rise -- core/metronome.py) AND the rise is on, and its
# rate is the rise's intensity: BUILD_STROBE_START at rise_index RISE_ON,
# STROBE_MAX at RISE_FULL (core/rise.py). It ends when the rise ends or the
# drop cuts. A loudness-only BUILD keeps its wash ramp and look, no strobe.
# The 8/16-bar two-stage ramp (BUILD_STROBE_STAGE*) is retired.
# Two-stage ramp (2026-09-16, third live run, recordings/session-20260916-
# 110050, a 15-bar build): the previous single 8-bar ramp reached 4 Hz at
# bar 8 and then sat there for six more bars -- Josh: "started a little
# early." A build in 4/4 dance music is 8 or 16 bars, and nothing in the
# audio says which in advance (this one's energy_norm actually FELL through
# its second half -- a filter pulling the low end out -- so an energy-driven
# ramp is not an option either). So the ramp hedges: stage 1 climbs from
# START to MID over 8 bars (an 8-bar build ends at 2.8 Hz, the roll accent
# adds the 255 flashes on top when a roll/riser is there), stage 2 climbs
# from MID to 4 Hz over bars 8-16 (a 16-bar build ends at the ceiling), and
# past 16 bars it holds. Not tuned to one song: the shape is bar-count
# only, the same on every track. A 4-bar build only reaches ~2.4 Hz.
# Raised 0.4/0.7 -> 0.5/0.85 and the flash byte's start 140 -> 160 after the
# fifth live run (2026-09-16, recordings/session-20260916-114402, a cued
# ~9-bar build that topped out at 3.1 Hz) -- Josh: "starts really slow and
# doesn't really get that strobe-y... raise the bar."
# Retuned again after the second live laser run (2026-09-15 night,
# recordings/session-20260915-212918, an 8-bar/16 s build): the previous
# 0.5 / 6 bars / squared shape hit 4 Hz five seconds before the drop --
# Josh: "the strobing went into effect way too early... by the time it
# dropped, the strobing was already done." Now the strobe idles as a slow
# pulse for the first half of a build and only becomes a strobe in the last
# bars; the roll accent (STROBE_ROLL_FLASH_DMX, below) supplies the intense
# finish on top when a roll/riser is detected. A 4-bar build only reaches
# ~1.9 Hz on this ramp -- a known cost of anchoring the shape to 8 bars.

# The build strobe ends on the COMMIT (the hard cut into the drop look), not
# on the candidate flag. History: 2026-09-15 test 4 (session-140630) had the
# commit trailing the audible hit by ~2 beats, so the strobe was cut on
# `drop_candidate` and Josh saw it stop on the hit. The second laser run the
# same night (session-212918) went the other way: the candidate fired on the
# riser's crescendo 0.8 s before the commit and Josh heard the strobe stop
# BEFORE the drop. The commit is the one event the wash cut, the look cut and
# the strobe end now share, so whichever way a song's lead falls the drop
# reads as a single cut, not a strobe that dies and then a cut.

# build_snare_accent's roll accent (rebuilt 2026-09-15 night, second live
# run -- see core/snare_trigger.py for why the onset-counting version never
# fired on a real roll). While RollRiseDetector says a roll is rising into
# the drop, the strobe rate goes to STROBE_MAX and the flash byte to
# STROBE_ROLL_FLASH_DMX -- brighter than the build's own flashes, which by
# the end of a build are already AT 4 Hz, so a same-rate accent was
# invisible. No burst gate on this path: 4 Hz is the sustained-safe line
# (SAFE-01), so a roll that lasts several bars is legal as-is; the accent
# ends with the roll or the build (the commit into the drop).
STROBE_ROLL_FLASH_DMX = 255

# Color: the phrase-frozen palette entry on the snapshot (engines/palette.py,
# picked by core/metronome.py). This engine used to keep its own 16-bar
# frozen hue anchor (`HUE_UPDATE_BARS`, 2026-09-08) -- but it never reached
# the wash (COLOR is LTP by priority and every Phrase scene outranks this
# layer) and it was pinned to the low band 100% of every capture (bass always
# wins a raw max() in electronic music). Retired 2026-09-15 in favor of the
# one shared pick.

# Wash/par brightness driven by energy-state (LIGHTING_BIBLE.md §5 look
# library + §9.1's per-position behavior table) -- one branch per bucket of
# the Bible's 5-state model (BREAK/BUILD/DROP/PEAK/OUTRO, scenes/base.py's
# EnergyBucket), see _bucket_wash_level.
#
# DROP ([VERIFIED against the Bible's own look defs] `drop_blackout_hit`:
# "reactivity: static all, kick_follow:false, the cut IS the effect, nothing
# reacts"): held, near-full intensity — NOT continuously audio-tracked
# (matches "static... nothing reacts"). Bible-integration Phase 2 correction
# (2026-09-11, Josh: drop intensity should reflect how loud the real sound
# actually was, not a fixed number regardless of it): the target now comes
# from `snap.drop_hit_intensity` -- captured ONCE, in core/metronome.py, at
# the exact moment DROP was committed, then held static from there (same
# "chosen once, not chased" principle as before, just no longer a flat
# constant). See core/metronome.py's DROP_INTENSITY_FLOOR/CEIL for the range
# it's scaled within (still the Bible's stated 85-100%).

# PEAK (`peak_full_rig`: "wash 85-100%... reactivity: static hue+pan,
# kick_follow:false") -- PEAK is genuinely new (see EnergyBucket's
# docstring, scenes/base.py) and is reached only by escalating out of a
# sustained DROP, never from a raw energy level. Held static like DROP, with
# a small additive boost on top of that SAME drop's captured hit intensity
# -- "more committed than the drop" scales with how big the drop already
# was, rather than jumping to an unrelated fixed ceiling.
PEAK_BOOST = 0.03  # added to the held DROP level, clamped to 1.0

# Held DROP wash level -- 2026-09-15, a deliberate, disclosed DEVIATION from
# the Bible's literal "wash 85-100%" for drop_blackout_hit/peak_full_rig.
# Rendering drop_hit_intensity (0.85..1.0) directly as the held level left
# NO headroom above it: measured on the first real capture after the beat-4
# flash landed (recordings/session-20260915-114303), mean wash-bar brightness
# on beat 4 was 0.965 vs 0.964 on beats 1-3 -- the flash was mathematically
# invisible (on a track whose hit captured as 1.0, literally zero), and a
# strobe burst (brightness-capped at WashBar.STROBE_BRIGHTNESS_CAP_DMX=140)
# read as a 45% brightness CUT against the full wash rather than an accent.
# Josh, live: "aren't beating on the fourth beat... or if they are, it's
# very, very subtle"; "they just strobe and then turn off really quick."
# So the held level now maps the captured hit onto [DROP_HELD_LOW,
# DROP_HELD_HIGH] instead -- still "chosen once, not chased," still scaling
# with how loud the real hit was -- leaving the top quarter for the beat-4
# accent and keeping the held byte at/below the strobe brightness cap
# (0.76 -> byte 139 through fixtures/profiles.py's gamma curve, vs the
# cap's 140; 0.66 -> ~103), so a burst is never a visible dim. In linear
# light that's roughly 40-55% output. NOT yet tuned by eye on hardware.
DROP_HELD_LOW = 0.66
DROP_HELD_HIGH = 0.76


def _drop_held_level(drop_hit_intensity: float) -> float:
    """Map the once-captured hit intensity (core/metronome.py's
    DROP_INTENSITY_FLOOR..CEIL range) onto the held wash range above."""
    span = DROP_INTENSITY_CEIL - DROP_INTENSITY_FLOOR
    progress = (drop_hit_intensity - DROP_INTENSITY_FLOOR) / span if span > 0 else 1.0
    progress = max(0.0, min(1.0, progress))
    return DROP_HELD_LOW + (DROP_HELD_HIGH - DROP_HELD_LOW) * progress

# Beat-4 accent flash, DROP/PEAK only -- Josh's own directed design call
# (2026-09-13, after watching two real --hardware --record sessions), NOT
# derived from LIGHTING_BIBLE.md's text: the Bible's own "nothing reacts"
# static-drop design (§5, kick_follow:false) read as genuinely flat and
# disconnected on real hardware, with the moving head (still its old,
# un-migrated raw-energy-driven intensity, see MOVE-07/`_musical_position`
# call sites) the only thing giving any sense of the beat. Explicit ask:
# "relatively static, and then... on beat four it flashes... shouldn't be
# overkill where it's just all flashing." A deliberate, disclosed
# DEVIATION from the Bible's literal text, not an oversight -- kept
# separate from PEAK_BOOST above (a real Bible-sourced number) so this
# doesn't get mistaken for one. HTP-style: only ever ADDS brightness on
# top of the held DROP/PEAK level, never dims it, so "relatively static"
# the rest of the bar is genuinely untouched.
#
# 2026-09-15 rework (same capture as DROP_HELD_* above): the old version
# decayed from 1.0 to 0 across the WHOLE beat via max(held, flash), so with
# a held level of 0.92-1.0 it only ever exceeded the base for the first
# ~8% of the beat (~35ms) -- unseeable. Now the flash decays from full back
# down to the HELD level over the first BEAT4_FLASH_DECAY_PHASE of the beat,
# so the whole visible excursion is held->1.0->held (~270ms at 133bpm),
# regardless of what the held level happens to be.
BEAT4_FLASH_PEAK = 1.0          # the flash always peaks at full
BEAT4_FLASH_DECAY_PHASE = 0.6   # fraction of beat 4 the flash takes to fall back to held

# The song's own accent beat(s) (rekordbox/song_style.py, Josh 2026-10-28: "each song ... unique"):
# beat 4 stays the default; brighter songs flash on the backbeat (2 and 4, where the claps are).
# No downbeat option: a flash on 1 doubles the kick (the Bible's amateur kick-follow), and it moved
# SWEETER's clap drop off its claps (STRUCT-09 PASS -> FAIL in the 2026-10-28 gate run).
ACCENT_BEATS: dict[str, tuple[int, ...]] = {"4": (4,), "24": (2, 4)}


def _beat4_flash(snap: ShowSnapshot, held: float) -> float:
    """The wash level during the accent beat (beat 4, or the song's own
    ACCENT_BEATS): starts at BEAT4_FLASH_PEAK on the beat
    and falls linearly back to `held` by BEAT4_FLASH_DECAY_PHASE of the
    beat, then stays at `held`. Returns `held` unchanged on any other
    beat."""
    if snap.beat_in_bar not in ACCENT_BEATS.get(snap.planned_accent, (4,))             or snap.beat_phase >= BEAT4_FLASH_DECAY_PHASE:
        return held
    remaining = 1.0 - snap.beat_phase / BEAT4_FLASH_DECAY_PHASE
    return held + (BEAT4_FLASH_PEAK - held) * remaining

# BUILD (`build_riser_wash`, the Bible's own "canonical build"): "intensity
# climbs across the build... the ramp IS the build; drive it from bar
# position, not audio, to stay predictable." Structural, not audio-reactive
# at all -- deliberately the opposite of kick-follow. Phase 2: this range now
# also covers the old HOLD bucket's energy (0.55-0.80, folded in per Open
# Question #1) -- the ramp formula itself is unchanged, just a wider energy
# range now reads as "still building" rather than downgrading to a calmer
# breathing look.
BUILD_RAMP_BARS = 8.0   # the Bible's own typical build length
BUILD_LOW = 0.2         # Bible: "20->95%"
BUILD_HIGH = 0.95

# BREAK (`break_low_wash`; renamed from BREAKDOWN, Phase 2, naming match only
# -- same concept): "a break has no transients to chase, so only a slow
# envelope earns reactivity... wash 15-35%." A heavily low-pass-filtered
# version of real energy -- a slow "breathing" rise/fall, not beat-locked
# spikes. BREAK_BREATH_TAU_S itself isn't given an exact number by the doc; a
# few seconds is what "slow envelope"/"breathing" implies, NOT yet tuned by
# ear on real hardware.
BREAK_LOW = 0.15
BREAK_HIGH = 0.35
BREAK_BREATH_TAU_S = 3.0

# OUTRO (`outro_fade_wash`: "wash 40->15%") -- Bible-integration Phase 2:
# genuinely new, reached only by promoting out of BREAK after a recent
# DROP/PEAK (see core/metronome.py's `_derive_bucket`), never from a raw
# energy level. A slow, one-way bar-position-driven fade (same mechanism
# shape as BUILD's ramp, just descending) rather than a breathing envelope --
# "warm fade, decreasing intensity" is a one-time decay, not an ongoing
# oscillation.
OUTRO_FADE_BARS = 16.0  # matches outro_fade_wash's own min_hold
OUTRO_HIGH = 0.40
OUTRO_LOW = 0.15

# Crossfade across a BUCKET CHANGE itself (2026-09-07, first real --hardware
# --record capture, see [[livereact-project]] memory): each branch above
# computes its own brightness independently, so a bucket flip used to just
# overwrite `self.wash_level` outright -- a real capture showed jumps up to
# |0.67| in a single ~40ms tick (e.g. DROP's held 0.9 landing straight on
# BUILD's freshly-reset 0.2 the instant BUILD was re-entered). Combined with
# 26 bucket flips in one real ~4min song, that read exactly like Josh's live
# report: the wash bar "turning off and fluttering... doing some randomness."
# Fix: blend `wash_level` toward the new bucket's target over
# WASH_CROSSFADE_TAU_S instead of snapping, for every transition EXCEPT
# entering DROP -- LIGHTING_BIBLE.md's own peak_full_rig/drop_blackout_hit
# looks are explicit that "the cut IS the effect," so a drop landing should
# stay a hard, instant hit; every other seam (including LEAVING drop) gets
# smoothed. This is a blend on the SEAM only, not a permanent filter -- once
# WASH_TRANSITION_WINDOW_S has passed, control reverts to the bucket's own
# formula directly, so BUILD's ramp/HOLD's & BREAKDOWN's breathing behave
# exactly as before (and their own existing tests still hold) once settled.
WASH_CROSSFADE_TAU_S = 0.6
WASH_TRANSITION_WINDOW_S = 3 * WASH_CROSSFADE_TAU_S  # ~95% converged by then


class _BurstGate:
    """Cap a single continuous strobe MOMENT at `max_burst_s`, then enforce
    `min_spacing_s` before the next one can start (LIGHTING_BIBLE.md §13) —
    CONFIRMED BUGGY then fixed 2026-09-08 (see STROBE_MIN_SPACING_S's own
    comment above for the full story): a moment ending EITHER way (hit the
    duration cap, or the source rate simply dropped to zero first) earns the
    same minimum spacing before a new one is allowed to start, so a sustained
    loud section can't chain moments back-to-back with only a token gap.

    Used by the ambient DROP/PEAK phrase-tail path only (2026-09-15 night:
    the roll accent rides the sustained-safe 4 Hz line and has no gate).

    Byte-quantized FIRST (2026-09-14, found while re-testing after
    _strobe_rate's bucket-gated redesign): a genuinely nonzero-but-sub-half-
    byte float rounds to DMX byte 0 -- invisible on the real fixture -- but
    without this rounding it still reads as `rate > 0.0` below and silently
    starts consuming the burst-duration budget, eventually forcing the
    mandatory gap during a stretch that never visibly strobed at all.
    Comparing the byte-quantized value instead means only an actually-
    visible rate ever starts or extends a moment."""

    def __init__(self, clock: Clock, max_burst_s: float, min_spacing_s: float) -> None:
        self.clock = clock
        self.max_burst_s = max_burst_s
        self.min_spacing_s = min_spacing_s
        self._burst_start: float | None = None  # when the current continuous burst began
        self._gap_until: float = 0.0             # can't strobe again until this time

    def apply(self, rate: float) -> float:
        rate = round(rate * 255) / 255.0
        now = self.clock.monotonic()
        if now < self._gap_until:
            return 0.0  # serving the minimum spacing before a new moment can start
        if rate <= 0.0:
            if self._burst_start is not None:
                # the moment just ended naturally -- this earns the same
                # cooldown as hitting the duration cap (both are "the moment
                # is over," just two different reasons for it ending)
                self._burst_start = None
                self._gap_until = now + self.min_spacing_s
            return 0.0
        if self._burst_start is None:
            self._burst_start = now
        if now - self._burst_start >= self.max_burst_s:
            self._gap_until = now + self.min_spacing_s
            self._burst_start = None
            return 0.0
        return rate


class _StrobeClock:
    """The one flash clock behind every automated strobe (see the
    STROBE_TOGGLE_MAX_HZ comment above). `advance(rate01)` returns True during
    the ON half of the current flash period. Phase is ACCUMULATED (phase +=
    hz * dt), never derived from wall time, so a rate that ramps every tick
    still produces evenly spaced flashes; a moment that starts from silence
    (rate 0 -> >0) restarts at phase 0 so its first frame is ON."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._phase = 0.0
        self._last_t: float | None = None
        self._was_on = False  # rate > 0 on the previous call

    @staticmethod
    def hz_for(rate01: float) -> float:
        return (min(rate01, STROBE_MAX) / STROBE_MAX) * STROBE_TOGGLE_MAX_HZ

    def advance(self, rate01: float) -> bool:
        now = self.clock.monotonic()
        dt = (now - self._last_t) if self._last_t is not None else 0.0
        self._last_t = now
        if rate01 <= 0.0:
            self._was_on = False
            self._phase = 0.0
            return False
        if not self._was_on:
            self._phase = 0.0  # fresh moment: lead with a flash
            self._was_on = True
        else:
            self._phase = (self._phase + self.hz_for(rate01) * dt) % 1.0
        return self._phase < 0.5


class ReactiveLayerEngine:
    """Always-on beat/energy/color reactivity."""

    def __init__(self, clock: Clock, manual: ManualParams | None = None, roll_sink=None) -> None:
        self.clock = clock
        self._ambient_gate = _BurstGate(clock, STROBE_MAX_BURST_S, STROBE_MIN_SPACING_S)
        self.strobe_level = 0.0     # exposed for the recorder: last post-gate 0..STROBE_MAX rate
        self._strobe_clock = _StrobeClock(clock)  # ONE clock for par + wash bar, see STROBE_TOGGLE_MAX_HZ
        self.strobe_on = False      # exposed for the recorder/visualizer: this tick's flash phase
        self.strobe_flash_dmx = STROBE_FLASH_DMX  # this tick's ON-phase byte (BUILD ramps it, see _build_strobe_rate)
        self._drop_entry_bar: int | None = None  # bar the current DROP landed on; phrase origin for _strobe_rate
        self._prev_bucket: EnergyBucket | None = None    # to detect a bucket change
        self._build_entry_pos: float | None = None       # musical position (bars) BUILD was entered at
        self._break_breath = 0.0        # slow low-pass envelope, BREAK's "breathing"
        self._outro_entry_pos: float | None = None  # bar position OUTRO was entered at, for its fade
        self._last_bucket_tick_t: float | None = None    # dt for the breathing envelopes above
        self.wash_level = 0.0       # exposed for the console/tests/recorder: the 0..1 wash/par level SENT this tick
        self._bucket_wash = 0.0     # the bucket model's own level (kept warm under Synth Sync, see tick)
        self._wash_transition_until = 0.0  # see WASH_CROSSFADE_TAU_S above -- 0.0 means "no
                                            # active blend," which also correctly makes the
                                            # very first tick ever a direct assignment (any
                                            # real monotonic clock reads > 0.0)
        # build_snare_accent: the roll-rise detector (core/snare_trigger.py),
        # owned here so the accent shares the one strobe clock/output.
        self._roll = RollRiseDetector(clock)
        self._roll_sink = roll_sink  # called with the roll state every tick (app.py: Metronome.note_roll)
        # The breath (Josh, run 11, 2026-09-17): "the snare roll ends the last
        # bar... and the strobing is still going until the end of the bar. If
        # we can make it so the strobing stops exactly when those snares
        # stop, it has that moment of silence right before the big hit." So
        # once a roll has ended while the drop is armed, the pre-drop strobe
        # holds OFF until the drop cuts (or the arming lapses, or a new roll
        # starts). Recorded: the roll ended 2.0-2.8 s before each hit and the
        # rise strobe filled that gap.
        self._breath = False
        self._prev_roll = False
        self.snare_roll_active = False  # exposed for the console/tests/recorder
        self.roll_ratio = 0.0            # exposed for the recorder: the detector's live ratio
        self.roll_path = ""              # which path(s) cleared: level / density / both (recorder)
        self.roll_rate_base = 0.0        # the onset-rate baseline the density path judged against
        # Synth Sync (see SYNTH_* above). `manual` is optional so every
        # caller/test that constructs a bare engine keeps the feature off.
        self.manual = manual
        self._synth_env = BandEnvelope(clock, gain=SYNTH_GAIN, normalize_tau_s=NORMALIZE_TAU_S)  # self-normalized, see envelope.py
        self.synth_sync_active = False  # exposed for the console/recorder: on AND audio fresh this tick
        self.synth_level = 0.0          # exposed for the recorder: the mid-band envelope, 0..1

    # LIGHTING_BIBLE.md §2.3's Titan tiers (Bible-integration Phase 4):
    # Safety=255 (not a contribution -- merge/merge.py's finalize() clamp,
    # unchanged), Transition=100, Phrase=50, Reactive=25.
    priority = 25

    @property
    def name(self) -> str:
        return "reactive"

    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        c = Contribution()
        r, g, b = scene_color(snap)
        # Both of these run EVERY tick, even under Synth Sync, so their state
        # (build ramp origin, strobe gates, drop phrase origin) stays warm and
        # switching the mode off mid-set is seamless.
        wash_level = self._bucket_wash_level(snap)  # also records DROP entry (phrase origin for strobe)
        self.strobe_flash_dmx = STROBE_FLASH_DMX  # _build_strobe_rate lowers it early in a build
        strobe = self._snare_accent_strobe_rate(snap)
        self.synth_level = self._synth_env.update(snap.audio_band_mid_abs, snap.audio_fresh)
        self.synth_sync_active = bool(self.manual is not None and self.manual.synth_sync and snap.audio_fresh)
        if (not self.synth_sync_active and snap.audio_fresh
                and EnergyBucket(snap.bucket) in (EnergyBucket.BREAK, EnergyBucket.OUTRO)):
            wash_level = min(1.0, wash_level + BREAK_SYNTH_DEPTH * self.synth_level)  # see BREAK_SYNTH_DEPTH
        if self.synth_sync_active:
            # The synth owns the wash LEVEL: envelope-driven brightness. The
            # automated strobes (build ramp, roll accent, drop-tail burst) are
            # NOT suppressed any more -- a strobe overrides the synth level
            # while it runs, then the wash hands straight back (Josh,
            # 2026-09-19). No real audio -> the normal bucket level above,
            # never dark.
            wash_level = SYNTH_FLOOR + (SYNTH_CEILING - SYNTH_FLOOR) * self.synth_level
        self.wash_level = wash_level
        self.strobe_level = strobe

        # --- washes: bucket-driven brightness (LIGHTING_BIBLE.md §5/§9.1), palette color ---
        # One flash clock for every wash fixture (see STROBE_TOGGLE_MAX_HZ):
        # while a strobe is active, par AND wash bar toggle their real dimmer
        # channel together -- ON at exactly STROBE_FLASH_DMX, OFF at 0 -- on
        # the same DMX frame. The wash bar's native ch2 is never written here.
        on_phase = self._strobe_clock.advance(strobe)
        self.strobe_on = strobe > 0 and on_phase
        for fx in rig.by_role("wash"):
            if isinstance(fx, (Par, WashBar)):
                # VERIFIED ON HARDWARE: Par has a real dimmer channel, same as
                # WashBar (not "baked into color" as previously assumed — that
                # left the dimmer at 0 and the real fixture never lit).
                fx.set_color(c, r, g, b)
                if strobe > 0:
                    fx.set_intensity(c, 0.0)
                    if on_phase:
                        # Written as a final BYTE, not through set_intensity's
                        # gamma+floor curve (fixtures/profiles.py's
                        # _intensity_to8): the cap is a byte Josh chose by eye
                        # on the real fixture, and the same byte on both
                        # fixtures is the point.
                        c.set(fx.offset + 1, self.strobe_flash_dmx)  # ch1 dimmer, 1-based
                else:
                    fx.set_intensity(c, wash_level)

        # --- moving heads: steady-ish dimmer, mirrored BPM-locked sweep ---
        position = self._musical_position(snap)
        sr, sg, sb = spot_color(snap)
        for fx in rig.by_role("spot"):
            fx.set_intensity(c, snap.energy)
            fx.set_color(c, sr, sg, sb)
            mirror = 1.0 if fx.side == "left" else -1.0
            pan = 0.5 + 0.4 * math.sin(position * 0.4) * mirror
            tilt = 0.45 + 0.15 * math.sin(position * 0.2)
            fx.set_position(c, pan, tilt)

        return c

    # ------------------------------------------------------------------ helpers
    def _snare_accent_strobe_rate(self, snap: ShowSnapshot) -> float:
        """The single strobe value for this tick: the ambient path (BUILD's
        sustained ramp, or the DROP/PEAK phrase-tail burst through its gate)
        max()'d with build_snare_accent's roll accent (STROBE_ROLL_FLASH_DMX,
        see that constant). One strobe clock: the roll overrides the rate."""
        self.snare_roll_active = self._roll.update(snap)
        if self._roll_sink is not None:
            self._roll_sink(self.snare_roll_active)  # core/metronome.py note_roll: a roll arms the drop
        self.roll_ratio = self._roll.ratio
        self.roll_path = self._roll.path
        self.roll_rate_base = self._roll.rate_base
        roll_rate = STROBE_MAX if self.snare_roll_active else 0.0  # ends with the roll or on the commit
        if self._prev_roll and not self.snare_roll_active and snap.drop_armed:
            self._breath = True   # the roll just ended: silence until the hit
        if self.snare_roll_active or not snap.drop_armed:
            self._breath = False  # a new roll, the drop landed, or the arming lapsed
        self._prev_roll = self.snare_roll_active
        ambient_rate = self._strobe_rate(snap)  # BUILD: sets strobe_flash_dmx to the ramp's byte...
        if roll_rate > 0:
            self.strobe_flash_dmx = STROBE_ROLL_FLASH_DMX  # ...which the roll accent overrides
        if EnergyBucket(snap.bucket) not in (EnergyBucket.DROP, EnergyBucket.PEAK):
            # The pre-drop strobe (armed + rising, whatever bucket the
            # classifier thinks) is a SUSTAINED slow-to-medium rate, not a
            # burst (see BUILD_STROBE_START) -- it bypasses the burst gate,
            # which still sees a 0 so any DROP-side moment it was tracking
            # closes and earns its spacing normally.
            self._ambient_gate.apply(0.0)
            ambient = ambient_rate
        else:
            ambient = self._ambient_gate.apply(ambient_rate)
        return max(ambient, roll_rate)

    def _strobe_rate(self, snap: ShowSnapshot) -> float:
        """Ambient strobe rate, DROP/PEAK only, and only during the last
        STROBE_PHRASE_TAIL_BEATS of every STROBE_PHRASE_BARS-bar phrase
        counted from the bar the drop landed on (see STROBE_PHRASE_BARS'
        comment above for the capture that motivated this -- the previous
        held-constant rate turned the burst gate into a 9.5s clock).

        The rate itself is a constant STROBE_MAX (since 2026-09-15 evening
        -- see the return below; before that it was rescaled off the held
        drop_hit_intensity) -- never raw per-tick energy_norm (confirmed
        live 2026-09-14 to read as random flicker). BUILD is a separate,
        sustained, accelerating strobe -- see _build_strobe_rate.
        Bucket transitions/PEAK-escalation logic lives in core/metronome.py
        -- see the separate open item (2026-09-14, Josh) about a sustained
        drop with no quiet section getting stuck; not addressed here."""
        bucket = EnergyBucket(snap.bucket)
        if bucket not in (EnergyBucket.DROP, EnergyBucket.PEAK):
            return self._build_strobe_rate(snap)  # armed + rising, in whatever bucket the classifier thinks
        # Phrase origin: the metronome's own drop_start_bar (the bar the
        # rise was FIRST seen in -- see ShowSnapshot.drop_start_bar) when it
        # has one; the bar this engine saw the bucket flip in otherwise.
        # Found live (2026-09-15): the commit bar was a full bar after the
        # hit, so counting from it put the strobe on the wrong bar.
        origin = snap.drop_start_bar if snap.drop_start_bar is not None else self._drop_entry_bar
        if origin is None:
            return 0.0  # engine started mid-drop with no known downbeat to count from
        bars_in = snap.bar - origin
        phrase_bars, bar_of_phrase, beats = STROBE_STYLES.get(snap.planned_strobe_style, STROBE_STYLES["tail"])
        if not phrase_bars or bars_in < bar_of_phrase or (bar_of_phrase == 0 and bars_in < phrase_bars):
            return 0.0   # no strobe in this song's drops (song_style.py), or the drop's own first bar
        if not ((bars_in % phrase_bars) == bar_of_phrase and snap.beat_in_bar in beats):
            return 0.0
        # Always the ceiling (2026-09-15, session-20260915-140630): scaling
        # the tail burst by drop_hit_intensity gave the track's second drop
        # a 2.4 Hz tail against the first's 4 Hz -- for a 0.8s accent that
        # only read as "weaker," not as "quieter drop." One burst shape.
        return STROBE_MAX

    def _build_strobe_rate(self, snap: ShowSnapshot) -> float:
        """The pre-drop strobe: only while the drop is armed AND the rise is
        on (see BUILD_STROBE_START's comment), at a rate from the rise's
        intensity -- BUILD_STROBE_START at rise_index RISE_ON, the ceiling
        at RISE_FULL. Not burst-gated: a sustained rate under the 4 Hz line."""
        # Planned count-in (rekordbox/planner.py "builds count in"): with a drop planned
        # within BUILD_COUNTIN_BEATS the rate follows the countdown even when the audio
        # rise detector is quiet -- the plan knows the downbeat, the ear-rule is the same.
        if self._breath:
            return 0.0   # the roll just ended: silence until the hit, whatever armed it
        countdown = 0.0
        if (snap.plan_active and snap.planned_strobe_allowed and snap.drop_armed
                and 0 < snap.beats_to_drop <= BUILD_COUNTIN_BEATS):
            countdown = 1.0 - (snap.beats_to_drop - 1) / float(BUILD_COUNTIN_BEATS)
        if countdown <= 0.0 and not (snap.drop_armed and snap.rising):
            return 0.0
        p = 0.0
        if snap.rising:
            p = max(0.0, min(1.0, (snap.rise_index - RISE_ON) / (RISE_FULL - RISE_ON)))
        p = max(p, countdown)
        rate01 = BUILD_STROBE_START + (1.0 - BUILD_STROBE_START) * p
        self.strobe_flash_dmx = round(STROBE_FLASH_START_DMX + (STROBE_FLASH_DMX - STROBE_FLASH_START_DMX) * p)
        return STROBE_MAX * rate01

    def _musical_position(self, snap: ShowSnapshot) -> float:
        """Continuous position in beats, for smooth tempo-locked movement."""
        return snap.bar * 4 + (snap.beat_in_bar - 1) + snap.beat_phase

    def _bucket_wash_level(self, snap: ShowSnapshot) -> float:
        """Wash/par brightness per LIGHTING_BIBLE.md's per-energy-state model
        (see the module-level constants above for exactly what's Bible-
        sourced vs. this project's own extrapolation for PEAK/OUTRO's exact
        numbers)."""
        now = self.clock.monotonic()
        dt = (now - self._last_bucket_tick_t) if self._last_bucket_tick_t is not None else 1 / 60
        self._last_bucket_tick_t = now

        # snap.bucket -- an ALREADY-DEBOUNCED, already-escalated (PEAK/OUTRO
        # included) bucket, computed once in metronome.py, NOT a fresh call
        # here. energy_norm itself (vs. raw snap.energy) matters too --
        # bucket thresholds are meaningless against a fixed absolute number
        # tuned to one mixer gain staging (found live 2026-09-07: a real drop
        # never crossed the old raw-energy DROP threshold at all). See
        # show_state.py's bucket/energy_norm docstrings.
        energy_bucket = EnergyBucket(snap.bucket)
        # BARS, not beats -- `_musical_position` (used by MH movement) is
        # beat-denominated; a "BUILD_RAMP_BARS" ramp needs the bar-denominated
        # helper instead, or it completes 4x too fast (found via testing).
        pos = musical_bar_position(snap)
        # Unguarded (fires on the very first tick too, prev_bucket=None ->
        # always "entering" -- matches this ramp's pre-existing behavior of
        # always starting a fresh BUILD from a real entry point, not just on
        # a later re-entry).
        entering_new_bucket = energy_bucket != self._prev_bucket
        if entering_new_bucket and energy_bucket == EnergyBucket.BUILD:
            self._build_entry_pos = pos
        if entering_new_bucket and energy_bucket == EnergyBucket.OUTRO:
            self._outro_entry_pos = pos
        # DROP entry marks the phrase origin _strobe_rate counts from. PEAK
        # is an overlay on the SAME drop (core/metronome.py), so DROP<->PEAK
        # moves in either direction keep the origin (found by replaying the
        # real capture: resetting on PEAK->DROP skipped a whole phrase's
        # strobe); only arriving from outside the drop sets a fresh one.
        if entering_new_bucket and energy_bucket in (EnergyBucket.DROP, EnergyBucket.PEAK) \
                and self._prev_bucket not in (EnergyBucket.DROP, EnergyBucket.PEAK):
            self._drop_entry_bar = snap.bar
        # Crossfade-arming is guarded on `self._prev_bucket is not None`,
        # UNLIKE the build-entry reset above: the engine's very first tick
        # ever has no meaningful prior state to blend FROM, and must land
        # exactly on its bucket's target (existing tests rely on this) --
        # which `now >= self._wash_transition_until` already gives it for
        # free (0.0 initial value) as long as this stays unarmed that tick.
        # DROP and PEAK both stay instant snaps (not just DROP) -- PEAK is an
        # escalation of an already-instant DROP hit, not a fresh transition
        # that should ease in.
        if self._prev_bucket is not None and entering_new_bucket and energy_bucket not in (
            EnergyBucket.DROP, EnergyBucket.PEAK,
        ):
            # (Re-)arm the crossfade window -- see WASH_CROSSFADE_TAU_S above.
            self._wash_transition_until = now + WASH_TRANSITION_WINDOW_S
        self._prev_bucket = energy_bucket

        if energy_bucket == EnergyBucket.DROP:
            held = _drop_held_level(snap.drop_hit_intensity)  # how loud THIS hit was, captured once
            target = _beat4_flash(snap, held)  # Josh's beat-4 accent, see BEAT4_FLASH_PEAK above
        elif energy_bucket == EnergyBucket.PEAK:
            held = min(1.0, _drop_held_level(snap.drop_hit_intensity) + PEAK_BOOST)
            target = _beat4_flash(snap, held)  # same beat-4 accent
        elif energy_bucket == EnergyBucket.BUILD:
            entry = self._build_entry_pos if self._build_entry_pos is not None else pos
            progress = max(0.0, min(1.0, (pos - entry) / BUILD_RAMP_BARS))
            target = BUILD_LOW + (BUILD_HIGH - BUILD_LOW) * progress
        elif energy_bucket == EnergyBucket.OUTRO:
            entry = self._outro_entry_pos if self._outro_entry_pos is not None else pos
            progress = max(0.0, min(1.0, (pos - entry) / OUTRO_FADE_BARS))
            target = OUTRO_HIGH + (OUTRO_LOW - OUTRO_HIGH) * progress  # descending
        else:  # BREAK
            # Normalize energy WITHIN this bucket's own range (0..BREAK_MAX)
            # before using it as the breathing target -- using the raw value
            # directly (found via testing) can never actually reach
            # BREAK_HIGH, since it's capped below BREAK_MAX by definition of
            # being in this bucket at all. energy_norm, not raw snap.energy --
            # same reasoning as the bucket boundary above, this threshold
            # means nothing against a fixed absolute number.
            breath_target = min(1.0, snap.energy_norm / BREAK_MAX)
            coeff = 1 - math.exp(-dt / BREAK_BREATH_TAU_S)
            self._break_breath += coeff * (breath_target - self._break_breath)
            target = BREAK_LOW + (BREAK_HIGH - BREAK_LOW) * self._break_breath

        if energy_bucket in (EnergyBucket.DROP, EnergyBucket.PEAK) or now >= self._wash_transition_until:
            self._bucket_wash = target  # settled, or a deliberately-instant DROP/PEAK cut
        else:
            crossfade_coeff = 1 - math.exp(-dt / WASH_CROSSFADE_TAU_S)
            self._bucket_wash += crossfade_coeff * (target - self._bucket_wash)
        return self._bucket_wash
