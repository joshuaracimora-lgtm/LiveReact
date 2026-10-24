<# The nightly job: pull latest, run the search for N hours.
   Everything it prints goes to results\nightly\<date>.log.
   Exit codes: 0 ok, 2 crash, 3 preflight failed (dirty tree, failed pull, determinism).
   The run ABORTS if the pull fails - searching stale code looked like progress once (Oct 2026)
   and wasted nights. It also holds a system-required power request so a wake-to-run start is not
   put back to sleep by the unattended idle timeout. #>
param([double]$Hours = 6, [int]$Patience = 300)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
New-Item -ItemType Directory -Force -Path results\nightly | Out-Null
$log = "results\nightly\$(Get-Date -Format yyyyMMdd-HHmm).log"
Start-Transcript -Path $log | Out-Null

. "$PSScriptRoot\common.ps1"      # UTF-8 python env, Hold-Wake/Release-Wake, Join-KillOnCloseJob
Join-KillOnCloseJob
$awake = Hold-Wake
$held = Hold-PowerRequest "LiveReact nightly knob search"
$planOk = Check-PowerPlan
$planSet = Set-RunPowerPlan     # sleep never for the run only; restored in finally
$runStart = Get-Date

try {
  # 0. Without git every check below reads as empty and the run looks clean (seen 2026-10-23).
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "PREFLIGHT FAILED: git not found on PATH"; exit 3 }
  # 1. The tree must be clean, or the pull below can never fast-forward.
  $dirty = (git status --porcelain 2>$null | Where-Object { $_ -notmatch '^\?\?' })
  if ($dirty) {
    Write-Host "PREFLIGHT FAILED: working tree has local changes; refusing to search stale code."
    Write-Host ($dirty -join "`n")
    Write-Host "Fix on the desktop: git stash  (or git checkout -- <file>)  then re-run."
    exit 3
  }
  # 2. Pull. A failed pull is fatal - never search yesterday's engine by accident.
  git fetch origin 2>&1 | Out-Null
  git checkout harness 2>&1 | Out-Null
  $pull = git pull --ff-only origin harness 2>&1
  $pull | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { Write-Host "PREFLIGHT FAILED: git pull --ff-only did not succeed (exit $LASTEXITCODE)"; exit 3 }
  Write-Host ("code: " + (git log --oneline -1 2>$null))
  $py = ".\.venv\Scripts\python.exe"
  & $py -m pip install --quiet -e ".[sim,dev]" 2>&1 | Out-Null
  # 3. Determinism preflight.
  & $py -m pytest -o addopts="" -q tests\test_harness_determinism.py 2>&1 | Select-Object -Last 1
  if ($LASTEXITCODE -ne 0) { Write-Host "PREFLIGHT FAILED: determinism test; aborting"; exit 3 }
  # 4. Search.
  & $py tools\loop\search.py --hours $Hours --patience $Patience 2>&1 | Out-Host   # Out-Host: reach the transcript
  $code = $LASTEXITCODE
  $latest = Get-ChildItem results\search -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
  if ($latest -and (Test-Path "$($latest.FullName)\summary.md")) { Get-Content "$($latest.FullName)\summary.md" -Encoding UTF8 | Select-Object -First 12 }
  else { Write-Host "no summary.md written - the search did not finish on its own (killed? sleep?)" }
  Write-Host "power events during the run:"
  Get-PowerEvents $runStart | ForEach-Object { Write-Host "  $_" }
  exit $code
} catch {
  # without this an unexpected error skips the rest silently and the task reports 0 (seen 2026-10-23)
  Write-Host "CRASH: $($_.Exception.Message)"
  Write-Host $_.ScriptStackTrace
  exit 2
} finally {
  if ($awake -or $held) { Release-Wake }
  Restore-RunPowerPlan
  Stop-Transcript | Out-Null
}
