# 04 — Hardware & Protocols (reference + reality check)

The rig, the channel maps, the wire-level protocol detail, and — most importantly — a **reality check** on what the protocols actually deliver vs. what the spec implies. Reproduced from `02_HARDWARE_RIG.md` and the appendices, annotated with external research.

> **Standing caveat:** every channel number, DMX address, and MIDI CC below was "verified on Josh's specific unit/firmware." You have no way to confirm that without the hardware. **Treat all of it as a starting hypothesis to verify**, and build the code so these values are *data* (rig files, profile tables, CC maps), never hard-coded constants scattered through logic.

---

## 1. The rig

| # | Device | Qty | Connection | Role |
|---|--------|-----|-----------|------|
| 1 | Pioneer XDJ-XZ | 1 | USB (MIDI) + Ethernet (PDL) | Audio source, musical clock, controller |
| 2 | Enttec DMX USB Pro | 1 | USB | DMX-512 output adapter |
| 3 | SHEHDS 180W Beam Moving Head | 2 | DMX chain | Spot beams |
| 4 | SHEHDS 3W Laser | 2 | DMX chain | Laser effects |
| 5 | RGBW Par (generic 4ch) | 2 | DMX chain | Color wash |
| 6 | OK-103-288WL Wash Bar | 2 | DMX chain | Bar wash (RGBW) |
| 7 | Host computer (MacBook in spec; **now also Windows**) | 1 | — | LiveReact host |

**8 fixtures, 74 DMX channels, one universe.** Chain order = MH-L → MH-R → Laser-L → Laser-R → Par-L → Par-R → Bar-L → Bar-R → 120 Ω terminator.

## 2. DMX address map (universe 1, no overlaps)

| Fixture | Label | Address | Channels | Range |
|---|---|---|---|---|
| SHEHDS 180W MH | MH Left | **1** | 15 | 1–15 |
| SHEHDS 180W MH | MH Right | **16** | 15 | 16–30 |
| SHEHDS 3W Laser | Laser Left | **31** | 12 | 31–42 |
| SHEHDS 3W Laser | Laser Right | **43** | 12 | 43–54 |
| RGBW Par | Par Left | **55** | 4 | 55–58 |
| RGBW Par | Par Right | **59** | 4 | 59–62 |
| OK-103 Wash Bar | Bar Left | **63** | 6 | 63–68 |
| OK-103 Wash Bar | Bar Right | **69** | 6 | 69–74 |

Fixture modes to set on hardware: moving heads **15CH**, wash bars **6CH**, lasers **12CH**.

## 3. Fixture channel maps (rebuild profiles from these)

### 3.1 SHEHDS 180W Moving Head — 15CH
`1 Pan · 2 Pan-fine · 3 Tilt · 4 Tilt-fine · 5 Speed · 6 Dimmer · 7 Strobe · 8 Color wheel · 9 Gobo wheel · 10 Prism · 11 Focus · 12 Frost · 13 Macro(keep 0) · 14 Macro-speed · 15 Reset`
- Pan/Tilt are 16-bit coarse+fine pairs. Strobe: 0=off, 1–255 slow→fast (≤25 Hz). Color wheel & gobo: value-range selects position; high ranges = continuous rotation.
- **Boot defaults:** Pan 128 (center), Tilt 80 (angled down), Speed 128, Dimmer 0. Keep Macro (13) = 0 — drive via DMX, not built-in macros.

### 3.2 SHEHDS 3W Laser — 12CH (**SAFETY-CRITICAL**)
`1 Mode · 2 Pattern-A coarse · 3 Pattern-A fine · 4 Rotation · 5 H-Flip · 6 V-Flip · 7 H-Move · 8 V-Move · 9 Pattern scale · 10 Pattern painting · 11 Scan · 12 Color`
- **CH1 ranges:** 0–41 = **LASER OFF (boot here)**, 42–83 manual, 84–125 auto-anim, 126–167 auto-beam, 168–209 sound-beam, 210–251 sound-anim, 252–255 manual.
- **Rules (non-negotiable, regulation-backed — see [08](08_LIGHTING_DESIGN_AND_SAFETY.md)):** CH1 boots at 0. Never auto-enable outside explicit drop scenes. **Never** use sound-active modes (168–251) — LiveReact owns all timing. Audience-safe pointing is verified at install by a human; software trusts the mounting and never tries to compensate.

### 3.3 RGBW Par — 4CH
`1 Red · 2 Green · 3 Blue · 4 White` — no master dimmer, no strobe channel. **Dim by scaling RGB; strobe by alternating RGB full/0 in software.**

