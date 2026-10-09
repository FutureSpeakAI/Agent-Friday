#Requires -Version 5.1
<#
    Agent Friday - build the Windows installer artifact.

    Run this on a machine that has Inno Setup 6.3 or newer (ISCC.exe). It
    stages the payload, then compiles dist\AgentFriday-Setup-<tag>.exe, the one
    file you send someone: it carries its own Python, Friday's files and the
    pre-built wheels, and needs nothing else on the other computer. A SHA-256
    file is written beside it. The setup program is unsigned.

    WHAT THIS DOES THAT THE INSTALLER DELIBERATELY DOES NOT
    -------------------------------------------------------
    1. Fetches the embeddable Python distribution and verifies its SHA-256
       against sources.json. The ARTIFACT brings its own Python; the REPO does
       not store an 11 MB binary. If the download fails here, the installer
       falls back to fetching it on the target machine, but shipping it means
       one fewer thing that can go wrong on her laptop.

    2. Builds wheels for the four packages that publish sdists only -
       pyautogui, pyscreeze, pygetwindow, pytweening. They are pure
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
    [switch] $NoWheelhouse,
    # Where ISCC.exe is, when it is not under Program Files\Inno Setup 6.
    [string] $IsccPath = ''
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

Set-StepTotal 6
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

function Get-PayloadExcludePatterns {
    <#  SECURITY BOUNDARY, by SHAPE rather than by name.

        Get-PayloadExcludes above is an exact-name list, which means it only
        ever catches the scratch files someone already thought of. Debugging
        dumps at the repo root (names like _msgws_raw.txt or _orig_bytes.tmp,
        holding live message data) are neither gitignored nor named in that
        list, so they would be copied into the shipped payload.
        pyinstaller_build.log is in the same position: gitignored, therefore
        absent from a clean clone, but present in the working tree this script
        actually reads.

        These patterns are matched against ROOT-LEVEL entries only (the copy
        loop below enumerates $RepoRoot, not the tree), so '_*' cannot reach a
        package's __init__.py or any other underscore-prefixed real source
        further down. Nothing tracked at root matches any of these -- verified
        with `git ls-files` before adding them.

        If you add a scratch file at the repo root, name it with a leading
        underscore and it will never ship.
    #>
    return @(
        '_*',            # leading-underscore scratch/dump convention
        '*.tmp', '*.log', # transient output; pyinstaller_build.log lived here
        '*.bak', '*.orig', '*.rej'  # editor and merge-conflict leftovers
    )
}

Say-Step 'Copying the application'
$excludes = Get-PayloadExcludes
$excludeSet = @{}
foreach ($e in $excludes) { $excludeSet[$e.ToLowerInvariant()] = $true }

$excludePatterns = Get-PayloadExcludePatterns

