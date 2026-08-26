#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Ollama.ps1

    Ollama is the thing that actually runs Friday's local models. Without it
    she cannot hold a conversation without a cloud key, which is most of the
    point of her.

    THE HONEST POSITION ON SILENT INSTALLATION
    ------------------------------------------
    There is no documented, guaranteed silent-install contract for Ollama on
    Windows. What exists:

      * `winget install Ollama.Ollama --silent` - works when winget is present
        and the source is reachable. Windows 11 ships winget, but it can be
        absent on a freshly imaged machine until App Installer updates itself,
        and it can be disabled by policy.
      * OllamaSetup.exe - historically an Inno Setup package, which accepts
        /VERYSILENT /NORESTART /SUPPRESSMSGBOXES. Upstream has an open issue
        (ollama/ollama#7969) titled "Administrative / silent install is
        borked", and a separate discussion (#15038) about moving to NSIS,
        whose silent switch is /S instead. So the correct switch depends on a
        build we do not control and cannot pin.

    Therefore this module does NOT assume. It tries, in order, and after every
    attempt it goes and looks for a working `ollama` binary. If none of the
    attempts produce one, it does not fail the install and it does not lie: it
    tells her, in one sentence, to install Ollama from a link, records the
    situation for Stephen, and lets the rest of setup complete. Friday with a
    cloud key and no local model is a reduced Friday, not a broken one.
#>

Set-StrictMode -Version 2.0

$script:OllamaDefaultDir = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'

function Get-OllamaExe {
    <#  Find ollama.exe. PATH first, then the known per-user install location,
        because a freshly-installed Ollama is not on the PATH of an already-
        running PowerShell session - the classic "I installed it, why does it
        say not recognized" trap. We look at disk, not just at PATH. #>
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }

    $candidates = @(
        (Join-Path $script:OllamaDefaultDir 'ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Ollama\ollama.exe')
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

function Test-OllamaInstalled {
    <# Verification: the binary exists AND answers. A file on disk that will
       not run is not an installation. #>
    $exe = Get-OllamaExe
    if (-not $exe) { return $false }
    $r = Invoke-Native -FilePath $exe -Arguments @('--version') -TimeoutSeconds 30
    # `ollama --version` prints a version and exits 0 when the daemon is up,
    # and also prints a version with a warning when it is down. Either is fine
    # here - this step asks "is it installed", not "is it running".
    $text = $r.Combined
    if ($text -match '\d+\.\d+') {
        Write-Log "Ollama found at $exe : $(($text -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1))" 'OK'
        return $true
    }
    Write-Log "Ollama binary at $exe did not report a version (exit $($r.ExitCode))" 'WARN'
    return $false
}

function Test-OllamaRunning {
    <# Ask the daemon directly rather than looking for a process name. #>
    param([int] $TimeoutSeconds = 3)
    $host_ = $env:OLLAMA_HOST
    if (-not $host_) { $host_ = 'http://localhost:11434' }
    if ($host_ -notmatch '^https?://') { $host_ = "http://$host_" }
    try {
        Initialize-Tls
        $req = [System.Net.HttpWebRequest]::Create(($host_.TrimEnd('/') + '/api/tags'))
        $req.Timeout = $TimeoutSeconds * 1000
        $req.Method = 'GET'
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch {
        return $false
    }
}

function Start-OllamaDaemon {
    <# Best effort. The Windows build normally starts a tray app on login;
       if it is not up we nudge `ollama serve` in the background. #>
    $exe = Get-OllamaExe
    if (-not $exe) { return $false }
    if (Test-OllamaRunning) { return $true }

    # The desktop app (ollama app.exe) is the supported way to bring it up on
    # Windows; fall back to `serve` if the app is not present.
    $appExe = Join-Path (Split-Path -Parent $exe) 'ollama app.exe'
    try {
        if (Test-Path -LiteralPath $appExe) {
            Write-Log "Starting Ollama desktop app: $appExe"
            Start-Process -FilePath $appExe -WindowStyle Hidden -ErrorAction Stop | Out-Null
        } else {
            Write-Log "Starting: $exe serve"
            Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden -ErrorAction Stop | Out-Null
        }
    } catch {
        Write-Log "Could not start Ollama: $($_.Exception.Message)" 'WARN'
        return $false
    }

    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-OllamaRunning) { Write-Log "Ollama daemon is up after $($i+1)s" 'OK'; return $true }
    }
    Write-Log "Ollama daemon did not come up within 30s" 'WARN'
    return $false
}

function Install-Ollama {
    <#  Try each method, verifying after each. Returns a hashtable describing
        what happened, which the uninstaller consults later - we only offer to
        remove Ollama if WE put it there.
    #>
    param(
        [string] $InstallerUrl = 'https://ollama.com/download/OllamaSetup.exe',
        [string] $CacheDir     = $env:TEMP,
        [string] $ExpectedSha256 = ''
    )

    $outcome = @{ Installed = $false; Method = 'none'; WeInstalledIt = $false }

    if (Test-OllamaInstalled) {
        $outcome.Installed = $true
        $outcome.Method    = 'already-present'
        Write-Log 'Ollama was already installed; leaving it alone.' 'OK'
        return $outcome
    }

    # --- 1. winget ------------------------------------------------------
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Say-Working 'Installing the part that runs models on this laptop.'
        $r = Invoke-Native -FilePath $winget.Source -Arguments @(
            'install','--id','Ollama.Ollama','-e',
            '--silent','--accept-source-agreements','--accept-package-agreements',
            '--disable-interactivity'
        ) -TimeoutSeconds 1800
        Add-OllamaDirToProcessPath
        if (Test-OllamaInstalled) {
            $outcome.Installed = $true; $outcome.Method = 'winget'; $outcome.WeInstalledIt = $true
            Write-Log 'Ollama installed via winget.' 'OK'
            return $outcome
        }
        Write-Log "winget did not produce a working ollama (exit $($r.ExitCode)); falling through to the direct installer." 'WARN'
    } else {
        Write-Log 'winget is not available on this machine.' 'WARN'
    }

    # --- 2. Direct installer, both known silent-switch dialects ---------
    $setup = Join-Path $CacheDir 'OllamaSetup.exe'
    if (-not (Test-Path -LiteralPath $setup)) {
        Say-Working 'Downloading the part that runs models on this laptop (about 700 MB).'
        if (-not (Get-RemoteFile -Uri $InstallerUrl -OutFile $setup -FriendlyName 'the local model engine')) {
            Write-Log 'Could not download the Ollama installer.' 'WARN'
            return $outcome
        }
    }
    if ($ExpectedSha256) { Assert-FileHash -Path $setup -ExpectedSha256 $ExpectedSha256 -What 'the Ollama installer' }
    else { Add-InstallWarning "The Ollama installer was NOT hash-verified - upstream publishes no stable per-release hash at a fixed URL, so there is nothing to pin against. It was fetched over TLS from ollama.com." }

    # Inno Setup dialect, then NSIS dialect. We verify after each rather than
    # trusting the exit code, because a silent installer that does nothing at
    # all commonly exits 0.
    $dialects = @(
        @{ Name = 'inno'; Args = @('/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES','/NOCANCEL') },
        @{ Name = 'nsis'; Args = @('/S') }
    )
    foreach ($d in $dialects) {
        Say-Working 'Setting up the local model engine.'
        $r = Invoke-Native -FilePath $setup -Arguments $d.Args -TimeoutSeconds 1800
        Write-Log "Ollama installer ($($d.Name) switches) exited $($r.ExitCode)"
        Start-Sleep -Seconds 3
        Add-OllamaDirToProcessPath
        if (Test-OllamaInstalled) {
            $outcome.Installed = $true; $outcome.Method = "setup-exe/$($d.Name)"; $outcome.WeInstalledIt = $true
            Write-Log "Ollama installed via OllamaSetup.exe with $($d.Name) switches." 'OK'
            return $outcome
        }
    }

    Write-Log 'Every automatic method for installing Ollama failed.' 'FAIL'
    return $outcome
}

