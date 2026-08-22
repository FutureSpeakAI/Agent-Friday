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

    # Skip the ~2.5 GB memory tier (torch). Friday works without it; she just
    # does not remember across sessions.
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

$Version = '5.5.0'
try {
    $pyproject = Join-Path $PayloadDir 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproject) {
        $m = [regex]::Match((Get-Content -LiteralPath $pyproject -Raw), '(?m)^version\s*=\s*"([^"]+)"')
        if ($m.Success) { $Version = $m.Groups[1].Value }
    }
} catch { }

Set-StepTotal 12

# =========================================================================
#  Welcome
# =========================================================================

Say-Banner -Version $Version
Say 'This sets up Friday on this laptop. It takes about 20 minutes, most of'
Say 'which is downloading. You can leave it running.'
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

Say-Ok "Windows $($osVer.Major), 64-bit, $freeGb GB free."

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
    $entered = Read-Host '  Claude key (or press Enter to skip)' -AsSecureString

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
            Write-Log 'Self-repair: consented.' 'HEAL'
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
#  Step 3 - Put Friday's own files in place
# =========================================================================

$null = Invoke-Step -Id 'app.copy' -Title 'Copying Friday onto this laptop' `
    -HumanFailure 'Friday''s own files could not be copied onto this computer.' `
    -HumanFix 'Make sure there is space on the drive and that no antivirus is blocking the folder, then run this again.' `
    -VerifyDescription "the application's main file is present and readable" `
    -Action {
        if (-not (Test-Path -LiteralPath $PayloadDir)) {
            throw "No payload folder at $PayloadDir. This installer was not built with build-installer.ps1."
        }
        if (Test-Path -LiteralPath $AppDir) {
            Write-Log "Removing previous app folder at $AppDir"
            Remove-Item -LiteralPath $AppDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
        Copy-Item -Path (Join-Path $PayloadDir '*') -Destination $AppDir -Recurse -Force
    } `
    -Verify {
        (Test-Path -LiteralPath (Join-Path $AppDir 'src\agent_friday\cli.py')) -and
        (Test-Path -LiteralPath (Join-Path $AppDir 'src\agent_friday\server.py')) -and
        (Test-Path -LiteralPath (Join-Path $AppDir 'src\agent_friday\setup_wizard.py')) -and
        (Test-Path -LiteralPath (Join-Path $AppDir 'index.html'))
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
    -HumanFix 'Check the internet connection and run this again. If it stops here twice, send Stephen the report file named at the end of this window.' `
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

$null = Invoke-Step -Id 'deps.recommended' -Title 'Installing voice, PDF reading and the privacy filter' `
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

if ($SkipOllama) {
    Write-Log 'Ollama step skipped by request (-SkipOllama).'
    Say-Step 'Skipping the local model engine'
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
    Say-Step 'Downloading Friday''s local brain'
    Say-Detail 'Friday works out what this laptop can handle first, then downloads only that.'
    Say-Detail 'Usually about 3 GB. This is the longest wait.'

    Set-HealAllowedModelTags @('gemma3:4b','qwen3:8b','gemma4:12b','embeddinggemma','nomic-embed-text')

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
} else {
    Write-Log 'Skipping model download - no working Ollama.'
}

# =========================================================================
#  Step 10 - Shortcuts, launchers, uninstaller
# =========================================================================

$shortcutsCreated = @()

$null = Invoke-Step -Id 'shortcuts.launchers' -Title 'Creating the shortcuts' `
    -HumanFailure 'Setup could not create the shortcut to start Friday.' `
    -HumanFix 'Run this again. If it happens twice, tell Stephen - everything else installed correctly.' `
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
    -Verify { Test-UninstallerRegistered }

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
        Say-Ok 'Friday will only start when you open her.'
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

$manifest = [ordered]@{
    schema_version    = 1
    product           = 'Agent Friday'
    version           = $Version
    installed_at      = (Get-Date).ToString('o')
    install_root      = $InstallRoot
    python_version    = $script:Sources.python.version
    shortcuts         = @($shortcutsCreated)
    autostart_enabled = $autostartOn
    ollama            = [ordered]@{
        installed_by_this_installer = [bool]$ollamaOutcome.WeInstalledIt
        method                      = [string]$ollamaOutcome.Method
        models_pulled               = @($modelTagsPulled)
    }
    uninstall_reg_key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday'
    _note = @(
        'The uninstaller reads this file so it removes exactly what was created and nothing else.',
        'In particular it only offers to remove Ollama if installed_by_this_installer is true,',
        'and it only removes the model tags listed in models_pulled - not every model on the machine.'
    )
}
[System.IO.File]::WriteAllText($ManifestPath, ($manifest | ConvertTo-Json -Depth 6),
                               (New-Object System.Text.UTF8Encoding($false)))
Write-Log "Manifest written: $ManifestPath" 'OK'

Complete-Install

# =========================================================================
#  Done
# =========================================================================

$warns = Get-InstallWarnings
Say ''
Say "  $($script:C.Green)$($script:C.Bold)Friday is installed.$($script:C.Reset)"
Say ''
if ($warns.Count -gt 0) {
    Say '  A couple of optional parts did not install. Friday works; some'
    Say '  things she can do are switched off. She will tell you which if'
    Say '  you ask her what she can do on this laptop.'
    Say ''
}
Say '  There is an Agent Friday icon on the desktop. Double-click it.'
Say '  The first start takes a minute or two - she is waking up, not stuck.'
Say ''
Say "  To remove her: Start menu $([char]0x2192) Agent Friday $([char]0x2192) Uninstall Agent Friday."
Say '  Your notes are kept unless you ask it to remove those too.'
Say ''
Say "  $($script:C.Grey)For Stephen: $(Join-Path $LogDir 'LAST-INSTALL-REPORT.md')$($script:C.Reset)"
Say ''

if (-not $Unattended) {
    $go = Read-Host '  Start Friday now? [Y/n]'
    if ($go -eq '' -or $go -match '^[Yy]') {
        Start-Process -FilePath (Join-Path $InstallRoot 'Agent Friday.cmd') -WorkingDirectory $InstallRoot
    }
}

exit 0
