<# One-time power-plan setup for the nightly run. Run ONCE as administrator (right-click PowerShell ->
   Run as administrator), then re-run only if Windows resets the plan (a feature update, a new plan).

     .\power_setup.ps1          # apply + verify
     .\power_setup.ps1 -Check   # verify only, change nothing

   Why: the nightly holds power requests while it runs and sets the plan's sleep timeout to never for
   the run only (restored at the end), so the daytime sleep timeout stays whatever you like (30 min).
   Two settings cannot be handled per run and need admin once: (a) the unattended-sleep timeout: after
   a wake-to-run with nobody signed in, Windows goes back to sleep 2 minutes later unless this is 0;
   (b) wake timers, without which the 1:00 trigger cannot wake the PC at all. Hibernate is set to never
   because a wake timer may not wake a hibernated PC. 2026-10-24 the desktop slept 03:22-11:29 in the
   middle of the run. Everything here is the AC (plugged in) side of the ACTIVE plan; a laptop on
   battery is not covered on purpose -- plug it in. #>
param([switch]$Check)

function Get-AcIndex([string]$alias) {
  $line = powercfg /aliases 2>&1 | Where-Object { $_ -match "\s$alias\s*$" }
  if (-not $line) { return $null }
  $guid = ($line -split "\s+")[0]
  $q = powercfg /q SCHEME_CURRENT SUB_SLEEP 2>&1 | Out-String
  $m = [regex]::Match($q, [regex]::Escape($guid) + "[\s\S]*?Current AC Power Setting Index: 0x([0-9a-f]+)", "IgnoreCase")
  if ($m.Success) { return [convert]::ToInt32($m.Groups[1].Value, 16) } else { return $null }
}

$want = @(
  @{ Alias = "HIBERNATEIDLE"; Value = 0; What = "hibernate after (AC)";          Cmd = { powercfg /change hibernate-timeout-ac 0 } },
  @{ Alias = "UNATTENDSLEEP"; Value = 0; What = "unattended sleep timeout (AC)"; Cmd = { powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP UNATTENDSLEEP 0 } },
  @{ Alias = "RTCWAKE";       Value = 1; What = "allow wake timers (AC)";        Cmd = { powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 } }
)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Check -and -not $isAdmin) { Write-Host "run this as administrator (setacvalueindex needs it); or pass -Check to only read the plan"; exit 2 }

$bad = 0
foreach ($w in $want) {
  $cur = Get-AcIndex $w.Alias
  if ($cur -eq $w.Value) { Write-Host ("ok      {0,-32} = {1}" -f $w.What, $cur); continue }
  if ($Check) { Write-Host ("WRONG   {0,-32} = {1} (want {2})" -f $w.What, $cur, $w.Value); $bad++; continue }
  & $w.Cmd | Out-Null
  $now = Get-AcIndex $w.Alias
  if ($now -eq $w.Value) { Write-Host ("fixed   {0,-32} {1} -> {2}" -f $w.What, $cur, $now) }
  else { Write-Host ("FAILED  {0,-32} still {1} (want {2})" -f $w.What, $now, $w.Value); $bad++ }
}
if (-not $Check) { powercfg /setactive SCHEME_CURRENT | Out-Null }

Write-Host ""
Write-Host "wake timers currently armed (the LiveReact task should appear here once registered):"
powercfg /waketimers 2>&1 | ForEach-Object { "  $_" }
Write-Host ""
Write-Host "sleep states this PC supports (a wake-to-run works from Standby or Hibernate, never from a shutdown):"
powercfg /a 2>&1 | ForEach-Object { "  $_" }
Write-Host ""
if ($bad) { Write-Host "$bad setting(s) not as wanted"; exit 1 }
Write-Host ("daytime sleep timeout (AC) is {0} s; left alone, the nightly sets it to never for the run and restores it" -f (Get-AcIndex "STANDBYIDLE"))
Write-Host "power plan ready for the nightly. Leave the PC asleep or on, signed in (locked is fine); do not shut it down."
exit 0
