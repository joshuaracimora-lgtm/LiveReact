# LiveReact nightly agent — brief (planner era, 2026-10-25)

You are a Claude Code session running unattended overnight on Josh's Windows desktop, in the LiveReact
repo. The orchestrator (`tools/windows/nightly_agent.ps1`) has already pulled `harness`, run the tests,
run the nightly **sweep** (`results/agent/<date>/SWEEP.md`), and created a fresh branch for you. Josh is
asleep: nobody can answer a question tonight, so anything you cannot decide goes in the report, not in a
guess. Your job is one night of **real improvement to how LiveReact understands a track**, measured by the
scorer, verified by the gate, left as reviewable commits on your branch.

The runtime header above this brief gives tonight's date, branch, start time, deadline and the sweep's head.

**Shell:** your Bash tool is Git Bash on Windows. Use forward slashes and the venv interpreter for every
python command: `.venv/Scripts/python.exe -m pytest -o addopts= -q`, `.venv/Scripts/python.exe tools/agent/status.py`.
(`-o addopts=` makes pytest print the `N failed, M passed` count; the repo's default `-q` hides it.)
PowerShell cmdlets are not available; you do not need any.

## What changed (why this brief is different from the classifier-era one)

LiveReact no longer guesses sections from loudness when a rekordbox track is loaded. The XDJ-XZ's status
packets name the track and its beat; the USB holds rekordbox's phrase table, beat grid and colour
waveform; `src/livereact/rekordbox/planner.py` plans the whole track at load and `core/metronome.py`
`_drive_from_plan` executes it (`--phrases drive`). The audio classifier is the fallback. Your target is the
**planner's audio understanding**: where it places sections and scenes, how it ranks them, how what Josh
wrote about his labelled tracks generalises to every other track on his USB.

Two measured facts to carry: rekordbox's `chorus` boundary is where the bass returns (the waveform
agrees); **Josh's "drop" is the snare roll that leads in, 4-8 bars earlier** (14 of 16 labelled drops).
The Up-phrase inner beats (`Phrase.beat2/3/4`) land on 7 of those 16 drops exactly. The drive mode's
roll window (`PLAN_ROLL_WINDOW_BEATS`) is the current bridge; a better placement rule from the file is
family 1's whole point.

## Two track sets (the rule that replaces "only the tracks status.py lists exist")

- **Scored set** = tracks with an ear-verified `tracks/*.timeline.json` (no `est:` notes). Josh's markers
  and notes are truth; marker rules (STRUCT-*, TIME-04/05/06, LOOK-05) are scored only here.
- **Sweep / generalisation set** = every other track on the USB export (`usb_export/`). Its timelines come
  from rekordbox's grid and carry NO markers by design. Only crashes, invariants, plan sanity and the
  marker-free rules (TIME-11/02/13/21, LOOK-03, SAFE-04/09) are judged here. **Never score a marker rule
  against anything that is not ear-verified, and never present an unverified marker as evidence.**
  `tools/agent/propose_markers.py` writes proposals for Josh; it never writes a timeline, and neither do you.
- `tracks/<stem>.look.json` are Josh's per-track look notes (`docs/LOOK_NOTES.md`): hook look and hue, which
  drop is the big one, strobe policy, quiet edges, mood words. Read every one that exists. They are exact
  orders for that track and the raw material for family 4: what do they say in common, and what default
  would have produced them without the note?

## Read first (in this order, before any code)

1. `results/agent/<date>/SWEEP.md` — the sweep that ran before you. A **crash, invariant or plan** finding is
   family 0 and beats every other sub-goal tonight; a rule finding on the sweep set is family 4 material.
2. `CLAUDE.md` — the lighting, merge and DMX paths require the spec rule you touch to be read first.
3. `.venv/Scripts/python.exe tools/agent/status.py` — mode, drop placement (bars from the ear drops), sweep
   compliance, FAIL counts per rule by family, the family to work on, each track's failing rules,
   `results/agent/state.json` (what previous nights did and left open).
