<#
  LiveReact improvement-loop setup for the Windows desktop (run once, then re-run to verify).

    1. Open PowerShell (not as admin).
    2. git clone https://github.com/joshuaracimora-lgtm/LiveReact.git $HOME\LiveReact   (first time only)
    3. cd $HOME\LiveReact ; git checkout harness
    4. powershell -ExecutionPolicy Bypass -File tools\windows\setup.ps1

  Installs: Git, Python 3.12, ffmpeg (via winget), a venv with the [sim,dev] extras, then
  runs the harness tests and a 1-track smoke run. Prints PASS/FAIL per step. Safe to re-run.
#>
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
function Step($name, $ok, $detail) { if ($ok) { Write-Host ("  PASS  " + $name) -ForegroundColor Green } else { Write-Host ("  FAIL  " + $name + "  -> " + $detail) -ForegroundColor Red } }

Write-Host "== LiveReact loop setup in $root"
Write-Host "   Windows $([System.Environment]::OSVersion.Version)  |  $(Get-Date)"

# --- 1. tools via winget
foreach ($pkg in @(@("Git.Git","git"), @("Python.Python.3.12","python"), @("Gyan.FFmpeg","ffmpeg"))) {
  $id, $exe = $pkg
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Write-Host "   installing $id ..."
    winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent | Out-Null
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
  }
  Step "$exe on PATH" ([bool](Get-Command $exe -ErrorAction SilentlyContinue)) "install $id, then open a NEW PowerShell and re-run"
}

# --- 2. python version
$pyver = (& python --version 2>&1)
Step "python is 3.12+ ($pyver)" ($pyver -match "3\.1[2-9]") "winget install Python.Python.3.12"

# --- 3. venv + package
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e ".[sim,dev]"
Step "venv + livereact[sim,dev] installed" ($LASTEXITCODE -eq 0) "see pip output above"

# --- 4. tracks present
$n = (Get-ChildItem tracks -Include *.mp3,*.wav,*.flac,*.m4a -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
$t = (Get-ChildItem tracks -Filter *.timeline.json -ErrorAction SilentlyContinue | Measure-Object).Count
Step "audio in tracks\ ($n files, $t timelines)" ($n -gt 0) "copy the tracks folder from the Mac (audio is not in git)"

# --- 5. harness + scorer + params tests
& $py -m pytest -q tests\test_harness_determinism.py tests\test_scorer.py tests\test_params.py 2>&1 | Select-Object -Last 3
Step "harness/scorer/params tests" ($LASTEXITCODE -eq 0) "paste the pytest output into the chat"

# --- 6. one real-track smoke run
$first = Get-ChildItem tracks -Include *.mp3 -Recurse -ErrorAction SilentlyContinue | Where-Object { Test-Path ($_.FullName -replace '\.mp3$','.timeline.json') } | Select-Object -First 1
if ($first) {
  & $py tools\harness\run_sim.py $first.FullName --out results\windows-smoke --duration 60 2>&1 | Select-Object -Last 2
  Step "smoke run of $($first.Name)" ($LASTEXITCODE -eq 0) "ffmpeg or the harness failed; paste the output"
} else { Step "smoke run" $false "no track with a timeline found" }

# --- 7. power settings reminder
Write-Host ""
Write-Host "Next: Settings > System > Power > Screen and sleep: set 'When plugged in, PC goes to sleep' to Never."
Write-Host "Then register the nightly task:  powershell -ExecutionPolicy Bypass -File tools\windows\register_task.ps1"
