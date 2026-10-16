# Authenticode-sign the setup program with Microsoft Artifact Signing, when a
# certificate is configured; otherwise say so and leave the file unsigned.
#
# Configured means these environment variables are set (the installer workflow
# passes them from repository secrets; none exist until the owner sets up an
# Artifact Signing account):
#   ARTIFACT_SIGNING_ENDPOINT   e.g. https://eus.codesigning.azure.net/
#   ARTIFACT_SIGNING_ACCOUNT    the Artifact Signing account name
#   ARTIFACT_SIGNING_PROFILE    the certificate profile name
#   AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
#                               an app registration with the signer role
#
# Signing changes the file, so the .sha256 beside it is written again after.
# A signature that does not verify fails the build: an installer is either
# properly signed or plainly unsigned, never half of each.
param(
    [Parameter(Mandatory = $true)][string]$Path
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path)) { throw "no file at $Path" }
$need = 'ARTIFACT_SIGNING_ENDPOINT', 'ARTIFACT_SIGNING_ACCOUNT', 'ARTIFACT_SIGNING_PROFILE',
        'AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET'
$missing = @($need | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
if ($missing.Count -eq $need.Count) {
    Write-Host "Authenticode signing is not configured (no certificate yet): $(Split-Path -Leaf $Path) ships unsigned, with its SHA-256."
    exit 0
}
if ($missing.Count -gt 0) {
    throw "Authenticode signing is half configured; missing: $($missing -join ', ')"
}

# signtool from the Windows SDK on the runner.
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw 'signtool.exe (Windows SDK) was not found' }

# The Artifact Signing client library that signtool loads.
$work = Join-Path $env:RUNNER_TEMP 'artifact-signing'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$pkg = Join-Path $work 'client.zip'
Invoke-WebRequest -UseBasicParsing -Uri 'https://www.nuget.org/api/v2/package/Microsoft.Trusted.Signing.Client' -OutFile $pkg
Expand-Archive -LiteralPath $pkg -DestinationPath (Join-Path $work 'client') -Force
$dlib = Get-ChildItem (Join-Path $work 'client') -Recurse -Filter 'Azure.CodeSigning.Dlib.dll' |
    Where-Object { $_.FullName -match '\\x64\\' } | Select-Object -First 1
if (-not $dlib) { throw 'Azure.CodeSigning.Dlib.dll was not found in the client package' }

$meta = Join-Path $work 'metadata.json'
@{ Endpoint = $env:ARTIFACT_SIGNING_ENDPOINT; CodeSigningAccountName = $env:ARTIFACT_SIGNING_ACCOUNT;
   CertificateProfileName = $env:ARTIFACT_SIGNING_PROFILE } | ConvertTo-Json | Set-Content -Encoding utf8 $meta

& $signtool.FullName sign /v /fd SHA256 /tr 'http://timestamp.acs.microsoft.com' /td SHA256 `
    /dlib $dlib.FullName /dmdf $meta $Path
if ($LASTEXITCODE -ne 0) { throw "signtool failed ($LASTEXITCODE)" }

$sig = Get-AuthenticodeSignature -LiteralPath $Path
if ($sig.Status -ne 'Valid') { throw "the signature does not verify: $($sig.Status) $($sig.StatusMessage)" }
Write-Host "Signed by: $($sig.SignerCertificate.Subject)"

$hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText("$Path.sha256", "$hash  $(Split-Path -Leaf $Path)`n", (New-Object System.Text.ASCIIEncoding))
Write-Host "SHA-256 after signing: $hash"
