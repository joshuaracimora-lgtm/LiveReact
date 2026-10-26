# Working Assumptions (living document)

There is no original author ("Josh") to consult and no prior source code. Every
"ask Josh" question has a researched **working assumption** to build on — see
[`docs/10_OPEN_QUESTIONS_AND_ASSUMPTIONS.md`](docs/10_OPEN_QUESTIONS_AND_ASSUMPTIONS.md)
for the full reasoning. This file is the **living tracker**: tick items as
hardware/usage confirms or refutes them. The dangerous failure mode is treating an
assumption as a fact — so these must also be surfaced in the UI/logs (e.g. "MIDI
map: defaults, unverified") until confirmed.

Confirm by: 🔧 hardware test · 🎚️ DJ/usage feedback · 🔬 researched (settled) · 🧩 product call

## Status of key assumptions

| ID | Assumption | Confirm | Status |
|----|------------|:------:|--------|
| A1 | DMX channel maps: SHEHDS 180W MH solid (PDF); laser/par/wash-bar are hypotheses — verify each via fixture-test panel | 🔧 | confirmed (hw), 2026-08-31 — all 4 fixture types verified by isolated channel probing, see `rigs/josh_rig.json`'s own description |
| A2 | XDJ-XZ MIDI CC map approximately right but **verify all** (may use HID for some controls) — ship MIDI-learn, persist map | 🔧 | open |
| A3 | PDL gives BPM/beat(1–4)/on-air/master/loop/play; **bar=inferred, phrase=unavailable, beat-phase=interpolated, position=unavailable** | 🔬 | settled |
| A4 | One Enttec, one universe, 74 channels (no multi-universe) | 🔬/🧩 | settled |
| A5 | Show machine = **macOS** (primary); build macOS-first, keep code portable | 🧩 | settled |
| A6 | **Static link-local** `169.254.50.50/16` host-side for the XDJ link; APIPA as zero-config fallback | 🔬/🔧 | open (hw) |
| B1 | Frequency→color rule kept for **consistency** (not neuroscience): energy→bright/sat mandatory, band→hue tunable data | 🔬/🎚️ | settled |
| B2 | Strobe conservative: sustained near ~3 Hz known-safe line; high-rate only as short auto-expiring drop bursts; prefer white over red | 🔬/🎚️ | settled |
| B3 | Lasers on **only in explicit drop scenes**, static/above-head, never sound-active modes, off by default, fail-safe to off | 🔬 | settled |
| B5 | ~8 scenes for v1 from the 12-scene starter catalog; expand during tuning | 🧩/🎚️ | superseded, 2026-09 — the separate Bible-integration effort (see `docs/bible_integration/`) replaced the 12-scene starter catalog with a 30-look catalog spanning every energy bucket, per `spec/LIGHTING_BIBLE.md` |
| B6 | Rotation cadence default **16 bars** (allow 32 for sustained high energy; never below 8) | 🎚️ | open |
| B7 | Scene selection: **round-robin within an energy bucket** for v1 (no Markov chain) | 🧩 | open |
| B8 | "Energy" estimated from MIDI EQ/fader state + engaged bands + phrase position, smoothed (no audio, no PDL phrase) | 🔬/🎚️ | open |
| C1 | Add a small optional **`config.json`** (last brightness, discovered MIDI map, selected rig, per-scene tuning) | 🧩 | planned (Phase 5) |
| C3 | No fixed external deadline; plan by phases, not calendar | 🧩 | settled |
| C6 | Tap-tempo / manual BPM fallback kept as the emergency path when PDL is unavailable | 🔬 | planned (Phase 5) |

## Engineering decisions (defaults locked)

| ID | Decision | Status |
|----|----------|--------|
| D1 | Threads + queues + injected clock (not asyncio) | locked |
| D2 | Roll the Enttec driver (~30 lines) rather than depend on a library | locked |
| D3 | Add an Art-Net/sACN backend for hardware-free visualizer testing | locked |
| D4 | Standard GIL CPython for v1; write free-threading-clean (revisit no-GIL in 2027+) | locked |
| D5 | Vendor / pin a SHA of `python-prodj-link`; wrap behind the InputSource interface | locked (2026-10-25: the copy in this Mac's `.venv` is the reference — no dist-info, SHA unknown; we now also use its `pdblib` (export.pdb) and `network.nfsclient` (NFS fetch from the player), both behind `rekordbox/`) |
| D6 | Test real-time logic via injected `Clock` + `FakeClock`; fake serial sink; synthetic messages | locked (in use) |

## Deviations from the original spec (see docs/13 for the full log)
- Python **3.14 → 3.12** (MIDI wheels + Windows high-res timer already at 3.11)
- **tkinter → PySide6** (60 Hz console + smooth visualizer; LGPL; already in tree via PDL)
- **macOS-only → macOS-first, portable** (OS-touching details behind abstractions)
- **Add Art-Net/sACN** output backend alongside Enttec serial
- "Phrase Engine" = **bar-count rotation** engine (true phrase data isn't transmitted)
- Frequency→color rule kept, but the **neuroscience rationale is dropped** (it's a consistency convention)
