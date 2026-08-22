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
Get-ChildItem -LiteralPath $Payload -Recurse -Force -File -Include '*.pyc','*.pyo','*.key','*.pem' -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Log "Removed from payload: $($_.FullName)" 'WARN'
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }

if ($skippedSensitive.Count -gt 0) {
    Say-Note ("Kept out of the artifact on purpose: " + ($skippedSensitive -join ', '))
    Write-Log "Sensitive files excluded: $($skippedSensitive -join ', ')" 'OK'
}

# --- Prove it. A grep is cheap; shipping a key is not. -------------------
Say-Working 'Scanning the payload for anything that looks like a credential.'
$leakPatterns = @('sk-ant-[A-Za-z0-9_\-]{8,}', 'AIza[A-Za-z0-9_\-]{20,}', 'sk-[A-Za-z0-9]{32,}')
$leaks = @()
foreach ($f in (Get-ChildItem -LiteralPath $Payload -Recurse -File -Include '*.py','*.bat','*.cmd','*.vbs','*.json','*.yaml','*.yml','*.md','*.txt','*.html','*.js' -ErrorAction SilentlyContinue)) {
    try { $text = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction SilentlyContinue } catch { continue }
    if (-not $text) { continue }
    foreach ($p in $leakPatterns) {
        if ([regex]::IsMatch($text, $p)) { $leaks += $f.FullName; break }
    }
}
if ($leaks.Count -gt 0) {
    Say-Problem -What ("The payload contains something that looks like a live API key, so the build has stopped. " +
                       "Files: " + (($leaks | ForEach-Object { Split-Path -Leaf $_ }) -join ', ')) `
                -WhatToDo 'Remove the key from those files, or add them to Get-PayloadExcludes, then build again.'
    Write-Log "BUILD ABORTED - possible credential in payload: $($leaks -join '; ')" 'FAIL'
    exit 1
}
Say-Ok "$copied top-level item(s) copied; no credential-shaped strings found."

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

    $r = Invoke-Native -FilePath $BuildPython -Arguments @(
        '-m','pip','wheel',
        '--no-deps',
        '--wheel-dir', $Wheels,
        '-r', (Join-Path $Here 'requirements\wheelhouse.txt')
    ) -TimeoutSeconds 1800

    # pip wheel with --no-deps only builds the named package, so build the
    # transitive sdist-only set explicitly. They are all pure Python.
    foreach ($p in @('pyscreeze','pygetwindow','mouseinfo','pytweening','pymsgbox','pyperclip','pyrect')) {
        $null = Invoke-Native -FilePath $BuildPython -Arguments @(
            '-m','pip','wheel','--no-deps','--wheel-dir', $Wheels, $p
        ) -TimeoutSeconds 900
    }

    $built = @(Get-ChildItem -LiteralPath $Wheels -Filter '*.whl' -ErrorAction SilentlyContinue)
    if ($built.Count -eq 0) {
        Say-Note 'No wheels were built. The installer will fall back to source builds on the target machine.'
        Write-Log 'Wheelhouse is EMPTY after the build step.' 'WARN'
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
