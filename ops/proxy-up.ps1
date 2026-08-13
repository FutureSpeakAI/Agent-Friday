<#
  proxy-up.ps1 - bring up the agent.friday presentation proxy.

  Does three things that require administrator elevation:
    1. Adds  127.0.0.1 agent.friday  and  ::1 agent.friday  to the Windows
       hosts file (idempotent, inside a marked block).
    2. Starts Caddy in the background, serving https://agent.friday on
       loopback only, reverse-proxying to Friday on 127.0.0.1:3000.
    3. Runs `caddy trust` so Caddy's local CA is trusted -> clean padlock.

  Run it normally (double-click / non-admin shell); it self-elevates via UAC.
  Idempotent: safe to run again. Does NOT touch Friday's app or its :3000 port.

  Rollback: ops\proxy-down.ps1
#>

$ErrorActionPreference = 'Stop'

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
$logDir   = Join-Path $proxyDir 'logs'
$caddyExe = Join-Path $proxyDir 'caddy.exe'
$caddyfile = Join-Path $PSScriptRoot 'Caddyfile'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Start-Transcript -Path (Join-Path $logDir 'setup-last.log') -Force | Out-Null
try {
	Write-Host "== Agent Friday proxy: bring-up ==" -ForegroundColor Cyan

	if (-not (Test-Path $caddyExe))  { throw "Caddy binary not found at $caddyExe" }
	if (-not (Test-Path $caddyfile)) { throw "Caddyfile not found at $caddyfile" }

	# --- 1. hosts entries (idempotent, marked block) ---
	# SAFETY: this box runs Spybot, which flags the hosts file ReadOnly and can
	# re-immunize it. We keep a write-once pristine backup, verify every write
	# left the file non-empty, and auto-restore from backup if it didn't -- a
	# system hosts file must NEVER be left empty.
	$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
	$begin = '# >>> agent.friday (Friday demo proxy) >>>'
	$end   = '# <<< agent.friday (Friday demo proxy) <<<'
	$orig  = "$hosts.friday.orig"   # write-once pristine copy (never overwritten)
	$bak   = "$hosts.friday.bak"    # rolling pre-edit copy

	$before = (Get-Item -LiteralPath $hosts -Force).Length
	if ($before -eq 0) { throw "hosts file is EMPTY before edit -- refusing to touch it. Restore from $bak or $orig first." }
	if (-not (Test-Path $orig)) { Copy-Item -LiteralPath $hosts -Destination $orig -Force }

	$content = Get-Content -LiteralPath $hosts -Raw
	if ($content -notmatch [regex]::Escape($begin)) {
		Copy-Item -LiteralPath $hosts -Destination $bak -Force
		# Clear ReadOnly, append our block, restore ReadOnly (keeps Spybot happy).
		$hf = Get-Item -LiteralPath $hosts -Force
		$wasReadOnly = $hf.IsReadOnly
		if ($wasReadOnly) { $hf.IsReadOnly = $false }
		$block = "`r`n$begin`r`n127.0.0.1`tagent.friday`r`n::1`t`tagent.friday`r`n$end`r`n"
		Add-Content -LiteralPath $hosts -Value $block -Encoding ASCII
		# verify the write did not truncate/empty the file
		if ((Get-Item -LiteralPath $hosts -Force).Length -lt $before) {
			Copy-Item -LiteralPath $bak -Destination $hosts -Force
			throw "hosts write shrank the file -- restored from $bak. Aborting."
		}
		if ($wasReadOnly) { (Get-Item -LiteralPath $hosts -Force).IsReadOnly = $true }
		Write-Host "hosts: added agent.friday (IPv4 + IPv6). Backups: .orig + .bak" -ForegroundColor Green
	} else {
		Write-Host "hosts: agent.friday block already present (skipped)." -ForegroundColor DarkGray
	}

	# --- 2. (re)start Caddy as a hidden background process ---
	# If the boot service is installed, IT owns Caddy -- don't fight it. Just make
	# sure it's up (its supervising loop handles start/restart) and move on.
	$svc = Get-ScheduledTask -TaskName 'AgentFridayProxy' -ErrorAction SilentlyContinue
	if ($svc) {
		Write-Host "caddy: managed by the AgentFridayProxy boot service -- not starting a second instance." -ForegroundColor DarkGray
		if ($svc.State -ne 'Running') { Start-ScheduledTask -TaskName 'AgentFridayProxy' }
	} else {
		# `caddy start` is unreliable on Windows (can hang on detach), so we launch
		# `caddy run` hidden, capture its logs, and poll the port ourselves.
		# Stop any existing instance: graceful admin-API POST /stop (the `caddy stop`
		# CLI can hang on Windows), then force-kill whatever remains.
		try {
			$rq = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:2019/stop')
			$rq.Method = 'POST'; $rq.Proxy = $null; $rq.Timeout = 3000; $rq.ContentLength = 0
			$rq.GetResponse().Close()
		} catch {}
		Get-Process caddy -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
		Start-Sleep -Seconds 1

		$outLog = Join-Path $logDir 'caddy.out.log'
		$errLog = Join-Path $logDir 'caddy.err.log'
		Write-Host "caddy: starting (hidden background, loopback only)" -ForegroundColor Green
		Start-Process -FilePath $caddyExe `
			-ArgumentList 'run','--config',"`"$caddyfile`"",'--adapter','caddyfile' `
			-WindowStyle Hidden `
			-RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null
	}

	# poll for the HTTPS listener on loopback:443 (whoever started it)
	$ok = $false
	for ($i = 0; $i -lt 30; $i++) {
		Start-Sleep -Milliseconds 500
		$t = New-Object System.Net.Sockets.TcpClient
		try { $t.Connect('127.0.0.1', 443); if ($t.Connected) { $ok = $true; $t.Close(); break } } catch {} finally { $t.Dispose() }
	}
	if ($ok) {
		Write-Host "caddy: listening on 127.0.0.1:443" -ForegroundColor Green
	} else {
		Write-Host "caddy: :443 did NOT come up. Last startup log:" -ForegroundColor Red
		$errLog = Join-Path $logDir 'caddy.err.log'
		if (Test-Path $errLog) { Get-Content $errLog -Tail 40 }
		throw "Caddy did not bind :443"
	}

	# --- 3. trust the local CA (clean padlock) ---
	# NOTE: `caddy trust` silently no-ops on some Windows builds (it logs
	# "installing root certificate" but nothing lands in the store). So we fetch
	# the root CA from the admin API and install it into LocalMachine\Root
	# directly via the X509Store API - deterministic, no confirmation dialog.
	Write-Host "trust: fetching local root CA from admin API" -ForegroundColor Green
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
		$store.Add($cert)
		Write-Host "trust: installed root CA '$($cert.Subject)' into LocalMachine\Root" -ForegroundColor Green
	} else {
		Write-Host "trust: root CA already trusted (skipped)" -ForegroundColor DarkGray
	}
	$store.Close()

	Write-Host "`nDONE. Open https://agent.friday" -ForegroundColor Cyan
	Write-Host "Rollback any time:  ops\proxy-down.ps1" -ForegroundColor DarkGray
}
catch {
	Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
	throw
}
finally {
	Stop-Transcript | Out-Null
}
