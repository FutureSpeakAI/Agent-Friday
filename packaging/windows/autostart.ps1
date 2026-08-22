#Requires -Version 5.1
<#
    Agent Friday - turn the automatic start on or off.

    Reached from Start menu > Agent Friday > "Start Friday when I sign in".

    This exists as its own visible, named thing because the brief said any
    autostart must be something she can turn off. A setting she cannot find is
    not optional in any sense that matters, and "edit the registry" is not an
    answer for someone who is not technical.

    It shows the current state before asking, so she can check without
    changing anything.
#>

[CmdletBinding()]
param([string] $InstallRoot = '')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir = Join-Path $Here 'lib'
if (-not (Test-Path $LibDir)) { $LibDir = $Here }

. (Join-Path $LibDir 'Common.ps1')
. (Join-Path $LibDir 'Shortcuts.ps1')

Initialize-Console
Initialize-Log (Join-Path $env:TEMP 'AgentFriday-autostart.log')

if (-not $InstallRoot) {
    $guess = Split-Path -Parent $Here
    if (Test-Path -LiteralPath (Join-Path $guess 'install-manifest.json')) { $InstallRoot = $guess }
    else { $InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday' }
}

$icon = Join-Path $InstallRoot 'app\assets\friday.ico'
if (-not (Test-Path -LiteralPath $icon)) { $icon = '' }

Say-Banner
$on = Test-Autostart

Say "$($script:C.Bold)Starting Friday automatically$($script:C.Reset)"
Say ''
if ($on) {
    Say "  Right now: $($script:C.Green)ON$($script:C.Reset) - Friday starts quietly when you sign in."
} else {
    Say "  Right now: $($script:C.Grey)OFF$($script:C.Reset) - Friday only starts when you open her."
}
Say ''

$prompt = '  Turn it ON? [y/N]'
if ($on) { $prompt = '  Turn it OFF? [y/N]' }
$answer = Read-Host $prompt

if ($answer -notmatch '^[Yy]') {
    Say ''
    Say '  Nothing changed.'
    Say ''
    Read-Host '  Press Enter to close' | Out-Null
    exit 0
}

if ($on) {
    [void](Disable-Autostart)
    # Verify, do not assume. Same rule as everywhere else in this installer.
    if (Test-Autostart) {
        Say ''
        Say-Note 'That did not work - Friday will still start when you sign in.'
        Say '        You can remove it by hand: press Windows key and R together,'
        Say '        type  shell:startup  and press Enter, then delete the'
        Say '        Agent Friday shortcut in the folder that opens.'
    } else {
        Say ''
        Say-Ok 'Turned off. Friday will only start when you open her.'
    }
} else {
    [void](Enable-Autostart -InstallRoot $InstallRoot -IconPath $icon)
    if (Test-Autostart) {
        Say ''
        Say-Ok 'Turned on. Friday will start quietly when you sign in.'
        Say '        Come back here any time to turn it off.'
    } else {
        Say ''
        Say-Note 'That did not work - Friday will not start automatically.'
        Say '        Everything else is unaffected. Try again, or tell Stephen.'
    }
}

Say ''
Read-Host '  Press Enter to close' | Out-Null
exit 0
