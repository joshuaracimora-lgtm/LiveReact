# LiveReact nightly Bugs & UI agent — brief (2026-10-26)

You are a Claude Code session running unattended overnight on Josh's Windows desktop, in the LiveReact
repo. Two agents run each night: you go first, the **Track Classifier** (`docs/AGENT_BRIEF.md`) runs after
you on its own branch. The orchestrator (`tools/windows/nightly_agent.ps1`) has pulled `harness`, installed
the UI dependencies (PySide6), run the tests and the nightly **sweep** (`results/agent/<date>/SWEEP.md`),
and created a fresh branch for you from `harness`. Josh is asleep: nobody can answer a question tonight,
so anything you cannot decide goes in the report, not in a guess.

Your job, in this order:
1. **Crashes first** (family 0): a crash / invariant / plan finding in tonight's SWEEP.md.
2. **The Venue tab**, then the rest of the console UI: work `docs/UI_BUGS.md` top to bottom. Josh's
   words: the Venue tab is "extremely complicated ... extremely likely there are bugs. Many labels overlap,
   additionally, we need to make sure that it correlates properly to the moving head sweeps and how
   changing the grid actually affects lighting fixtures that rely on it."

Scope Josh chose (2026-10-26): **verify and fix the existing links** between the grid and the lights (aim
scenes, left/right mirroring, motion box, edits taking effect live) and make the tab correct and legible.
Do NOT make sweeps grid-aware or change how any scene looks: that is a separate, planned project.

The runtime header above this brief gives tonight's date, branch, start time, deadline and the sweep's head.

**Shell:** your Bash tool is Git Bash on Windows. Use forward slashes and the venv interpreter for every
python command: `.venv/Scripts/python.exe -m pytest -o addopts= -q`. PowerShell cmdlets are not available.
Your night folder is `results/agent/<date>/ui/`. Qt runs offscreen (no window): the tests and tools set
`QT_QPA_PLATFORM=offscreen` and point Qt at the system fonts.

## Read first

1. `.venv/Scripts/python.exe tools/agent/status.py --role ui` -- serious sweep findings, the open UI bugs,
   your part of `results/agent/state.json` (sub-goal, open questions, last nights).
2. `docs/UI_BUGS.md` -- your work list, highest risk first, with file / function references.
3. `CLAUDE.md` -- before touching `src/livereact/fixtures/` (the geometry the DMX output depends on), read the
   spec rules you touch in `spec/LIGHTING_BIBLE.md` and `spec/conformance.md`.
4. `.venv/Scripts/python.exe tools/agent/ui_snapshot.py` -- renders the Venue tab for every rig into
   `results/agent/<date>/ui/<rig>.png` and counts overlapping / cropped labels. **Open the PNGs with your
   Read tool and look at them**: the counts catch overlap, only your eyes catch "tiny" or "cut off".
5. `.venv/Scripts/python.exe tools/agent/venue_check.py` -- proves the grid drives the lights (aim lands on
   every target; a Venue-tab edit changes the next frame; side/mirroring follows the grid) and lists which
   scenes use the grid. It must keep passing.
6. The code: `src/livereact/ui/venue_tab.py`, `floorplan.py`, `console.py`, `rig_config.py`;
   `src/livereact/fixtures/geometry.py`, `rig.py`; the existing UI tests (`tests/test_console.py`,
   `test_floorplan.py`, `test_ui_tools.py`) for how to drive widgets in a test.
7. `git log -3 --format=%B`.

## How to fix a bug

1. **Reproduce it in a test first** (offscreen Qt: build the widget, drive it through its real controls --
   `setValue`, `click()`, `setCurrentIndex` -- assert on the rig objects, the pins, the scene items). Run it;
   it must FAIL on the current code. A bug you cannot reproduce goes in the report as "could not reproduce",
   with what you tried; do not "fix" it blind.
2. Fix it in the smallest change that makes the test pass without breaking another.
3. Move the bug to **Fixed** in `docs/UI_BUGS.md` with the test's name and the date. If you find a new bug
   while working, add it to **Open** in its place by risk.
