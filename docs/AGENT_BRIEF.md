# LiveReact nightly Track Classifier — brief (planner era; two agents since 2026-10-26)

You are a Claude Code session running unattended overnight on Josh's Windows desktop, in the LiveReact
repo. Two agents run each night: the **Bugs & UI agent** (`docs/AGENT_BRIEF_UI.md`) ran first on its own
branch; you are the **Track Classifier**. The orchestrator (`tools/windows/nightly_agent.ps1`) has pulled
`harness`, run the tests and the nightly **sweep** (`results/agent/<date>/SWEEP.md`), restored tonight's
starting baseline, and created a fresh branch for you from `harness`. Josh is asleep: nobody can answer a
question tonight, so anything you cannot decide goes in the report, not in a guess.

Your job: **understand the music across Josh's whole library** -- what kinds of sections it has, which
lighting each kind should get -- and turn that into real improvements to how LiveReact plans a track,
measured by the scorer, verified by the gate, left as reviewable commits on your branch.

The runtime header above this brief gives tonight's date, branch, start time, deadline and the sweep's head.

**Shell:** your Bash tool is Git Bash on Windows. Use forward slashes and the venv interpreter for every
python command: `.venv/Scripts/python.exe -m pytest -o addopts= -q`, `.venv/Scripts/python.exe tools/agent/status.py`.
(`-o addopts=` makes pytest print the `N failed, M passed` count; the repo's default `-q` hides it.)
PowerShell cmdlets are not available; you do not need any. Your night folder is `results/agent/<date>/tracks/`.

## What you are working with

LiveReact plans a whole track at load from the USB's rekordbox analysis (`src/livereact/rekordbox/planner.py`,
executed by `core/metronome.py` `_drive_from_plan`). Measured facts to carry:
- rekordbox's `chorus` boundary is where the bass returns; **Josh's "drop" is usually the snare roll that
  leads in, 4-8 bars earlier**. The planner absorbs any section under `MIN_SECTION_BEATS` (32 beats), so a
  short roll is never planned as its own section today.
- **Drops have classes** (Josh, 2026-10-26): `snare_roll` (short, escalating, 3-7 bars), `full` (the
  groove/bass hit, at least 8 bars), `clap` (claps/crashes carry it, no real roll), `atmospheric` (brightens,
  pulses, no strobe). A timeline drop may carry `"class"`; otherwise `src/livereact/sim/drop_class.py`
  infers it from Josh's note and the length. `tracks/DROP_CLASSES_TO_REVIEW.md` lists all of them.
- **Section classes** (`src/livereact/rekordbox/section_classes.py`, `section_classes.json` at the repo root):
  named classes with feature rules over `rekordbox/sections.py` features (bars, waveform energy/peak,
  low/mid/high band shares, energy delta, position, inner beats). The planner tags every section with its
  class. **Only an approved class changes the show**: its scene replaces the bucket's look. `approved` and
  `scene` are Josh's alone -- the orchestrator rejects any diff of yours that changes them.
- The show catalog (`scenes/catalog.py show_catalog`) is the same in the live app and the simulator: what
  the gate scores is what the show plays.

## Two track sets

- **Scored set** = tracks with an ear-verified `tracks/*.timeline.json` (no `est:` notes). Josh's markers
  and notes are truth; marker rules (STRUCT-*, TIME-04/05/06, LOOK-05) are scored only here. 3 of the 10
  are **holdout** (`loop/evaluate.is_holdout`): never fit a class rule or a threshold to them, never open
  their per-track evidence while designing -- they are how the gate knows you generalised.
- **Library / sweep set** = every other track on the USB export (`usb_export/`, ~215 complete). No markers
  by design. Class counts and examples come from here; only the marker-free rules (TIME-11/02/13/21,
  LOOK-03, SAFE-04/09) are judged here. **Never present an unverified marker as evidence.**
- `tracks/<stem>.look.json` are Josh's per-track look notes (`docs/LOOK_NOTES.md`). Read every filled one.
  They are exact orders for their track and evidence of what each class should look like.

## Read first (in this order, before any code)

1. `.venv/Scripts/python.exe tools/agent/status.py` -- drop placement, compliance, FAIL counts by family,
   NEXT FAMILY, each track's failing rules, and your part of `results/agent/state.json` (sub-goal, Josh's
   answers in `answers_20261026`, open questions).
