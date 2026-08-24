<#
  forensics-install.ps1 - keep Friday's volatile state from dying on restart.

  Registers a Scheduled Task "AgentFridayForensics" that runs
  ops/forensics-snapshot.py every few minutes as the CURRENT USER, capturing
  the in-memory orb and task registries plus the rotatable files into
  ~/.friday/forensics/.

  Why a user task and not SYSTEM (unlike proxy-service-install.ps1): the
  capture reads ~/.friday and calls Friday's own loopback API, both of which
  are the user's. SYSTEM would have neither, and would need UAC to register.
  This script needs no elevation.

  Why a scheduled task and not a daemon: a one-shot script replaced on a timer
  cannot leave a dead process behind, and a crashed run is simply superseded
  by the next tick. The Python side takes a PID lock, so the task's
  IgnoreNew policy and the lock agree even if a run overruns its interval.

  Safe to run against a live Friday: the capture is read-only, holds no locks,
  and never restarts anything.

  Idempotent - re-running re-registers cleanly (-Force).
  Uninstall: ops/forensics-down.ps1
  Check:     ops/forensics-verify.ps1
#>
[CmdletBinding()]
param(
	[int]$IntervalMinutes = 2,
	[string]$TaskName = 'AgentFridayForensics'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$script   = Join-Path $PSScriptRoot 'forensics-snapshot.py'
if (-not (Test-Path $script)) { throw "missing: $script" }

# Prefer the repo venv - it is the interpreter the project is tested against,
# and `python` on PATH here resolves to an unrelated venv.
$venvPy = Join-Path $repoRoot 'venv\Scripts\python.exe'
if (Test-Path $venvPy) {
	$py = $venvPy
} else {
	$py = (Get-Command python -ErrorAction SilentlyContinue).Source
	if (-not $py) { throw "no python found (looked for $venvPy, then PATH)" }
	Write-Host "note: repo venv not found, using $py" -ForegroundColor Yellow
}

$outDir = Join-Path $env:USERPROFILE '.friday\forensics'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "== AgentFridayForensics: install capture task ==" -ForegroundColor Cyan
Write-Host "  python   : $py"
Write-Host "  script   : $script"
Write-Host "  writes to: $outDir"
Write-Host "  interval : every $IntervalMinutes minute(s)"

$action = New-ScheduledTaskAction -Execute $py -Argument ('"{0}"' -f $script) `
	-WorkingDirectory $repoRoot

# Two triggers: one repeating from now (covers this session), one at logon
# (survives reboot). Both drive the same one-shot script.
$now = (Get-Date).AddMinutes(1)
$tRepeat = New-ScheduledTaskTrigger -Once -At $now `
	-RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
	-LogonType Interactive -RunLevel Limited

# ExecutionTimeLimit is short on purpose: a capture that takes minutes is a
# capture that has gone wrong, and killing it lets the next tick start clean
# rather than stacking runs behind a hung one.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
	-DontStopIfGoingOnBatteries -StartWhenAvailable `
	-MultipleInstances IgnoreNew `
	-ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
	-RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action `
	-Trigger @($tRepeat, $tLogon) -Principal $principal -Settings $settings `
	-Description 'Agent Friday - capture the in-memory orb/task registries and rotatable state files to ~/.friday/forensics. Read-only; safe alongside a live Friday.' `
	-Force | Out-Null
Write-Host "task: registered '$TaskName' (current user, every $IntervalMinutes min + at logon)" -ForegroundColor Green

Start-ScheduledTask -TaskName $TaskName
Write-Host "task: started one run now" -ForegroundColor Green

# Wait for that run to actually finish and prove it wrote something.
$deadline = (Get-Date).AddSeconds(90)
$ok = $false
while ((Get-Date) -lt $deadline) {
	Start-Sleep -Seconds 2
	$info = Get-ScheduledTaskInfo -TaskName $TaskName
	if ($info.LastTaskResult -eq 0 -and $info.LastRunTime -gt $now.AddMinutes(-2)) { $ok = $true; break }
	if ($info.LastTaskResult -ne 267009 -and $info.LastTaskResult -ne 0) {
		throw "task ran and returned $($info.LastTaskResult) - see $outDir\snapshot.log"
	}
}
if (-not $ok) {
	Write-Host "task: did not confirm a clean run inside 90s; check with ops/forensics-verify.ps1" -ForegroundColor Yellow
} else {
	Write-Host "task: first run completed cleanly" -ForegroundColor Green
}

Write-Host ""
Write-Host "Turn it off with:  powershell -ExecutionPolicy Bypass -File ops\forensics-down.ps1" -ForegroundColor Cyan
Write-Host "Check it with:     powershell -ExecutionPolicy Bypass -File ops\forensics-verify.ps1" -ForegroundColor Cyan
