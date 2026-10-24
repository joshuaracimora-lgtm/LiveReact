<# Registers (or replaces) the "LiveReact nightly search" Task Scheduler job: 1:00 AM daily,
   wakes the PC, runs a tools\windows script for N hours, only when on AC power. Run once.

     .\register_task.ps1                                   # knob search only (nightly.ps1, 6 h)
     .\register_task.ps1 -Script nightly_agent.ps1 -Args "-Hours 4 -SearchHours 1.5"   # the engine agent

   The task keeps one name so re-registering replaces the previous job. #>
param([string]$Script = "nightly.ps1", [string]$Args = "-Hours 6 -Patience 300", [string]$At = "1:00AM", [int]$LimitHours = 8)
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script = Join-Path $root "tools\windows\$Script"
if (-not (Test-Path $script)) { Write-Host "no such script: $script"; exit 1 }
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" $Args" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "LiveReact nightly search" -Action $action -Trigger $trigger -Settings $settings -Description "LiveReact nightly: $Script $Args" -Force | Out-Null
Get-ScheduledTask -TaskName "LiveReact nightly search" | Format-List TaskName, State
Write-Host "Registered $Script $Args at $At. Test it now with:  Start-ScheduledTask -TaskName 'LiveReact nightly search'   (then check results\nightly\*.log)"
