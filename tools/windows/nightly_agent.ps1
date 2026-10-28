<# The nightly AGENTS (three since 2026-10-27): pull harness, run the sweep, then three headless Claude Code
   sessions one after the other, each on its own branch cut from harness and verified independently:
     1. Bugs & UI (docs\AGENT_BRIEF_UI.md)       -> branch agent/<date>-ui,  results\agent\<date>\ui\
     2. App (docs\AGENT_BRIEF_APP.md)            -> branch agent/<date>-app, results\agent\<date>\app\
     3. Track Classifier (docs\AGENT_BRIEF.md)    -> branch agent/<date>,     results\agent\<date>\tracks\
   then tune knobs around whatever the Track Classifier landed. -HardStop (default 23:30) caps every agent's
   deadline and the search so the night is over before the PC goes off at midnight. Everything it prints goes to
   results\nightly\<date>-agent.log, every step timestamped; the night's artefacts go to results\agent\<date>\
   (SWEEP.md, baseline_before.json, NIGHT.md, and per role prompt.md, agent.out, agent.err, gate_verify.json,
   the agent's REPORT.md). Exit codes: 0 ok, 2 an agent/search crashed, 3 preflight failed, 4 an agent's
   work failed verification. Requires Claude Code on PATH and signed in, and a git identity
   (docs\HANDOFF_windows_agent_setup.md). #>
