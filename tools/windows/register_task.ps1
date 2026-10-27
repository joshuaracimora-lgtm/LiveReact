<# Registers (or replaces) the "LiveReact nightly search" Task Scheduler job: 4:00 PM daily (moved from
   1:00 AM on 2026-10-25 so the run fits 4-10 PM while the PC is up anyway), wakes the PC if it sleeps,
   runs a tools\windows script for N hours. Run once per change.

     .\register_task.ps1                                                             # knob search only (nightly.ps1, 6 h)
     .\register_task.ps1 -Script nightly_agent.ps1 -ScriptArgs "-UiHours 1.5 -AppHours 2.5 -Hours 2.5 -SearchHours 1 -Push" -At "2:00PM" -LimitHours 10
                                                                                     # the three agents (2026-10-27)

   The task keeps one name so re-registering replaces the previous job. It runs as the current user,
   only while that user is logged on (a locked screen is fine; a reboot without a login is not). #>
param([string]$Script = "nightly.ps1", [string]$ScriptArgs = "-Hours 6 -Patience 300", [string]$At = "4:00PM", [int]$LimitHours = 8,
      [switch]$RunWhenLoggedOff)   # register with a stored password so the task also runs after a reboot with nobody signed in
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$path = Join-Path $root "tools\windows\$Script"
if (-not (Test-Path $path)) { Write-Host "no such script: $path"; exit 1 }
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$path`" $ScriptArgs" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# WakeToRun: wakes the PC from sleep or hibernate at $At (needs "allow wake timers" = enabled, see power_setup.ps1;
# it cannot wake a PC that was shut down -- that takes a BIOS "power on by RTC alarm"). The battery flags are
# for a laptop desktop: never skip or stop the night because the charger state changed.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances IgnoreNew
if ($RunWhenLoggedOff) {
  # "Run whether user is logged on or not", with the password stored by Task Scheduler (not S4U: the Claude Code
  # and git credentials are DPAPI-protected and need a real logon session). Re-run this after a password change.
  $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for the account the nightly runs as"
  Register-ScheduledTask -TaskName "LiveReact nightly search" -Action $action -Trigger $trigger -Settings $settings `
      -Description "LiveReact nightly: $Script $ScriptArgs" -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
} else {
  Register-ScheduledTask -TaskName "LiveReact nightly search" -Action $action -Trigger $trigger -Settings $settings -Description "LiveReact nightly: $Script $ScriptArgs" -Force | Out-Null
}
Get-ScheduledTask -TaskName "LiveReact nightly search" | Format-List TaskName, State
(Get-ScheduledTask -TaskName "LiveReact nightly search").Actions | Format-List Execute, Arguments
Write-Host "Registered $Script $ScriptArgs at $At. Test it now with:  Start-ScheduledTask -TaskName 'LiveReact nightly search'   (then check results\nightly\*.log)"
