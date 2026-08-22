#Requires -Version 5.1
<#
    Agent Friday - build the Windows installer artifact.

    Run this on a machine that has a normal Python 3.12 with pip. It produces
    dist\AgentFriday-Setup-<version>.zip, which is the thing you actually send
    someone: unzip it anywhere, double-click "Install Agent Friday.cmd", done.

    WHAT THIS DOES THAT THE INSTALLER DELIBERATELY DOES NOT
    -------------------------------------------------------
    1. Fetches the embeddable Python distribution and verifies its SHA-256
       against sources.json. The ARTIFACT brings its own Python; the REPO does
       not store an 11 MB binary. If the download fails here, the installer
       falls back to fetching it on the target machine, but shipping it means
       one fewer thing that can go wrong on her laptop.

    2. Builds wheels for the five packages that publish sdists only -
       pyautogui, pyscreeze, pygetwindow, mouseinfo, pytweening. They are pure
       Python, so a wheel built here works anywhere. This is what lets the
       installer stay literally --only-binary=:all: on the target machine and
       never invoke a build backend, which matters because pip's build
       isolation does not work under an embeddable interpreter at all.

    3. Copies the application source into payload\, excluding everything that
       is not needed to run: tests, .git, build artifacts, __pycache__, and -
       importantly - any of the legacy launch scripts at the repo root, which
       historically contained API keys in plain text.

    The exclusion list in Get-PayloadExcludes is a security boundary, not a
    size optimisation. Read it before you change it.
#>

[CmdletBinding()]
param(
    # NOTE: these deliberately default to empty and are resolved in the body.
    # $PSScriptRoot is not reliably populated while param() defaults are being
    # evaluated under PowerShell 5.1 - it comes back empty and Join-Path
    # throws before the script has printed a single line. Found the hard way.
    [string] $RepoRoot    = '',
    [string] $OutputDir   = '',
    [string] $BuildPython = 'python',
    # Skip fetching the embeddable Python. The installer will download it on
    # the target machine instead.
    [switch] $NoBundlePython,
    [switch] $NoWheelhouse
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot)  { $RepoRoot  = (Resolve-Path (Join-Path $Here '..\..')).Path }
if (-not $OutputDir) { $OutputDir = (Join-Path $Here 'dist') }

$Staging  = Join-Path $Here 'staging'
$Payload  = Join-Path $Staging 'payload'
$PyBundle = Join-Path $Staging 'python'
$Wheels   = Join-Path $Staging 'wheelhouse'

. (Join-Path $Here 'lib\Common.ps1')
. (Join-Path $Here 'lib\Download.ps1')

Initialize-Console
Initialize-Log (Join-Path $Here 'dist\build.log')

$sources = Get-Content -LiteralPath (Join-Path $Here 'sources.json') -Raw | ConvertFrom-Json