### 3.4 OK-103-288WL Wash Bar — 6CH
`1 Master Dimmer · 2 Strobe (0–9 off, 10–255 slow→fast) · 3 All-Red · 4 All-Green · 5 All-Blue · 6 All-White`

### 3.5 Rig-file format (keep this idea)
JSON describing slots: `slot_id, fixture_type, dmx_address, label, role (spot|wash|laser), side (left|right)`. The app loads it, instantiates profiles, and exposes fixtures by role/side so scenes say "all wash, both sides." This is clean and worth preserving verbatim.

---

## 4. PROTOCOL REALITY CHECK (read this carefully)

The spec's `ShowState` and engine logic assume a richer PDL feed than the XDJ-XZ actually provides. None of this is fatal, but **design to the real data, not the optimistic data.**

### 4.1 What Pro DJ Link genuinely gives you
Confirmed against the `python-prodj-link` source and Deep Symmetry's protocol analysis (djl-analysis.deepsymmetry.org):

- **Directly available:** BPM + pitch, **beat-within-bar (1–4)**, on-air, master, sync, play state, loop active, player/device number. Beat packets also carry countdown timers to the next beat/bar.
- **Inferred locally:** **bar number** — count the 4→1 wrap of the beat counter (trust the *master* deck's counter; re-anchor on each beat packet because re-cues/loops make it jump).
- **Not transmitted over PDL — but available next to it (revised 2026-10-25):** **musical phrase / song structure.** No PDL packet carries section labels, so the "Phrase Engine" built so far is a *bar-count rotation engine*. However the CDJ **status** packet carries the loaded track's **rekordbox id** and an **absolute beat counter**, and rekordbox writes its phrase analysis (`PSSI` tag in `PIONEER/USBANLZ/.../ANLZ0000.EXT`: intro/up/down/chorus/outro or verse/bridge/chorus, positioned by beat number) onto the very USB stick in the player, readable over NFS (`src/livereact/rekordbox/`). Beat counter + phrase table = the current section and the beats until the next one. Staged in (docs/13 D-21): shadow mode first, engine use after hardware verification.
- **Interpolated:** **continuous beat phase (0–1).** Packets are events, not a smooth clock. Run a local metronome that advances phase from a monotonic clock and resyncs/slews to each beat packet. Never drive animation off raw packet arrival (it jitters 5–20 ms and lights will stutter).
- **Absolute track position on the XDJ-XZ (revised 2026-10-25):** the 30 ms high-resolution position packets are CDJ-3000-only, but the ordinary CDJ status packet (~5/s) has a **beat counter from the start of the track** (`beat_count`, 0xffffffff when no analyzed track is loaded). Status packets are *unicast only to peers that announce themselves* — which is why the 2026-08-31 bench capture, made with a failed announce, saw none and wrongly concluded the unit never sends them. `inputs/pdl_announce.py` announces as a rekordbox-class peer (player 17) and answers the XZ's hello handshake. Beat-resolution position is plenty for section lookups; `track_position_seconds` at sub-beat precision is still not something to build on.

### 4.2 XDJ-XZ specifics
- It's effectively **2 standalone timing decks + a mixer** (channels 3/4 are for external sources), *not* four independent timing sources. Model **2 timing-bearing players + mixer**; players 3/4 only populate if external CDJs are linked.
- It's confirmed to broadcast standard DJ-Link packets (Deep Symmetry's beat-link reports XDJ-XZ support). Your virtual-CDJ identity must claim a **free device number (typically 5–7)** so it doesn't collide with the XZ's internal players — a known "won't go online" failure if it collides.
- Pro DJ Link timing comes over Ethernet regardless of any USB MIDI mode; you don't need a special MIDI mode *for PDL* (you do for the MIDI reads — see §6).

### 4.3 Implication for `ShowState`
Keep the spec's `ShowState` shape but annotate provenance and confidence per field: which are *measured* (BPM, beat, on-air, loop), which are *inferred* (bar, in_transition, energy), and which are *interpolated* (beat_phase). Drop or mark-unreliable `track_position_seconds`. This honesty prevents engines from trusting data that isn't really there.

---

## 5. DMX-512 wire format & the Enttec Widget API

### 5.1 DMX-512 essentials
RS-485, 250 kbps, 11 bits/byte. A frame = BREAK (line low ≥88 µs) + MAB (≥8 µs high) + start code (0x00 = standard data) + up to 512 data bytes. ~22.6 ms for a full universe ⇒ **~44 Hz max**; target 30–44 Hz (below ~25 Hz flickers). 120 Ω termination at the chain end. The Enttec generates all this timing internally — you just push frames over USB.

