# Handoff: engine work, driven by the scorer

**For:** a Claude Code session in `~/LiveReact` on branch `harness` (Mac). Josh reviews every change.
The same work runs unattended on the desktop each night via `docs/AGENT_BRIEF.md` + `tools/windows/nightly_agent.ps1`;
`python tools/agent/status.py` shows where the families stand, `tools/agent/dump_signals.py` is the study table,
`tools/agent/gate_head.py` is the acceptance test.
**Goal:** make the engine pass the rules it currently fails, one rule family at a time, with the
scorer as the acceptance test. Scorer first, code second: the failing verdicts ARE the spec.

## Read first
- `CLAUDE.md`, then `spec/LIGHTING_BIBLE.md` for any rule you touch (the lighting/merge/DMX paths
  demand it), `spec/PENDING_RULES.md` (Josh's new vocabulary: roll / cut / groove, fills, whiteout,
  clap-driven and atmospheric drops), `docs/HANDOFF_scorer_v2.md` (what the scorer measures).
- `src/livereact/sim/score.py`: each rule's docstring says exactly what is measured. Make the engine
  satisfy the measurement; **never relax a rule to make a track pass** (Josh owns the rules).
- `params.json` + `src/livereact/params.py`: engine constants are knobs. If a change needs a new
  constant, add it to `params.json` with a tag (`tunable` with a range, or `safety-frozen` if it
  touches strobe/laser). Never change a `safety-frozen` or `ear-tuned` value.

## How to work
```
.venv313/bin/python tools/loop/baseline.py                  # before: results/baseline/evaluation.json (committed)
.venv313/bin/pytest -q                                       # must stay green (3 known fails in test_spec_conformance_gaps.py)
.venv313/bin/python tools/harness/run_batch.py --out results/engine && .venv313/bin/python tools/harness/score.py results/engine
```
Use `livereact.loop.evaluate.evaluate(None)` + `livereact.loop.gate.compare(baseline, candidate)` to
check a change the way the nightly loop will: tuning score up, hold-out not worse, **no rule regressing
on any track**, quality metrics not collapsing. One PR-sized commit per rule family, message listing
which tracks flipped FAIL->PASS and anything that got worse. Regenerate the baseline only when a change
is accepted (commit it with `git add -f results/baseline/evaluation.json`).

Ten tracks have ear-verified markers (`tracks/*.timeline.json` with no `est:` notes); reference tracks
per behaviour: Thriller BVRNOUT (whiteouts, rolls, grooves, weak roll), SWEETER (clap-driven), messy
(escalating long drop), Beautiful Now (fading build, power-off), One More Time (fills), Somebody To
Love (three rolls), Sweet Nothing (quiet roll, still breakdown), Talk To You (atmospheric drop).

## Work order (FAIL counts across the 10 labeled tracks, scorer v2 baseline)

1. **Drop detection — STRUCT-03 (8/10 fail) and STRUCT-01 (2).** **CLOSED 2026-10-22 by Josh (commit `c4e2159`: roll-start detector landed; the remaining cases need a rule change or Josh, not more engine work). The counts in this list are historical — `python tools/agent/status.py` has the live ones.** Everything else depends on the engine
   knowing where the drop is. STRUCT-03: the engine should enter DROP/PEAK within 2 beats of the labeled
   roll start; today it is "never" on most tracks and +11 beats on Latch. Study the dip/rebound and
   roll-arm logic in `core/metronome.py` against the labeled rolls (snare-band rise, kick often ABSENT
   during the roll — see SWEETER, Somebody To Love). STRUCT-01: fills must not read as drops; Josh's
   cue is "kicks and claps present but it's just the track starting / a tool section". Prefer a new
   signal (e.g. snare/clap-band roll detector, sustained-energy-without-dip) over threshold fiddling
   the search could do itself. Keep new thresholds as tunable knobs.
2. **Dwell and transitions — TIME-11 (10), TIME-02 (9), TIME-13 (5).** The engine dwells in seconds
   (3 s); the bible dwells in bars per state (BREAK 8, BUILD 4, DROP 8, PEAK 8, OUTRO 16) and holds a
   look 8 bars (16 in BREAK/OUTRO). Also OUTRO is exited without a track transition on Latch/Beautiful
   Now — find what resets it. Beware: longer dwell will slow STRUCT-03 unless entry is fast and exit is
   slow (TIME-12 asymmetry).
3. **Three missing behaviours — TIME-04 (9), TIME-05 (8), TIME-06 (7).** Pre-drop blackout snapping to
   full on the downbeat (needs step 1 to know the downbeat is coming: use the armed-roll state), the
   build dimmer ramp 20%->95% with dark-fraction 30%->0%, and breakdown stripping to <=2 fixtures with
   one static hue and no motion.
4. **Josh's vocabulary — STRUCT-05 power-off (7), STRUCT-07 groove (5), STRUCT-04 whiteout (4),
   STRUCT-02 escalation (3), STRUCT-08 weak roll (3), STRUCT-06 fading build (2), STRUCT-09 clap-driven
   (1).** These need new scene/engine behaviour: a `whiteout` look (wash to white, strobe off, heads to
   the DJ pose when `dj_pan_tilt` exists), a `groove` state after the roll resolves (wash bar follows the
   mid/high band, heads at full slew, laser 2-4 beat bursts every other bar), strobe escalation across a
   roll scaled to the roll's snare energy, the stepped shutter fade on a non-drop exit from an intense
   section, and the white slow-dim for a fading build. Detecting "cut" (drums gone, synths loud) is the
   shared primitive for whiteout and power-off.

Stop after each family and report: what changed, which tracks flipped, what the gate said, and what you
could not fix without a rule change or Josh's input (then ask, don't guess). Don't touch `tracks/`,
`spec/conformance.md`, or the Windows/loop tooling; don't start a search on this Mac.
