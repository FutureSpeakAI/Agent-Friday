#Requires -Version 5.1
<#
    Agent Friday - Windows installer

    Run this by double-clicking "Install Agent Friday.cmd" next to it. That
    wrapper exists so nobody has to be told about execution policies.

    WHAT THIS IS FOR
    ----------------
    Someone with a stock Windows 11 laptop and none of Python, git or Ollama
    should be able to double-click one thing, answer a few plain questions,
    and end up talking to Friday. The previous path required all three plus a
    virtual environment and pip, which is a developer workflow wearing the
    word "installer".

    DESIGN RULES, IN ORDER OF HOW MUCH THEY MATTER
    ----------------------------------------------
    1. Never report success that has not been verified. Every step is run
       through Invoke-Step, which decides success from a -Verify block and
       ignores the command's own opinion. See lib/Common.ps1.
    2. She never sees a stack trace, a path, or an exit code. Every failure
       becomes one plain sentence and one thing to do.
    3. No output ever names a secret - not its value, and not its name.
    4. Everything is per-user. No administrator, no Program Files, no HKLM.
       An installer that needs admin to remove itself is not removable.
    5. Anything that fails and is survivable is survived, loudly, in the
       report - not silently.
#>

[CmdletBinding()]
param(
    # Override the install location. Used by the test harness so a test run
    # cannot disturb a real install on the same machine.
    [string] $InstallRoot = (Join-Path $env:LOCALAPPDATA 'AgentFriday'),

    # Unattended mode for testing. Skips every prompt, declines self-repair,
    # declines autostart, and does NOT run the interactive setup wizard.
    [switch] $Unattended,

    # Skip the memory tier. Friday works without it; she just does not
    # remember across sessions.
    #
    # Measured on Windows 11, 2026-08-21: core + recommended is about 800 MB of
    # site-packages, and the memory tier adds roughly 1.5 GB on top (torch is
    # 490 MB of that on disk). It used to be far worse - headroom-ai[all] in the
    # recommended tier was quietly pulling torch, transformers,
    # sentence-transformers, datasets, scikit-learn, pandas, OpenCV and an OCR
    # engine, so -SkipMemory skipped nothing and the size shown to the user was
    # wrong. See requirements/recommended.txt.
    [switch] $SkipMemory,

    # Skip Ollama entirely. For test runs and for machines that already have
    # a managed Ollama the installer should not touch.
    [switch] $SkipOllama,

    # Stop after the dependency install. Used to test the machinery without
    # creating shortcuts or touching the registry.
    [switch] $DepsOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Here 'lib\Common.ps1')
. (Join-Path $Here 'lib\Download.ps1')
. (Join-Path $Here 'lib\Python.ps1')
. (Join-Path $Here 'lib\Deps.ps1')
. (Join-Path $Here 'lib\Ollama.ps1')
. (Join-Path $Here 'lib\Shortcuts.ps1')
. (Join-Path $Here 'lib\Heal.ps1')

Initialize-Console

# --- Layout --------------------------------------------------------------
$AppDir        = Join-Path $InstallRoot 'app'
$LogDir        = Join-Path $InstallRoot 'logs'
$CacheDir      = Join-Path $InstallRoot 'cache'
$ToolsDir      = Join-Path $InstallRoot 'tools'
$ManifestPath  = Join-Path $InstallRoot 'install-manifest.json'
$PayloadDir    = Join-Path $Here 'payload'        # produced by build-installer.ps1
$WheelhouseDir = Join-Path $Here 'wheelhouse'
$ReqDir        = Join-Path $Here 'requirements'
$IconPath      = Join-Path $InstallRoot 'app\assets\friday.ico'

