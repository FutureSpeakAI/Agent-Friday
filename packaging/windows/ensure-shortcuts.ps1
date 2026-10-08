#Requires -Version 5.1
<#
    Agent Friday - make sure the shortcuts landed.

    The setup program creates the Desktop and Start menu shortcuts itself and
    then checks that the files exist. When one does not, it runs this: it
    creates them again through the Windows shell's own idea of where the
    Desktop and Start menu are (which follows a OneDrive-redirected Desktop),
    and reports what is on disk afterwards.

    Exit 0 when both the Desktop and the Start menu shortcut exist, 1 if not.
    Writes <InstallRoot>\logs\shortcuts.json either way.
#>
[CmdletBinding()]
param([string] $InstallRoot = '')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir = Join-Path $Here 'lib'
if (-not (Test-Path -LiteralPath $LibDir)) { $LibDir = $Here }
. (Join-Path $LibDir 'Common.ps1')
. (Join-Path $LibDir 'Shortcuts.ps1')

if (-not $InstallRoot) {
    $guess = Split-Path -Parent $Here
    if (Test-Path -LiteralPath (Join-Path $guess 'install-manifest.json')) { $InstallRoot = $guess }
    else { $InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday' }
}
$LogDir = Join-Path $InstallRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Initialize-Log (Join-Path $LogDir 'shortcuts.log')

$icon = Join-Path $InstallRoot 'AgentFriday.ico'
if (-not (Test-Path -LiteralPath $icon)) { $icon = Join-Path $InstallRoot 'app\assets\icons\futurespeak.ico' }
if (-not (Test-Path -LiteralPath $icon)) { $icon = '' }

[void](Install-Shortcuts -InstallRoot $InstallRoot -IconPath $icon)

$desktopDir = Get-DesktopDir
$startDir = Get-StartMenuDir
$desktopLnk = ''; if ($desktopDir) { $desktopLnk = Join-Path $desktopDir 'Agent Friday.lnk' }
$startLnk = ''; if ($startDir) { $startLnk = Join-Path $startDir 'Agent Friday.lnk' }
$haveDesktop = [bool]($desktopLnk -and (Test-Path -LiteralPath $desktopLnk))
$haveStart = [bool]($startLnk -and (Test-Path -LiteralPath $startLnk))
Write-Log "Desktop shortcut $desktopLnk exists: $haveDesktop" $(if ($haveDesktop) { 'OK' } else { 'FAIL' })
Write-Log "Start menu shortcut $startLnk exists: $haveStart" $(if ($haveStart) { 'OK' } else { 'FAIL' })

$record = [ordered]@{ desktop = $desktopLnk; desktop_exists = $haveDesktop; start_menu = $startLnk; start_menu_exists = $haveStart }
[System.IO.File]::WriteAllText((Join-Path $LogDir 'shortcuts.json'), ($record | ConvertTo-Json),
                               (New-Object System.Text.UTF8Encoding($false)))
if ($haveDesktop -and $haveStart) { exit 0 }
exit 1
