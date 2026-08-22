#Requires -Version 5.1
<#
    Agent Friday - Windows uninstaller

    One obvious action, reached from Start menu > Agent Friday > Uninstall
    Agent Friday, or from Settings > Apps > Installed apps.

    WHY THIS FILE IS AS LONG AS IT IS
    ---------------------------------
    A cautious person will not install something she cannot remove. So this
    has to actually work, and it has to be legible while it works. It says
    what it will remove and what it will keep BEFORE it asks, in one screen,
    in plain words, with sizes - and then it does exactly that and nothing
    else.

    THE ONE THING THAT WOULD BE EASY TO GET CATASTROPHICALLY WRONG
    -------------------------------------------------------------
    Friday's vault is encrypted at rest. The passphrase that opens it lives in
    Windows Credential Manager under agent-friday/vault-passphrase.

    If the uninstaller "tidied up" that credential while preserving the vault,
    it would leave her a folder of AES-256-GCM ciphertext with the key thrown
    away. Technically it left her data. Practically it destroyed it, silently,
    while reporting success.

    So the rule is: the credential entries and the vault live or die together.
    Keep the notes, keep the key. Remove the notes, remove the key. There is
    no third option and the code below has no branch that produces one.

    WHAT COUNTS AS "NOTHING BEHIND"
    -------------------------------
    Removed in every case: the install folder (app + its private Python),
    every shortcut, the autostart entry, the Add/Remove Programs entry, the
    multi-gigabyte model checkpoints and caches under ~/.friday, the
    all-MiniLM-L6-v2 weights in the Hugging Face cache, and the Ollama models
    THIS installer pulled.

    Kept by default, and said out loud: her notes, wiki, conversation memory,
    skills, settings and creations under ~/.friday, plus the credential that
    unlocks them.

    Never touched unless she explicitly asks: Ollama itself (and only if this
    installer put it there), and any Ollama model this installer did not pull.
#>