New-Item -ItemType Directory -Force -Path $InstallRoot, $LogDir, $CacheDir, $ToolsDir | Out-Null
Initialize-Log (Join-Path $LogDir ("install-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss')))

$script:Sources = (Get-Content -LiteralPath (Join-Path $Here 'sources.json') -Raw | ConvertFrom-Json)
Initialize-RemediationMenu

$Version = '5.7.0'
try {
    $pyproject = Join-Path $PayloadDir 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproject) {
        $m = [regex]::Match((Get-Content -LiteralPath $pyproject -Raw), '(?m)^version\s*=\s*"([^"]+)"')
        if ($m.Success) { $Version = $m.Groups[1].Value }
    }
} catch { }

# Count the Say-Step calls below, not a guess. This read 12 while the script
# made fifteen of them, so the last three announced themselves as "[13/12]",
# "[14/12]", "[15/12]". The models step is conditional on Ollama being present,
# so the total is bumped by one at that point rather than assumed here.
# Bumped 15 -> 16 when step 2b ("How Friday should think") was added.
Set-StepTotal 16

# =========================================================================
#  Welcome
# =========================================================================

Say-Banner -Version $Version
Say 'This sets up Friday on this laptop. Most of it is downloading, so how'
Say 'long it takes depends on the internet connection more than on the'
Say 'computer. You can leave it running.'
Say ''
Say 'Nothing else on the computer is changed. Everything Friday needs goes'
Say 'into one folder, and there is an uninstaller that removes it.'
Say ''

# =========================================================================
#  Step 1 - Is this machine going to work at all?
#           No healing here, by design: this runs before any key exists, so
#           the error text has to stand on its own. See the note in the brief
#           about not letting healing become an excuse for poor error text.
# =========================================================================

Say-Step 'Checking this laptop'

$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
Write-Log "OS: $($os.Caption) $($os.Version) $($os.OSArchitecture)"

if ([Environment]::Is64BitOperatingSystem -eq $false) {
    Say-Problem -What 'Friday needs a 64-bit version of Windows, and this computer is running a 32-bit one.' `
                -WhatToDo 'Friday cannot run on this machine. Nothing was installed or changed.'
    Complete-Install -Failed -FailedStep 'preflight.arch'
    exit 1
}

$osVer = [Version]$os.Version
if ($osVer -lt [Version]'10.0') {
    Say-Problem -What 'Friday needs Windows 10 or Windows 11. This computer is running something older.' `
                -WhatToDo 'Friday cannot run on this machine. Nothing was installed or changed.'
    Complete-Install -Failed -FailedStep 'preflight.osversion'
    exit 1
}

$drive = (Get-Item $InstallRoot).PSDrive
$freeGb = [math]::Round($drive.Free / 1GB, 1)
$neededGb = 8
if ($SkipMemory) { $neededGb = 5 }
if ($SkipOllama) { $neededGb = $neededGb - 3 }
Write-Log "Free space on $($drive.Name): $freeGb GB; need about $neededGb GB"
if ($freeGb -lt $neededGb) {
    Say-Problem -What ("Friday needs about $neededGb gigabytes of free space and this computer has $freeGb. " +
                       'Setup has stopped rather than fill up the disk.') `
                -WhatToDo 'Empty the Recycle Bin, or move some photos or videos to another drive, then run this again.'
    Complete-Install -Failed -FailedStep 'preflight.disk'
    exit 1
}

# Windows 11 reports itself as version 10.0.x - the major number never moved.
# Printing "Windows $($osVer.Major)" told a Windows 11 laptop it was Windows 10,
# which is the sort of small wrongness that makes someone distrust everything
# else on the screen. The build number is the real discriminator (22000+).
$osName = 'Windows'
if ($os.Caption) { $osName = ($os.Caption -replace '^Microsoft\s+', '') }
elseif ($osVer.Build -ge 22000) { $osName = 'Windows 11' }
else { $osName = 'Windows 10' }
Say-Ok "$osName, 64-bit, $freeGb GB free."

# =========================================================================
#  Step 2 - The self-repair question
# =========================================================================

Say-Step 'One question before we start'

$healConsent = $false
$healKey     = $null

if ($Unattended) {
    Write-Log 'Unattended: self-repair declined automatically.' 'HEAL'
    Say-Detail 'Skipped (unattended run).'
}
else {
    Say ''
    Say '  Friday can use a Claude key to fix problems by herself if setup'
    Say '  runs into one, instead of stopping and asking you to sort it out.'
    Say ''
    Say '  This is optional. Setup works fine without it - it just cannot'
    Say '  repair itself if something goes wrong.'
    Say ''
    Say '  If you have a Claude key, paste it now. If you do not, or you would'
    Say '  rather not, just press Enter.'
    Say ''
    Say '  (You will be asked for your keys properly in a few minutes either'
    Say '   way. This is only about letting setup fix itself.)'
    Say ''
    # Up to three goes. A key that does not work is worth catching HERE, where
    # the fix is to paste a different one, rather than twenty minutes later at
    # the first failure - which is where it used to surface.
    $entered = $null
    for ($try = 1; $try -le 3; $try++) {
        $entered = Read-Host '  Claude key (or press Enter to skip)' -AsSecureString
        if (-not $entered -or $entered.Length -eq 0) { $entered = $null; break }

        Say ''
        Say-Working 'Checking that key works.'
        $check = Test-AnthropicKey -ApiKey $entered

        if ($check.Verdict -eq 'ok') {
            Say-Ok 'That key works.'
            break
        }

        if ($check.Verdict -eq 'unknown') {
            # FAIL OPEN. Our pre-flight could not reach the API; that is not
            # evidence against her key, and setup does not get to stop over it.
            Say-Note ("Setup could not check the key - " + $check.Message + " Carrying on with it anyway.")
            Add-InstallWarning "The self-repair key could not be verified before use: $($check.Message)"
            break
        }

        # rejected / no_credit - the API said so plainly.
        Say ''
        Say-Problem -What $check.Message `
                    -WhatToDo $(if ($check.Verdict -eq 'no_credit') {
                        'Add credit to that account, or use a different key. You can also press Enter to carry on without self-repair - setup works fine without it.'
                    } else {
                        'Check you copied the whole key, then paste it again. You can also press Enter to carry on without self-repair - setup works fine without it.'
                    })
        $entered = $null
        if ($try -eq 3) {
            Say-Note 'Carrying on without self-repair.'
            break
        }
        Say ''
    }

    if ($entered -and $entered.Length -gt 0) {
        Say ''
        $answer = Read-Host '  Use it to fix problems automatically if any come up? [Y/n]'
        $healConsent = ($answer -eq '' -or $answer -match '^[Yy]')
        if ($healConsent) {
            $healKey = $entered
            # Hand it to the setup wizard later so she is not asked twice.
            # Process-scope only: this environment variable exists inside this
            # PowerShell process and the children it starts, and dies with it.
            # It is never written to a file. The legacy start.bat pattern of
            # putting keys in plain text on disk is deliberately not repeated.
            $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($healKey)
            try { $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
            Say-Ok 'Thank you. Setup will fix small problems on its own if it can.'
            Say ''
            # Said out loud because there is no per-person key management: the
            # key is stored on THIS machine and used until it is replaced, and
            # whoever owns it pays for what Friday does here. If it belongs to
            # someone else, revoking it stops this install with no way to revoke
            # only this one. See KNOWN_ISSUES.md.
            Say '  One thing worth knowing: this key is saved on this computer,'
            Say '  encrypted, and used until you replace it. Whoever owns the key'
            Say '  pays for what Friday does here - so if it is not yours, and'
            Say '  they change it, Friday stops until you are given the new one.'
            Say '  You can replace it any time in Settings.'
            Write-Log 'Self-repair: consented. Key verified before arming.' 'HEAL'
        } else {
            Say-Ok 'Understood - setup will not use it.'
            Write-Log 'Self-repair: key supplied, consent DECLINED.' 'HEAL'
        }
    } else {
        Say-Ok 'No problem - carrying on without it.'
        Write-Log 'Self-repair: no key supplied.' 'HEAL'
    }
}

# =========================================================================
#  Step 2b - Cloud key only, or a local model as well?
#
#  This question exists so that -SkipOllama stops being something the person
#  running the installer has to KNOW about. Before it, the default on a small
#  card was to pull a local model, and on an 8 GiB card that produced the worse
#  of the two configurations rather than the better one:
#
#    * with NO local model, a vault-touching turn takes the `redact` branch and
#      routes to Claude. It works.
#    * with a local model present, ModelRouter._route_vault force-routes those
#      turns on-device and does NOT fall back to cloud. If that seat is slow,
#      cold or dead, the turn fails outright.
#
#  So on a machine that cannot comfortably hold a seat, "no local model" is not
#  a degraded install. It is the safer one, and it should be what happens when
#  nobody answers.
# =========================================================================

function Get-CardVramGib {
    <#  Total VRAM of the largest NVIDIA card, or $null if we cannot tell.

        nvidia-smi rather than the app's own hardware_profile because Friday's
        Python does not exist yet at this point in the install - the whole
        value of asking here is that it comes BEFORE twenty minutes of
        downloading, not after it. AMD and Intel cards read as $null, which is
        the same answer detect_gpus gives (it shells nvidia-smi and nothing
        else), so the two agree about what they cannot see. #>
    try {
        $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if (-not $smi) { return $null }
        $out = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $best = 0
        foreach ($line in @($out)) {
            $t = ("$line").Trim()
            if ($t -match '^[0-9]+$') { $v = [int]$t; if ($v -gt $best) { $best = $v } }
        }
        if ($best -le 0) { return $null }
        return [math]::Round($best / 1024.0, 1)
    } catch { return $null }
}

Say-Step 'How Friday should think'

# The threshold mirrors services/model_plan.py, where DISPLAY_RESERVE_GIB (2.5)
# is what comes off the card before a model sees any of it. A model's own KV
# cache, projector and CUDA context are NOT deducted here: they live inside each
# model's own footprint in model_plan's ladder. Subtracting them in both places
# is the double count that used to make this installer refuse, on a 12 GiB card,
# the model that runs fully resident on a 12 GiB card.
#
# This arithmetic is duplicated here ONLY to choose a default answer and to name
# the tier - model_plan remains the authority on which model is actually
# installed, and it runs later against the real hardware profile.
$cardGib   = Get-CardVramGib
$usableGib = $null
if ($null -ne $cardGib) { $usableGib = [math]::Round($cardGib - 2.5, 1) }

# The ladder, as model_plan.BRAIN_MODELS computes it: the model's own footprint
# on the card (download size converted to GiB, plus the 1.7 GiB of runtime
# overhead measured on the reference card), and the download it costs.
$brainLadder = @(
    @{ Id = 'qwen3:4b';   Needs =  4.03; Gb =  2.50; Says = 'a small model - good for quick questions, weaker at long multi-step jobs' },
    @{ Id = 'qwen3:8b';   Needs =  6.57; Gb =  5.23; Says = 'a solid everyday model' },
    @{ Id = 'gemma4:12b'; Needs =  8.74; Gb =  7.56; Says = 'the model Friday is tuned and measured against' },
    @{ Id = 'qwen3:14b';  Needs = 10.34; Gb =  9.28; Says = 'a strong model that handles multi-step work well' },
    @{ Id = 'qwen3:32b';  Needs = 20.51; Gb = 20.20; Says = 'the largest Friday offers - closest to a cloud model for tools and multi-step work' }
)
$brainPick = $null
if ($null -ne $usableGib) {
    foreach ($rung in $brainLadder) {
        if ($usableGib -ge $rung.Needs) { $brainPick = $rung }
    }
}

# Comfortable means a card that can hold something better than the floor. On a
# card that only just fits the smallest model, the key is the better default:
# the small seat is genuinely weaker at exactly the multi-step tool work people
# ask Friday for, and it can be added later in a couple of clicks.
$localIsComfortable = ($null -ne $brainPick -and $brainPick.Id -ne 'qwen3:4b')
$localWanted = $localIsComfortable

if ($SkipOllama) {
    $localWanted = $false
    Write-Log 'Local model declined: -SkipOllama.' 'INFO'
    Say-Detail 'Using your Claude key only (asked for with -SkipOllama).'
}
elseif ($Unattended) {
    Write-Log "Unattended: local model = $localWanted (card $cardGib GiB, usable $usableGib GiB)." 'INFO'
    if ($localWanted) { Say-Detail 'Unattended: this card has room, so a local model will be downloaded.' }
    else              { Say-Detail 'Unattended: Claude key only - no local model will be downloaded.' }
}
else {
    Say ''
    if ($null -eq $cardGib) {
        Say '  Friday could not find a graphics card she knows how to measure,'
        Say '  so running a model on this laptop would be slow.'
    } else {
        Say ("  This computer has a {0} GB graphics card. After the display takes" -f $cardGib)
        Say ("  its share, about {0} GB of that is left for Friday." -f $usableGib)
        if ($null -ne $brainPick) {
            Say ''
            Say ("  That is enough for {0} - {1}." -f $brainPick.Id, $brainPick.Says)
            Say ("  It is a {0} GB download." -f $brainPick.Gb)
        }
    }
    Say ''
    Say '  There are two ways to run her, and you can change your mind later.'
    Say ''
    Say '    1. Use your Claude key.'
    Say '       Nothing extra to download. She can do everything she does'
    Say '       best - talking, searching, writing, files, voice. Your'
    Say '       private notes stay on this laptop and are never sent.'
    Say ''
    Say '    2. Also download a model that runs on this laptop.'
    Say '       A few gigabytes more now - how many depends on what this card'
    Say '       can hold - and a longer wait. Lets her work with no internet,'
    Say '       and lets her read your private notes back to you.'
    Say ''
    if ($localIsComfortable) {
        Say '  This computer has room for both, so 2 is the usual choice here.'
        $prompt = '  Which would you like? [1/2, default 2]'
    } else {
        Say '  On a card this size Friday recommends 1. A model squeezed onto a'
        Say '  small card is slower than the key and can stall on long answers -'
        Say '  and you can add one later in a couple of clicks.'
        $prompt = '  Which would you like? [1/2, default 1]'
    }
    Say ''
    $pick = ("" + (Read-Host $prompt)).Trim()
    if     ($pick -eq '2') { $localWanted = $true  }
    elseif ($pick -eq '1') { $localWanted = $false }
    # anything else, including Enter, keeps the hardware-derived default

    Write-Log "Local model wanted = $localWanted (card=$cardGib GiB, usable=$usableGib GiB, answered='$pick')" 'INFO'
    if ($localWanted) {
        Say-Ok 'Friday will download a local model as well. That is the long wait later on.'
    } else {
        Say-Ok 'Friday will use your Claude key. Nothing extra to download.'
        Say-Detail 'On a fast connection the rest of setup takes ten to fifteen minutes.'
        Say-Detail 'To add a local model later: open Friday, then Settings -> Intelligence.'
    }
}

# =========================================================================
#  Step 3 - Put Friday's own files in place
# =========================================================================

function Get-InstalledAppVersion {
    <#  The version of the code ALREADY in $AppDir, or '' if it cannot be read.

        pyproject.toml is the version's only source of truth and has shipped in
        every payload, so it is what we ask. Returning '' for "cannot tell" is
        load-bearing: every caller treats unknown as "not the version we are
        installing", so the copy runs. Guessing the other way is the bug below.
    #>
    param([string] $Dir)
    try {
        $pp = Join-Path $Dir 'pyproject.toml'
        if (-not (Test-Path -LiteralPath $pp)) { return '' }
        $m = [regex]::Match((Get-Content -LiteralPath $pp -Raw), '(?m)^version\s*=\s*"([^"]+)"')
        if ($m.Success) { return $m.Groups[1].Value }
    } catch { }
    return ''
}

# --- User state that lives INSIDE the app folder -------------------------
#
# app.copy deletes $AppDir wholesale and copies a fresh payload over it. That
# is right for code and catastrophic for anything the USER put there, and the
# setup wizard puts the single most unrecoverable thing in the product there:
#
#   setup_wizard.py:968  "Never write vault_password to settings files - it
#                         lives only in start.bat as a FRIDAY_PASSWORD env var"
#
# start.bat is written to PROJ_ROOT, which for a packaged install is $AppDir.
# So <InstallRoot>\app\start.bat is the ONLY automatic home of the passphrase
# that decrypts ~/.friday/vault (AES-256-GCM over an Argon2id key). ~/.friday
# is deliberately never touched by this installer, so deleting start.bat
# leaves the ciphertext intact and the key gone. There is no recovery.
#
# Before 5.6.5 this was latent: app.copy's verify passed before the action on
# every upgrade, so the delete never ran and the file survived by accident.
# 5.6.5 fixed the verify - correctly - and by doing so made this reachable for
# the first time. It is fixed here rather than by reverting that.
#
# THE LIST IS EXACTLY Get-PayloadExcludes' secret-bearing set (see
# build-installer.ps1). That is what makes restoring safe rather than a
# conflict: the payload is GUARANTEED never to contain these names, so putting
# them back can never overwrite a file this release shipped.
$script:AppUserFiles = @(
    'start.bat', 'launch_now.bat', 'friday_startup.bat', 'friday_startup.vbs',
    '.env', 'secrets.yaml', 'config.yaml'
)

function Save-AppUserFiles {
    <#  Move user state out of $AppDir before it is deleted. Returns the count.
        The holding folder sits in $InstallRoot, NOT in $AppDir, so the
        recursive delete cannot reach it. #>
    param([Parameter(Mandatory)][string] $AppDir,
          [Parameter(Mandatory)][string] $KeepDir)
    $n = 0
    foreach ($name in $script:AppUserFiles) {
        $src = Join-Path $AppDir $name
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
        try {
            New-Item -ItemType Directory -Force -Path $KeepDir | Out-Null
            Copy-Item -LiteralPath $src -Destination (Join-Path $KeepDir $name) -Force
            # Names only. NEVER the contents - these files hold the vault
            # passphrase and API keys in plain text.
            Write-Log "Preserving user file across the copy: $name" 'OK'
            $n++
        } catch {
            Write-Log "Could not preserve $name : $($_.Exception.Message)" 'WARN'
        }
    }
    return $n
}

function Restore-AppUserFiles {
    <#  Put them back, then delete the holding copy immediately - it is a
        second plaintext copy of a credential and must not outlive the step.

        Idempotent, and safe to call when nothing was saved. If $AppDir is
        missing (the copy failed outright) the holding folder is LEFT so a
        retry, or Stephen, can still recover it. #>
    param([Parameter(Mandatory)][string] $KeepDir,
          [Parameter(Mandatory)][string] $AppDir)
    if (-not (Test-Path -LiteralPath $KeepDir)) { return 0 }
    if (-not (Test-Path -LiteralPath $AppDir)) {
        Write-Log "App folder missing at restore time - keeping preserved files at $KeepDir" 'WARN'
        return 0
    }
    $n = 0
    foreach ($f in @(Get-ChildItem -LiteralPath $KeepDir -File -Force -ErrorAction SilentlyContinue)) {
        try {
            Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $AppDir $f.Name) -Force
            Write-Log "Restored user file after the copy: $($f.Name)" 'OK'
            $n++
        } catch {
            Write-Log "Could not restore $($f.Name) : $($_.Exception.Message)" 'FAIL'
            return $n   # leave KeepDir in place; do not destroy the only copy
        }
    }
    Remove-Item -LiteralPath $KeepDir -Recurse -Force -ErrorAction SilentlyContinue
    return $n
}

