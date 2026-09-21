# Poll for console-capable process starts from INSIDE one PowerShell process.
#
# The Python version spawned a powershell per tick to do the query, so it
# reported 368 "new processes" in 120 seconds of which ~360 were itself. A
# watcher whose own noise buries the signal is worse than no watcher. Here the
# query is Get-CimInstance in-process: zero child processes, zero self-noise.
#
# Win32_ProcessStartTrace would be better (event-driven, catches sub-tick
# lifetimes) but it needs elevation on this box - HRESULT 0x80041032.
param([int]$Seconds = 150)

$watch = @('cmd.exe','powershell.exe','pwsh.exe','node.exe','claude.exe',
           'WindowsTerminal.exe','OpenConsole.exe','git.exe','conhost.exe')

function Snap {
  $h = @{}
  foreach ($p in Get-CimInstance Win32_Process) {
    if ($p.Name -in $watch) {
      $h[[int]$p.ProcessId] = @{ n=$p.Name; pp=[int]$p.ParentProcessId
                                 c=($p.CommandLine -replace '\s+',' ') }
    } elseif ($p.Name -like '*.exe') {
      # keep everything for ancestry resolution, cheaply
      $h[[int]$p.ProcessId] = @{ n=$p.Name; pp=[int]$p.ParentProcessId; c='' }
    }
  }
  $h
}

$known = Snap
$me = $PID
"watching $Seconds s; baseline $($known.Count) processes"
$end = (Get-Date).AddSeconds($Seconds)
$n = 0
while ((Get-Date) -lt $end) {
  $cur = Snap
  foreach ($procId in $cur.Keys) {
    if ($known.ContainsKey($procId)) { continue }
    $info = $cur[$procId]
    if ($info.n -notin $watch) { continue }
    if ($procId -eq $me) { continue }
    $n++
    $c = $info.c; if ($c.Length -gt 170) { $c = $c.Substring(0,170) }
    "`n[{0:HH:mm:ss}] #{1} {2} pid={3}" -f (Get-Date), $n, $info.n, $procId
    "    $c"
    $id = $info.pp
    for ($i = 0; $i -lt 4 -and $id; $i++) {
      $par = $cur[$id]; if (-not $par) { $par = $known[$id] }
      if (-not $par) { "    ^ $id (gone)"; break }
      $pc = $par.c; if ($pc.Length -gt 110) { $pc = $pc.Substring(0,110) }
      "    ^ $id $($par.n) :: $pc"
      $id = $par.pp
    }
  }
  foreach ($k in $cur.Keys) { $known[$k] = $cur[$k] }
  Start-Sleep -Milliseconds 200
}
"`ndone. $n console-capable start(s)."

