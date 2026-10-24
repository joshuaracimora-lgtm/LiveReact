# Handoff: bring the Windows desktop back in sync and make the nightly run stick

**For:** a Claude Code session on the Windows desktop, started in `%USERPROFILE%\LiveReact`.
**Why:** the first two nightly runs (2026-10-22 12:04, 2026-10-23 01:00) searched stale code. The clone
was stuck at `67a6ddd` because `HARDWARE.md` had a local modification (the Mac's then-uncommitted rewrite,
shipped inside `LiveReact-desktop.zip`; it is byte-identical to the committed file), so
`git pull --ff-only` aborted every time and the old `nightly.ps1` carried on anyway. The 01:00 run was
also killed from outside after 25 trials (no summary.md, no transcript footer) — sleep is suspected.
Do everything below yourself, verify each step, and report what you did. Do not edit code or `params.json`.

**Shell note:** your Bash tool is Git Bash. Use forward slashes in paths (`tools/windows/...`). PowerShell
cmdlets must be wrapped: `powershell -Command "Start-ScheduledTask -TaskName 'LiveReact nightly search'"`;
`powercfg`, `git` and `python` run directly.

1. Show `git status --short` and `git log --oneline -1`. Expect `M HARDWARE.md` and `67a6ddd`.
2. Discard the local HARDWARE.md change (safe — identical to the committed version):
   `git checkout -- HARDWARE.md`. If anything else is modified, show it and stop; ask Josh.
3. `git pull --ff-only origin harness`. Expect the tip to be the "tools/windows: nightly aborts on a
   dirty tree..." commit or later. Show `git log --oneline -3`.
4. `.venv/Scripts/python.exe -m pip install -e ".[sim,dev]"` then
   `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_harness_determinism.py tests/test_roll_period.py tests/test_scorer.py tests/test_agent_tools.py tests/test_timeline_pairing.py tests/test_windows_scripts.py`
   — all must pass. Then confirm all ten labeled tracks pair with their timelines (two file names carry
   accents; the audio copied from the Mac is in a different Unicode form from the committed timelines,
   which Windows treats as different names — the loader now tolerates this, do NOT rename the mp3s):
   `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); from livereact.loop.evaluate import labeled_tracks; t=labeled_tracks(); print(len(t)); [print(' ', p.name) for p in t]"`
   must print `10` and list Inez and messy among them.
5. Power. Set both the normal and the *unattended* sleep timeouts to never on AC (the unattended one
   is what puts a wake-to-run machine back to sleep after ~2 minutes):
   ```
   powercfg /change standby-timeout-ac 0
   powercfg /change hibernate-timeout-ac 0
   powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP UNATTENDSLEEP 0
   powercfg /setactive SCHEME_CURRENT
   powercfg /query SCHEME_CURRENT SUB_SLEEP
   ```
   Paste the query output in the report.
6. Find out what killed the 01:00 run. In Event Viewer (or `Get-WinEvent`) list System events between
   2026-10-23 01:20 and 01:30: Kernel-Power (41/42/107), Power-Troubleshooter (1), and any
   shutdown/restart (1074, 6006). Report what you find; do not guess.
7. Re-register the task so it runs the new script:
   `powershell -ExecutionPolicy Bypass -File tools/windows/register_task.ps1`.
8. Test it: `powershell -Command "Start-ScheduledTask -TaskName 'LiveReact nightly search'"`, wait 4 minutes,
   show the newest `results/nightly/*.log`. It must show `power: system-required request held`, a
   successful pull (`code: <new tip>`), `1 passed` for the determinism test, and
   `search: 10 tracks, baseline tuning=0.3083 holdout=0.32` (the current baseline — if it says 0.2834
   the code is still stale, stop and report). Then
   `powershell -Command "Stop-ScheduledTask -TaskName 'LiveReact nightly search'"`.
   Delete the aborted test run's folder under `results/search/` so it is not mistaken for a night's work.
9. Confirm the task is enabled, daily 1:00 AM, wake-to-run on, and that `tracks/` still has 29
   `*.timeline.json` files beside the audio. If Josh wants the engine agent instead of the knob search,
   continue with `docs/HANDOFF_windows_agent_setup.md` (it re-registers the task itself).

Do not push. Do not run the search in the foreground. If a step needs Josh's input, ask instead of guessing.