function Add-OllamaDirToProcessPath {
    <# A just-installed Ollama is not on the PATH this process inherited.
       Add it for the remainder of the install so the very next check can see
       it. Process scope only - we do not touch the user's persistent PATH,
       because the Ollama installer owns that and the uninstaller should not
       have to guess who wrote what. #>
    $dirs = @($script:OllamaDefaultDir, (Join-Path $env:ProgramFiles 'Ollama'))
    foreach ($d in $dirs) {
        if ($d -and (Test-Path -LiteralPath $d) -and ($env:PATH -notlike "*$d*")) {
            $env:PATH = "$env:PATH;$d"
            Write-Log "Added $d to this process's PATH"
        }
    }
}

function Show-ManualOllamaInstruction {
    <#  The graceful continue. This is the wording she sees. It names one
        thing to do, one place to do it, and says plainly what she loses
        until she does - no exit codes, no "see the log". #>
    Say-Note 'One part could not be installed automatically.'
    Say ''
    Say '  Friday needs a free program called Ollama to run AI on this laptop'
    Say '  instead of over the internet. Setup could not install it for you.'
    Say ''
    Say "  $($script:C.Bold)To finish this part later:$($script:C.Reset) go to https://ollama.com/download"
    Say '  click Download for Windows, run it, then start Friday again.'
    Say ''
    Say '  Everything else is being set up now. Friday will still work with'
    Say '  an internet AI key - she just cannot run privately on this laptop'
    Say '  until Ollama is there.'
    Say ''
    Add-InstallWarning ('OLLAMA NOT INSTALLED. Automatic installation failed via every method ' +
                        '(winget, OllamaSetup /VERYSILENT, OllamaSetup /S). The user was given ' +
                        'the manual download link and the install continued. Local models are ' +
                        'unavailable until she does it by hand.')
}

