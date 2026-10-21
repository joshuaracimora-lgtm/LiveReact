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
| STRUCT-04 | Whiteout: when intensity is high and kicks and claps are gone (sustained high-energy section with no low-band/snare transients), all wash fixtures go white and the moving heads turn to face the DJ. | major | COLOR channels ~equal and high on wash fixtures; MOVEMENT channels at the rig's DJ-facing pan/tilt (needs a `dj_position` in the rig/venue manifest — geometry exists in fixtures/geometry.py, confirm the field) |
| STRUCT-05 | Power-off: leaving an intense scene NOT via a beat drop, when the music goes quiet for a while (Beautiful Now 1:04.02, end of the extended intro), the rig does a shutter fade — a stepped/flickering dim-to-dark that reads as "power off" — instead of a smooth fade or a hard cut. | major | at a `breakdown` marker that follows a high-energy section with no drop in between: dimmer envelope over ~1–2 bars is stepped (multiple distinct downward steps with brief holds), ends ≤ 5%, no strobe byte |
| STRUCT-06 | Extended build that fades out (not into a drop): white, slow dim, dramatic — Beautiful Now 0:30→1:04 as the reference. | major | over the build window: hue drifts toward white, dimmer decreases slowly and smoothly, no strobe, minimal movement |

Open questions to settle before STRUCT-04/05 are scored:
- "Face the DJ": which pan/tilt is that per fixture? Likely derived from rig geometry (`x`/`y` + a DJ booth position) — add `dj_position` to the venue block.
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
| STRUCT-07 | In a `groove` section the wash bar's intensity tracks the synth band (not the kick), moving heads run at their max slew, and the laser fires a 2–4 beat burst on alternating bars. | major | correlation of bar DIMMER with the mid/high band > correlation with kick; MOVEMENT per-frame delta near the fixture's clamp; LASER-channel bursts of 2–4 beats with a 1-bar gap |
| STRUCT-08 | Strobe intensity in a roll scales with the roll's intensity: a weaker snare roll (Thriller 2:25) gets a visibly lower strobe ceiling than a strong one (Thriller 1:17). | minor | peak strobe byte over each drop window ordered the same way as the roll's snare-band energy |
