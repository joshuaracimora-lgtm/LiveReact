<# Registers (or replaces) the "LiveReact nightly search" Task Scheduler job: 1:00 AM daily,
   wakes the PC, runs a tools\windows script for N hours. Run once per change.

     .\register_task.ps1                                                             # knob search only (nightly.ps1, 6 h)
     .\register_task.ps1 -Script nightly_agent.ps1 -ScriptArgs "-Hours 4 -SearchHours 1.5"   # the engine agent

   The task keeps one name so re-registering replaces the previous job. It runs as the current user,
   only while that user is logged on (a locked screen is fine; a reboot without a login is not). #>
param([string]$Script = "nightly.ps1", [string]$ScriptArgs = "-Hours 6 -Patience 300", [string]$At = "1:00AM", [int]$LimitHours = 8)
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$path = Join-Path $root "tools\windows\$Script"
if (-not (Test-Path $path)) { Write-Host "no such script: $path"; exit 1 }
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$path`" $ScriptArgs" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "LiveReact nightly search" -Action $action -Trigger $trigger -Settings $settings -Description "LiveReact nightly: $Script $ScriptArgs" -Force | Out-Null
Get-ScheduledTask -TaskName "LiveReact nightly search" | Format-List TaskName, State
(Get-ScheduledTask -TaskName "LiveReact nightly search").Actions | Format-List Execute, Arguments
Write-Host "Registered $Script $ScriptArgs at $At. Test it now with:  Start-ScheduledTask -TaskName 'LiveReact nightly search'   (then check results\nightly\*.log)"