function Get-OllamaInstalledModels {
    $exe = Get-OllamaExe
    if (-not $exe) { return ,@() }
    $r = Invoke-Native -FilePath $exe -Arguments @('list') -TimeoutSeconds 60
    if ($r.ExitCode -ne 0) { return ,@() }
    $names = @()
    foreach ($line in ($r.StdOut -split "`r?`n")) {
        if ($line -match '^\s*NAME\s') { continue }
        if (-not $line.Trim()) { continue }
        $names += ($line -split '\s+')[0]
    }
    return ,@($names)   # comma: keep it an array even with 0 or 1 element
}

function Test-OllamaHasModel {
    <#  Matching rule copied deliberately from cli.py::_has_model, including
        the reason it is written this way: a BARE family name ("gemma3")
        matches any tag of that family, but a SPECIFIC tag ("gemma3:4b") must
        match exactly, allowing only Ollama's quantisation suffixes. The old
        prefix-match said yes to gemma4:e2b when only gemma4:12b was present,
        and the next call 404'd. Comparing the shape of an identifier instead
        of resolving what it points at is the same defect three times over in
        this codebase; do not reintroduce it here.
    #>
    param([Parameter(Mandatory)][string] $Tag, [string[]] $Installed = $null)
    if ($Installed -eq $null) { $Installed = Get-OllamaInstalledModels }
    if ($Tag -notmatch ':') {
        foreach ($m in $Installed) { if (($m -split ':')[0] -eq $Tag) { return $true } }
        return $false
    }
    foreach ($m in $Installed) {
        if ($m -eq $Tag) { return $true }
        if ($m.StartsWith($Tag + '-')) { return $true }
    }
    return $false
}