$null = Invoke-Step -Id 'app.copy' -Title 'Copying Friday onto this laptop' `
    -HumanFailure 'Friday''s own files could not be copied onto this computer.' `
    -HumanFix 'Make sure there is space on the drive and that no antivirus is blocking the folder, then run this again.' `
    -VerifyDescription "Friday's own files are present AND are this version" `
    -Action {
        if (-not (Test-Path -LiteralPath $PayloadDir)) {
            throw "No payload folder at $PayloadDir. This installer was not built with build-installer.ps1."
        }
        $keepDir = Join-Path $InstallRoot '.upgrade-keep'
        # An interrupted earlier attempt may have left files here. Take them
        # back first, so this attempt's Save sees the real current state.
        [void](Restore-AppUserFiles -KeepDir $keepDir -AppDir $AppDir)
        if (Test-Path -LiteralPath $AppDir) {
            $was = Get-InstalledAppVersion $AppDir
            if ($was) { Write-Log "Upgrading app files from $was to $Version" }
            else      { Write-Log "Replacing app files of unknown version with $Version" }
            # BEFORE the delete. start.bat holds the vault passphrase and it
            # exists nowhere else - see the note above Save-AppUserFiles.
            [void](Save-AppUserFiles -AppDir $AppDir -KeepDir $keepDir)
            Remove-Item -LiteralPath $AppDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        try {
            New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
            Copy-Item -Path (Join-Path $PayloadDir '*') -Destination $AppDir -Recurse -Force
        } finally {
            # In finally, not after the copy: if the copy throws, the user's
            # passphrase must still go back. A failed upgrade is recoverable;
            # a destroyed vault is not.
            [void](Restore-AppUserFiles -KeepDir $keepDir -AppDir $AppDir)
        }
    } `
    -Verify {
        # VERSION-AWARE ON PURPOSE. This is the whole point of the block.
        #
        # Invoke-Step runs this verify BEFORE the action and skips the action
        # when it passes. Until 5.6.5 it asked only whether four files EXISTED,
        # which any previous install satisfies -- so re-running a newer
        # installer over an older install short-circuited here and copied
        # nothing. The installer then said "Friday is installed", wrote the new
        # version into install-manifest.json and exited 0, while every file in
        # $AppDir was still the old release.
        #
        # Measured, 5.6.3 -> 5.6.4: 0 of 489 files updated; manifest claiming
        # 5.6.4 beside a pyproject.toml still reading 5.6.3; connector_secrets.py
        # -- the only file new in 5.6.4 -- absent, so GET /api/mcp/servers went
        # on handing connector tokens to the browser in the clear, which is the
        # exact exposure that release announced it had closed. The defect
        # shipped with the installer itself, so NO in-place upgrade has ever
        # delivered code.
        #
        # Asking for the VERSION as well as the files closes it, and closes it
        # from ANY prior release rather than only the one below this: an older
        # install reports an older version, and an install too old to have a
        # readable pyproject.toml reports '' -- both mismatch, both copy. The
        # fast path survives for the case it was actually for, re-running the
        # SAME installer (a retry, or the healer), where skipping is correct.
        (Test-Path -LiteralPath (Join-Path $AppDir 'src\agent_friday\cli.py')) -and
        (Test-Path -LiteralPath (Join-Path $AppDir 'src\agent_friday\server.py')) -and
        (Test-Path -LiteralPath (Join-Path $AppDir 'src\agent_friday\setup_wizard.py')) -and
        (Test-Path -LiteralPath (Join-Path $AppDir 'index.html')) -and
        ((Get-InstalledAppVersion $AppDir) -eq $Version)
    }

# Now that the app is on disk, self-repair has something to repair.
if ($healKey -and $healConsent) {
    $healCfg = $null
    $healCfgPath = Join-Path $Here 'healing.json'
    if (Test-Path -LiteralPath $healCfgPath) {
        try {
            # ConvertFrom-Json yields a PSCustomObject; Initialize-Healing
            # wants a hashtable, and PS 5.1 has no -AsHashtable.
            $obj = Get-Content -LiteralPath $healCfgPath -Raw | ConvertFrom-Json
            $healCfg = @{}
            foreach ($p in $obj.PSObject.Properties) {
                if ($p.Name.StartsWith('_')) { continue }   # comment keys
                $healCfg[$p.Name] = $p.Value
            }
        } catch {
            Write-Log "healing.json could not be read; using built-in defaults. $($_.Exception.Message)" 'WARN'
            $healCfg = $null
        }
    }
    [void](Initialize-Healing -ApiKey $healKey -Consented $true -InstallRoot $InstallRoot -Config $healCfg)
}

# =========================================================================
#  Step 4 - Friday's own Python
# =========================================================================

$pyZipName = Split-Path -Leaf ([Uri]$script:Sources.python.url).AbsolutePath
$pyZipPath = Join-Path $CacheDir $pyZipName
$shippedZip = Join-Path $Here "python\$pyZipName"

$null = Invoke-Step -Id 'python.provision' -Title 'Setting up the engine Friday runs on' `
    -HumanFailure ('Setup could not put in place the small program Friday needs in order to run. ' +
                   'This is usually the internet connection dropping partway through a download.') `
    -HumanFix 'Check the internet connection and run this again. Nothing was left half-installed.' `
    -VerifyDescription 'the interpreter starts, reports the right version, and can see Friday''s own code' `
    -Action {
        $src = $pyZipPath
        if (Test-Path -LiteralPath $shippedZip) {
            Write-Log "Using the copy that shipped with the installer: $shippedZip"
            $src = $shippedZip
        }
        elseif (-not (Test-FileHash -Path $pyZipPath -ExpectedSha256 $script:Sources.python.sha256)) {
            Say-Working 'Downloading (about 11 MB).'
            if (-not (Get-RemoteFile -Uri $script:Sources.python.url -OutFile $pyZipPath `
                                     -FriendlyName 'Friday''s engine' -Retries 3)) {
                throw 'The download did not complete.'
            }
        }
        Install-EmbeddedPython -InstallRoot $InstallRoot -ZipPath $src `
                               -ExpectedSha256 $script:Sources.python.sha256 `
                               -PthStem $script:Sources.python.pth_stem `
                               -AppSrcDir (Join-Path $AppDir 'src')
    } `
    -Verify {
        Test-PythonProvisioned -InstallRoot $InstallRoot `
                               -ExpectedVersion $script:Sources.python.version `
                               -AppSrcDir (Join-Path $AppDir 'src')
    }

