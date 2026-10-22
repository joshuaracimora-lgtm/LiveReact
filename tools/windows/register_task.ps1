<# Registers (or replaces) the "LiveReact nightly search" Task Scheduler job: 1:00 AM daily,
   wakes the PC, runs tools\windows\nightly.ps1 for 6 hours, only when on AC power. Run once. #>
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script = Join-Path $root "tools\windows\nightly.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Hours 6 -Patience 300" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 1:00AM
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "LiveReact nightly search" -Action $action -Trigger $trigger -Settings $settings -Description "Parameter search over the labeled tracks (tools/loop/search.py)" -Force | Out-Null
Get-ScheduledTask -TaskName "LiveReact nightly search" | Format-List TaskName, State
Write-Host "Registered. Test it now with:  Start-ScheduledTask -TaskName 'LiveReact nightly search'   (then check results\nightly\*.log)"
