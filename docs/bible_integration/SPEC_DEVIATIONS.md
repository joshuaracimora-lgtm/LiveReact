# Bible-integration spec deviations

**Status: living document.** Distinct from `spec/03-lighting-bible-corrections.md`
(that file records corrections found by the original evidence-verification
pass against the source PDFs — factual errors in the Bible's own text).
This file records places where the Bible's text is accurate and not in
dispute, but this specific hardware/software stack cannot implement it as
literally written — a deliberate, disclosed substitution, not a bug or a
someday-TODO. Each entry: what the Bible asks for, why it can't be built as
written, what LiveReact does instead, and what would change the answer.

---

## D1 — §9.6 predictive scheduling: no phrase-boundary data exists on this hardware

**What the Bible asks for.** §9.6's "predictive scheduling model": Pro DJ
Link supplies exact bar/beat/phrase position; when PDL indicates the current
phrase ends (a drop) at bar N, pre-arm the cue to fire ON that predicted
downbeat, with audio only used afterward as confirmation. The stated intent:
audio-only detection is structurally late (a drop is only classifiable after
it begins), so scheduling ahead of time is how the light is never behind.

**Why it can't be built as written.** Confirmed directly against this
project's own code and Pro DJ Link usage (not assumed): the data LiveReact
actually receives from the XDJ-XZ over Pro DJ Link is tempo, current
beat-phase, beat-in-bar, bar count (a locally-reconstructed counter, not the
track's real position — see `core/show_state.py`'s own docstring), transport
state, and which deck is on-air/master. There is no phrase-boundary field,
no song-section label, no "a drop happens at bar N" signal anywhere in what
this hardware transmits. §9.6's central mechanism has no data to schedule
against — this is a hardware/protocol limitation, not an unbuilt feature.

**What LiveReact does instead (Bible-integration Phase 3, approved by Josh
2026-09-12, "acceptable for now").** Beat-quantized reactive firing: once
the classifier confirms a drop (still purely reactive, off real audio, same
mechanism as before), the commitment is held for the fraction of a beat it
takes to land on the next real downbeat, rather than firing mid-bar wherever
the confirming tick happened to fall. This satisfies the achievable half of
§9.6's intent — cues land on a clean beat boundary, never mid-bar — without
fabricating a phrase-prediction signal that doesn't exist. It does NOT
provide genuine foresight: the light still only finds out about a drop the
same moment it always did.

**Partial mitigation, tracked separately, not yet built.** A plain heuristic
(not the prediction this section describes, and not machine learning) using
signals already computed — how long the classifier has dwelled in DROP/PEAK,
and the same-track `drop_cycle` count — as soft leading indicators that a
transition is statistically likely soon. Scoped into Phase 3 as a
`transition_likely` signal, verified via simulation before use.

**What would change this answer.** A different input source that actually
carries song-structure metadata (e.g. if a future DJ platform exposes phrase
or cue-point data over its own protocol), or a deliberately separate,
explicitly-scoped R&D effort to train a real online structure-recognition
model — discussed with Josh 2026-09-12 and intentionally NOT started: it's a
genuinely different, larger initiative (real-time-from-nothing music
structure segmentation is an open problem in general, not just for this
project), requiring either a labeled dataset or self-labeling off this
project's own imperfect classifier, a model cheap enough to run live, and
real validation against tracks it wasn't trained on. Revisit only if the
heuristic version above hits an evidenced ceiling worth that investment.

**Untracked gap, found 2026-09-13 (full Phase 1-6 re-audit)**: §9.7's
"Mis-prediction recovery" text ("build that doesn't resolve: hold the
pre-drop state for max 1 bar past prediction... resume `build_riser_wash`
at current intensity and re-predict"; "unexpected cut: audio RMS collapse
sustained ≥1 bar → fall to `break_low_wash` via a 1-bar crossfade") has
**no corresponding rule ID anywhere in `spec/conformance.md`'s 170-row
taxonomy**, and isn't built. Distinct from `FAIL-03` (audio signal *loss*,
a hardware/connection event) — this is about the track *staying connected
but suddenly going quiet or not dropping when expected*, a musical event,
not a signal fault. Given D1 above, this section's literal "re-predict"
framing doesn't apply either (there's no prediction to have gotten wrong)
— but the underlying behavioral intent (don't hang in a bad state when a
build overruns, gracefully recover from a real unexpected quiet passage)
is still a real, currently-unaddressed gap, independent of D1's own
predictive-scheduling substitution. Not scheduled into any phase; flagged
here rather than left invisible.

