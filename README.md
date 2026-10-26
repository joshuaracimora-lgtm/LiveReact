# LiveReact

A beat-reactive DMX stage-lighting controller for live DJ sets. It listens to
the music (real audio analysis from the DJ board's USB audio interface, plus
Pro DJ Link beat-grid timing and read-only MIDI from a Pioneer XDJ-XZ when
present), classifies the set's energy (BREAK / BUILD / DROP / PEAK / OUTRO),
and drives a DMX rig through an Enttec DMX USB Pro at 40 Hz. **Automation is
the default**; a human only nudges brightness, strobe cap, blackout, and
(on some rigs) laser cues.

It has run real sets: a bar residency, a Thai-restaurant set (two lasers + a
wash bar), and a house-party rig with two moving heads, two lasers and a wash
bar. Everything in `rigs/` reflects fixtures verified on hardware by channel
probing, not datasheets.

Design history lives in [`docs/`](docs/) — start with
[`docs/00_EXECUTIVE_SUMMARY.md`](docs/00_EXECUTIVE_SUMMARY.md). The original
build checklist is [`PLAN.md`](PLAN.md) (frozen at the end of the clean-room
rebuild; see its scope note), deferred hardware/usage decisions are in
[`ASSUMPTIONS.md`](ASSUMPTIONS.md), and the hardware test procedure is in
[`TESTING.md`](TESTING.md).

## Status

**Deployed and gigging.** The full pipeline (inputs → ShowState → Reactive +
Phrase + Transition engines → priority merge / safety → DMX writer) runs on real
hardware with an operator console. The Enttec serial output, real audio
capture + FFT analysis, Pro DJ Link beat grid, and MIDI input have all been
verified on the physical rig. `--hardware` still degrades gracefully: with no
Enttec it logs frames instead of sending them; with no XDJ, point
`--audio-device` at the laptop mic and beat timing falls back to tap tempo and
a free-running metronome.

Known rough edges, tracked in the repo rather than hidden:

- `tests/test_spec_conformance_gaps.py` holds **3 intentionally failing tests**
  that encode spec behaviour the merge/failsafe code does not yet implement.
  The other 619 tests pass.
- Python 3.14's garbage collector has segfaulted inside the show-engine thread
  on live nights. A Python 3.13 venv (`.venv313`) is used for shows and has not
  reproduced it; `tools/run_show.sh` prefers it and relaunches after a crash.
- The output backend is chosen once at launch. If the Enttec is absent then (or
  has dropped off USB before a relaunch), the run is log-only and never retries
  the port; replug, then relaunch. Mid-run send failures do get a reopen loop.
- The fixture-side strobe flash rate is set by a static DMX byte, so photosensitive-safety
  frequency limits cannot be enforced in software. See `spec/LIGHTING_BIBLE.md`.

## Running it

```bash
# Demo mode: synthetic music, no hardware, operator console + virtual rig
.venv/bin/python -m livereact --demo --visualize

# A live night (preferred): relaunch loop, hardware, recording, a specific rig
nohup tools/run_show.sh --hardware --visualize --record --rig rigs/junior_crib.json \
    --audio-device 1 > logs/watchdog.log 2>&1 & disown
#   stop it: touch logs/STOP  then close the window (or kill the python PID)

# Bring-up and diagnostics
python -m livereact --fixture-test           # drive raw DMX channels
python -m livereact --midi-monitor           # discover the XDJ's MIDI CCs
python -m livereact --net-check              # Pro DJ Link network readiness
python -m livereact --pdl-probe --pdl-fetch  # XDJ-XZ status packets + the loaded track's rekordbox phrase table
python -m livereact --hardware --phrases drive   # the per-track plan runs the sections (default: shadow)
python -m livereact --doctor                 # environment + detected-devices report
```

Useful flags: `--rig <json>` (fixtures + laser profile), `--audio-device
<name|index>` (default: auto-detect the XDJ; `1` is usually the MacBook mic),
`--record` (logs + state + notes into `recordings/session-<timestamp>/`),
`--full-catalog` (rotate all 29 scenes instead of the 6-scene core set),
`--bible-failsafe` (fade to a safe-default look instead of hard blackout when
music stops), `--no-norm-seed` (cold-start the energy normaliser at a new venue).
`python -m livereact --help` lists the rest.

### Rigs and laser profiles

`--rig rigs/<file>.json` picks the fixtures, their DMX addresses, the venue
geometry, and the **laser profile** (`fixtures/rig.py`, `LASER_PROFILES`):