param(
  [double]$Hours = 3,            # Track Classifier wall-clock budget
  [double]$UiHours = 1.5,        # Bugs & UI agent budget (0 = skip that agent)
  [double]$AppHours = 2.5,       # App agent budget: docs\APP_ROADMAP.md (0 = skip that agent)
  [string]$HardStop = "23:30",   # nothing runs past this clock time today (agents, search); "" = no cap
  [double]$SearchHours = 1.5,    # knob search after the Track Classifier (0 = skip)
  [double]$SweepMinutes = 40,    # bug/invariant sweep over the USB export before the agents (0 = skip)
  [int]$Patience = 300,
  [string]$Model = "",           # e.g. "opus"; empty = Claude Code's default
  [double]$MaxBudgetUsd = 0,     # per agent; 0 = no cap
  [switch]$Push,                 # push the agents' branches (reports stay local: the repo is public, see tools\private_bundle.py)
  [string]$AgentCmd = "",        # stress tests only: a command line run INSTEAD of claude (reads the prompt on stdin;
                                 # $env:LIVEREACT_AGENT_ROLE tells it which agent it is)
  [string]$PulledFrom = ""       # set by self_update.ps1 on the re-run: the harness commit it pulled from
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
. "$PSScriptRoot\self_update.ps1"
$rerun = Invoke-PulledSelf $PSCommandPath $PSBoundParameters
if ($rerun -ne $null) { exit $rerun }
$date = Get-Date -Format yyyyMMdd
# a second run on the same date (2026-10-28: an overnight run, then the 2 PM one) gets its own folder,
# as its branches get -2, or it would overwrite the first run's NIGHT.md and agent reports
$nightTag = $date; $k = 2
while (Test-Path (Join-Path $root "results\agent\$nightTag")) { $nightTag = "$date-$k"; $k++ }
New-Item -ItemType Directory -Force -Path results\nightly, "results\agent\$nightTag" | Out-Null
$night = Join-Path $root "results\agent\$nightTag"
$log = "results\nightly\$(Get-Date -Format yyyyMMdd-HHmm)-agent.log"
Start-Transcript -Path $log | Out-Null
if ($PulledFrom) { Write-Host "self-update: pulled harness $PulledFrom -> $("$(git rev-parse --short harness 2>$null)".Trim()) and re-ran this script from the new code" }
$py = ".\.venv\Scripts\python.exe"
$utf8 = New-Object System.Text.UTF8Encoding($false)   # no BOM: these files are read by python/claude
$exit = 0
$nightStart = Get-Date

. "$PSScriptRoot\common.ps1"      # UTF-8 python env, Hold-Wake/Release-Wake, Join-KillOnCloseJob
Join-KillOnCloseJob
$awake = Hold-Wake
$held = Hold-PowerRequest "LiveReact nightly engine agents"
$planOk = Check-PowerPlan
$planSet = Set-RunPowerPlan     # sleep never for the run only; restored in finally
$script:proc = $null

function Fail($msg) { Write-Host "PREFLIGHT FAILED: $msg" }
# every step is timestamped: a 3.5 h silent gap (2026-10-25, between the search and the end) had no cause on record
function Step($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" }

# Scope per role: an agent may change ONLY these paths (each brief's "Build" list). Anything else in its
# commits fails it before tests, gate or search run: the gate runs the BRANCH's scorer and track set, so a
# branch that edited them could pass a check it rewrote. Deleting a test file counts too.
$AllowTracks = '^(src/livereact/rekordbox/(planner|mapping|sections)\.py$|src/livereact/core/metronome\.py$|params\.json$|rekordbox_map\.json$|section_classes\.json$|tests/.+)'
$AllowUi = '^(src/livereact/ui/.+|src/livereact/fixtures/(geometry|rig)\.py$|docs/UI_BUGS\.md$|tests/.+)'
$UiFamily0 = '^src/livereact/(rekordbox|core)/[^/]+\.py$'   # the ONE file a sweep traceback names (family 0)
# the App agent builds around the show, never in it: no app.py, engines, scenes, merge, sim, profiles.py
$AllowApp = '^(src/livereact/(userdata|settings)\.py$|src/livereact/fixtures/(definition|generic|library|traits|ofl|rig)\.py$|src/livereact/fixtures/(builtin|manual_reader)/.+|src/livereact/presets/.+|src/livereact/ui/.+|packaging/.+|\.github/workflows/release\.yml$|docs/APP_ROADMAP\.md$|tests/(?!conftest\.py$).+)'

# -HardStop as a clock time today; agents and the search are clamped to it (2026-10-27: the PC goes off at
# midnight). A task started after the stop time gets no agents rather than one that runs into the night.
$hardStopAt = $null
if ($HardStop) { $hardStopAt = [datetime]::Today.Add([timespan]::Parse($HardStop)) }
function Get-HoursLeft([int]$ReserveMin) {
  # hours until the hard stop minus a reserve (an agent's gates + push); a large number when there is no cap
  if (-not $hardStopAt) { return [double]1e6 }
  return [math]::Max([double]0, ($hardStopAt.AddMinutes(-$ReserveMin) - (Get-Date)).TotalHours)
}

function Test-ApiReachable {
  # DNS + TCP 443 to the API host, 5 s cap; no HTTP, no credentials
  try {
    $null = [System.Net.Dns]::GetHostAddresses("api.anthropic.com")
    $c = New-Object System.Net.Sockets.TcpClient
    $ok = $c.ConnectAsync("api.anthropic.com", 443).Wait(5000) -and $c.Connected
    $c.Close()
    return $ok
  } catch { return $false }
}

function Wait-ApiReachable([string]$Role, [int]$MaxMinutes) {
  # 2026-10-27: the network dropped at 14:28 and all three agents died in 3 min each on ENOTFOUND.
  # Wait for it (one check a minute) rather than burn an agent; never past the hard stop.
  if (Test-ApiReachable) { return $true }
  $until = (Get-Date).AddMinutes($MaxMinutes)
  if ($hardStopAt -and $hardStopAt.AddMinutes(-26) -lt $until) { $until = $hardStopAt.AddMinutes(-26) }
  Step "$Role agent: API unreachable, waiting up to $($until.ToString('HH:mm')) for the network"
  while ((Get-Date) -lt $until) {
    Start-Sleep -Seconds 60
    if (Test-ApiReachable) { Step "$Role agent: network back"; return $true }
  }
  Step "$Role agent: API still unreachable at $(Get-Date -Format 'HH:mm')"
  return $false
}

function Get-Changed([string]$range) { @(git -c core.quotepath=off diff --name-only $range 2>$null | Where-Object { $_ }) }

function Invoke-GitTimed([string[]]$GitArgs, [int]$Seconds) {
  # git push once hung the night (suspected credential prompt); never wait more than $Seconds
  $o = Join-Path $env:TEMP "lr_git_$PID.out"; $e = Join-Path $env:TEMP "lr_git_$PID.err"
  $p = Start-Process -FilePath "git" -ArgumentList $GitArgs -NoNewWindow -PassThru -RedirectStandardOutput $o -RedirectStandardError $e
  # read the handle now: without it ExitCode stays empty after exit and every push read as failed (2026-10-26)
  $null = $p.Handle
  if (-not $p.WaitForExit($Seconds * 1000)) {
    & taskkill /T /F /PID $p.Id 2>&1 | Out-Null
    Write-Host "  git $($GitArgs -join ' '): no answer after $Seconds s, killed"
    return 124
  }
  foreach ($f in $o, $e) { if (Test-Path $f) { Get-Content $f | ForEach-Object { Write-Host "  $_" }; Remove-Item $f -Force } }
  return $p.ExitCode
}

function Test-ClassApprovals([string]$range) {
  # Josh alone approves a section class or names its scene (rekordbox/section_classes.py)
  $chk = @'
import json, subprocess, sys
base, head = sys.argv[1].split("..")
def load(ref):
    r = subprocess.run(["git", "show", ref + ":section_classes.json"], capture_output=True)
    if r.returncode != 0:
        return {}
    try:
        d = json.loads(r.stdout.decode("utf-8"))
    except ValueError:
        return {"_broken": True}
    out = {c.get("name"): (bool(c.get("approved")), c.get("scene")) for c in d.get("classes") or []}
    out["_palette"] = (bool((d.get("palette_by_profile") or {}).get("approved")), None)
    return out
b, h = load(base), load(head)
none = (False, None)
# the agent may change rules and notes, never approved/scene: any difference there, added or removed, is Josh's
bad = [k for k in set(b) | set(h) if b.get(k, none) != h.get(k, none)]
print(",".join(sorted(str(x) for x in bad)))
'@
  $out = ($chk | & $py - $range 2>$null)
  return "$out".Trim()
}

function Invoke-NightAgent([string]$Role, [string]$Brief, [double]$AgentHours, [string]$AllowRe, [string]$BaselineBefore, [string]$SweepNote, [int]$SweepExit, [string]$Base) {
  $r = [ordered]@{ Role = $Role; Branch = ""; Commits = 0; Status = "skipped"; Minutes = 0; Gate = "NO-CHANGE"; Tests = "skipped"
                   OutOfScope = @(); HeadEval = $null; Start = (Get-Date); Exit = 0; Changed = @() }
  $dir = Join-Path $night $Role
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $left = Get-HoursLeft 20
  $budget = [math]::Min($AgentHours, $left)
  if ($left -lt 0.1) {
    Step "$Role agent: skipped, under 6 min left before the hard stop ($HardStop)"
    $r.Status = "skipped (hard stop)"
    return [pscustomobject]$r
  }
  if ($budget -lt $AgentHours) { Step "$Role agent: budget cut from $AgentHours h to $([math]::Round($budget, 2)) h by the hard stop ($HardStop)" }
  git checkout harness 2>&1 | Out-Null
  $branch = switch ($Role) { "ui" { "agent/$date-ui" } "app" { "agent/$date-app" } default { "agent/$date" } }
  $stem = $branch; $k = 2
  while (git rev-parse --verify --quiet $branch 2>$null) { $branch = "$stem-$k"; $k++ }
  git checkout -b $branch 2>&1 | Out-Null
  $r.Branch = $branch
  Step "$Role agent: branch $branch (from harness $Base), budget $([math]::Round($budget, 2)) h"

  $start = Get-Date; $r.Start = $start
  $deadline = $start.AddHours($budget)
  $header = @"
# Runtime header (generated by nightly_agent.ps1)
- agent: $Role  (brief below)
- date: $date
- branch: $branch (created from harness @ $Base)
- repo: $root  (Windows). Your Bash tool is Git Bash: use forward slashes and the venv interpreter
  ``.venv/Scripts/python.exe`` for every python/pytest command, e.g. ``.venv/Scripts/python.exe -m pytest -q``.
- start: $($start.ToString('yyyy-MM-dd HH:mm'))   deadline: $($deadline.ToString('yyyy-MM-dd HH:mm'))  (hard kill at the deadline)
- your night folder (report, gate outputs, study scripts): results/agent/$nightTag/$Role/
- tonight's sweep: results/agent/$nightTag/SWEEP.md; tonight's starting baseline: results/agent/$nightTag/baseline_before.json
- the knob search runs after the agents, by the orchestrator; do not run it yourself
- usb export: usb_export/ (the tools find it on their own)
- sweep: exit $SweepExit (0 clean, 1 findings, 2 tool error). SWEEP.md head:

$SweepNote

"@
  $promptPath = Join-Path $dir "prompt.md"
  [IO.File]::WriteAllText($promptPath, ($header + (Get-Content $Brief -Raw -Encoding UTF8)), $utf8)
  $outPath = Join-Path $dir "agent.out"
  $errPath = Join-Path $dir "agent.err"
  $deny = @("Bash(git push:*)", "Bash(git checkout harness:*)", "Bash(git checkout main:*)", "Bash(git switch:*)",
            "Bash(git merge:*)", "Bash(git rebase:*)", "Bash(git branch -D:*)", "Bash(git reset --hard:*)",
            "Bash(git worktree add:*)",
            "Bash(pip install:*)", "Bash(python -m pip:*)", "Bash(.venv/Scripts/python.exe -m pip:*)",
            "Bash(./.venv/Scripts/python.exe -m pip:*)", "Bash(.venv/Scripts/pip:*)") | ForEach-Object { "`"$_`"" }
  if ($AgentCmd) {
    $runner = $AgentCmd
  } else {
    $flags = "-p --dangerously-skip-permissions --output-format text --no-session-persistence --disallowedTools " + ($deny -join " ")
    if ($Model) { $flags += " --model $Model" }
    if ($MaxBudgetUsd -gt 0) { $flags += " --max-budget-usd $MaxBudgetUsd" }
    $runner = "claude $flags"
  }
  $cmd = "type `"$promptPath`" | $runner > `"$outPath`" 2> `"$errPath`""
  $env:LIVEREACT_AGENT_ROLE = $Role
  # stub agents (-AgentCmd) need no network; a real one waits for it, then gets one retry on a network death
  if (-not $AgentCmd -and -not (Wait-ApiReachable $Role 30)) { Write-Host "$Role agent: launching anyway" }
  for ($attempt = 1; $attempt -le 2; $attempt++) {
    $msLeft = [int][math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds)
    $script:proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -WorkingDirectory $root -PassThru -WindowStyle Hidden
    $finished = $script:proc.WaitForExit($msLeft)
    if (-not $finished) { break }
    if ($script:proc.ExitCode -eq 0 -or $attempt -eq 2) { break }
    # retry only a session that never got going: no commits, a network error, 30+ min of budget left
    $said = ((Get-Content $outPath, $errPath -Raw -ErrorAction SilentlyContinue) -join "`n")
    $commits = [int](git rev-list --count "harness..HEAD" 2>$null)
    if ($commits -gt 0 -or $said -notmatch "ENOTFOUND|ECONNREFUSED|ETIMEDOUT|ECONNRESET|Can't reach the API") { break }
    if (($deadline - (Get-Date)).TotalMinutes -lt 30) { break }
    Step "$Role agent: died on a network error, retrying once"
    Copy-Item $outPath "$outPath.attempt1" -Force -ErrorAction SilentlyContinue
    if (-not $AgentCmd -and -not (Wait-ApiReachable $Role 30)) { break }
  }
  if (-not $finished) {
    Step "$Role agent: deadline reached, killing the session tree"
    & taskkill /T /F /PID $script:proc.Id 2>&1 | Out-Null
    $r.Status = "killed at deadline"
  } else { $r.Status = "exited $($script:proc.ExitCode)" }
  $r.Minutes = [int]((Get-Date) - $start).TotalMinutes
  Step "$Role agent: $($r.Status) after $($r.Minutes) min"
  if (Test-Path $errPath) { Get-Content $errPath -Encoding UTF8 | Select-Object -Last 15 | ForEach-Object { Write-Host "agent.err: $_" } }

  # ---- verify independently ----
  $left = @(git status --porcelain 2>$null)
  if ($left.Count -gt 0) {
    Write-Host "$Role agent left uncommitted/untracked changes; stashing them as 'agent-$date-$Role-leftovers':"
    $left | ForEach-Object { Write-Host "  $_" }
    git stash push -u -m "agent-$date-$Role-leftovers" 2>&1 | ForEach-Object { Write-Host "  stash: $_" }
    if (@(git status --porcelain 2>$null).Count -gt 0) {
      # a hard kill mid-commit can leave .git\index.lock behind; clear it and retry once
      if (Test-Path ".git\index.lock") { Remove-Item ".git\index.lock" -Force; Write-Host "  removed .git\index.lock" }
      git stash push -u -m "agent-$date-$Role-leftovers" 2>&1 | ForEach-Object { Write-Host "  stash retry: $_" }
    }
  }
  $treeClean = (@(git status --porcelain 2>$null).Count -eq 0)
  $r.Commits = [int](git rev-list --count "harness..HEAD" 2>$null)
  Write-Host "commits on $branch : $($r.Commits)"
  git log --oneline "harness..HEAD" 2>$null | ForEach-Object { Write-Host "  $_" }
  if ($finished -and $script:proc.ExitCode -ne 0 -and $r.Commits -eq 0) {
    # the session died (login expired, CLI error, Git Bash missing...): not a quiet night, a failed one
    $r.Status += " -- AGENT FAILED, see agent.err"
    $r.Exit = 2
  }
  if ($r.Commits -gt 0) {
    $r.Changed = Get-Changed "harness..HEAD"
    $r.OutOfScope = @($r.Changed | Where-Object { $_ -notmatch $AllowRe })
    if ($Role -eq "ui") {
      # family 0: at most ONE engine file, the one a sweep traceback names
      $engine = @($r.OutOfScope | Where-Object { $_ -match $UiFamily0 })
      if ($engine.Count -eq 1) { $r.OutOfScope = @($r.OutOfScope | Where-Object { $_ -notmatch $UiFamily0 }) }
    }
    $r.OutOfScope += @(git -c core.quotepath=off diff --name-only --diff-filter=D "harness..HEAD" -- tests 2>$null | ForEach-Object { "$_ (test file deleted)" })
    if ($r.Changed -contains "section_classes.json") {
      $bad = Test-ClassApprovals "harness..HEAD"
      if ($bad) { $r.OutOfScope += "section_classes.json: approved/scene changed for $bad (Josh's to set)" }
    }
  }
  if ($r.OutOfScope.Count -gt 0) {
    Step "$Role agent: OUT OF SCOPE; not verifying:"
    $r.OutOfScope | ForEach-Object { Write-Host "  $_" }
    $r.Gate = "OUT-OF-SCOPE"; $r.Exit = 4
  } elseif ($r.Commits -gt 0 -and -not $treeClean) {
    Write-Host "working tree still dirty after stashing; verification would judge the wrong code"
    $r.Tests = "FAIL"; $r.Gate = "ERROR"; $r.Exit = 4
  } elseif ($r.Commits -gt 0) {
    Step "$Role agent: full pytest"
    $pt = @(& $py -m pytest -o addopts="" -q 2>&1)
    $ptExit = $LASTEXITCODE
    # the LAST "N passed / N failed" line, not the last line: Qt prints errors from callbacks to stderr,
    # which can land after pytest's summary (stress test 2026-10-26: a green run read as FAIL)
    $summary = "$(@($pt | Where-Object { "$_" -match '^\s*=*\s*\d+ (passed|failed)|^\s*=*\s*no tests ran' }) | Select-Object -Last 1)"
    Write-Host "pytest (exit $ptExit): $summary"
    $failed = 0; if ($summary -match '(\d+) failed') { $failed = [int]$Matches[1] }
    $unknown = @($pt | Where-Object { $_ -match '^(FAILED|ERROR)' } | Where-Object { $_ -notmatch 'test_spec_conformance_gaps' })
    $r.Tests = if ($ptExit -le 1 -and $failed -le 3 -and $unknown.Count -eq 0 -and $summary -match 'passed') { "ok" } else { "FAIL" }
    $unknown | ForEach-Object { Write-Host "  $_" }
    $verify = Join-Path $dir "gate_verify.json"
    if ($Role -eq "ui") {
      Step "ui agent: gate_ui vs harness"
      & $py tools\agent\gate_ui.py --base harness --baseline $BaselineBefore --out $verify 2>&1 | Select-Object -Last 30 | Out-Host
    } elseif ($Role -eq "app") {
      Step "app agent: gate_app vs harness"
      & $py tools\agent\gate_app.py --base harness --baseline $BaselineBefore --out $verify 2>&1 | Select-Object -Last 30 | Out-Host
    } else {
      $rulesOnly = @($r.Changed | Where-Object { $_ -notmatch '^(section_classes\.json|src/livereact/rekordbox/sections\.py|tests/.+)$' }).Count -eq 0
      $r.HeadEval = Join-Path $dir "head_eval.json"
      if ($rulesOnly) {
        Step "tracks agent: class rules only -> lighting gate in no-regression mode"
        & $py tools\agent\gate_head.py --baseline $BaselineBefore --usb usb_export --drive --no-regress --out $verify --out-eval $r.HeadEval 2>&1 | Select-Object -Last 30 | Out-Host
      } else {
        Step "tracks agent: lighting gate"
        & $py tools\agent\gate_head.py --baseline $BaselineBefore --usb usb_export --drive --out $verify --out-eval $r.HeadEval 2>&1 | Select-Object -Last 30 | Out-Host
      }
    }
    $r.Gate = if ($LASTEXITCODE -eq 0) { "ACCEPT" } elseif ($LASTEXITCODE -eq 1) { "REJECT" } else { "ERROR" }
    Step "$Role agent: independent gate $($r.Gate) ; tests $($r.Tests)"
    if ($r.Gate -ne "ACCEPT" -or $r.Tests -ne "ok") { $r.Exit = 4 }
  }
  # the baseline file describes harness, not a branch: undo an agent's --write-baseline before anything else runs
  if ((Test-Path "results\baseline\evaluation.json") -and ((Get-FileHash "results\baseline\evaluation.json").Hash -ne (Get-FileHash $BaselineBefore).Hash)) {
    Copy-Item $BaselineBefore "results\baseline\evaluation.json" -Force
    Write-Host "$Role agent had rewritten results\baseline\evaluation.json; restored tonight's starting baseline"
  }
  & $py tools\agent\record_night.py --role $Role --date $date --branch $branch --commits $r.Commits --gate $r.Gate --tests $r.Tests --note "orchestrator: $($r.Status)" | Out-Null
  git checkout harness 2>&1 | Out-Null
  return [pscustomobject]$r
}

$results = @()
try {
  # ---- preflight ---------------------------------------------------------------------------------
  Step "preflight"
  # without git every check below reads as empty and the run looks clean (seen 2026-10-23)
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found on PATH"; exit 3 }
  # tracked changes block the pull; untracked files (e.g. .claude/settings.local.json from an interactive
  # session) are tolerated here and only matter after an agent (stashed per agent)
  $dirty = @(git status --porcelain 2>$null | Where-Object { $_ -notmatch '^\?\?' })
  if ($dirty.Count -gt 0) { Fail "working tree has local changes (stash or checkout):`n$($dirty -join "`n")"; exit 3 }
  if (-not $AgentCmd) {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Fail "claude CLI not found on PATH"; exit 3 }
    $claudeVer = (& claude --version 2>&1 | Select-Object -First 1)
    Write-Host "claude: $claudeVer"
  } else { Write-Host "agent command (stress test): $AgentCmd" }
  if (-not (git config user.email)) { Fail "git identity missing: git config user.name / user.email (repo-local is fine)"; exit 3 }
  git fetch origin 2>&1 | Out-Null
  git checkout harness 2>&1 | Out-Null
  $pull = git pull --ff-only origin harness 2>&1
  $pull | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { Fail "git pull --ff-only did not succeed"; exit 3 }
  $base = (git rev-parse --short HEAD).Trim()
  Write-Host ("code: " + (git log --oneline -1))
  # [ui]: the Bugs & UI agent's tests need PySide6 (2026-10-26: without it every UI test silently skipped)
  # [app]: platformdirs, pypdf, anthropic, keyring for the App agent's roadmap items (2026-10-27)
  & $py -m pip install --quiet -e ".[sim,dev,ui,app]" 2>&1 | Out-Null
  & $py -m pytest -o addopts="" -q tests\test_harness_determinism.py 2>&1 | Select-Object -Last 1
  if ($LASTEXITCODE -ne 0) { Fail "determinism test"; exit 3 }
  # the planner era needs the rekordbox USB export copy (docs\HANDOFF_windows_resync.md step 10)
  if (-not (Test-Path "usb_export\PIONEER\rekordbox\export.pdb")) { Fail "usb_export\PIONEER\rekordbox\export.pdb missing: copy the rekordbox USB export into usb_export\"; exit 3 }
  $uiReady = $true
  & $py -c "import PySide6" 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { $uiReady = $false; Write-Host "PySide6 not importable: the Bugs & UI and App agents are skipped tonight" }
  if ($hardStopAt) { Write-Host "hard stop: $($hardStopAt.ToString('yyyy-MM-dd HH:mm')) (agents and search are cut to it)" }

  # ---- sweep (bugs, invariants, plan sanity over every USB track; exit 1 = findings, not a failed night) --
  $sweepNote = "skipped"
  $sweepExit = 0
  if ($SweepMinutes -gt 0) {
    Step "sweep: $SweepMinutes min over usb_export -> $night\SWEEP.md"
    & $py tools\agent\sweep.py --minutes $SweepMinutes --out $night 2>&1 | Out-Host
    $sweepExit = $LASTEXITCODE
    $sweepMd = Join-Path $night "SWEEP.md"
    if (Test-Path $sweepMd) { $sweepNote = (Get-Content $sweepMd -Encoding UTF8 | Select-Object -First 40) -join "`n" }
    else { $sweepNote = "sweep wrote no SWEEP.md (exit $sweepExit)" }
    Step "sweep: exit $sweepExit"
  }

  # ---- the baseline the night starts from: fresh for harness, then snapshotted -------------------------
  # results\baseline\evaluation.json is local and gitignored (private). If the pull brought new engine code,
  # a scorer change or new labels, regenerate it first, or the gates would credit the agents with the pull.
  if (Test-Path "results\baseline\evaluation.json") {
    & $py tools\agent\baseline_fresh.py 2>&1 | ForEach-Object { Write-Host $_ }
    $freshExit = $LASTEXITCODE
  } else { $freshExit = 2 }
  if ($freshExit -ne 0) {
    Step "baseline: regenerating on harness $base"
    & $py tools\loop\baseline.py 2>&1 | Select-Object -Last 3 | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path "results\baseline\evaluation.json")) { Fail "baseline regeneration failed"; exit 3 }
  }
  $baselineBefore = Join-Path $night "baseline_before.json"
  Copy-Item "results\baseline\evaluation.json" $baselineBefore -Force

  # ---- the three agents ------------------------------------------------------------------------------
  if ($UiHours -gt 0 -and $uiReady) {
    $results += Invoke-NightAgent -Role "ui" -Brief "docs\AGENT_BRIEF_UI.md" -AgentHours $UiHours -AllowRe $AllowUi -BaselineBefore $baselineBefore -SweepNote $sweepNote -SweepExit $sweepExit -Base $base
  }
  if ($AppHours -gt 0 -and $uiReady) {
    $results += Invoke-NightAgent -Role "app" -Brief "docs\AGENT_BRIEF_APP.md" -AgentHours $AppHours -AllowRe $AllowApp -BaselineBefore $baselineBefore -SweepNote $sweepNote -SweepExit $sweepExit -Base $base
  }
  $tracks = $null
  if ($Hours -gt 0) {
    $tracks = Invoke-NightAgent -Role "tracks" -Brief "docs\AGENT_BRIEF.md" -AgentHours $Hours -AllowRe $AllowTracks -BaselineBefore $baselineBefore -SweepNote $sweepNote -SweepExit $sweepExit -Base $base
    $results += $tracks
  }
  foreach ($r in $results) { if ($r.Exit -gt $exit) { $exit = $r.Exit } }

  # ---- knob search around the Track Classifier's code (only when its night is good) --------------------
  # The search's baseline is the verified evaluation of HEAD (head_eval.json), never the local file.
  $searchNote = "skipped"
  $searchOut = $null
  $searchLeft = Get-HoursLeft 10
  if ($SearchHours -gt $searchLeft) {
    Step "search: cut from $SearchHours h to $([math]::Round($searchLeft, 2)) h by the hard stop ($HardStop)"
    $SearchHours = [math]::Round($searchLeft, 2)
    if ($SearchHours -lt 0.1) { $SearchHours = 0 }
  }
  if ($SearchHours -gt 0 -and $tracks -and $tracks.Commits -gt 0 -and $tracks.Gate -eq "ACCEPT" -and $tracks.Tests -eq "ok" -and (Test-Path $tracks.HeadEval)) {
    git checkout $tracks.Branch 2>&1 | Out-Null
    $searchOut = "results\search\$date-$(Get-Date -Format HHmm)-agent"
    Step "search: $SearchHours h around $($tracks.Branch) -> $searchOut"
    # Out-Host: a native command's stdout does not reach the transcript on its own under Task Scheduler
    & $py tools\loop\search.py --hours $SearchHours --patience $Patience --baseline $tracks.HeadEval --usb usb_export --drive --out $searchOut 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 2) { $exit = 2 }
    git checkout harness 2>&1 | Out-Null
  } elseif ($SearchHours -gt 0 -and @(git status --porcelain 2>$null).Count -eq 0) {
    # Nothing accepted: tune knobs on harness for the search budget plus what the Track Classifier left
    # unused, so a quiet night is not an idle one. [double]0: Max(int,int) would truncate 3.4 h to 3.
    $unused = 0
    if ($tracks) { $unused = [math]::Max([double]0, $Hours - ((Get-Date) - $tracks.Start).TotalHours) }
    $fallbackHours = [math]::Round([math]::Min($SearchHours + $unused, (Get-HoursLeft 10)), 2)
    $searchOut = "results\search\$date-$(Get-Date -Format HHmm)-harness"
    git checkout harness 2>&1 | Out-Null
    Step "search: nothing accepted tonight; $fallbackHours h on harness $base -> $searchOut"
    & $py tools\loop\search.py --hours $fallbackHours --patience $Patience --baseline $baselineBefore --usb usb_export --drive --out $searchOut 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 2) { $exit = 2 }
  }
  if ($searchOut) {
    $sm = "$searchOut\summary.md"
    if (Test-Path $sm) { $searchNote = (Get-Content $sm -Encoding UTF8 | Select-Object -First 6) -join "`n" ; Write-Host $searchNote }
    else { $searchNote = "no summary.md in $searchOut (search did not finish)" }
  }
  Step "search: done"

  # ---- morning note ------------------------------------------------------------------------------
  Step "night note"
  $sections = @()
  foreach ($r in $results) {
    $rep = Join-Path $night "$($r.Role)\REPORT.md"
    $head = if (Test-Path $rep) { (Get-Content $rep -Encoding UTF8 | Select-Object -First 50) -join "`n" } else { "(the agent wrote no REPORT.md)" }
    $name = switch ($r.Role) { "ui" { "Bugs & UI agent" } "app" { "App agent" } default { "Track Classifier" } }
    $sections += @"
## $name - $($r.Branch)

- agent: $($r.Status) ($($r.Minutes) min); commits: $($r.Commits); tests: $($r.Tests); independent gate: $($r.Gate)
- out of scope: $(if ($r.OutOfScope.Count) { ($r.OutOfScope -join ', ') } else { 'none' })
- commits:
$((git log --oneline "harness..$($r.Branch)" 2>$null) -join "`n")

### Report (head)
$head
"@
  }
  # every pair of branches: a file two of them changed means merge one, then the other (and re-check)
  $both = @()
  for ($i = 0; $i -lt $results.Count; $i++) {
    for ($j = $i + 1; $j -lt $results.Count; $j++) {
      foreach ($f in @($results[$i].Changed | Where-Object { $results[$j].Changed -contains $_ -and $_ -notmatch '^tests/' })) {
        $both += "$f ($($results[$i].Role) + $($results[$j].Role))"
      }
    }
  }
  $nightMd = @"
