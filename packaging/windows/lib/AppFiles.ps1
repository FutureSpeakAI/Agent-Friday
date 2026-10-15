<#
    Agent Friday installer - the update guard.

    An upgrade deletes the install folder and copies the new release over it
    (install.ps1, step app.copy). Anything a person or a helper changed in
    there was lost without a word. This file:

      * Write-AppFilesManifest   at BUILD time, writes app-files.sha256 into the
                                 payload: one "<sha256>  <path>" line per
                                 shipped file, '/'-separated, LF, no BOM;
      * Get-ChangedAppFiles      at INSTALL time, compares the installed folder
                                 against the manifest it shipped with (or, for a
                                 release that had none, against the time
                                 install-manifest.json was written);
      * Backup-ChangedAppFiles   copies those files to
                                 ~\.friday\overrides\replaced-<version>\ with a
                                 REPLACED.txt list, BEFORE anything is deleted.
                                 Any file it cannot copy stops the update before
                                 anything is deleted, with a message that names
                                 each file, why, and how to go on.
      * Remove-AppReparsePoints  unlinks junctions and symlinks inside the app
                                 folder (never their targets) before the folder
                                 is deleted.

    The Python half is src\agent_friday\services\update_guard.py (the in-app
    update check). The two apply the same rules; change them together.
    tests\unit\test_user_overrides.py checks that the exclusion lists below
    and update_guard.py's are identical.

    WHAT IS NEVER COMPARED (the exclusion list). A clean install followed at
    once by an upgrade must report 0 changed files, so everything that lands
    in the app folder AFTER the payload copy, and is not the person's work,
    is excluded. All names compare case-insensitively (Windows does).

      AppFilesSkipDirs          any folder with this name, anywhere:
                                __pycache__ (the interpreter writes bytecode
                                the first time each module is imported: the
                                installer's own `agent_friday.cli models` and
                                `setup` runs, then every start); tool caches;
                                VCS and virtual-environment folders, which a
                                release never ships.
      AppFilesSkipDirSuffixes   any folder ending so: pip metadata
                                (.egg-info, .dist-info).
      AppFilesSkipSuffixes      any file ending so: bytecode (.pyc, .pyo).
      AppFilesSkipNames         any file with this name, anywhere: what Windows
                                Explorer drops into a folder somebody browses
                                (Thumbs.db, desktop.ini) and its macOS
                                counterpart (.DS_Store).
      AppFilesSkipPrefixes      any path starting so ('/'-separated):
                                tests/conformance/results/, where
                                services/model_seat_gate.save_evidence writes
                                (the payload ships no tests/ folder at all).
      AppFilesUserFiles         these names at the TOP of the app folder only:
                                install.ps1's $script:AppUserFiles, which the
                                setup wizard writes after the copy and the
                                installer itself preserves across it. They
                                hold the vault passphrase and keys in plain
                                text, so they are never listed or copied.
      app-files.sha256          the manifest itself.

    Logs, the launchers (*.cmd), the embedded Python and install-manifest.json
    are written to the install ROOT, beside app\, never inside it, and
    friday.log, settings and every other runtime file live under ~\.friday.

    Junctions and symlinks (reparse points) are never followed: a loop would
    never end, and a link's target is not part of the install. They are
    reported in Skipped and unlinked by Remove-AppReparsePoints.

    No Write-Log here: the Inno page's probe dot-sources this file on its own,
    without Common.ps1. Callers log what comes back.
#>

