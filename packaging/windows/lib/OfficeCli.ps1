#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: OfficeCli.ps1

    OfficeCLI (https://github.com/iOfficeAI/OfficeCLI, Apache License 2.0) is
    the engine Friday uses to create and edit .docx, .xlsx and .pptx files on
    this PC (src/agent_friday/services/office_engine.py).

    THE PIN LIVES HERE, NOT IN THE DOWNLOAD. The release version, size and
    SHA-256 below are part of the installer's own source, checked against
    GitHub's published asset digest and the release's SHA256SUMS when they
    were set. The downloaded file must match them before it is placed where
    Friday will run it; a mismatch is discarded, never used. Upstream publishes
    no signature, so the checksum is the whole of the integrity check, and it
    is only as good as the moment the pin was taken.

    Friday re-verifies the same SHA-256 from INSTALL.json before the first run
    in every process, and sets OFFICECLI_SKIP_UPDATE on every call. This build
    has no updater; the switch keeps a later one off.

    Optional: without OfficeCLI, Friday says the document engine is not
    installed when asked for an Office file. Nothing else depends on it.
#>

$script:OfficeCliVersion = 'v1.0.152'
$script:OfficeCliAsset   = 'officecli-win-x64.exe'
$script:OfficeCliUrl     = 'https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.152/officecli-win-x64.exe'
$script:OfficeCliSize    = 33460136
$script:OfficeCliSha256  = '047705402974c3690a4437e55f620d03afac4beba4fdd28fdb59af610a3afff2'

function Get-OfficeCliDir {
    return (Join-Path $env:USERPROFILE '.friday\runtime\officecli')
}

function Test-OfficeCliInstalled {
    <#  True when officecli.exe is in place, matches the pin, and INSTALL.json
        records the same checksum Friday will check it against. #>
    $dir = Get-OfficeCliDir
    $exe = Join-Path $dir 'officecli.exe'
    $rec = Join-Path $dir 'INSTALL.json'
    if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $rec)) { return $false }
    if (-not (Test-FileHash -Path $exe -ExpectedSha256 $script:OfficeCliSha256)) { return $false }
    try {
        $pinned = [string]((Get-Content -LiteralPath $rec -Raw | ConvertFrom-Json).sha256)
    } catch { return $false }
    return ($pinned.ToLowerInvariant() -eq $script:OfficeCliSha256)
}

function Install-OfficeCli {
    <#  Download the pinned release, verify it, then place it and write
        INSTALL.json. Returns $true when OfficeCLI is ready. #>
    if (Test-OfficeCliInstalled) {
        Write-Log "OfficeCLI $($script:OfficeCliVersion) already installed and verified." 'OK'
        return $true
    }
    $dir  = Get-OfficeCliDir
    $part = Join-Path $dir 'officecli.exe.download'
    $exe  = Join-Path $dir 'officecli.exe'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    if (-not (Get-RemoteFile -Uri $script:OfficeCliUrl -OutFile $part -FriendlyName 'the document engine' -TimeoutSeconds 900)) {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        return $false
    }
    $size = (Get-Item -LiteralPath $part).Length
    if ($size -ne $script:OfficeCliSize -or -not (Test-FileHash -Path $part -ExpectedSha256 $script:OfficeCliSha256)) {
        Write-Log "OfficeCLI download does not match its pin (size $size, sha256 $(Get-Sha256 $part)); discarded." 'FAIL'
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        return $false
    }
    Move-Item -LiteralPath $part -Destination $exe -Force

    $record = [ordered]@{
        tool           = 'officecli'
        source         = 'https://github.com/iOfficeAI/OfficeCLI'
        license        = 'Apache-2.0'
        pinned_version = $script:OfficeCliVersion
        asset          = $script:OfficeCliAsset
        installed_as   = 'officecli.exe'
        size_bytes     = $script:OfficeCliSize
        sha256         = $script:OfficeCliSha256
        sha256_source  = 'pinned in the Agent Friday installer (packaging/windows/lib/OfficeCli.ps1)'
        signature      = $null
        auto_update    = 'disabled'
        installed_at   = (Get-Date -Format 'yyyy-MM-dd')
        installed_by   = 'Agent Friday installer'
    }
    [System.IO.File]::WriteAllText((Join-Path $dir 'INSTALL.json'),
                                   ($record | ConvertTo-Json -Depth 3),
                                   (New-Object System.Text.UTF8Encoding($false)))
    Write-Log "OfficeCLI $($script:OfficeCliVersion) installed to $exe and verified." 'OK'
    return (Test-OfficeCliInstalled)
}
