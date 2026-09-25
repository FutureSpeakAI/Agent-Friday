#Requires -Version 5.1
<#
    Fresh-machine verification of a built installer.

    Installs the zip into the CURRENT profile of a machine that has never seen
    Friday, starts the installed server, checks what a first-time person gets,
    then uninstalls and checks what is left behind. It is written for a
    disposable CI runner (a fresh Windows VM per job) and refuses to run
    anywhere else, because it installs into the real profile, writes the
    hosts file and removes what it installed.

    What it checks, each recorded in RESULT.json:

      install   the installer's own verified steps finished
      localhost Friday answers on http://127.0.0.1:<port>/ with no login
      consent   first run opens on the vault-first consent flow, and the
                weekly update check is a question, not a default
      setup-chat the setup chat starts at its first stage
      first-load reloading the desktop does not end first-run setup
      connections the checklist lists the services and no secret
      setup-later "Set up later" completes setup with defaults (run last)
      schedules scheduled jobs default to a local seat; the update check is off
      phone     the phone is off until someone configures it
      address   agent.<name> answers once its hosts entry exists and Friday's
                listener is on (the same marked hosts block the elevated step
                in Settings writes). Trusting Friday's certificate raises a
                Windows security dialog, so it is not automated here.
      uninstall the install folder, shortcuts and Apps entry are gone, and
                ~/.friday (the person's own data) is kept
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Zip,
    [Parameter(Mandatory)][string] $WorkDir,
    [int] $Port = 3000,
    [int] $BootSeconds = 420
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'verify-clean-install.ps1 installs into this profile and edits the hosts file; it runs only on a disposable CI runner.'
}

$Results = [ordered]@{}
function Check([string] $name, [bool] $ok, [string] $detail) {
    $Results[$name] = [ordered]@{ ok = $ok; detail = $detail }
    $mark = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1}: {2}" -f $mark, $name, $detail)
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$Unpacked    = Join-Path $WorkDir 'unpacked'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday'
$FridayDir   = Join-Path $env:USERPROFILE '.friday'
$ServerLog   = Join-Path $WorkDir 'server.log'

if (Test-Path $InstallRoot) { throw "not a fresh machine: $InstallRoot exists" }
if (Test-Path $FridayDir)   { throw "not a fresh machine: $FridayDir exists" }

# ── Install ────────────────────────────────────────────────────────────────
Expand-Archive -LiteralPath $Zip -DestinationPath $Unpacked -Force
$installer = Get-ChildItem -LiteralPath $Unpacked -Recurse -Filter 'install.ps1' -File |
             Where-Object { $_.FullName -notmatch '\\payload\\' } | Select-Object -First 1
if (-not $installer) { throw 'no install.ps1 in the zip' }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer.FullName `
    -Unattended -SkipOllama -SkipMemory -SkipJudgment *> (Join-Path $WorkDir 'install.log')
$installExit = $LASTEXITCODE
$py = Join-Path $InstallRoot 'python\python.exe'
Check 'install' (($installExit -eq 0) -and (Test-Path $py) -and (Test-Path (Join-Path $InstallRoot 'app\server.py'))) `
      "exit $installExit; interpreter present: $(Test-Path $py)"

