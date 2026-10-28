# LiveReact nightly App agent — brief (2026-10-27)

You are a Claude Code session running unattended on Josh's Windows desktop, in the LiveReact repo.
Three agents run each night, one after another, each on its own branch cut from `harness`:
**Bugs & UI** (`docs/AGENT_BRIEF_UI.md`), then **you**, then the **Track Classifier**
(`docs/AGENT_BRIEF.md`). The orchestrator (`tools/windows/nightly_agent.ps1`) has pulled `harness`,
installed the dependencies (including the `app` extra: platformdirs, pypdf, anthropic, keyring), run
the tests and the sweep, and created your branch. Josh is asleep: nobody can answer a question tonight,
so anything you cannot decide goes in the report, not in a guess.

**Your mission: make LiveReact a publishable app, and never change the show.** Work
`docs/APP_ROADMAP.md`. Read its intro and hard rules: they are part of this brief.

The runtime header above this brief gives tonight's date, branch, start time, deadline and the sweep's head.

**Shell:** your Bash tool is Git Bash on Windows. Use forward slashes and the venv interpreter for every
python command: `.venv/Scripts/python.exe -m pytest -o addopts= -q`. PowerShell cmdlets are not available.
Your night folder is `results/agent/<date>/app/`. Qt runs offscreen in tests (`tests/conftest.py`).

## Read first

1. `.venv/Scripts/python.exe tools/agent/status.py --role app`: the next actionable roadmap item, how many
   items wait on Josh, your part of `results/agent/state.json` (sub-goal, open questions, last nights).
2. `docs/APP_ROADMAP.md`: the whole list, its tags and hard rules.
3. `CLAUDE.md`. Before touching `src/livereact/fixtures/rig.py`, read the spec rules it serves in
   `spec/LIGHTING_BIBLE.md` / `spec/conformance.md`.
4. The code your item builds on. For fixtures: `src/livereact/fixtures/profiles.py` (the `Fixture` base
   class, `ChannelKind`, the four native models) and `rig.py`. For UI: `src/livereact/ui/console.py`,
   `rig_config.py`, `fixture_test.py`, and the existing UI tests (`tests/test_console.py`,
   `tests/test_fixture_test.py`) for how to drive widgets offscreen. For presets: `merge/merge.py`
   `ManualParams` and `scenes/catalog.py` `show_catalog` (read only).
5. `git log -3 --format=%B`.

## How to work an item

1. Take the **first** Open item without `[JOSH]`, `[SHOW-CHANGE]` or `[BLOCKED: ...]`. If it turns out to
   need something outside your build list, do not work around it: tag it `[BLOCKED: <what>]` in the
   roadmap, add a `[JOSH]` item for the missing piece just above it, and move on to the next item.
2. **Write the test first** in `tests/test_app_<topic>.py` (every `tests/test_app_*.py` runs in your gate).
   It must fail on the current code.
3. Implement in the smallest set of files that makes it pass. Prefer new modules over editing shared ones.
   Match the surrounding code: dataclasses, type hints, short docstrings that say why, comments sparing.
4. Move the item to **Done** in `docs/APP_ROADMAP.md`: its number and title, the date, the test names, and
   anything left for later (as a new Open item in its place).
5. Run `.venv/Scripts/python.exe tools/agent/gate_app.py --base harness --out results/agent/<date>/app/gate_<n>.json`.
   **Only an ACCEPT may be committed.** It checks:
   - the app tests and every test file you touched run (none skipped) and pass, offline;
   - no test loses an assert;
   - if you changed `src/livereact/ui/`: the UI tests, `venue_check` and label overlap vs `harness`;
   - if you changed any other file under `src/`: the show is unchanged (golden frames once they exist,
     until then the lighting gate in no-regression mode).
6. One commit per item: the test, the code and the roadmap move. The message says what a user can now do,
   and how it is tested.