### 5.2 Enttec DMX USB Pro Widget API (confirmed against API spec v1.44)
Every command is framed:
```
[0x7E][label][len_LSB][len_MSB][...payload...][0xE7]
```
For DMX output use **label 6** ("Output Only Send DMX Packet"). Payload = 1 start-code byte (0x00) + 512 channel bytes = **513 bytes** ⇒ len field = 513 (LSB 0x01, MSB 0x02). Total wire frame = **518 bytes**:
```
0x7E 0x06 0x01 0x02 [0x00 + 512 data bytes] 0xE7
```
Reference builder:
```python
def build_enttec_frame(universe: bytes) -> bytes:        # universe = exactly 512 bytes
    payload = b"\x00" + universe                          # start code + channels
    n = len(payload)                                      # 513
    return bytes([0x7E, 0x06, n & 0xFF, (n >> 8) & 0xFF]) + payload + bytes([0xE7])
```
Send at 30–44 Hz from a dedicated thread with a drift-corrected loop ([07 §timing](07_CROSSPLATFORM_STRATEGY.md)). Baud rate is largely a formality (USB runs full speed; the chip clocks DMX). On `SerialException`, retry/reopen — the writer thread must never crash ([05](05_ARCHITECTURE_REVIEW.md)).

### 5.3 Auto-detecting the Enttec cross-platform
The Enttec uses an **FTDI** chip (VID `0x0403`, typical PID `0x6001`). Detect via `serial.tools.list_ports` by VID/PID, not by name — `port.device` returns `COMx` on Windows and `/dev/cu.usbserial-*` on macOS automatically. **On macOS always use the `cu.*` node, never `tty.*`** (the `tty.*` node blocks on open). Detail in [07](07_CROSSPLATFORM_STRATEGY.md).

---

## 6. XDJ-XZ MIDI map (verify empirically)

The spec's CC map (from `APPENDICES/C_XDJ_XZ_MIDI_MAP.md`), reproduced as a **hypothesis**. MIDI channels are 1-indexed here / 0-indexed in `mido`.

**Mixer (the part LiveReact needs — spec says MIDI channel 6 / `mido` channel 5):**

| ShowState field | Control | CC# |
|---|---|---|
| `fader_a` | Channel 1 fader | 23 |
| `fader_b` | Channel 2 fader | 24 |
| `crossfader` | Crossfader | 31 |
| `eq_low_a` / `mid_a` / `high_a` | Ch1 EQ low/mid/high | 15 / 16 / 17 |
| `eq_low_b` / `mid_b` / `high_b` | Ch2 EQ low/mid/high | 19 / 20 / 21 |

Scale CC 0–127 → 0.0–1.0 (`v/127`); EQ neutral ≈ 64/127 ≈ 0.5; crossfader 0=full A, 1=full B.

**Why you must re-verify on hardware:**
- The XDJ-XZ's raw USB-MIDI mapping is **not well documented publicly**; much of its DJ-software integration uses **HID, not class-compliant MIDI**, so some controls you want may not appear as MIDI at all.
- CC numbers/channels vary with **firmware** and the device's **MIDI-mode setting** (must be enabled in Utility → MIDI).
- Some faders may be **14-bit** (MSB+LSB CC pairs) for higher resolution.

**Build a calibration tool, not a hard-coded map.** Ship a small "MIDI learn / monitor" panel (the spec already sketches one) that logs `channel/control/value` while you move each control, and persist the discovered map to a file. Then the CC table above is just the default to test against. (See [10](10_OPEN_QUESTIONS_AND_ASSUMPTIONS.md).)

---

## 7. Hardware bring-up checklist (when/if you get the rig)
1. Each fixture powered, address + channel mode set, passes its own self-test.
2. DMX daisy-chained in map order; 120 Ω terminator on the last fixture.
3. Enttec detected (by VID/PID); single fixture verified first (one par at addr 55).
4. XDJ-XZ: Ethernet to host, **Link enabled**; host NIC on `169.254.x.y/255.255.0.0` (static is more reliable than waiting on APIPA — see [07](07_CROSSPLATFORM_STRATEGY.md)); confirm beat/status packets arrive.
5. XDJ-XZ: USB connected, **MIDI mode enabled**; run the MIDI monitor and remap every control you use.
6. Walk the manual fixture-test panel through all 8 fixtures before running the full system.