# ── Start the installed server ─────────────────────────────────────────────
$env:FRIDAY_PORT = "$Port"
$env:PYTHONUTF8 = '1'
$server = Start-Process -FilePath $py -ArgumentList @('server.py') `
    -WorkingDirectory (Join-Path $InstallRoot 'app') -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $ServerLog -RedirectStandardError "$ServerLog.err"

$base = "http://127.0.0.1:$Port"
$page = $null
$deadline = (Get-Date).AddSeconds($BootSeconds)
while ((Get-Date) -lt $deadline -and -not $server.HasExited) {
    try {
        $page = Invoke-WebRequest -Uri "$base/" -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 10
        if ($page.StatusCode -eq 200) { break }
    } catch { Start-Sleep -Seconds 3 }
}

try {
    $served = ($null -ne $page) -and ($page.StatusCode -eq 200)
    Check 'localhost' ($served -and $page.Content -match 'FRIDAY') `
          $(if ($served) { "200 from $base/ with no login" } else { "no answer from $base/ within $BootSeconds s" })
    if (-not $served) { throw 'server did not start' }

    $token = [regex]::Match($page.Content, 'window\.__FRIDAY_API_TOKEN="([^"]+)"').Groups[1].Value
    $hdr = @{ 'X-Friday-Token' = $token }
    function Api([string] $path, [string] $method = 'GET', $body = $null) {
        $req = @{ Uri = "$base$path"; Headers = $hdr; UseBasicParsing = $true; Method = $method; TimeoutSec = 60 }
        if ($null -ne $body) { $req.Body = ($body | ConvertTo-Json -Compress); $req.ContentType = "application/json" }
        return (Invoke-WebRequest @req).Content | ConvertFrom-Json
    }

    # First run: the consent flow comes first, the vault second, and the
    # update check is asked rather than assumed.
    $copy = Api '/api/onboarding/copy'
    $order = @($copy.screens | ForEach-Object { $_.name })
    Check 'consent' (($order[0] -eq 'collects') -and ($order[1] -eq 'vault') -and ($order -contains 'updates')) `
          ("screens: " + ($order -join ', '))

    # After the consent screens, the setup chat: it starts at its first stage
    # and nothing has been said yet.
    $chat = Api '/api/setup-chat/state'
    Check 'setup-chat' (($chat.stage -eq 'welcome') -and (-not $chat.consent_done) -and (-not $chat.completed)) `
          "stage: $($chat.stage); consent done: $($chat.consent_done); completed: $($chat.completed)"

    # Loading the desktop, twice, must leave first-run setup unfinished: a
    # reload before the person finishes still brings the setup back.
    foreach ($n in 1..2) { $null = Api '/api/evolution'; $null = Api '/api/setup/status' }
    $still = Api '/api/setup/status'
    Check 'first-load' (-not $still.initialized) "initialized after two page loads: $($still.initialized)"

    # The connection checklist lists the services, and never a secret.
    $raw = (Invoke-WebRequest -Uri "$base/api/setup/connections" -Headers $hdr -UseBasicParsing -TimeoutSec 60).Content
    $list = $raw | ConvertFrom-Json
    $ids = @($list.items | ForEach-Object { $_.id })
    $want = @('provider:anthropic', 'provider:openai', 'provider:openrouter', 'google', 'twilio',
              'connector:github', 'channel:telegram', 'platform:youtube', 'connector:notion',
              'provider:brave', 'provider:elevenlabs', 'cloudflare')
    $missing = @($want | Where-Object { $ids -notcontains $_ })
    $secretShaped = $raw -match '(sk-ant-|sk-or-|AIza[0-9A-Za-z_\-]{20}|ghp_[0-9A-Za-z]{20}|xox[baprs]-)'
    Check 'connections' (($missing.Count -eq 0) -and ($ids.Count -ge 30) -and (-not $secretShaped)) `
          ("$($ids.Count) services; missing: " + ($missing -join ', ') + "; key-shaped text present: $secretShaped")

    # Scheduled jobs: seeded at boot into ~/.friday/schedules.json.
    $schedFile = Join-Path $FridayDir 'schedules.json'
    $sched = @()
    $wait = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $wait -and -not (Test-Path $schedFile)) { Start-Sleep -Seconds 2 }
    if (Test-Path $schedFile) { $sched = Get-Content $schedFile -Raw | ConvertFrom-Json }
    $local = @($sched | Where-Object { $_.task.local_only -eq $true } | ForEach-Object { $_.id })
    $upd = $sched | Where-Object { $_.id -eq 'sch_update_check' } | Select-Object -First 1
    $okSched = ($local.Count -gt 0) -and ($null -ne $upd) -and (-not $upd.enabled)
    Check 'schedules' $okSched ("local-only: " + ($local -join ', ') + "; update check enabled: " + $(if ($upd) { $upd.enabled } else { 'missing' }))

    $phone = Api '/api/phone/status'
    # ingress is an object ({running: false}); any object is truthy, so ask it.
    Check 'phone' ((-not $phone.config.enabled) -and (-not $phone.ingress.running)) `
          "enabled: $($phone.config.enabled); ingress running: $($phone.ingress.running)"

    # agent.<name>: the marked hosts block, then Friday's own listener.
    # GitHub's Windows images run IIS, which answers port 80 through http.sys
    # ahead of any other listener. A person's PC normally does not; on a
    # disposable runner it is stopped so the check is about Friday.
    foreach ($svc in 'W3SVC', 'WAS') {
        if (Get-Service -Name $svc -ErrorAction SilentlyContinue) { Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue }
    }
    $la = Api '/api/local-address'
    $hostName = [string]$la.host
    if (-not $hostName) { $hostName = 'agent.friday' }
    $hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
    Add-Content -LiteralPath $hostsPath -Value ("`r`n# >>> Agent Friday local address >>>`r`n127.0.0.1`t$hostName`r`n# <<< Agent Friday local address <<<`r`n")
    ipconfig /flushdns | Out-Null
    $serve = Api '/api/local-address/serve' 'POST' @{ on = $true }
    $named = $null
    try { $named = Invoke-WebRequest -Uri "http://$hostName/" -UseBasicParsing -TimeoutSec 20 } catch { }
    # Content is a byte array for some content types; read the raw body as text.
    $body = ''
    $title = ''
    if ($named) {
        $body = [System.Text.Encoding]::UTF8.GetString($named.RawContentStream.ToArray())
        $t = [regex]::Match($body, '(?is)<title>(.*?)</title>')
        if ($t.Success) { $title = $t.Groups[1].Value.Trim() }
    }
    Check 'address' (($null -ne $named) -and ($named.StatusCode -eq 200) -and ($body -match 'FRIDAY')) `
          ("http://$hostName/ -> " + $(if ($named) { "$($named.StatusCode) $($named.Headers['Content-Type']); title: '$title'; server: $($named.Headers['Server'])" } else { 'no answer' }) + "; listener ok: $($serve.ok)")

    # Last, because it finishes first-run setup: "Set up later" from the
    # first stage completes setup with defaults.
    $null = Api '/api/setup-chat/begin' 'POST' @{ routing_mode = 'local_only' }
    $done = Api '/api/setup-chat/skip-all' 'POST' @{}
    $status = Api '/api/setup/status'
    Check 'setup-later' ($done.completed -and $status.initialized -and (Test-Path (Join-Path $FridayDir '.setup_complete'))) `
          "completed: $($done.completed); initialized: $($status.initialized)"
}
finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    Start-Sleep -Seconds 3
}

