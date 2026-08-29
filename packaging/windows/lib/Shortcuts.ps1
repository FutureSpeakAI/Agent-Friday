#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Shortcuts.ps1

    Shortcuts, autostart, and the Add/Remove Programs entry.

    EVERYTHING HERE IS PER-USER. No admin rights, no HKLM, no Program Files,
    no machine-wide anything. Two reasons: she should not have to answer a UAC
    prompt to install a chat program, and a per-user install is one that a
    per-user uninstall can actually finish. An installer that needs
    administrator to remove itself is not removable in practice.

    Every artefact created here is recorded in install-manifest.json so the
    uninstaller removes exactly what was created and nothing else. It does not
    guess by name, and it does not delete anything it did not write.
#>

Set-StrictMode -Version 2.0

$script:StartMenuFolderName = 'Agent Friday'
$script:UninstallRegKey     = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday'
$script:AutostartRegRun     = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$script:AutostartValueName  = 'AgentFriday'

function Get-SpecialDir {
    <#  A known folder, or '' - never a throw, and never a silent wrong answer.

        [Environment]::GetFolderPath() returns an EMPTY STRING when the folder
        it names does not physically exist, and Join-Path refuses an empty
        -Path with a ParameterBindingValidationException. Because
        Get-StartMenuDir was evaluated at the top of Install-Shortcuts, one
        empty lookup threw before the first shortcut was attempted and took all
        four down together - including the desktop icon, which is the one thing
        the closing screen tells her to double-click.

        Found on 2026-08-25, on the first cold install anyone had ever run.
        The trigger there was a redirected %APPDATA% in a test harness, which
        is not something a normal profile will have. A profile mid-provision, a roaming
        profile, or a OneDrive Known Folder Move that has not finished are, and
        they produce exactly the same empty string. #>
    param([Parameter(Mandatory)][string] $Name, [string] $Fallback = '')
    $d = ''
    try { $d = [Environment]::GetFolderPath($Name) } catch { $d = '' }
    if ($d) { return $d }
    if ($Fallback) {
        try {
            if (-not (Test-Path -LiteralPath $Fallback)) {
                New-Item -ItemType Directory -Force -Path $Fallback -ErrorAction Stop | Out-Null
            }
            Write-Log "GetFolderPath('$Name') was empty; using $Fallback" 'WARN'
            return $Fallback
        } catch {
            Write-Log "GetFolderPath('$Name') was empty and $Fallback is unusable: $($_.Exception.Message)" 'WARN'
        }
    }
    Write-Log "GetFolderPath('$Name') was empty and no fallback worked." 'WARN'
    return ''
}

function Get-DesktopDir {
    $fb = ''
    if ($env:USERPROFILE) { $fb = Join-Path $env:USERPROFILE 'Desktop' }
    return (Get-SpecialDir -Name 'Desktop' -Fallback $fb)
}

