# Handoff: turn the desktop nightly into the engine agent

**For:** a Claude Code session on the Windows desktop, started in `%USERPROFILE%\LiveReact`, AFTER
`docs/HANDOFF_windows_resync.md` has been completed (clean tree, `harness` up to date, power settings done).
**What changes:** the 1 AM task stops running the knob search alone (`nightly.ps1`) and runs
`nightly_agent.ps1` instead: a headless Claude Code session improves the engine against the scorer for
4 hours on a branch `agent/<date>`, the orchestrator verifies its claim with its own gate run, and only
then spends 1.5 hours on the knob search around the new code. Josh reviews the branch in the morning.
Do everything below yourself, verify each step, and report. Do not edit code or `params.json`.

**Shell note:** your Bash tool is Git Bash. Use forward slashes in paths. PowerShell cmdlets
(`Start-ScheduledTask`, `Get-ScheduledTask`, `Stop-ScheduledTask`, `powercfg` works anywhere) must be
wrapped: `powershell -Command "Start-ScheduledTask -TaskName 'LiveReact nightly search'"`.

1. `git status --short` must be empty and `git log --oneline -1` must show the commit that added
   `tools/windows/nightly_agent.ps1` or later. If not, finish the resync brief first.
2. **Git identity** (the agent commits; a scheduled task has no interactive git). Repo-local, the same
   identity the Mac commits with so pushed commits attribute to Josh's GitHub account:
   `git config user.name "Joshua Racimora"` and `git config user.email joshuaracimora@gmail.com`.
   (Every agent commit also carries a Co-Authored-By line naming Claude.) Confirm `git config user.email`.
3. **Claude Code on this machine.** Check `claude --version`. If missing, install it the way the
   current docs say (https://docs.claude.com/en/docs/claude-code/setup — on Windows the native
   installer is run from PowerShell: `irm https://claude.ai/install.ps1 | iex`), open a new shell,
   confirm `claude --version`. Then **stop and ask Josh to sign in**: he runs `claude` interactively
   once in the repo and completes `/login` with his Anthropic account. The agent cannot log in by itself
   and must never be given an API key in a file. Confirm afterwards with
   `echo "reply with the single word ok" | claude -p --output-format text` printing `ok`.
   Then confirm the deny list is honoured under skip-permissions (this is what keeps the agent off
   `git push`): `echo "Run exactly: git push --dry-run origin harness . Then quote the tool's response verbatim." | claude -p --dangerously-skip-permissions --disallowedTools "Bash(git push:*)" --output-format text`
   must show the command being refused, not a dry-run result. If it ran, stop and report.
4. Dry run of the orchestrator, short budget, no search, so Josh sees the whole shape once:
   `powershell -ExecutionPolicy Bypass -File tools/windows/nightly_agent.ps1 -Hours 0.25 -SearchHours 0`
   Expect in the log (`results/nightly/<date>-agent.log`): `job: kill-on-close job object held`, `power: ...held`, `claude: <version>`,
   `code: <harness tip>`, `1 passed` for the determinism test, `branch: agent/<date>`, `agent: starting`, then
   after ~15 min either `agent: exited 0` or `agent: deadline reached`. `results/agent/<date>/` must
   contain `prompt.md`, `agent.out`, `NIGHT.md`, and (if it got that far) `REPORT.md`. Show `NIGHT.md`
   and the first 40 lines of `agent.out`. If `agent.out` or `agent.err` shows a permission, login or
   "command not found" error, fix that first; nothing else matters until the session runs and can call
   `.venv/Scripts/python.exe`.
5. Clean up the dry run. Show `git branch` and `git stash list` first. Then: `git checkout harness`,
   `git branch -D agent/<date>` (only the dry-run branch), drop any stash named `agent-<date>-leftovers`,
   delete `results/agent/<date>/`, and delete `results/agent/state.json` so the first real night starts
   fresh.
6. Re-register the task for the agent:
   `powershell -ExecutionPolicy Bypass -File tools/windows/register_task.ps1 -Script nightly_agent.ps1 -ScriptArgs "-Hours 4 -SearchHours 1.5"`
   Add ` -Push` inside the `-ScriptArgs` string only if Josh says he wants each night's branch and
   reports pushed to GitHub (then pushing must already work here without a prompt: test
   `git push --dry-run origin harness`).
7. Test the registered task (this proves PATH and login work under Task Scheduler, not just in your shell):
   `powershell -Command "Start-ScheduledTask -TaskName 'LiveReact nightly search'"`, wait 3 minutes, show the
   newest `results/nightly/*-agent.log`: it must reach `agent: starting`. Then
   `powershell -Command "Stop-ScheduledTask -TaskName 'LiveReact nightly search'"`, and repeat step 5's cleanup
   for the branch and folder it created (kill any leftover `claude`/`node` process from the test first:
   `tasklist | grep -i -E "claude|node"`).
8. Confirm the task: enabled, daily 1:00 AM, wake-to-run on, execution limit 8 h, action shows
   `nightly_agent.ps1 -Hours 4 -SearchHours 1.5`. Note for Josh: the task runs only while his user is
   logged on (locked is fine). If Windows Update reboots the PC overnight and nobody logs back in, the
   night is skipped — tell him, so he can decide on auto-login or "run whether user is logged on or not".
9. Report: Claude Code version, whether login succeeded, the dry run's `NIGHT.md`, the task's action
   line, and anything the agent tried to do that the brief forbids.

Each morning Josh reads `results/agent/<date>/NIGHT.md` (the orchestrator's verified summary) and
`REPORT.md` (the agent's own account), then on the Mac:

```
git fetch origin && git log --oneline harness..origin/agent/<date>     # if -Push is on
git merge --ff-only origin/agent/<date>                                 # accept the night into harness
```

or deletes the branch. Nothing the agent does reaches `harness` without that merge.

Do not push `harness`. Do not run the search in the foreground. If a step needs Josh's input, ask.
