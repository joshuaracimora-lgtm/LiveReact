# Agent night 20261024 — agent/20261024-2

## Sub-goal
Family 2, rule **TIME-02** (a BREAK/OUTRO look must hold 16 bars). Mechanism: the phrase engine's scheduled
16-bar rotation no longer starts a new look inside the break family (BREAK/OUTRO). The break look holds
until the bucket changes (LIGHTING_BIBLE.md §9.1 "Break: slow, static hue"; break_low_wash fail mode
"motion/hue churn kills the rest"). **Result: 1 commit, gate ACCEPT** (`ce1cc10`).

**Run interruption:** the desktop appears to have slept from about 03:20 to 11:29, during a study run
after the commit (file mtimes: `study/detail_1.txt` 03:19, `study/runs_a2.pkl` 11:29; `date` read 11:30).
When it woke it was past the 07:05 deadline, so I stopped building, reverted the uncommitted experiment
(see "Tomorrow") and wrote this report. Please check the power settings or the orchestrator's power
request. This run's actual working time was about 15 minutes.

## What the music does (evidence from dump_signals, with track names and times)
Evidence is from a per-tick study of the scene and bucket runs over the 10 ear-verified tracks
(`results/agent/20261024/study/t02.py`, `detail_head.txt`). House breaks on this set run 16–44 bars and
end on the drop. The phrase engine rotated looks every 16 bars whatever the state, so 16–18 bars into a
long break it started a second break look. TIME-02 needs that look to hold 16 bars, and every time this
happened the drop cut it short:
- SWEETER: the opening Deep Wash Breathe held 18 bars, then a rotation at 32.76 s; the drop at 33.9 s
  ended the new look after 0.6 bars. In the 44-bar break from 134.8 s, rotations came at 170.9 and
  203.69 s, and the drop at 215.2 s ended the second one after 6.3 bars.
- messy: Deep Wash Breathe from 101.47 s held 18 bars, then a rotation at 133.7 s (Break Low Wash); the
  drop at 149.4 s ended it after 8.8 bars. With the scheduled BREAK rotation removed, the false OUTRO at
  136.1 s swapped in Outro Fade Wash at 140.88 s instead (4.7 bars before the drop). That is why OUTRO is
  included in the hold.
- Same pattern on Talk To You 112.87 s (2.4 bars) and Beautiful Now 34.04 s (0.1 bars). Those tracks
  have other violations too.
The remaining TIME-02 violations have two other causes:
(a) the power-on BREAK run before the first drop: Thriller 4.5 bars, Namerakana 8.2, Somebody To Love 2.0,
Sweet Nothing 8.0, Talk To You 2.1;
(b) a bucket-driven BREAK look in a break shorter than 16 bars: Thriller 86.6→96.3 s, Namerakana 73.2→78.2 s,
Somebody To Love 79.4 / 125.6 / 186.5 s, Sweet Nothing 192.1 s, Beautiful Now 169.0 s, Latch 148.75 s,
OMT 199.41 s (12 bars, then BUILD).

## What I changed (files, mechanism, new knobs with tags)
- `src/livereact/engines/phrase.py` `_maybe_rotate`: on the scheduled cadence only, no rotation while
  both the bucket and the held look are in the break family. The bucket rule (family change, rank up),
  a DJ `skip()` and the settle after a mix still rotate exactly as before.
- `tests/test_phrase.py`: four new tests (hold through 3× ROTATION_BARS of BREAK; hold under a promoted
  OUTRO; skip still rotates; leaving the break still rotates). One existing test,
  `test_escalation_mid_crossfade_finishes_it_and_rotates_same_bar`, used a scheduled break→break
  rotation as its setup. It now starts that crossfade with `skip()`, and its assertions are unchanged.
- No new knobs, no knob default changed.
- Tests on the final commit: 665 passed, 4 skipped, 3 failed (the known 3 in
  test_spec_conformance_gaps.py). Run with `-p no:cacheprovider --basetemp=results/agent/20261024/study/pytmp`
  because of this desktop's temp-dir PermissionError, which last run's report describes. The first try
  without the pre-created basetemp dir errored out in test_agent_tools (FileNotFoundError).

## Gate result (compliance before -> after, critical fails, every track flip)
`results/agent/20261024/gate_1.json`: **ACCEPT** (baseline rewritten and committed in `ce1cc10`)
- tuning compliance 0.3083 -> 0.338, critical fails 27 -> 25
- hold-out compliance 0.32 -> 0.32, critical fails 11 -> 11
- flips: SWEETER TIME-02 FAIL -> PASS; messy TIME-02 FAIL -> PASS. No PASS -> FAIL anywhere.
- violation totals over 10 tracks: TIME-02 32 -> 26, TIME-11 94 -> 94, TIME-13 6 -> 6
- quality: tuning metrics unchanged (distinct_looks 4.286, drop_response_mean 0.007); hold-out
  distinct_looks 4.0 -> 3.333 (floor 3.2)
- Before this, one study gate with the narrower version (OUTRO still rotated): REJECT, because messy
  STRUCT-02 went PASS->FAIL (`study/gate_1a.json`). Not committed.

## What got worse or stayed broken, and why
- Hold-out distinct_looks fell 4.0 -> 3.333 (83% of baseline, floor 80%). On Latch and Talk To You,
  Break Low Wash only ever arrived through a scheduled break rotation, so each track now shows 3 looks.
  A future change that removes another look on the hold-out tracks will hit the floor.
- A real outro promoted from BREAK now keeps the break look instead of switching to Outro Fade Wash.
  On the labeled set every auto-OUTRO was a false mid-track one (last run's report). Once OUTRO is
  terminal or DJ-cued, the outro look should come back through the bucket rule.
- TIME-02 still fails on 8 tracks, for causes (a) and (b) above. Neither can pass without holding a
  break look over the drop, or a rule decision.
- messy STRUCT-02 still passes only because of the false OUTRO (last run's finding). My change kept it
  passing but did not make it legitimate.

## Could not fix without Josh (rule change needed, or a question) — one bullet each
- TIME-02 and TIME-11 both score the power-on BREAK run before the first drop (5 tracks, 2–8 bars).
  Should the first run be exempt, as the last one already is?
- TIME-02 in a break shorter than 16 bars: the look entered on the break can only reach 16 bars by
  staying through the drop. Should TIME-02 measure a look against the section it was entered in (end
  at the bucket change), or should short breaks keep the outgoing drop look? The second option
  contradicts your 2026-09-15 fix.
- OK to keep a promoted OUTRO on the break look (no auto-switch to Outro Fade Wash) until the
  terminal/DJ-cued OUTRO question from last run is answered?
- Last run's four open questions (attempt3_combined.patch, terminal OUTRO, DROP 8-bar floor vs your
  2-bar cut exit, opening-run exemption) are still open.

## Tomorrow (the sub-goal to continue, or the next family)
Family 2, TIME-11. Last run's attempt 2 patch (`results/agent/20261024-run1/attempt2_peak_dwell_tempo_bars.patch`)
still applies on top of `ce1cc10`. With it, TIME-11 drops to 1 violation on SWEETER (the false OUTRO
68.03 s, 10.7 bars) and 2 on One More Time (DROP 133.49 s 5.6 bars, BREAK 144.34 s 3.2 bars), from my
partial study (`study/detail_a2.txt`; the run was interrupted by the sleep, and Beautiful Now came in
after the wake). Not gated tonight. Next: check whether OMT's two short runs can go without breaking
the 2-bar cut exit. If they can, attempt 2 plus that should flip OMT TIME-11.
