#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Upgrade.ps1

    Everything an in-place upgrade does around the file copy:

      * work out what is already installed, and how it ranks against this
        release by BUILD SEQUENCE (never by version number: Beta 1.0 is
        1.0.0b1, below the 5.x line it replaces);
      * stop a running Friday, politely, by the exact processes that run from
        the install folder (never by a name pattern);
      * copy the person's data to a timestamped backup folder BEFORE any file
        is touched;
      * take a snapshot of the data home before and after, and say plainly if
        anything in it went missing or changed.

    The installer never deletes or rewrites anything under the data home
    (~/.friday). The backup and the before/after comparison exist so that a
    claim of "your data is untouched" is something that was checked.

    The sequence arithmetic restates agent_friday/release.py; a test runs this
    function and the Python one over the same table and fails on any difference.
#>

Set-StrictMode -Version 2.0

$script:EraFloor = 100000000

function Get-BuildSequence {
    <#  The build sequence of a version string or tag, or $null. See
        src/agent_friday/release.py for the definition. #>
    param([string] $Version)
    if (-not $Version) { return $null }
    $rx = '^\s*[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:(?:[-.]?(a|alpha|b|beta|rc|c|pre|preview|dev)[-.]?(\d+)?)|(?:[-+].*))?\s*$'
    $m = [regex]::Match($Version, $rx, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $m.Success) { return $null }
    [int64]$major = [int64]$m.Groups[1].Value
    [int64]$minor = 0; if ($m.Groups[2].Success) { $minor = [int64]$m.Groups[2].Value }
    [int64]$patch = 0; if ($m.Groups[3].Success) { $patch = [int64]$m.Groups[3].Value }
    $label = ''; if ($m.Groups[4].Success) { $label = $m.Groups[4].Value.ToLowerInvariant() }
    [int64]$number = 0; if ($m.Groups[5].Success) { $number = [int64]$m.Groups[5].Value }
    if ($label -eq '' -and $major -eq 5) { return [int64]($major * 10000 + $minor * 100 + $patch) }
    [int64]$stage = 99
    if ($label -ne '') {
        [int64]$base = 1
        if ($label -eq 'rc' -or $label -eq 'c') { $base = 50 }
        $stage = $base + [math]::Max(0, $number - 1)
        $cap = 49; if ($base -ne 1) { $cap = 98 }
        $stage = [math]::Min($stage, $cap)
    }
    return [int64]($script:EraFloor + $major * 1000000 + $minor * 10000 + $patch * 100 + $stage)
}

function Get-FridayDataHome {
    if ($env:FRIDAY_HOME) { return $env:FRIDAY_HOME }
    return (Join-Path $env:USERPROFILE '.friday')
}

function Get-ExistingInstall {
    <#  What is installed at $InstallRoot now: @{ Found; Version; Sequence; Source }.
        The version is read off the files on disk (pyproject.toml), exactly as
        the app does; the manifest and the registry are only fallbacks, because
        a manifest records intent. #>
    param([Parameter(Mandatory)][string] $InstallRoot)
    $r = @{ Found = $false; Version = ''; Sequence = $null; Source = '' }
    $pp = Join-Path $InstallRoot 'app\pyproject.toml'
    if (Test-Path -LiteralPath $pp) {
        $m = [regex]::Match((Get-Content -LiteralPath $pp -Raw), '(?m)^version\s*=\s*"([^"]+)"')
        if ($m.Success) { $r.Found = $true; $r.Version = $m.Groups[1].Value; $r.Source = 'files' }
    }
    if (-not $r.Found) {
        $mf = Join-Path $InstallRoot 'install-manifest.json'
        if (Test-Path -LiteralPath $mf) {
            try {
                $j = Get-Content -LiteralPath $mf -Raw | ConvertFrom-Json
                if ($j.PSObject.Properties.Match('version').Count -and $j.version) {
                    $r.Found = $true; $r.Version = [string]$j.version; $r.Source = 'manifest'
                }
            } catch { }
        }
    }
    if (-not $r.Found) {
        foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday')) {
            try {
                $reg = Get-ItemProperty -Path $key -ErrorAction Stop
                if ($reg.DisplayVersion) { $r.Found = $true; $r.Version = [string]$reg.DisplayVersion; $r.Source = 'registry' }
            } catch { }
        }
    }
    if ($r.Found) { $r.Sequence = Get-BuildSequence -Version $r.Version }
    return $r
}

# --- stopping a running Friday -------------------------------------------

