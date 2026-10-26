# Dot-sourced by nightly.ps1 and nightly_agent.ps1 FIRST, before common.ps1. ASCII only: PS 5.1.
#
# PowerShell reads a script (and everything it dot-sources) when the run starts, so code that the
# preflight pull brings in only took effect the NEXT night: 811a11b dropped two functions from
# common.ps1 and 2026-10-25 still ran the old copy. This pulls harness before anything else is loaded
# and, if harness moved, re-runs the calling script from the pulled tree with the same arguments.
# Keep it small: a fix to THIS file still lands one night late.
#
# Returns the re-run's exit code (the caller exits with it), or $null to carry on in this process:
# harness did not move, git is missing, the tree is dirty or the pull failed. Those last three are left
# to the caller's own preflight, which reports them in the night's log.
function Invoke-PulledSelf([string]$ScriptPath, $Bound) {
  if ($Bound.ContainsKey("PulledFrom")) { return $null }   # already the re-run
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $null }
  if (@(git status --porcelain 2>$null | Where-Object { $_ -notmatch '^\?\?' }).Count -gt 0) { return $null }
  $before = "$(git rev-parse --short harness 2>$null)".Trim()
  git fetch origin 2>&1 | Out-Null
  git checkout harness 2>&1 | Out-Null
  git pull --ff-only origin harness 2>&1 | Out-Null
  $after = "$(git rev-parse --short harness 2>$null)".Trim()
  if (-not $before -or -not $after -or $before -eq $after) { return $null }
  $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath)
  foreach ($k in $Bound.Keys) {
    $v = $Bound[$k]
    if ($v -is [System.Management.Automation.SwitchParameter]) { if ($v.IsPresent) { $argv += "-$k" } }
    elseif ("$v" -ne "") { $argv += @("-$k", "$v") }
  }
  $argv += @("-PulledFrom", $before)
  # Out-Host: the re-run's output must not become this function's return value
  & powershell.exe @argv | Out-Host
  return $LASTEXITCODE
}