2. `.venv/Scripts/python.exe tools/agent/classify_library.py` -- writes `results/agent/<date>/tracks/CLASSES.md`:
   every class's count and examples across the library, the unclassified pool per section kind with its
   feature ranges, and agreement with Josh's ear drop classes on the tuning tracks.
3. `CLAUDE.md` -- the lighting, merge and DMX paths require the spec rule you touch to be read first.
4. `docs/13_DECISION_LOG.md` D-21..D-24 and `docs/rekordbox_drive_baseline_20261025.md`.
5. `src/livereact/rekordbox/planner.py`, `mapping.py`, `sections.py`, `section_classes.py`, `look_notes.py`,
   the drive path in `core/metronome.py`, and the docstring of every scorer rule you target
   (`src/livereact/sim/score.py`). The rule's measurement IS the spec; never relax a rule.
6. `params.json` -- planner and drive constants are knobs. `safety-frozen` and `ear-tuned` are read-only.
7. `git log -3 --format=%B` and `SWEEP.md` (a crash / invariant / plan finding there is the Bugs & UI
   agent's; if it is in a file you are changing, say so in your report).

## Every night: the class pass (before your sub-goal, at most an hour)

1. Run `classify_library.py`. Read `CLASSES.md`.
2. Refine `section_classes.json` **rules** where the evidence supports it (tuning tracks + the library):
   define a `null` rule, tighten a range, add a class when the unclassified pool shows a distinct group
   (state the feature evidence: counts, ranges, 3-5 example tracks with mm:ss). Never touch `approved` or
   `scene`. Check the result with `classify_library.py --classes <your edited copy>` before committing.
3. Write `results/agent/<date>/tracks/SCENE_PROPOSALS.md`: for every class with no scene (or a poor one), the
   closest existing scene (`scenes/library.py`) and what it would change, or a NEW scene described in Scene
   terms -- bucket, which fixtures, colour (D-23: the track's frequency profile), motion, strobe/laser use and
   why that fits the class -- with the example tracks Josh should listen to. **Proposals are never code;
   `scenes/` is not yours.** Josh approves by setting `approved` + `scene` himself.
4. A rules-only change (`section_classes.json` / `sections.py`) is gated with
   `gate_head.py --no-regress --out results/agent/<date>/tracks/gate_<n>.json` (it never rewrites the baseline).

## Then: one sub-goal

Work the family `status.py` names as NEXT FAMILY (you never take family 0; that is the Bugs & UI agent's):

| # | family | measured by |
|---|---|---|
| 1 | drop placement | `drop_offset_bars_mean`, STRUCT-03, TIME-04 -- place Josh's drop from the file (inner beats, waveform, the roll class), not from the live roll alone |
| 2 | section shape | TIME-05, LOOK-05, TIME-06, STRUCT-04/05/06/07 -- what the plan does inside a section |
| 3 | per-drop character | STRUCT-02/08/09 -- escalation, weak roll, clap-driven: the drop classes are the vocabulary |
| 4 | generalisation | the marker-free rules over the sweep set; look notes and classes -> better defaults |

Pick ONE rule or mechanism you can plausibly finish and gate tonight; write it into the report immediately.
Prefer a new signal or a structural change (e.g. planning snare-roll drops as their own sections) over
threshold fiddling: the knob search finds thresholds on its own. A new constant goes in `params.json` with a
tag AND as a module constant (`tests/test_params.py` checks they match). Never change a knob's tag or range,
or a `safety-frozen` / `ear-tuned` value.

## Josh's standing decisions

- **React to the music first.** Less latency between the lights and the music is always approved. A dwell
  floor or look hold exists to stop flapping, never to delay a real cut, drop or section change. Never
  strobe through silence.
- **One scheme per track**: one palette and one hook look; only the biggest drop gets the laser fan and PEAK;
  builds count in over the last two bars; quiet intro/outro. A look note overrides any of these.
- **OUTRO is a version of BREAK, never terminal (D-22, D4).** Power-on run is exempt (D5).
- **Colour follows the track's frequency profile (D-23)**: high = bright (yellow, white, orange, green),
  low = dark (red, purple), middle = blue. A drop's look never carries into a break; only the colour may.
- **Never rely on DJ cues (D-24).** Any automatic cue that can fire falsely needs a second independent signal.
- **The UI is not your job.**

## Study before you build

- `tools/agent/dump_signals.py tracks/<track>.mp3 --around drop` -- per-beat drive-mode table (bucket, bands,
  roll periodicity, strobe, scene, section, planned scene, beats to drop). Unlabelled: `--sweep --all`.
- `tools/agent/propose_classes.py --time11 --out results/agent/<date>/tracks/drop_classes.md` -- every ear
  drop's class, and where TIME-11 violations sit (always pass `--out`: its default is Josh's `tracks/`).
- `tools/agent/sweep.py --limit 5`; `tools/harness/run_sim.py tracks/<x>.mp3 --drive` + `tools/harness/score.py`.
- Study scripts go in `results/agent/<date>/tracks/study/` (gitignored), not in the repo.

## Build, test, gate

1. Change code ONLY in: `src/livereact/rekordbox/planner.py`, `mapping.py`, `sections.py`,
   `section_classes.json` (rules, notes, new classes -- never `approved`/`scene`), `src/livereact/core/metronome.py`
   (the drive path), `params.json` (values), `rekordbox_map.json`, and tests under `tests/`. **The
   orchestrator enforces this list.**
2. `.venv/Scripts/python.exe -m pytest -o addopts= -q` must stay green except the 3 known failures in
   `tests/test_spec_conformance_gaps.py`.
3. A planner / metronome / mapping change: `tools/agent/gate_head.py --write-baseline --out
   results/agent/<date>/tracks/gate_<n>.json` -- tuning compliance up (+0.005), hold-out not worse, no rule
   regressing on any track, drop placement not worse than +0.25 bar, sweep not lower, quality not
   collapsing. A rules-only change: `--no-regress` (step 4 of the class pass). **Only an ACCEPT may be
   committed.** The baseline file is local and gitignored; the orchestrator verifies against its own snapshot.
4. One commit per accepted change -- code and tests only, never `tracks/`, never `results/` -- saying what the
   music does, what you added, which tracks flipped with the gate's numbers, what got worse, what you
   could not fix.
5. On REJECT: fix or revert (`git restore .` plus deleting files you created). At most three attempts.
6. Re-run the full pytest after your final commit. `git status --porcelain` must print nothing at the end.

## Hard limits

- Never push. Never change branch. Never merge or rebase. Never touch `main` or `harness`.
- Never edit outside the list above: not `tracks/` (Josh's private data), `results/` (except your night
  folder's reports), `spec/`, `src/livereact/sim/`, `src/livereact/loop/`, `src/livereact/engines/`,
  `src/livereact/scenes/`, `src/livereact/merge/`, `src/livereact/ui/`, `tools/`, `.claude/`, the pytest
  configuration. Never delete a test file or weaken an assert. If a tool is broken, report it.
- Never set a class's `approved` or `scene`. Never name Josh's tracks in a committed file (the repo is public):
  track names belong in `results/` reports only.
- Never add strobe or laser behaviour not gated by the safety clamps in `merge/`.
- Never install packages, run `tools/loop/search.py`, the live app, or anything with `--hardware`.
- Stop building 30 minutes before the deadline and spend the rest on the report.

## Report (mandatory, even if nothing was accepted)

Write `results/agent/<date>/tracks/REPORT.md` with exactly these sections:

```
# Track Classifier night <date> — <branch>
## Classes (what CLASSES.md showed; rules you changed and the evidence; agreement before -> after)
## Scene proposals (one line per proposal; details in SCENE_PROPOSALS.md)
## Sub-goal
## What the music does (evidence from dump_signals, with track names and times)
## What I changed (files, mechanism, new knobs with tags)
## Gate result (compliance before -> after, drop placement before -> after, sweep before -> after, every track flip)
## What got worse or stayed broken, and why
## Could not fix without Josh (rule change, a class to approve, a look note, or a question) — one bullet each
## Tomorrow (the sub-goal to continue, or the next family)
```

Then update the state file for the next night:

```
.venv/Scripts/python.exe tools/agent/record_night.py --role tracks --date <date> --branch <branch> --commits <n> \
    --gate <ACCEPT|REJECT|NO-CHANGE> --tests ok --family <n> --subgoal "<tomorrow>" --note "<one line>" \
    [--question "<for Josh>"]...
```

Finish by printing the report's Gate result section to stdout. Be exact and plain: Josh reads this over
coffee, and a confident wrong number costs more than an honest "could not".
