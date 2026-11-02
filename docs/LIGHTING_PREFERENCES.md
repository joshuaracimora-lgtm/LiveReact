# Josh's lighting preferences — the yardstick (2026-10-30)

This file is what LiveReact's lighting is measured against. Every line is something Josh said, watching the
real rig or the simulator, with the date and the decision that encodes it (`docs/13_DECISION_LOG.md`).
It replaces "the Bible says so" as the test of whether a show is right: where this file and
`spec/LIGHTING_BIBLE.md` disagree, this file wins, and the disagreement is recorded in the log.

Rules for whoever reads this (agents included):

- A preference is a **check**, not a licence. Verify the show against it; report every violation with the
  track, the time and what the lights did. Fix only a violation. Never add a look, a scene, a strobe or
  laser behaviour, a palette or a class that this file does not call for. "Josh never approved it" is the
  default state of anything not written here.
- `tracks/<song>.look.json` (`docs/LOOK_NOTES.md`) is Josh's word on ONE track and outranks this file for
  that track.
- An open question at the bottom stays open until Josh answers it. Do not guess; do not build around a guess.

## What drives the lights (P-01..P-05)

| # | Preference | Check in the sim / recording | Decision |
|---|---|---|---|
| P-01 | **The track plan is the structure.** Sections, drops, the big moment, rolls, strobe style and accent come from the rekordbox phrase table and grid at load, over Pro DJ Link. The plan is proof the music is playing: with no audio the show still runs the plan, the heads sit at the planned energy, the lasers may fire. | `plan_active` true for a planned track; buckets follow `section_kind`; with `audio_fresh` false the lasers still open on the big moment and `energy` follows `PLAN_ENERGY`. | D-21, D-33 |
| P-02 | **Audio is the deck's own feed, never the room.** The mic is not a lighting input. The XDJ-XZ over USB is the only audio source; its job is the per-hit layer (stabs, wubs, roll detection, measured hit). | `--audio-device` names the XDJ or nothing. A recording with the mic as source is invalid evidence. | 2026-10-29 |
| P-03 | **Nothing reacts to small wobbles.** Synth Sync holds the section level and lifts only on a stab above the threshold (default 0.6). | `wash_level` flat between stabs; lifts only when `synth_level` crosses `synth_threshold`. | D-32 |
| P-04 | **Less latency is always right.** A planned look change starts on the boundary tick; a dwell or hold exists to stop flapping, never to delay a real cut. | Scene change begins within one tick of the planned boundary (the 1-bar crossfade may follow). | D-34 |
| P-05 | **Never rely on DJ cues to find structure** (D-24). One scheme per track: one palette, one hook look. | — | D-24, D-27 |

## Builds, rolls and strobes (P-10..P-17)

