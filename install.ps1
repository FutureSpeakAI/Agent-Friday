# -------------------------------------------------------------
#  Agent Friday - Windows Installer (PowerShell)
#  https://github.com/FutureSpeakAI/Agent-Friday
#
#  Works on Windows PowerShell 5.1 and PowerShell 7+.
#  If Windows blocks the script, see the "SmartScreen / ExecutionPolicy"
#  section in docs/INSTALLATION.md (short version: run
#    powershell -ExecutionPolicy Bypass -File .\install.ps1 ).
# -------------------------------------------------------------
$ErrorActionPreference = "Stop"

function Write-Color($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

function Write-Banner {
    Write-Host ""
    Write-Color "    ===============================================" Cyan
    Write-Color "         A G E N T   F R I D A Y" Cyan
    Write-Color "       Sovereign Personal AI Assistant" Cyan
    Write-Color "            by FutureSpeak.AI" Cyan
    Write-Color "    ===============================================" Cyan
    Write-Host ""
}

function Test-Command($cmd) {
    $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue)
}

# -- Banner --------------------------------------------------
Write-Banner

# -- Python check --------------------------------------------
Write-Host "[1/8] Checking Python..."
$pythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Test-Command $candidate) {
        $ver = & $candidate --version 2>&1
        if ($ver -match "(\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) {
                $pythonCmd = $candidate
                Write-Host "  Found $ver"
                break
            }
        }
    }
}
if (-not $pythonCmd) {
    Write-Color "  Python 3.10+ is required but was not found." Red
    Write-Host  "  Download from https://www.python.org/downloads/"
    Write-Host  "  During install, tick 'Add python.exe to PATH', then re-run this script."
    exit 1
}

# -- Clone or update -----------------------------------------
$installDir = Join-Path $env:USERPROFILE "Agent-Friday"

Write-Host "[2/8] Setting up project directory..."
if (Test-Path (Join-Path $installDir ".git")) {
    Write-Host "  Existing install found - pulling latest..."
    Push-Location $installDir
    git pull --ff-only 2>$null
    Pop-Location
} elseif (Test-Path $installDir) {
    Write-Host "  Directory exists but is not a git repo. Using as-is."
} else {
    if (Test-Command "git") {
        Write-Host "  Cloning repository..."
        git clone https://github.com/FutureSpeakAI/Agent-Friday.git $installDir
        if ($LASTEXITCODE -ne 0) {
            Write-Color "  git clone failed (check your network / proxy)." Red
            exit 1
        }
    } else {
        Write-Color "  Git is not installed. Please install Git first:" Red
        Write-Host  "  https://git-scm.com/download/win"
        exit 1
    }
}

Set-Location $installDir

# -- Virtual environment -------------------------------------
Write-Host "[3/8] Creating Python virtual environment..."
if (-not (Test-Path "venv")) {
    & $pythonCmd -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Color "  Failed to create the virtual environment." Red
        Write-Host  "  On Windows this usually means the 'venv' module is missing -"
        Write-Host  "  reinstall Python from python.org (the standard installer includes it)."
        exit 1
    }
}
$venvPython = Join-Path $installDir "venv\Scripts\python.exe"
$venvPip = Join-Path $installDir "venv\Scripts\pip.exe"

# -- Dependencies --------------------------------------------
# Prefer the pyproject install (lands the `friday` command + all optional
# capability groups). Fall back to requirements.txt if that path errors.
Write-Host "[4/8] Installing Agent Friday + all optional capabilities..."
Write-Host "  (first run downloads a few hundred MB - this can take several minutes)"
& $venvPython -m pip install --upgrade pip --quiet

$installed = $false
if (Test-Path "pyproject.toml") {
    & $venvPip install -e ".[all]"
    if ($LASTEXITCODE -eq 0) {
        $installed = $true
        Write-Color "  Agent Friday + optional capabilities installed." Green
    } else {
        Write-Color "  Full install hit an error - falling back to the core dependency set." Yellow
    }
}
if (-not $installed -and (Test-Path "requirements.txt")) {
    & $venvPip install -r requirements.txt
    if ($LASTEXITCODE -eq 0) {
        $installed = $true
        Write-Color "  Core dependencies installed." Green
    }
}
if (-not $installed) {
    Write-Color "  Dependency install failed. Common causes:" Red
    Write-Host  "    * No internet / behind a proxy - set HTTP_PROXY / HTTPS_PROXY"
    Write-Host  "    * Missing a C/Rust build toolchain for an optional package"
    Write-Host  "  Friday still runs without the optional extras; you can retry later with:"
    Write-Host  "    venv\Scripts\pip.exe install -e ."
    exit 1
}

# -- API keys ------------------------------------------------
Write-Host "[5/8] API key configuration..."
$fridayDir = Join-Path $env:USERPROFILE ".friday"
$settingsPath = Join-Path $fridayDir "settings.json"

if (-not (Test-Path $fridayDir)) {
    New-Item -ItemType Directory -Path $fridayDir -Force | Out-Null
}

