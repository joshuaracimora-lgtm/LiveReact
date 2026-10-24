# Handoff: turn the desktop nightly into the engine agent

**For:** a Claude Code session on the Windows desktop, started in `%USERPROFILE%\LiveReact`, AFTER
`docs/HANDOFF_windows_resync.md` has been completed (clean tree, `harness` up to date, power settings done).
**What changes:** the 1 AM task stops running the knob search alone (`nightly.ps1`) and runs
`nightly_agent.ps1` instead: a headless Claude Code session improves the engine against the scorer for
4 hours on a branch `agent/<date>`, the orchestrator verifies its claim with its own gate run, and only
then spends 1.5 hours on the knob search around the new code. Josh reviews the branch in the morning.
Do everything below yourself, verify each step, and report. Do not edit code or `params.json`.

1. `git status --short` must be empty and `git log --oneline -1` must show the commit that added
   `tools\windows\nightly_agent.ps1` or later. If not, finish the resync brief first.
2. **Claude Code on this machine.** Check `claude --version`. If missing, install it the way the
   current docs say (https://docs.claude.com/en/docs/claude-code/setup — on Windows PowerShell the
   native installer is `irm https://claude.ai/install.ps1 | iex`), open a new shell, confirm
   `claude --version`. Then **stop and ask Josh to sign in**: run `claude` interactively once in the
   repo and complete `/login` with his Anthropic account. The agent cannot log in by itself and must
   never be given an API key in a file. Confirm afterwards with
   `echo "reply with the single word ok" | claude -p --output-format text` printing `ok`.
3. Dry run of the orchestrator, short budget, no search, so Josh sees the whole shape once:
   `powershell -ExecutionPolicy Bypass -File tools\windows\nightly_agent.ps1 -Hours 0.25 -SearchHours 0`
   Expect in the log: `power: ...held`, `code: <harness tip>`, the determinism test passing,
   `claude: <version>`, `branch: agent/<date>`, `agent: starting`, then after ~15 min either
   `agent: exited 0` or `agent: deadline reached`. Then `results\agent\<date>\` must contain
   `prompt.md`, `agent.out`, `NIGHT.md`, and (if it got that far) `REPORT.md`. Show `NIGHT.md`.
   If `agent.out` shows a permission or login error, fix that first; nothing else matters until the
   session runs.
4. Clean up the dry run: `git checkout harness`, `git branch -D agent/<date>` (delete only the dry-run
   branch; show `git branch` first), and delete `results\agent\<date>\`. Leave `results\agent\state.json`
   only if it is empty of nights; otherwise delete it too so the first real night starts fresh.
5. Re-register the task for the agent:
   `powershell -ExecutionPolicy Bypass -File tools\windows\register_task.ps1 -Script nightly_agent.ps1 -Args "-Hours 4 -SearchHours 1.5"`
   Add `-Push` to the `-Args` string only if Josh says he wants the branch pushed to GitHub each night
   (then `git push` must already work from this machine without a prompt: test `git push --dry-run origin harness`).
6. Confirm the task: enabled, daily 1:00 AM, wake-to-run on, execution limit 8 h, action shows
   `nightly_agent.ps1 -Hours 4 -SearchHours 1.5`.
7. Report: Claude Code version, whether login succeeded, the dry run's `NIGHT.md`, and the task's
   action line. Mention anything the agent tried to do that the brief forbids.

Each morning Josh reads `results\agent\<date>\NIGHT.md` (the orchestrator's verified summary) and
`REPORT.md` (the agent's own account), then on the Mac:

```
git fetch origin && git log --oneline harness..origin/agent/<date>     # if -Push is on
git merge --ff-only origin/agent/<date>                                 # accept the night into harness
```

or deletes the branch. Nothing the agent does reaches `harness` without that merge.

Do not push `harness`. Do not run the search in the foreground. If a step needs Josh's input, ask.
