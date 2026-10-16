<#
    For the setup program's Ready page: which installed files were changed
    since they were installed, so the page can say so BEFORE anything is
    replaced. The comparison is lib\AppFiles.ps1's, the same one install.ps1
    runs (and backs the files up with) a moment later.

    Output, one record per line (UTF-8):
        BASIS|manifest|install-time|none
        OVERRIDES|<folder the copies go to>
        FILE|modified|<path>      or      FILE|added|<path>
#>
param(
    [Parameter(Mandatory)][string] $AppDir,
    [Parameter(Mandatory)][string] $OutFile,
    [Parameter(Mandatory)][string] $LibPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
. $LibPath

$lines = New-Object System.Collections.Generic.List[string]
$found = Get-ChangedAppFiles -AppDir $AppDir
$lines.Add('BASIS|' + $found.Basis)
$lines.Add('OVERRIDES|' + (Get-FridayOverridesDir))
foreach ($r in $found.Modified) { $lines.Add('FILE|modified|' + $r) }
foreach ($r in $found.Added) { $lines.Add('FILE|added|' + $r) }
[System.IO.File]::WriteAllLines($OutFile, $lines.ToArray(), (New-Object System.Text.UTF8Encoding($true)))
exit 0
