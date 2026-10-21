# Gesture scripts

Scripted mixer moves the harness replays as MIDI (see `livereact/sim/gestures.py`).

- `static.json` — no operator input; pure engine behaviour.
- `clean_mix.json` — one 8-bar blend out at 64 s (bass cut on the outgoing deck),
  one long 16-s blend back at 160 s with a late low cut on deck B.

Times are seconds into the track. Add scripts for the situations you want
exercised (EQ chops, fast cuts, long breakdown blends). Control names are the
semantic ones in `inputs/midi_map.py`.
