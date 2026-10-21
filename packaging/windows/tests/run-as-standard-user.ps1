#Requires -Version 5.1
<#
    The fresh-install check, run by a person who is NOT an administrator.

    Most people's PCs run day to day as a standard user. The setup program
    installs per user (PrivilegesRequired=lowest, into %LOCALAPPDATA%), so it
    must install, start and uninstall without ever asking for elevation. The
    fresh-install leg runs as the runner's administrator account and cannot
    show that.

    This script, on a disposable CI runner only:
      1. creates a local account in Users and nowhere else, with a random
         password that is never printed;
      2. gives that account the one right a scheduled task needs to run as it
         (log on as a batch job); it gets no other right and no admin group;
      3. stages the setup program, verify-clean-install.ps1 and the two files
         it reads the expected release from (pyproject.toml, release.py) in a
         folder that account may read and write;
      4. runs verify-clean-install.ps1 -StandardUser as that account through
         Task Scheduler (a real logon with the account's own profile and
         environment, limited token), and waits;
      5. copies RESULT.json, MACHINE.json and the logs to -WorkDir and exits
         with the check's own exit code.

    A scheduled task, not Start-Process -Credential: the latter hands the new
    process this account's environment block (USERPROFILE, LOCALAPPDATA),
    which would install into the administrator's profile and prove nothing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Setup,
    [Parameter(Mandatory)][string] $WorkDir,
    [string] $UserName = 'fridaystd',
    [int] $TimeoutMinutes = 70
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'run-as-standard-user.ps1 creates a local account and installs as it; it runs only on a disposable CI runner.'
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# ── 1. The account ─────────────────────────────────────────────────────────
$bytes = New-Object byte[] 24
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$plain = 'Fs1!' + [Convert]::ToBase64String($bytes)
$secure = ConvertTo-SecureString $plain -AsPlainText -Force
if (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue) { throw "account $UserName already exists" }
New-LocalUser -Name $UserName -Password $secure -PasswordNeverExpires -AccountNeverExpires `
              -Description 'Agent Friday CI: standard-user install check' | Out-Null
$usersGroup = (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')).Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
$adminGroup = (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')).Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
Add-LocalGroupMember -Group $usersGroup -Member $UserName -ErrorAction SilentlyContinue
$sid = (Get-LocalUser -Name $UserName).SID.Value
$admins = @(Get-LocalGroupMember -Group $adminGroup -ErrorAction SilentlyContinue | Where-Object { $_.SID.Value -eq $sid })
if ($admins.Count) { throw "$UserName is in $adminGroup; this leg would prove nothing" }
Write-Host "standard account $UserName ($sid) created; member of $usersGroup only"

# ── 2. Log on as a batch job, and nothing else ─────────────────────────────
$inf = Join-Path $env:RUNNER_TEMP 'batch-right.inf'
$sdb = Join-Path $env:RUNNER_TEMP 'batch-right.sdb'
secedit /export /cfg $inf /areas USER_RIGHTS | Out-Null
$lines = Get-Content -LiteralPath $inf -Encoding Unicode
$found = $false
$lines = foreach ($l in $lines) {
    if ($l -match '^SeBatchLogonRight\s*=') { $found = $true; "$l,*$sid" } else { $l }
}
if (-not $found) {
    $out = New-Object System.Collections.ArrayList
    foreach ($l in $lines) { [void]$out.Add($l); if ($l -match '^\[Privilege Rights\]') { [void]$out.Add("SeBatchLogonRight = *$sid") } }
    $lines = $out
}
Set-Content -LiteralPath $inf -Value $lines -Encoding Unicode
secedit /configure /db $sdb /cfg $inf /areas USER_RIGHTS | Out-Null
if ($LASTEXITCODE -ne 0) { throw "could not grant the batch logon right (secedit exit $LASTEXITCODE)" }

# ── 3. Stage what the check needs where the account can reach it ───────────
$stage = 'C:\fridayci\std'
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'packaging\windows\tests'), (Join-Path $stage 'src\agent_friday'), (Join-Path $stage 'out') | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot 'pyproject.toml') -Destination $stage
Copy-Item -LiteralPath (Join-Path $RepoRoot 'src\agent_friday\release.py') -Destination (Join-Path $stage 'src\agent_friday')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'verify-clean-install.ps1') -Destination (Join-Path $stage 'packaging\windows\tests')
$setupCopy = Join-Path $stage (Split-Path -Leaf $Setup)
Copy-Item -LiteralPath $Setup -Destination $setupCopy
icacls $stage /grant "*${sid}:(OI)(CI)M" /T /Q | Out-Null

$out = Join-Path $stage 'out'
$cmd = Join-Path $stage 'run.cmd'
$verify = Join-Path $stage 'packaging\windows\tests\verify-clean-install.ps1'
$cmdText = @(
    '@echo off',
    'set GITHUB_ACTIONS=true',
    "powershell -NoProfile -ExecutionPolicy Bypass -File `"$verify`" -Setup `"$setupCopy`" -WorkDir `"$out`" -StandardUser > `"$out\run.log`" 2>&1",
    "echo %ERRORLEVEL%> `"$out\exit.txt`""
) -join "`r`n"
[System.IO.File]::WriteAllText($cmd, $cmdText + "`r`n", (New-Object System.Text.ASCIIEncoding))

# ── 4. Run it as the account ───────────────────────────────────────────────
$task = 'AgentFridayStandardUserCheck'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$cmd`""
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes $TimeoutMinutes) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -User $UserName -Password $plain -RunLevel Limited | Out-Null
$plain = $null
Start-ScheduledTask -TaskName $task
Write-Host "started the check as $UserName; waiting up to $TimeoutMinutes minutes"

$exitFile = Join-Path $out 'exit.txt'
$deadline = (Get-Date).AddMinutes($TimeoutMinutes + 2)
$seen = 0
while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $exitFile)) {
    Start-Sleep -Seconds 15
    $log = Join-Path $out 'run.log'
    if (Test-Path -LiteralPath $log) {
        $all = @(Get-Content -LiteralPath $log -ErrorAction SilentlyContinue)
        if ($all.Count -gt $seen) { $all[$seen..($all.Count - 1)] | ForEach-Object { Write-Host "  | $_" }; $seen = $all.Count }
    }
    $info = Get-ScheduledTaskInfo -TaskName $task -ErrorAction SilentlyContinue
    $state = (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue).State
    if ($state -eq 'Ready' -and $info -and $info.LastTaskResult -ne 267009 -and $info.LastRunTime -gt [datetime]'2000-01-01' -and -not (Test-Path -LiteralPath $exitFile)) {
        Start-Sleep -Seconds 5
        if (-not (Test-Path -LiteralPath $exitFile)) {
            Write-Host "the task ended without writing an exit code (last result $($info.LastTaskResult))"
            break
        }
    }
}
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue

# ── 5. Evidence and verdict ────────────────────────────────────────────────
Get-ChildItem -LiteralPath $out -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $WorkDir -Force }
if (-not (Test-Path -LiteralPath $exitFile)) { Write-Host 'FAILED: the standard-user check did not finish'; exit 1 }
$code = [int]((Get-Content -LiteralPath $exitFile -Raw).Trim())
Write-Host "standard-user check exit code: $code"
exit $code