$null = Invoke-Step -Id 'python.pip' -Title 'Getting ready to install Friday''s parts' `
    -HumanFailure 'Setup could not prepare the tool it uses to install the rest of Friday.' `
    -HumanFix 'Check the internet connection and run this again.' `
    -VerifyDescription 'pip runs and reports a version' `
    -Action {
        $getPip = Join-Path $CacheDir 'get-pip.py'
        $shipped = Join-Path $Here 'python\get-pip.py'
        if (Test-Path -LiteralPath $shipped) { Copy-Item -LiteralPath $shipped -Destination $getPip -Force }
        elseif (-not (Test-Path -LiteralPath $getPip)) {
            if (-not (Get-RemoteFile -Uri $script:Sources.get_pip.url -OutFile $getPip -FriendlyName 'a setup tool')) {
                throw 'The download did not complete.'
            }
        }
        Install-Pip -InstallRoot $InstallRoot -GetPipPath $getPip -ExpectedSha256 $script:Sources.get_pip.sha256
    } `
    -Verify { Test-PipWorking -InstallRoot $InstallRoot }

# =========================================================================
#  Step 5-7 - Dependencies
# =========================================================================

$null = Invoke-Step -Id 'deps.core' -Title 'Installing the parts Friday cannot run without' `
    -HumanFailure ('Some of Friday''s parts would not install. She cannot start without them, ' +
                   'so setup has stopped rather than leave you with something half-working.') `
    -HumanFix 'Check the internet connection and run this again. If it stops here twice, the install report named at the end of this window says which part failed.' `
    -VerifyDescription 'every core module imports in Friday''s own interpreter' `
    -Action {
        Say-Working 'This takes a few minutes.'
        Install-RequirementSet -InstallRoot $InstallRoot `
                               -RequirementsFile (Join-Path $ReqDir 'core.txt') `
                               -WheelhouseDir $WheelhouseDir `
                               -ExtraFlags (Get-HealExtraPipFlags)
    } `
    -Verify {
        Test-ModulesImportable -InstallRoot $InstallRoot -Modules @(
            'flask','flask_sock','requests','yaml','bs4','feedparser','rich','colorama',
            'cryptography','psutil','anthropic','google.genai','keyring','pystray','PIL','pynput',
            'googleapiclient','google_auth_oauthlib'
        )
    }

$null = Invoke-Step -Id 'deps.control' -Title 'Installing the part that lets Friday use the screen' `
    -Optional `
    -VerifyDescription 'pyautogui imports' `
    -Action { Install-PyAutoGuiFamily -InstallRoot $InstallRoot -WheelhouseDir $WheelhouseDir } `
    -Verify { Test-ModulesImportable -InstallRoot $InstallRoot -Modules @('pyautogui') }

# NOTE (2026-08-25): this step used to be titled '... and the privacy filter',
# which overstated what installing presidio-analyzer buys. Presidio runs
# OBSERVE-ONLY -- it logs what it would have flagged and changes no egress
# decision unless FRIDAY_PRESIDIO_ENFORCE=1 is set explicitly, which is not
# recommended (measured 2026-08-24: TIER_2 where regex returns TIER_3, and 6 of
# 12 benign prompts escalated). The always-on part of the gate is Layers 1a+1b,
# which are built in and install nothing. Do not restore the old title.
#
# The word "trial" in the current title is load-bearing for the same reason:
# it says on screen that this filter is measured and not in force. Do not
# quietly promote it to "privacy filter" - that is the claim the note above
# exists to prevent.
$null = Invoke-Step -Id 'deps.recommended' -Title 'Installing voice, PDF reading, and a trial privacy filter' `
    -Optional `
    -VerifyDescription 'the recommended modules import' `
    -Action {
        Say-Working 'A few more minutes.'
        Install-RequirementSet -InstallRoot $InstallRoot `
                               -RequirementsFile (Join-Path $ReqDir 'recommended.txt') `
                               -WheelhouseDir $WheelhouseDir `
                               -ExtraFlags (Get-HealExtraPipFlags)
    } `
    -Verify {
        Test-ModulesImportable -InstallRoot $InstallRoot -Modules @(
            'faster_whisper','piper','onnxruntime','pyttsx3',
            'presidio_analyzer','presidio_anonymizer','nacl','pdfplumber'
        )
    }

if (-not $SkipMemory) {
    Say-Step 'Installing Friday''s memory'
    Say-Detail 'This is the big one: about 2.5 GB, and the slowest part of setup.'
    Say-Detail 'It is what lets Friday remember things between conversations.'
    $null = Invoke-Step -Id 'deps.memory' -Title 'Installing Friday''s memory' -Quiet `
        -Optional `
        -VerifyDescription 'torch, sentence-transformers and chromadb import' `
        -Action {
            Install-RequirementSet -InstallRoot $InstallRoot `
                                   -RequirementsFile (Join-Path $ReqDir 'memory.txt') `
                                   -WheelhouseDir $WheelhouseDir `
                                   -ExtraFlags (Get-HealExtraPipFlags) `
                                   -TimeoutSeconds 7200
        } `
        -Verify {
            Test-ModulesImportable -InstallRoot $InstallRoot -Modules @('torch','sentence_transformers','chromadb')
        }
    if (Test-ModulesImportable -InstallRoot $InstallRoot -Modules @('chromadb')) {
        Say-Ok 'Done. Friday will remember things between conversations.'
    } else {
        Say-Note 'That part did not install. Friday will work, but she will not remember previous conversations.'
    }
} else {
    Write-Log 'Memory tier skipped by request (-SkipMemory).'
    Say-Step 'Skipping Friday''s memory'
    Say-Detail 'Requested. Friday will not remember between conversations.'
}

if ($DepsOnly) {
    Say ''
    Say-Ok 'Stopping here (-DepsOnly).'
    Complete-Install
    exit 0
}

# =========================================================================
#  Step 8 - Ollama
# =========================================================================

$ollamaOutcome = @{ Installed = $false; Method = 'skipped'; WeInstalledIt = $false }

if (-not $localWanted) {
    # Not a skipped step - a chosen configuration. Step 2b explains why this is
    # the safer answer on a small card rather than the lesser one.
    Write-Log "Ollama step not run: local model not wanted (SkipOllama=$SkipOllama)."
    Say-Step 'Skipping the local model engine'
    Say-Detail 'You chose to run Friday on your Claude key. Nothing to install here.'
} else {
    Say-Step 'Setting up the part that runs AI on this laptop'
    Say-Detail 'This is what lets Friday work without sending anything to the internet.'

    $ollamaOutcome = Install-Ollama -CacheDir $CacheDir -InstallerUrl $script:Sources.ollama.url

    if ($ollamaOutcome.Installed) {
        Say-Ok 'Ready.'
        [void](Start-OllamaDaemon)
    } else {
        # The graceful continue the brief asked for. Not a failure, not a lie.
        Show-ManualOllamaInstruction
    }
}

# =========================================================================
#  Step 9 - Models
# =========================================================================

$modelTagsPulled = @()

if ($ollamaOutcome.Installed) {
    Set-StepTotal 17     # this step only exists when there is an Ollama to use
    Say-Step 'Downloading Friday''s local brain'
    Say-Detail 'Friday works out what this laptop can handle first, then downloads only that.'
    Say-Detail 'Between about 2.5 and 7.5 GB depending on the card. This is the longest wait.'

    Set-HealAllowedModelTags @('gemma3:4b','qwen3:4b','qwen3:8b','gemma4:12b','qwen3:14b','qwen3:32b',
                              'embeddinggemma','nomic-embed-text')

    $before = @(Get-OllamaInstalledModels)

    $null = Invoke-Step -Id 'models.install' -Title 'Downloading Friday''s local brain' -Quiet `
        -Optional `
        -VerifyDescription 'friday models --install finished successfully and a conversational model is present' `
        -Action {
            # Deliberately calls the app's OWN planner rather than hardcoding a
            # tag. It reads the machine's RAM, VRAM and disk, decides what can
            # actually run here, and verifies each pull afterwards. Duplicating
            # that logic in PowerShell would mean two places to be wrong.
            Invoke-Native -FilePath (Get-PythonExe $InstallRoot) `
                          -Arguments @('-m','agent_friday.cli','models','--install') `
                          -WorkingDirectory $AppDir -TimeoutSeconds 7200
        } `
        -Verify {
            # cmd_models returns non-zero on a failed install, and main()
            # propagates it (commit d37db5a). We still do not rely on that
            # alone - we ask Ollama what is actually on disk.
            $installed = @(Get-OllamaInstalledModels)
            $conversational = $installed | Where-Object { $_ -notmatch 'embed' }
            return ($conversational.Count -gt 0)
        }

    $after = @(Get-OllamaInstalledModels)
    $modelTagsPulled = @($after | Where-Object { $before -notcontains $_ })
    Write-Log "Models added by this install: $($modelTagsPulled -join ', ')"

    if ($after.Count -gt 0) { Say-Ok 'Friday can now run on this laptop without the internet.' }
    else { Say-Note 'No local model was downloaded. Friday will need an internet AI key to talk.' }
} elseif ($localWanted) {
    Write-Log 'Skipping model download - Ollama is not working.'
} else {
    Write-Log 'Skipping model download - Claude key only, by choice.'
}

# =========================================================================
#  Step 10 - Shortcuts, launchers, uninstaller
# =========================================================================

$shortcutsCreated = @()

$null = Invoke-Step -Id 'shortcuts.launchers' -Title 'Creating the shortcuts' `
    -HumanFailure 'Setup could not create the shortcut to start Friday.' `
    -HumanFix ('Everything else installed correctly. Run this again to retry just the shortcut; if it fails ' +
               'twice, open the Agent Friday folder in the Start menu and start her from there instead.') `
    -VerifyDescription 'the launcher scripts exist' `
    -Action {
        Copy-Item -LiteralPath (Join-Path $Here 'lib') -Destination $ToolsDir -Recurse -Force
        Copy-Item -LiteralPath (Join-Path $Here 'uninstall.ps1') -Destination (Join-Path $ToolsDir 'uninstall.ps1') -Force
        Copy-Item -LiteralPath (Join-Path $Here 'autostart.ps1') -Destination (Join-Path $ToolsDir 'autostart.ps1') -Force
        Copy-Item -LiteralPath (Join-Path $Here 'sources.json') -Destination (Join-Path $ToolsDir 'sources.json') -Force
        [void](Install-LauncherScripts -InstallRoot $InstallRoot)
    } `
    -Verify { Test-LauncherScripts -InstallRoot $InstallRoot }

$null = Invoke-Step -Id 'shortcuts.icons' -Title 'Putting Friday on the desktop' `
    -Optional `
    -VerifyDescription 'the desktop shortcut exists and its target exists' `
    -Action {
        $icon = ''
        if (Test-Path -LiteralPath $IconPath) { $icon = $IconPath }
        $script:shortcutsCreated = Install-Shortcuts -InstallRoot $InstallRoot -IconPath $icon
    } `
    -Verify { Test-ShortcutsInstalled -InstallRoot $InstallRoot }

$null = Invoke-Step -Id 'shortcuts.uninstall' -Title 'Registering the uninstaller' `
    -HumanFailure 'Setup could not register Friday in the list of installed programs.' `
    -HumanFix ('Friday is installed and will work. To remove her later, open the Agent Friday ' +
               'folder in the Start menu and choose Uninstall Agent Friday.') `
    -VerifyDescription 'Friday appears in Add/Remove Programs and the uninstall command exists' `
    -Action { Register-Uninstaller -InstallRoot $InstallRoot -Version $Version -IconPath $IconPath } `
    -Verify { Test-UninstallerRegistered -ExpectedVersion $Version }

# =========================================================================
#  Step 11 - Autostart
# =========================================================================

$autostartOn = $false
Say-Step 'Starting automatically'

if ($Unattended) {
    Say-Detail 'Skipped (unattended run).'
} else {
    Say ''
    Say '  Friday can start quietly whenever you sign in, so she is already'
    Say '  there when you want her.'
    Say ''
    Say '  You can change this at any time: open the Start menu, find the'
    Say '  Agent Friday folder, and click "Start Friday when I sign in".'
    Say ''
    $a = Read-Host '  Start Friday when you sign in? [y/N]'
    if ($a -match '^[Yy]') {
        $link = Enable-Autostart -InstallRoot $InstallRoot -IconPath $IconPath
        if ($link -and (Test-Autostart)) {
            $autostartOn = $true
            $shortcutsCreated += $link
            Say-Ok 'Friday will start when you sign in. You can turn this off from the Start menu.'
        } else {
            Say-Note 'That did not work, so Friday will not start automatically. Everything else is fine.'
            Add-InstallWarning 'Enable-Autostart failed; autostart is OFF.'
        }
    } else {
        # HONOUR THE NO. On an upgrade the previous install may already have
        # autostart on, and until 5.6.6 answering No here did nothing to it -
        # the entry stayed, Friday went on starting at sign-in, and the manifest
        # recorded autostart_enabled=false anyway. So the answer was ignored AND
        # written down wrong, and the uninstaller then read the manifest and left
        # the entry behind. An answer the installer records has to be an answer
        # the installer acts on.
        if (Test-Autostart) {
            [void](Disable-Autostart)
            if (Test-Autostart) {
                Say-Note 'Friday could not be removed from your sign-in items. She may still start automatically.'
                Add-InstallWarning 'Disable-Autostart failed; autostart is still ON despite the user answering No.'
            } else {
                Say-Ok 'Friday will only start when you open her.'
            }
        } else {
            Say-Ok 'Friday will only start when you open her.'
        }
    }
}

# =========================================================================
#  Step 12 - The setup wizard (Friday's own, not ours)
# =========================================================================

Say-Step 'Friday''s own questions'

if ($Unattended) {
    Say-Detail 'Skipped (unattended run).'
    Write-Log 'Setup wizard skipped: unattended.'
} else {
    Say ''
    Say '  The rest is Friday asking you a few things: your name, a passphrase'
    Say '  for your private notes, and your keys.'
    Say ''
    Say '  Pick a passphrase you will remember. It protects your notes and'
    Say '  nobody can recover it for you - which is rather the point.'
    Say ''

    # The wizard is INVOKED, not reimplemented. It owns key entry, validation
    # and encrypted storage. ANTHROPIC_API_KEY is already in this process's
    # environment if she gave one for self-repair, so step 5 pre-fills and
    # she is not asked the same question twice.
    #
    # This runs in the foreground with inherited stdio - the wizard is a rich
    # console UI and it needs a real terminal, so it cannot go through
    # Invoke-Native's redirected pipes.
    $wizardExit = 0
    try {
        $p = Start-Process -FilePath (Get-PythonExe $InstallRoot) `
                           -ArgumentList @('-m','agent_friday.cli','setup') `
                           -WorkingDirectory $AppDir -NoNewWindow -Wait -PassThru
        $wizardExit = $p.ExitCode
    } catch {
        $wizardExit = -1
        Write-Log "Could not start the setup wizard: $($_.Exception.Message)" 'FAIL'
    }
    Write-Log "Setup wizard exited $wizardExit"

    $marker = Join-Path $env:USERPROFILE '.friday\.setup_complete'
    if (Test-Path -LiteralPath $marker) {
        Say-Ok 'Friday is configured.'
    } else {
        # Not fatal. She may have deliberately skipped keys - the tutorial
        # actively recommends adding the Claude key through Settings instead.
        Say-Note 'Friday is installed. You can finish her setup from inside her, in Settings.'
        Add-InstallWarning "The setup wizard exited $wizardExit and ~/.friday/.setup_complete was not created. The user may have cancelled, or the wizard may have failed."
    }
}

# =========================================================================
#  Manifest + report
# =========================================================================

# A MANIFEST IS A RECORD OF WHAT HAPPENED, NOT OF WHAT WAS INTENDED.
#
# This block used to write down the installer's intentions: the version it set
# out to install, the shortcuts this run created, the autostart answer it was
# given. Invoke-Step skips any step whose verify already passes, so on an
# upgrade several of those intentions were not carried out and the file said
# they were. Three separate ways it lied, all fixed here by MEASURING:
#
#   version           - said $Version even when app.copy short-circuited and
#                       the code on disk was still the old release. That is the
#                       5.6.5 bug's paper trail. Now read back off disk.
#   shortcuts         - empty on every upgrade, because Install-Shortcuts did
#                       not run, so the uninstaller left four .lnk files behind.
#   autostart_enabled - recorded the ANSWER, not the state. Answering No on a
#                       machine that already had autostart wrote false while
#                       leaving it on. (The No is now also acted on - see above.)
#
# installer_version is kept beside the measured one deliberately. If the two
# ever disagree, that disagreement is the single most useful line in the file.
# MEASURING MUST NOT BE ABLE TO FAIL THE INSTALL. Every step above has already
# passed its own verify by the time we get here, so a throw in this block would
# fail an install that actually worked -- and, worse, skip the manifest write, so
# the uninstaller would have nothing to read. That is exactly what happened the
# first time this block measured anything: Test-Autostart fed an empty known
# folder to Join-Path and took the whole run down at step 17 of 16.
#
# So each measurement is asked for defensively and degrades to a stated
# fallback. A manifest that says "we could not tell" is worth far more than no
# manifest at all.
function Get-Measured {
    param([Parameter(Mandatory)][string] $What,
          [Parameter(Mandatory)][scriptblock] $Probe,
          $Fallback)
    try { return (& $Probe) }
    catch {
        Write-Log "Manifest: could not measure $What ($($_.Exception.Message)); recording fallback." 'WARN'
        Add-InstallWarning "The install completed, but $What could not be measured for the manifest."
        return $Fallback
    }
}

$versionOnDisk = Get-Measured 'the installed version' { Get-InstalledAppVersion $AppDir } ''
if ($versionOnDisk -ne $Version) {
    Write-Log ("Manifest: code on disk reports '$versionOnDisk' but this installer is '$Version'. " +
               'Recording what is on disk.') 'WARN'
    Add-InstallWarning ("The installed files report version '$versionOnDisk' but this installer is " +
                        "'$Version'. The manifest records the version actually on disk.")
}

$manifest = [ordered]@{
    schema_version    = 2
    product           = 'Agent Friday'
    version           = $versionOnDisk       # MEASURED off app\pyproject.toml
    installer_version = $Version             # what this installer carried
    installed_at      = (Get-Date).ToString('o')
    install_root      = $InstallRoot
    python_version    = $script:Sources.python.version
    shortcuts         = @(Get-Measured 'the installed shortcuts' { Get-InstalledShortcutPaths } $shortcutsCreated)
    autostart_enabled = [bool](Get-Measured 'the autostart state' { Test-Autostart } $autostartOn)
    ollama            = [ordered]@{
        installed_by_this_installer = [bool]$ollamaOutcome.WeInstalledIt
        method                      = [string]$ollamaOutcome.Method
        models_pulled               = @($modelTagsPulled)
    }
    uninstall_reg_key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday'
    _note = @(
        'The uninstaller reads this file so it removes exactly what was created and nothing else.',
        'In particular it only offers to remove Ollama if installed_by_this_installer is true,',
        'and it only removes the model tags listed in models_pulled - not every model on the machine.',
        'version, shortcuts and autostart_enabled are MEASURED after the install, not assumed from',
        'what the installer set out to do. If version and installer_version disagree, the copy did',
        'not take and the files on disk are the older release.'
    )
}
try {
    [System.IO.File]::WriteAllText($ManifestPath, ($manifest | ConvertTo-Json -Depth 6),
                                   (New-Object System.Text.UTF8Encoding($false)))
    Write-Log "Manifest written: $ManifestPath" 'OK'
} catch {
    # Everything Friday needs is already on disk and verified. Losing the
    # manifest degrades the UNINSTALLER (it falls back to conservative
    # defaults), which is not worth failing a working install over - but it is
    # worth saying out loud rather than swallowing.
    Write-Log "Manifest could not be written: $($_.Exception.Message)" 'FAIL'
    Add-InstallWarning ('The install completed but install-manifest.json could not be written. ' +
                        'Uninstalling will still work; it will be more cautious about what it removes.')
}

Complete-Install

# =========================================================================
#  Done
# =========================================================================

$warns = Get-FailureWarnings
Say ''
Say "  $($script:C.Green)$($script:C.Bold)Friday is installed.$($script:C.Reset)"
Say ''
if ($warns.Count -gt 0) {
    Say '  A couple of optional parts did not install. Friday works; some'
    Say '  things she can do are switched off. She will tell you which if'
    Say '  you ask her what she can do on this laptop.'
    Say ''
}
# Say what is actually there. This screen used to promise a desktop icon and a
# Start menu folder unconditionally - on the same screen that had just reported
# 'Putting Friday on the desktop' as skipped. Someone told to double-click an
# icon that is not there concludes the install failed, which is the one
# impression a finished install must not leave. Design rule 1 applies to the
# closing screen as much as to a step.
$desktopLnk = ''
try { $d = Get-DesktopDir; if ($d) { $desktopLnk = Join-Path $d 'Agent Friday.lnk' } } catch { }
$haveDesktop   = ($desktopLnk -and (Test-Path -LiteralPath $desktopLnk))
$haveStartMenu = $false
try { $sm = Get-StartMenuDir; $haveStartMenu = ($sm -and (Test-Path -LiteralPath $sm)) } catch { }

if ($haveDesktop) {
    Say '  There is an Agent Friday icon on the desktop. Double-click it.'
} elseif ($haveStartMenu) {
    Say '  Setup could not put an icon on the desktop, so open the Start menu'
    Say '  and look for the Agent Friday folder instead.'
} else {
    Say '  Setup could not create the shortcuts, so start her from this folder:'
    Say "    $(Join-Path $InstallRoot 'Agent Friday.cmd')"
}
Say '  The first start takes a minute or two - she is waking up, not stuck.'
Say ''
if ($haveStartMenu) {
    Say "  To remove her: Start menu -> Agent Friday -> Uninstall Agent Friday."
} else {
    Say '  To remove her, run:'
    Say "    $(Join-Path $InstallRoot 'Uninstall Agent Friday.cmd')"
}
Say '  Your notes are kept unless you ask it to remove those too.'
Say ''
Say "  $($script:C.Grey)Install report: $(Join-Path $LogDir 'LAST-INSTALL-REPORT.md')$($script:C.Reset)"
Say ''

if (-not $Unattended) {
    $go = Read-Host '  Start Friday now? [Y/n]'
    if ($go -eq '' -or $go -match '^[Yy]') {
        Start-Process -FilePath (Join-Path $InstallRoot 'Agent Friday.cmd') -WorkingDirectory $InstallRoot
    }
}

exit 0

