<# The nightly job: pull latest, regenerate baseline if needed, run the search for N hours.
   Everything it prints goes to results\nightly\<date>.log. Exit codes: 0 ok, 2 crash, 3 preflight failed. #>
param([double]$Hours = 6, [int]$Patience = 300)
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
New-Item -ItemType Directory -Force -Path results\nightly | Out-Null
$log = "results\nightly\$(Get-Date -Format yyyyMMdd-HHmm).log"
Start-Transcript -Path $log | Out-Null
try {
  git fetch origin 2>&1 | Out-Null
  git checkout harness 2>&1 | Out-Null
  git pull --ff-only origin harness 2>&1
  $py = ".\.venv\Scripts\python.exe"
  & $py -m pip install --quiet -e ".[sim,dev]"
  & $py -m pytest -q tests\test_harness_determinism.py 2>&1 | Select-Object -Last 1
  if ($LASTEXITCODE -ne 0) { Write-Host "DETERMINISM FAILED, aborting"; exit 3 }
  & $py tools\loop\search.py --hours $Hours --patience $Patience
  $code = $LASTEXITCODE
  $latest = Get-ChildItem results\search -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
  if ($latest -and (Test-Path "$($latest.FullName)\summary.md")) { Get-Content "$($latest.FullName)\summary.md" | Select-Object -First 12 }
  exit $code
} finally { Stop-Transcript | Out-Null }
