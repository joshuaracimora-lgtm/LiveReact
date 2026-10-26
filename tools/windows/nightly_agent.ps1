<# The nightly ENGINE AGENT: pull harness, branch, let a headless Claude Code session improve the engine
   against the scorer for N hours, verify its claim independently, then tune knobs around whatever it
   landed. Everything it prints goes to results\nightly\<date>-agent.log; the night's artefacts go to
   results\agent\<date>\ (prompt.md, agent.out, agent.err, gate_verify.json, NIGHT.md, the agent's REPORT.md).
   Exit codes: 0 ok, 2 agent/search crash, 3 preflight failed, 4 the agent's work failed verification.
   Requires Claude Code on PATH and signed in, and a git identity (docs\HANDOFF_windows_agent_setup.md). #>
param(
  [double]$Hours = 4,            # agent wall-clock budget
  [double]$SearchHours = 1.5,    # knob search after an accepted night (0 = skip)
  [double]$SweepMinutes = 40,    # bug/invariant sweep over the USB export before the agent (0 = skip)
  [int]$Patience = 300,
  [string]$Model = "",           # e.g. "opus"; empty = Claude Code's default
  [double]$MaxBudgetUsd = 0,     # 0 = no cap
  [switch]$Push                  # commit the night's reports into docs\agent_nights\ and push the branch
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
$date = Get-Date -Format yyyyMMdd
New-Item -ItemType Directory -Force -Path results\nightly, "results\agent\$date" | Out-Null
$night = Join-Path $root "results\agent\$date"
$log = "results\nightly\$(Get-Date -Format yyyyMMdd-HHmm)-agent.log"
Start-Transcript -Path $log | Out-Null
$py = ".\.venv\Scripts\python.exe"
$utf8 = New-Object System.Text.UTF8Encoding($false)   # no BOM: these files are read by python/claude
$exit = 0

. "$PSScriptRoot\common.ps1"      # UTF-8 python env, Hold-Wake/Release-Wake, Join-KillOnCloseJob
Join-KillOnCloseJob
$awake = Hold-Wake
$held = Hold-PowerRequest "LiveReact nightly engine agent"
$planOk = Check-PowerPlan
$planSet = Set-RunPowerPlan     # sleep never for the run only; restored in finally
$proc = $null

function Fail($msg) { Write-Host "PREFLIGHT FAILED: $msg" }

try {
  # ---- preflight ---------------------------------------------------------------------------------
  # without git every check below reads as empty and the run looks clean (seen 2026-10-23)
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found on PATH"; exit 3 }
  # tracked changes block the pull; untracked files (e.g. .claude/settings.local.json from an interactive
  # session) are tolerated here and only matter after the agent (stashed below)
  $dirty = @(git status --porcelain 2>$null | Where-Object { $_ -notmatch '^\?\?' })
  if ($dirty.Count -gt 0) { Fail "working tree has local changes (stash or checkout):`n$($dirty -join "`n")"; exit 3 }
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Fail "claude CLI not found on PATH"; exit 3 }
  $claudeVer = (& claude --version 2>&1 | Select-Object -First 1)
  Write-Host "claude: $claudeVer"
  if (-not (git config user.email)) { Fail "git identity missing: git config user.name / user.email (repo-local is fine)"; exit 3 }
  git fetch origin 2>&1 | Out-Null
  git checkout harness 2>&1 | Out-Null
  $pull = git pull --ff-only origin harness 2>&1
  $pull | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { Fail "git pull --ff-only did not succeed"; exit 3 }
  $base = (git rev-parse --short HEAD).Trim()
  Write-Host ("code: " + (git log --oneline -1))
  & $py -m pip install --quiet -e ".[sim,dev]" 2>&1 | Out-Null
  & $py -m pytest -o addopts="" -q tests\test_harness_determinism.py 2>&1 | Select-Object -Last 1
  if ($LASTEXITCODE -ne 0) { Fail "determinism test"; exit 3 }
  # the planner era needs the rekordbox USB export copy (docs\HANDOFF_windows_resync.md step 10)
  if (-not (Test-Path "usb_export\PIONEER\rekordbox\export.pdb")) { Fail "usb_export\PIONEER\rekordbox\export.pdb missing: copy the rekordbox USB export into usb_export\"; exit 3 }

  # ---- sweep (bugs, invariants, plan sanity over every USB track; exit 1 = findings, not a failed night) --
  $sweepNote = "skipped"
  $sweepExit = 0
  if ($SweepMinutes -gt 0) {
    Write-Host "sweep: $SweepMinutes min over usb_export -> $night\SWEEP.md"
    & $py tools\agent\sweep.py --minutes $SweepMinutes --out $night 2>&1 | Out-Host
    $sweepExit = $LASTEXITCODE
    $sweepMd = Join-Path $night "SWEEP.md"
    if (Test-Path $sweepMd) { $sweepNote = (Get-Content $sweepMd -Encoding UTF8 | Select-Object -First 40) -join "`n" }
    else { $sweepNote = "sweep wrote no SWEEP.md (exit $sweepExit)" }
    Write-Host "sweep: exit $sweepExit"
  }

  # ---- branch ------------------------------------------------------------------------------------
  $branch = "agent/$date"
  $k = 2
  while (git rev-parse --verify --quiet $branch 2>$null) { $branch = "agent/$date-$k"; $k++ }
  git checkout -b $branch 2>&1 | Out-Null
  Write-Host "branch: $branch (from harness $base)"

  # ---- prompt ------------------------------------------------------------------------------------
  $start = Get-Date
  $deadline = $start.AddHours($Hours)
  $header = @"
# Runtime header (generated by nightly_agent.ps1)
- date: $date
- branch: $branch (created from harness @ $base)
- repo: $root  (Windows). Your Bash tool is Git Bash: use forward slashes and the venv interpreter
  ``.venv/Scripts/python.exe`` for every python/pytest command, e.g. ``.venv/Scripts/python.exe -m pytest -q``.
- start: $($start.ToString('yyyy-MM-dd HH:mm'))   deadline: $($deadline.ToString('yyyy-MM-dd HH:mm'))  (hard kill at the deadline)
- night folder for your report, gate outputs and study scripts: results/agent/$date/
- the knob search runs AFTER you, by the orchestrator; do not run it yourself
- usb export: usb_export/ (gate_head.py, dump_signals.py and sweep.py find it on their own)
- sweep: exit $sweepExit (0 clean, 1 findings, 2 tool error). SWEEP.md head:

$sweepNote

"@
  $brief = Get-Content docs\AGENT_BRIEF.md -Raw -Encoding UTF8
  $promptPath = Join-Path $night "prompt.md"
  [IO.File]::WriteAllText($promptPath, ($header + $brief), $utf8)

  # ---- run the agent -----------------------------------------------------------------------------
  $outPath = Join-Path $night "agent.out"
  $errPath = Join-Path $night "agent.err"
  $deny = @("Bash(git push:*)", "Bash(git checkout harness:*)", "Bash(git checkout main:*)", "Bash(git switch:*)",
            "Bash(git merge:*)", "Bash(git rebase:*)", "Bash(git branch -D:*)", "Bash(git reset --hard:*)",
            "Bash(pip install:*)", "Bash(python -m pip:*)", "Bash(.venv/Scripts/python.exe -m pip:*)",
            "Bash(./.venv/Scripts/python.exe -m pip:*)", "Bash(.venv/Scripts/pip:*)") | ForEach-Object { "`"$_`"" }
  $flags = "-p --dangerously-skip-permissions --output-format text --no-session-persistence --disallowedTools " + ($deny -join " ")
  if ($Model) { $flags += " --model $Model" }
  if ($MaxBudgetUsd -gt 0) { $flags += " --max-budget-usd $MaxBudgetUsd" }
  $cmd = "type `"$promptPath`" | claude $flags > `"$outPath`" 2> `"$errPath`""
  Write-Host "agent: starting at $($start.ToString('HH:mm')), budget $Hours h"
  $proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -WorkingDirectory $root -PassThru -WindowStyle Hidden
  $finished = $proc.WaitForExit([int]($Hours * 3600 * 1000))
  if (-not $finished) {
    Write-Host "agent: deadline reached, killing the session tree"
    & taskkill /T /F /PID $proc.Id 2>&1 | Out-Null
    $agentStatus = "killed at deadline"
  } else { $agentStatus = "exited $($proc.ExitCode)" }
  $agentMin = [int]((Get-Date) - $start).TotalMinutes
  Write-Host "agent: $agentStatus after $agentMin min"
  if (Test-Path $errPath) { Get-Content $errPath -Encoding UTF8 | Select-Object -Last 15 | ForEach-Object { Write-Host "agent.err: $_" } }

  # ---- verify independently ----------------------------------------------------------------------
  $left = @(git status --porcelain 2>$null)
  if ($left.Count -gt 0) {
    Write-Host "agent left uncommitted/untracked changes; stashing them as 'agent-$date-leftovers':"
    $left | ForEach-Object { Write-Host "  $_" }
    git stash push -u -m "agent-$date-leftovers" 2>&1 | ForEach-Object { Write-Host "  stash: $_" }
    if (@(git status --porcelain 2>$null).Count -gt 0) {
      # a hard kill mid-commit can leave .git\index.lock behind; clear it and retry once
      if (Test-Path ".git\index.lock") { Remove-Item ".git\index.lock" -Force; Write-Host "  removed .git\index.lock" }
      git stash push -u -m "agent-$date-leftovers" 2>&1 | ForEach-Object { Write-Host "  stash retry: $_" }
    }
  }
  $treeClean = (@(git status --porcelain 2>$null).Count -eq 0)
  $commits = [int](git rev-list --count "harness..HEAD" 2>$null)
  Write-Host "commits on $branch : $commits"
  git log --oneline "harness..HEAD" 2>$null | ForEach-Object { Write-Host "  $_" }
  if ($finished -and $proc.ExitCode -ne 0 -and $commits -eq 0) {
    # the session died (login expired, CLI error, Git Bash missing...): not a quiet night, a failed one
    $agentStatus += " -- AGENT FAILED, see agent.err"
    $exit = 2
  }
  git diff --quiet harness HEAD -- results/baseline/evaluation.json 2>$null
  $baselineRegen = if ($LASTEXITCODE -eq 0) { "no" } else { "yes" }
  # Scope (planner era, docs\AGENT_BRIEF.md "Build, test, gate" step 1): the agent may change ONLY these
  # paths. Anything else in its commits fails the night before tests, gate or search run: the gate below
  # runs the BRANCH's scorer and track set, so a branch that edited them could pass a check it rewrote.
  # Deleting a test file counts too.
  $outOfScope = @()
  if ($commits -gt 0) {
    $allowRe = '^(src/livereact/rekordbox/(planner|mapping)\.py$|src/livereact/core/metronome\.py$|params\.json$|rekordbox_map\.json$|tests/.+|results/baseline/evaluation\.json$)'
    $outOfScope = @(git -c core.quotepath=off diff --name-only "harness..HEAD" 2>$null | Where-Object { $_ -notmatch $allowRe })
    $outOfScope += @(git -c core.quotepath=off diff --name-only --diff-filter=D "harness..HEAD" -- tests 2>$null | ForEach-Object { "$_ (test file deleted)" })
  }
  $tests = "skipped"; $gate = "NO-CHANGE"
  if ($outOfScope.Count -gt 0) {
    Write-Host "OUT OF SCOPE: the agent's commits touch files it may not change; not verifying or searching:"
    $outOfScope | ForEach-Object { Write-Host "  $_" }
    $gate = "OUT-OF-SCOPE"; $exit = 4
  } elseif ($commits -gt 0 -and -not $treeClean) {
    Write-Host "working tree still dirty after stashing; verification would judge the wrong code"
    $tests = "FAIL"; $gate = "ERROR"; $exit = 4
  } elseif ($commits -gt 0) {
    # addopts="" so pytest prints the FAILED/ERROR lines and the "N failed, M passed" count
    $pt = @(& $py -m pytest -o addopts="" -q 2>&1)
    $ptExit = $LASTEXITCODE
    $summary = ($pt | Select-Object -Last 1)
    Write-Host "pytest (exit $ptExit): $summary"
    $failed = 0; if ($summary -match '(\d+) failed') { $failed = [int]$Matches[1] }
    $unknown = @($pt | Where-Object { $_ -match '^(FAILED|ERROR)' } | Where-Object { $_ -notmatch 'test_spec_conformance_gaps' })
    $tests = if ($ptExit -le 1 -and $failed -le 3 -and $unknown.Count -eq 0 -and $summary -match 'passed') { "ok" } else { "FAIL" }
    $unknown | ForEach-Object { Write-Host "  $_" }
    $headEval = Join-Path $night "head_eval.json"
    & $py tools\agent\gate_head.py --baseline-ref harness --usb usb_export --drive --out (Join-Path $night "gate_verify.json") --out-eval $headEval 2>&1 | Select-Object -Last 30
    $gate = if ($LASTEXITCODE -eq 0) { "ACCEPT" } elseif ($LASTEXITCODE -eq 1) { "REJECT" } else { "ERROR" }
    Write-Host "independent gate vs harness baseline: $gate ; tests: $tests ; baseline regenerated by agent: $baselineRegen"
    if ($gate -ne "ACCEPT" -or $tests -ne "ok") { $exit = 4 }
  }

  # ---- knob search around the new code (only when the night is good) -----------------------------
  # The search's baseline is the verified evaluation of HEAD (head_eval.json), never the committed file:
  # if the agent forgot to regenerate it, a stale baseline would credit the engine's gain to the first
  # harmless random perturbation.
  $searchNote = "skipped"
  $searchOut = $null
  if ($SearchHours -gt 0 -and $commits -gt 0 -and $gate -eq "ACCEPT" -and $tests -eq "ok" -and (Test-Path $headEval)) {
    $searchOut = "results\search\$date-$(Get-Date -Format HHmm)-agent"
    Write-Host "search: $SearchHours h around $branch -> $searchOut"
    # Out-Host: a native command's stdout does not reach the transcript on its own under Task Scheduler
    & $py tools\loop\search.py --hours $SearchHours --patience $Patience --baseline $headEval --usb usb_export --drive --out $searchOut 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 2) { $exit = 2 }
  } elseif ($SearchHours -gt 0 -and $treeClean) {
    # Nothing accepted: tune knobs on harness itself for the search budget plus whatever the agent left
    # unused, so a quiet agent night is not an idle one (2026-10-24: agent done after 30 min of 4 h).
    # harness's committed baseline is its own evaluation, so search.py's default --baseline is right.
    # [double]0: with an int first argument PowerShell picks Max(int,int) and truncates 3.4 h to 3
    $fallbackHours = [math]::Round($SearchHours + [math]::Max([double]0, $Hours - ((Get-Date) - $start).TotalHours), 2)
    $searchOut = "results\search\$date-$(Get-Date -Format HHmm)-harness"
    git checkout harness 2>&1 | Out-Null
    Write-Host "search: nothing accepted tonight; $fallbackHours h on harness $base -> $searchOut"
    & $py tools\loop\search.py --hours $fallbackHours --patience $Patience --usb usb_export --drive --out $searchOut 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 2) { $exit = 2 }
    git checkout $branch 2>&1 | Out-Null
  }
  if ($searchOut) {
    $sm = "$searchOut\summary.md"
    if (Test-Path $sm) { $searchNote = (Get-Content $sm -Encoding UTF8 | Select-Object -First 6) -join "`n" ; Write-Host $searchNote }
    else { $searchNote = "no summary.md in $searchOut (search did not finish)" }
  }

  # ---- morning note ------------------------------------------------------------------------------
  $report = Join-Path $night "REPORT.md"
  $reportHead = if (Test-Path $report) { (Get-Content $report -Encoding UTF8 | Select-Object -First 60) -join "`n" } else { "(the agent wrote no REPORT.md)" }
  $nightMd = @"
# Night $date - $branch

- sweep: exit $sweepExit (0 clean, 1 findings, 2 error) -> $night\SWEEP.md
- agent: $agentStatus ($agentMin min); commits: $commits; tests: $tests; independent gate vs harness ($base): $gate
- baseline regenerated by the agent: $baselineRegen (the brief requires it on every accepted commit)
- out of scope: $(if ($outOfScope.Count) { ($outOfScope -join ', ') } else { 'none' })
- knob search:
$searchNote

## Power events during the run (from 5 min before the start)
$((Get-PowerEvents $start) -join "`n")

## Commits
$((git log --oneline "harness..HEAD" 2>$null) -join "`n")

## Agent report (head)
$reportHead
"@
  [IO.File]::WriteAllText((Join-Path $night "NIGHT.md"), $nightMd, $utf8)
  & $py tools\agent\record_night.py --date $date --branch $branch --commits $commits --gate $gate --tests $tests --note "orchestrator: $agentStatus" | Out-Null
  if ($Push) {
    # Every night, not only nights with commits: the reports (and a night's questions for Josh) are the
    # point of a quiet night too. On a 0-commit night the branch holds only this reports commit.
    # The night folder is gitignored; carry the two reports in-repo so the branch is reviewable from GitHub.
    $dst = "docs\agent_nights\$date"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item (Join-Path $night "NIGHT.md") "$dst\NIGHT.md" -Force
    if (Test-Path $report) { Copy-Item $report "$dst\REPORT.md" -Force }
    if (Test-Path (Join-Path $night "SWEEP.md")) { Copy-Item (Join-Path $night "SWEEP.md") "$dst\SWEEP.md" -Force }
    # the knob search's result lives in gitignored results\search\; carry it too so the Mac can apply it
    # (search.json = the search's summary.json, renamed so it does not read as the night's summary)
    if ($searchOut) {
      foreach ($f in @(@("best_params.json", "best_params.json"), @("summary.md", "search.md"), @("summary.json", "search.json"))) {
        if (Test-Path "$searchOut\$($f[0])") { Copy-Item "$searchOut\$($f[0])" "$dst\$($f[1])" -Force }
      }
    }
    git add $dst 2>&1 | Out-Null
    git commit -q -m "agent night $date : reports" 2>&1 | Out-Null
    git push -u origin $branch 2>&1 | ForEach-Object { Write-Host $_ }
  }
  git checkout harness 2>&1 | Out-Null
  Write-Host "done: $branch commits=$commits gate=$gate tests=$tests -> $night\NIGHT.md"
  exit $exit
} catch {
  # without this an unexpected error skips the rest silently and the task reports 0 (seen 2026-10-23)
  Write-Host "CRASH: $($_.Exception.Message)"
  Write-Host $_.ScriptStackTrace
  try { [IO.File]::WriteAllText((Join-Path $night "NIGHT.md"), "# Night $date - CRASHED`n`n$($_.Exception.Message)`n`n$($_.ScriptStackTrace)`n", $utf8) } catch {}
  git checkout harness 2>&1 | Out-Null
  exit 2
} finally {
  if ($proc -and -not $proc.HasExited) { Write-Host "cleanup: agent still running, killing its tree"; & taskkill /T /F /PID $proc.Id 2>&1 | Out-Null }
  if ($awake -or $held) { Release-Wake }
  Restore-RunPowerPlan
  Stop-Transcript | Out-Null
}