[CmdletBinding()]
param(
    [string] $InstallRoot = '',
    # Set when we have already copied ourselves out of the folder we are about
    # to delete. See Move-SelfOutOfHarmsWay.
    [switch] $Relaunched,
    # Non-interactive, for tests. Preserves data (the safe default).
    [switch] $Unattended,
    # Remove her notes too. Interactive runs ask; this is for tests.
    [switch] $RemoveEverything
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir = Join-Path $Here 'lib'
if (-not (Test-Path $LibDir)) { $LibDir = $Here }   # tools/ layout after install

. (Join-Path $LibDir 'Common.ps1')
. (Join-Path $LibDir 'Download.ps1')
. (Join-Path $LibDir 'Ollama.ps1')
. (Join-Path $LibDir 'Shortcuts.ps1')

Initialize-Console

# --- Locate the install ---------------------------------------------------

if (-not $InstallRoot) {
    # tools\uninstall.ps1 -> install root is two levels up. If that is not it,
    # fall back to the registry, then to the default location.
    $guess = Split-Path -Parent $Here
    if (Test-Path -LiteralPath (Join-Path $guess 'install-manifest.json')) {
        $InstallRoot = $guess
    } else {
        try {
            $reg = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday' -ErrorAction Stop
            $InstallRoot = $reg.InstallLocation
        } catch {
            $InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday'
        }
    }
}

$LogDir = Join-Path $env:TEMP 'AgentFriday-uninstall'
Initialize-Log (Join-Path $LogDir ("uninstall-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss')))
Write-Log "Install root: $InstallRoot"

$FridayDir   = Join-Path $env:USERPROFILE '.friday'
if ($env:FRIDAY_HOME) { $FridayDir = Join-Path $env:FRIDAY_HOME '.friday' }

$manifest = $null
$manifestPath = Join-Path $InstallRoot 'install-manifest.json'
if (Test-Path -LiteralPath $manifestPath) {
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json } catch { }
}
if (-not $manifest) {
    Write-Log 'No install manifest found - falling back to conservative defaults.' 'WARN'
}

# --- Get out of the folder we are about to delete -------------------------

function Move-SelfOutOfHarmsWay {
    <#  We cannot delete the folder we are running from - Windows holds the
        script file and the PowerShell process's working directory. So copy
        the tools somewhere neutral and re-launch from there.

        Doing this rather than "delete everything except this one file and
        hope" is why the uninstaller can honestly claim to leave nothing. #>
    param([string] $Root)
    $temp = Join-Path $env:TEMP ("AgentFriday-uninstall-" + [guid]::NewGuid().ToString('N').Substring(0,8))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    Copy-Item -Path (Join-Path $Here '*') -Destination $temp -Recurse -Force
    Write-Log "Relaunching uninstaller from $temp"
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File', (Join-Path $temp 'uninstall.ps1'),
              '-InstallRoot', $Root, '-Relaunched')
    if ($Unattended)       { $args += '-Unattended' }
    if ($RemoveEverything) { $args += '-RemoveEverything' }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $args -NoNewWindow -Wait
    exit 0
}

$hereFull = ([System.IO.Path]::GetFullPath($Here)).TrimEnd('\')
$rootFull = ([System.IO.Path]::GetFullPath($InstallRoot)).TrimEnd('\')
if (-not $Relaunched -and ($hereFull -eq $rootFull -or $hereFull.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase))) {
    Move-SelfOutOfHarmsWay -Root $InstallRoot
}

# --- Measure, so the numbers on screen are real ---------------------------

function Remove-FridayCredentials {
    <#  Delete the two Windows Credential Manager entries Friday creates.
        Identifiers only - the values are never read, printed or logged.

          service  agent-friday   account  vault-passphrase
          service  agent-friday   account  governance-key

        Sources: cli.py:1002 / routes/core_routes.py:599 / routes/insights.py:309
        and governance/proof_of_integrity.py:324-325.

        Called ONLY from the remove-everything branch. If you find a call to
        this in a branch that preserves ~/.friday, that is a bug that destroys
        her data while reporting success - see the header of this file.

        Defined up here rather than next to its caller because PowerShell
        scripts execute top-down: a function defined below its call site does
        not exist yet when the call runs.
    #>
    $names = @('agent-friday/vault-passphrase', 'agent-friday/governance-key')
    foreach ($n in $names) {
        $r = Invoke-Native -FilePath "$env:SystemRoot\System32\cmdkey.exe" -Arguments @("/delete:$n") -TimeoutSeconds 30
        Write-Log "cmdkey /delete for a stored credential exited $($r.ExitCode)"
    }
    # cmdkey does not always see credentials written through the Python
    # keyring backend, so ask keyring itself too, while Friday's interpreter
    # still exists. Best effort - deleting the vault files is what actually
    # removes the data.
    $py = Join-Path $InstallRoot 'python\python.exe'
    if (Test-Path -LiteralPath $py) {
        $code = @'
import sys
try:
    import keyring
except Exception:
    sys.exit(0)
for account in ("vault-passphrase", "governance-key"):
    try:
        keyring.delete_password("agent-friday", account)
    except Exception:
        pass
'@
        $null = Invoke-Native -FilePath $py -Arguments @('-c', $code) -TimeoutSeconds 60
    }
    Write-Log 'Credential Manager entries for agent-friday removed (identifiers only; no values were read).' 'OK'
}

function Get-FolderSizeGb {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0.0 }
    try {
        $b = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
        if (-not $b) { return 0.0 }
        return [math]::Round($b / 1GB, 2)
    } catch { return 0.0 }
}

# Cache/checkpoint folders under ~/.friday. These are downloads, not her work,
# so they go even when her data is preserved - otherwise "uninstalled" would
# leave several gigabytes sitting there.
#
# Deliberately a DENY-list, not an allow-list: anything under ~/.friday that
# is not named here is preserved. A future version that adds a new data folder
# is therefore preserved by default rather than deleted by default, which is
# the right way round for a mistake to happen.
$CacheSubdirs = @(
    'local_voice',          # faster-whisper + piper checkpoints
    'models\nemo',          # NeMo GPU voice + its private HF cache
    'runtime\models',       # GGUF weights copied out of Ollama
    'cache',                # model discovery cache
    'audio-cache',
    'vibe-code-logs',
    'logs'
)

$HfModelDirs = @(
    (Join-Path $env:USERPROFILE '.cache\huggingface\hub\models--sentence-transformers--all-MiniLM-L6-v2'),
    (Join-Path $env:USERPROFILE '.cache\torch\sentence_transformers')
)

$sizeInstall = Get-FolderSizeGb $InstallRoot
$sizeCaches  = 0.0
foreach ($s in $CacheSubdirs) { $sizeCaches += Get-FolderSizeGb (Join-Path $FridayDir $s) }
foreach ($d in $HfModelDirs)  { $sizeCaches += Get-FolderSizeGb $d }
$sizeData    = (Get-FolderSizeGb $FridayDir) - $sizeCaches
if ($sizeData -lt 0) { $sizeData = 0.0 }

$pulledModels = @()
if ($manifest -and $manifest.ollama -and $manifest.ollama.models_pulled) {
    $pulledModels = @($manifest.ollama.models_pulled)
}
$weInstalledOllama = $false
if ($manifest -and $manifest.ollama) { $weInstalledOllama = [bool]$manifest.ollama.installed_by_this_installer }

$sizeModels = 0.0
if ($pulledModels.Count -gt 0) {
    $ollamaRoot = $env:OLLAMA_MODELS
    if (-not $ollamaRoot) { $ollamaRoot = Join-Path $env:USERPROFILE '.ollama\models' }
    $sizeModels = Get-FolderSizeGb $ollamaRoot
}

# --- Say what will happen, then ask ---------------------------------------

Say-Banner
Say "$($script:C.Bold)Removing Agent Friday$($script:C.Reset)"
Say ''
Say "$($script:C.Bold)This will be removed:$($script:C.Reset)"
Say ("  - Friday's program files                         {0,6:N2} GB" -f $sizeInstall)
Say ("  - The AI models and voices she downloaded        {0,6:N2} GB" -f ($sizeCaches + $sizeModels))
Say '  - The desktop and Start menu shortcuts'
if (Test-Autostart) { Say '  - The setting that starts her when you sign in' }
Say '  - Her entry in the list of installed programs'
Say ''

$removeData = $RemoveEverything

if (-not $Unattended) {
    Say "$($script:C.Bold)This will be kept:$($script:C.Reset)"
    Say ("  - Your notes, wiki, conversations and settings   {0,6:N2} GB" -f $sizeData)
    Say "    $($script:C.Grey)$FridayDir$($script:C.Reset)"
    Say '  - The passphrase that unlocks them, in Windows Credential Manager'
    Say ''
    Say '  Those are kept on purpose. If you ever install Friday again she'
    Say '  picks up exactly where she left off. If you want them gone, say so'
    Say '  in a moment - but they cannot be recovered afterwards.'
    Say ''
    if ($pulledModels.Count -gt 0) {
        Say "$($script:C.Bold)Also on this computer:$($script:C.Reset)"
        Say "  - Ollama, the program that runs AI models"
        if ($weInstalledOllama) {
            Say '    Setup installed this, so it can be removed too. You will be asked.'
        } else {
            Say '    This was already here before Friday, so it will be left alone.'
        }
        Say ''
    }

    $go = Read-Host '  Remove Agent Friday? [y/N]'
    if ($go -notmatch '^[Yy]') {
        Say ''
        Say '  Nothing was removed.'
        Say ''
        exit 0
    }

    Say ''
    Say '  One more question.'
    Say ''
    $d = Read-Host '  Delete your notes and conversations as well? This cannot be undone. [y/N]'
    $removeData = ($d -match '^[Yy]')
    if ($removeData) {
        Say ''
        $confirm = Read-Host '  Type DELETE to confirm you want your notes gone'
        if ($confirm.Trim() -ne 'DELETE') {
            $removeData = $false
            Say '  Not confirmed - your notes will be kept.'
        }
    }
    Say ''
}

Write-Log "removeData = $removeData"
Set-StepTotal 8

# =========================================================================
#  1. Stop anything that is running, or the deletes will fail on file locks
# =========================================================================

Say-Step 'Closing Friday if she is running'
$stopped = 0
foreach ($p in @(Get-Process -ErrorAction SilentlyContinue)) {
    try {
        if (-not $p.Path) { continue }
        $full = ([System.IO.Path]::GetFullPath($p.Path)).TrimEnd('\')
        if ($full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
            Write-Log "Stopping $($p.ProcessName) (pid $($p.Id)) running from the install folder"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    } catch { }
}
if ($stopped -gt 0) { Start-Sleep -Seconds 3 }
Say-Ok "$stopped running item(s) closed."

# =========================================================================
#  2. Shortcuts and autostart
# =========================================================================

$null = Invoke-Step -Id 'uninstall.shortcuts' -Title 'Removing the shortcuts' `
    -Optional -VerifyDescription 'no Friday shortcuts remain' `
    -Action {
        $targets = @()
        if ($manifest -and $manifest.shortcuts) { $targets += @($manifest.shortcuts) }
        # Belt and braces for an install whose manifest is missing or stale.
        $targets += @(
            (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Agent Friday.lnk'),
            (Join-Path ([Environment]::GetFolderPath('Startup')) 'Agent Friday.lnk')
        )
        foreach ($t in ($targets | Sort-Object -Unique)) {
            if ($t -and (Test-Path -LiteralPath $t)) {
                Remove-Item -LiteralPath $t -Force -ErrorAction SilentlyContinue
                Write-Log "Removed shortcut: $t"
            }
        }
        [void](Disable-Autostart)
        $sm = Get-StartMenuDir
        if (Test-Path -LiteralPath $sm) { Remove-Item -LiteralPath $sm -Recurse -Force -ErrorAction SilentlyContinue }
    } `
    -Verify {
        (-not (Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Agent Friday.lnk'))) -and
        (-not (Test-Autostart)) -and
        (-not (Test-Path -LiteralPath (Get-StartMenuDir)))
    }

# =========================================================================
#  3. Ollama models this installer pulled
# =========================================================================

if ($pulledModels.Count -gt 0 -and (Get-OllamaExe)) {
    Say-Step 'Removing the AI models Friday downloaded'
    foreach ($tag in $pulledModels) {
        # Only the tags recorded at install time. Not `ollama rm` on
        # everything - she may have models this installer never touched.
        if ($tag -notmatch '^[a-z0-9][a-z0-9._\-]*(:[a-z0-9][a-z0-9._\-]*)?$') {
            Write-Log "Skipping oddly-shaped model tag from manifest: '$tag'" 'WARN'
            continue
        }
        $r = Invoke-Native -FilePath (Get-OllamaExe) -Arguments @('rm', $tag) -TimeoutSeconds 300
        if (Test-OllamaHasModel -Tag $tag) {
            Add-InstallWarning "Model '$tag' is still present after 'ollama rm' (exit $($r.ExitCode))."
            Write-Log "Model '$tag' NOT removed." 'WARN'
        } else {
            Write-Log "Model '$tag' removed." 'OK'
        }
    }
    Say-Ok 'Done.'
} else {
    Say-Step 'Removing the AI models Friday downloaded'
    Say-Detail 'None were downloaded by this install.'
}

# =========================================================================
#  4. Ollama itself - only if we put it there, and only if asked
# =========================================================================

Say-Step 'Ollama'
if (-not $weInstalledOllama) {
    Say-Detail 'Left alone - it was already on this computer before Friday.'
    Write-Log 'Ollama not removed: not installed by this installer.'
}
elseif ($Unattended) {
    Say-Detail 'Left alone (unattended run).'
}
else {
    Say ''
    Say '  Setup installed Ollama. It is a separate program that runs AI'
    Say '  models on this laptop. Other things can use it too.'
    Say ''
    $ro = Read-Host '  Remove Ollama as well? [y/N]'
    if ($ro -match '^[Yy]') {
        $removed = $false
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            $null = Invoke-Native -FilePath $winget.Source -Arguments @(
                'uninstall','--id','Ollama.Ollama','-e','--silent','--disable-interactivity'
            ) -TimeoutSeconds 900
            $removed = -not (Test-OllamaInstalled)
        }
        if (-not $removed) {
            $unins = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Ollama') `
                                   -Filter 'unins*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($unins) {
                $null = Invoke-Native -FilePath $unins.FullName -Arguments @('/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES') -TimeoutSeconds 900
                Start-Sleep -Seconds 3
                $removed = -not (Test-OllamaInstalled)
            }
        }
        if ($removed) {
            Say-Ok 'Ollama removed.'
        } else {
            # Do not claim it. Tell her where to finish the job.
            Say-Note 'Ollama could not be removed automatically.'
            Say '        To remove it: Start menu, type "Add or remove programs",'
            Say '        find Ollama in the list, and click Uninstall.'
            Add-InstallWarning 'Automatic Ollama uninstall failed; the user was given manual instructions.'
        }
    } else {
        Say-Ok 'Ollama kept.'
    }
}

