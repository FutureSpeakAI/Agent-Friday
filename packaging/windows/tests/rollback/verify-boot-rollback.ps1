#Requires -Version 5.1
<#
    The rollback leg (north star §34.2-8): on the INSTALLED build, a bad change
    followed by starts that never reach serving is rolled back to the last
    state that actually booted, and Friday then starts and serves again.

    Steps, on a fresh GitHub-hosted Windows runner:
      install       the setup program, silently
      known-good    Friday starts, serves, and its boot guard records the
                    proven-bootable state (~/.friday/boot_guard/known_good)
      bad-change    the page (app\index.html, one of the self-editable
                    surfaces the boot guard covers) is overwritten
      failed-starts three starts are stopped mid-boot, after the guard marked
                    them started and before they served: real interrupted
                    starts, which is what a crashing change looks like to it
      rollback      the next start rolls back before serving: the page is
                    byte-for-byte the known-good one again, the broken copy is
                    kept under boot_guard\failed\<time>, a plain-words note is
                    written, /api/health answers this release, and the
                    person's data is untouched
      uninstall     removes what it installed

    Writes RESULT.json in -WorkDir; exits 1 if any check failed. Every check
    starts as "did not run" so a crash is a failure, never a pass. Runs only
    on a disposable GitHub-hosted runner.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Setup,
    [Parameter(Mandatory)][string] $WorkDir,
    [int] $Port = 3000,
    [int] $BootSeconds = 300
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'verify-boot-rollback.ps1 installs into this profile and breaks it on purpose; it runs only on a disposable GitHub-hosted runner.'
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday'
$FridayDir   = Join-Path $env:USERPROFILE '.friday'
$GuardDir    = Join-Path $FridayDir 'boot_guard'
$Attempt     = Join-Path $GuardDir 'boot_attempt.json'
$py          = Join-Path $InstallRoot 'python\python.exe'
$AppDir      = Join-Path $InstallRoot 'app'
$Page        = Join-Path $AppDir 'index.html'
$env:FRIDAY_PORT = "$Port"
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
if (Test-Path $InstallRoot) { throw "not a fresh machine: $InstallRoot exists" }
if (Test-Path $FridayDir)   { throw "not a fresh machine: $FridayDir exists" }