$script:AppFilesManifestName    = 'app-files.sha256'
$script:AppFilesInstallManifest = 'install-manifest.json'
$script:AppFilesSkipDirs        = @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules', '.git', 'venv', '.venv')
$script:AppFilesSkipDirSuffixes = @('.egg-info', '.dist-info')
$script:AppFilesSkipSuffixes    = @('.pyc', '.pyo')
$script:AppFilesSkipNames       = @('thumbs.db', 'desktop.ini', '.ds_store')
$script:AppFilesSkipPrefixes    = @('tests/conformance/results/')
# install.ps1's $script:AppUserFiles: kept across the copy by the installer
# itself, and they hold the vault passphrase and keys in plain text, so they
# are never listed or copied by the guard.
$script:AppFilesUserFiles       = @('start.bat', 'launch_now.bat', 'friday_startup.bat', 'friday_startup.vbs',
                                    '.env', 'secrets.yaml', 'config.yaml')
$script:AppFilesInstallSlackSeconds = 120

function Get-FridayOverridesDir {
    if ($env:FRIDAY_OVERRIDES_DIR) { return $env:FRIDAY_OVERRIDES_DIR }
    $home_ = $env:FRIDAY_HOME
    if (-not $home_) { $home_ = Join-Path $env:USERPROFILE '.friday' }
    return (Join-Path $home_ 'overrides')
}

function Test-AppFilesSkippedDir {
    param([string] $Name)
    $n = $Name.ToLowerInvariant()
    if ($script:AppFilesSkipDirs -contains $n) { return $true }
    foreach ($s in $script:AppFilesSkipDirSuffixes) { if ($n.EndsWith($s)) { return $true } }
    return $false
}

function Test-AppFilesSkippedFile {
    <#  $Rel is '/'-relative to the app folder. #>
    param([string] $Rel)
    $r = $Rel.ToLowerInvariant()
    $name = $r
    $i = $r.LastIndexOf('/')
    if ($i -ge 0) { $name = $r.Substring($i + 1) }
    foreach ($s in $script:AppFilesSkipSuffixes) { if ($name.EndsWith($s)) { return $true } }
    if ($script:AppFilesSkipNames -contains $name) { return $true }
    foreach ($p in $script:AppFilesSkipPrefixes) { if ($r.StartsWith($p)) { return $true } }
    if ($r -eq $script:AppFilesManifestName) { return $true }
    if (($i -lt 0) -and ($script:AppFilesUserFiles -contains $name)) { return $true }
    return $false
}

