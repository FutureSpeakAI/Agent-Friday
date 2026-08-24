<#
  forensics-verify.ps1 - is the capture actually running, and capturing?

  Answers three separate questions, because "the task exists" is not the same
  as "it ran" and neither is the same as "it wrote anything":

    1. is the Scheduled Task registered, and what did its last run return?
    2. when did the capture last write, per its own state file?
    3. what is on disk, and how big has it got?

  Read-only. Needs no elevation.
#>
[CmdletBinding()]
param([string]$TaskName = 'AgentFridayForensics')

$ErrorActionPreference = 'Continue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$outDir   = Join-Path $env:USERPROFILE '.friday\forensics'

Write-Host "== AgentFridayForensics: verify ==" -ForegroundColor Cyan

# 1. the task
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $t) {
	Write-Host "  task      : NOT REGISTERED (run ops/forensics-install.ps1)" -ForegroundColor Red
} else {
	$info = Get-ScheduledTaskInfo -TaskName $TaskName
	Write-Host "  task      : $($t.State)"
	Write-Host "  last run  : $($info.LastRunTime)"
	# 267009 = STILL_RUNNING, and 0 = success. Anything else is a real failure.
	$rc = $info.LastTaskResult
	$rcTxt = switch ($rc) { 0 { 'OK' } 267009 { 'still running' } default { "FAILED ($rc)" } }
	$colour = if ($rc -eq 0 -or $rc -eq 267009) { 'Green' } else { 'Red' }
	Write-Host "  last rc   : $rcTxt" -ForegroundColor $colour
	Write-Host "  next run  : $($info.NextRunTime)"
}

# 2 + 3. what the capture itself says, straight from the script
$venvPy = Join-Path $repoRoot 'venv\Scripts\python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
$snap = Join-Path $PSScriptRoot 'forensics-snapshot.py'
if ($py -and (Test-Path $snap)) {
	Write-Host ""
	& $py $snap --status
}

# The tail of the capture's own log - the one place a silent failure shows up.
$log = Join-Path $outDir 'snapshot.log'
if (Test-Path $log) {
	Write-Host ""
	Write-Host "last 5 capture runs:" -ForegroundColor Cyan
	Get-Content $log -Tail 5 | ForEach-Object { Write-Host "  $_" }
}
