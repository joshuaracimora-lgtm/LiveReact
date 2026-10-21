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

Normalizer seeding: runs seed the energy normalizer from `config.json` like the
live app; `--cold-start` reproduces a set's first minutes (energy_norm reads 0.0
for ~40 s cold, found on the first scored run).