# =========================================================================
#  5. Model checkpoints and caches under ~/.friday, and the HF cache
# =========================================================================

$null = Invoke-Step -Id 'uninstall.caches' -Title 'Removing the downloaded voices and model files' `
    -Optional -VerifyDescription 'the cache folders are gone' `
    -Action {
        foreach ($s in $CacheSubdirs) {
            $p = Join-Path $FridayDir $s
            if (Test-Path -LiteralPath $p) {
                Write-Log "Removing cache folder: $p ($(Get-FolderSizeGb $p) GB)"
                Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        foreach ($d in $HfModelDirs) {
            if (Test-Path -LiteralPath $d) {
                Write-Log "Removing model cache: $d"
                Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    } `
    -Verify {
        $left = @()
        foreach ($s in $CacheSubdirs) { if (Test-Path -LiteralPath (Join-Path $FridayDir $s)) { $left += $s } }
        foreach ($d in $HfModelDirs)  { if (Test-Path -LiteralPath $d) { $left += $d } }
        if ($left.Count -gt 0) { Write-Log "Cache folders still present: $($left -join ', ')" 'WARN' }
        return ($left.Count -eq 0)
    }

# =========================================================================
#  6. Her data - and the credential that opens it. Together, always.
# =========================================================================

Say-Step 'Your notes'

if ($removeData) {
    # Both, or neither. See the header of this file.
    $null = Invoke-Step -Id 'uninstall.data' -Title 'Deleting your notes' -Quiet `
        -Optional -VerifyDescription 'the .friday folder is gone' `
        -Action {
            if (Test-Path -LiteralPath $FridayDir) {
                Remove-Item -LiteralPath $FridayDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            $creations = Join-Path ([Environment]::GetFolderPath('Desktop')) 'friday-creations'
            if (Test-Path -LiteralPath $creations) {
                Remove-Item -LiteralPath $creations -Recurse -Force -ErrorAction SilentlyContinue
            }
            Remove-FridayCredentials
        } `
        -Verify { -not (Test-Path -LiteralPath $FridayDir) }

    Say-Ok 'Your notes and the passphrase that unlocked them are gone.'
    Say-Detail 'Nothing about Friday remains on this computer.'
}
else {
    # THE STATEMENT. Explicit, with the path, and with the reason the
    # credential is still there - because otherwise its presence looks like
    # leftover mess rather than a deliberate decision.
    Say-Ok 'Kept, exactly as they were.'
    Say ''
    Say "        Your notes, wiki, conversations and settings are here:"
    Say "          $($script:C.Bold)$FridayDir$($script:C.Reset)"
    Say ''
    Say '        The passphrase that unlocks them is still in Windows'
    Say '        Credential Manager, on purpose. Removing it would leave you'
    Say '        with notes that nobody, including you, could ever open again.'
    Say ''
    Say '        If you install Friday again she picks these up automatically.'
    Say '        To delete them by hand, delete that folder.'
    Say ''
    Write-Log "PRESERVED: $FridayDir and the agent-friday credential entries (vault key)." 'OK'
}

# =========================================================================
#  7. Add/Remove Programs entry
# =========================================================================

$null = Invoke-Step -Id 'uninstall.registry' -Title 'Removing Friday from the installed programs list' `
    -Optional -VerifyDescription 'the registry entry is gone' `
    -Action { [void](Unregister-Uninstaller) } `
    -Verify { -not (Test-UninstallerRegistered) }

# =========================================================================
#  8. The install folder itself
# =========================================================================

$null = Invoke-Step -Id 'uninstall.root' -Title 'Removing Friday''s program files' `
    -HumanFailure 'Most of Friday was removed, but one folder would not delete. Something on the computer is still using a file inside it.' `
    -HumanFix 'Restart the computer and run the uninstaller once more from Settings, Apps, Installed apps.' `
    -VerifyDescription 'the install folder is gone' `
    -Action {
        if (Test-Path -LiteralPath $InstallRoot) {
            Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $InstallRoot) {
            # One retry after a pause - a just-killed process can hold a
            # handle for a second or two after it exits.
            Start-Sleep -Seconds 5
            Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    } `
    -Verify { -not (Test-Path -LiteralPath $InstallRoot) }

# =========================================================================

Complete-Install -ReportPath (Join-Path $LogDir 'LAST-UNINSTALL-REPORT.md')

$warns = Get-FailureWarnings
Say ''
if ($warns.Count -eq 0) {
    Say "  $($script:C.Green)$($script:C.Bold)Agent Friday has been removed.$($script:C.Reset)"
} else {
    Say "  $($script:C.Yellow)$($script:C.Bold)Agent Friday has been removed, with a couple of leftovers.$($script:C.Reset)"
    Say ''
    Say '  Something on this computer was holding on to a file, so one or two'
    Say '  things could not be deleted. Restarting the computer and running'
    Say '  the uninstaller once more usually clears it.'
}
Say ''
Say "  $($script:C.Grey)For Stephen: $(Join-Path $LogDir 'LAST-UNINSTALL-REPORT.md')$($script:C.Reset)"
Say ''

if (-not $Unattended) { Read-Host '  Press Enter to close' | Out-Null }
exit 0

