# Agent night 20261025 — agent/20261025

## Sub-goal
Family 2, rule **TIME-11** (each energy state holds its minimum dwell). I started from last night's
`results/agent/20261024-run1/attempt2_peak_dwell_tempo_bars.patch`: PEAK gets its own dwell, and dwell is
counted in real bars instead of seconds at 128 BPM. The aim was to clear the remaining short bucket runs
on at least one track. **Result: 1 commit, gate ACCEPT** (`79ea278`). One More Time TIME-11 goes FAIL→PASS.

## What the music does (evidence from dump_signals, with track names and times)
I applied the attempt-2 patch on HEAD (811a11b), with no commit yet, and re-measured. 37 TIME-11
violations were left, and they came from three mechanisms:

1. **PEAK → DROP → BREAK blips.** I listed every PEAK→DROP de-escalation on the 10 tracks
   (`study/pd.py`, per-tick energy_norm from the sim). All 9 ended in a BREAK, and 7 of them within 8 bars:
   messy 93.8 s (2.8 bars), Latch 253.1 s (0.7), Somebody To Love 45.1 s (1.5), Beautiful Now 164.9 s
   (1.2), Namerakana 111.6 s (4.8), One More Time 133.5 s (5.6), Thriller 203.7 s (7.3). In every case the
   energy was fading into the breakdown, not settling at a lower drop level. On One More Time, energy_norm
   falls from 0.77 at 120 s to 0.26–0.36 at 127–131 s (BREAK range is <0.30), then hovers at 0.3–0.65
   until the labeled breakdown at 140.85 s, where it reaches 0. I replayed the overlay offline over the
   recorded energy (`study/emu.py`, which reproduces the real run exactly). Holding PEAK on any BREAK-range
   reading, plus an 8-bar confirmation, removes all seven blips. A 4-bar confirmation makes Thriller worse
   (136.4 s: 11.6 → 6.5 bars).
2. **One More Time's auto build cue, 3.2 bars into the breakdown.** The kick stays at about 0.4× the
   drop's level through the whole 140.85 s breakdown (band_low 3–4 vs 8–11 in the drop), and the next drop
   is at 232.55 s. `core/build_rule.py` counted its four kick bars starting from the drop's own last bar
   (bar 73, which was still DROP until 144.34 s). So BUILD came in at 150.63 s, after 3.2 bars of BREAK.
3. **Somebody To Love 184.63 s ("snare roll 3: escalating strobe").** The auto cue from 177.28 s was
   released on the drop itself. Through the roll the kick climbs to 4–6, which is 0.65–1.0× the reference
   and therefore above the rule's band, and the rule counted that as "kick gone". The show fell back to
   BREAK at 184.65 s, on Josh's drop.

## What I changed (files, mechanism, new knobs with tags)
`src/livereact/core/metronome.py`, `src/livereact/core/build_rule.py`, `params.json`, `tests/test_metronome.py`, `tests/test_build_rule.py`.
- **Attempt-2 base, now committed.** PEAK keeps its own clock. DROP must be on show for its dwell before PEAK
  escalates. PEAK holds 8 bars, and PEAK→BUILD steps through DROP. Dwell is counted in bars at the live
  tempo (`_bar_s`/`_dwell_s`).
- **Fade vs. de-escalation.** Under PEAK, a reading below `catalog.BREAK_MAX` means a cut is under way. It
  holds PEAK and restarts the de-escalation count, so a breakdown goes PEAK→BREAK directly (legal).
  `BREAK_MAX` is read from the module at runtime, so a tuned value follows.
- **New knob `PEAK_EXIT_CONFIRM_BARS`:** `tunable`, default **8.0**, range [2.5, 8.0], module
  `livereact.core.metronome`. The default equals DROP's dwell: a lower section has to last as long as the
  DROP it would enter must hold.
- **Auto build cue.** If any of the rule's four kick bars was not wholly shown as BREAK, the streak began in
  the drop's tail. In that case the cue waits until the shown BREAK has held its 8-bar dwell. While it
  waits, OUTRO is not promoted; a drop, a DJ cue or a release cancels it. The cue's 24-bar hold still counts
  from the bar the rule fired. Four kick bars all inside the break (Somebody To Love 177 s) still cue at
  once. My first version deferred every cue, and it cost the gate (see below).
- **build_rule "kick gone" now means below the band only.** A kick above the band is the drop arriving.
- **Tests:** 6 new metronome tests, 4 new build-rule tests (both mechanisms mutation-checked: disabling
  either one makes its test fail). `test_metronome.py` reads dwell through `m._dwell_s(...)`, because the
  default 124 BPM is not 128. The manual-PEAK-cue test now expects PEAK to hold; it used to fall back to
  DROP on the next tick. Full suite: **676 passed, 4 skipped, 3 failed** (the known 3 in
  test_spec_conformance_gaps.py), both with plain `pytest -o addopts= -q` and with an explicit basetemp.
  The temp-dir PermissionError from 10/24 did not occur tonight.

