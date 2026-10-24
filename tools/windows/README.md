# Running the loop on the Windows desktop

1. `git clone https://github.com/joshuaracimora-lgtm/LiveReact.git %USERPROFILE%\LiveReact`, then `git checkout harness`.
2. Copy the Mac's `~/LiveReact/tracks/` folder (audio + `*.timeline.json`) into the clone. Audio is gitignored on purpose.
3. `powershell -ExecutionPolicy Bypass -File tools\windows\setup.ps1` — installs Git/Python 3.12/ffmpeg, builds the venv, runs the tests and a smoke run. Re-run until every line says PASS.
4. Power: Settings > System > Power > sleep "Never" when plugged in.
5. `powershell -ExecutionPolicy Bypass -File tools\windows\register_task.ps1` — registers the 1 AM nightly job (wakes the PC, 6 h search).
6. Morning: `results\nightly\<date>.log` and `results\search\<timestamp>\summary.md`.

Nothing here edits engine code. The search may only change `params.json` values tagged `tunable`.

The nightly script aborts (exit 3, logged) if the working tree has local changes or the pull does not
fast-forward — it never searches stale code silently. If a log says PREFLIGHT FAILED, follow
`docs/HANDOFF_windows_resync.md`. The script also holds a Windows system-required power request for the
run; set the AC sleep *and* unattended-sleep timeouts to never as well (same doc, step 5).
