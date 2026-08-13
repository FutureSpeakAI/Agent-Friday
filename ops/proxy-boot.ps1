<#
  proxy-boot.ps1 - the worker the boot service/task runs (as SYSTEM, hidden).

  Responsibilities, in order, forever:
    1. Ensure the agent.friday hosts lines exist (self-heal a Spybot wipe by
       restoring from hosts.friday.bak / .orig, or re-appending our block).
    2. Launch `caddy run` (NOT `caddy start`, which hangs on Windows) using the
       existing loopback-only Caddyfile, hidden, with machine-wide storage.
    3. Supervise: every interval, re-verify hosts and relaunch Caddy if it died.

  It never exits, so the Task Scheduler keeps it in the "Running" state; the
  restart-on-failure setting is a backstop if the process itself is killed.

  Invoked by the scheduled task with absolute paths (SYSTEM's %USERPROFILE% is
  NOT swebs, so nothing here may depend on a per-user profile dir).
#>
param(
	[Parameter(Mandatory)][string]$Caddyfile,
	[Parameter(Mandatory)][string]$CaddyExe,
	[int]$GuardIntervalSec = 30
)
$ErrorActionPreference = 'Continue'

$logDir = 'C:\ProgramData\AgentFriday\logs'
New-Item -ItemType Directory -Force -Path 'C:\ProgramData\AgentFriday\caddy' | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$bootLog = Join-Path $logDir 'boot.log'
function Log($m) { "{0}  {1}" -f (Get-Date -Format 's'), $m | Add-Content -LiteralPath $bootLog }

$hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$begin = '# >>> agent.friday (Friday demo proxy) >>>'
$end   = '# <<< agent.friday (Friday demo proxy) <<<'
$block = "`r`n$begin`r`n127.0.0.1`tagent.friday`r`n::1`t`tagent.friday`r`n$end`r`n"
$bak   = "$hosts.friday.bak"
$orig  = "$hosts.friday.orig"

function Ensure-Hosts {
	$content = ''
	try { $content = Get-Content -LiteralPath $hosts -Raw -ErrorAction Stop } catch {}
	if ($content -match 'agent\.friday') { return }   # resolves fine, nothing to do
	Log "hosts: agent.friday MISSING -> self-heal"
	$hf = Get-Item -LiteralPath $hosts -Force -ErrorAction SilentlyContinue
	$wasRO = $hf -and $hf.IsReadOnly
	if ($wasRO) { $hf.IsReadOnly = $false }
	if ([string]::IsNullOrWhiteSpace($content) -or $content.Length -lt 1000) {
		# wiped/tiny -> restore a full known-good list
		if (Test-Path $bak) { Copy-Item -LiteralPath $bak -Destination $hosts -Force; Log "hosts: restored full file from .bak" }
		elseif (Test-Path $orig) { Copy-Item -LiteralPath $orig -Destination $hosts -Force; Add-Content -LiteralPath $hosts -Value $block -Encoding ASCII; Log "hosts: restored from .orig + re-added block" }
		else { Add-Content -LiteralPath $hosts -Value $block -Encoding ASCII; Log "hosts: no backup -> wrote block only" }
	} else {
		# file intact but our lines were stripped -> re-append just the block
		Add-Content -LiteralPath $hosts -Value $block -Encoding ASCII
		Log "hosts: re-appended agent.friday block"
	}
	if ($wasRO) { try { (Get-Item -LiteralPath $hosts -Force).IsReadOnly = $true } catch {} }
	ipconfig /flushdns | Out-Null
}

function Caddy-Alive {
	try {
		$r = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:2019/config/')
		$r.Proxy = $null; $r.Timeout = 3000
		$r.GetResponse().Close(); return $true
	} catch { return $false }
}

function Start-Caddy {
	Log "caddy: launching hidden run ($CaddyExe)"
	Start-Process -FilePath $CaddyExe `
		-ArgumentList 'run','--config',"`"$Caddyfile`"",'--adapter','caddyfile' `
		-WindowStyle Hidden `
		-RedirectStandardOutput (Join-Path $logDir 'caddy.out.log') `
		-RedirectStandardError  (Join-Path $logDir 'caddy.err.log')
}

Log "=== proxy-boot start (identity=$([Security.Principal.WindowsIdentity]::GetCurrent().Name)) ==="
Ensure-Hosts
if (-not (Caddy-Alive)) { Start-Caddy }

while ($true) {
	Start-Sleep -Seconds $GuardIntervalSec
	Ensure-Hosts
	if (-not (Caddy-Alive)) { Log "caddy: not alive -> relaunch"; Start-Caddy }
}
