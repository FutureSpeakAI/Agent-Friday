#Requires -Version 5.1
<#
    Agent Friday - Windows uninstaller :: LocalAddress.ps1

    Undoes the two system changes Friday's local address can make
    (services/local_address.py), which live outside both the install folder
    and ~/.friday:

      * a block in the hosts file between Friday's markers, mapping
        agent.<name> to 127.0.0.1 and ::1. Changing the hosts file needs
        administrator rights, so removing it needs Windows' permission prompt.
      * Friday's own certificate authority in the current user's Root store,
        added with `certutil -user -addstore Root`. Removing it needs no
        administrator rights; Windows asks the user to confirm.

    Only what Friday made is touched: the hosts block between its markers,
    and root certificates whose thumbprints appear in Friday's local-address
    folder (the current authority and the ones it replaced).
#>

$script:FridayHostsBegin = '# >>> Agent Friday local address >>>'
$script:FridayHostsEnd   = '# <<< Agent Friday local address <<<'

function Get-HostsFilePath {
    return (Join-Path $env:SystemRoot 'System32\drivers\etc\hosts')
}

function Remove-FridayHostsBlockText {
    <#  The hosts text without Friday's marked block. Pure; used by the
        elevated script and by tests. #>
    param([AllowEmptyString()][string] $Text)
    $rx = '(?ms)^' + [regex]::Escape($script:FridayHostsBegin) + '.*?^' +
          [regex]::Escape($script:FridayHostsEnd) + '[^\r\n]*(\r?\n)?'
    return [regex]::Replace($Text, $rx, '')
}

function Test-FridayHostsBlock {
    param([string] $HostsPath = (Get-HostsFilePath))
    if (-not (Test-Path -LiteralPath $HostsPath)) { return $false }
    try {
        $text = [IO.File]::ReadAllText($HostsPath)
    } catch { return $false }
    return $text.Contains($script:FridayHostsBegin)
}

function Get-FridayHostsRemovalScript {
    <#  The script the elevated PowerShell runs. It removes only the marked
        block, keeps the file's read-only flag, and flushes the DNS cache. #>
    $begin = $script:FridayHostsBegin
    $end = $script:FridayHostsEnd
    return @"
`$ErrorActionPreference = 'Stop'
`$hosts = Join-Path `$env:SystemRoot 'System32\drivers\etc\hosts'
if (-not (Test-Path -LiteralPath `$hosts)) { exit 0 }
`$text = [IO.File]::ReadAllText(`$hosts)
`$rx = '(?ms)^' + [regex]::Escape('$begin') + '.*?^' + [regex]::Escape('$end') + '[^\r\n]*(\r?\n)?'
`$new = [regex]::Replace(`$text, `$rx, '')
if (`$new -eq `$text) { exit 0 }
`$item = Get-Item -LiteralPath `$hosts -Force
`$ro = `$item.IsReadOnly
if (`$ro) { `$item.IsReadOnly = `$false }
[IO.File]::WriteAllText(`$hosts, `$new, (New-Object System.Text.UTF8Encoding(`$false)))
if (`$ro) { (Get-Item -LiteralPath `$hosts -Force).IsReadOnly = `$true }
ipconfig /flushdns | Out-Null
exit 0
"@
}

function Invoke-FridayHostsRemovalElevated {
    <#  Starts the removal elevated and waits. Returns $true when the block is
        gone afterwards. Declining the prompt returns $false. #>
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes((Get-FridayHostsRemovalScript)))
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    try {
        $p = Start-Process -FilePath $ps -Verb RunAs -PassThru -WindowStyle Hidden `
             -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-EncodedCommand', $enc)
        $null = $p.Handle
        $p.WaitForExit()
    } catch {
        Write-Log "Hosts removal was not started: $($_.Exception.Message)" 'WARN'
        return $false
    }
    return -not (Test-FridayHostsBlock)
}

function ConvertTo-PlainThumbprint {
    param([string] $Thumbprint)
    return (($Thumbprint -replace '[^0-9A-Fa-f]', '').ToUpperInvariant())
}

function Get-FridayCaThumbprints {
    <#  SHA-1 thumbprints of every authority Friday made on this account: the
        current ca.pem, and state.json's current and previous ones. #>
    param([Parameter(Mandatory)][string] $StateDir)
    $out = New-Object System.Collections.Generic.List[string]
    $ca = Join-Path $StateDir 'ca.pem'
    if (Test-Path -LiteralPath $ca) {
        try {
            $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ca)
            $out.Add((ConvertTo-PlainThumbprint $cert.Thumbprint))
        } catch { }
    }
    $state = Join-Path $StateDir 'state.json'
    if (Test-Path -LiteralPath $state) {
        try {
            $doc = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
            if ($doc.PSObject.Properties['ca_sha1'] -and $doc.ca_sha1) {
                $out.Add((ConvertTo-PlainThumbprint $doc.ca_sha1))
            }
            if ($doc.PSObject.Properties['previous_cas']) {
                foreach ($p in @($doc.previous_cas)) {
                    if ($p -and $p.PSObject.Properties['sha1'] -and $p.sha1) {
                        $out.Add((ConvertTo-PlainThumbprint $p.sha1))
                    }
                }
            }
        } catch { }
    }
    return @($out | Where-Object { $_.Length -eq 40 } | Sort-Object -Unique)
}

function Get-TrustedFridayThumbprints {
    <#  Which of Friday's authorities the current user's Root store holds. #>
    param([string[]] $Thumbprints)
    if (-not $Thumbprints -or $Thumbprints.Count -eq 0) { return @() }
    $trusted = @()
    try {
        $inStore = @(Get-ChildItem -Path 'Cert:\CurrentUser\Root' -ErrorAction Stop |
                     ForEach-Object { $_.Thumbprint.ToUpperInvariant() })
        $trusted = @($Thumbprints | Where-Object { $inStore -contains $_ })
    } catch { }
    return $trusted
}

function Remove-FridayTrustedCertificates {
    <#  certutil -user -delstore Root <thumbprint> for each of Friday's
        authorities the user store trusts. Windows asks the user to confirm
        each one. Returns the thumbprints still trusted afterwards. #>
    param([string[]] $Thumbprints, [int] $TimeoutSeconds = 180)
    $certutil = Join-Path $env:SystemRoot 'System32\certutil.exe'
    foreach ($t in $Thumbprints) {
        $r = Invoke-Native -FilePath $certutil -Arguments @('-user', '-delstore', 'Root', $t) -TimeoutSeconds $TimeoutSeconds
        Write-Log "certutil -user -delstore Root for one Friday certificate exited $($r.ExitCode)"
    }
    return @(Get-TrustedFridayThumbprints -Thumbprints $Thumbprints)
}
