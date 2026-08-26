#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Download.ps1

    Fetching things from the internet, and refusing to use them if they are
    not what we asked for.

    Every downloaded artefact that we then EXECUTE (the Python distribution,
    the pip bootstrap, the Ollama installer) is pinned by SHA-256 in
    sources.json where a pin is available. A pin that cannot be checked is
    reported as a warning in Stephen's report rather than silently skipped -
    "we did not verify this" is information he is entitled to.
#>

Set-StrictMode -Version 2.0

function Initialize-Tls {
    <# Windows PowerShell 5.1 defaults to a TLS setting that some hosts no
       longer accept, producing a bewildering "could not create SSL/TLS
       secure channel" error. Force modern TLS before any download. #>
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    } catch {
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
    }
}

function Get-RemoteFile {
    <#  Download with progress she can understand, retries, and no exceptions
        leaking to the console. Returns $true/$false; the caller verifies.

        Uses HttpClient rather than Invoke-WebRequest because IWR on PS 5.1
        buffers the entire response in memory (a 3 GB model installer would
        be unpleasant) and its progress bar cannot be made to say anything
        human.
    #>
    param(
        [Parameter(Mandatory)][string] $Uri,
        [Parameter(Mandatory)][string] $OutFile,
        [string] $FriendlyName = 'a file',
        [int]    $Retries = 3,
        [int]    $TimeoutSeconds = 1800
    )

    Initialize-Tls
    $dir = Split-Path -Parent $OutFile
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue }
        Write-Log "Downloading (attempt $attempt/$Retries): $Uri -> $OutFile"

        $client = $null
        $stream = $null
        $file   = $null
        try {
            Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
            $client = New-Object System.Net.Http.HttpClient
            $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
            $client.DefaultRequestHeaders.UserAgent.ParseAdd('AgentFridayInstaller/1.0')

            $resp = $client.GetAsync($Uri, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            if (-not $resp.IsSuccessStatusCode) {
                Write-Log "HTTP $([int]$resp.StatusCode) for $Uri" 'WARN'
                continue
            }
            $total = 0
            if ($resp.Content.Headers.ContentLength) { $total = [int64]$resp.Content.Headers.ContentLength }

            $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $file   = [System.IO.File]::Create($OutFile)
            $buf    = New-Object byte[] 262144
            $read   = 0
            $done   = [int64]0
            $lastPct = -1
            while (($read = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
                $file.Write($buf, 0, $read)
                $done += $read
                if ($total -gt 0) {
                    $pct = [int](($done * 100) / $total)
                    if ($pct -ne $lastPct -and ($pct % 5) -eq 0) {
                        $lastPct = $pct
                        $mb = [math]::Round($total / 1MB, 0)
                        Write-Progress -Activity "Downloading $FriendlyName" -Status "$pct% of $mb MB" -PercentComplete $pct
                    }
                }
            }
            $file.Close(); $file = $null
            $stream.Close(); $stream = $null
            Write-Progress -Activity "Downloading $FriendlyName" -Completed

            $size = (Get-Item -LiteralPath $OutFile).Length
            if ($total -gt 0 -and $size -ne $total) {
                Write-Log "Short download: got $size bytes, expected $total" 'WARN'
                continue
            }
            Write-Log "Downloaded $size bytes to $OutFile" 'OK'
            return $true
        }
        catch {
            Write-Log "Download failed: $($_.Exception.Message)" 'WARN'
            Start-Sleep -Seconds ([math]::Min(15, $attempt * 4))
        }
        finally {
            if ($file)   { try { $file.Close() }   catch { } }
            if ($stream) { try { $stream.Close() } catch { } }
            if ($client) { try { $client.Dispose() } catch { } }
        }
    }

    Write-Progress -Activity "Downloading $FriendlyName" -Completed
    Write-Log "Giving up on $Uri after $Retries attempts" 'FAIL'
    return $false
}

function Get-Sha256 {
    param([Parameter(Mandatory)][string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Test-FileHash {
    param([Parameter(Mandatory)][string] $Path, [Parameter(Mandatory)][string] $ExpectedSha256)
    if (-not $ExpectedSha256) { return $true }
    $actual = Get-Sha256 $Path
    return ($actual -eq $ExpectedSha256.ToUpperInvariant())
}

function Assert-FileHash {
    <#  Refuse to continue on a hash mismatch. This is the one place in the
        installer that stops hard without offering to heal: a file that is not
        the file we pinned is either corrupt or substituted, and "try to fix
        it automatically" is precisely the wrong instinct in both cases. The
        healing menu can re-FETCH a download (refetch_download); it cannot
        wave through a bad one.
    #>
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][string] $ExpectedSha256,
        [string] $What = 'a downloaded file'
    )
    if (-not $ExpectedSha256) {
        Add-InstallWarning "No SHA-256 pin available for $What ($Path) - contents were NOT verified."
        return
    }
    $actual = Get-Sha256 $Path
    if ($actual -ne $ExpectedSha256.ToUpperInvariant()) {
        Write-Log "HASH MISMATCH for $Path : expected $ExpectedSha256, got $actual" 'FAIL'
        Say-Problem -What ("A file that setup downloaded is not the file it was expecting. " +
                           "Setup has stopped rather than use it. This usually means the " +
                           "download was interrupted, but it can also mean something on the " +
                           "network interfered with it.") `
                    -WhatToDo ("Check the internet connection and run the installer again. " +
                               "If it happens twice, stop rather than try a third time - a file " +
                               "that keeps arriving wrong is worth looking into before it is used.")
        Complete-Install -Failed -FailedStep 'download.integrity'
        exit 1
    }
    Write-Log "SHA-256 verified for $What : $actual" 'OK'
}