| # | Preference | Check | Decision |
|---|---|---|---|
| P-10 | **A build that leads into an intense drop strobes through its snare roll**; the roll is planned from the phrase table (8 bars before the chorus boundary, 4 on a crescendo), not detected live. Thriller's rolls land within 0.02 s of Josh's ear notes. | Roll sections exist before every qualifying drop; the strobe runs the whole roll. | D-33 |
| P-11 | **Only intense drops get a lead-in.** A drop under 90 % of the song's loudest drop (waveform energy) gets no roll strobe and no count-in; the build before it stays quiet. Josh: "that buildup isn't building up to a high-intensity part." | Lesser drops: no roll section, `strobe_allowed` false on the build before them, zero strobe transitions there. | D-36 |
| P-12 | **The roll strobe matches the snare roll.** Flashes lock to the beat grid: 8th notes for the first half of the roll, 16ths for the second (~4.5 → ~9 Hz at 135 BPM). Brightness climbs from dim (byte 40) to full (255). **This knowingly exceeds the Bible's 4 Hz sustained line (SAFE-01) inside planned rolls; the 10-25 Hz band is never commanded.** | Flash rate in the first half ≈ 2 × beat Hz, second half ≈ 4 × beat Hz; first ON byte ≈ 40, last ≈ 255. | D-35 |
| P-13 | **No count-in into a roll.** The roll is the count-in. A 2 Hz→4 Hz count-in followed by a roll restarting at 2 Hz read as "build up into a drop into a build up". | Zero strobe in the last two bars of a build whose next drop boundary is a roll. | D-34 |
| P-14 | **No strobe in intros, outros or breaks, with or without a plan.** With a plan the live roll detector may accent only in a build within 8 bars of its drop, where the plan allows strobe; inside a drop only the planned strobes fire (run 6: it heard "rolls" in Thriller's vocals and hats). With no plan (first track before Pro DJ Link connects, a plan loss) it may accent only in a build / drop / peak while `energy_norm` ≥ 0.5 — run 6 strobed Thriller's intro at 0.00: "There should never be strobing like that in the intro, especially when intensity is that low." | Zero strobe transitions in planned intro / outro / breakdown sections; zero live-roll accents inside planned drops; zero no-plan strobe in BREAK/OUTRO or under 0.5. | D-35, D-41 |
| P-15 | **Flash colour stays in the song's gradient.** A flash is the song colour lifted toward white by the song's brightness; dark bass-heavy songs flash in their colour, bright songs near white. Never a jump from the wash colour to pure white. | ON-frame RGB = `flash_colour(palette, flash_white)`; W channel = the white share. | D-36 |
| P-16 | **Inside a drop the strobe is the song's style** ("tail" / "stab" / "downbeat" / "sparse" / "none" per song), one moment per phrase, with the accent flash on the song's accent beat. Josh on Thriller: the tail strobe through the drop "I guess is fine". | STROBE_STYLES; accent beats from `planned_accent`. | D-30 |
| P-17 | **A planned build between two intense parts holds a steady mid level in the song's companion colour.** No white, no rise: the rise belongs to the roll strobe. Synth Sync does not touch a planned build (run 6: its off-beat spikes made the riser "not really in sync with the music"). | Build wash ≈ 0.45, colour = `COMPANION[song]`, heads in the song colour; no Synth Sync level in a planned build. | D-35, D-36, D-41 |
| P-18 | **A strobe never cuts the rig dark, and never flips its colours.** Between flashes the wash holds a dim floor in its colour (`STROBE_GAP_LEVEL` 0.42, byte ~38); the heads keep the Tease colour on flash frames; a planned section shorter than 2 bars keeps the colours it came in with. Run 6: "The lights would just cut out and turn to blue or white"; Thriller's 4-beat "up" flipped drop → build → drop colours in one bar ("it was just doing too much"). | No wash dimmer byte 0 between flashes; no palette change for a section under 8 beats. | D-41 |

## Breaks, risers and cues (P-20..P-24)

| # | Preference | Check | Decision |
|---|---|---|---|
| P-20 | **The rising wash belongs only to a break with no kicks.** Plan says break AND the low band is quiet → the wash rises across the section in the break's companion colour (not white). With kicks, the break breathes low in the companion colour. | `planned_riser` only when `planned_bucket == "break"` and the bass follower is under `QUIET_BASS_ABS`; palette unchanged. | D-35, D-36 |
| P-21 | **Never a random intense scene that does not match what is around it.** Josh on an early white: "extremely bright and didn't match what was beforehand and after." | Any full-white or full-intensity hold must be a look-note cue or a flash in the song's tint. | 2026-10-30 |
| P-22 | **Josh's ear notes drive the show.** A look note's timed `cues` (whiteout at a time) override the frame for their beats, in the song's flash tint; `laser_scene` / `laser_cues` pick the laser look; `big_moment`, `strobe`, `intro`/`outro` flags are orders. | Cue windows land on the first grid beat at or after the note's time; colour = the flash tint. | D-34, D-36 |
| P-23 | **Colour follows the track's frequency profile** (high = bright, low = dark, middle = blue), two colours per song (the song colour and its companion), dark to light across the library. A drop's look never carries into a break. | `palette` per bucket from `song_pair`; D-28 ordering. | D-23, D-27, D-28 |
| P-24 | **Tease: the pre-drop scene (Josh's name; the Bible's "whiteout").** Kicks and claps leave while the track keeps building toward a drop → the rig starts DIM in the song colour (its drop colour) and brightens with the music, drifting toward white, ending at the strobe flash tint. Before any drop at least **70 %** of the song's loudest ("Tease loose, strobe strict": the roll strobe keeps D-36's 90 %; before a lesser drop the Tease hands straight into the drop, no strobe). Only on an INTENSE song: its drops' mean waveform height >= 0.53, the top ~40 % of the library (Josh: "the justification for a tease [is] the intensity of the song"); a song under it keeps the P-20 riser; ear-note cues ignore the gate. Never in the outro; in the intro by the same rule. At most the last 16 bars before the drop. Brightness CLIMBS ON BAR POSITION (the Bible's LOOK-04; Josh: "The Bible is probably correct") from dim to its ceiling, and the music NUDGES it a little either way ("Ramp + music nudges it"). A cue Tease that starts inside a drop (Thriller 1:03-1:17, "where most of those stabs live, so it can be bright there") is BRIGHT and dips between the ~1 s stab bursts. Stabs and lone hits (Thriller's "Thriller" stabs, Tongue Tied 1:22.8) PUNCH: fast up, then "it shutters down when it cools down off the punch" -- a smooth decay, never a step. When a roll follows, the wash HOLDS the Tease's level under the strobe (60 % of it between flashes, so the flashes stay visible over a bright hold) and the pre-drop blackout beat is skipped ("it cant jump from intense to not intense"). No lasers and no pre-drop strobe inside a Tease. A drop that misses 70 % keeps the P-20 companion riser. Ear notes can add one: `{"look": "tease", "t": s}` runs to the next drop (for stretches the waveform cannot see, e.g. Thriller 27.2 s, where the stabs keep the bass channel full). Example: Tongue Tied (TWINSICK) Tease 1:05-1:28.7 → its 82 % drop (no strobe); 1:42.5-1:52.3 → roll → 2:00. | Tease window = kick-free bars (waveform red at the beat onsets <= 4.5) ending at a drop or its roll, drop energy >= 0.7 x loudest, no outro, <= 64 beats; level starts <= floor + 30 % of range; punch decays monotonically; wash under the roll >= the hold level; no black frame between Tease and roll; laser off and no strobe outside the roll. | 2026-10-30 (Josh), D-39 |

## Lasers (P-30..P-34)

| # | Preference | Check | Decision |
|---|---|---|---|
| P-30 | **Automatic, never cued by hand during the set.** Armed once per night (L); then the fan engine fires itself. | `laser_armed` true all set; moments open without `laser_hit`/`laser_cue`. | 2026-10-29 |
| P-31 | **Only at the most intense parts.** The big moment's drop and its PEAK; a regular drop (hit < 0.95) never fires; nothing where the plan forbids the laser (intro/outro); nothing before a drop has landed. | Every auto moment inside DROP/PEAK of the big moment. | D-27, D-33 |
| P-32 | **While the peak holds: 2 bars on, 4 bars off.** "If it fires for 2 bars, it should fire again in another 4 bars, while the energy is still high." | Moments at 6-bar blocks from the drop origin, 2 bars long, 4-bar gap. | D-34 |
| P-33 | **A moment stays inside its section.** No new moment in the last bar of a section; a moment ends the instant the bucket leaves DROP/PEAK. | No lit frame in a planned build / break after a drop ends. | D-35 |
| P-34 | **Colour from the palette, scene from the notes.** CH12 follows the show palette (the laser matches the other fixtures); the pattern is the song's `laser_scene` / `laser_cues` from the library in `scenes/laser_scenes.json`, default Rainbow. | `laser_color` = `PALETTE_TO_LASER[palette]`; `laser_scene` from the notes. | D-27, D-31 |

## Things Josh has NOT approved (do not build, do not propose as done)

- New scenes, looks, classes with scenes, palettes, movement shapes, or strobe/laser behaviours not listed above.
- Any change to what a scene renders "because the scorer went up". The scorer (`sim/score.py`) still carries
  Bible hold rules (TIME-11 8-bar dwell, TIME-02 16-bar look hold) that this file does not endorse; a FAIL
  there is not, by itself, a reason to change the show. (Rewriting the scorer to this file is a planned job.)
- Knob-search results: no `best_params.json` has ever been adopted; do not adopt one without Josh.
- Reacting to the room microphone in any way.

## Known violation to fix first (found by the preference pass, 2026-10-30)

**The no-plan fallback breaks P-14 and P-31 on almost every track.** (2026-10-30 run 6, D-41: the strobe half is fixed -- no no-plan strobe in a break/outro or under energy_norm 0.5. The early classifier DROP and the lasers are not.) Replaying all 29 local tracks with no
phrase table (the state the rig is in whenever Pro DJ Link is down or a track has no rekordbox phrase
analysis): the audio classifier calls DROP/PEAK a median 19 s into the song (min 3.7 s) from a cold energy
normaliser, the lasers fire inside the first minute on 21 of 29 tracks and repeat every ~11 s, and 21 of 29
strobe through the intro, 27 of 29 through the outro. With the plan on, the same checker is clean. Until the
fallback is fixed (no fan, no strobe for the first N bars / until a real drop confirms; a warm normaliser),
a plan loss mid-set degrades into exactly the show Josh does not want.

## Open questions for Josh (unanswered: do not guess)

- Thriller's groove note asks for a laser burst "every other bar" (denser than P-32). Per-song cue, or a new general rule?
- Should a lesser drop (P-11) show anything at its boundary beyond landing as a drop?
- The strobe's brightness ceiling on the real bar: the dimmer curve (gamma 2.2, floor 31) makes bytes above ~160 look alike. Measure on the fixture before tuning further.
- Hub reliability (Enttec, Ethernet adapter and XDJ all dropped off USB on 2026-10-30): LiveReact should re-detect ports and show an operator error; not yet built.
