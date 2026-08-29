#Requires -Version 5.1
<#
    Published-asset upgrade rehearsal: does an in-place upgrade destroy the
    user's vault passphrase?

    The passphrase lives ONLY in <InstallRoot>\app\start.bat (setup_wizard.py
    writes it there and refuses to put it in settings). app.copy deletes
    $AppDir recursively. Before 5.6.5 app.copy never ran on an upgrade, so the
    file survived by accident. 5.6.5 made it run.

    This installs a published BASE zip, mints a real passphrase through the
    wizard's OWN _write_start_bat, encrypts a real vault artifact under the
    key that passphrase derives, then upgrades with the UPGRADE zip and asks
    three questions:

        1. did the app code actually update?      (the 5.6.5 fix must not regress)
        2. did start.bat survive?
        3. does the vault still decrypt?

    -Unattended is deliberate. It skips the setup wizard at step 12, which
    isolates the ONE thing under test: whether app.copy destroys the file.
    The wizard's own re-run behaviour (step_vault_password minting a new
    passphrase from a default-Yes prompt) is a separate defect with a separate
    test; mixing them would leave neither proven.

    ISOLATION IS ASSERTED, NOT ASSUMED. If the redirected profile does not
    take, this script stops before it can touch the real ~/.friday.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $BaseZip,
    [Parameter(Mandatory)][string] $UpgradeZip,
    [Parameter(Mandatory)][string] $Root,
    [Parameter(Mandatory)][string] $Label
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

function Note($m) { Write-Host ("[{0}] {1}" -f $Label, $m) }

# --- Layout ---------------------------------------------------------------
$InstallRoot = Join-Path $Root 'install'
$FakeHome    = Join-Path $Root 'home'
$BaseDir     = Join-Path $Root 'base'
$UpDir       = Join-Path $Root 'up'
$Result      = Join-Path $Root 'RESULT.json'

