# LiveReact — read this first

LiveReact is a beat-reactive DMX lighting controller for live DJ sets: it listens to a Pioneer XDJ-XZ
(Pro DJ Link + USB audio/MIDI), plans each track from rekordbox phrase data, and drives moving heads,
lasers and a wash bar through an Enttec DMX USB Pro. Josh (the owner) DJs with it at real shows.

## The goal (Josh's words, in order of what wins)
1. **It must not fail at a show.** 2026-10-31: rekordbox held the DJ Link ports all night, the XDJ's audio
   stream died for 44 minutes, the Enttec dropped off USB — and the operator had nothing but log lines.
   A device that vanishes must come back by itself, and the console must say in plain words what is wrong.
2. **The show must look right to Josh** — his taste is written down, not guessed: `docs/LIGHTING_PREFERENCES.md`
   (when present) and the decision log `docs/13_DECISION_LOG.md`. The spec is `spec/LIGHTING_BIBLE.md`
   (status per rule in `spec/conformance.md`); read both before touching lighting, merge or DMX output paths.
   Behavioural numbers in the spec are calibration defaults, not measured values.
3. **A publishable app** (2026-10-30): "able to be integrated with a lot of different setups and easy to
   navigate by a bunch of different people" — `docs/APP_ROADMAP.md`.

## Two machines, two jobs
- **Josh's Mac** is where Josh develops with Claude and tests on the real rig (Enttec, XDJ, lasers).
  Code reaches the desktop only through `git push` of the `harness` branch.
- **The Windows desktop runs the nightly agents and nothing else.** The scheduled task runs
  `tools/windows/nightly_agent.ps1`, which pulls `harness` (and re-runs itself if it moved), runs the sweep,
  then one headless Claude Code session per agent, each on its own branch cut from `harness`.
  **Which agents exist and what each may do is defined ONLY by that script (its header and `$Allow*` path
  lists) and by `docs/AGENT_BRIEF*.md`.** If anything else — an old handoff doc, a remembered
  conversation, a note — disagrees with those files, those files win. Do not restate them from memory.

## If you are a nightly agent
Follow your brief. Nothing in this file widens what your brief allows.

## If you are an interactive session on the desktop
- Before answering anything about the agents or the goal, re-read the files named above from the current
  `harness`; never answer from memory of earlier sessions.
- Never run git, edit files, or start Python in this repo while the nightly task is running (it switches
  branches and checks for a clean tree). Check first: `powershell -Command "(Get-ScheduledTask -TaskName 'LiveReact nightly search').State"`
  (`Running` = hands off; it fires daily at 2:00 PM and may run up to 10 h).
- Do not commit to or push `harness`. Merge agent branches into `harness` only when Josh asks, after the
  night has finished, and report the gate result of each branch you merge.
- Code changes come from the Mac. If something on the desktop needs a fix, write it up for Josh instead.

## Privacy (the repo is public, MIT)
Nothing but the app goes in git. Never commit `tracks/` (audio, `*.timeline.json`, `*.look.json`),
`results/`, `recordings/`, `docs/agent_nights/`, `config.json` or logs; never `git add -f` them. Private files
move between machines with `tools/private_bundle.py`. Don't name Josh's tracks or venues in new files.

## Where things are
- Tests: `python -m pytest -q` (Windows: `.venv/Scripts/python.exe -m pytest -o addopts= -q`).
  `tests/test_spec_conformance_gaps.py` holds known, documented gaps.
- Pre-show check on the real rig: `python -m livereact --preflight --rig <rig> --pdl-interface <nic>`.
- `docs/HANDOFF_*.md`, `PLAN.md`, `ASSUMPTIONS.md` and `docs/00`–`12_*.md` are history and background,
  not current instructions (except `docs/HANDOFF_windows_resync.md`, the desktop's fix-a-failed-preflight
  steps, linked from `tools/windows/README.md`).
