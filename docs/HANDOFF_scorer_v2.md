# Handoff: scorer v2 — rules for Josh's lighting vocabulary

**For:** a Claude Code session in `~/LiveReact` on branch `harness`.
**Goal:** add scorer rules so the judge can see the behaviours Josh has been describing while labeling tracks (whiteout, groove, clap-driven drops, weak rolls), with planted-violation tests, without touching the engine.

## Where things stand (Oct 21 2026)

- `src/livereact/sim/` — offline replay harness. `harness.run_sim()` runs the REAL pipeline from a file on a `FakeClock` and returns a `RunResult` (uint8[N,512] DMX frames + per-tick signals + summary). Deterministic (`tests/test_harness_determinism.py`). ~80x real time.
- `src/livereact/sim/score.py` — **scorer v1**, 19 rules. Read it first; new rules follow its pattern exactly: a `@rule("ID", "severity")` function taking a `Run` and returning a `Verdict(rule, status, severity, metric, evidence, note)`. Status ∈ PASS / FAIL / UNVERIFIABLE_IN_SIM / NOT_SCORED. `Run` precomputes per-tick arrays by `ChannelKind`: `dimmer[n,nf]`, `strobe[n,nf]`, `color[j]`, `move[j]` (16-bit pan/tilt pairs decoded), `laser_any[n]`, `active`, `strobe_any`, `bucket[n]`, `scene[n]`, bar/beat arithmetic from the Timeline. Rules judge DMX output only — never engine code, never LLM calls.
- `src/livereact/sim/timeline.py` — `Marker(t, kind, note, end)`. Kinds: `drop` (the roll; has `end`), `fill`, `breakdown`, `build`, `whiteout`, `groove` (has `end`). Markers whose `note` starts with `est:` are machine guesses → rules return NOT_SCORED (see `_needs_markers`).
- `tests/test_scorer.py` — planted-violation tests: hand-built frame logs where exactly one rule must FAIL and nothing else. **Every new rule needs one of these.** `make_run()` builds a clean run; `set_window()` plants a violation.
- `spec/PENDING_RULES.md` — the rules to implement, with Josh's wording, severities and what to measure. `spec/LIGHTING_BIBLE.md` + `spec/conformance.md` are the existing bible (read `CLAUDE.md` first; the lighting/merge/DMX paths are off-limits here anyway).
- `src/livereact/loop/` — evaluate / gate / search. A parameter search is running on this Mac right now (`results/search/<ts>/`); **do not edit `tracks/*.timeline.json` for the 7 labeled tracks** (the 5 originals, Thriller BVRNOUT, SWEETER) while it runs, and don't start another search. Tests and batch runs are fine.
- Run: `.venv313/bin/pytest -q` (3 pre-existing expected failures live in `test_spec_conformance_gaps.py`; everything else must stay green). Scorer on real tracks: `.venv313/bin/python tools/harness/run_batch.py --out results/v2 && .venv313/bin/python tools/harness/score.py results/v2`.

## Josh's vocabulary (ground truth for the new rules)

A tech-house drop has three phases the engine currently collapses into one DROP:

| Phase | Marker | Lights |
|---|---|---|
| **roll** (snare roll / in-the-roll kick) | `drop` with `end` | strobe starts low and **escalates** to the end of the roll (STRUCT-02, exists); a weaker roll gets a visibly lower strobe ceiling (STRUCT-08) |
| **cut** (drums drop out, track stays intense: stabs/synths carry it) | `whiteout` | strobe → **white out**, moving heads turn to the DJ (STRUCT-04). Also fires on stab-carried sections with no drums (Thriller 0:27) |
| **groove** (main full-energy section after the cut) | `groove` with `end` | wash bar **synth-synced** (intensity tracks the synth/mid band, not the kick), moving heads at **full speed**, lasers fire a **2–4 beat burst every other bar** (STRUCT-07) |

Plus: a **clap-driven drop** (SWEETER: no roll, synths + claps, no kick) → hits on the claps/crashes, soft slow strobe at most — never the roll strobe. Josh writes this in the marker `note` ("react to the claps", "no snare roll"). And a **fill** (`fill` marker) never strobes or uses DROP looks (STRUCT-01, exists).

Reference tracks with verified markers: Thriller BVRNOUT (whiteouts, rolls, grooves, weak roll), SWEETER (clap-driven drops, grooves), messy (escalating long drop), Beautiful Now (fading extended build → white slow dim, STRUCT-06), One More Time (fills).

## Build

1. **STRUCT-04 whiteout** (major): in the bar after a `whiteout` marker (until the next marker, max 2 bars): wash fixtures' COLOR channels converge to white (R≈G≈B, high) with dimmer ≥ 60%; strobe byte 0; moving heads' pan/tilt move toward a DJ-facing pose. The DJ pose needs a `dj_position` (or `dj_pan_tilt`) in the rig JSON — add an optional field to `fixtures/rig.py` loading with a safe default of "not configured → that sub-check NOT_SCORED", don't invent a position.
2. **STRUCT-07 groove** (major): over each `groove` window: (a) correlation of the wash bar DIMMER with the mid/high band (signals.csv has `band_mid`, `band_high`, `band_low` per tick) exceeds its correlation with `band_low`; (b) moving-head per-frame delta near the profile's slew clamp (find the clamp in `fixtures/profiles.py` / MOVE rules); (c) laser channel bursts of 2–4 beats with ~1 bar between them. Report the three sub-metrics; FAIL if any is clearly off, with a tolerance you document.
3. **STRUCT-08 weak roll** (minor): peak strobe byte over each `drop` window, ordered the same way as the roll's snare-band energy (compute from `signals.csv` bands or recompute from audio via `sim/analyze._band_curves`). Only scorable when a track has ≥ 2 drops.
4. **STRUCT-09 clap-driven drop** (major, new): a `drop` whose note mentions claps/crashes/"no snare roll": strobe byte stays below a soft ceiling (document it), and dimmer transients line up with snare/clap-band onsets (use the onset rate in signals or `_band_curves`). Fail on roll-strobe behaviour.
5. **STRUCT-05 power-off shutter fade** (major) and **STRUCT-06 fading build** (major) from PENDING_RULES — implement with the placeholders there (3–6 steps over 1–2 bars; hue → white, smooth slow dim) and mark the tolerances as first guesses in the docstring.
6. Tests for each in `tests/test_scorer.py` (planted violation fails only its rule; clean run fails nothing — note the clean run in `make_run` may need `whiteout`/`groove` markers added so the new rules are exercised, not NOT_SCORED).
7. Add the new IDs to the table in `spec/PENDING_RULES.md` if missing, update `tools/harness/README.md` tunables list, run the full suite, run the scorer on `results/v2`, and commit on `harness` with a message listing which tracks each new rule now FAILs on (expected: most of them — the engine has none of these behaviours yet; that's the point).

## Don'ts

- Don't change `engines/`, `merge/`, `output/`, `core/metronome.py`, `fixtures/profiles.py` behaviour (rig.py may gain the optional DJ field only). Scorer first, code second.
- Don't relax an existing rule to make a track pass.
- Don't touch `params.json` tags or `results/baseline/`.
- Don't edit timelines of the 7 labeled tracks while the search runs.
