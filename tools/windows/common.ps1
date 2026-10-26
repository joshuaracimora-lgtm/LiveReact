# Shared by nightly.ps1 and nightly_agent.ps1 (dot-source: . "$PSScriptRoot\common.ps1"). ASCII only: PS 5.1.

# Python children (the search, the agent's pytest/status.py) print track names like "Laszewo" with
# accents; the Windows console code page is not UTF-8 and status.py died with UnicodeEncodeError.
# PYTHONUNBUFFERED keeps their lines flowing into the transcript instead of one blob at exit.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

# Nobody is at the keyboard: if the stored GitHub credential is missing or expired, git must fail
# (and the log say so) instead of opening a sign-in window that holds the night until the time limit.
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"

# Keep the machine awake for the run (ES_CONTINUOUS | ES_SYSTEM_REQUIRED = 0x80000001). Best effort.
# A plain 0x80000000 literal is Int64 in PowerShell; pass the exact uint32 the API wants.
function Hold-Wake {
  try {
    if (-not ("LiveReact.Power" -as [type])) {
      Add-Type -Name Power -Namespace LiveReact -MemberDefinition @"
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
    }
    [void][LiveReact.Power]::SetThreadExecutionState([uint32]2147483649)
    Write-Host "power: system-required request held for this run"
    return $true
  } catch { Write-Host "power: could not hold a wake request ($($_.Exception.Message)); relying on power plan"; return $false }
}
function Release-Wake {
  try { [void][LiveReact.Power]::SetThreadExecutionState([uint32]2147483648) } catch {}
  try { if ($script:powerRequest) { [void][LiveReact.PowerReq]::Clear($script:powerRequest); $script:powerRequest = $null } } catch {}
}

# Second hold, process-wide: a kernel power request (PowerCreateRequest/PowerSetRequest, the thing
# `powercfg /requests` lists under SYSTEM). SetThreadExecutionState above is per THREAD, and the
# thread PowerShell runs a long script on is not guaranteed to be the one that made the call.
# 2026-10-24: the PC slept 03:22-11:29 in the middle of the agent's run with only the thread hold.
function Hold-PowerRequest([string]$Reason = "LiveReact nightly run") {
  try {
    if (-not ("LiveReact.PowerReq" -as [type])) {
      Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace LiveReact {
  public static class PowerReq {
    [StructLayout(LayoutKind.Sequential)]
    struct ReasonContext { public uint Version; public uint Flags; public IntPtr SimpleReasonString; }
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr PowerCreateRequest(ref ReasonContext ctx);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool PowerSetRequest(IntPtr request, int type);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool PowerClearRequest(IntPtr request, int type);
    // 0 = PowerRequestDisplayRequired, 1 = PowerRequestSystemRequired, 2 = PowerRequestAwayModeRequired
    public static IntPtr Hold(string reason) {
      ReasonContext ctx = new ReasonContext();
      ctx.Version = 0; ctx.Flags = 1;   // POWER_REQUEST_CONTEXT_SIMPLE_STRING
      ctx.SimpleReasonString = Marshal.StringToHGlobalUni(reason);
      IntPtr h = PowerCreateRequest(ref ctx);
      if (h == IntPtr.Zero || h == new IntPtr(-1)) throw new Exception("PowerCreateRequest failed, error " + Marshal.GetLastWin32Error());
      if (!PowerSetRequest(h, 1)) throw new Exception("PowerSetRequest(system) failed, error " + Marshal.GetLastWin32Error());
      PowerSetRequest(h, 2);   // away mode: best effort, lets the display sleep while the system stays up
      PowerSetRequest(h, 3);   // execution required: best effort, the request Modern Standby PCs honor
      return h;
    }
    public static void Clear(IntPtr h) { PowerClearRequest(h, 3); PowerClearRequest(h, 2); PowerClearRequest(h, 1); }
  }
}
"@
    }
    $script:powerRequest = [LiveReact.PowerReq]::Hold($Reason)
    Write-Host "power: kernel power request held (check with: powercfg /requests)"
    return $true
  } catch { Write-Host "power: could not hold a kernel power request ($($_.Exception.Message))"; return $false }
}

