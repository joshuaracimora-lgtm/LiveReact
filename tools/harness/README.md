# Replay harness

Runs the real LiveReact pipeline (AudioAnalyzer -> Metronome -> engines -> merge)
from files on a virtual clock. No decks, no Enttec, no UI. About 80x real time.
Same inputs -> byte-identical DMX frame log (`tests/test_harness_determinism.py`).

```
# one-time per track: beat grid + first downbeat (pass the BPM you know)
python tools/harness/analyze_track.py tracks/song.mp3 --bpm 128

# one run
python tools/harness/run_sim.py tracks/song.mp3 --gestures gestures/clean_mix.json

# every track in tracks/ -> results/<timestamp>/summary.json  (exit 1 on any failure)
python tools/harness/run_batch.py --gestures gestures/static.json
```

Outputs per run: `frames.npz` (uint8[N,512] universe per tick + virtual timestamps),
`signals.csv` (engine-internal signals per tick), `summary.json` (machine-readable,
includes `frames_digest` and `unverifiable_in_sim`).

Needs `ffmpeg` on PATH and `pip install -e ".[sim]"`. Audio in `tracks/` is gitignored;
the small `*.timeline.json` files next to it are committed (hand-correct them if the
downbeat is off -- the harness trusts the file).

## Scoring

```
python tools/harness/run_batch.py --out results/v1          # frames needed for scoring
python tools/harness/score.py results/v1                    # score.json per run + scorecard.md
```

`livereact/sim/score.py` judges the DMX output only (never the engine code): a
verdict per rule ID -- PASS / FAIL / UNVERIFIABLE_IN_SIM (strobe Hz) / NOT_SCORED
(needs markers this run lacks) -- plus show-quality metrics and a hard gate: any
critical FAIL marks the run BLOCKED. `tests/test_scorer.py` plants violations in
hand-built frame logs and checks that only the planted rule fails.

Rule parameters that are still first guesses (tune by arguing with real verdicts):
`BUILD_BARS=16`, `FILL_BARS=8`, `DROP_LATENCY_MAX_BEATS=2`, the TIME-06 hue/motion
tolerances, and the TIME-04 snap window (<15% -> >=60% within 3 ticks).

Scorer v2 (2026-10-21, STRUCT-04..09 for the roll / cut / groove vocabulary in
`spec/PENDING_RULES.md`) adds a block of tunables at the top of `sim/score.py`,
every one a first guess:
- STRUCT-04 whiteout: `WHITEOUT_MAX_BARS=2`, `WHITEOUT_SETTLE_BEATS=1`, white =
  `WHITE_LEVEL=0.6` / `WHITE_CHROMA=0.15` with `WHITE_DIMMER=0.6` for
  `WHITE_FRACTION=0.75` of the window; heads within `DJ_POSE_TOL=0.03` of the DJ
  pose or closing `DJ_APPROACH_FRACTION=0.5`. The DJ pose is the rig slot's
  optional `dj_pan_tilt` (or a calibrated `dj_booth` aim); none -> that
  sub-check is NOT_SCORED.
- STRUCT-07 groove: `GROOVE_CORR_MIN=0.15` + `GROOVE_CORR_MARGIN=0.05` (bar
  follows synth, not kick); heads at `GROOVE_HEAD_SPEED_FRACTION=0.5` of the
  slew clamp for `GROOVE_HEAD_MOVING_FRACTION=0.5` of frames; laser bursts
  `GROOVE_BURST_BEATS=(1.5, 4.5)` every `GROOVE_PERIOD_BEATS=(5, 10)` beats.
- STRUCT-08 weak roll: `ROLL_ENERGY_RATIO=1.15`, `ROLL_STROBE_VISIBLE_STEP=20` bytes.
- STRUCT-09 clap-driven drop: `CLAP_WORDS`, `CLAP_STROBE_CEILING=96` bytes,
  `CLAP_ONSET_CORR_MIN=0.15` within `CLAP_ONSET_MAX_LAG_TICKS=3`.
- STRUCT-05 power-off: `POWEROFF_PRIOR_LEVEL=0.15`, `POWEROFF_START_BARS=2`,
  `POWEROFF_MAX_BARS=2`, `POWEROFF_STEPS=(3, 6)`, `POWEROFF_STEP_MIN=0.05`,
  `POWEROFF_HOLD_TICKS=3`, `POWEROFF_DARK=0.05`.
- STRUCT-06 fading build: `FADE_WORDS`, `FADE_DIM_DROP=0.10`, `FADE_MAX_STEP=0.05`,
  `FADE_MAX_RISE_FRACTION=0.10`, `FADE_CHROMA_WHITE=0.15`, `FADE_CHROMA_DROP=0.10`,
  `FADE_MAX_MOTION=0.01`.

Normalizer seeding: runs seed the energy normalizer from `config.json` like the
live app; `--cold-start` reproduces a set's first minutes (energy_norm reads 0.0
for ~40 s cold, found on the first scored run).
