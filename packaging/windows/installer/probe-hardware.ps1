#Requires -Version 5.1
<#
    Agent Friday - installer :: probe-hardware.ps1

    Run by the setup wizard's model page. Reads this computer (memory, graphics
    card, free disk), works out which Bonsai models fit, and writes them to a
    plain text file the wizard reads. Nothing is sent anywhere.

    ModelPicker.ps1 sits beside this script when the wizard has extracted both,
    and in ..\lib in the repository.
    -FactsJson replaces the detection with fixed facts, for tests.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $OutFile,
    [Parameter(Mandatory)][string] $ShortlistPath,
    [string] $ModelsPath = '',
    [string] $FactsJson = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$picker = Join-Path $PSScriptRoot 'ModelPicker.ps1'
if (-not (Test-Path -LiteralPath $picker)) { $picker = Join-Path $PSScriptRoot '..\lib\ModelPicker.ps1' }
. $picker

if ($FactsJson) {
    $obj = Get-Content -LiteralPath $FactsJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $facts = [ordered]@{}
    foreach ($p in $obj.PSObject.Properties) { $facts[$p.Name] = $p.Value }
} else {
    $facts = Get-HardwareFacts -ModelsPath $ModelsPath
}

$options = Get-ModelOptions -Facts $facts -ShortlistPath $ShortlistPath
Write-ModelOptionsFile -Facts $facts -Options $options -OutFile $OutFile
exit 0