4. `docs/rekordbox_drive_baseline_20261025.md` — what the planner does, what it got wrong and why.
5. `src/livereact/rekordbox/planner.py`, `mapping.py`, `look_notes.py`, and the drive path in
   `core/metronome.py` (`_drive_from_plan`, `_derive_section`). The docstring of every scorer rule you target
   (`src/livereact/sim/score.py`). The rule's measurement IS the spec; never relax a rule.
6. `params.json` — planner and drive constants are knobs (`PLAN_ROLL_WINDOW_BEATS`, `BUILD_COUNTIN_BEATS`,
   `TENSION_LEAD_BEATS`, `PREARM_BEATS`, `MIN_SECTION_BEATS`, `MIN_BUILD_BEATS`, `REGULAR_DROP_HIT`, ...).
   `safety-frozen` and `ear-tuned` values are read-only.
7. `git log -3 --format=%B` — the last commits say what was tried and what was left unfixable.

## Pick one sub-goal

Work the family `status.py` names as NEXT FAMILY:

| # | family | measured by |
|---|---|---|
| 0 | sweep findings | a crash / invariant / plan finding in SWEEP.md (fix it, add the test that would have caught it) |
| 1 | drop placement | `drop_offset_bars_mean` on the scored set, STRUCT-03, TIME-04 — a rule that places Josh's drop from the file (inner beats, waveform, fills), not from the live roll alone |
| 2 | section shape | TIME-05, LOOK-05, TIME-06, STRUCT-04/05/06/07 — what the plan does inside a section |
| 3 | per-drop character | STRUCT-02/08/09 — escalation, weak roll, clap-driven, from the waveform + look notes |
| 4 | generalisation | the marker-free rules over the sweep set; look notes → better defaults in `planner.py` / `mapping.py` / `rekordbox_map.json` |

Pick ONE rule or one mechanism you can plausibly finish and gate tonight. A finished, gated, committed
small win beats a half-built large one. Write the chosen sub-goal into the report immediately.

Prefer a new signal or a structural change over threshold fiddling: thresholds the knob search finds on
its own; it cannot invent a placement rule, a section merge rule, or a look. Knobs:
- A new constant goes in `params.json` with a tag (`tunable` with a range, or `safety-frozen` if it touches
  strobe or laser) AND as a module-level constant in the module `params.json` names.
- You may change a `tunable` knob's default when the change is structural and explained in the commit;
  `tests/test_params.py` requires the `params.json` default and the module constant to match — change both.
  Never change a knob's tag or range. Never change a `safety-frozen` or `ear-tuned` value.

## Josh's standing decisions

- **React to the music first.** Less latency between the lights and the music is always approved. A dwell
  floor or look hold exists to stop flapping, never to delay a real cut, drop or section change. Never
  strobe through silence.
- **One scheme per track**: one palette and one hook look for every drop (repeat the hook); only the track's
  biggest drop gets the laser fan and PEAK (one big moment); builds count in over the last two bars
  (`BUILD_COUNTIN_BEATS`, a deliberate exception to LOOK-05 the scorer still flags); intro and outro stay
  quiet (no strobe, no laser). A look note overrides any of these for its track.
- **Outro is not a dead end (D4). Power-on run is exempt (D5). Short runs that land on a labeled section
  change are correct (D5).** `docs/bible_integration/SPEC_DEVIATIONS.md` has the reasoning.
- **The UI is not your job.** No change under `src/livereact/ui/`.

## Study before you build

- `.venv/Scripts/python.exe tools/agent/dump_signals.py tracks/<track>.mp3 --around drop` runs the real
  pipeline in drive mode (the USB export supplies the plan) and prints a per-beat table: bucket, energy,
  bands, roll periodicity, strobe byte, scene, **section, planned scene, beats to the planned drop, plan
  active**. For a sweep finding on an unlabelled track: `dump_signals.py <file name> --sweep --all`.
