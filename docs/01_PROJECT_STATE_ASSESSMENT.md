# 01 — Project State Assessment

Goal of this document: tell you exactly **what you actually have**, separate **real artifacts from aspirational prose**, and map the **gaps** between "what's described" and "what exists."

---

## 1. What physically exists in `initial_docs/`

```
initial_docs/LiveReact-Portfolio/
├── 00_README.md                 (107 lines)  — project map, constraints, "done" definition
├── 01_DOMAIN_PRIMER.md          (290 lines)  — DMX/PDL/MIDI/lighting primer
├── 02_HARDWARE_RIG.md           (266 lines)  — devices, wiring, DMX address map, channel maps
├── 03_ARCHITECTURE.md           (499 lines)  — v2 design, threading, ShowState, engines, merge, UI
├── 04_DESIGN_PRINCIPLES.md      (259 lines)  — locked decisions, v1→v2 story, anti-patterns
├── 05_BUILD_PLAN.md             (258 lines)  — phased roadmap 0→9, acceptance per phase
├── 06_TESTING_AND_SHOW_DAY.md   (296 lines)  — test pyramid, bring-up, show-night ops
├── APPENDICES/
│   ├── A_DMX_512_PROTOCOL.md    (211 lines)  — wire format + Enttec Widget API
│   ├── B_PRODJLINK_PROTOCOL.md  (181 lines)  — PDL networking + library notes
│   ├── C_XDJ_XZ_MIDI_MAP.md     (199 lines)  — CC map (verified on Josh's unit)
│   ├── E_GLOSSARY.md            (183 lines)  — terms
│   ├── architecture_v2_poster.pdf            — one-page architecture diagram
│   └── D_FIXTURE_MANUALS/
│       ├── README.md            (21 lines)
│       └── SHEHDS_180W_Moving_Head_Manual.pdf
└── REFERENCE/
    ├── README.md                (22 lines)
    ├── v2_phase1_notes.md       (61 lines)
    └── v1_codebase/
        ├── README.md            (150 lines)  — v1 setup/feature guide
        ├── NOTES.md             (51 lines)   — what v1 was / kept / replaced
        ├── run.py               (28 lines)   — entry point only (imports ui.control_panel)
        ├── version.py           (7 lines)    — "0.1.0-BETA", build 2026-04-08
        ├── LiveReact.command    (106 lines)  — macOS double-click launcher (auto-detect)
        └── LiveReact_Setup.command (110 lines) — macOS first-time setup (brew/portaudio/pip)
```

**Total: ~3,000 lines of documentation, 5 lines of real Python logic, 2 macOS shell scripts, 2 PDFs.**

---

## 2. The crucial distinction: documentation vs. code

This is the single most important thing to internalize about the handover.

| Claimed to exist | Actually present? | Notes |
|---|---|---|
| v1 application (audio-FFT, ran real shows) | ❌ **Logic absent** | Only `run.py` (28 lines, just imports `ui.control_panel.main`), `README.md`, `version.py`, and 2 launchers. The `core/`, `audio/`, `dmx/`, `fixtures/`, `scenes/`, `ui/` modules referenced by `run.py` are **not in the snapshot**. `v1_codebase/NOTES.md` openly admits "the snapshot here doesn't include those modules." |
| v2 Phase 1 (input layer + ShowState, "built and tested") | ❌ **Not included at all** | `v2_phase1_notes.md` says the source "exists — Josh has it" but was deliberately withheld so the new dev rebuilds cleanly. |
| Architecture, channel maps, MIDI map, design rules | ✅ **Present (as prose)** | High quality, but unvalidated by a running build in this package. |
| Fixture manuals | ⚠️ **Partial** | Only the SHEHDS 180W moving-head PDF. Laser and wash-bar manuals were "not retained"; their channel maps live only in `02_HARDWARE_RIG.md`. |

**Implication for you:** Since you have *no* code and *no* Josh, the prose is your only source of truth, and some of it is "verified on Josh's unit" (channel maps, MIDI CCs) — meaning it was true for one specific device at one firmware revision. Treat those as **starting hypotheses to verify on hardware**, not gospel.

---

## 3. Maturity assessment by area

| Area | Spec maturity | Code maturity | Confidence the spec is right |
|---|---|---|---|
| Domain understanding (DJ sets, lighting) | Very high | n/a | High — corroborated by external research ([08](08_LIGHTING_DESIGN_AND_SAFETY.md)) |
| Hardware rig / DMX address map | High | n/a | Medium-High — internally consistent; *verify fixture channel modes on hardware* |
| DMX-512 / Enttec protocol | High | n/a | High — framing confirmed against Enttec API spec v1.44 |
| Pro DJ Link concept | High | n/a | Medium — concept right, but **data-availability is overstated** (see [04](04_HARDWARE_AND_PROTOCOLS.md)) |
| MIDI map | Medium | n/a | **Low-Medium** — "verified on Josh's unit"; XDJ-XZ MIDI mapping is poorly documented publicly and may even prefer HID for some controls. *Empirically remap on hardware.* |
| Architecture (engines/bus/merge) | High | n/a | Medium-High — sound shape; needs the refinements in [05](05_ARCHITECTURE_REVIEW.md) |
| Tech stack (Python 3.14 + tkinter, macOS) | Stated as locked | n/a | **Low** — wrong for cross-platform; see [06](06_TECH_STACK_EVALUATION.md) |
| Scenes / lighting content | Medium (described, not designed) | None | n/a — needs real design work; starter catalog in [08](08_LIGHTING_DESIGN_AND_SAFETY.md) |
| Testing strategy | High | None | High — good plan; demo-first is right |

