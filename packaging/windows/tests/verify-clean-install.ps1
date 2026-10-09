#Requires -Version 5.1
<#
    Fresh-machine verification of a built installer.

    Runs the setup program (AgentFriday-Setup-<tag>.exe) silently in the
    CURRENT profile of a machine that has never seen Friday, starts the
    installed server, checks what a first-time person gets, uninstalls keeping
    the person's data, checks what is left behind, and installs again over the
    kept data. It is written for a disposable CI runner (a fresh Windows VM per
    job) and refuses to run anywhere else, because it installs into the real
    profile, writes the hosts file and removes what it installed.

    The model page is answered with the cloud option (/ModelsCloud=1), so
    nothing is downloaded in CI.

    What it checks, each recorded in RESULT.json:

      install   the setup program exited 0 and Friday's files are in place
      shortcuts the Desktop shortcut (wherever Windows says the Desktop is)
                and the Start menu shortcut exist
      apps      the Apps list has the setup program's entry, at this release
      first-run first-run.json records the cloud choice and no download
      localhost Friday answers on http://127.0.0.1:<port>/ with no login
      health    /api/health answers and reports this release
      consent   first run opens on the vault-first consent flow, and the
                weekly update check is a question, not a default
      setup-chat the setup chat starts at its first stage
      first-load reloading the desktop does not end first-run setup
      connections the checklist lists the services and no secret
      setup-later "Set up later" completes setup with defaults (run last)
      schedules scheduled jobs default to a local seat; the update check is off
      phone     the phone is off until someone configures it
      officecli the document engine is installed and matches its pinned SHA-256
      address   agent.<name> answers once its hosts entry exists and Friday's
                listener is on (the same marked hosts block the elevated step
                in Settings writes). Trusting Friday's certificate raises a
                Windows security dialog, so it is not automated here.
      uninstall the install folder, shortcuts and Apps entry are gone, and
                ~/.friday (the person's own data) is kept, byte for byte
      reinstall setup runs again over the kept data; Friday starts and answers
                /api/health; the kept data is still there
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Setup,
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
$InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday'
$FridayDir   = Join-Path $env:USERPROFILE '.friday'
$ServerLog   = Join-Path $WorkDir 'server.log'

if (Test-Path $InstallRoot) { throw "not a fresh machine: $InstallRoot exists" }
if (Test-Path $FridayDir)   { throw "not a fresh machine: $FridayDir exists" }

$AppsKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{BE782F40-3D1C-4F12-8635-4F7561938F75}_is1'
$py = Join-Path $InstallRoot 'python\python.exe'
$env:FRIDAY_PORT = "$Port"
$env:PYTHONUTF8 = '1'

function Invoke-Setup([string] $tag) {
    $log = Join-Path $WorkDir "setup-$tag.log"
    $p = Start-Process -FilePath $Setup -PassThru -Wait `
         -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=`"$log`"",
                         '/ModelsCloud=1', '/SkipMemory=1', '/SkipJudgment=1')
    return $p.ExitCode
}

function Test-ShortcutsLanded {
    # The Desktop is wherever Windows says it is (OneDrive may have moved it).
    $desk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Agent Friday.lnk'
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Agent Friday\Agent Friday.lnk'
    return [pscustomobject]@{ Desktop = (Test-Path -LiteralPath $desk); StartMenu = (Test-Path -LiteralPath $menu);
                              DesktopPath = $desk; StartMenuPath = $menu }
}