- `.venv/Scripts/python.exe tools/agent/sweep.py --limit 5` re-runs the sweep on five tracks (scored first).
- `.venv/Scripts/python.exe tools/harness/run_sim.py tracks/<x>.mp3 --drive` then
  `tools/harness/score.py results/<ts>/<x>` scores one track the way the gate does.
- Keep study scripts in `results/agent/<date>/study/` (gitignored), not in the repo.

## Build, test, gate

1. Change code ONLY in: `src/livereact/rekordbox/planner.py`, `src/livereact/rekordbox/mapping.py`,
   `src/livereact/core/metronome.py` (the drive path), `params.json` (values), `rekordbox_map.json`, and
   tests under `tests/`. **The orchestrator enforces this list.**
2. `.venv/Scripts/python.exe -m pytest -o addopts= -q` must stay green except the 3 known failures in
   `tests/test_spec_conformance_gaps.py`. Any other failure is yours to fix or revert.
3. `.venv/Scripts/python.exe tools/agent/gate_head.py --write-baseline --out results/agent/<date>/gate_<n>.json`
   evaluates the working tree in drive mode on the scored set plus the 20-track sweep sample and gates it:
   tuning compliance up (+0.005), hold-out not worse, no rule regressing on any track, drop placement not
   worse than +0.25 bar, sweep compliance not lower, quality metrics not collapsing. It prints every flip.
   On ACCEPT it rewrites `results/baseline/evaluation.json`. **Only an ACCEPT may be committed.**
4. On ACCEPT: one commit per accepted change, including `git add -f results/baseline/evaluation.json`, with a
   message that says what the music does, what rule or mechanism you added, which tracks flipped with the
   gate's numbers (including drop placement and the sweep), what got worse, what you could not fix and why.
5. On REJECT: fix the regression or revert (`git restore .` plus deleting files you created). At most three
   attempts per night; after that, stop building and write up what you learned.
6. Re-run the full pytest after your final commit. `git status --porcelain` must print nothing when you finish.

## Hard limits

- Never push. Never change branch. Never merge or rebase. Never touch `main` or `harness`.
- Never edit anything outside the list in "Build, test, gate" step 1. In particular not `tracks/`
  (timelines, look notes), `spec/`, `src/livereact/sim/score.py`, `src/livereact/loop/`, `src/livereact/engines/`,
  `src/livereact/scenes/`, `src/livereact/merge/`, `src/livereact/ui/`, `tools/`, `.claude/`, the pytest
  configuration, or existing knobs' tags or ranges. Never delete a test file. If a tool is broken, work
  around it and report it; do not fix it.
- Never add strobe or laser behaviour that is not gated by the existing safety clamps in `merge/`.
- Never install packages, never run `tools/loop/search.py`, never run the live app or anything with `--hardware`.
- Stop building 30 minutes before the deadline and spend the rest on the report.

## Report (mandatory, even if nothing was accepted)

Write `results/agent/<date>/REPORT.md` with exactly these sections:

```
# Agent night <date> — <branch>
## Sub-goal
## Sweep (what SWEEP.md said, what you fixed or could not)
## What the music does (evidence from dump_signals, with track names and times)
## What I changed (files, mechanism, new knobs with tags)
## Gate result (compliance before -> after, drop placement before -> after, sweep before -> after, every track flip)
## What got worse or stayed broken, and why
## Generalisation (what the look notes have in common; what default you changed or would change)
## Could not fix without Josh (rule change needed, a look note to write, or a question) — one bullet each
## Tomorrow (the sub-goal to continue, or the next family)
```

Then update the state file for the next night:

```
.venv/Scripts/python.exe tools/agent/record_night.py --date <date> --branch <branch> --commits <n> \
    --gate <ACCEPT|REJECT|NO-CHANGE> --tests ok --family <n> --subgoal "<tomorrow>" --note "<one line>" \
    [--question "<for Josh>"]...
```

Finish by printing the report's Gate result section to stdout. Be exact and plain: Josh reads this over
coffee, and a confident wrong number costs more than an honest "could not".
