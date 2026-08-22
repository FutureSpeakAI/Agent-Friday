#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Python.ps1

    Provisions a private, self-contained CPython for Friday.

    WHY THE EMBEDDABLE DISTRIBUTION
    -------------------------------
    A stock Windows 11 machine has a `python.exe` on PATH that is not Python.
    It is a Microsoft Store *app execution alias* - a zero-byte reparse point
    that opens the Store when you run it. Scripts that check "is python
    available?" by running `python --version` get a Store page, not a version,
    and a non-technical user is now three clicks into a shop wondering what
    they did wrong. There is no reliable way to tell the stub from the real
    thing by name alone.

    So we do not look for a Python. We bring one. The embeddable distribution
    is a ~11 MB zip that unpacks to a working interpreter with no installer,
    no registry entries, no PATH changes and no interaction with any other
    Python on the machine. Deleting the folder uninstalls it completely, which
    is also what makes the uninstaller honest.

    THE THREE EMBEDDABLE-PYTHON TRAPS (all verified on Windows 11, 2026-08-21)
    -------------------------------------------------------------------------
    1. `python312._pth` exists, and its presence puts the interpreter in
       isolated mode. That is what we want - but it also means PYTHONPATH is
       IGNORED. Verified: setting PYTHONPATH and asking sys.path about it
       returns False. Anything that expects to inject a path via the
       environment silently does nothing.

       Consequence for this app: agent_friday.cli spawns server.py and
       setup_wizard.py with env["PYTHONPATH"] = <src>. Under this interpreter
       that assignment is inert. We therefore put the app's src directory
       into the ._pth itself, which is honoured, so the import works whether
       or not the env var does. We do NOT need to modify cli.py.

    2. Because PYTHONPATH is inert, pip's PEP 517 build isolation is broken -
       it hands the build backend its dependencies via PYTHONPATH. Any sdist
       fails with "BackendUnavailable: Cannot import 'setuptools.build_meta'".
       Fix: pre-install setuptools+wheel into site-packages and pass
       --no-build-isolation. Better fix, which is what we actually do: ship
       pre-built wheels so no sdist is ever built on her machine at all.

    3. No `tkinter`, no `venv`, no `ensurepip`. Confirmed present and working:
       sqlite3, ssl, ctypes, lzma, bz2, decimal, unicodedata, multiprocessing.
       Nothing in agent_friday imports tkinter (grepped: zero matches), and
       `mouseinfo` - the only dependency that does - is imported defensively
       by pyautogui, which imports fine without it.
#>

Set-StrictMode -Version 2.0

function Get-PythonExe   { param([string] $InstallRoot) return (Join-Path $InstallRoot 'python\python.exe') }
function Get-PythonRoot  { param([string] $InstallRoot) return (Join-Path $InstallRoot 'python') }

