<#
  forensics-down.ps1 - stop the forensics capture.

  Unregisters the "AgentFridayForensics" Scheduled Task. Captured data in
  ~/.friday/forensics/ is LEFT IN PLACE - stopping the capture and throwing
  away what it captured are different decisions, and this script only makes
  the first one. Delete that directory by hand if you want the second.

  Needs no elevation (the task is registered for the current user).
#>
[CmdletBinding()]
param([string]$TaskName = 'AgentFridayForensics')

$ErrorActionPreference = 'Stop'

$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $t) {
	Write-Host "task '$TaskName' is not registered - nothing to do." -ForegroundColor Yellow
} else {
	try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
	Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
	Write-Host "task: unregistered '$TaskName'" -ForegroundColor Green
}

# A run killed mid-flight leaves its PID lock behind. The Python side treats a
# lock older than 15 minutes as stale and reclaims it, so this is tidiness
# rather than repair - but leaving a lock file next to a stopped capture reads
# like something is still running, and it is not.
$lock = Join-Path $env:USERPROFILE '.friday\forensics\.lock'
if (Test-Path $lock) {
	Remove-Item $lock -Force -ErrorAction SilentlyContinue
	Write-Host "cleared a leftover run lock" -ForegroundColor Green
}

$outDir = Join-Path $env:USERPROFILE '.friday\forensics'
if (Test-Path $outDir) {
	$size = (Get-ChildItem $outDir -Recurse -File | Measure-Object -Sum Length).Sum
	Write-Host ("captured data kept at {0} ({1:N1} MB)" -f $outDir, ($size / 1MB)) -ForegroundColor Cyan
}
