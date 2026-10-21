#Requires -Version 5.1
<#
    The standard-user leg (north star §34.3-5): make a local Windows account
    that is NOT an administrator, whose name (and so whose profile folder) is
    not plain ASCII, and run verify-clean-machine.ps1 as that account. The
    setup program is per-user (PrivilegesRequired=lowest), so a person without
    administrator rights must be able to install, start and uninstall Friday.

    Runs only on a disposable CI runner (it creates and removes an account).
    The account's password is random, lives only in this process, and the
    account is removed at the end. This file is UTF-8 with a byte-order mark
    so Windows PowerShell 5.1 reads the account name as written.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Setup,
    [Parameter(Mandatory)][string] $WorkDir,
    [string] $UserName = "Zoë Ünïcode"
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'run-as-standard-user.ps1 creates a Windows account; it runs only on a disposable CI runner.' }

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path

# A copy of the setup program, the repository files the check reads, and a
# work folder, all where the new account can read and write.
$shared = Join-Path $env:SystemDrive 'friday-standard-user'
if (Test-Path $shared) { Remove-Item -Recurse -Force $shared }
New-Item -ItemType Directory -Force -Path $shared | Out-Null
Copy-Item -LiteralPath $Setup -Destination $shared
$setupCopy = Join-Path $shared (Split-Path -Leaf $Setup)
$treeCopy = Join-Path $shared 'repo'
foreach ($rel in @('pyproject.toml', 'packaging\windows\tests\clean-machine\verify-clean-machine.ps1')) {
    $dest = Join-Path $treeCopy $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo $rel) -Destination $dest
}
$work = Join-Path $shared 'work'
New-Item -ItemType Directory -Force -Path $work | Out-Null

# The runner proof the check asks for: the account cannot see GITHUB_ACTIONS.
$runId = [string]$env:GITHUB_RUN_ID
$marker = Join-Path $shared 'runner-marker.txt'
Set-Content -LiteralPath $marker -Value $runId -Encoding ascii

# The account: a member of Users only.
Add-Type -AssemblyName System.Web
$plain = [System.Web.Security.Membership]::GeneratePassword(24, 4) + 'aA1!'
$secure = ConvertTo-SecureString $plain -AsPlainText -Force
if (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $UserName }
New-LocalUser -Name $UserName -Password $secure -PasswordNeverExpires -AccountNeverExpires | Out-Null
Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-545').Name -Member $UserName
$adminGroup = (Get-LocalGroup -SID 'S-1-5-32-544').Name
$isAdminMember = @(Get-LocalGroupMember -Group $adminGroup | Where-Object { $_.Name -like "*\$UserName" }).Count -gt 0
if ($isAdminMember) { throw "$UserName is an administrator; this leg needs a standard account" }
icacls $shared /grant "${UserName}:(OI)(CI)M" /T | Out-Null

$cred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$UserName", $secure)
$psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
          (Join-Path $treeCopy 'packaging\windows\tests\clean-machine\verify-clean-machine.ps1'),
          '-Setup', $setupCopy, '-WorkDir', $work, '-Variant', 'standard-user',
          '-RunnerMarker', $marker, '-RunId', $runId)
$exit = 1
try {
    $p = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $psArgs -Credential $cred `
         -LoadUserProfile -WorkingDirectory $shared -PassThru -Wait `
         -RedirectStandardOutput (Join-Path $work 'leg.out.log') -RedirectStandardError (Join-Path $work 'leg.err.log')
    $exit = $p.ExitCode
}
finally {
    Get-ChildItem -LiteralPath $work -File -ErrorAction SilentlyContinue | Copy-Item -Destination $WorkDir -Force
    foreach ($f in @('leg.out.log', 'leg.err.log')) {
        $path = Join-Path $WorkDir $f
        if (Test-Path $path) { Get-Content -LiteralPath $path | Write-Host }
    }
    # Leave nothing of the account behind.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { try { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User -eq $UserName } catch { $false } } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-LocalUser -Name $UserName -ErrorAction SilentlyContinue
}
if (-not (Test-Path (Join-Path $WorkDir 'RESULT.json'))) { Write-Host 'FAILED: the standard-user leg wrote no RESULT.json'; exit 1 }
exit $exit