$Results = [ordered]@{}
foreach ($c in 'install', 'known-good', 'bad-change', 'failed-starts', 'rollback', 'page-restored',
               'kept-for-inspection', 'note', 'serves-again', 'data-intact', 'uninstall') {
    $Results[$c] = [ordered]@{ ok = $false; detail = 'did not run' }
}
function Check([string] $name, [bool] $ok, [string] $detail) {
    $Results[$name] = [ordered]@{ ok = $ok; detail = $detail }
    Write-Host ("[{0}] {1}: {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)
}
function Read-Json([string] $path) { try { return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch { return $null } }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$vm = [regex]::Match((Get-Content -LiteralPath (Join-Path $RepoRoot 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"([^"]+)"')
$ExpectedVersion = $vm.Groups[1].Value

$n = 0
function Start-Friday {
    $script:n++
    $log = Join-Path $WorkDir ("server-{0}.log" -f $script:n)
    return Start-Process -FilePath $py -ArgumentList @('server.py') -WorkingDirectory $AppDir -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"
}
function Stop-Friday($proc) {
    if ($proc -and -not $proc.HasExited) { & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null }
    if ($proc) { $proc.WaitForExit(30000) | Out-Null }
}
function Wait-Served($proc) {
    $deadline = (Get-Date).AddSeconds($BootSeconds)
    while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 10
            if ($r.StatusCode -eq 200) { return $r }
        } catch { Start-Sleep -Seconds 2 }
    }
    return $null
}

$server = $null
try {
    # ── install ──────────────────────────────────────────────────────────
    $p = Start-Process -FilePath $Setup -PassThru -Wait -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
          '/SP-', "/LOG=`"$(Join-Path $WorkDir 'setup.log')`"", '/ModelsCloud=1', '/SkipMemory=1', '/SkipJudgment=1')
    Check 'install' (($p.ExitCode -eq 0) -and (Test-Path $py) -and (Test-Path $Page)) "exit $($p.ExitCode); page present: $(Test-Path $Page)"
    if (-not $Results['install'].ok) { throw 'install failed' }

    # ── a start that serves, and the state it proves ─────────────────────
    $server = Start-Friday
    $served = Wait-Served $server
    $manifest = Join-Path $GuardDir 'known_good.json'
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline -and -not (Test-Path $manifest)) { Start-Sleep -Seconds 3 }
    $att = Read-Json $Attempt
    $goodHash = (Get-FileHash -LiteralPath $Page -Algorithm SHA256).Hash
    Check 'known-good' (($null -ne $served) -and (Test-Path $manifest) -and $att.last_start_completed) `
          "served: $($null -ne $served); known_good.json: $(Test-Path $manifest); last start completed: $($att.last_start_completed)"
    Stop-Friday $server
    if (-not $Results['known-good'].ok) { throw 'no proven-bootable state' }

    # The person's data, to prove a rollback never touches it.
    $sentinel = Join-Path $FridayDir 'ci-rollback-sentinel.txt'
    $stamp = [guid]::NewGuid().ToString()
    Set-Content -LiteralPath $sentinel -Value $stamp -Encoding ascii

    # ── a bad change ─────────────────────────────────────────────────────
    Set-Content -LiteralPath $Page -Value '<!-- a broken change --><html><body>BROKEN</body></html>' -Encoding utf8
    Check 'bad-change' ((Get-FileHash -LiteralPath $Page -Algorithm SHA256).Hash -ne $goodHash) 'the page was overwritten'

    # ── three starts that never reach serving ───────────────────────────
    $stopped = 0
    for ($i = 1; $i -le 3; $i++) {
        $before = (Read-Json $Attempt).last_start
        $server = Start-Friday
        $deadline = (Get-Date).AddSeconds($BootSeconds)
        $marked = $false
        while ((Get-Date) -lt $deadline -and -not $server.HasExited) {
            $a = Read-Json $Attempt
            if ($a -and $a.last_start -and ($a.last_start -ne $before) -and (-not $a.last_start_completed)) { $marked = $true; break }
            Start-Sleep -Milliseconds 250
        }
        Stop-Friday $server
        $a = Read-Json $Attempt
        if ($marked -and -not $a.last_start_completed) { $stopped++ }
        Write-Host "start $i stopped mid-boot: $marked; failures now $($a.consecutive_failures)"
        # mark_boot_started counts a previous start that never completed, so
        # the timestamps must differ from one start to the next.
        Start-Sleep -Seconds 2
    }
    $a = Read-Json $Attempt
    Check 'failed-starts' (($stopped -eq 3) -and ([int]$a.consecutive_failures -ge 2)) `
          "starts stopped before serving: $stopped of 3; consecutive failures: $($a.consecutive_failures)"

    # ── the start that rolls back ────────────────────────────────────────
    $failedRoot = Join-Path $GuardDir 'failed'
    $failedBefore = @(Get-ChildItem -LiteralPath $failedRoot -Directory -ErrorAction SilentlyContinue).Count
    $notesFile = Join-Path $GuardDir 'rollback_notes.jsonl'
    $notesBefore = @(Get-Content -LiteralPath $notesFile -ErrorAction SilentlyContinue).Count
    $server = Start-Friday
    $page = Wait-Served $server
    $log = Get-Content -LiteralPath (Join-Path $WorkDir ("server-{0}.log" -f $n)) -Raw -ErrorAction SilentlyContinue
    Check 'rollback' ($log -match 'rolled back to the last proven-bootable state') `
          $(if ($log -match '(Boot guard:[^\r\n]*)') { $Matches[1] } else { 'no boot-guard line in the server log' })
    Check 'page-restored' ((Get-FileHash -LiteralPath $Page -Algorithm SHA256).Hash -eq $goodHash) 'the page is the known-good one again'
    $failedAfter = @(Get-ChildItem -LiteralPath $failedRoot -Directory -ErrorAction SilentlyContinue)
    $kept = $failedAfter | Sort-Object LastWriteTime | Select-Object -Last 1
    $brokenKept = $false
    # The guard keeps each replaced entry under its slug (index.html-<hash>);
    # only that entry can hold the broken page.
    if ($kept) { $brokenKept = [bool](Get-ChildItem -LiteralPath $kept.FullName -File -Filter 'index.html*' -ErrorAction SilentlyContinue |
                 Where-Object { (Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue) -cmatch 'a broken change' }) }
    Check 'kept-for-inspection' (($failedAfter.Count -gt $failedBefore) -and $brokenKept) `
          "failed\ folders: $failedBefore -> $($failedAfter.Count); the broken page kept: $brokenKept"
    $notes = @(Get-Content -LiteralPath $notesFile -ErrorAction SilentlyContinue)
    Check 'note' (($notes.Count -gt $notesBefore) -and ($notes[-1] -match 'Rolled back')) "$($notes.Count - $notesBefore) new note(s)"
    $health = $null
    if ($page) {
        $token = [regex]::Match($page.Content, 'window\.__FRIDAY_API_TOKEN="([^"]+)"').Groups[1].Value
        try { $health = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -Headers @{ 'X-Friday-Token' = $token } `
                         -UseBasicParsing -TimeoutSec 60).Content | ConvertFrom-Json } catch { }
    }
    Check 'serves-again' (($null -ne $page) -and ($page.Content -match 'FRIDAY') -and ([string]$health.version -eq $ExpectedVersion)) `
          "page served: $($null -ne $page); /api/health version: $($health.version) (expected $ExpectedVersion)"
    Check 'data-intact' ((Get-Content -LiteralPath $sentinel -Raw -ErrorAction SilentlyContinue).Trim() -eq $stamp) 'the data folder was not touched'
}
finally {
    Stop-Friday $server
    $un = Join-Path $InstallRoot 'unins000.exe'
    if (Test-Path $un) {
        Start-Process -FilePath $un -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') | Out-Null
        $gone = (Get-Date).AddMinutes(5)
        while ((Get-Date) -lt $gone -and (Test-Path $InstallRoot)) { Start-Sleep -Seconds 3 }
    }
    Check 'uninstall' (-not (Test-Path $InstallRoot)) "install folder gone: $(-not (Test-Path $InstallRoot))"
    [ordered]@{ variant = 'rollback'; os = [string](Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption; checks = $Results } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $WorkDir 'RESULT.json') -Encoding UTF8
}
$failed = @($Results.Keys | Where-Object { -not $Results[$_].ok })
if ($failed) { Write-Host "FAILED: $($failed -join ', ')"; exit 1 }
Write-Host 'Rollback proven on the installed build.'
