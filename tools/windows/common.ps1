# Shared by nightly.ps1 and nightly_agent.ps1 (dot-source: . "$PSScriptRoot\common.ps1"). ASCII only: PS 5.1.

# Python children (the search, the agent's pytest/status.py) print track names like "Laszewo" with
# accents; the Windows console code page is not UTF-8 and status.py died with UnicodeEncodeError.
# PYTHONUNBUFFERED keeps their lines flowing into the transcript instead of one blob at exit.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

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
function Release-Wake { try { [void][LiveReact.Power]::SetThreadExecutionState([uint32]2147483648) } catch {} }

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