| Profile | Behaviour | Engine | Rig(s) |
|---|---|---|---|
| `accent` | One sweep per 8-bar block during a PEAK | `engines/laser.py` | `josh_rig.json` (default when unset) |
| `feature` | Two lasers as the main light, lit for the whole set | `engines/laser_feature.py` | `bar_rig.json` |
| `trigger` | Lasers as punctuation: dark by default, 2-bar moments on drop hits and sustained peaks; **J** fires one now, **K** holds | `engines/laser_trigger.py` | `testing_setup.json` (garage truss rig) |
| `show` | Lasers lit all set; a "Laser Show" console tab with follow-the-music / rotate / hold modes | `engines/laser_show.py` | `sophias_thai*.json` |
| `manual` | Nothing listens to the music; scene cue buttons (OFF + Rainbow, Slow Rainbow, Rainbow Dots, Blue Sweep, White, Green) on the Show tab, off at launch | `engines/laser_manual.py` | `junior_crib.json` |

After a drop, moving heads run `scenes/movement.py`'s tempo-locked shapes
inside a per-head **motion box** (pan/tilt boundaries captured live from the
Venue tab) around the calibrated crowd aim.

### The console

Four tabs when `--visualize` is on: **Show** (energy state, manual caps,
blackout, laser arm, cue buttons on `manual` rigs, floor-plan mini-map),
**Laser Show** (only on `show` rigs), **Venue** (editable floor plan in feet,
per-fixture position/height/facing, moving-head calibration: jog live, set aim,
capture motion-box bounds, save rig), and **Rig Config**.

## Quick start

Python **3.12 or 3.13** (3.14 currently crashes under load — see Status).

```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[ui,hardware,dev]"
.venv/bin/python -m livereact --demo --duration 3     # short headless demo
.venv/bin/python -m pytest                            # test suite

# Windows (PowerShell)
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[ui,hardware,dev]"
.venv\Scripts\python -m livereact --demo --duration 3
.venv\Scripts\python -m pytest
```

Extras: `ui` (PySide6 console), `hardware` (pyserial, mido, python-rtmidi,
sounddevice, numpy), `net` (Art-Net output), `dev` (pytest). Pro DJ Link needs
the git-only `python-prodj-link` library; see [`HARDWARE.md`](HARDWARE.md).
The dev launchers `run.bat` / `./run.command` start demo mode from `.venv`.

## Lighting spec & look library

The *content* of the show — which look plays for a given energy state, its
colours and movement, how it degrades — is driven by
[`spec/LIGHTING_BIBLE.md`](spec/LIGHTING_BIBLE.md), a normative lighting-design
spec, with [`spec/conformance.md`](spec/conformance.md) tracking implementation
status rule by rule and
[`docs/bible_integration/RULES_MATRIX.md`](docs/bible_integration/RULES_MATRIX.md)
the current state. [`src/livereact/scenes/library.py`](src/livereact/scenes/library.py)
holds 29 scenes across every energy bucket; a core set of 6 (one per bucket, two
for BREAK) rotates by default. A few Bible-named looks are deliberately
deferred and documented in-code, pending hardware this rig lacks (a gobo wheel,
haze).

## Layout

```
src/livereact/
  __main__.py / app.py   CLI entry point; supervisor that wires components and owns shutdown
  clock.py               Clock / RealClock / FakeClock + drift-corrected LoopTimer
  core/                  ShowState, Metronome, show engine, build/rise/snare/silence detectors
  inputs/                InputSource interface; demo, real audio (FFT), Pro DJ Link, MIDI
  engines/               Reactive / Phrase / Transition layers, colour + palette, 5 laser profiles
  scenes/                29-scene library, catalog, tempo-locked moving-head movement
  merge/                 priority merge + manual caps + safety clamps
  fixtures/              fixture profiles, rig loader/saver, venue geometry + head calibration
  output/                backend interface, null/log backend, Enttec serial, 40 Hz writer
  ui/                    PySide6 console (Show / Laser Show / Venue / Rig Config), floor plan, visualizer
  recorder.py, diagnostics.py, network.py, tap_tempo.py, dynamics.py
rigs/                    rig JSON files (fixtures, addresses, venue geometry, laser profile)
spec/                    LIGHTING_BIBLE.md + conformance.md
tools/                   run_show.sh relaunch loop, laser_probe.py, harness + scorer CLIs, loop (knob search),
                         agent (status / study / gate tools for the nightly engine agent), windows (scheduled runs)
tests/                   pytest suite (deterministic via FakeClock)
```

## Design notes baked into the foundation

- **Injected clock everywhere** — real-time logic is unit-testable; a 90-min show
  simulates in milliseconds.
- **Double-buffered DMX** — the writer always emits the last *complete* frame, so
  an engine stall holds the lights steady instead of flickering.
- **The writer never stops** — every backend fault is contained; it reopens with
  backoff and keeps ticking.
- **Output behind an interface** — null/log, Enttec serial and Art-Net are
  swapped without touching engine code.
- **Real-time audio discipline** — the capture callback only copies into a ring
  buffer; FFT and band analysis run on a separate thread.
- **Verified on hardware, not assumed** — channel maps, addresses, and the
  XDJ's audio-over-USB path were each confirmed on the rig before being relied on.

## License

[MIT](LICENSE).