function Test-PythonProvisioned {
    <#  The verification for the "bring your own Python" step.

        Deliberately does more than Test-Path. A truncated download, a
        half-extracted zip or a Store stub copied into place would all pass a
        file-existence check. We run the interpreter and make it tell us its
        own version and platform, and we require the app's src directory to be
        on sys.path - which is the thing we actually need to be true.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [Parameter(Mandatory)][string] $ExpectedVersion,
        [string] $AppSrcDir = $null
    )
    $exe = Get-PythonExe $InstallRoot
    if (-not (Test-Path -LiteralPath $exe)) { return $false }

    # A Store alias is a 0-byte reparse point. Real python.exe is ~100 KB.
    try {
        $len = (Get-Item -LiteralPath $exe).Length
        if ($len -lt 20000) {
            Write-Log "python.exe at $exe is only $len bytes - that is an app-execution stub, not an interpreter." 'FAIL'
            return $false
        }
    } catch { return $false }

    # Each fact is LABELLED and matched by its label, not by its line number.
    # The first version of this read $lines[0], $lines[1], $lines[2] and broke
    # the moment the output arrived in a different order - which it did, for a
    # reason that had nothing to do with Python (see Invoke-Native's header).
    # A verifier that can be defeated by line ordering is not a verifier, and
    # this one reports a healthy install as a failed download when it goes
    # wrong, which is the worst possible direction to be wrong in.
    $probe = 'import sys,sqlite3,ssl,ctypes' + "`n" +
             'print("FRIDAY_VER=" + sys.version.split()[0])' + "`n" +
             'print("FRIDAY_PLAT=" + sys.platform)' + "`n" +
             'print("FRIDAY_PATH=" + "|".join(sys.path))'
    $r = Invoke-Native -FilePath $exe -Arguments @('-c', $probe) -TimeoutSeconds 60
    if ($r.ExitCode -ne 0) {
        Write-Log "Interpreter probe exited $($r.ExitCode)" 'FAIL'
        return $false
    }

    $ver = ''; $plat = ''; $path = ''
    foreach ($line in ($r.Combined -split "`r?`n")) {
        $t = $line.Trim()
        if ($t.StartsWith('FRIDAY_VER='))  { $ver  = $t.Substring(11) }
        elseif ($t.StartsWith('FRIDAY_PLAT=')) { $plat = $t.Substring(12) }
        elseif ($t.StartsWith('FRIDAY_PATH=')) { $path = $t.Substring(12) }
    }
    if (-not $ver -or -not $plat -or -not $path) {
        Write-Log "Interpreter probe did not report all three facts (ver='$ver' plat='$plat' path len=$($path.Length))" 'FAIL'
        return $false
    }

    if ($ver -ne $ExpectedVersion) {
        Write-Log "Python version mismatch: wanted $ExpectedVersion, interpreter says $ver" 'FAIL'
        return $false
    }
    if ($plat -ne 'win32') {
        Write-Log "Python platform is '$plat', expected win32" 'FAIL'
        return $false
    }
    if ($AppSrcDir) {
        $normalisedWanted = ([System.IO.Path]::GetFullPath($AppSrcDir)).TrimEnd('\')
        $found = $false
        foreach ($p in ($path -split '\|')) {
            if (-not $p) { continue }
            try { $full = ([System.IO.Path]::GetFullPath($p)).TrimEnd('\') } catch { continue }
            if ($full -ieq $normalisedWanted) { $found = $true; break }
        }
        if (-not $found) {
            Write-Log "App source dir '$AppSrcDir' is not on the interpreter's sys.path - the ._pth was not applied." 'FAIL'
            return $false
        }
    }
    Write-Log "Python $ver ($plat) verified at $exe; app src on sys.path." 'OK'
    return $true
}

function Install-EmbeddedPython {
    <#  Unpack the embeddable zip and make it usable.
        $ZipPath may be a file shipped inside the installer folder (offline
        install) or one we downloaded. Either way the SHA-256 is checked
        against the pinned value before a single byte is extracted.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [Parameter(Mandatory)][string] $ZipPath,
        [Parameter(Mandatory)][string] $ExpectedSha256,
        [Parameter(Mandatory)][string] $PthStem,       # e.g. 'python312'
        [Parameter(Mandatory)][string] $AppSrcDir
    )

    Assert-FileHash -Path $ZipPath -ExpectedSha256 $ExpectedSha256 -What 'the Python download'

    $pyRoot = Get-PythonRoot $InstallRoot
    if (Test-Path -LiteralPath $pyRoot) {
        Write-Log "Removing previous python folder at $pyRoot"
        Remove-Item -LiteralPath $pyRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path $pyRoot | Out-Null

    Write-Log "Extracting $ZipPath -> $pyRoot"
    # Expand-Archive on PS 5.1 is slow but present everywhere. Worth the
    # seconds not to depend on 7-zip or tar.exe being available.
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $pyRoot -Force

    Set-PythonPathFile -InstallRoot $InstallRoot -PthStem $PthStem -AppSrcDir $AppSrcDir
}

function Set-PythonPathFile {
    <#  Write python3xx._pth.

        Order matters. `Lib\site-packages` must come before the app src so a
        real installed package always wins over anything shadowing it in the
        source tree. `import site` must be present or .pth files (and hence
        several packages' path hooks) are never processed.

        Paths are relative to the folder containing the ._pth file, which is
        why the app source is reached with '..\app\src' rather than absolutely
        - the whole install directory can be moved and still work.

        This function is also the repair target for the `repair_python_pth`
        remediation, so it must be safe to call repeatedly.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [Parameter(Mandatory)][string] $PthStem,
        [Parameter(Mandatory)][string] $AppSrcDir
    )
    $pyRoot = Get-PythonRoot $InstallRoot
    $pth    = Join-Path $pyRoot "$PthStem._pth"

    $rel = Get-RelativePath -From $pyRoot -To $AppSrcDir

    $content = @(
        "$PthStem.zip",
        ".",
        "Lib\site-packages",
        $rel,
        "",
        "# 'import site' MUST stay uncommented. Without it, .pth files in",
        "# site-packages are never processed and several packages break in",
        "# ways that look like unrelated import errors.",
        "import site"
    ) -join "`r`n"

    [System.IO.File]::WriteAllText($pth, $content + "`r`n", (New-Object System.Text.ASCIIEncoding))
    Write-Log "Wrote $pth (app src -> $rel)"
}

function Install-Pip {
    <# get-pip.py into the embedded interpreter. Hash-checked like everything
       else - this one executes as code, so it matters more than most. #>
    param(
        [Parameter(Mandatory)][string] $InstallRoot,
        [Parameter(Mandatory)][string] $GetPipPath,
        [string] $ExpectedSha256 = ''
    )
    if ($ExpectedSha256) {
        Assert-FileHash -Path $GetPipPath -ExpectedSha256 $ExpectedSha256 -What 'the pip bootstrap'
    } else {
        # No pin available (get-pip.py is a moving target upstream). Say so
        # loudly in the log rather than implying it was checked.
        Add-InstallWarning "get-pip.py was used WITHOUT a SHA-256 pin. It is fetched over TLS from bootstrap.pypa.io but its contents were not verified against a known hash."
    }
    $exe = Get-PythonExe $InstallRoot
    $r = Invoke-Native -FilePath $exe -Arguments @($GetPipPath, '--no-warn-script-location', '--no-cache-dir') -TimeoutSeconds 900
    return $r
}

function Test-PipWorking {
    param([Parameter(Mandatory)][string] $InstallRoot)
    $exe = Get-PythonExe $InstallRoot
    if (-not (Test-Path -LiteralPath $exe)) { return $false }
    $r = Invoke-Native -FilePath $exe -Arguments @('-m','pip','--version') -TimeoutSeconds 120
    if ($r.ExitCode -ne 0) { return $false }
    if ($r.StdOut -notmatch 'pip\s+\d+') { return $false }
    Write-Log "pip verified: $($r.StdOut.Trim())" 'OK'
    return $true
}

function Get-RelativePath {
    <# PS 5.1 has no [System.IO.Path]::GetRelativePath. Uri does the job. #>
    param([Parameter(Mandatory)][string] $From, [Parameter(Mandatory)][string] $To)
    $fromUri = New-Object System.Uri(($From.TrimEnd('\') + '\'))
    $toUri   = New-Object System.Uri($To)
    $rel     = $fromUri.MakeRelativeUri($toUri).ToString()
    return ([System.Uri]::UnescapeDataString($rel) -replace '/', '\')
}
