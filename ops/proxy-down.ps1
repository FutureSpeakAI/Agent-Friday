<#
  proxy-down.ps1 - full rollback of the agent.friday presentation proxy.

  Reverses everything proxy-up.ps1 did:
    1. Stops the background Caddy process.
    2. Removes Caddy's local CA from the Windows trust store (`caddy untrust`).
    3. Removes the agent.friday block from the Windows hosts file.

  Run it normally; it self-elevates via UAC. Idempotent - safe if already down.
  Leaves Friday's app and its :3000 port completely untouched.

  The downloaded caddy.exe and runtime data under %USERPROFILE%\.friday\proxy
  are left in place (harmless, out of the repo). Delete that folder to remove
  them too.
#>

$ErrorActionPreference = 'Continue'

# --- self-elevate (UAC) if not already admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
	Write-Host "Requesting administrator elevation (UAC)..." -ForegroundColor Yellow
	Start-Process powershell -Verb RunAs -ArgumentList `
		'-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`""
	return
}

$caddyExe = Join-Path $env:USERPROFILE '.friday\proxy\caddy.exe'
$logDir   = Join-Path $env:USERPROFILE '.friday\proxy\logs'
if (Test-Path $logDir) { Start-Transcript -Path (Join-Path $logDir 'teardown-last.log') -Force | Out-Null }

Write-Host "== Agent Friday proxy: rollback ==" -ForegroundColor Cyan

# --- 0. remove the boot service/task FIRST ---
# Must happen before we kill Caddy, or its supervising loop would relaunch it.
$TaskName = 'AgentFridayProxy'
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
	try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
	# the supervising powershell + its hidden caddy child are separate processes;
	# kill the task's powershell so the loop can't relaunch caddy mid-teardown.
	Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
		Where-Object { $_.CommandLine -like '*proxy-boot.ps1*' } |
		ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
	Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
	Write-Host "service: task '$TaskName' stopped and unregistered" -ForegroundColor Green
} else {
	Write-Host "service: no '$TaskName' task (skipped)" -ForegroundColor DarkGray
}

# --- 1. stop Caddy ---
# Graceful admin-API POST /stop (best-effort, short timeout), then force-kill.
# NB: the `caddy stop` CLI can hang on Windows, so we hit the API directly.
try {
	$rq = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:2019/stop')
	$rq.Method = 'POST'; $rq.Proxy = $null; $rq.Timeout = 3000; $rq.ContentLength = 0
	$rq.GetResponse().Close()
} catch {}
Start-Sleep -Milliseconds 500
Get-Process caddy -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "caddy: stopped" -ForegroundColor Green

# --- 2. untrust: remove the Caddy local root CA from LocalMachine\Root ---
# We installed it via X509Store (caddy's own trust no-ops here), so remove it the
# same way. Matches by subject so it works even after the running instance is gone.
try {
	$store = New-Object System.Security.Cryptography.X509Certificates.X509Store 'Root','LocalMachine'
	$store.Open('ReadWrite')
	$toRemove = @($store.Certificates | Where-Object { $_.Subject -like '*Caddy Local Authority*' })
	foreach ($c in $toRemove) { $store.Remove($c); Write-Host "untrust: removed '$($c.Subject)'" -ForegroundColor Green }
	if ($toRemove.Count -eq 0) { Write-Host "untrust: no Caddy CA in LocalMachine\Root (skipped)" -ForegroundColor DarkGray }
	$store.Close()
} catch { Write-Host "untrust: $($_.Exception.Message)" -ForegroundColor DarkGray }

# --- 3. remove hosts block ---
# SAFETY: only ever strip OUR marked block; verify the result still holds the
# bulk of the file (Spybot ships a large hosts list) and never write an empty
# file. If the strip would remove more than our block, abort and restore.
$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$begin = '# >>> agent.friday (Friday demo proxy) >>>'
$bak   = "$hosts.friday.bak"
$content = Get-Content -LiteralPath $hosts -Raw
if ([string]::IsNullOrEmpty($content)) {
	Write-Host "hosts: file is empty -- NOT writing. Restore from $hosts.friday.orig if needed." -ForegroundColor Yellow
} elseif ($content -match [regex]::Escape($begin)) {
	Copy-Item -LiteralPath $hosts -Destination $bak -Force
	$pattern = "(?s)\r?\n?# >>> agent\.friday \(Friday demo proxy\) >>>.*?# <<< agent\.friday \(Friday demo proxy\) <<<\r?\n?"
	$new = [regex]::Replace($content, $pattern, "")
	# guard: our block is ~135 bytes; if the file shrank by much more, something
	# is wrong (e.g. a greedy match) -- do NOT write.
	if ($new.Length -lt ($content.Length - 4096) -or $new.Trim().Length -eq 0) {
		Write-Host "hosts: strip would remove too much -- ABORTED, file left intact." -ForegroundColor Red
	} else {
		$hf = Get-Item -LiteralPath $hosts -Force
		$wasReadOnly = $hf.IsReadOnly
		if ($wasReadOnly) { $hf.IsReadOnly = $false }
		Set-Content -LiteralPath $hosts -Value $new -NoNewline -Encoding ASCII
		if ((Get-Item -LiteralPath $hosts -Force).Length -eq 0) {
			Copy-Item -LiteralPath $bak -Destination $hosts -Force
			Write-Host "hosts: write emptied the file -- restored from $bak." -ForegroundColor Red
		}
		if ($wasReadOnly) { (Get-Item -LiteralPath $hosts -Force).IsReadOnly = $true }
		Write-Host "hosts: agent.friday block removed. Backup: $bak" -ForegroundColor Green
	}
} else {
	Write-Host "hosts: no agent.friday block found (skipped)." -ForegroundColor DarkGray
}

Write-Host "`nRollback complete. agent.friday no longer resolves or serves." -ForegroundColor Cyan
try { Stop-Transcript | Out-Null } catch {}
