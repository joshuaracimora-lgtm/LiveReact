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

# Keep the machine awake for the whole run (ES_CONTINUOUS | ES_SYSTEM_REQUIRED). Best effort.
$awake = $false
try {
  Add-Type -Name Power -Namespace LiveReact -MemberDefinition @"
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
  [void][LiveReact.Power]::SetThreadExecutionState(0x80000000 -bor 0x00000001)
  $awake = $true
  Write-Host "power: system-required request held for this run"
} catch { Write-Host "power: could not hold a wake request ($($_.Exception.Message)); relying on power plan" }

try {
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
  & $py tools\loop\search.py --hours $Hours --patience $Patience
  $code = $LASTEXITCODE
  $latest = Get-ChildItem results\search -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
  if ($latest -and (Test-Path "$($latest.FullName)\summary.md")) { Get-Content "$($latest.FullName)\summary.md" -Encoding UTF8 | Select-Object -First 12 }
  else { Write-Host "no summary.md written - the search did not finish on its own (killed? sleep?)" }
  exit $code
} finally {
  if ($awake) { try { [void][LiveReact.Power]::SetThreadExecutionState(0x80000000) } catch {} }
  Stop-Transcript | Out-Null
}
