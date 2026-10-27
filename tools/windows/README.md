# Running the loop on the Windows desktop

1. `git clone https://github.com/joshuaracimora-lgtm/LiveReact.git %USERPROFILE%\LiveReact`, then `git checkout harness`.
2. Copy the Mac's `~/LiveReact/tracks/` folder (audio + `*.timeline.json` + `*.look.json`) into the clone. All of
   `tracks/` is gitignored on purpose (public repo); `tools/private_bundle.py` zips the small private files.
3. `powershell -ExecutionPolicy Bypass -File tools\windows\setup.ps1` — installs Git/Python 3.12/ffmpeg, builds the venv, runs the tests and a smoke run. Re-run until every line says PASS.
4. Power: Settings > System > Power > sleep "Never" when plugged in.
5. `powershell -ExecutionPolicy Bypass -File tools\windows\register_task.ps1` — registers the 1 AM nightly job (wakes the PC, 6 h search).
6. Morning: `results\nightly\<date>.log` and `results\search\<timestamp>\summary.md`.

Nothing here edits engine code. The search may only change `params.json` values tagged `tunable`.

The nightly script aborts (exit 3, logged) if the working tree has local changes or the pull does not
fast-forward — it never searches stale code silently. If a log says PREFLIGHT FAILED, follow
`docs/HANDOFF_windows_resync.md`. The script also holds a Windows system-required power request for the
run; set the AC sleep *and* unattended-sleep timeouts to never as well (same doc, step 5).

Both nightly scripts dot-source `common.ps1`: UTF-8 python environment (Windows console is not UTF-8;
`status.py` died on an accented track name), the wake request, and a kill-on-close job object so a
stopped task (`Stop-ScheduledTask`, the task's time limit: 10 h for the two-agent night) kills the agent's `claude.exe` tree too, not just the script.
Before that they dot-source `self_update.ps1`: it pulls harness first and, if it moved, re-runs the script
from the pulled tree (log line `self-update: pulled harness <old> -> <new>`), so a fix pushed from the Mac
runs the same night instead of the next one.

## Two nightly modes

- `nightly.ps1` — knob search only (random perturbation of `tunable` params, gated). Cheap; useful right
  after an engine change lands, pointless night after night on an unchanged engine.
- `nightly_agent.ps1` — the **three nightly agents** (2026-10-27), one after the other, each a headless Claude
  Code session on its own branch cut from harness and verified by the orchestrator, never by itself:
  1. **Bugs & UI** (`docs/AGENT_BRIEF_UI.md`, `-UiHours`, branch `agent/<date>-ui`): sweep crashes first,
     then `docs/UI_BUGS.md` (the Venue tab); gated by `tools/agent/gate_ui.py`.
  2. **App** (`docs/AGENT_BRIEF_APP.md`, `-AppHours`, branch `agent/<date>-app`): the publishable app --
     fixture definitions, rig manager, presets, Manual Override panel, packaging -- from
     `docs/APP_ROADMAP.md`, never touching the show; gated by `tools/agent/gate_app.py` (offline tests,
     UI checks, show unchanged).
  3. **Track Classifier** (`docs/AGENT_BRIEF.md`, `-Hours`, branch `agent/<date>`): classes of sections
     across the whole USB library, scene proposals for Josh, then one planner improvement; gated by
     `tools/agent/gate_head.py` against harness's baseline.
  Then `-SearchHours` of knob search around the Track Classifier's accepted code. `-HardStop` (default
  `23:30`) cuts every agent's deadline and the search to that clock time. Nothing reaches `harness`
  until Josh merges a branch (NIGHT.md flags files changed on more than one branch). Before the agents the
  baseline is regenerated if the pull changed code, scorer or labels (`tools/agent/baseline_fresh.py`).
  Setup: `docs/HANDOFF_windows_agent_setup.md`. Morning read: `results\agent\<date>\NIGHT.md`, then
  `ui\REPORT.md`, `app\REPORT.md`, `tracks\REPORT.md`, `tracks\SCENE_PROPOSALS.md`. Register (from an
  administrator PowerShell) with
  `register_task.ps1 -Script nightly_agent.ps1 -ScriptArgs "-UiHours 1.5 -AppHours 2.5 -Hours 2.5 -SearchHours 1 -Push" -At "2:00PM" -LimitHours 10`
  (about 9 h from 2 PM: sweep 0.7 + UI 1.5 + app 2.5 + tracks 2.5 + search 1 + gates; done by about 23:15).
