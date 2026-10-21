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