function Get-StartMenuDir {
    $fb = ''
    if ($env:APPDATA) { $fb = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs' }
    $programs = Get-SpecialDir -Name 'Programs' -Fallback $fb
    if (-not $programs) { return '' }
    return (Join-Path $programs $script:StartMenuFolderName)
}

function New-Shortcut {
    <# Returns the .lnk path on success, $null on failure. Callers verify. #>
    param(
        [Parameter(Mandatory)][string] $LinkPath,
        [Parameter(Mandatory)][string] $TargetPath,
        [string] $Arguments        = '',
        [string] $WorkingDirectory = '',
        [string] $Description      = '',
        [string] $IconLocation     = ''
    )
    try {
        $dir = Split-Path -Parent $LinkPath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($LinkPath)
        $sc.TargetPath = $TargetPath
        if ($Arguments)        { $sc.Arguments        = $Arguments }
        if ($WorkingDirectory) { $sc.WorkingDirectory = $WorkingDirectory }
        if ($Description)      { $sc.Description      = $Description }
        if ($IconLocation)     { $sc.IconLocation     = $IconLocation }
        $sc.Save()
        [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null

        if (Test-Path -LiteralPath $LinkPath) {
            Write-Log "Shortcut created: $LinkPath -> $TargetPath $Arguments" 'OK'
            return $LinkPath
        }
        Write-Log "CreateShortcut reported no error but $LinkPath does not exist." 'FAIL'
        return $null
    } catch {
        Write-Log "Could not create shortcut $LinkPath : $($_.Exception.Message)" 'FAIL'
        return $null
    }
}

function Install-Shortcuts {
    <#  Desktop + Start Menu. Returns the list of paths created, for the
        manifest. The desktop shortcut is the one she will actually use; the
        Start Menu folder is where a cautious person goes looking for an
        uninstaller, which is why the uninstaller lives there too.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [string] $IconPath = ''
    )
    $created = @()

    $launcher  = Join-Path $InstallRoot 'Agent Friday.cmd'
    $uninst    = Join-Path $InstallRoot 'Uninstall Agent Friday.cmd'
    $autotog   = Join-Path $InstallRoot 'Start Friday when I sign in.cmd'

    # Each destination is resolved and checked on its own. A folder we cannot
    # find costs the shortcuts that live in it and nothing else - it used to
    # cost all four, because one Join-Path on an empty string threw before the
    # first one was attempted.
    $desktop   = Get-DesktopDir
    $startMenu = Get-StartMenuDir

    if ($desktop) {
        $p = New-Shortcut -LinkPath (Join-Path $desktop 'Agent Friday.lnk') `
                          -TargetPath $launcher -WorkingDirectory $InstallRoot `
                          -Description 'Start Agent Friday' -IconLocation $IconPath
        if ($p) { $created += $p }
    } else {
        Write-Log 'No usable Desktop folder; the desktop shortcut was not created.' 'WARN'
    }

    if ($startMenu) {
        $p = New-Shortcut -LinkPath (Join-Path $startMenu 'Agent Friday.lnk') `
                          -TargetPath $launcher -WorkingDirectory $InstallRoot `
                          -Description 'Start Agent Friday' -IconLocation $IconPath
        if ($p) { $created += $p }

        $p = New-Shortcut -LinkPath (Join-Path $startMenu 'Uninstall Agent Friday.lnk') `
                          -TargetPath $uninst -WorkingDirectory $InstallRoot `
                          -Description 'Remove Agent Friday from this computer'
        if ($p) { $created += $p }

        $p = New-Shortcut -LinkPath (Join-Path $startMenu 'Start Friday when I sign in.lnk') `
                          -TargetPath $autotog -WorkingDirectory $InstallRoot `
                          -Description 'Turn the automatic start on or off'
        if ($p) { $created += $p }
    } else {
        Write-Log 'No usable Start Menu folder; those shortcuts were not created.' 'WARN'
    }

    return $created
}

function Test-ShortcutsInstalled {
    param([Parameter(Mandatory)][string] $InstallRoot)
    $desktop = Join-Path (Get-DesktopDir) 'Agent Friday.lnk'
    if (-not (Test-Path -LiteralPath $desktop)) { return $false }

    # A .lnk whose target has gone is worse than no .lnk - it is a broken
    # promise on her desktop. Resolve it and check the target exists.
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($desktop)
        $target = $sc.TargetPath
        [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
        if (-not $target -or -not (Test-Path -LiteralPath $target)) {
            Write-Log "Desktop shortcut exists but its target '$target' does not." 'FAIL'
            return $false
        }
    } catch {
        return $false
    }
    return (Test-Path -LiteralPath (Join-Path (Get-StartMenuDir) 'Uninstall Agent Friday.lnk'))
}

# --- Autostart -----------------------------------------------------------

function Get-StartupDir {
    <#  The sign-in Startup folder, or '' - never a throw.

        The three autostart functions called [Environment]::GetFolderPath
        ('Startup') directly and fed the result straight to Join-Path, which is
        the exact ParameterBindingValidationException Get-SpecialDir was written
        to stop. They escaped it because they were only ever reached inside the
        "she answered Yes" branch, which no test harness had taken - so the one
        code path still holding the raw call was also the one nothing exercised.

        5.6.6 made Test-Autostart run unconditionally, to record the MEASURED
        autostart state in the manifest, and it threw on the first install that
        ran afterwards. The manifest was never written at all, which would have
        left the uninstaller with nothing to read - the same class of failure
        that release exists to fix. #>
    $fb = ''
    if ($env:APPDATA) {
        $fb = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    }
    return (Get-SpecialDir -Name 'Startup' -Fallback $fb)
}

function Enable-Autostart {
    <#  Startup-folder shortcut rather than a Run registry value.

        Both work. The shortcut is chosen because it is the one a normal
        person can find and delete: Win+R, shell:startup, there it is. A
        registry value under CurrentVersion\Run is invisible to her and looks,
        to anyone who later goes looking, exactly like the thing malware does.
        For a program that asks for a Claude key and can read her calendar,
        being visibly removable matters more than being tidy.

        Points at the tray launcher, not the console launcher, so signing in
        does not throw a black window at her.
    #>
    param([Parameter(Mandatory)][string] $InstallRoot, [string] $IconPath = '')
    $startupDir = Get-StartupDir
    if (-not $startupDir) {
        Write-Log 'No usable Startup folder; autostart was not enabled.' 'WARN'
        return ''
    }
    $link = Join-Path $startupDir 'Agent Friday.lnk'
    $target = Join-Path $InstallRoot 'Agent Friday (background).cmd'
    return (New-Shortcut -LinkPath $link -TargetPath $target -WorkingDirectory $InstallRoot `
                         -Description 'Start Agent Friday quietly when you sign in' -IconLocation $IconPath)
}

function Disable-Autostart {
    $startupDir = Get-StartupDir
    if ($startupDir) {
        $link = Join-Path $startupDir 'Agent Friday.lnk'
        if (Test-Path -LiteralPath $link) {
            Remove-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
            Write-Log "Autostart shortcut removed: $link" 'OK'
        }
    }
    # Belt and braces: if an older build ever wrote a Run value, clear it too.
    try {
        $existing = Get-ItemProperty -Path $script:AutostartRegRun -Name $script:AutostartValueName -ErrorAction SilentlyContinue
        if ($existing) {
            Remove-ItemProperty -Path $script:AutostartRegRun -Name $script:AutostartValueName -Force -ErrorAction SilentlyContinue
            Write-Log "Removed legacy autostart registry value." 'OK'
        }
    } catch { }
    return (-not (Test-Autostart))
}

function Test-Autostart {
    $startupDir = Get-StartupDir
    if (-not $startupDir) { return $false }
    return (Test-Path -LiteralPath (Join-Path $startupDir 'Agent Friday.lnk'))
}

function Get-InstalledShortcutPaths {
    <#  Every shortcut this installer knows how to create, that EXISTS now.

        The manifest used to record only what the current run created, and
        Invoke-Step skips a step whose verify already passes - so on an upgrade
        Install-Shortcuts did not run, the list came out empty, and the manifest
        told the uninstaller there were no shortcuts to remove. It then left
        four of them on the machine after an uninstall that reported success.

        Measuring instead of remembering makes the manifest a record of what is
        true rather than of what this particular run happened to do. #>
    $found = @()
    $desktop   = Get-DesktopDir
    $startMenu = Get-StartMenuDir
    $startup   = Get-StartupDir

    if ($desktop)   { $found += (Join-Path $desktop 'Agent Friday.lnk') }
    if ($startMenu) {
        foreach ($n in @('Agent Friday.lnk','Uninstall Agent Friday.lnk',
                         'Start Friday when I sign in.lnk')) {
            $found += (Join-Path $startMenu $n)
        }
    }
    if ($startup)   { $found += (Join-Path $startup 'Agent Friday.lnk') }

    return @($found | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
}

# --- Add / Remove Programs ----------------------------------------------

function Register-Uninstaller {
    <#  Put Friday in Settings > Apps > Installed apps.

        This is not cosmetic. Stephen's brief says a cautious person will not
        install something she cannot remove, and the place a cautious person
        looks is Add/Remove Programs. If she cannot find Friday there, the
        uninstaller might as well not exist. HKCU, so no admin needed.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [Parameter(Mandatory)][string] $Version,
        [string] $IconPath = ''
    )
    try {
        if (-not (Test-Path $script:UninstallRegKey)) {
            New-Item -Path $script:UninstallRegKey -Force | Out-Null
        }
        $uninstCmd = '"' + (Join-Path $InstallRoot 'Uninstall Agent Friday.cmd') + '"'
        $sizeKb = 0
        try {
            $sizeKb = [int]((Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -ErrorAction SilentlyContinue |
                              Measure-Object -Property Length -Sum).Sum / 1KB)
        } catch { }

        $props = @{
            DisplayName     = 'Agent Friday'
            DisplayVersion  = $Version
            Publisher       = 'FutureSpeak.AI'
            InstallLocation = $InstallRoot
            UninstallString = $uninstCmd
            NoModify        = 1
            NoRepair        = 1
            EstimatedSize   = $sizeKb
            InstallDate     = (Get-Date -Format 'yyyyMMdd')
        }
        if ($IconPath) { $props['DisplayIcon'] = $IconPath }

        foreach ($k in $props.Keys) {
            $type = 'String'
            if ($props[$k] -is [int]) { $type = 'DWord' }
            New-ItemProperty -Path $script:UninstallRegKey -Name $k -Value $props[$k] -PropertyType $type -Force | Out-Null
        }
        Write-Log "Registered in Add/Remove Programs at $($script:UninstallRegKey)" 'OK'
        return $true
    } catch {
        Write-Log "Could not register uninstaller: $($_.Exception.Message)" 'FAIL'
        return $false
    }
}

function Test-UninstallerRegistered {
    <#  .PARAMETER ExpectedVersion
          When given, the registered DisplayVersion must equal it.

          Register-Uninstaller writes DisplayVersion, and until 5.6.6 this
          check never read it back. Invoke-Step runs Verify BEFORE the action
          and skips the action when it passes, so on every upgrade this
          returned $true from the PREVIOUS install's entry, Register-Uninstaller
          never ran, and Add/Remove Programs went on displaying the old version
          for ever. Same defect as app.copy's, one surface over - and this is
          the surface a user checks to find out what they are running.

          The uninstaller calls this with no argument, on purpose: it is asking
          "is there an entry at all", and any version answers that. #>
    param([string] $ExpectedVersion = '')
    try {
        $v = Get-ItemProperty -Path $script:UninstallRegKey -ErrorAction Stop
        if (-not $v.DisplayName) { return $false }
        if (-not $v.UninstallString) { return $false }
        if ($ExpectedVersion) {
            $have = ''
            if ($v.PSObject.Properties.Match('DisplayVersion').Count -gt 0) {
                $have = [string]$v.DisplayVersion
            }
            if ($have -ne $ExpectedVersion) {
                Set-VerifyDetail "Add/Remove Programs shows version '$have', expected '$ExpectedVersion'."
                return $false
            }
        }
        # The uninstall command must point at something that exists, or the
        # Add/Remove entry is a dead end - which is worse than no entry.
        $path = $v.UninstallString.Trim('"')
        return (Test-Path -LiteralPath $path)
    } catch { return $false }
}

function Unregister-Uninstaller {
    try {
        if (Test-Path $script:UninstallRegKey) {
            Remove-Item -Path $script:UninstallRegKey -Recurse -Force -ErrorAction Stop
            Write-Log 'Removed the Add/Remove Programs entry.' 'OK'
        }
        return $true
    } catch {
        Write-Log "Could not remove Add/Remove Programs entry: $($_.Exception.Message)" 'WARN'
        return $false
    }
}

# --- Launcher scripts ----------------------------------------------------

function Install-LauncherScripts {
    <#  The .cmd files the shortcuts point at.

        These are the ONLY place the embedded interpreter's path is written
        down, so moving or renaming the install folder breaks exactly one
        thing, loudly, instead of five things quietly.

        They contain no keys. Keys live in Friday's encrypted credential store
        via the setup wizard. The legacy start.bat pattern - writing
        ANTHROPIC_API_KEY as plain text into a batch file at the repo root -
        is deliberately not reproduced here.
    #>
    param([Parameter(Mandatory)][string] $InstallRoot)

    $pyRel     = 'python\python.exe'
    $pywRel    = 'python\pythonw.exe'
    $created   = @()

    $console = @"
@echo off
rem Agent Friday - written by the installer. Safe to delete along with the
rem rest of the install folder; contains no personal data and no keys.
title Agent Friday

rem Friday's own console output contains em-dashes, arrows and box characters.
rem On a default Windows console (code page 437 or 1252) those arrive as
rem mojibake - "Agent Friday - Asimov's Mind" renders with a replacement
rem character in the middle of the product name, on the very first line she
rem sees. cli.py already degrades gracefully rather than crashing, but the
rem right place to fix the ENCODING is the launcher, not the application.
rem
rem chcp switches this console to UTF-8; PYTHONUTF8 tells Python to use UTF-8
rem for stdio regardless of the locale. Both are scoped to this window.
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0app"
"%~dp0$pyRel" -m agent_friday.cli %*
if errorlevel 1 (
  echo.
  echo Friday stopped with a problem. The details are in:
  echo   %~dp0logs
  echo.
  pause
)
"@

    $background = @"
@echo off
rem Agent Friday - quiet start, used by the sign-in shortcut. pythonw.exe has
rem no console window, so signing in does not flash a black box.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0app"
start "" "%~dp0$pywRel" "%~dp0app\friday_tray.py"
"@

    $autostartToggle = @"
@echo off
rem Turns the automatic start on or off. This is here so that turning it off
rem is as easy as turning it on - see Shortcuts.ps1 for why that matters.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\autostart.ps1"
"@

    $uninstall = @"
@echo off
rem Removes Agent Friday from this computer.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall.ps1"
"@

    $map = @{
        'Agent Friday.cmd'                    = $console
        'Agent Friday (background).cmd'       = $background
        'Start Friday when I sign in.cmd'     = $autostartToggle
        'Uninstall Agent Friday.cmd'          = $uninstall
    }

    foreach ($name in $map.Keys) {
        $path = Join-Path $InstallRoot $name
        # ASCII, CRLF. cmd.exe is unhappy with a UTF-8 BOM at the top of a
        # batch file - it tries to execute the BOM as a command.
        [System.IO.File]::WriteAllText($path, ($map[$name] -replace "`r?`n", "`r`n") + "`r`n",
                                       (New-Object System.Text.ASCIIEncoding))
        $created += $path
        Write-Log "Wrote launcher: $path"
    }
    return $created
}

function Test-LauncherScripts {
    param([Parameter(Mandatory)][string] $InstallRoot)
    foreach ($n in @('Agent Friday.cmd','Agent Friday (background).cmd',
                     'Start Friday when I sign in.cmd','Uninstall Agent Friday.cmd')) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot $n))) {
            Write-Log "Missing launcher: $n" 'FAIL'
            return $false
        }
    }
    return $true
}
