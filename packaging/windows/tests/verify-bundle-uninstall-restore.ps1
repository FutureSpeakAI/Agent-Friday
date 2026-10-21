#Requires -Version 5.1
<#
    Export first, real uninstall, restore on the same clean machine (CI only).

    north star 27.10 (uninstall offers export first; verify deletion; an
    uninstall receipt), 27.6 (restore), 34.3-7 (restore from a portable bundle).

      install     the setup program installs silently on a fresh runner
      seed        a realistic data folder (tests/clean_machine/seed_home.py,
                  run by the INSTALLED Python): vault, memory, skills,
                  workspaces, receipts, and credentials that must not travel
      uninstall   the setup program's own uninstaller, silent, /REMOVEDATA=1
                  /BUNDLETO=<folder>: it makes and verifies a Friday Bundle
                  first, then removes everything
      nothing     install folder, data folder, upgrade backups, every
                  agent-friday Credential Manager entry, the Apps entry and the
                  shortcuts are gone; the uninstall receipt says so
      reinstall   the setup program again, silently
      restore     `friday restore <bundle>` with the installed Python, then
                  tests/clean_machine/check_restore.py
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Setup,
    [Parameter(Mandatory)][string] $WorkDir
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'verify-bundle-uninstall-restore.ps1 installs, uninstalls and edits Credential Manager; CI runners only.'
}