$copied = 0
$skippedSensitive = @()
$skippedScratch = @()
foreach ($item in (Get-ChildItem -LiteralPath $RepoRoot -Force)) {
    $name = $item.Name.ToLowerInvariant()
    if ($excludeSet.ContainsKey($name)) {
        if ($name -match 'start|startup|\.env|secret|config\.yaml|commit') { $skippedSensitive += $item.Name }
        Write-Log "Excluded from payload: $($item.Name)"
        continue
    }
    # Shape-based exclusion. Deliberately AFTER the exact-name list so the
    # sensitive-file accounting above is unchanged, and deliberately loud: a
    # scratch file that reaches a build is worth seeing in the log, because the
    # alternative is finding it in a published artifact.
    $matchedPattern = $null
    foreach ($pat in $excludePatterns) {
        if ($name -like $pat) { $matchedPattern = $pat; break }
    }
    if ($matchedPattern) {
        $skippedScratch += $item.Name
        Write-Log "Excluded from payload (scratch pattern '$matchedPattern'): $($item.Name)"
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

# -----------------------------------------------------------------------
#  TRACKED-TREE GUARD. The copy loop above snapshots a WORKING TREE, and the
#  exclusion lists only catch what someone already thought of. A build made
#  from a working tree has shipped ~290 files that exist only on the
#  developer's machine: a second git repository
#  checked out inside the repo root (with its own .git/), seven gitignored
#  token files, a Claude memory file, PowerShell caches, and local-only
#  handoff documents. Every one of them was gitignored or locally excluded,
#  so a clean clone would never have them -- but nothing enforced the
#  "build from a clean worktree at the tag" sentence in the release docs.
#
#  This does. Every file in the payload must be tracked by git at the commit
#  being built, and no tracked file may carry uncommitted changes. A build
#  that would ship anything else stops here, names the strays, and tells you
#  to build from a fresh clone or `git worktree add <dir> v<version>`.
# -----------------------------------------------------------------------
Say-Step 'Verifying the payload is the committed tree'
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Say-Problem -What 'git is not on PATH, so the build cannot prove the payload matches a commit.' `
                -WhatToDo 'Install Git for Windows (or build from a machine that has it) and run the build again from a fresh clone at the release tag.'
    Write-Log 'BUILD ABORTED - git unavailable; tracked-tree guard cannot run' 'FAIL'
    Complete-Install -Failed -FailedStep 'build.trackedtree' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
$headSha = (& git -C $RepoRoot rev-parse --short HEAD 2>$null)
$trackedRaw = & git -C $RepoRoot -c core.quotepath=off ls-files -z
$tracked = @{}
foreach ($t in ($trackedRaw -split "`0")) {
    if ($t) { $tracked[$t.Replace('/', '\').ToLowerInvariant()] = $true }
}
$stray = @()
$payloadFiles = @(Get-ChildItem -LiteralPath $Payload -Recurse -Force -File -ErrorAction SilentlyContinue)
foreach ($pf in $payloadFiles) {
    $rel = $pf.FullName.Substring($Payload.Length).TrimStart('\')
    if (-not $tracked.ContainsKey($rel.ToLowerInvariant())) { $stray += $rel }
}
$dirty = @(& git -C $RepoRoot status --porcelain --untracked-files=no 2>$null | Where-Object { $_ })
if ($stray.Count -gt 0 -or $dirty.Count -gt 0) {
    $shown = ($stray | Select-Object -First 15) -join ', '
    if ($stray.Count -gt 15) { $shown += ", ... and $($stray.Count - 15) more" }
    $why = @()
    if ($stray.Count -gt 0) { $why += "$($stray.Count) file(s) in the payload are not tracked by git at $headSha ($shown)" }
    if ($dirty.Count -gt 0) { $why += "$($dirty.Count) tracked file(s) have uncommitted changes" }
    Say-Problem -What ("The payload is not the committed tree: " + ($why -join '; ') + ". Untracked and modified files " +
                       "are exactly how a developer's private material reaches a public artifact, so the build has stopped.") `
                -WhatToDo ('Build from a fresh clone or a clean worktree at the release tag ' +
                           '(git worktree add <dir> v<version>; cd <dir>\packaging\windows; .\build-installer.ps1). ' +
                           'If a file genuinely belongs in the release, commit it first.')
    foreach ($s in $stray) { Write-Log "Untracked file in payload: $s" 'FAIL' }
    foreach ($d in $dirty) { Write-Log "Modified tracked file: $d" 'FAIL' }
    Write-Log "BUILD ABORTED - payload is not the committed tree ($($stray.Count) untracked, $($dirty.Count) modified)" 'FAIL'
    Complete-Install -Failed -FailedStep 'build.trackedtree' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
Say-Ok "All $($payloadFiles.Count) payload file(s) are tracked at $headSha with no uncommitted changes."
Write-Log "Tracked-tree guard passed: $($payloadFiles.Count) files, HEAD $headSha" 'OK'

if ($skippedSensitive.Count -gt 0) {
    Say-Note ("Kept out of the artifact on purpose: " + ($skippedSensitive -join ', '))
    Write-Log "Sensitive files excluded: $($skippedSensitive -join ', ')" 'OK'
}
if ($skippedScratch.Count -gt 0) {
    Say-Note ("Scratch files left out of the artifact: " + ($skippedScratch -join ', '))
    Write-Log "Scratch files excluded by pattern: $($skippedScratch -join ', ')" 'OK'
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
    'friday_tray.py',
    # The Reader draws a PDF with the vendored pdf.js; these are the files it imports.
    'static\vendor\pdfjs-6.4.299\VERSION.json',
    'static\vendor\pdfjs-6.4.299\legacy\pdf.min.mjs',
    'static\vendor\pdfjs-6.4.299\legacy\pdf.worker.min.js'
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

# --- Prove each vendored library arrived whole ----------------------------
#
# A library under static\vendor\<name>\ that carries a VERSION.json (every file it ships,
# with its SHA-256) is checked against that manifest here, in the payload, so a copy that
# lost files or an exclusion that reaches into static\vendor stops the build instead of
# shipping a Reader that cannot draw a PDF. A build that finds no manifest at all stops too:
# a check that has nothing to check would pass for the wrong reason.
Say-Working 'Checking the vendored libraries against their manifests.'
$vendorRoot = Join-Path $Payload 'static\vendor'
$vendorBad = @()
$vendorChecked = 0
$vendorLibs = 0
foreach ($dir in @(Get-ChildItem -LiteralPath $vendorRoot -Directory -ErrorAction SilentlyContinue)) {
    $manifest = Join-Path $dir.FullName 'VERSION.json'
    if (-not [System.IO.File]::Exists($manifest)) { continue }
    $vendorLibs++
    try { $pins = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).files }
    catch { $vendorBad += "$($dir.Name)\VERSION.json cannot be read"; continue }
    foreach ($pin in $pins.PSObject.Properties) {
        $file = Join-Path $dir.FullName ($pin.Name -replace '/', '\')
        if (-not [System.IO.File]::Exists($file)) { $vendorBad += "$($dir.Name)\$($pin.Name) is missing"; continue }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $pin.Value) { $vendorBad += "$($dir.Name)\$($pin.Name) differs from its pinned hash" }
        $vendorChecked++
    }
}
if ($vendorBad.Count -gt 0 -or $vendorLibs -eq 0) {
    $shown = (@($vendorBad) | Select-Object -First 8) -join '; '
    if ($vendorBad.Count -gt 8) { $shown += "; and $($vendorBad.Count - 8) more" }
    Say-Problem -What ("A vendored library is not whole in the payload, so the build has stopped. " +
                       $(if ($vendorBad.Count) { $shown } else { 'No VERSION.json manifest was found under static\vendor.' })) `
                -WhatToDo 'Check that nothing in Get-PayloadExcludes reaches into static\vendor, and that the library is committed with its VERSION.json. Do not ship this artifact.'
    Write-Log "BUILD ABORTED - vendored library incomplete: $shown (libraries with a manifest: $vendorLibs)" 'FAIL'
    Complete-Install -Failed -FailedStep 'build.vendor' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
Say-Ok "$vendorChecked vendored file(s) in $vendorLibs librar$(if ($vendorLibs -eq 1) { 'y' } else { 'ies' }) match their pinned hashes."

# --- Prove it carries no keys. A grep is cheap; shipping a key is not. ----
#
# The naive version of this - "does the file contain /sk-ant-.{8,}/?" - fired
# on four files, all of which turned out to be deliberate test fixtures and
# documentation placeholders. One of them carries the repo's own
# `# pragma: allowlist secret`; the others record that those strings are
# benign.
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
#  3. Wheelhouse: the four sdist-only, pure-Python packages
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
    # four packages are pure Python, so the resulting wheels are py3-none-any
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
    # mouseinfo is left out: it is GPL-3.0, pyautogui imports it only inside a
    # try block, and it cannot load here anyway (it needs tkinter).
    $targets = @('pyautogui','pyscreeze','pygetwindow','pytweening','pymsgbox','pyperclip','pyrect')
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

    # --- Every runtime wheel, so the install is offline ---------------------
    #
    # The setup program installs from this folder with --no-index. So the folder
    # must hold a wheel for everything the installer will ask pip for: each
    # requirements file, with its whole dependency tree. Download them here
    # (same interpreter, same platform as the target: CPython 3.12, win_amd64),
    # then PROVE coverage by resolving every file again with the index switched
    # off. A build whose wheelhouse has a hole stops here.
    # The wheels built above (the sdist-only family); the pure-Python check
    # below applies to these only, never to the platform wheels collected here.
    $ownWheelNames = @(Get-ChildItem -LiteralPath $Wheels -Filter '*.whl' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $tierFiles = @('core', 'recommended', 'memory', 'judgment') |
        ForEach-Object { Join-Path (Join-Path $Here 'requirements') "$_.txt" }
    $pipCommon = @('--only-binary=:all:', '--disable-pip-version-check', '--no-input')
    if (-not $embedZip) {
        Say-Problem -What 'The wheelhouse cannot be completed without the bundled interpreter (-NoBundlePython).' `
                    -WhatToDo 'Build without -NoBundlePython, or pass -NoWheelhouse to accept a setup that needs the internet.'
        Complete-Install -Failed -FailedStep 'build.wheelhouse' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
        exit 1
    }
    foreach ($tf in $tierFiles) {
        Say-Working ("Collecting the wheels for " + (Split-Path -Leaf $tf) + ' (a large download the first time).')
        $dl = Invoke-Native -FilePath $buildPy -Arguments (@('-m', 'pip', 'download') + $pipCommon +
            @('--dest', $Wheels, '--find-links', $Wheels, '-r', $tf)) -TimeoutSeconds 7200
        if ($dl.ExitCode -ne 0) {
            Say-Problem -What ("Could not collect wheels for $(Split-Path -Leaf $tf): " + (($dl.Combined -split "`r?`n") | Select-Object -Last 3) -join ' ') `
                        -WhatToDo 'Check the network and build again. A wheelhouse with a hole is never shipped.'
            Write-Log "BUILD ABORTED - pip download failed for $tf" 'FAIL'
            Complete-Install -Failed -FailedStep 'build.wheelhouse' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
            exit 1
        }
    }
    foreach ($tf in $tierFiles) {
        $chk = Invoke-Native -FilePath $buildPy -Arguments (@('-m', 'pip', 'install', '--dry-run', '--no-index') + $pipCommon +
            @('--find-links', $Wheels, '-r', $tf)) -TimeoutSeconds 1800
        if ($chk.ExitCode -ne 0) {
            $why = (($chk.Combined -split "`r?`n") | Where-Object { $_ -match 'No matching distribution|ERROR' } | Select-Object -First 3) -join ' '
            Say-Problem -What ("The wheelhouse does not cover $(Split-Path -Leaf $tf): $why") `
                        -WhatToDo 'Do not ship this artifact. Build again; if it repeats, a requirement has no wheel for CPython 3.12 on Windows.'
            Write-Log "BUILD ABORTED - wheelhouse does not cover $tf" 'FAIL'
            Complete-Install -Failed -FailedStep 'build.wheelhouse' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
            exit 1
        }
    }
    $allWheels = @(Get-ChildItem -LiteralPath $Wheels -Filter '*.whl')
    $wheelMb = [math]::Round((($allWheels | Measure-Object -Property Length -Sum).Sum) / 1MB)
    Say-Ok "$($allWheels.Count) wheels ($wheelMb MB) cover every requirement; the install resolves from them offline."
    Write-Log "Wheelhouse covers core, recommended, memory and judgment: $($allWheels.Count) wheels, $wheelMb MB" 'OK'

    # The throwaway interpreter must not end up inside the artifact - it would
    # double the download and ship a second Python nobody asked for.
    $bpRootClean = Join-Path $Staging 'buildpy'
    if (Test-Path -LiteralPath $bpRootClean) { Remove-Item -LiteralPath $bpRootClean -Recurse -Force }

    $built = @(Get-ChildItem -LiteralPath $Wheels -Filter '*.whl' -ErrorAction SilentlyContinue |
               Where-Object { $ownWheelNames -contains $_.Name })
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
        $kept = @($built | Where-Object { Test-Path -LiteralPath $_.FullName })
        Say-Ok "$($kept.Count) universal wheel(s): $(($kept | ForEach-Object { $_.Name -replace '-py[23].*','' }) -join ', ')"
    }
}

# =========================================================================
#  4. Installer files
# =========================================================================

Say-Step 'Assembling the installer'
foreach ($f in @('install.ps1','uninstall.ps1','autostart.ps1','ensure-shortcuts.ps1','sources.json','healing.json')) {
    $src = Join-Path $Here $f
    if (-not (Test-Path -LiteralPath $src)) {
        Say-Problem -What "The installer file $f is missing from packaging\windows." -WhatToDo 'This is a bug in the repository, not in your checkout. Do not ship this artifact.'
        Write-Log "BUILD ABORTED - installer file missing: $f" 'FAIL'
        Complete-Install -Failed -FailedStep 'build.installerfiles' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
        exit 1
    }
    Copy-Item -LiteralPath $src -Destination $Staging -Force
}
Copy-Item -LiteralPath (Join-Path $Here 'lib')          -Destination $Staging -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Here 'requirements') -Destination $Staging -Recurse -Force
Say-Ok 'Assembled.'

# =========================================================================
#  5. Compile the setup program
# =========================================================================

Say-Step 'Compiling the installer with Inno Setup'

# --- The release's names and numbers, from the one place that owns them ----
function ConvertTo-ReleaseTag {
    <#  1.0.1b1 (PEP 440, as pyproject.toml spells it) -> 1.0.1-beta.1 (the tag
        and the file name). A plain 5.14.3 stays as it is. #>
    param([Parameter(Mandatory)][string] $Pep440)
    $mm = [regex]::Match($Pep440, '^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$')
    if (-not $mm.Success) { throw "Cannot turn the version '$Pep440' into a release tag." }
    if (-not $mm.Groups[2].Success) { return $mm.Groups[1].Value }
    $word = @{ a = 'alpha'; b = 'beta'; rc = 'rc' }[$mm.Groups[2].Value]
    return ('{0}-{1}.{2}' -f $mm.Groups[1].Value, $word, $mm.Groups[3].Value)
}

$releasePy = Join-Path $RepoRoot 'src\agent_friday\release.py'
$seqMatch = [regex]::Match((Get-Content -LiteralPath $releasePy -Raw), '(?m)^BUILD_SEQUENCE\s*=\s*([0-9_]+)')
if (-not $seqMatch.Success) {
    Say-Problem -What 'BUILD_SEQUENCE could not be read from src\agent_friday\release.py.' -WhatToDo 'Restore the line, then build again.'
    Complete-Install -Failed -FailedStep 'build.sequence' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
$buildSequence = [int64]($seqMatch.Groups[1].Value -replace '_', '')

# The same arithmetic, written a second time in lib\Upgrade.ps1 for the
# installer's own use. They must agree about THIS release before it ships.
. (Join-Path $Here 'lib\Upgrade.ps1')
$sequenceFromVersion = Get-BuildSequence -Version $version
if ($sequenceFromVersion -ne $buildSequence) {
    Say-Problem -What ("release.py says build sequence $buildSequence but the version $version in pyproject.toml works out to $sequenceFromVersion. " +
                       'The setup program would mis-rank this release against the ones before it, so the build has stopped.') `
                -WhatToDo 'Bump BUILD_SEQUENCE in src\agent_friday\release.py (and RELEASE_TAG) together with the version in pyproject.toml.'
    Write-Log "BUILD ABORTED - sequence mismatch: release.py=$buildSequence pyproject=$sequenceFromVersion" 'FAIL'
    Complete-Install -Failed -FailedStep 'build.sequence' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
$releaseTag = ConvertTo-ReleaseTag -Pep440 $version
$stageNumber = [int64]($buildSequence % 100)
$fileVersion = ''
$fv = [regex]::Match($releaseTag, '^(\d+)\.(\d+)\.(\d+)')
$fileVersion = '{0}.{1}.{2}.{3}' -f $fv.Groups[1].Value, $fv.Groups[2].Value, $fv.Groups[3].Value, $stageNumber

# --- Find Inno Setup ------------------------------------------------------
$iscc = $null
if ($IsccPath) {
    if (Test-Path -LiteralPath $IsccPath) { $iscc = $IsccPath }
} else {
    $pf86 = ${env:ProgramFiles(x86)}
    $candidates = @(
        $(if ($pf86) { Join-Path $pf86 'Inno Setup 6\ISCC.exe' }),
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe' }),
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe' })
    ) | Where-Object { $_ }
    foreach ($cand in $candidates) { if (Test-Path -LiteralPath $cand) { $iscc = $cand; break } }  # not $c: that is $script:C, the colour table
    if (-not $iscc) {
        $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($onPath) { $iscc = $onPath.Source }
    }
}
if (-not $iscc) {
    Say-Problem -What 'Inno Setup 6 (ISCC.exe) was not found, so the setup program cannot be compiled.' `
                -WhatToDo 'Install it (winget install JRSoftware.InnoSetup, or choco install innosetup) or pass -IsccPath. GitHub''s windows-latest runners already have it.'
    Write-Log 'BUILD ABORTED - ISCC.exe not found' 'FAIL'
    Complete-Install -Failed -FailedStep 'build.iscc' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}
Write-Log "Using Inno Setup compiler: $iscc"

$issPath = Join-Path $Here 'installer\AgentFriday.iss'
$exeName = "AgentFriday-Setup-$releaseTag.exe"
$exeOut = Join-Path $OutputDir $exeName
if (Test-Path -LiteralPath $exeOut) { Remove-Item -LiteralPath $exeOut -Force }

Say-Working "Compiling $exeName (this compresses the payload and takes a few minutes)."
$compile = Invoke-Native -FilePath $iscc -Arguments @(
    "/DAppVersion=$version", "/DAppTag=$releaseTag", "/DBuildSequence=$buildSequence",
    "/DAppFileVersion=$fileVersion", "/DStageDir=$Staging", "/DRepoRoot=$RepoRoot",
    "/DOutDir=$OutputDir", $issPath
) -TimeoutSeconds 1800
if ($compile.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $exeOut)) {
    $tail = (($compile.Combined -split "`r?`n") | Select-Object -Last 12) -join ' | '
    Say-Problem -What "Inno Setup could not compile the installer (exit $($compile.ExitCode)). $tail" `
                -WhatToDo 'The full compiler output is in dist\build.log.'
    Write-Log "BUILD ABORTED - ISCC exit $($compile.ExitCode)" 'FAIL'
    Complete-Install -Failed -FailedStep 'build.iscc' -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
    exit 1
}

# --- The checksum travels beside the file --------------------------------
$hash = (Get-FileHash -LiteralPath $exeOut -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText("$exeOut.sha256", "$hash  $exeName`n", (New-Object System.Text.ASCIIEncoding))
$sizeMb = [math]::Round((Get-Item -LiteralPath $exeOut).Length / 1MB, 1)

Say ''
Say-Ok "$exeName  ($sizeMb MB)"
Say "        $exeOut"
Say "        SHA-256 $hash"
Say ''
Say '  Send that one file. It carries its own Python, Friday''s files and the'
Say '  pre-built wheels; nothing else is needed on the other computer. It is'
Say '  unsigned, so Windows SmartScreen may ask the person to confirm.'
Say ''

Complete-Install -ReportPath (Join-Path $OutputDir 'BUILD-REPORT.md')
exit 0
