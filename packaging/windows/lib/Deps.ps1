#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Deps.ps1

    Installing Friday's Python dependencies into the embedded interpreter.

    THE TIERS, AND WHY
    ------------------
    core         Friday will not start without these. A failure here is fatal.
    recommended  Friday starts and works, but noticeably less of her works.
                 Local voice, PDF reading, the full privacy egress gate.
                 A failure here is a warning, never a stop.
    memory       Conversation memory (sentence-transformers -> torch, plus
                 ChromaDB). This is ~2.5 GB of download on its own and is the
                 single slowest thing in the install. Also optional, also
                 never fatal - but it is the part the tutorial calls "the
                 heart of her", so we install it by default and say what it
                 costs in time rather than quietly skipping it.

    WHEELS ONLY, AND THE ONE HONEST EXCEPTION
    -----------------------------------------
    Everything is installed with --only-binary=:all:, so pip will refuse a
    source distribution rather than silently try to compile it. No compiler is
    ever invoked on her machine. Verified on Windows 11 on 2026-08-21: the
    core and recommended tiers resolve and install with zero wheel builds.

    The exception is the PyAutoGUI family - pyautogui, pyscreeze, pygetwindow,
    mouseinfo, pytweening. These publish NO wheels at all, only sdists. Their
    sdists were inspected and contain no C sources and no ext_modules, so they
    need no compiler; but --only-binary=:all: still refuses them on principle.
    Rather than relaxing that flag on her machine, build-installer.ps1 builds
    those five into wheels on the BUILD machine and ships them in a local
    wheelhouse. The installer then stays literally wheels-only and never needs
    setuptools, a build backend, or a network round-trip to a build isolation
    environment - which is just as well, because pip's build isolation is
    broken under an embeddable interpreter anyway (see Python.ps1).

    If the wheelhouse is missing (someone ran the installer straight from a
    source checkout), we fall back to --no-build-isolation with setuptools
    pre-installed, log loudly that we did, and continue.
#>

Set-StrictMode -Version 2.0

function Get-PipBaseArgs {
    <# Flags every pip invocation gets. Kept in one place so a healing
       remediation that adds a flag cannot accidentally drop these. #>
    param([string] $WheelhouseDir = $null)
    $a = @(
        '-m','pip','install',
        '--only-binary=:all:',
        '--no-warn-script-location',
        '--disable-pip-version-check',
        '--no-input'
    )
    if ($WheelhouseDir -and (Test-Path -LiteralPath $WheelhouseDir)) {
        # --find-links adds the local wheelhouse as an ADDITIONAL source. We
        # deliberately do not pass --no-index: the wheelhouse only carries the
        # handful of packages that have no wheels on PyPI, and everything else
        # must still come from PyPI so version resolution stays normal.
        $a += @('--find-links', $WheelhouseDir)
    }
    return $a
}

function Install-RequirementSet {
    <#  Install one requirements file. Returns the Invoke-Native result.
        Does NOT decide whether it worked - Test-ModulesImportable does that,
        and Invoke-Step is what compares the two.
    #>
    param(
        [Parameter(Mandatory)][string]   $InstallRoot,
        [Parameter(Mandatory)][string]   $RequirementsFile,
        [string]   $WheelhouseDir = $null,
        [string[]] $ExtraFlags = @(),
        [int]      $TimeoutSeconds = 5400
    )
    $exe  = Get-PythonExe $InstallRoot
    $args = (Get-PipBaseArgs -WheelhouseDir $WheelhouseDir) + $ExtraFlags + @('-r', $RequirementsFile)
    return (Invoke-Native -FilePath $exe -Arguments $args -TimeoutSeconds $TimeoutSeconds)
}