foreach ($d in @($Root, $FakeHome, $BaseDir, $UpDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$RealHome = $env:USERPROFILE
Note "real profile   : $RealHome"
Note "redirected to  : $FakeHome"

# --- Extract both assets --------------------------------------------------
Note "extracting base    : $(Split-Path -Leaf $BaseZip)"
Expand-Archive -LiteralPath $BaseZip -DestinationPath $BaseDir -Force
Note "extracting upgrade : $(Split-Path -Leaf $UpgradeZip)"
Expand-Archive -LiteralPath $UpgradeZip -DestinationPath $UpDir -Force

function Find-Installer([string] $dir) {
    $p = Get-ChildItem -LiteralPath $dir -Recurse -Filter 'install.ps1' -File |
         Where-Object { $_.FullName -notmatch '\\payload\\' -and $_.FullName -notmatch '\\scripts\\' } |
         Select-Object -First 1
    if (-not $p) { throw "no install.ps1 under $dir" }
    return $p.FullName
}

$baseInstaller = Find-Installer $BaseDir
$upInstaller   = Find-Installer $UpDir
Note "base installer : $baseInstaller"
Note "up   installer : $upInstaller"

# --- Run an installer with the profile redirected -------------------------
function Invoke-Installer([string] $script, [string] $tag) {
    $log = Join-Path $Root "run-$tag.log"
    $ps  = (Get-Command powershell.exe).Source
    $env:USERPROFILE = $FakeHome
    $env:HOME        = $FakeHome
    $env:HOMEDRIVE   = (Split-Path -Qualifier $FakeHome)
    $env:HOMEPATH    = (Split-Path -NoQualifier $FakeHome)
    $env:FRIDAY_SKIP_MODEL = '1'
    try {
        $p = Start-Process -FilePath $ps -PassThru -Wait -NoNewWindow `
             -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
             -ArgumentList @(
                '-NoProfile','-ExecutionPolicy','Bypass','-File', $script,
                '-InstallRoot', $InstallRoot, '-Unattended', '-SkipOllama', '-SkipMemory'
             )
        return $p.ExitCode
    } finally {
        $env:USERPROFILE = $RealHome
        $env:HOME        = $RealHome
    }
}

# --- 1. Base install ------------------------------------------------------
Note 'installing BASE (this pulls Python + deps; several minutes)'
$rc = Invoke-Installer $baseInstaller 'base'
Note "base installer exit = $rc"

$AppDir = Join-Path $InstallRoot 'app'
if (-not (Test-Path -LiteralPath $AppDir)) { throw "base install produced no app dir" }

# ISOLATION ASSERTION — the wizard/vault work below writes under the profile.
# If USERPROFILE did not take, we would be writing into the real ~/.friday.
$probe = Join-Path $FakeHome '.friday'
New-Item -ItemType Directory -Force -Path $probe | Out-Null
$pyExe = Join-Path $InstallRoot 'python\python.exe'
if (-not (Test-Path -LiteralPath $pyExe)) {
    $pyExe = (Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter 'python.exe' -File |
              Select-Object -First 1).FullName
}
if (-not $pyExe) { throw "no python.exe under $InstallRoot" }
Note "python : $pyExe"

$env:USERPROFILE = $FakeHome
$env:HOME        = $FakeHome
$seen = & $pyExe -c "import os,pathlib;print(pathlib.Path.home())" 2>&1
$env:USERPROFILE = $RealHome
$env:HOME        = $RealHome
Note "python sees home = $seen"
if ("$seen".Trim().ToLower() -ne $FakeHome.ToLower()) {
    throw "ISOLATION FAILED: python resolved home to '$seen', not '$FakeHome'. Refusing to continue."
}

# --- 2. Mint a real passphrase + a real encrypted vault artifact ----------
# Uses the wizard's OWN _write_start_bat, and the product's OWN vault crypto.
$PASS = 'rehearsal-passphrase-Do-Not-Reuse-9271'
$mint = @'
import json, os, sys, pathlib
sys.path.insert(0, os.path.join(os.environ["APPDIR"], "src"))
from agent_friday import setup_wizard as w

# The wizard's own writer, with the wizard's own config shape.
# The api-key value is deliberately NOT key-shaped. _write_start_bat only cares
# that the field is truthy, and a realistic-looking literal in a committed test
# is a false positive the secret scanner is right to raise every time.
w._write_start_bat({
    "anthropic_api_key": "placeholder-not-a-key",
    "vault_password": os.environ["PASS"],
})

# A real vault artifact, encrypted the way the product encrypts.
from agent_friday.privacy import vault_crypto as vc
home  = pathlib.Path.home()
vdir  = home / ".friday" / "vault"
vdir.mkdir(parents=True, exist_ok=True)
salt  = os.urandom(32)
(vdir / ".vault_config.json").write_text(json.dumps({"salt_hex": salt.hex()}), encoding="utf-8")
key   = vc.derive_key(os.environ["PASS"], salt, vc.FAST_PROFILE)
blob  = vc.encrypt(b"Stephen's private note. If this decrypts, the vault survived.", key)
(vdir / "note.enc").write_bytes(blob)

print(json.dumps({
    "start_bat": str(pathlib.Path(w.PROJ_ROOT) / "start.bat"),
    "vault_dir": str(vdir),
    "blob_sha": __import__("hashlib").sha256(blob).hexdigest(),
}))
'@
$mintPy = Join-Path $Root 'mint.py'
[System.IO.File]::WriteAllText($mintPy, $mint, (New-Object System.Text.UTF8Encoding($false)))

$env:USERPROFILE = $FakeHome; $env:HOME = $FakeHome
$env:APPDIR = $AppDir; $env:PASS = $PASS
$mintOut = & $pyExe $mintPy 2>&1 | Select-Object -Last 1
$env:USERPROFILE = $RealHome; $env:HOME = $RealHome
Note "mint: $mintOut"
$minted = $mintOut | ConvertFrom-Json

$startBat = $minted.start_bat
if (-not (Test-Path -LiteralPath $startBat)) { throw "wizard did not write start.bat at $startBat" }
$beforeText = Get-Content -LiteralPath $startBat -Raw
$beforeHasPass = $beforeText -match [regex]::Escape($PASS)
Note "start.bat written : $startBat"
Note "contains passphrase before upgrade : $beforeHasPass"
if (-not $beforeHasPass) { throw "precondition failed: start.bat does not contain the passphrase" }

function Get-AppVersion([string] $dir) {
    $pp = Join-Path $dir 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $pp)) { return '' }
    $m = [regex]::Match((Get-Content -LiteralPath $pp -Raw), '(?m)^version\s*=\s*"([^"]+)"')
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}
$verBefore = Get-AppVersion $AppDir
Note "app version before upgrade : $verBefore"

# --- 3. Upgrade in place --------------------------------------------------
Note 'installing UPGRADE over the same InstallRoot'
$rc2 = Invoke-Installer $upInstaller 'upgrade'
Note "upgrade installer exit = $rc2"

# --- 4. The three questions ----------------------------------------------
$verAfter    = Get-AppVersion $AppDir
$batExists   = Test-Path -LiteralPath $startBat
$afterText   = ''
$afterHasPass = $false
if ($batExists) {
    $afterText = Get-Content -LiteralPath $startBat -Raw
    $afterHasPass = $afterText -match [regex]::Escape($PASS)
}

# Can the vault still be decrypted with whatever the passphrase source now holds?
$check = @'
import json, os, re, sys, pathlib
sys.path.insert(0, os.path.join(os.environ["APPDIR"], "src"))
from agent_friday.privacy import vault_crypto as vc

home = pathlib.Path.home()
vdir = home / ".friday" / "vault"
out  = {"recovered_from": None, "decrypted": False, "plaintext": None, "error": None}

# Recover the passphrase the way the product would: start.bat is its only home.
sb = pathlib.Path(os.environ["APPDIR"]) / "start.bat"
pw = None
if sb.exists():
    m = re.search(r'(?im)^\s*SET\s+FRIDAY_PASSWORD=(.*)$', sb.read_text(encoding="utf-8", errors="ignore"))
    if m:
        pw = m.group(1).strip()
        out["recovered_from"] = "start.bat"
if pw is None:
    out["error"] = "passphrase not recoverable: start.bat absent or has no FRIDAY_PASSWORD"
else:
    try:
        salt = bytes.fromhex(json.loads((vdir / ".vault_config.json").read_text(encoding="utf-8"))["salt_hex"])
        key  = vc.derive_key(pw, salt, vc.FAST_PROFILE)
        pt   = vc.decrypt((vdir / "note.enc").read_bytes(), key)
        out["decrypted"] = True
        out["plaintext"] = pt.decode("utf-8", "replace")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
'@
$checkPy = Join-Path $Root 'check.py'
[System.IO.File]::WriteAllText($checkPy, $check, (New-Object System.Text.UTF8Encoding($false)))

$env:USERPROFILE = $FakeHome; $env:HOME = $FakeHome; $env:APPDIR = $AppDir
$checkOut = & $pyExe $checkPy 2>&1 | Select-Object -Last 1
$env:USERPROFILE = $RealHome; $env:HOME = $RealHome
$checked = $checkOut | ConvertFrom-Json

$summary = [ordered]@{
    label                 = $Label
    base_zip              = (Split-Path -Leaf $BaseZip)
    upgrade_zip           = (Split-Path -Leaf $UpgradeZip)
    base_exit             = $rc
    upgrade_exit          = $rc2
    version_before        = $verBefore
    version_after         = $verAfter
    code_actually_updated = ($verAfter -ne $verBefore -and $verAfter -ne '')
    start_bat_survived    = $batExists
    passphrase_survived   = $afterHasPass
    vault_decrypted       = [bool]$checked.decrypted
    recovered_from        = $checked.recovered_from
    vault_error           = $checked.error
    plaintext             = $checked.plaintext
}
[System.IO.File]::WriteAllText($Result, ($summary | ConvertTo-Json -Depth 4),
                               (New-Object System.Text.UTF8Encoding($false)))

Note '──────────── RESULT ────────────'
$summary.GetEnumerator() | ForEach-Object { Note ("  {0,-22}: {1}" -f $_.Key, $_.Value) }
Note "written to $Result"
