# Handoff: set up the improvement loop on the Windows desktop

**For:** a Claude Code session on the Windows desktop, started in `%USERPROFILE%\LiveReact`.
Do everything below yourself, verify each step, and report what you did.

1. Confirm the repo: `git status` and `git log --oneline -3` should show branch `harness` pointed at
   `https://github.com/joshuaracimora-lgtm/LiveReact.git`. Do not push and do not change branches.
   If `git` is not on PATH yet, install it first (step 2) and refresh PATH.
2. Install Git, Python 3.12 and ffmpeg with winget if they aren't on PATH: `Git.Git`,
   `Python.Python.3.12`, `Gyan.FFmpeg`. After installing, refresh PATH in your shell (new installs don't
   appear in the current session) and confirm `git --version`, `python --version` (3.12+) and
   `ffmpeg -version` all work.
3. Run `powershell -ExecutionPolicy Bypass -File tools\windows\setup.ps1`. It builds a venv, installs the
   package with the `[sim,dev]` extras, runs the harness/scorer/params tests and does a smoke run of one
   track. Every step must print PASS. If anything prints FAIL, fix the cause and re-run until all PASS.
   `tools\windows\README.md` has context.
4. Power: the PC must never sleep when plugged in. `powercfg /change standby-timeout-ac 0` and
   `powercfg /change hibernate-timeout-ac 0`; confirm with `powercfg /query`.
5. Register the nightly job: `powershell -ExecutionPolicy Bypass -File tools\windows\register_task.ps1`.
   Test it: `Start-ScheduledTask -TaskName 'LiveReact nightly search'`, wait 3 minutes, show the newest
   file in `results\nightly\` (it should show the determinism check passing and the search starting with
   "search: 10 tracks"), then `Stop-ScheduledTask -TaskName 'LiveReact nightly search'`. Confirm the task
   runs daily at 1:00 AM with wake-to-run enabled.
6. Report whether this machine is Windows 10 or 11 and, if 10, whether it is enrolled in Extended
   Security Updates.

Don't edit code, `params.json`, or anything under `tracks\`. Don't run the search in the foreground;
the scheduled task is how it runs. If a step needs Josh's input, ask instead of guessing.