function Test-ModulesImportable {
    <#  THE verification for a dependency tier.

        Not `pip list`. Not pip's exit code. We start the interpreter and
        import the modules, because that is the only question that matters and
        the only one that catches the interesting failures: a wheel that
        installed but whose native DLL will not load, a half-written
        site-packages left by an interrupted install, a package whose import
        name differs from its distribution name.

        Returns $true only if EVERY module imports. Logs precisely which ones
        did not.
    #>
    param(
        [Parameter(Mandatory)][string]   $InstallRoot,
        [Parameter(Mandatory)][string[]] $Modules
    )
    $exe = Get-PythonExe $InstallRoot
    if (-not (Test-Path -LiteralPath $exe)) { return $false }

    # One interpreter start, importing each module in a try so we get a full
    # list of what is broken rather than only the first failure.
    $py = @'
import importlib, sys, json
mods = json.loads(sys.argv[1])
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append("%s: %s: %s" % (m, type(e).__name__, e))
print("MISSING_COUNT=%d" % len(bad))
for b in bad:
    print("MISSING " + b)
'@
    $json = ($Modules | ConvertTo-Json -Compress)
    if ($Modules.Count -eq 1) { $json = "[$($Modules[0] | ConvertTo-Json -Compress)]" }

    $r = Invoke-Native -FilePath $exe -Arguments @('-c', $py, $json) -TimeoutSeconds 300
    if ($r.ExitCode -ne 0) {
        Write-Log "Import probe itself failed (exit $($r.ExitCode)) - treating tier as not installed." 'FAIL'
        return $false
    }
    if ($r.StdOut -match 'MISSING_COUNT=(\d+)') {
        $n = [int]$Matches[1]
        if ($n -eq 0) {
            Write-Log "All $($Modules.Count) module(s) import cleanly." 'OK'
            return $true
        }
        foreach ($line in ($r.StdOut -split "`r?`n")) {
            if ($line -like 'MISSING *') { Write-Log $line 'FAIL' }
        }
        return $false
    }
    Write-Log 'Import probe produced no verdict line - treating as failure.' 'FAIL'
    return $false
}

function Install-PyAutoGuiFamily {
    <#  The sdist-only five. Prefers the shipped wheelhouse; falls back to
        --no-build-isolation with pre-installed setuptools.

        Kept separate from the tiers on purpose: this is the ONLY place in the
        whole installer where a non-wheel path can be taken, so it is the only
        place anyone has to audit if that ever needs to change.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [string] $WheelhouseDir = $null
    )
    $exe = Get-PythonExe $InstallRoot

    if ($WheelhouseDir -and (Test-Path -LiteralPath $WheelhouseDir) -and
        (Get-ChildItem -LiteralPath $WheelhouseDir -Filter '*.whl' -ErrorAction SilentlyContinue)) {
        Write-Log "Installing the PyAutoGUI family from the shipped wheelhouse at $WheelhouseDir"
        $args = @('-m','pip','install','--only-binary=:all:','--no-warn-script-location',
                  '--disable-pip-version-check','--no-input',
                  '--find-links', $WheelhouseDir, 'pyautogui')
        return (Invoke-Native -FilePath $exe -Arguments $args -TimeoutSeconds 900)
    }

    Add-InstallWarning ('No wheelhouse was shipped, so the PyAutoGUI family was built from ' +
                        'source distributions on the target machine. They are pure Python so ' +
                        'no compiler was needed, but this path is slower and less predictable ' +
                        'than the intended one. Run build-installer.ps1 to produce a proper ' +
                        'artifact.')

    # setuptools+wheel must be present BEFORE this, because build isolation
    # cannot deliver them under an embeddable interpreter (PYTHONPATH is inert).
    $null = Invoke-Native -FilePath $exe -Arguments @(
        '-m','pip','install','--only-binary=:all:','--no-warn-script-location',
        '--disable-pip-version-check','--no-input','setuptools','wheel'
    ) -TimeoutSeconds 600

    return (Invoke-Native -FilePath $exe -Arguments @(
        '-m','pip','install','--no-build-isolation','--no-warn-script-location',
        '--disable-pip-version-check','--no-input','pyautogui'
    ) -TimeoutSeconds 900)
}

function Get-InstalledDistributions {
    <# Used by the report and by the uninstaller's sanity checks. #>
    param([Parameter(Mandatory)][string] $InstallRoot)
    $exe = Get-PythonExe $InstallRoot
    $r = Invoke-Native -FilePath $exe -Arguments @('-m','pip','list','--format=freeze','--disable-pip-version-check') -TimeoutSeconds 180
    if ($r.ExitCode -ne 0) { return ,@() }
    return ,@($r.StdOut -split "`r?`n" | Where-Object { $_ -match '==' })
}