$settings = @{}
if (Test-Path $settingsPath) {
    try {
        $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
    } catch {
        $settings = @{}
    }
}

$hasAnthropic = $settings.anthropic_api_key -or $env:ANTHROPIC_API_KEY
$hasGemini = $settings.gemini_api_key -or $env:GEMINI_API_KEY

Write-Host "  You can skip these now and add keys later in Settings."
Write-Host "  With no keys, Friday opens in DEMO MODE so you can explore the UI."

if (-not $hasAnthropic) {
    Write-Host ""
    Write-Host "  Anthropic API key (Claude - primary reasoning)."
    Write-Host "  Get one at: https://console.anthropic.com/settings/keys"
    $key = Read-Host "  Enter your Anthropic API key (or press Enter to skip)"
    if ($key) {
        $env:ANTHROPIC_API_KEY = $key
        Write-Host "  Set ANTHROPIC_API_KEY for this session."
    }
}

if (-not $hasGemini) {
    Write-Host ""
    Write-Host "  Google Gemini API key (voice mode + creative)."
    Write-Host "  Get one at: https://aistudio.google.com/apikey"
    $key = Read-Host "  Enter your Gemini API key (or press Enter to skip)"
    if ($key) {
        $env:GEMINI_API_KEY = $key
        Write-Host "  Set GEMINI_API_KEY for this session."
    }
}

# -- GPU detection + Ollama ----------------------------------
Write-Host "[6/8] Checking GPU and local model support..."
$hasGpu = $false
try {
    $gpuInfo = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA|AMD|Radeon" }
    if ($gpuInfo) {
        $hasGpu = $true
        Write-Host "  GPU detected: $($gpuInfo[0].Name)"
    }
} catch {}

if ($hasGpu) {
    if (Test-Command "ollama") {
        Write-Host "  Ollama is already installed."
        Write-Host "  Tip: run 'ollama pull llama3.2' for a local model."
    } else {
        Write-Host ""
        Write-Host "  GPU detected! Ollama enables local AI models for vault privacy."
        $installOllama = Read-Host "  Install Ollama? (y/N)"
        if ($installOllama -eq "y" -or $installOllama -eq "Y") {
            try {
                Write-Host "  Downloading Ollama installer..."
                $ollamaUrl = "https://ollama.com/download/OllamaSetup.exe"
                $ollamaPath = Join-Path $env:TEMP "OllamaSetup.exe"
                Invoke-WebRequest -Uri $ollamaUrl -OutFile $ollamaPath -UseBasicParsing
                Write-Host "  Running Ollama installer..."
                Start-Process -FilePath $ollamaPath -Wait
                Write-Host "  Ollama installed. Run 'ollama pull llama3.2' after setup."
            } catch {
                Write-Color "  Ollama download failed (non-fatal) - install later from https://ollama.com" Yellow
            }
        }
    }
} else {
    Write-Host "  No dedicated GPU detected. Ollama (local models) skipped."
    Write-Host "  Cloud models will handle all requests."
}

# -- Build UI ------------------------------------------------
Write-Host "[7/8] Building UI..."
if (Test-Path "build_ui.py") {
    & $venvPython build_ui.py
    Write-Host "  UI built successfully."
} else {
    Write-Host "  build_ui.py not found - skipping UI build."
}

# Post-install health check (non-fatal).
if (Test-Path "friday_cli.py") {
    Write-Host "  Running post-install health check..."
    try { & $venvPython friday_cli.py health } catch {}
}

# -- Start script + shortcut ---------------------------------
Write-Host "[8/8] Creating start script..."
$startBat = Join-Path $installDir "start.bat"
$batContent = @"
@echo off
title Agent Friday
cd /d "$installDir"
call venv\Scripts\activate.bat
$(if ($env:ANTHROPIC_API_KEY) { "set ANTHROPIC_API_KEY=$($env:ANTHROPIC_API_KEY)" })  # pragma: allowlist secret
$(if ($env:GEMINI_API_KEY) { "set GEMINI_API_KEY=$($env:GEMINI_API_KEY)" })  # pragma: allowlist secret
python server.py
pause
"@
Set-Content -Path $startBat -Value $batContent -Encoding utf8

# Desktop shortcut
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "Agent Friday.lnk"
try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $startBat
    $shortcut.WorkingDirectory = $installDir
    $shortcut.Description = "Launch Agent Friday"
    $shortcut.Save()
    Write-Host "  Desktop shortcut created."
} catch {
    Write-Host "  Could not create desktop shortcut (non-fatal)."
}

# -- Done ----------------------------------------------------
Write-Host ""
Write-Color "  Installation complete!" Green
Write-Host ""
Write-Host "  To start Agent Friday:"
Write-Host "    1. Double-click 'Agent Friday' on your desktop, or"
Write-Host "    2. Run: $startBat"
Write-Host ""
Write-Host "  Friday will open at http://localhost:3000"
Write-Host "  (If port 3000 is busy, Friday picks the next free port and prints the URL.)"
Write-Host ""
Write-Host "  First run? The setup wizard will guide you through"
Write-Host "  API keys, model selection, and voice configuration."
Write-Host ""
