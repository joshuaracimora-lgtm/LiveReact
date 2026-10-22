# Hardware Bring-Up Guide

> **Status: VERIFIED ON REAL GEAR.** The Enttec DMX output, every fixture channel
> map below, the XDJ-XZ's Pro DJ Link beat stream, its USB-MIDI mixer controls,
> and its USB audio interface have all been confirmed on the physical rig
> (first bench session 2026-08-31; live sets since). This file is the checklist
> for bringing up a *new* rig, a new venue, or a new laptop — and a record of
> the hardware quirks that bit us, so they don't bite twice. Step-by-step test
> scripts with a recording/feedback loop live in [`TESTING.md`](TESTING.md).

## 0. Prerequisites

- Python **3.12 or 3.13** venv with the hardware deps:
  `pip install -e ".[ui,hardware,dev]"`. **Not 3.14**: its garbage collector has
  segfaulted inside the show-engine thread on live nights. `tools/run_show.sh`
  prefers a `.venv313` if one exists.
- For Pro DJ Link, install the git-only library once (pin a SHA). Only its
  packet *definitions* are used — see §3 for why:
  ```
  pip install "git+https://github.com/flesniak/python-prodj-link@<sha>"
  ```
  Without it, hardware mode still runs; beat timing comes from the audio
  analysis, tap tempo (**T**) and a free-running metronome.
- Sanity-check the environment and what's plugged in:
  ```
  python -m livereact --doctor
  ```

## 1. The rig

Fixtures and their DMX addresses are **per rig file**, not fixed. Pick one with
`--rig rigs/<file>.json`; the Rig Config and Venue tabs in the console edit and
save them. Every address below was set on the unit itself and verified.

| Fixture | Profile (`fixtures/profiles.py`) | Mode | Verified |
|---|---|---|---|
| SHEHDS 180W beam moving head | `MovingHead` | 15CH, 16-bit pan/tilt (fine channels used) | 2026-08-31; two heads at a time since 2026-10-10 |
| SHEHDS 3W laser | `Laser` | 12CH, CH1 = mode (0–41 off) | 2026-08-31; two at a time since 2026-10-08 |
| RGB par | `Par` | 4CH: dimmer, R, G, B (no white — a 5th channel does nothing) | 2026-08-31 by isolated channel probing |
| OK-103-288WL wash bar | `WashBar` | 6CH: master, strobe, R, G, B, W | 2026-08-31 |

| Rig file | Fixtures @ address | Laser profile |
|---|---|---|
| `rigs/josh_rig.json` | laser @1, par @21, moving head @36, bar @69 | `accent` (default) |
| `rigs/testing_setup.json` | the garage truss rig (6 slots) | `trigger` |
| `rigs/bar_rig.json` | bar @69 + two lasers | `feature` |
| `rigs/sophias_thai_venue.json` | bar @69, laser L @75, laser R @1 | `show` |
| `rigs/junior_crib.json` | head L @51, head R @36, laser L @75, laser R @1, bar @69 | `manual` |

Set each fixture's address and channel mode on the fixture, daisy-chain them,
and put a **120 Ω terminator** on the last one.

Physical quirks worth knowing:

- **Wash bar**: R/G/B light a different LED zone ("outer") than White
  ("center"). It is not a true RGBW mixing pixel; the profile accounts for it.
- **Par**: colour with the dimmer at 0 produces nothing, and dimmer with colour
  at 0 produces nothing. Both are needed.
- **Moving heads hung at 18 ft cannot tilt down onto a booth directly below
  them.** Calibrate against wall and booth-edge targets instead (§6).
- **Laser units can be mis-addressed silently.** On 2026-10-10 one laser showed
  no output while software sent both identical bytes; it was the unit's own
  address/mode, fixed on the fixture. Isolate with the fixture-test panel
  before suspecting code.

## 2. DMX output (Enttec DMX USB Pro)

1. Plug in the Enttec. It is matched by its FTDI chip (VID `0x0403`), never by
   name; on macOS the `cu.*` node is used (the `tty.*` node blocks on open).
2. **The output backend is chosen once, at launch.** If the Enttec is not on
   the bus at that moment the run is log-only (`Hardware mode: no Enttec
   detected; output will be logged only`) and it never re-probes the port.
   Plug in, *then* launch. If it drops off USB mid-set — it happened once, on a
   hub, during a show — replug and relaunch; `tools/run_show.sh` makes that a
   window-close away. Send failures on an open port do get a reopen loop with
   capped backoff.
3. **Test one fixture at a time** with the manual panel (`--hardware` is needed
   so it talks to the real Enttec rather than the log backend):
   ```
   python -m livereact --hardware --fixture-test --rig rigs/<file>.json
   ```
   Use each fixture's **Flash** button and the per-channel sliders. Any
   mismatch is fixed in `src/livereact/fixtures/profiles.py` or the rig file,
   never in engine code.
4. **Laser safety**: confirm CH1 boots at 0 (off) and only lights when you
   raise it. The console's laser arm switch (**L**) and blackout (**B**) zero
   the laser channels on every profile, including `show` and `manual`. Aim is
   verified by a human at install — keep beams above head height (~3 m). Never
   use the fixture's sound-active modes (CH1 168–251). The fixture's own
   circuit sets the strobe flash rate from a static byte, so photosensitive
   flash-frequency limits cannot be enforced in software; see
   `spec/LIGHTING_BIBLE.md`.