$version = '0.0.0'
$m = [regex]::Match((Get-Content -LiteralPath (Join-Path $RepoRoot 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"([^"]+)"')
if ($m.Success) { $version = $m.Groups[1].Value }

Set-StepTotal 5
Say-Banner -Version $version
Say "Building the Windows installer artifact."
Say "  repo   : $RepoRoot"
Say "  output : $OutputDir"
Say ''

if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Staging, $Payload, $OutputDir | Out-Null

# =========================================================================
#  1. Application payload
# =========================================================================

function Get-PayloadExcludes {
    <#  SECURITY BOUNDARY. Everything listed here is deliberately kept OUT of
        the artifact.

        The launch scripts at the repo root are the important ones.
        setup_wizard.py generates start.bat containing SET ANTHROPIC_API_KEY=
        and SET FRIDAY_PASSWORD= as plain text (setup_wizard.py:878-888), and
        friday_startup.vbs on a developer machine has historically held live
        secrets inline. They are gitignored, so they will not be in a clean
        clone - but this script runs against a working tree, and a working
        tree is exactly where they are. Shipping the developer's keys inside
        the installer would be the worst bug in this project's history.

        .friday, .env and *.key are here for the same reason.
    #>
    return @(
        '.git', '.github', '.claude', '.agents', '.venv', 'venv', 'env',
        'build', 'dist', 'node_modules', '__pycache__', '.pytest_cache',
        '.mypy_cache', '.ruff_cache', 'tests', 'packaging',
        # --- secret-bearing, never ship ---
        'start.bat', 'launch_now.bat', 'friday_startup.bat', 'friday_startup.vbs',
        'do_commit.bat', '.env', '.friday', 'secrets.yaml', 'config.yaml'
    )
}

Say-Step 'Copying the application'
$excludes = Get-PayloadExcludes
$excludeSet = @{}
foreach ($e in $excludes) { $excludeSet[$e.ToLowerInvariant()] = $true }

$copied = 0
$skippedSensitive = @()
foreach ($item in (Get-ChildItem -LiteralPath $RepoRoot -Force)) {
    $name = $item.Name.ToLowerInvariant()
    if ($excludeSet.ContainsKey($name)) {
        if ($name -match 'start|startup|\.env|secret|config\.yaml|commit') { $skippedSensitive += $item.Name }
        Write-Log "Excluded from payload: $($item.Name)"
        continue
    }
    Copy-Item -LiteralPath $item.FullName -Destination $Payload -Recurse -Force
    $copied++
}

# Second pass: kill anything that slipped through inside subdirectories.
foreach ($junk in @('__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','node_modules')) {
    Get-ChildItem -LiteralPath $Payload -Recurse -Force -Directory -Filter $junk -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

# -----------------------------------------------------------------------
# DO NOT reintroduce `-LiteralPath ... -Include`. PowerShell SILENTLY IGNORES
# -Include when the path is given as -LiteralPath. The previous version of
# these four lines read:
#
#   Get-ChildItem -LiteralPath $Payload -Recurse -Force -File `
#                 -Include '*.pyc','*.pyo','*.key','*.pem' | Remove-Item
#
# which matched EVERY FILE IN THE PAYLOAD and deleted all of them. The build
# then reported "48 top-level item(s) copied; no credential-shaped strings
# found" and produced a 12.5 MB zip containing the full directory tree and
# zero files. The credential scan passed because there was nothing left to
# scan - a check that passes vacuously is worse than no check, because it
# reports as evidence.
#
# Filtering in PowerShell rather than in the provider avoids the whole area.
# -----------------------------------------------------------------------
$junkExt = @('.pyc', '.pyo', '.key', '.pem')
Get-ChildItem -LiteralPath $Payload -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object { $junkExt -contains $_.Extension.ToLowerInvariant() } |
    ForEach-Object {
        Write-Log "Removed from payload: $($_.FullName)" 'WARN'
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }

if ($skippedSensitive.Count -gt 0) {
    Say-Note ("Kept out of the artifact on purpose: " + ($skippedSensitive -join ', '))
    Write-Log "Sensitive files excluded: $($skippedSensitive -join ', ')" 'OK'
}

# --- Prove the payload is actually there ---------------------------------
#
# This check exists because of the bug documented above: a build step deleted
# every file in the payload and NOTHING NOTICED. The directory tree was
# intact, the top-level count was right, the credential scan came back clean,
# and the artifact was 12.5 MB of empty folders. The installer then failed on
# a stranger's laptop with "Friday's own files could not be copied", which is
# a true sentence pointing at entirely the wrong machine.
#
# So: name the files without which the artifact is worthless, and refuse to
# continue if any is missing. A plausibility floor on the file count catches
# the same class of failure for files nobody thought to name.
Say-Working 'Checking the payload is complete.'
$mustExist = @(
    'pyproject.toml',
    'requirements.txt',
    'index.html',
    'src\agent_friday\cli.py',
    'src\agent_friday\server.py',
    'src\agent_friday\setup_wizard.py',
    'src\agent_friday\__init__.py',
    'friday_tray.py'
)
$missing = @()
foreach ($rel in $mustExist) {
    if (-not [System.IO.File]::Exists((Join-Path $Payload $rel))) { $missing += $rel }
}
$payloadFiles = @(Get-ChildItem -LiteralPath $Payload -Recurse -Force -File -ErrorAction SilentlyContinue)
if ($missing.Count -gt 0 -or $payloadFiles.Count -lt 200) {
    Say-Problem -What ("The payload is incomplete, so the build has stopped. " +
                       "Missing: " + $(if ($missing.Count) { $missing -join ', ' } else { '(nothing named)' }) +
                       ". File count: $($payloadFiles.Count).") `
                -WhatToDo 'This is a bug in build-installer.ps1, not in your checkout. Do not ship this artifact.'
    Write-Log "BUILD ABORTED - payload incomplete. missing=[$($missing -join ',')] filecount=$($payloadFiles.Count)" 'FAIL'
    Complete-Install -Failed -FailedStep 'build.payload' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
Say-Ok "$($payloadFiles.Count) files, all required entry points present."

# --- Prove it carries no keys. A grep is cheap; shipping a key is not. ----
#
# The naive version of this - "does the file contain /sk-ant-.{8,}/?" - fired
# on four files, all of which turned out to be deliberate test fixtures and
# documentation placeholders. One of them carries the repo's own
# `# pragma: allowlist secret`; another is docs/audits/release-readiness.md,
# which exists specifically to record that those strings are benign.
#
# A scanner that cries wolf gets switched off, and a scanner that is switched
# off is how a real key ships. So this one discriminates instead:
#
#   * a real credential is LONG. Anthropic keys run ~100 characters, Google
#     AIza keys are exactly 39, OpenAI sk- keys are 51. Placeholders are 21-37
#     characters of "abc123xyz".
#   * a real credential is HIGH ENTROPY. "abcdefghijklmnop" is not random and
#     Shannon entropy says so in one line of arithmetic.
#   * the repo already has a convention for "I know, it's a fixture" - honour
#     it rather than inventing a second one.
#
# Every candidate that is examined and cleared is logged by name, so the scan
# leaves evidence that it ran rather than only evidence that it found nothing.
function Get-ShannonEntropy {
    param([string] $S)
    if (-not $S -or $S.Length -eq 0) { return 0.0 }
    $counts = @{}
    foreach ($ch in $S.ToCharArray()) {
        if ($counts.ContainsKey($ch)) { $counts[$ch]++ } else { $counts[$ch] = 1 }
    }
    $h = 0.0
    foreach ($k in $counts.Keys) {
        $p = $counts[$k] / $S.Length
        $h -= $p * [Math]::Log($p, 2)
    }
    return $h
}

Say-Working 'Scanning the payload for anything that looks like a credential.'

# pattern, minimum plausible real length
$leakPatterns = @(
    @{ Rx = 'sk-ant-[A-Za-z0-9_\-]{16,}'; MinLen = 50 },   # real ~100 chars
    @{ Rx = 'AIza[A-Za-z0-9_\-]{30,}';    MinLen = 39 },   # real exactly 39
    @{ Rx = 'sk-[A-Za-z0-9]{40,}';        MinLen = 48 },   # real 51
    @{ Rx = 'gh[pousr]_[A-Za-z0-9]{30,}'; MinLen = 40 },
    @{ Rx = 'xox[baprs]-[A-Za-z0-9\-]{24,}'; MinLen = 40 }
)
$scanExt = @('.py','.bat','.cmd','.vbs','.json','.yaml','.yml','.md','.txt','.html','.js','.ts','.ps1','.sh','.env','.ini','.cfg','.toml')
$leaks    = @()
$cleared  = @()

foreach ($f in (Get-ChildItem -LiteralPath $Payload -Recurse -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $scanExt -contains $_.Extension.ToLowerInvariant() })) {
    $lines = $null
    try { $lines = Get-Content -LiteralPath $f.FullName -ErrorAction SilentlyContinue } catch { continue }
    if (-not $lines) { continue }
    $ln = 0
    foreach ($line in $lines) {
        $ln++
        if ($line -match 'allowlist secret|pragma:\s*allowlist') { continue }
        foreach ($p in $leakPatterns) {
            foreach ($m in [regex]::Matches($line, $p.Rx)) {
                $v = $m.Value
                if ($v.Length -lt $p.MinLen) {
                    $cleared += "$($f.Name):$ln (too short: $($v.Length) chars)"
                    continue
                }
                $tail = $v.Substring([Math]::Min(8, $v.Length))
                $ent = Get-ShannonEntropy $tail
                if ($ent -lt 3.2) {
                    $cleared += ("$($f.Name):$ln (low entropy: {0:N2} bits/char)" -f $ent)
                    continue
                }
                # Long AND random. Treat as real and stop the build.
                $leaks += "$($f.FullName.Replace($Payload,'<payload>')):$ln"
            }
        }
    }
}

if ($cleared.Count -gt 0) {
    Write-Log "Credential scan examined and CLEARED $($cleared.Count) placeholder(s):" 'OK'
    foreach ($c2 in ($cleared | Select-Object -Unique)) { Write-Log "  cleared: $c2" 'OK' }
}

if ($leaks.Count -gt 0) {
    Say-Problem -What ("The payload contains something that looks like a LIVE API key - long enough " +
                       "and random enough to be real - so the build has stopped rather than ship it. " +
                       "Locations: " + ($leaks -join ', ')) `
                -WhatToDo ('Remove the key from those files, add the file to Get-PayloadExcludes, or ' +
                           "append '# pragma: allowlist secret' if it genuinely is a fixture. Then build again.")
    Write-Log "BUILD ABORTED - probable live credential in payload: $($leaks -join '; ')" 'FAIL'
    Complete-Install -Failed -FailedStep 'build.credentialscan' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
Say-Ok "$copied top-level item(s); $($cleared.Count) placeholder(s) examined and cleared; no live credentials."

# =========================================================================
#  2. Bundle the embeddable Python
# =========================================================================

if ($NoBundlePython) {
    Say-Step 'Skipping the bundled Python (-NoBundlePython)'
    Say-Detail 'The installer will download it on the target machine instead.'
} else {
    Say-Step 'Fetching the embeddable Python'
    New-Item -ItemType Directory -Force -Path $PyBundle | Out-Null
    $zipName = Split-Path -Leaf ([Uri]$sources.python.url).AbsolutePath
    $zipPath = Join-Path $PyBundle $zipName
    if (-not (Get-RemoteFile -Uri $sources.python.url -OutFile $zipPath -FriendlyName 'the embeddable Python')) {
        Say-Problem -What 'Could not download the Python distribution.' -WhatToDo 'Check the network and build again, or pass -NoBundlePython.'
        exit 1
    }
    Assert-FileHash -Path $zipPath -ExpectedSha256 $sources.python.sha256 -What 'the embeddable Python'
    Say-Ok "$zipName bundled and hash-verified."

    if (-not (Get-RemoteFile -Uri $sources.get_pip.url -OutFile (Join-Path $PyBundle 'get-pip.py') -FriendlyName 'the pip bootstrap')) {
        Say-Note 'Could not fetch get-pip.py; the installer will fetch it on the target machine.'
    } else {
        $h = Get-Sha256 (Join-Path $PyBundle 'get-pip.py')
        Say-Ok "get-pip.py bundled. Its SHA-256 today is $h"
        Say-Detail 'sources.json deliberately does not pin this - see the note in that file.'
    }
}

# =========================================================================
#  3. Wheelhouse: the five sdist-only, pure-Python packages
# =========================================================================

if ($NoWheelhouse) {
    Say-Step 'Skipping the wheelhouse (-NoWheelhouse)'
    Say-Detail 'The installer will fall back to building them on the target machine.'
} else {
    Say-Step 'Building wheels for the packages that do not publish any'
    New-Item -ItemType Directory -Force -Path $Wheels | Out-Null

    # --- Which Python builds the wheels ---------------------------------
    #
    # NOT whatever `python` resolves to on the build machine. On this one it
    # resolved to an unrelated project's venv shim, pip wheel failed, and the
    # build cheerfully produced an artifact with an empty wheelhouse - i.e.
    # it silently fell back to the source-build path the wheelhouse exists to
    # avoid. A build step that can quietly not happen is worse than one that
    # is not there.
    #
    # So we build with the SAME embeddable interpreter we just bundled. It is
    # sitting in staging already, it is the exact version the target machine
    # will run, and it means the build host needs no Python of its own. The
    # five packages are pure Python, so the resulting wheels are py3-none-any
    # and work anywhere regardless.
    $buildPy = $null
    $embedZip = $null
    if (-not $NoBundlePython) {
        $embedZip = Get-ChildItem -LiteralPath $PyBundle -Filter '*.zip' -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($embedZip) {
        $bpRoot = Join-Path $Staging 'buildpy'
        Say-Working 'Preparing a throwaway interpreter to build them with.'
        Expand-Archive -LiteralPath $embedZip.FullName -DestinationPath $bpRoot -Force
        $pth = Get-ChildItem -LiteralPath $bpRoot -Filter 'python*._pth' | Select-Object -First 1
        @("$($pth.BaseName -replace '\._pth$','').zip", '.', 'Lib\site-packages', 'import site') |
            Set-Content -LiteralPath $pth.FullName -Encoding ascii
        $buildPy = Join-Path $bpRoot 'python.exe'

        $gp = Join-Path $PyBundle 'get-pip.py'
        if (-not (Test-Path -LiteralPath $gp)) {
            [void](Get-RemoteFile -Uri $sources.get_pip.url -OutFile $gp -FriendlyName 'the pip bootstrap')
        }
        $null = Invoke-Native -FilePath $buildPy -Arguments @($gp, '--no-warn-script-location', '--no-cache-dir') -TimeoutSeconds 900
        # setuptools + wheel must be installed, not fetched by build isolation:
        # a ._pth makes PYTHONPATH inert, which is how pip hands build backends
        # their dependencies. Without this, every sdist fails with
        # "BackendUnavailable: Cannot import 'setuptools.build_meta'".
        $null = Invoke-Native -FilePath $buildPy -Arguments @(
            '-m','pip','install','--only-binary=:all:','--no-warn-script-location',
            '--disable-pip-version-check','--no-input','setuptools','wheel'
        ) -TimeoutSeconds 900
    }
    if (-not $buildPy -or -not (Test-Path -LiteralPath $buildPy)) {
        Say-Detail 'Falling back to the build machine''s own Python.'
        $buildPy = $BuildPython
    }

    # Every sdist-only package in the PyAutoGUI dependency graph, named
    # explicitly. `pip wheel --no-deps` builds only what you name, and naming
    # them individually means one failing does not take the rest with it.
    $targets = @('pyautogui','pyscreeze','pygetwindow','mouseinfo','pytweening','pymsgbox','pyperclip','pyrect')
    $failed = @()
    foreach ($p in $targets) {
        $r = Invoke-Native -FilePath $buildPy -Arguments @(
            '-m','pip','wheel','--no-deps','--no-build-isolation',
            '--wheel-dir', $Wheels, $p
        ) -TimeoutSeconds 900
        if ($r.ExitCode -ne 0) {
            # Retry once WITH build isolation - the build machine's own Python
            # can do that even though the embedded one cannot.
            $r = Invoke-Native -FilePath $buildPy -Arguments @(
                '-m','pip','wheel','--no-deps','--wheel-dir', $Wheels, $p
            ) -TimeoutSeconds 900
        }
        if ($r.ExitCode -ne 0) {
            $failed += $p
            Write-Log "Could not build a wheel for ${p}: exit $($r.ExitCode)" 'WARN'
        }
    }
    if ($failed.Count -gt 0) {
        Say-Note ("Could not build: " + ($failed -join ', '))
    }

    # The throwaway interpreter must not end up inside the artifact - it would
    # double the download and ship a second Python nobody asked for.
    $bpRootClean = Join-Path $Staging 'buildpy'
    if (Test-Path -LiteralPath $bpRootClean) { Remove-Item -LiteralPath $bpRootClean -Recurse -Force }

    $built = @(Get-ChildItem -LiteralPath $Wheels -Filter '*.whl' -ErrorAction SilentlyContinue)
    if ($built.Count -eq 0) {
        # ABORT rather than warn. An empty wheelhouse means the artifact
        # silently falls back to building sdists on her laptop - the exact
        # thing the wheelhouse exists to prevent - and the previous version of
        # this script printed a Note and then said "installer built" anyway.
        # A build that half-worked must not report as a build that worked.
        Say-Problem -What ('No wheels could be built, so the installer would have to compile ' +
                           'packages on the target machine. The build has stopped rather than ' +
                           'produce an artifact that quietly does the wrong thing.') `
                    -WhatToDo ('Check that the bundled Python could reach PyPI, or re-run with ' +
                               '-NoWheelhouse if you accept the source-build fallback deliberately.')
        Write-Log 'BUILD ABORTED - wheelhouse is empty.' 'FAIL'
        Complete-Install -Failed -FailedStep 'build.wheelhouse' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
        exit 1
    } else {
        # Every wheel must be pure-Python (py3-none-any). A wheel tagged for a
        # specific CPython ABI would only work on the build machine's version
        # and would silently not match on hers.
        $bad = @($built | Where-Object { $_.Name -notmatch 'py3-none-any\.whl$' -and $_.Name -notmatch 'py2\.py3-none-any\.whl$' })
        foreach ($b in $bad) {
            Say-Note "Not pure-Python, removing from the wheelhouse: $($b.Name)"
            Write-Log "Removed non-universal wheel: $($b.Name)" 'WARN'
            Remove-Item -LiteralPath $b.FullName -Force
        }
        $kept = @(Get-ChildItem -LiteralPath $Wheels -Filter '*.whl')
        Say-Ok "$($kept.Count) universal wheel(s): $(($kept | ForEach-Object { $_.Name -replace '-py[23].*','' }) -join ', ')"
    }
}

# =========================================================================
#  4. Installer files
# =========================================================================

Say-Step 'Assembling the installer'
foreach ($f in @('install.ps1','uninstall.ps1','autostart.ps1','sources.json','healing.json','Install Agent Friday.cmd','README.md')) {
    $src = Join-Path $Here $f
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $Staging -Force }
}
Copy-Item -LiteralPath (Join-Path $Here 'lib')          -Destination $Staging -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Here 'requirements') -Destination $Staging -Recurse -Force
Say-Ok 'Assembled.'

# =========================================================================
#  5. Zip
# =========================================================================

Say-Step 'Packing'
$zipOut = Join-Path $OutputDir "AgentFriday-Setup-$version.zip"
if (Test-Path -LiteralPath $zipOut) { Remove-Item -LiteralPath $zipOut -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Staging, $zipOut,
    [System.IO.Compression.CompressionLevel]::Optimal, $false)

$sizeMb = [math]::Round((Get-Item $zipOut).Length / 1MB, 1)
$hash = Get-Sha256 $zipOut

Say ''
Say-Ok "AgentFriday-Setup-$version.zip  ($sizeMb MB)"
Say "        $zipOut"
Say "        SHA-256 $hash"
Say ''
Say '  Send that zip. She unzips it anywhere and double-clicks'
Say '  "Install Agent Friday.cmd". Nothing else is needed on her machine -'
Say '  no Python, no git, no Ollama.'
Say ''

Complete-Install -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
exit 0