function Start-InstalledServer([string] $logName) {
    $log = Join-Path $WorkDir $logName
    return Start-Process -FilePath $py -ArgumentList @('server.py') `
        -WorkingDirectory (Join-Path $InstallRoot 'app') -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
}

function Wait-ForFriday($proc) {
    $deadline = (Get-Date).AddSeconds($BootSeconds)
    $got = $null
    while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
        try {
            $got = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 10
            if ($got.StatusCode -eq 200) { break }
        } catch { Start-Sleep -Seconds 3 }
    }
    return $got
}

# ── Install ────────────────────────────────────────────────────────────────
$installExit = Invoke-Setup 'fresh'
Check 'install' (($installExit -eq 0) -and (Test-Path $py) -and (Test-Path (Join-Path $InstallRoot 'app\server.py'))) `
      "exit $installExit; interpreter present: $(Test-Path $py)"

$lnk = Test-ShortcutsLanded
Check 'shortcuts' ($lnk.Desktop -and $lnk.StartMenu) `
      "desktop $($lnk.DesktopPath): $($lnk.Desktop); start menu $($lnk.StartMenuPath): $($lnk.StartMenu)"

$apps = $null
try { $apps = Get-ItemProperty -Path $AppsKey -ErrorAction Stop } catch { }
Check 'apps' ($null -ne $apps -and $apps.DisplayName -eq 'Agent Friday Beta 1.0.1' -and [string]$apps.DisplayVersion -match '^1\.0\.1-beta\.1$') `
      $(if ($apps) { "$($apps.DisplayName) $($apps.DisplayVersion)" } else { 'no Apps entry' })

$firstRun = $null
try { $firstRun = Get-Content -LiteralPath (Join-Path $InstallRoot 'first-run.json') -Raw | ConvertFrom-Json } catch { }
Check 'first-run' (($null -ne $firstRun) -and ($firstRun.cloud -eq $true) -and ($firstRun.consent_download -eq $false) -and
                   (-not (Test-Path (Join-Path $FridayDir 'runtime\models')))) `
      $(if ($firstRun) { "cloud: $($firstRun.cloud); consent: $($firstRun.consent_download)" } else { 'no first-run.json' })

# ── Start the installed server ─────────────────────────────────────────────
$server = Start-InstalledServer 'server.log'
$base = "http://127.0.0.1:$Port"
$page = Wait-ForFriday $server

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

    $health = Api '/api/health'
    Check 'health' ([string]$health.version -eq '1.0.1b1') "version: $($health.version); release: $($health.release_name)"

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

    # The document engine: installed, and the file matches the pin its record names.
    $ocDir = Join-Path $FridayDir 'runtime\officecli'
    $ocExe = Join-Path $ocDir 'officecli.exe'
    $ocRec = $null
    try { $ocRec = Get-Content (Join-Path $ocDir 'INSTALL.json') -Raw | ConvertFrom-Json } catch { }
    $ocHash = if (Test-Path $ocExe) { (Get-FileHash $ocExe -Algorithm SHA256).Hash.ToLower() } else { '' }
    $ocOk = ($null -ne $ocRec) -and $ocHash -and ($ocHash -eq [string]$ocRec.sha256)
    Check 'officecli' $ocOk ("installed: $(Test-Path $ocExe); pinned: $(if ($ocRec) { $ocRec.pinned_version } else { 'no record' }); hash matches: $($ocHash -eq [string]$ocRec.sha256)")

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
    # The listener may need a moment after it is switched on and IIS is
    # stopped: ask up to three times, and keep the last error so a failure
    # names its cause. The pass condition is unchanged.
    $named = $null
    $namedErr = ''
    foreach ($attempt in 1..3) {
        try { $named = Invoke-WebRequest -Uri "http://$hostName/" -UseBasicParsing -TimeoutSec 20; break }
        catch { $namedErr = "attempt ${attempt}: $($_.Exception.Message)"; Start-Sleep -Seconds 3 }
    }
    # Content is a byte array for some content types; read the raw body as text.
    $body = ''
    $title = ''
    if ($named) {
        $body = [System.Text.Encoding]::UTF8.GetString($named.RawContentStream.ToArray())
        $t = [regex]::Match($body, '(?is)<title>(.*?)</title>')
        if ($t.Success) { $title = $t.Groups[1].Value.Trim() }
    }
    Check 'address' (($null -ne $named) -and ($named.StatusCode -eq 200) -and ($body -match 'FRIDAY')) `
          ("http://$hostName/ -> " + $(if ($named) { "$($named.StatusCode) $($named.Headers['Content-Type']); title: '$title'; server: $($named.Headers['Server'])" } else { "no answer ($namedErr)" }) + "; listener ok: $($serve.ok)")

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

# ── Uninstall, keeping the person's data ───────────────────────────────────
# A sentinel in the data home, hashed now: the setup program's uninstaller must
# leave it byte for byte, and so must the reinstall.
$sentinel = Join-Path $FridayDir 'ci-sentinel.txt'
Set-Content -LiteralPath $sentinel -Value "kept on purpose $(Get-Date -Format o)" -Encoding ascii
$sentinelHash = (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
$vaultCfg = Join-Path $FridayDir 'vault\.vault_config.json'
$vaultHashBefore = $(if (Test-Path -LiteralPath $vaultCfg) { (Get-FileHash -LiteralPath $vaultCfg -Algorithm SHA256).Hash } else { '' })

$uninstaller = Join-Path $InstallRoot 'unins000.exe'
if (-not (Test-Path -LiteralPath $uninstaller)) { throw "the setup program left no uninstaller at $uninstaller" }
# The uninstaller hands off to a copy of itself and returns, so wait for the
# folder to go rather than for the process.
Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$(Join-Path $WorkDir 'uninstall.log')`"") | Out-Null
$gone = (Get-Date).AddSeconds(300)
while ((Get-Date) -lt $gone -and ((Test-Path $InstallRoot) -or (Test-Path $AppsKey))) { Start-Sleep -Seconds 3 }
$lnkAfter = Test-ShortcutsLanded
$programs = [Environment]::GetFolderPath('Programs')
$menuLeft = @(Get-ChildItem -LiteralPath $programs -Recurse -Filter '*Friday*' -ErrorAction SilentlyContinue)
$keptOk = (Test-Path -LiteralPath $sentinel) -and ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -eq $sentinelHash)
$vaultSame = ($vaultHashBefore -eq '') -or ((Test-Path -LiteralPath $vaultCfg) -and ((Get-FileHash -LiteralPath $vaultCfg -Algorithm SHA256).Hash -eq $vaultHashBefore))
Check 'uninstall' ((-not (Test-Path $InstallRoot)) -and (-not (Test-Path $AppsKey)) -and (-not $lnkAfter.Desktop) -and ($menuLeft.Count -eq 0) -and $keptOk -and $vaultSame) `
      ("install folder gone: $(-not (Test-Path $InstallRoot)); Apps entry gone: $(-not (Test-Path $AppsKey)); desktop shortcut gone: $(-not $lnkAfter.Desktop); " +
       "start menu leftovers: $($menuLeft.Count); data kept byte for byte: $keptOk; vault config unchanged: $vaultSame")

# ── Reinstall over the kept data ───────────────────────────────────────────
$reExit = Invoke-Setup 'reinstall'
$lnkRe = Test-ShortcutsLanded
$manifestRe = $null
try { $manifestRe = Get-Content -LiteralPath (Join-Path $InstallRoot 'install-manifest.json') -Raw | ConvertFrom-Json } catch { }
$serverRe = $null
$healthRe = $null
if ($reExit -eq 0 -and (Test-Path $py)) {
    $serverRe = Start-InstalledServer 'server-reinstall.log'
    try {
        $pageRe = Wait-ForFriday $serverRe
        if ($pageRe -and $pageRe.StatusCode -eq 200) {
            $tokenRe = [regex]::Match($pageRe.Content, 'window\.__FRIDAY_API_TOKEN="([^"]+)"').Groups[1].Value
            $healthRe = (Invoke-WebRequest -Uri "$base/api/health" -Headers @{ 'X-Friday-Token' = $tokenRe } -UseBasicParsing -TimeoutSec 60).Content | ConvertFrom-Json
        }
    } finally {
        if ($serverRe -and -not $serverRe.HasExited) { Stop-Process -Id $serverRe.Id -Force }
        Start-Sleep -Seconds 3
    }
}
$keptAgain = (Test-Path -LiteralPath $sentinel) -and ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -eq $sentinelHash)
Check 'reinstall' (($reExit -eq 0) -and $lnkRe.Desktop -and $lnkRe.StartMenu -and ($null -ne $manifestRe) -and
                   ([int64]$manifestRe.build_sequence -gt 51403) -and ($null -ne $healthRe) -and ([string]$healthRe.version -eq '1.0.1b1') -and $keptAgain) `
      ("exit $reExit; shortcuts: $($lnkRe.Desktop)/$($lnkRe.StartMenu); build sequence: " +
       $(if ($manifestRe) { $manifestRe.build_sequence } else { 'no manifest' }) + "; health version: " +
       $(if ($healthRe) { $healthRe.version } else { 'no answer' }) + "; data kept: $keptAgain")

$Results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $WorkDir 'RESULT.json') -Encoding UTF8
$failed = @($Results.Keys | Where-Object { -not $Results[$_].ok })
if ($failed.Count) { Write-Host "FAILED: $($failed -join ', ')"; exit 1 }
Write-Host 'All fresh-install checks passed.'