## 3. Pro DJ Link (Ethernet beat grid)

1. Wire the XDJ-XZ to the laptop by Ethernet and enable **Link** on the XZ.
2. Give the wired NIC a **static link-local** address, `169.254.50.50` /
   `255.255.0.0`, and allow UDP **50000–50002** through the firewall. Check:
   ```
   python -m livereact --net-check
   ```
3. Launch hardware mode and watch the console's PDL indicator go LOCKED/fresh
   once a deck is playing. "No DJ-Link device seen" is almost always the
   static IP or the firewall.

What the bench session taught us (all in `inputs/prodj.py`):

- The XDJ-XZ broadcasts as **three devices from one IP** (deck 1, deck 2, its
  mixer, player numbers cycling 1 → 2 → 33). `python-prodj-link`'s runtime keys
  clients by IP, folds them into one record, and silently drops the beat
  packets. LiveReact therefore binds its own sockets on the three PDL ports and
  uses only the library's packet parsers.
- The unit **never sends status packets** (port 50002), only keepalive (50000)
  and beat (50001). So there is no on-air/master data: whichever deck is
  sending beats is treated as master, and "beats are arriving" doubles as
  "a deck is playing". No virtual-CDJ announcement is needed.

## 4. MIDI (the XDJ mixer controls)

1. On the XDJ-XZ enable **Utility → MIDI** and connect the USB-B cable.
2. The XZ exposes **two** USB-MIDI ports; only the one named "… (2)" on macOS
   carries control-change data. `inputs/midi.py` prefers it explicitly.
3. The full CC map is already in `inputs/midi_map.py` (`DEFAULT_CC_MAP`),
   confirmed one control at a time: everything arrives on mido channel 4,
   deck A fader = CC 17 with EQ high/mid/low = 2/3/4, deck B fader = 18 with
   EQ = 7/8/9. Re-capture only if your unit differs:
   ```
   python -m livereact --midi-monitor
   ```
4. Optional operator cue controls (e.g. a `laser_cue` knob) go in a
   `midi_map.json` at the repo root as `"channel:cc": "name"`; the file is
   read at launch if present. MIDI is read-only: LiveReact never sends to the XZ.

MIDI is a nice-to-have. Shows have run without the XZ connected at all.

## 5. Audio (the signal the engine actually listens to)

The XDJ-XZ exposes a CoreAudio device ("XDJ-XZ", 8 in / 12 out, 44.1 kHz) over
the **same USB-B cable** as MIDI. Of its 8 input channels only 5–8 carry
signal; LiveReact captures channels 5 and 6. Auto-detect finds any input
device with "xdj" in its name; otherwise it refuses to start.

- No XDJ, or it dropped off USB? Point at another input, e.g. the laptop mic:
  `--audio-device 1` (name or index; list them with
  `python -c "import sounddevice; print(sounddevice.query_devices())"`).
  The analyzer has automatic gain control tuned for the mic-fallback case,
  but the XDJ's line feed is far better.
- The capture callback only copies into a ring buffer; all FFT/band analysis
  runs on a separate thread, so a slow Python moment cannot drop audio.
- `--no-norm-seed` cold-starts the energy normaliser at a new venue instead
  of seeding from the last session's saved range.

## 6. Moving-head calibration (Venue tab)

Everything the operator types is in feet. Enter the room size, place the
booth and each fixture (X/Y, lens height, facing), then capture a **motion
box** per head: tick "Jog live", move pan/tilt until the beam sits on each
target (venue edges, booth edges), press **ADD BOUND POINT** at each, and
**Save rig…**. All automatic movement is then remapped inside that box and
absolute aims are clamped to it. The two-point geometric calibration also
exists (**SET AIM** / **ADD 2ND POINT**) but the box proved more reliable live.

## 7. End-to-end

```
nohup tools/run_show.sh --hardware --visualize --record --rig rigs/<file>.json \
    [--audio-device N] > logs/watchdog.log 2>&1 & disown
```

Play a track: the rig pulses on the beat, scenes rotate with the energy state,
drops fire the lasers once armed, and the session is recorded under
`recordings/`. Console hotkeys: **B** blackout, **F** freeze, **→** skip look,
**T** tap tempo, **S** synth sync, **L** arm lasers, **K** hold laser cue,
**J** laser hit, **C** cue a build, **D** arm a drop, **Space** strobe burst.
Stop with `touch logs/STOP` and close the window, or kill the python PID
(`pgrep -fl "m livereact"`). Never `pkill -f python` on a machine running
anything else.

## What's verified vs. not

- **Verified on hardware:** Enttec serial output at 40 Hz (0 send failures
  across full sets), all four fixture channel maps, PDL beat stream and its
  three-devices-one-IP behaviour, the XZ MIDI CC map and port quirk, XZ audio
  over USB, the laptop-mic fallback, head motion boxes, all five laser profiles.
- **Still assumed:** the Art-Net backend (`net` extra) has never driven a real
  node; the Enttec USB dropout cause (hub vs cable) is unresolved; strobe flash
  frequency is fixture-side and unmeasured.
