# 00 — Executive Summary

## What you've inherited

A **strong design document, not a working program.** `initial_docs/LiveReact-Portfolio/` is a meticulously written ~3,000-line portfolio describing **LiveReact**: a beat-reactive DMX lighting controller for live DJ sets. It reads musical timing from a Pioneer XDJ-XZ (Pro DJ Link over Ethernet) and the DJ's hand movements (USB MIDI), and drives an 8-fixture, 74-channel DMX rig through an Enttec DMX USB Pro at ~44 Hz. The vision is *automation by default*: the lights run a tasteful, beat-locked, phrase-aware show with no operator intervention for a full ~90-minute set.

**There is essentially no inheritable code.** The "reference" v1 snapshot contains only an entry point, a README, a version file, and two macOS launcher scripts — **none of the core logic**. The "v2 Phase 1" that allegedly built and validated the input layer is described but its **source is not included**. So in practice you are not extending anything; you are building from a spec. That is exactly the situation the spec itself recommends ("start fresh, build it cleanly in one pass").

**Bottom line:** the spec is high quality and ~85% of it is directly usable. The architecture is sound in shape. But it was written for a **single macOS machine and Python 3.14**, and several of its "locked" technical decisions are either wrong for a cross-platform build or rest on claims that don't survive scrutiny. The valuable rebuild is: *keep the architecture and the domain thinking, replace the stack-level decisions, and correct a few factual overclaims.*

## Verdict on the core question

> *Should you rebuild from scratch?* **Yes — but "from scratch" means from the spec, which is what the spec already intended.** There's no code worth salvaging, the domain analysis is genuinely good, and the architecture is a reasonable starting point. The work is real (this is a multi-week build with hardware-gated phases), but the spec front-loads it well.

## Top 10 findings & recommendations

1. **Keep Python — the ecosystem decides it.** `python-prodj-link`, `mido`, and `pyserial` have no real cross-platform equivalent in another language. Rewriting Pro DJ Link in Rust/Go/C#/TS is a large, ongoing tax for zero functional gain. *(See [06](06_TECH_STACK_EVALUATION.md).)*

2. **Pin to Python 3.11 or 3.12 — NOT the spec's 3.14.** This single decision retires the two biggest cross-platform risks at once: (a) `python-rtmidi` ships prebuilt wheels for 3.8–3.12 but **not 3.13/3.14** as of mid-2026 (newer Python → painful source builds on Windows), and (b) the high-resolution `time.sleep()` that makes a steady 44 Hz writer achievable on Windows has existed **since 3.11**. *(See [06](06_TECH_STACK_EVALUATION.md), [07](07_CROSSPLATFORM_STRATEGY.md).)*

3. **Drop tkinter.** Recommended: **PySide6 (Qt)** for the operator console + a `QGraphicsView` rig visualizer (and Qt is already pulled in transitively by `python-prodj-link`). Backup: **Dear PyGui** (GPU immediate-mode, ideal for live meters). Third option: **local web UI** if the visualizer becomes the centerpiece. tkinter has a hard ceiling exactly where this project needs headroom (60 Hz redraw + smooth visualizer + modern cross-platform look). *(See [06](06_TECH_STACK_EVALUATION.md).)*

4. **Platform: macOS-first, code kept portable (updated 2026-06).** The target is now **macOS** (your incoming dev + show machine), with the codebase kept OS-agnostic via hardware abstractions so a Windows build stays a *low-cost future option* rather than maintained work. This retires the cross-platform **testing/support/packaging** burden while costing almost nothing in code — and you can still develop demo mode on Windows in the interim. The Windows-specific gotchas that would otherwise dominate — link-local/APIPA + Firewall UDP for Pro DJ Link, single-client MIDI, timer resolution — are now **revival reference** in [07](07_CROSSPLATFORM_STRATEGY.md), not active work. (macOS keeps its own, smaller gotchas: use the serial `cu.*` node; sign + notarize the `.app`.)