---

## 4. What the spec gets genuinely right (keep these)

- **The v1→v2 pivot rationale.** Audio FFT for beat/energy/transition detection *is* unreliable in real DJ sets (limiters compress dynamic range; filter sweeps kill the bass that beat detection relies on). Moving to structured inputs (PDL + MIDI) is the correct call. External research confirms this is exactly why naive audio-reactive rigs struggle.
- **The shared-bus + parallel-engines decomposition.** Reduces coupling from ~12 possible edges to a handful, and makes engines independently testable. Sound.
- **Additive layering / "manual is a bias, not an override."** Good real-time design; keeps the show alive when the operator walks away.
- **The anti-pattern catalog** (read-then-overwrite, epsilon-init normalization, tkinter Canvas-before-mainloop, reserved-attribute collisions, multi-field tearing, beat-jitter-as-clock). These are real, hard-won bugs; honor them.
- **Demo-mode as a first-class development substrate.** Correct and central to a hardware-uncertain rebuild.
- **The safety rules** (laser boots off, strobe caps). Confirmed correct against regulation ([08](08_LIGHTING_DESIGN_AND_SAFETY.md)).

---

## 5. What the spec gets wrong, oversells, or leaves dangerous (fix these)

| # | Issue | Why it matters | Where addressed |
|---|---|---|---|
| 1 | **macOS-only assumptions throughout** (`/dev/tty.usbserial-*`, `.command` launchers, `ifconfig`, `en7` interfaces) | macOS is fine as the target, but to keep the code portable these OS-touching details must live behind abstractions, not be hard-coded into logic | [07](07_CROSSPLATFORM_STRATEGY.md) |
| 2 | **Python 3.14 target** | Breaks `python-rtmidi` (no 3.13/3.14 wheels in 2026) and `netifaces`; 3.11/3.12 is strictly better here | [06](06_TECH_STACK_EVALUATION.md) |
| 3 | **tkinter** | Hard ceiling on a 60 Hz console + smooth visualizer; dated cross-platform look | [06](06_TECH_STACK_EVALUATION.md) |
| 4 | **"Phrase Engine" implies phrase detection** | Phrase data is **not transmitted** by PDL; you infer bars by counting downbeats. The engine rotates on bar counts, not detected phrases | [04](04_HARDWARE_AND_PROTOCOLS.md), [05](05_ARCHITECTURE_REVIEW.md) |
| 5 | **"bass=red because the brain says so"** | Overstated science; the hue mapping is convention, not neuroscience. Keep the rule, fix the rationale | [08](08_LIGHTING_DESIGN_AND_SAFETY.md) |
| 6 | **`python-prodj-link` treated as turnkey** | Lightly maintained, git-only, weak interface auto-detection, fragile deps on new Python | [04](04_HARDWARE_AND_PROTOCOLS.md), [06](06_TECH_STACK_EVALUATION.md) |
| 7 | **MIDI map presented with confidence** | XDJ-XZ MIDI is under-documented and firmware/mode-dependent; some controls may be HID | [04](04_HARDWARE_AND_PROTOCOLS.md), [10](10_OPEN_QUESTIONS_AND_ASSUMPTIONS.md) |
| 8 | **Absolute `track_position_seconds` in ShowState** | Not available on the XDJ-XZ; don't design features that depend on it | [04](04_HARDWARE_AND_PROTOCOLS.md) **Revised 2026-10-25:** beat-resolution position IS available -- the CDJ status packet's `beat_count` (unicast once we announce, `inputs/pdl_announce.py`) -> `ShowSnapshot.track_beat`; sub-beat seconds still not something to build on. |
| 9 | **No persistence at all** | Defensible for v1, but losing scene tuning between runs will hurt during iteration; consider a tiny config/preset file | [05](05_ARCHITECTURE_REVIEW.md), [10](10_OPEN_QUESTIONS_AND_ASSUMPTIONS.md) |

---

## 6. The "no Josh, no code" multiplier

Two handover assumptions in the spec are now false for you, and they change the plan:

- The spec repeatedly says *"ask Josh"* for domain calls, hardware quirks, the v1/v2 source, and anything blocking >2 hours. **You have none of that.** Every such question must be answered by research + reasoned assumption. They are collected in [10](10_OPEN_QUESTIONS_AND_ASSUMPTIONS.md) with my best-guess working answers so you're never blocked waiting for a person who isn't coming.
- The spec assumes you can crib data structures and fixture profiles from the v1/v2 source "if useful." **You can't.** Everything must be reconstructed from the channel maps in `02_HARDWARE_RIG.md`. The good news: those maps are complete enough to build from.

---

## 7. One-screen summary

- **You have:** an excellent spec, a partial-and-logic-free v1 snapshot, two PDFs, no usable code, no author.
- **You're really doing:** a clean-room build from a strong specification — which is precisely what the spec asks for.
- **The spec is ~85% reusable.** Keep the domain analysis, architecture shape, design principles, and safety rules. Replace the stack decisions and the macOS-only operational layer. Correct a handful of factual overclaims.
- **Next:** read [02](02_GOAL_AND_REQUIREMENTS.md) for the distilled requirements, then [06](06_TECH_STACK_EVALUATION.md) for the decisions that unblock building.
