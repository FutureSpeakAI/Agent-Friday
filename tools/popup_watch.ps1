<#
.SYNOPSIS
  Watch for console windows appearing on the desktop and name what caused them.

.DESCRIPTION
  Detects the WINDOW, not a proxy for it. Earlier versions of this watched for
  WindowsTerminal.exe / OpenConsole.exe being COM-activated, which catches a
  popup only when Windows Terminal is the default console host. A classic
  conhost-hosted console produces no such process and was invisible to it -
  and conhost.exe itself cannot be used as the signal either, because
  CREATE_NO_WINDOW still allocates a console (hidden) and still spawns conhost.

  So this enumerates top-level VISIBLE windows of a console class via
  EnumWindows. That is the thing the person at the desk actually sees, and it
  is true for both console hosts.

  On each new window it dumps every process started in the preceding few
  seconds with command lines and parents, because the window's own owning PID
  is useless for attribution: Terminal is COM-activated by svchost, and a
  flashing console is usually gone before it can be queried.

  Deliberately broad on the lookback. An earlier version filtered to a
  hand-written list of process names and would have missed anything not on it.

  In-process only - the watcher spawns nothing. A Python version of this
  shelled out once per tick and reported 368 new processes in 120 seconds, of
  which ~360 were itself.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\popup_watch.ps1 -Minutes 90
#>
param(
  [int]$Minutes = 30,
  [int]$LookbackSeconds = 5,
  [int]$PollMs = 120
)

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class WinEnum {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<string> ConsoleWindows() {
    var outp = new List<string>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var cls = new StringBuilder(256); GetClassName(h, cls, 256);
      string c = cls.ToString();
      if (c != "ConsoleWindowClass" && c != "CASCADIA_HOSTING_WINDOW_CLASS") return true;
      var t = new StringBuilder(300); GetWindowText(h, t, 300);
      uint pid; GetWindowThreadProcessId(h, out pid);
      outp.Add(h.ToInt64() + "|" + pid + "|" + c + "|" + t.ToString());
      return true;
    }, IntPtr.Zero);
    return outp;
  }
}
"@

"popup_watch: {0} min, poll {1}ms, lookback {2}s - detecting VISIBLE console windows" -f $Minutes, $PollMs, $LookbackSeconds
"started {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date)

$seen = [System.Collections.Generic.HashSet[string]]::new()
foreach ($w in [WinEnum]::ConsoleWindows()) { [void]$seen.Add(($w -split '\|')[0]) }
"baseline: $($seen.Count) console window(s) already visible`n"

$end = (Get-Date).AddMinutes($Minutes)
$n = 0

while ((Get-Date) -lt $end) {
  foreach ($w in [WinEnum]::ConsoleWindows()) {
    $parts = $w -split '\|', 4
    $hwnd = $parts[0]
    if ($seen.Contains($hwnd)) { continue }
    [void]$seen.Add($hwnd)
    $n++
    $now = Get-Date
    "=============================================================="
    "POPUP #{0} at {1:HH:mm:ss.fff}" -f $n, $now
    "  hwnd={0} owner_pid={1} class={2}" -f $parts[0], $parts[1], $parts[2]
    "  title: '{0}'" -f $parts[3]
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($parts[1])"
    if ($owner) { "  owner: $($owner.Name) :: $(($owner.CommandLine -replace '\s+',' '))" }
    "  processes started in the previous $LookbackSeconds s:"
    $cut = $now.AddSeconds(-$LookbackSeconds)
    $recent = Get-CimInstance Win32_Process |
                Where-Object { $_.CreationDate -and $_.CreationDate -gt $cut } |
                Sort-Object CreationDate
    if (-not $recent) { "     (none - cause started earlier, or is not a new process)" }
    foreach ($r in $recent) {
      $cl = ($r.CommandLine -replace '\s+', ' ')
      if (-not $cl) { $cl = '(no command line)' }
      if ($cl.Length -gt 170) { $cl = $cl.Substring(0, 170) }
      $par = Get-CimInstance Win32_Process -Filter "ProcessId=$($r.ParentProcessId)"
      $pn = if ($par) { $par.Name } else { '(gone)' }
      "     {0:HH:mm:ss.fff} {1} [{2}] <- {3} [{4}]" -f `
        $r.CreationDate, $r.Name, $r.ProcessId, $pn, $r.ParentProcessId
      "          $cl"
    }
    ""
  }
  Start-Sleep -Milliseconds $PollMs
}

"`nfinished {0:HH:mm:ss}. {1} popup(s) recorded." -f (Get-Date), $n