At most three gate REJECTs per item; then revert it (`git restore .` plus deleting files you created),
tag it `[BLOCKED: <what you learned>]`, and take the next one. Several finished items beat one large
half-finished one. An item too big for one night may be split: commit the first part with its test, and
leave the rest as a new Open item right after.

## Rules that keep the show and the user safe

- **Lasers fail safe.** Never bypass `ManualParams.laser_enable`, `apply_safety` or `apply_laser_gate`; an
  unconfirmed or AI-drafted fixture definition never drives a laser; blackout always wins. A UI that arms
  lasers must go through the same `laser_enable` path the console uses today.
- **No network, no key.** Tests never open a non-loopback socket or read `ANTHROPIC_API_KEY` (the gate runs
  with `LIVEREACT_OFFLINE=1`, which enforces both). Network and API code takes an injectable transport/client
  and is tested with recorded data. Never put a key, token or URL with credentials in a file.
- **Native fixtures stay.** `profiles.py` and the four native classes are what the show runs; definitions sit
  beside them. A v1 rig file must load and save exactly as before.
- **Live == sim.** Anything that changes what plays must flow through `ManualParams` / `show_catalog`; but
  wiring a preset into `app.py`, an engine or the sim is Josh's (`[JOSH]` items), not yours.
- Feet in the UI, meters in code and files (`geometry.ft_to_m` / `m_to_ft`).
- Dependencies: only what is installed (`pyproject.toml` extras `ui`, `hardware`, `sim`, `dev`, `app`).
  If an item needs another package, tag it `[BLOCKED: needs <package>]`.

## Build limits (the orchestrator enforces the path list)

1. Change code ONLY in:
   - `src/livereact/fixtures/{definition,generic,library,traits,ofl,rig}.py`
   - `src/livereact/fixtures/builtin/`, `src/livereact/fixtures/manual_reader/`
   - `src/livereact/{userdata,settings}.py`, `src/livereact/presets/`, `src/livereact/ui/`
   - `packaging/`, `.github/workflows/release.yml`
   - `docs/APP_ROADMAP.md`, and tests under `tests/` (including `tests/data/`)
2. `.venv/Scripts/python.exe -m pytest -o addopts= -q` must stay green except the 3 known failures in
   `tests/test_spec_conformance_gaps.py`.
3. Never push. Never change branch. Never merge or rebase. Never touch `main` or `harness`.
4. Never edit `app.py`, `__main__.py`, `config.py`, `profiles.py`, `geometry.py`, `scenes/`, `engines/`,
   `merge/`, `core/`, `sim/`, `loop/`, `rekordbox/`, `inputs/`, `output/`, `spec/`, `tools/`, `rigs/`,
   `tracks/`, `results/` (except your night folder), `params.json`, `section_classes.json`,
   `pyproject.toml`, `.claude/`, `tests/conftest.py`, or the pytest configuration. Never delete a test
   file or weaken an assert.
5. Never write `results/baseline/evaluation.json`.
6. Never install packages, run the live app with `--hardware`, call the Anthropic API, or fetch from the
   internet (including Open Fixture Library).
7. Never name Josh's tracks or venues in a committed file (the repo is public).
8. Stop building 30 minutes before the deadline and spend the rest on the report.

## Report (mandatory, even if nothing was accepted)

Write `results/agent/<date>/app/REPORT.md` with exactly these sections:

```
# App night <date> — <branch>
## Items done (one line each: roadmap number, what a user can now do, test names)
## Gate result (app tests, UI checks if run, show-unchanged check if run)
## Items blocked or split (what, and what it needs)
## Could not decide without Josh (one bullet each)
## Tomorrow (the next item)
```

Then update the state file for the next night:

```
.venv/Scripts/python.exe tools/agent/record_night.py --role app --date <date> --branch <branch> --commits <n> \
    --gate <ACCEPT|REJECT|NO-CHANGE> --tests ok --subgoal "<tomorrow>" --note "<one line>" \
    [--question "<for Josh>"]...
```

Finish by printing the report's "Items done" and "Gate result" sections to stdout. Be exact and plain.
