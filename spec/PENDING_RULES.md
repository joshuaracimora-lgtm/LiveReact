# spec/PENDING_RULES.md — design rules from Josh not yet in LIGHTING_BIBLE.md

Source: listening sessions while labeling `tracks/*.timeline.json` (2026-10-20).
Each becomes a bible rule with a stable ID, then a scorer check (expected to
FAIL on day one), then engine work. Scorer first, code second — the failing
verdict is the spec. IDs below are proposals; renumber when merged into the
bible so they don't collide with existing `TIME-*`/`SAFE-*` rows.

| Proposed ID | Rule | Severity | Scorer measures |
|---|---|---|---|
| STRUCT-01 | A `fill` (energy entry that is not a drop — tool tracks like Namerakana, One More Time's first sections) must NOT trigger strobe or any DROP look. | critical | strobe byte == 0 and no DROP-family scene for the 8 bars after each `fill` marker |
| STRUCT-02 | A drop marked as escalating (long drop, quiet start — messy 2:08) builds strobe/intensity progressively, like the snare roll, rather than slamming at full. | major | strobe byte and dimmer trend over the drop window is monotone-rising, first-quarter mean < last-quarter mean |
| STRUCT-03 | Drop reaction latency: the engine's own drop detection should fire within N beats of the labeled drop start. | major | time from `drop.t` to first `drop_marker`/DROP bucket; reported as a number, threshold TBD (start: 2 beats) |
| STRUCT-04 | Whiteout: when intensity is high and kicks and claps are gone (sustained high-energy section with no low-band/snare transients), all wash fixtures go white and the moving heads turn to face the DJ. | major | **Scored (v2, 2026-10-21)** over the `whiteout` marker's window (to the next marker, max 2 bars, first beat skipped): wash COLOR white (W ≥ 0.6, or R≈G≈B ≥ 0.6 within 0.15) with dimmer ≥ 0.6 for ≥ 75 % of the window; strobe byte 0; each head ends within 0.03 of its DJ pose or closes half its distance. The DJ pose is the slot's optional `dj_pan_tilt` in the rig JSON, else derived from a `dj_booth` venue target when the head is placed and aim-calibrated (`Rig.dj_pose`); not configured → that sub-check NOT_SCORED, never guessed |
| STRUCT-05 | Power-off: leaving an intense scene NOT via a beat drop, when the music goes quiet for a while (Beautiful Now 1:04.02, end of the extended intro), the rig does a shutter fade — a stepped/flickering dim-to-dark that reads as "power off" — instead of a smooth fade or a hard cut. | major | **Scored (v2, 2026-10-21, placeholders)**: at a `breakdown` marker that is not a drop's cut and where some fixture averaged ≥ 15 % in the bar before: on the lit-fixture mean dimmer, the first step lands within 2 bars, the rig is ≤ 5 % within 2 bars of it, 3–6 distinct steps (falls ≥ 5 % within 3 ticks, ≥ 3 ticks apart), no strobe byte during the fade |
| STRUCT-06 | Extended build that fades out (not into a drop): white, slow dim, dramatic — Beautiful Now 0:30→1:04 as the reference. | major | **Scored (v2, 2026-10-21, placeholders)**: a `build` marker followed by a `breakdown`, or the 16 bars before a `breakdown` whose note says "fade": lit-fixture mean dimmer falls ≥ 0.10 first→last quarter with no per-tick jump > 0.05 and ≤ 10 % of ticks rising; lit wash chroma ends ≤ 0.15 or falls by 0.10; no strobe; head delta ≤ 0.01/frame |

Open questions (STRUCT-04/05 are now scored with first-guess tolerances; these still need a human answer):
- "Face the DJ": settled as data, not geometry guesses — a slot's optional `dj_pan_tilt` (raw 0..1), or derived via `geometry.aim()` only from a `dj_booth` venue target plus a captured calibration. Josh's rig has neither yet, so the head sub-check reads NOT_SCORED until he captures the pose.
- "Kicks and claps are gone": define as low-band transient rate < X/bar AND snare-band onset rate < Y/bar for ≥ 2 bars while energy_norm stays > 0.7.
- Shutter fade shape: how many steps, over how many bars? First pass: 3–6 steps over 1–2 bars.

## Vocabulary from the labeling sessions (Thriller BVRNOUT, Beautiful Now, Oct 21 2026)

Josh's model of a tech-house drop, in lighting terms, has three parts the
engine currently collapses into one "DROP":

| Phase | Marker kind | What the lights do |
|---|---|---|
| **roll** — the snare roll / in-the-roll kick leading into the slam | `drop` (with `end`) | strobe starts low-brightness and escalates to the end of the roll (STRUCT-02); a weaker roll gets a proportionally weaker strobe |
| **cut** — drums drop out for 1–2 bars while the track stays intense | `whiteout` | strobe → white out, moving heads to the DJ (STRUCT-04). Also fires on stab/synth-carried sections with no drums (Thriller 0:27) |
| **groove** — the main full-energy section that lands after the cut | `groove` (with `end`) | wash bar synth-synced (vibrating with the synths), moving heads at full speed, lasers fire a 2–4 beat burst every other bar |

| Proposed ID | Rule | Severity | Scorer measures |
|---|---|---|---|
| STRUCT-07 | In a `groove` section the wash bar's intensity tracks the synth band (not the kick), moving heads run at their max slew, and the laser fires a 2–4 beat burst on alternating bars. | major | **Scored (v2, 2026-10-21)** per `groove` window ≥ 2 bars: (a) corr(bar DIMMER, band_mid or band_high) ≥ 0.15 and ≥ corr(bar, band_low) + 0.05; (b) on some head, ≥ 50 % of frames move an axis at ≥ 50 % of the profile's slew clamp; (c) in windows ≥ 4 bars, ≥ 2 LASER bursts, median length 1.5–4.5 beats, median start-to-start 5–10 beats |
| STRUCT-08 | Strobe intensity in a roll scales with the roll's intensity: a weaker snare roll (Thriller 2:25) gets a visibly lower strobe ceiling than a strong one (Thriller 1:17). | minor | **Scored (v2, 2026-10-21)**: needs ≥ 2 roll drops (clap-driven ones excluded). Rolls ordered by Josh's note ("weak") when present, else by mean band_high over the drop window (ratio ≥ 1.15 to count as different); every clearly-stronger roll must peak ≥ 20 strobe bytes above every weaker one |
| STRUCT-09 | Clap-driven drop (SWEETER 1:12, 3:16): no snare roll — synths + claps/crashes carry it. Hit on the claps/crashes; a soft, slower strobe at most; never the roll strobe. Josh writes it in the drop note ("react to the claps", "no snare roll"). | major | **Scored (v2, 2026-10-21)**: `drop` markers whose note says "claps" / "crashes" / "no snare roll" / "react to the clap…" (plural on purpose: messy's "starts on the clap" is a roll): peak strobe byte ≤ 96; no roll behaviour (strobe not escalating across the window nor ramping inside a burst ≥ 1 beat); corr(rig dimmer up-flux, band_high up-flux) ≥ 0.15 at the best 0–3 tick lag (hits land on the claps) |