# Night $date (harness $base)

- sweep: exit $sweepExit (0 clean, 1 findings, 2 error) -> $night\SWEEP.md
- baseline: $(if ($freshExit -eq 0) { 'fresh' } else { 'regenerated on harness before the agents' }); every agent was gated against baseline_before.json
- files changed on more than one branch (merge one, then the other): $(if ($both.Count) { $both -join ', ' } else { 'none' })
- knob search:
$searchNote

$($sections -join "`n`n")

## Power events during the run (from 5 min before the start)
$((Get-PowerEvents $nightStart) -join "`n")
"@
  [IO.File]::WriteAllText((Join-Path $night "NIGHT.md"), $nightMd, $utf8)
  if ($Push) {
    # The branches only. Reports (NIGHT.md, REPORT.md, SWEEP.md) and the search results stay in results\ --
    # the repo is public and they carry track names; tools\private_bundle.py moves them to the Mac.
    foreach ($r in $results) {
      if ($r.Commits -gt 0) {
        Step "push $($r.Branch)"
        $pe = Invoke-GitTimed @("push", "-u", "origin", $r.Branch) 600
        if ($pe -ne 0) { Write-Host "push of $($r.Branch) failed (exit $pe); the branch is still local" }
      }
    }
  }
  git checkout harness 2>&1 | Out-Null
  Step ("done: " + (($results | ForEach-Object { "$($_.Role) $($_.Branch) commits=$($_.Commits) gate=$($_.Gate) tests=$($_.Tests)" }) -join " | ") + " -> $night\NIGHT.md")
  exit $exit
} catch {
  # without this an unexpected error skips the rest silently and the task reports 0 (seen 2026-10-23)
  Write-Host "CRASH: $($_.Exception.Message)"
  Write-Host $_.ScriptStackTrace
  try { [IO.File]::WriteAllText((Join-Path $night "NIGHT.md"), "# Night $date - CRASHED`n`n$($_.Exception.Message)`n`n$($_.ScriptStackTrace)`n", $utf8) } catch {}
  git checkout harness 2>&1 | Out-Null
  exit 2
} finally {
  if ($script:proc -and -not $script:proc.HasExited) { Write-Host "cleanup: agent still running, killing its tree"; & taskkill /T /F /PID $script:proc.Id 2>&1 | Out-Null }
  if ($awake -or $held) { Release-Wake }
  Restore-RunPowerPlan
  Step "night ended"
  Stop-Transcript | Out-Null
}