# AC index of one SUB_SLEEP setting of the active plan, by powercfg alias (STANDBYIDLE, HIBERNATEIDLE,
# UNATTENDSLEEP, RTCWAKE), in the unit powercfg shows (seconds for timeouts). $null if unreadable.
function Get-AcPowerIndex([string]$Alias) {
  try {
    $line = powercfg /aliases 2>&1 | Where-Object { $_ -match "\s$Alias\s*$" }
    if (-not $line) { return $null }
    $guid = ("$line" -split "\s+")[0]
    $q = powercfg /q SCHEME_CURRENT SUB_SLEEP 2>&1 | Out-String
    $m = [regex]::Match($q, [regex]::Escape($guid) + "[\s\S]*?Current AC Power Setting Index: 0x([0-9a-f]+)", "IgnoreCase")
    if ($m.Success) { return [convert]::ToInt32($m.Groups[1].Value, 16) } else { return $null }
  } catch { return $null }
}

# The PC is meant to sleep normally in the daytime (the resync brief keeps the 30 min default) and be
# AWAKE for the whole run no matter what. The requests above are what Windows is supposed to honor;
# 2026-10-24 it did not (asleep 03:22-11:29 mid-run). So for the run itself the plan's own sleep and
# hibernate timeouts are set to never here, and put back at the end. The previous values are written to
# a file first: if the run dies without reaching Restore-RunPowerPlan, the next start restores from
# that file instead of remembering "never" as the previous value and leaving the PC awake all day.
function Set-RunPowerPlan([string]$StateFile = "results\nightly\power_plan_prev.json") {
  try {
    $prev = $null
    if (Test-Path $StateFile) {
      $prev = Get-Content $StateFile -Raw | ConvertFrom-Json
      Write-Host "power plan: previous run did not restore the plan; keeping its saved values (sleep $($prev.standby_s) s, hibernate $($prev.hibernate_s) s)"
    } else {
      $prev = [pscustomobject]@{ standby_s = (Get-AcPowerIndex "STANDBYIDLE"); hibernate_s = (Get-AcPowerIndex "HIBERNATEIDLE") }
      if ($prev.standby_s -eq $null) { Write-Host "power plan: could not read the sleep timeout; leaving the plan alone"; return $false }
      $dir = Split-Path -Parent $StateFile
      if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
      $prev | ConvertTo-Json -Compress | Set-Content -Path $StateFile -Encoding ASCII
    }
    powercfg /change standby-timeout-ac 0 | Out-Null
    powercfg /change hibernate-timeout-ac 0 | Out-Null
    $now = Get-AcPowerIndex "STANDBYIDLE"
    if ($now -eq 0) { Write-Host "power plan: sleep and hibernate set to never for this run (was sleep $($prev.standby_s) s, hibernate $($prev.hibernate_s) s)"; return $true }
    Write-Host "power plan: tried to set sleep to never for the run but it reads $now s; relying on the power requests"
    return $false
  } catch { Write-Host "power plan: could not change it ($($_.Exception.Message)); relying on the power requests"; return $false }
}
function Restore-RunPowerPlan([string]$StateFile = "results\nightly\power_plan_prev.json") {
  try {
    if (-not (Test-Path $StateFile)) { return }
    $prev = Get-Content $StateFile -Raw | ConvertFrom-Json
    # powercfg /change takes MINUTES; 0 = never
    $sMin = [int][math]::Round([double]$prev.standby_s / 60)
    $hMin = [int][math]::Round([double]$prev.hibernate_s / 60)
    powercfg /change standby-timeout-ac $sMin | Out-Null
    powercfg /change hibernate-timeout-ac $hMin | Out-Null
    Remove-Item $StateFile -Force
    Write-Host "power plan: restored (sleep $sMin min, hibernate $hMin min)"
  } catch { Write-Host "power plan: could not restore it ($($_.Exception.Message)); run power_setup.ps1 -Check" }
}

# What the one-time tools\windows\power_setup.ps1 (admin) must have set, printed into the night's log with a
# WARNING line if not: unattended sleep 0 (after a wake-to-run Windows otherwise sleeps again 2 min later,
# whatever the sleep timeout says) and wake timers enabled (or the 1:00 trigger cannot wake the PC).
# The daytime sleep timeout is printed, not judged: Set-RunPowerPlan handles it for the run.
function Check-PowerPlan {
  try {
    $sleep = Get-AcPowerIndex "STANDBYIDLE"; $hib = Get-AcPowerIndex "HIBERNATEIDLE"
    $unatt = Get-AcPowerIndex "UNATTENDSLEEP"; $rtc = Get-AcPowerIndex "RTCWAKE"
    Write-Host "power plan (AC) now: sleep after $sleep s, hibernate after $hib s, unattended sleep after $unatt s, wake timers $rtc (1 = enabled, 2 = important only)"
    $bad = @()
    if ($unatt -ne 0) { $bad += "unattended sleep timeout is $unatt, not 0" }
    if ($rtc -ne 1) { $bad += "wake timers are $rtc, not enabled (the 1:00 wake may not fire)" }
    if ($bad.Count) { Write-Host "power plan WARNING: $($bad -join '; '). Run tools\windows\power_setup.ps1 as administrator." }
    return ($bad.Count -eq 0)
  } catch { Write-Host "power plan: could not read it ($($_.Exception.Message))"; return $false }
}