$Results = [ordered]@{}
function Check([string] $name, [bool] $ok, [string] $detail) {
    $Results[$name] = [ordered]@{ ok = $ok; detail = $detail }
    $mark = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1}: {2}" -f $mark, $name, $detail)
}
function Save-Result {
    ($Results | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath (Join-Path $WorkDir 'RESULT.json') -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$RepoRoot    = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday'
$FridayDir   = Join-Path $env:USERPROFILE '.friday'
$Backups     = Join-Path $env:USERPROFILE '.friday-backups'
$AppsKey     = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{BE782F40-3D1C-4F12-8635-4F7561938F75}_is1'
$py          = Join-Path $InstallRoot 'python\python.exe'
$BundleDir   = Join-Path $WorkDir 'bundles'
$Expected    = Join-Path $WorkDir 'expected.json'
$env:PYTHONUTF8 = '1'
$env:FRIDAY_BUNDLE_PASSPHRASE = 'ci clean machine bundle passphrase'   # pragma: allowlist secret
$env:SEED_VAULT_PASSPHRASE    = 'ci clean machine vault passphrase'    # pragma: allowlist secret

if (Test-Path $InstallRoot) { throw "not a fresh machine: $InstallRoot exists" }

function Invoke-Setup([string] $tag) {
    $log = Join-Path $WorkDir "setup-$tag.log"
    $p = Start-Process -FilePath $Setup -PassThru -Wait `
         -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=`"$log`"",
                         '/ModelsCloud=1', '/SkipMemory=1', '/SkipJudgment=1')
    return $p.ExitCode
}
function Get-FridayTargets {
    $out = @()
    foreach ($line in (& cmdkey.exe /list)) {
        if ($line -match '^\s*Target:\s*(.+?)\s*$' -and $Matches[1] -match 'agent-friday') { $out += $Matches[1] }
    }
    return ,$out
}

try {
    # ── install ─────────────────────────────────────────────────────────────
    $code = Invoke-Setup 'install'
    Check 'install' ($code -eq 0 -and (Test-Path $py)) "setup exited $code; python present: $(Test-Path $py)"

    # ── seed, with the installed Python ─────────────────────────────────────
    $env:FRIDAY_HOME = $FridayDir
    $seed = Start-Process -FilePath $py -PassThru -Wait -NoNewWindow `
        -WorkingDirectory (Join-Path $InstallRoot 'app') `
        -ArgumentList @((Join-Path $RepoRoot 'tests\clean_machine\seed_home.py'), '--expected', $Expected, '--over-fresh-install') `
        -RedirectStandardOutput (Join-Path $WorkDir 'seed.log') -RedirectStandardError (Join-Path $WorkDir 'seed.err')
    Check 'seed' ($seed.ExitCode -eq 0 -and (Test-Path $Expected)) "seed exited $($seed.ExitCode)"
    Remove-Item Env:\FRIDAY_HOME
    Check 'credentials_written' (@(Get-FridayTargets).Count -gt 0) ("agent-friday targets: " + (@(Get-FridayTargets) -join ', '))

    # ── uninstall: export first, then everything ────────────────────────────
    $uninstaller = Join-Path $InstallRoot 'unins000.exe'
    Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/REMOVEDATA=1', "/BUNDLETO=`"$BundleDir`"", "/LOG=`"$(Join-Path $WorkDir 'uninstall.log')`"") | Out-Null
    $deadline = (Get-Date).AddSeconds(900)
    while ((Get-Date) -lt $deadline -and ((Test-Path $InstallRoot) -or (Test-Path $AppsKey) -or (Test-Path $FridayDir))) {
        Start-Sleep -Seconds 5
    }
    $bundle = Get-ChildItem -LiteralPath $BundleDir -Filter '*.fridaybundle' -ErrorAction SilentlyContinue | Select-Object -First 1
    Check 'bundle_made_first' ($null -ne $bundle) "bundle: $($bundle.FullName)"
    $targets = @(Get-FridayTargets)
    $desk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Agent Friday.lnk'
    $menuLeft = @(Get-ChildItem -LiteralPath ([Environment]::GetFolderPath('Programs')) -Recurse -Filter '*Friday*' -ErrorAction SilentlyContinue)
    Check 'nothing_left' ((-not (Test-Path $InstallRoot)) -and (-not (Test-Path $FridayDir)) -and (-not (Test-Path $Backups)) -and
                          ($targets.Count -eq 0) -and (-not (Test-Path $AppsKey)) -and (-not (Test-Path $desk)) -and ($menuLeft.Count -eq 0)) `
          ("install folder: $(Test-Path $InstallRoot); data folder: $(Test-Path $FridayDir); backups: $(Test-Path $Backups); " +
           "credentials: $($targets -join ', '); Apps entry: $(Test-Path $AppsKey); desktop: $(Test-Path $desk); start menu: $($menuLeft.Count)")
    $receiptPath = Join-Path $env:TEMP 'AgentFriday-uninstall\uninstall-receipt.json'
    $receipt = $null
    if (Test-Path $receiptPath) { $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json }
    Check 'uninstall_receipt' ($null -ne $receipt -and $receipt.data_removed -and $receipt.checks.credentials_left -eq 0 -and
                               $receipt.checks.data_folder_gone -and $receipt.checks.upgrade_backups_gone) `
          ("receipt: " + ($(if ($receipt) { $receipt | ConvertTo-Json -Compress -Depth 4 } else { 'missing' })))

    # ── reinstall and restore ───────────────────────────────────────────────
    $code = Invoke-Setup 'reinstall'
    Check 'reinstall' ($code -eq 0 -and (Test-Path $py)) "setup exited $code"
    $env:FRIDAY_HOME = $FridayDir
    $r = Start-Process -FilePath $py -PassThru -Wait -NoNewWindow -WorkingDirectory (Join-Path $InstallRoot 'app') `
        -ArgumentList @('-m', 'agent_friday.cli', 'restore', $bundle.FullName) `
        -RedirectStandardOutput (Join-Path $WorkDir 'restore.log') -RedirectStandardError (Join-Path $WorkDir 'restore.err')
    Check 'restore' ($r.ExitCode -eq 0) "friday restore exited $($r.ExitCode)"
    $c = Start-Process -FilePath $py -PassThru -Wait -NoNewWindow -WorkingDirectory (Join-Path $InstallRoot 'app') `
        -ArgumentList @((Join-Path $RepoRoot 'tests\clean_machine\check_restore.py'), '--expected', $Expected) `
        -RedirectStandardOutput (Join-Path $WorkDir 'check.json') -RedirectStandardError (Join-Path $WorkDir 'check.err')
    Check 'restored_and_checked' ($c.ExitCode -eq 0) (Get-Content -LiteralPath (Join-Path $WorkDir 'check.json') -Raw)
} catch {
    Check 'script' $false "$_"
} finally {
    Save-Result
}
$failed = @($Results.Values | Where-Object { -not $_.ok }).Count
if ($failed) { Write-Host "$failed check(s) failed"; exit 1 }
exit 0