function Get-ProcessesRunningFrom {
    <#  Processes whose executable lives under $Root. By path, never by name:
        a Python that belongs to some other program is not Friday's. #>
    param([Parameter(Mandatory)][string] $Root)
    $rootFull = ([System.IO.Path]::GetFullPath($Root)).TrimEnd('\')
    $found = @()
    foreach ($p in @(Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if (-not $p.Path) { continue }
            $full = ([System.IO.Path]::GetFullPath($p.Path)).TrimEnd('\')
            if ($full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) { $found += $p }
        } catch { }
    }
    return $found
}

function Stop-FridayGracefully {
    <#  Ask first, then stop what is left, only ever the processes that run from
        the install folder. Returns the number of processes that had to be
        stopped by force. #>
    param([Parameter(Mandatory)][string] $InstallRoot, [int] $GraceSeconds = 15)
    $running = @(Get-ProcessesRunningFrom -Root $InstallRoot)
    if ($running.Count -eq 0) { Write-Log 'No Friday process is running from the install folder.'; return 0 }
    Write-Log ("Asking {0} process(es) to close: {1}" -f $running.Count, (($running | ForEach-Object { "$($_.ProcessName) $($_.Id)" }) -join ', '))
    foreach ($p in $running) { try { [void]$p.CloseMainWindow() } catch { } }
    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        if (@(Get-ProcessesRunningFrom -Root $InstallRoot).Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
    # The tray restarts a server that dies, so more than one pass: what the
    # first pass stops can bring a process back before the tray itself is gone.
    $stopped = 0
    for ($pass = 1; $pass -le 3; $pass++) {
        $left = @(Get-ProcessesRunningFrom -Root $InstallRoot)
        if ($left.Count -eq 0) { break }
        foreach ($p in $left) {
            Write-Log "Still running after $GraceSeconds s (pass $pass), stopping pid $($p.Id) ($($p.ProcessName))" 'WARN'
            try { Stop-Process -Id $p.Id -Force -ErrorAction Stop; $stopped++ } catch { Write-Log "Could not stop pid $($p.Id): $($_.Exception.Message)" 'WARN' }
        }
        Start-Sleep -Seconds 2
    }
    $remaining = @(Get-ProcessesRunningFrom -Root $InstallRoot)
    if ($remaining.Count -gt 0) {
        throw ("Agent Friday is still running ({0}) and would not close. Close it from the tray icon and run setup again." -f
               (($remaining | ForEach-Object { $_.ProcessName }) -join ', '))
    }
    return $stopped
}

# --- the data home: backup and before/after check ------------------------

#: Download caches, not the person's work. Same idea as the uninstaller's
#: deny-list: anything not named here is backed up.
$script:BackupSkipDirs = @('local_voice', 'nemo', 'audio-cache', 'vibe-code-logs', 'logs', 'cache',
                           'browser-profile', 'models', '.cache', 'tool-output')
$script:BackupMaxFileBytes = 104857600     # 100 MB: logs and caches, not notes
$script:BackupBudgetBytes  = 6GB

#: The files whose bytes must not change: the vault's key material and the
#: person's settings. Relative to the data home.
$script:GuardedFiles = @('vault\.vault_config.json', 'vault\.governance-key',
                         'vault\.attestation-key-ed25519', 'vault\.attestation-pubkey-ed25519',
                         'settings.json', '.setup_complete', 'secret_key', 'user_id')

function Get-DataHomeInventory {
    <#  The files a backup would copy, counted. Skips the deny-listed folders
        and anything over the single-file cap. #>
    param([Parameter(Mandatory)][string] $DataHome)
    $count = 0; $bytes = [int64]0
    if (-not (Test-Path -LiteralPath $DataHome)) { return @{ Files = 0; Bytes = [int64]0 } }
    $stack = New-Object System.Collections.Stack
    $stack.Push($DataHome)
    while ($stack.Count -gt 0) {
        $dir = [string]$stack.Pop()
        try {
            foreach ($d in [System.IO.Directory]::GetDirectories($dir)) {
                $leaf = [System.IO.Path]::GetFileName($d)
                if ($script:BackupSkipDirs -contains $leaf.ToLowerInvariant()) { continue }
                $stack.Push($d)
            }
            foreach ($f in [System.IO.Directory]::GetFiles($dir)) {
                $len = ([System.IO.FileInfo]$f).Length
                if ($len -gt $script:BackupMaxFileBytes) { continue }
                $count++; $bytes += $len
            }
        } catch { }
    }
    return @{ Files = $count; Bytes = $bytes }
}

function Get-DataSnapshot {
    <#  File count of the whole data home, plus the SHA-256 of each guarded
        file that exists. The count walks every folder, including the ones a
        backup skips: "nothing went missing" has to cover them too. #>
    param([Parameter(Mandatory)][string] $DataHome)
    $snap = @{ DataHome = $DataHome; Exists = (Test-Path -LiteralPath $DataHome); Files = 0; Hashes = @{} }
    if (-not $snap.Exists) { return $snap }
    $n = 0
    $stack = New-Object System.Collections.Stack
    $stack.Push($DataHome)
    while ($stack.Count -gt 0) {
        $dir = [string]$stack.Pop()
        try {
            foreach ($d in [System.IO.Directory]::GetDirectories($dir)) { $stack.Push($d) }
            $n += [System.IO.Directory]::GetFiles($dir).Count
        } catch { }
    }
    $snap.Files = $n
    foreach ($rel in $script:GuardedFiles) {
        $p = Join-Path $DataHome $rel
        if (Test-Path -LiteralPath $p -PathType Leaf) {
            try { $snap.Hashes[$rel] = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash } catch { $snap.Hashes[$rel] = 'unreadable' }
        }
    }
    return $snap
}

function Compare-DataSnapshots {
    <#  $Ok is false if a guarded file changed or vanished, or the file count
        fell. The count may rise (logs); it may not drop. #>
    param([Parameter(Mandatory)] $Before, [Parameter(Mandatory)] $After)
    $problems = @()
    if ($Before.Exists -and -not $After.Exists) { $problems += 'The data folder is gone.' }
    foreach ($rel in $Before.Hashes.Keys) {
        if (-not $After.Hashes.ContainsKey($rel)) { $problems += "$rel is missing after the upgrade."; continue }
        if ($After.Hashes[$rel] -ne $Before.Hashes[$rel]) { $problems += "$rel changed during the upgrade." }
    }
    if ($After.Files -lt $Before.Files) {
        $problems += ("The data folder holds {0} file(s) after the upgrade and held {1} before." -f $After.Files, $Before.Files)
    }
    return @{ Ok = ($problems.Count -eq 0); Problems = $problems;
              FilesBefore = $Before.Files; FilesAfter = $After.Files;
              GuardedBefore = $Before.Hashes.Count; GuardedAfter = $After.Hashes.Count }
}

function Backup-FridayData {
    <#  Copy the data home to <BackupRoot>\<stamp>. Returns @{ Path; Files; Bytes; Mode; Skipped }.
        Mode is 'full' (everything not deny-listed), 'essentials' (the vault
        and settings only, because the full set would not fit the budget or the
        disk), or 'none' (nothing to back up). The folder is outside the data
        home so a later cleanup of the data home cannot take its own backup
        with it. #>
    param([Parameter(Mandatory)][string] $DataHome, [Parameter(Mandatory)][string] $BackupRoot,
          [string] $Label = 'upgrade')
    $res = @{ Path = ''; Files = 0; Bytes = [int64]0; Mode = 'none'; Skipped = @() }
    if (-not (Test-Path -LiteralPath $DataHome)) { return $res }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $dest = Join-Path $BackupRoot "$Label-$stamp"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $res.Path = $dest

    $inv = Get-DataHomeInventory -DataHome $DataHome
    $free = [int64]0
    try { $free = (New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($dest)))).AvailableFreeSpace } catch { }
    $full = ($inv.Bytes -le $script:BackupBudgetBytes) -and (($free - $inv.Bytes) -gt 5GB)
    $res.Mode = 'essentials'; if ($full) { $res.Mode = 'full' }
    Write-Log ("Backup: {0} file(s), {1:N0} MB to copy; mode {2}; destination {3}" -f $inv.Files, ($inv.Bytes / 1MB), $res.Mode, $dest)

    $robocopy = Join-Path $env:SystemRoot 'System32\robocopy.exe'
    if ($full) {
        $rcArgs = @($DataHome, $dest, '/E', "/MAX:$($script:BackupMaxFileBytes)", '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD') + $script:BackupSkipDirs
        $null = & $robocopy @rcArgs
        $code = $LASTEXITCODE
        if ($code -ge 8) { throw "The backup of the data folder failed (robocopy exit $code). Nothing has been changed." }
    } else {
        foreach ($rel in $script:GuardedFiles + @('vault')) {
            $src = Join-Path $DataHome $rel
            if (-not (Test-Path -LiteralPath $src)) { continue }
            $to = Join-Path $dest $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
            if (Test-Path -LiteralPath $src -PathType Container) {
                $null = & $robocopy $src $to '/E' "/MAX:$($script:BackupMaxFileBytes)" '/R:1' '/W:1' '/NFL' '/NDL' '/NJH' '/NJS' '/NP'
                if ($LASTEXITCODE -ge 8) { throw "The backup of the vault failed (robocopy exit $LASTEXITCODE). Nothing has been changed." }
            } else {
                Copy-Item -LiteralPath $src -Destination $to -Force
            }
        }
        $res.Skipped = @('Everything outside the vault and settings: the full set would not fit the backup budget or the free disk space.')
    }
    $copied = @(Get-ChildItem -LiteralPath $dest -Recurse -File -Force -ErrorAction SilentlyContinue)
    $res.Files = $copied.Count
    $res.Bytes = [int64]($copied | Measure-Object -Property Length -Sum).Sum
    return $res
}

function Invoke-UpgradePreflight {
    <#  Run before any file is touched. Returns @{ Existing; Backup; Snapshot }.
        Throws if the installed release ranks AFTER this one (a downgrade), or
        if the backup could not be made: an upgrade that cannot protect the
        data does not start. #>
    param([Parameter(Mandatory)][string] $InstallRoot,
          [Parameter(Mandatory)][int64] $NewSequence,
          [string] $Label = 'upgrade')
    $out = @{ Existing = $null; Backup = $null; Snapshot = $null }
    $existing = Get-ExistingInstall -InstallRoot $InstallRoot
    $out.Existing = $existing
    if ($existing.Found) {
        Write-Log ("Existing install: version {0}, sequence {1} (read from {2})" -f $existing.Version, $existing.Sequence, $existing.Source)
        if ($null -ne $existing.Sequence -and $existing.Sequence -gt $NewSequence) {
            throw ("A newer Agent Friday ({0}) is already installed here. This installer is older and will not replace it." -f $existing.Version)
        }
    } else {
        Write-Log 'No earlier install found; this is a fresh install.'
    }

    $dataHome = Get-FridayDataHome
    $out.Snapshot = Get-DataSnapshot -DataHome $dataHome
    if ($existing.Found) {
        $stopped = Stop-FridayGracefully -InstallRoot $InstallRoot
        Write-Log "Friday stopped ($stopped by force)."
        if ($out.Snapshot.Exists) {
            $backupRoot = Join-Path $env:USERPROFILE '.friday-backups'
            if ($env:FRIDAY_BACKUP_ROOT) { $backupRoot = $env:FRIDAY_BACKUP_ROOT }
            $out.Backup = Backup-FridayData -DataHome $dataHome -BackupRoot $backupRoot -Label $Label
            Write-Log ("Backup complete: {0} file(s), {1:N0} MB ({2}) at {3}" -f $out.Backup.Files, ($out.Backup.Bytes / 1MB), $out.Backup.Mode, $out.Backup.Path) 'OK'
        }
    }
    return $out
}

function Test-DataHomeIntact {
    <#  Run after the upgrade. Logs the comparison either way. #>
    param([Parameter(Mandatory)] $Before, [Parameter(Mandatory)][string] $LogDir)
    $after = Get-DataSnapshot -DataHome $Before.DataHome
    $cmp = Compare-DataSnapshots -Before $Before -After $after
    $record = [ordered]@{
        checked_at = (Get-Date).ToString('o'); ok = $cmp.Ok
        files_before = $cmp.FilesBefore; files_after = $cmp.FilesAfter
        guarded_files_before = $cmp.GuardedBefore; guarded_files_after = $cmp.GuardedAfter
        problems = @($cmp.Problems)
    }
    try {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $LogDir 'data-check.json'), ($record | ConvertTo-Json -Depth 4),
                                       (New-Object System.Text.UTF8Encoding($false)))
    } catch { }
    if ($cmp.Ok) {
        Write-Log ("Data home intact: {0} file(s) before, {1} after; {2} guarded file(s) byte-identical." -f $cmp.FilesBefore, $cmp.FilesAfter, $cmp.GuardedAfter) 'OK'
    } else {
        foreach ($p in $cmp.Problems) { Write-Log "DATA CHECK: $p" 'FAIL' }
    }
    return $cmp
}