## Gate result (compliance before -> after, critical fails, every track flip)
`results/agent/20261025/gate_1.json`: **ACCEPT** (baseline rewritten and committed in `79ea278`).
Re-checked with `--baseline-ref harness` (`gate_verify.json`): same result, ACCEPT.
- tuning compliance **0.3083 -> 0.3226**, critical fails 27 -> 26
- hold-out compliance **0.32 -> 0.32**, critical fails 11 -> 11
- flips: **One More Time TIME-11 FAIL -> PASS**. No PASS -> FAIL anywhere.
- violation totals over 10 tracks: TIME-11 94 -> 27; TIME-02 32 -> 32; TIME-13 6 -> 6
- quality: tuning drop_response_mean 0.007 -> **0.006** (floor 0.0056); distinct_looks, dynamic range and
  utilization unchanged; hold-out unchanged.
- Before this, one study gate (`study/gate_dry1.json`) was **REJECTed**: drop_response_mean fell
  0.007 -> 0.0. That version deferred every auto cue to the BREAK floor, which delayed Somebody To Love's
  real build by 2 bars, so its look crossfaded in right on the 184.63 s drop (response −0.10). The fix was
  to defer only when the kick streak began in the drop.

## What got worse or stayed broken, and why
- **drop_response_mean is now 0.006, just above its 0.0056 floor.** Somebody To Love 0.028 -> 0.013:
  at 184.63 s the rig now holds the build look into the roll. The old +0.072 came from Rising Tide
  crossfading out while the post-drop beat was being measured. Thriller −0.085 -> −0.075. The next change
  that moves any labeled drop's response down will hit the floor.
- **TIME-11 still fails on 9 tracks**, all in categories that wait on Josh:
  - OUTRO runs that exit illegally: SWEETER 68.03 s, Latch 162.4 s, Talk To You 91.32 s, messy 135.47 s,
    Sweet Nothing 103.14 s, Beautiful Now 79.78 s. These are the terminal-OUTRO question.
  - The power-on BREAK run: Thriller 4.5 bars, Somebody 2.0, Talk To You 2.1.
  - Short base BREAK/DROP runs from the 2-bar cut exit or a fast cut-in: Thriller 86.59 s / 158.45 s,
    Namerakana 73.17 / 120.56 / 130.01 s (7.6 bars), Sweet Nothing 14.84 / 188.76 / 195.3 s, Beautiful Now
    112.8 / 167.09 / 241.37 / 245.16 s, Somebody 62.62–122.07 s.
  - Somebody 166.25 s: 6.0 bars of BREAK before the real build. Holding it for 8 bars is what broke
    drop_response in the rejected version.
- TIME-02 and TIME-13 unchanged. Last night's TIME-02 commit (`ce1cc10` on agent/20261024-2) is not on
  harness, so it is not in this branch. The two branches touch different files (phrase.py vs
  metronome.py/build_rule.py), but both rewrite `results/baseline/evaluation.json`. Merging both means
  regenerating the baseline once.

## Could not fix without Josh (rule change needed, or a question) — one bullet each
- Family 2 has no TIME-11 work left that doesn't depend on Josh. All 27 remaining violations are OUTRO,
  power-on, or cut-exit cases (above). The open questions in state.json (terminal OUTRO, first-run
  exemption, 8-bar DROP floor vs your 2-bar cut exit) decide what happens next.
- Is it OK that PEAK now holds through a fade until the base's cut exit (≤2 bars of quiet under the PEAK
  look), instead of showing the DROP look for a few bars before the break?
- On One More Time the first auto build (now 159.99–195.6 s) is still a false build by your markers (it
  is inside the 140.85 s breakdown). The kick stays at ~0.4× drop level through that breakdown, so the
  kick-level rule can't tell it from a build. Should an auto build need some second signal, or is this
  the C key's job?

## Tomorrow (the sub-goal to continue, or the next family)
If Josh answers the OUTRO / first-run / cut-exit questions, continue family 2 with whichever is decided
(on this commit, SWEETER's and Latch's only TIME-11 violation is the false OUTRO, so the attempt-1
OUTRO latch should flip both, plus TIME-13 on 6 tracks. I have not gated that combination, and last
night's messy STRUCT-02 / drop_response regressions from the OUTRO latch would still block it). Otherwise move
to **family 3, TIME-04 (pre-drop blackout snapping to full on the downbeat)**, using the armed-roll state.
Also merge or close agent/20261024-2 (TIME-02) before more family-2 work, so the baselines stop diverging.