5. **Reality-check the Pro DJ Link data model.** The XDJ-XZ provides BPM, beat-within-bar (1–4), on-air, master, loop, and play state directly. But **bar number must be inferred**, **musical phrase is not transmitted at all** (so the "Phrase Engine" actually rotates on *inferred* 8/16-bar counts, not real phrase detection), **beat phase must be interpolated locally**, and **absolute track position is not available on the XDJ-XZ** (that's a CDJ-3000 feature). None of this breaks the design, but the naming oversells what the hardware gives you. *(See [04](04_HARDWARE_AND_PROTOCOLS.md).)* *(Revised 2026-10-25: song structure now comes from rekordbox's phrase tables on the USB, indexed by the status packet's track id + beat counter -- docs/13 D-21, docs/04 §4.1.)*

6. **`python-prodj-link` is usable but needs hardening.** It's lightly maintained (last commit ~May 2025), git-only (not on PyPI), and auto-guesses the network interface in a way that fails on multi-homed Windows laptops. Plan to (a) patch in an explicit interface/IP override, (b) replace the fragile `netifaces` dependency, and (c) build your own local-metronome + staleness layer regardless. If show-grade reliability becomes paramount, the Java **beat-link** library run as a sidecar is the better-maintained foundation. *(See [04](04_HARDWARE_AND_PROTOCOLS.md), [06](06_TECH_STACK_EVALUATION.md).)*

7. **The architecture is good; tighten three things.** Keep the dual-input → ShowState bus → parallel engines → merge → DMX shape and the threaded model. Add: **double-buffering** of the DMX universe (writer never sees a half-written frame, never stops), **queue-based message passing** for inputs (instead of listener threads poking shared state directly), and an **injected clock** so real-time logic is testable without hardware or real time passing. *(See [05](05_ARCHITECTURE_REVIEW.md).)*

8. **Correct the frequency→color rationale, but keep the rule.** The claim that "the brain pairs bass with red" is **overstated**: perception research robustly supports pitch/energy → *brightness* (high/loud = brighter/whiter — cross-cultural, even cross-species), but the specific *hue* mapping (bass=red, high=cyan) is an **aesthetic convention, not neuroscience**. Keep it "locked" because *consistency* is what makes automation feel authored — just don't encode the false scientific claim. *(See [08](08_LIGHTING_DESIGN_AND_SAFETY.md).)*

9. **The safety posture is correct and load-bearing — confirmed against regulations.** Lasers boot OFF, software never auto-enables them outside explicit drop scenes, never uses the fixture's own sound-active modes, never audience-scans (3 W lasers pointed at people would require an FDA variance + certified safety engineering; keep beams above ~3 m / head height per FDA 21 CFR 1040.11(c) and ILDA). Strobe should be capped near the ~3-flashes/second known-safe line (WCAG 2.3.1 / Epilepsy Foundation), rationed to short drop-only bursts, with extra caution on saturated-red flashing. *(See [08](08_LIGHTING_DESIGN_AND_SAFETY.md).)*

10. **Build demo-first and make it a first-class mode.** Hardware access is uncertain, and the whole engine/scene/UI layer can be built and iterated with a synthetic data source and an on-screen virtual rig. Add an **Art-Net/sACN output backend** alongside the Enttec serial driver as OS-agnostic insurance and to enable testing against software visualizers (QLC+, Resolume). *(See [11](11_RECOMMENDED_APPROACH.md), [12](12_REBUILD_ROADMAP.md).)*

## The one-paragraph recommended approach

Rebuild in **Python 3.12**, threaded, with a clean shared-state core (immutable snapshots, single-writer-per-field, queue inputs, double-buffered DMX output, an injected clock). Use **PySide6** for the console and a 2-D rig visualizer. Abstract output behind an interface with **Enttec-serial** and **Art-Net/sACN** backends. Use **`python-prodj-link`** (hardened) for timing and **`mido`+`python-rtmidi`** for MIDI, each behind an interface so a `DemoDataSource` can stand in. Build **demo-first**: get the full console + virtual rig + scene catalog + engines working with synthetic data before touching hardware, then swap inputs/outputs to real devices in a hardware phase. Keep the spec's frequency→color rule and safety rules; correct the rationale; treat all channel maps and MIDI CCs as "verify on hardware." Full plan in [11](11_RECOMMENDED_APPROACH.md) and [12](12_REBUILD_ROADMAP.md).

## What to read next

- If you want the **gap analysis** of what exists → [01](01_PROJECT_STATE_ASSESSMENT.md).
- If you want the **stack decisions and why** → [06](06_TECH_STACK_EVALUATION.md).
- If you want a **concrete plan to start building** → [11](11_RECOMMENDED_APPROACH.md) + [12](12_REBUILD_ROADMAP.md).
- If you want every **deviation from the spec** in one place → [13](13_DECISION_LOG.md).