---

**Partial predictive cue, 2026-09-17 (`core/build_rule.py`).** One
audio-only cue now exists inside D1's limits: after a drop has set the
track's kick level, four bars of the kick back at 30-65 % of it while the
loudness classifier still reads BREAK raises the same held BUILD the C
key does. It is not phrase prediction (no bar-count anticipation, no
downbeat knowledge) and it cannot see a set's first build; it shipped
because it fires on zero non-builds across every real capture (scored
with `tools/simulations/replay_state.py --no-cue`), per Josh's rule that a
false build start is the worse failure. The C key remains primary.

## D2 — §14 "manual = biases and caps, never an override": Synth Sync is an operator-chosen reactivity mode

**What the Bible asks for.** The console's manual controls are biases and
caps on a fully automated show (master brightness, strobe cap, color bias,
laser arm, blackout/freeze). The operator never picks *what reacts to
what*; the engine does, from the energy-state model.

**Why it's deviated from.** Josh (2026-09-15): in UK garage the prominent
sound is the synth/chord stab, not the kick, and the bucket model's
wash behavior (held level in a drop, ramp in a build) reads flat against
that music. He asked for a UI toggle that makes the par + wash bar "literally
mimic the synths." No automated classifier can tell "this is a stab-driven
track" from the audio LiveReact has (see D1: no song-structure data), so
the human who knows the track makes the call -- the same reasoning as the
planned DJ drop cue (`Metronome.request_manual_bucket`).

