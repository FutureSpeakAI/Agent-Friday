<#
  proxy-service-install.ps1 - make agent.friday survive reboot.

  Registers a boot-triggered Scheduled Task "AgentFridayProxy" that runs
  proxy-boot.ps1 as SYSTEM (hidden, highest privileges, restart-on-failure).
  Result: on every boot, BEFORE any user logs in, the hosts entry is verified/
  restored and Caddy comes up on loopback :443 with the trusted local CA.

  Why a scheduled task and not `sc.exe`/NSSM: Caddy has no native Windows-service
  interface (would need a wrapper), and we already know `caddy start/stop` hang on
  this box. A boot task running hidden `caddy run` under a supervising PowerShell
  loop is dependency-free, runs as SYSTEM before login, and lets us fold in the
  hosts self-heal that a bare service wrapper couldn't.

  Run normally; it self-elevates via UAC. Idempotent. Uninstall: proxy-down.ps1
  (or  Unregister-ScheduledTask -TaskName AgentFridayProxy -Confirm:$false).
#>

$ErrorActionPreference = 'Stop'
$TaskName = 'AgentFridayProxy'

# --- self-elevate (UAC) if not already admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
	Write-Host "Requesting administrator elevation (UAC)..." -ForegroundColor Yellow
	Start-Process powershell -Verb RunAs -ArgumentList `
		'-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`""
	return
}

$proxyDir = Join-Path $env:USERPROFILE '.friday\proxy'
$caddyExe = Join-Path $proxyDir 'caddy.exe'
$caddyfile = Join-Path $PSScriptRoot 'Caddyfile'
$bootScript = Join-Path $PSScriptRoot 'proxy-boot.ps1'
$logDir = Join-Path $proxyDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir 'service-install-last.log') -Force | Out-Null
try {
	Write-Host "== AgentFridayProxy: install boot service ==" -ForegroundColor Cyan
	foreach ($p in $caddyExe, $caddyfile, $bootScript) { if (-not (Test-Path $p)) { throw "missing: $p" } }

	# machine-wide runtime dirs (shared by elevated-user + SYSTEM contexts)
	New-Item -ItemType Directory -Force -Path 'C:\ProgramData\AgentFriday\caddy' | Out-Null
	New-Item -ItemType Directory -Force -Path 'C:\ProgramData\AgentFriday\logs'  | Out-Null

	# stop any manually-started Caddy so the service owns :443 / admin :2019
	try {
		$rq = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:2019/stop')
		$rq.Method = 'POST'; $rq.Proxy = $null; $rq.Timeout = 3000; $rq.ContentLength = 0
		$rq.GetResponse().Close()
	} catch {}
	Get-Process caddy -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
	Start-Sleep -Seconds 1

	# --- register the boot task (SYSTEM, at startup, hidden, restart-on-fail) ---
	$arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Caddyfile "{1}" -CaddyExe "{2}"' -f $bootScript, $caddyfile, $caddyExe
	$action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
	$trigger   = New-ScheduledTaskTrigger -AtStartup
	$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest  # S-1-5-18 = LocalSystem
	$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
		-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
		-MultipleInstances IgnoreNew -StartWhenAvailable
	Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
		-Settings $settings -Description 'Agent Friday loopback proxy (agent.friday) - boot auto-start + hosts self-heal.' -Force | Out-Null
	Write-Host "task: registered '$TaskName' (SYSTEM, AtStartup)" -ForegroundColor Green

	# --- start it now (no reboot needed) ---
	Start-ScheduledTask -TaskName $TaskName
	Write-Host "task: started" -ForegroundColor Green

	# wait for Caddy (launched by the task) to bind :443
	$ok = $false
	for ($i = 0; $i -lt 30; $i++) {
		Start-Sleep -Milliseconds 500
		$t = New-Object System.Net.Sockets.TcpClient
		try { $t.Connect('127.0.0.1', 443); if ($t.Connected) { $ok = $true; $t.Close(); break } } catch {} finally { $t.Dispose() }
	}
	if (-not $ok) { Write-Host "caddy: :443 not up yet; see C:\ProgramData\AgentFriday\logs\caddy.err.log" -ForegroundColor Red; throw "Caddy did not bind :443 under the service" }
	Write-Host "caddy: listening on 127.0.0.1:443 (via service)" -ForegroundColor Green

	# --- trust the CA from the (now machine-wide) storage ---
	$crt = Join-Path $proxyDir 'root.crt'
	$req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:2019/pki/ca/local')
	$req.Proxy = $null; $req.Timeout = 8000
	$sr = New-Object System.IO.StreamReader((($req.GetResponse()).GetResponseStream()))
	$ca = $sr.ReadToEnd() | ConvertFrom-Json
	$ca.root_certificate | Set-Content -LiteralPath $crt -Encoding ASCII
	$cert  = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $crt
	$store = New-Object System.Security.Cryptography.X509Certificates.X509Store 'Root','LocalMachine'
	$store.Open('ReadWrite')
	if (-not ($store.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint })) {
		$store.Add($cert); Write-Host "trust: installed root CA '$($cert.Subject)' into LocalMachine\Root" -ForegroundColor Green
	} else { Write-Host "trust: root CA already trusted (skipped)" -ForegroundColor DarkGray }
	$store.Close()

	Write-Host "`nDONE. agent.friday will auto-start on every boot (before login)." -ForegroundColor Cyan
	Write-Host "Verify: ops\proxy-verify.ps1   Uninstall: ops\proxy-down.ps1" -ForegroundColor DarkGray
}
catch { Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red; throw }
finally { Stop-Transcript | Out-Null }