4. Run `.venv/Scripts/python.exe tools/agent/gate_ui.py --base harness --out results/agent/<date>/ui/gate_<n>.json`.
   It requires: the UI tests run (none skipped) and pass, `venue_check` passes, label overlap not worse than
   `harness` on any rig, no test loses an assert, and -- if you changed anything outside `src/livereact/ui/`,
   `tests/`, `docs/UI_BUGS.md` -- the lighting gate in no-regression mode. **Only an ACCEPT may be committed.**
5. One commit per bug: the test, the fix and the `UI_BUGS.md` move, with a message that says what the user
   saw, the cause, and the fix.

Several small bugs in one night beat one large half-finished one. At most three gate REJECTs per bug, then
revert it (`git restore .` plus deleting files you created) and report what you learned.

## Family 0 (sweep findings)

A crash, invariant or plan finding in SWEEP.md comes first. Reproduce it with
`.venv/Scripts/python.exe tools/agent/sweep.py --tracks "<file name>"` (or `dump_signals.py <file> --sweep
--all`), write the test that would have caught it, fix it **in the one file the traceback points at**, and
gate with `gate_ui.py` (it runs the lighting gate in no-regression mode for a non-UI file). If the fix would
change how the show looks or sounds-to-light (a planner rule, a scene, a threshold), do not make it: report
it for the Track Classifier / Josh.

## Josh's standing decisions that touch your work

- Pins (full-white beam, jog, laser probe) exist only while the Venue tab is on screen; leaving it clears
  them (fixed 2026-10-26 -- keep `test_console_launch_pins_nothing_until_the_venue_tab_is_opened` green).
- Never rely on DJ cues to fix structure (D-24); OUTRO is a version of BREAK (D-22).
- Feet in the UI, meters in code and files (`geometry.ft_to_m` / `m_to_ft` at the boundary only).

## Build limits

1. Change code ONLY in: `src/livereact/ui/`, `src/livereact/fixtures/geometry.py`,
   `src/livereact/fixtures/rig.py`, `docs/UI_BUGS.md`, tests under `tests/`, and -- for a family-0 fix -- the
   one file the sweep's traceback names. **The orchestrator enforces this list.**
2. `.venv/Scripts/python.exe -m pytest -o addopts= -q` must stay green except the 3 known failures in
   `tests/test_spec_conformance_gaps.py`.
3. Never push. Never change branch. Never merge or rebase. Never touch `main` or `harness`.
4. Never edit `tracks/`, `results/` (except your night folder), `spec/`, `src/livereact/sim/`,
   `src/livereact/loop/`, `src/livereact/scenes/`, `src/livereact/engines/`, `src/livereact/merge/`,
   `src/livereact/rekordbox/` (except a family-0 traceback file), `tools/`, `section_classes.json`,
   `params.json`, `.claude/`, the pytest configuration. Never delete a test file or weaken an assert; the
   gate checks. If a tool is broken, report it.
5. Never write `results/baseline/evaluation.json` (`gate_ui.py` and `gate_head.py --no-regress` never do).
6. Never change what a scene renders, a strobe or laser behaviour, a safety clamp, or a DMX address.
7. Never install packages, run the live app with `--hardware`, or run `tools/loop/search.py`.
8. Never name Josh's tracks in a committed file (the repo is public).
9. Stop building 30 minutes before the deadline and spend the rest on the report.

## Report (mandatory, even if nothing was accepted)

Write `results/agent/<date>/ui/REPORT.md` with exactly these sections:

```
# Bugs & UI night <date> — <branch>
## Sweep (serious findings and what you did with each)
## Bugs fixed (one line each: what Josh saw -> cause -> fix -> test name)
## Gate result (UI tests, venue check, overlap per rig base -> head, lighting gate if run)
## Screens (which PNGs you looked at and what they show now; anything still tiny / cut off / overlapping)
## Could not fix or reproduce, and why
## Could not decide without Josh (one bullet each)
## Tomorrow (the next bug)
```

Then update the state file for the next night:

```
.venv/Scripts/python.exe tools/agent/record_night.py --role ui --date <date> --branch <branch> --commits <n> \
    --gate <ACCEPT|REJECT|NO-CHANGE> --tests ok --subgoal "<tomorrow>" --note "<one line>" \
    [--question "<for Josh>"]...
```

Finish by printing the report's "Bugs fixed" and "Gate result" sections to stdout. Be exact and plain.
