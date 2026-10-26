# rekordbox drive mode vs the loudness classifier — offline baseline, 2026-10-25

Same harness, same scorer, same ten ear-labelled tracks (`tracks/*.timeline.json`
without `est:` notes), `rigs/josh_rig.json`. `off` = today's classifier.
`drive` = `--phrases drive`: the track plan from the track's rekordbox `.EXT`
(phrase table + beat grid + colour waveform, found in this Mac's rekordbox
library) with the XDJ status stream synthesized on the rekordbox beat grid.
Reproduce: `tools/harness/run_sim.py <mp3> --pssi <ANLZ0000.EXT> --drive`, then
`tools/harness/score.py` on both batches.

| track | off | drive | rules that changed (off → drive) |
|---|---|---|---|
| Thriller (BVRNOUT) | 0.333 | 0.500 | STRUCT-02 F→P, TIME-11 F→P |
| Namerakana 2.0 | 0.471 | 1.000 | STRUCT-01, TIME-02, TIME-11 F→P |
| One More Time | 0.500 | 0.500 | STRUCT-03 F→P, TIME-05 P→F |
| SWEETER | 0.552 | 0.483 | STRUCT-04, TIME-05 F→P; TIME-02, TIME-11 P→F |
| Latch | 0.286 | 0.536 | LOOK-05, STRUCT-03, TIME-11 F→P |
| Somebody To Love | 0.343 | 0.229 | LOOK-05, STRUCT-02 P→F |
| Sweet Nothing | 0.333 | 0.500 | LOOK-05, TIME-02, TIME-11 F→P; STRUCT-09 P→F |
| Talk To You | 0.517 | 0.448 | STRUCT-03 P→F |
| Beautiful Now | 0.375 | 0.469 | TIME-11 F→P |
| messy (laszewo edit) | 0.517 | 0.448 | STRUCT-02 P→F |
| **mean** | **0.423** | **0.511** | |

Numbers are after the 2026-10-25 review pass (PEAK/OUTRO restraint moved into the
overlay, the planned PEAK look reachable, the roll "breath" respected by the count-in).

## What the comparison taught (measured, not assumed)

1. **rekordbox's "chorus" is where the bass comes back; Josh's "drop" is where the
   roll starts, 4 or 8 bars earlier.** On 16 ear-labelled drops every waveform
   locator (height step, red/low-band step, "bass reaches chorus level") pointed at
   the chorus boundary, i.e. the waveform agrees with rekordbox. Josh's markers sit
   exactly 4 or 8 bars before it (Somebody To Love: all three drops −4 bars;
   SWEETER, Thriller, Talk To You, Beautiful Now: −8). That is the vocabulary
   LiveReact already uses (`ROLL_START_COMMITS`: "the snare roll is where Josh's
   drop begins"). Hence the hybrid in `core/metronome.py` `_drive_from_plan`: the
   plan fixes the hit; inside `PLAN_ROLL_WINDOW_BEATS` (8 bars) before it a
   detected roll start commits the drop early with the planned hit level.
2. **The "Up" phrase's inner beat markers (`Phrase.beat2/3/4`, the "Up 3" layout)
   landed on 7 of the 16 ear drops to the tenth of a second** but are not
   consistent enough to use alone. Kept in the parser for a later rule.
3. **rekordbox emits 2–4 bar micro-phrases around hooks** (SWEETER: chorus 2 bars,
   up 2 bars, chorus). A look cannot hold on those, so `rekordbox/planner.py`
   merges consecutive same-kind phrases and absorbs interior sections shorter than
   8 bars (4 for builds) into their predecessor. This is what turned TIME-11 around
   on Thriller, Latch, Sweet Nothing and Beautiful Now.
4. **Remaining regressions are explainable:**
   - SWEETER / Talk To You STRUCT-03: the ear drop is the roll start 8 bars before
     the chorus and the offline roll detector did not fire there, so the drop look
     lands at the chorus. Live, the DJ's D key or a roll does the same job.
   - LOOK-05 / TIME-05 flips: Josh's "builds count in" rule strobes the last two
     bars before a planned drop; LIGHTING_BIBLE.md LOOK-05 says hold the strobe
     back through the build. Deliberate conflict, Josh's call; the scorer encodes
     the Bible (`BUILD_COUNTIN_BEATS` = 8 keeps it to two bars).
   - STRUCT-02 (escalating drop) / STRUCT-09 (clap-driven drop): per-drop audio
     character rules that the plan's fixed hit level does not address yet.
   - TIME-02 "'Deep Wash Breathe' held 8 bars in BREAK, needs 16" on an 8-bar
     rekordbox breakdown: the music is 8 bars long; the rule wants 16.

## Where this leaves the nightly agent

Its families 1–2 (drop detection latency, dwell and transitions) were compensating
for the classifier guessing sections late; with a plan those are table lookups.
Still worth its time: within-section execution (ramp shapes, strobe scaling,
whiteout/groove/power-off behaviour — families 3–4), `PSSI_BEAT_OFFSET` once the
hardware gate has run, the section→look mapping (`rekordbox_map.json`), and tracks
with no phrase table (the classifier is still the show there).