# ── Uninstall ──────────────────────────────────────────────────────────────
$uninstaller = Join-Path $InstallRoot 'tools\uninstall.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uninstaller -Unattended -InstallRoot $InstallRoot `
    *> (Join-Path $WorkDir 'uninstall.log')
$gone = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $gone -and (Test-Path $InstallRoot)) { Start-Sleep -Seconds 3 }
$arp = Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday'
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Agent Friday.lnk'
$programs = [Environment]::GetFolderPath('Programs')
$menuLeft = @(Get-ChildItem -LiteralPath $programs -Recurse -Filter '*Friday*' -ErrorAction SilentlyContinue)
Check 'uninstall' ((-not (Test-Path $InstallRoot)) -and (-not $arp) -and (-not (Test-Path $desktopLnk)) -and ($menuLeft.Count -eq 0) -and (Test-Path $FridayDir)) `
      ("install folder gone: $(-not (Test-Path $InstallRoot)); Apps entry gone: $(-not $arp); shortcuts left: $($menuLeft.Count + [int](Test-Path $desktopLnk)); ~/.friday kept: $(Test-Path $FridayDir)")

$Results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $WorkDir 'RESULT.json') -Encoding UTF8
$failed = @($Results.Keys | Where-Object { -not $Results[$_].ok })
if ($failed.Count) { Write-Host "FAILED: $($failed -join ', ')"; exit 1 }
Write-Host 'All fresh-install checks passed.'