**What LiveReact does instead.** `ManualParams.synth_sync` (console
checkbox / `S` key, never persisted, off at every start). While on and real
audio is fresh: the wash dimmer follows a baseline-excess envelope on the
mid band (`engines/envelope.py`, `engines/reactive.py`'s `SYNTH_*`), and
every automated strobe is suppressed so two signals never fight one dimmer
channel. Par + wash bar only; the moving head, color, movement and laser
are untouched. Known limit: band amplitude can't separate instruments --
vocal chops and snare body share the mids.

**What would change the answer.** A stab/chord detector that could identify
the instrument (not just the band) could gate this automatically; nothing
like that exists here today.

## D3 — §9.8 legal transition matrix: DROP → BUILD is allowed as a confirmed exit

**What the Bible asks for.** The transition matrix marks DROP → BUILD ✗:
a drop either keeps going, escalates to PEAK, or resolves through BREAK
first; it never downgrades to "still building."

**Why it's deviated from.** The matrix assumes a breakdown reads as BREAK.
On the tracks this runs on, the section after a drop is often a pad- or
vocal-heavy breakdown that sits at `energy_norm` 0.2-0.5 -- BUILD range,
never under the 0.15 BREAK exit -- so the strict matrix held DROP through
the whole breakdown AND the build that followed: no build ramp, no build
strobe, and the second drop committed with no lead (both the RMS lead and
the hit lead are gated on a committed BUILD). Josh (2026-09-17) chose to
allow the edge rather than route it through a fake BREAK look.

**What LiveReact does instead.** `scenes/catalog.py`'s `LEGAL_TRANSITIONS`
declares DROP → BUILD, and `core/metronome.py`'s `_derive_bucket` gives it
exactly the treatment DROP → BREAK got on 2026-09-16: no dwell-floor wait,
but a two-bar `CUT_EXIT_CONFIRM_S` confirmation of the smoothed level
(already under the 0.65 down-hysteresis) before it commits. A between-kick
dip or a one-bar fill returns to DROP range and clears the candidate. So
does every gap where the instantaneous level falls under the break line
(the same lead that lets a cut exit early flips the reading to BREAK for
that tick and restarts the BUILD clock): only a section whose sound stays
continuously above the break line for two bars -- pads or a vocal with the
kick gone -- becomes a build, while the thinning tail of a drop (sparse
kicks over a bed, `session-20260916-114402` 355-372 s) keeps flickering
and holds DROP. Pooling the two exits into one clock was tried on that
capture and turned the tail into a 12 s BUILD with a strobe ramp -- a
false build, the worse failure. On the eight captures replayed the change
alters no transition at all (their breakdowns all read BREAK); it is a
safety net for the pad-heavy case, not a retune. The Bible's intent -- a
drop can't flicker back to "building" on a dip -- is kept by the
confirmation, not by the table.

**What would change the answer.** Song-structure data (D1) that could say
"this is the same drop, still going" during a two-bar mid-energy passage;
until then two bars under the drop band is treated as a section change.

## D4 — §9.8 / TIME-11: OUTRO is not a terminal state and dwells 8 bars, not 16

**What the Bible asks for.** OUTRO has no legal exit (the set is ending)
and holds a 16-bar minimum dwell.

**Why it's deviated from.** Josh (2026-10-25): "Outro and break can look
similar, as long as being an outro doesn't stop another build-up within
that song. A DJ can transition during a break; it's just the quiet part of
it." LiveReact has no way to know a set is ending versus a breakdown
between two drops, and `core/metronome.py` has deliberately un-promoted
OUTRO on a real energy rise since Phase 2 (see the OUTRO_LOOKBACK_S
comment). The scorer still graded by the Bible, so every correct exit out
of a quiet part counted as a TIME-11 and TIME-13 failure (6 tracks on
2026-10-25).

**What LiveReact does instead.** `sim/score.py`: `MIN_HOLD_BARS["OUTRO"]`
is 8 (BREAK's), `LEGAL["OUTRO"]` is BREAK's exits plus BREAK itself (the
un-promotion). The look hold (TIME-02) stays 16 bars for both. No engine
change; OUTRO remains an overlay on BREAK that a build or drop clears.

**What would change the answer.** A DJ-cued "set ending" signal. Until
then an outro is a flavour of break.

## D5 — TIME-11 / TIME-02: dwell floors and look holds yield to a labeled section change

**What the Bible asks for.** Every state holds its minimum dwell and every
look its minimum hold, full stop.

**Why it's deviated from.** Josh (2026-10-25): "have the lights as reactive
to the music as possible -- less latency between the lights and the music
will always be approved." The floors exist to stop flapping on a dip, not
to hold a drop look through a DJ's cut. The two-bar cut exit (D3,
2026-09-16/17) already follows a real cut early; measured by the Bible's
floor alone that correct behaviour fails TIME-11 whenever the drop was
short. Likewise a look that hands over because a labeled breakdown is
shorter than 16 bars is a correct look, not a flap. And the opening run of
a track is where the engine stood at power-on, not a decision.

**What LiveReact does instead.** `sim/score.py`: a run (TIME-11) or a look
(TIME-02) that ends within `SECTION_CHANGE_TOL_BARS` (1 bar) of an
ear-labeled section edge -- any marker's start, or a drop's labeled end --
is not scored; only verified markers count (an `est:` guess never excuses
a short run). The first run of a track is exempt like the last already
was. A short run with no labeled edge near its end is still flapping and
still fails. The engine is unchanged: its floors still apply to dips; the
scorer now agrees that following the music is right.

**What would change the answer.** Nothing foreseen; this encodes the
project's standing priority (react first, hold only against noise).