function Test-AppFilesReparsePoint {
    param([string] $Path)
    try {
        $a = [System.IO.File]::GetAttributes($Path)
        return (($a -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    } catch { return $false }
}

function Get-AppFileList {
    <#  Every file under $Root the guard cares about, as '/'-relative paths.
        Never follows a junction or symlink. -Skipped (a List[string]) is
        given "reparse|<rel>" for each link passed over and "unlisted|<rel>|
        <why>" for each folder that could not be read. #>
    param([Parameter(Mandatory)][string] $Root,
          $Skipped = $null)
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $out = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) { return @() }
    $stack = New-Object System.Collections.Stack
    $stack.Push($rootFull)
    while ($stack.Count -gt 0) {
        $dir = [string]$stack.Pop()
        try {
            $subdirs = [System.IO.Directory]::GetDirectories($dir)
            $files = [System.IO.Directory]::GetFiles($dir)
        } catch {
            if ($null -ne $Skipped) {
                $relDir = ''
                if ($dir.Length -gt $rootFull.Length) { $relDir = $dir.Substring($rootFull.Length + 1).Replace('\', '/') }
                $Skipped.Add("unlisted|$relDir|$($_.Exception.Message)")
            }
            continue
        }
        foreach ($d in $subdirs) {
            if (Test-AppFilesSkippedDir ([System.IO.Path]::GetFileName($d))) { continue }
            if (Test-AppFilesReparsePoint $d) {
                if ($null -ne $Skipped) { $Skipped.Add('reparse|' + $d.Substring($rootFull.Length + 1).Replace('\', '/')) }
                continue
            }
            $stack.Push($d)
        }
        foreach ($f in $files) {
            $rel = $f.Substring($rootFull.Length + 1).Replace('\', '/')
            if (Test-AppFilesSkippedFile $rel) { continue }
            if (Test-AppFilesReparsePoint $f) {
                if ($null -ne $Skipped) { $Skipped.Add("reparse|$rel") }
                continue
            }
            $out.Add($rel)
        }
    }
    $arr = $out.ToArray()
    [System.Array]::Sort($arr, [System.StringComparer]::Ordinal)
    return $arr
}

function Get-AppFileHash {
    <#  Lower-case SHA-256 of one file, read with shared access. '' when unreadable. #>
    param([Parameter(Mandatory)][string] $Path)
    $fs = $null
    try {
        $fs = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Open,
                  [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { return ([System.BitConverter]::ToString($sha.ComputeHash($fs))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
    } catch { return '' }
    finally { if ($fs) { $fs.Dispose() } }
}

function Write-AppFilesManifest {
    <#  BUILD TIME. Writes <Root>\app-files.sha256 and returns the file count. #>
    param([Parameter(Mandatory)][string] $Root)
    $lines = New-Object System.Text.StringBuilder
    $n = 0
    foreach ($rel in (Get-AppFileList -Root $Root)) {
        $h = Get-AppFileHash -Path (Join-Path $Root ($rel.Replace('/', '\')))
        if (-not $h) { throw "Could not hash $rel for the shipped-files manifest." }
        [void]$lines.Append("$h  $rel`n")
        $n++
    }
    [System.IO.File]::WriteAllText((Join-Path $Root $script:AppFilesManifestName), $lines.ToString(),
                                   (New-Object System.Text.UTF8Encoding($false)))
    return $n
}

function Read-AppFilesManifest {
    <#  Hashtable rel -> sha256 (ordinal keys), or $null when the install has none. #>
    param([Parameter(Mandatory)][string] $AppDir)
    $p = Join-Path $AppDir $script:AppFilesManifestName
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return $null }
    $map = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
    foreach ($line in [System.IO.File]::ReadAllLines($p)) {
        if (-not $line.Trim() -or $line.TrimStart().StartsWith('#')) { continue }
        $m = [regex]::Match($line, '^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$')
        if ($m.Success) { $map[$m.Groups[2].Value.Replace('\', '/')] = $m.Groups[1].Value.ToLowerInvariant() }
    }
    return $map
}

function Get-ChangedAppFiles {
    <#  @{ Basis = 'manifest'|'install-time'|'none'; Modified; Added; Total;
           Reparse; Unlisted }.
        Modified: shipped files whose bytes differ (or, with no manifest, files
        written after the install finished). Added: files the release did not
        ship. A deleted shipped file is not listed: the update puts it back.
        Reparse: junctions/symlinks passed over (never followed, never copied).
        Unlisted: "<rel>|<why>" for folders that could not be read; their
        contents are unknown, so Backup-ChangedAppFiles stops on them. #>
    param([Parameter(Mandatory)][string] $AppDir)
    $res = @{ Basis = 'none'; Modified = @(); Added = @(); Total = 0; Reparse = @(); Unlisted = @() }
    if (-not (Test-Path -LiteralPath $AppDir -PathType Container)) { return $res }
    $manifest = Read-AppFilesManifest -AppDir $AppDir
    $mod = New-Object System.Collections.Generic.List[string]
    $add = New-Object System.Collections.Generic.List[string]
    $skipped = New-Object System.Collections.Generic.List[string]
    if ($null -ne $manifest) {
        $res.Basis = 'manifest'
        foreach ($rel in (Get-AppFileList -Root $AppDir -Skipped $skipped)) {
            if (-not $manifest.ContainsKey($rel)) { $add.Add($rel); continue }
            $h = Get-AppFileHash -Path (Join-Path $AppDir ($rel.Replace('/', '\')))
            if ($h -ne $manifest[$rel]) { $mod.Add($rel) }
        }
    } else {
        $marker = Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($AppDir).TrimEnd('\'))) $script:AppFilesInstallManifest
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { return $res }
        $res.Basis = 'install-time'
        $cutoff = (Get-Item -LiteralPath $marker).LastWriteTimeUtc.AddSeconds($script:AppFilesInstallSlackSeconds)
        foreach ($rel in (Get-AppFileList -Root $AppDir -Skipped $skipped)) {
            try {
                $t = [System.IO.File]::GetLastWriteTimeUtc((Join-Path $AppDir ($rel.Replace('/', '\'))))
                if ($t -gt $cutoff) { $mod.Add($rel) }
            } catch { $mod.Add($rel) }
        }
    }
    $res.Modified = $mod.ToArray()
    $res.Added = $add.ToArray()
    $res.Total = $mod.Count + $add.Count
    $res.Reparse = @($skipped | Where-Object { $_.StartsWith('reparse|') } | ForEach-Object { $_.Substring(8) })
    $res.Unlisted = @($skipped | Where-Object { $_.StartsWith('unlisted|') } | ForEach-Object { $_.Substring(9) })
    return $res
}

# --- Long paths ------------------------------------------------------------
#
# A backup lands under ~\.friday\overrides\replaced-<version>\<path inside the
# app folder>, which can pass 260 characters even when the source did not.
# Windows PowerShell 5.1 runs on .NET Framework with legacy path handling, so
# Copy-Item and System.IO refuse such paths. The Win32 calls take the \\?\
# form at any length, so the copy goes through them (no robocopy). If they
# cannot be loaded, Copy-Item is used, and a long path then fails like any
# other copy failure: listed, and the update stops before deleting anything.

$script:AppFilesLongPathReady = $null

function Initialize-AppFilesLongPath {
    if ($null -ne $script:AppFilesLongPathReady) { return $script:AppFilesLongPathReady }
    try {
        if (-not ('FridayAppFiles.LongPath' -as [type])) {
            Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace FridayAppFiles {
    public static class LongPath {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool CopyFileW(string src, string dst, bool failIfExists);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool CreateDirectoryW(string path, IntPtr security);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFileAttributesW(string path);

        public static string Prefix(string p) {
            if (p.StartsWith(@"\\?\")) return p;
            if (p.StartsWith(@"\\")) return @"\\?\UNC\" + p.Substring(2);
            return @"\\?\" + p;
        }
        public static bool Exists(string p) {
            return GetFileAttributesW(Prefix(p)) != 0xFFFFFFFF;
        }
        public static void CreateDirectories(string dir) {
            string p = dir.TrimEnd('\\');
            Stack<string> todo = new Stack<string>();
            while (p.Length > 3 && !Exists(p)) {
                todo.Push(p);
                int i = p.LastIndexOf('\\');
                if (i < 3) break;
                p = p.Substring(0, i);
            }
            while (todo.Count > 0) {
                string d = todo.Pop();
                if (!CreateDirectoryW(Prefix(d), IntPtr.Zero)) {
                    int e = Marshal.GetLastWin32Error();
                    if (e != 183) throw new Win32Exception(e);   // 183: already exists
                }
            }
        }
        public static void Copy(string src, string dst) {
            if (!CopyFileW(Prefix(src), Prefix(dst), false))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }
}
'@
        }
        $script:AppFilesLongPathReady = $true
    } catch {
        $script:AppFilesLongPathReady = $false
    }
    return $script:AppFilesLongPathReady
}

function Copy-AppFileLong {
    <#  Copy one file, creating its folder, at any path length. Throws with the
        Windows reason ("There is not enough space on the disk.", ...). Both
        paths must be full paths. #>
    param([Parameter(Mandatory)][string] $Source,
          [Parameter(Mandatory)][string] $Destination)
    $parent = $Destination.Substring(0, $Destination.LastIndexOf('\'))
    if (Initialize-AppFilesLongPath) {
        [FridayAppFiles.LongPath]::CreateDirectories($parent)
        [FridayAppFiles.LongPath]::Copy($Source, $Destination)
    } else {
        New-Item -ItemType Directory -Force -Path $parent -ErrorAction Stop | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination -Force -ErrorAction Stop
    }
}

function Get-AppFilesBackupFailureText {
    <#  The message for a backup that could not copy every file. Names each
        file and why, where the copies were going, and both ways to go on. #>
    param([Parameter(Mandatory)] $Failed,
          [string] $Dest = '',
          [string] $AppDir = '',
          [string] $Version = '')
    $n = @($Failed).Count
    $where = '~\.friday\overrides\replaced-' + $(if ($Version) { $Version } else { '<version>' })
    if ($Dest) { $where = "$where ($Dest)" }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("Setup stopped before changing anything. $n file(s) you or a helper changed in the installed copy of Friday could not be backed up to $where, and replacing them now would lose them:`r`n")
    foreach ($f in @($Failed)) { [void]$sb.Append("    $($f.Rel)  ($($f.Why))`r`n") }
    [void]$sb.Append("Nothing in $AppDir has been deleted or replaced. To go on, either:`r`n")
    [void]$sb.Append("    1. close anything that may have these files open (Agent Friday itself, an editor, a backup or virus scan), free some space if the drive is full, and run setup again; or`r`n")
    [void]$sb.Append("    2. copy those files somewhere yourself, delete them from $AppDir, and run setup again (a file that is no longer there is not backed up or replaced).")
    return $sb.ToString()
}

function Backup-ChangedAppFiles {
    <#  Copy each changed/added file to <Overrides>\replaced-<Version>\<path>
        and write REPLACED.txt. Never overwrites an earlier backup.

        FAIL-CLOSED. Every file is attempted; if ANY could not be copied (or a
        folder could not be listed, so its contents are unknown), this throws
        Get-AppFilesBackupFailureText's message, which lists them. Nothing has
        been deleted yet, so stopping is safe; replacing would lose the only
        copy. Returns @{ Dest; Files; Failed; Basis } on success. #>
    param([Parameter(Mandatory)][string] $AppDir,
          [string] $Version = '',
          [string] $OverridesDir = '',
          $Found = $null)
    if ($null -eq $Found) { $Found = Get-ChangedAppFiles -AppDir $AppDir }
    $out = @{ Dest = ''; Files = @(); Failed = @(); Basis = $Found.Basis }
    $unlisted = @()
    if ($Found.ContainsKey('Unlisted')) { $unlisted = @($Found.Unlisted) }
    if ($Found.Total -eq 0 -and $unlisted.Count -eq 0) { return $out }
    if (-not $OverridesDir) { $OverridesDir = Get-FridayOverridesDir }
    $label = ($Version -replace '[^A-Za-z0-9._-]+', '-').Trim('-', '.')
    if (-not $label) { $label = 'unknown-version' }
    $base = [System.IO.Path]::GetFullPath((Join-Path $OverridesDir ("replaced-" + $label)))
    $dest = $base; $n = 1
    while (Test-Path -LiteralPath $dest) { $n++; $dest = "$base-$n" }
    $appFull = [System.IO.Path]::GetFullPath($AppDir).TrimEnd('\')

    $list = New-Object System.Collections.Generic.List[string]
    $list.Add("Files an update of Agent Friday $Version was about to replace, copied here first.")
    $list.Add("Compared by: $($Found.Basis). Original folder: $AppDir")
    $list.Add('')
    $copied = New-Object System.Collections.Generic.List[string]
    $failed = New-Object System.Collections.Generic.List[object]
    foreach ($u in $unlisted) {
        $parts = @($u -split '\|', 2)
        $why = 'the folder could not be read'
        if ($parts.Count -gt 1) { $why = "the folder could not be read: $($parts[1])" }
        $failed.Add(@{ Rel = ($parts[0] + '/'); Why = $why })
    }
    $destReady = $true
    try {
        if (Initialize-AppFilesLongPath) { [FridayAppFiles.LongPath]::CreateDirectories($dest) }
        else { New-Item -ItemType Directory -Force -Path $dest -ErrorAction Stop | Out-Null }
    } catch {
        $destReady = $false
        $why = "the backup folder could not be created: $($_.Exception.Message)"
        foreach ($r in (@($Found.Modified) + @($Found.Added))) { $failed.Add(@{ Rel = $r; Why = $why }) }
    }
    if ($destReady) {
        $items = @()
        foreach ($r in $Found.Modified) { $items += ,@('modified', $r) }
        foreach ($r in $Found.Added) { $items += ,@('added', $r) }
        foreach ($it in $items) {
            $kind = $it[0]; $rel = $it[1]
            $src = $appFull + '\' + $rel.Replace('/', '\')
            $to = $dest + '\' + $rel.Replace('/', '\')
            try {
                Copy-AppFileLong -Source $src -Destination $to
                $copied.Add($rel)
                $list.Add("$kind  $rel")
            } catch {
                $msg = $_.Exception.Message
                if ($_.Exception.InnerException) { $msg = $_.Exception.InnerException.Message }
                $failed.Add(@{ Rel = $rel; Why = $msg })
                $list.Add("$kind  $rel  (NOT COPIED: $msg)")
            }
        }
        try {
            [System.IO.File]::WriteAllText((Join-Path $dest 'REPLACED.txt'), (($list.ToArray() -join "`r`n") + "`r`n"),
                                           (New-Object System.Text.UTF8Encoding($false)))
        } catch {
            if ($failed.Count -eq 0) {
                $failed.Add(@{ Rel = 'REPLACED.txt'; Why = "the list of backed-up files could not be written: $($_.Exception.Message)" })
            }
        }
    }
    $out.Dest = $dest
    $out.Files = $copied.ToArray()
    $out.Failed = $failed.ToArray()
    if ($failed.Count -gt 0) {
        throw (Get-AppFilesBackupFailureText -Failed $out.Failed -Dest $dest -AppDir $AppDir -Version $label)
    }
    $readme = Join-Path $OverridesDir 'README.md'
    if (-not (Test-Path -LiteralPath $readme)) {
        # Friday writes the full README on first start; until then, say what this is.
        try {
            [System.IO.File]::WriteAllText($readme, ("# Your overrides`r`n`r`nThis folder belongs to you; updates never touch it. " +
                "replaced-<version> folders hold installed files an update was about to replace because they had been changed. " +
                "Friday writes the full guide here the next time it starts.`r`n"), (New-Object System.Text.UTF8Encoding($false)))
        } catch { }
    }
    return $out
}

function Remove-AppReparsePoints {
    <#  Unlink every junction/symlink Get-ChangedAppFiles passed over, BEFORE
        the app folder is deleted. Removes the LINK only, never what it points
        at: a recursive delete in Windows PowerShell 5.1 can walk into a
        junction and delete the target's contents. Returns the links removed. #>
    param([Parameter(Mandatory)][string] $AppDir,
          $Found = $null)
    if ($null -eq $Found) { $Found = Get-ChangedAppFiles -AppDir $AppDir }
    $removed = New-Object System.Collections.Generic.List[string]
    if (-not $Found.ContainsKey('Reparse')) { return @() }
    $appFull = [System.IO.Path]::GetFullPath($AppDir).TrimEnd('\')
    foreach ($rel in @($Found.Reparse)) {
        $p = $appFull + '\' + $rel.Replace('/', '\')
        try {
            if ([System.IO.Directory]::Exists($p)) { [System.IO.Directory]::Delete($p, $false) }
            else { [System.IO.File]::Delete($p) }
            $removed.Add($rel)
        } catch { }
    }
    return $removed.ToArray()
}