# Callers pass the run's start; the window opens 5 minutes earlier so the wake that started a 1:00 run
# (Power-Troubleshooter id 1, logged ~20 s before the task starts) is listed too.
function Get-PowerEvents([datetime]$Since) {
  # SilentlyContinue, not Stop: "no events found" is the normal case and Stop wrote a TerminatingError
  # record into the transcript every quiet night
  $ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 1, 41, 42, 107, 187, 1074, 6006, 6008; StartTime = $Since.AddMinutes(-5) } -ErrorAction SilentlyContinue |
          Where-Object { $_.Id -ne 1 -or $_.ProviderName -eq 'Microsoft-Windows-Power-Troubleshooter' })
  if ($ev.Count -eq 0) { return @("none") }
  $ev | Sort-Object TimeCreated | ForEach-Object {
    $line = "$($_.TimeCreated.ToString('HH:mm:ss')) id $($_.Id) $(($_.Message -split "`n")[0].Trim())"
    if ($_.Id -eq 187) {
      $caller = ([xml]$_.ToXml()).Event.EventData.Data | Where-Object { $_.Name -eq 'ApiCallerName' }
      if ($caller) { $line += " caller: $($caller.'#text')" }
    }
    $line
  }
}

# Put THIS PowerShell process in a Windows job object with KILL_ON_JOB_CLOSE. Every child it starts
# (cmd.exe -> claude.exe -> bash -> python, or the search) inherits the job, so when this process ends
# for any reason -- Stop-ScheduledTask, the task's 8 h limit, a crash -- the whole tree dies with it.
# Without this a stopped task left claude.exe running (seen 2026-10-23). Best effort: Task Scheduler
# already runs us inside its own job; nested jobs work on Windows 8+ and we log if they do not.
function Join-KillOnCloseJob {
  try {
    if (-not ("LiveReact.Job" -as [type])) {
      Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace LiveReact {
  public static class Job {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attrs, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint len);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [StructLayout(LayoutKind.Sequential)]
    struct Basic { public long PerProcessUserTime, PerJobUserTime; public uint LimitFlags;
                   public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcessLimit;
                   public UIntPtr Affinity; public uint PriorityClass, SchedulingClass; }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong a, b, c, d, e, f; }
    [StructLayout(LayoutKind.Sequential)]
    struct Extended { public Basic BasicLimitInformation; public IoCounters IoInfo;
                      public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed; }
    static IntPtr handle = IntPtr.Zero;   // held for the life of the process on purpose: closing it kills the job
    public static string AttachCurrentProcess() {
      if (handle == IntPtr.Zero) {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) return "CreateJobObject failed, error " + Marshal.GetLastWin32Error();
        Extended ext = new Extended();
        ext.BasicLimitInformation.LimitFlags = 0x2000;   // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        int len = Marshal.SizeOf(typeof(Extended));
        IntPtr p = Marshal.AllocHGlobal(len);
        try {
          Marshal.StructureToPtr(ext, p, false);
          if (!SetInformationJobObject(handle, 9, p, (uint)len))   // 9 = JobObjectExtendedLimitInformation
            return "SetInformationJobObject failed, error " + Marshal.GetLastWin32Error();
        } finally { Marshal.FreeHGlobal(p); }
      }
      IntPtr me = System.Diagnostics.Process.GetCurrentProcess().Handle;
      if (!AssignProcessToJobObject(handle, me)) return "AssignProcessToJobObject failed, error " + Marshal.GetLastWin32Error();
      return "";
    }
  }
}
"@
    }
    $err = [LiveReact.Job]::AttachCurrentProcess()
    if ($err) { Write-Host "job: children will NOT die with this script ($err); a stopped task may leave claude.exe running" }
    else { Write-Host "job: kill-on-close job object held; children die with this script" }
  } catch { Write-Host "job: could not create the job object ($($_.Exception.Message))" }
}
