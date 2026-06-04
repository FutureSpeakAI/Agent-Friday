# ─────────────────────────────────────────────────────────────
#  Agent Friday — Windows Installer (PowerShell)
#  https://github.com/FutureSpeakAI/Agent-Friday
# ─────────────────────────────────────────────────────────────
#Requires -Version 5.1
$ErrorActionPreference = "Stop"

function Write-Banner {
    $cyan = "`e[36m"; $reset = "`e[0m"; $bold = "`e[1m"
    Write-Host ""
    Write-Host "${cyan}${bold}    ╔═══════════════════════════════════════════════╗${reset}"
    Write-Host "${cyan}${bold}    ║           A G E N T   F R I D A Y             ║${reset}"
    Write-Host "${cyan}${bold}    ║       Sovereign Personal AI Assistant         ║${reset}"
    Write-Host "${cyan}${bold}    ║            by FutureSpeak.AI                  ║${reset}"
    Write-Host "${cyan}${bold}    ╚═══════════════════════════════════════════════╝${reset}"
    Write-Host ""
}

function Test-Command($cmd) {
    $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue)
}

# ── Banner ──────────────────────────────────────────────────
Write-Banner

# ── Python check ────────────────────────────────────────────
Write-Host "[1/8] Checking Python..."
$pythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Test-Command $candidate) {
        $ver = & $candidate --version 2>&1
        if ($ver -match "(\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 10) {
                $pythonCmd = $candidate
                Write-Host "  Found $ver"
                break
            }
        }
    }
}
if (-not $pythonCmd) {
    Write-Host "`e[31m  Python 3.10+ is required but not found.`e[0m"
    Write-Host "  Download from https://www.python.org/downloads/"
    exit 1
}

# ── Clone or update ─────────────────────────────────────────
$installDir = Join-Path $env:USERPROFILE "Agent-Friday"

Write-Host "[2/8] Setting up project directory..."
if (Test-Path (Join-Path $installDir ".git")) {
    Write-Host "  Existing install found — pulling latest..."
    Push-Location $installDir
    git pull --ff-only 2>$null
    Pop-Location
} elseif (Test-Path $installDir) {
    Write-Host "  Directory exists but is not a git repo. Using as-is."
} else {
    if (Test-Command "git") {
        Write-Host "  Cloning repository..."
        git clone https://github.com/FutureSpeakAI/Agent-Friday.git $installDir
    } else {
        Write-Host "`e[31m  Git is not installed. Please install Git first:`e[0m"
        Write-Host "  https://git-scm.com/download/win"
        exit 1
    }
}

Set-Location $installDir

# ── Virtual environment ─────────────────────────────────────
Write-Host "[3/8] Creating Python virtual environment..."
if (-not (Test-Path "venv")) {
    & $pythonCmd -m venv venv
}
$venvPython = Join-Path $installDir "venv\Scripts\python.exe"
$venvPip = Join-Path $installDir "venv\Scripts\pip.exe"

# ── Dependencies ────────────────────────────────────────────
Write-Host "[4/8] Installing dependencies..."
& $venvPip install --upgrade pip --quiet
if (Test-Path "requirements.txt") {
    & $venvPip install -r requirements.txt --quiet
    Write-Host "  Core dependencies installed."
} else {
    Write-Host "  No requirements.txt found — skipping pip install."
}

# ── API keys ────────────────────────────────────────────────
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

if (-not $hasAnthropic) {
    Write-Host ""
    Write-Host "  Anthropic API key (required for Claude)."
    Write-Host "  Get one at: https://console.anthropic.com/settings/keys"
    $key = Read-Host "  Enter your Anthropic API key (or press Enter to skip)"
    if ($key) {
        $env:ANTHROPIC_API_KEY = $key
        Write-Host "  Set ANTHROPIC_API_KEY for this session."
    }
}

if (-not $hasGemini) {
    Write-Host ""
    Write-Host "  Google Gemini API key (required for voice mode + creative)."
    Write-Host "  Get one at: https://aistudio.google.com/apikey"
    $key = Read-Host "  Enter your Gemini API key (or press Enter to skip)"
    if ($key) {
        $env:GEMINI_API_KEY = $key
        Write-Host "  Set GEMINI_API_KEY for this session."
    }
}

# ── GPU detection + Ollama ──────────────────────────────────
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
            Write-Host "  Downloading Ollama installer..."
            $ollamaUrl = "https://ollama.com/download/OllamaSetup.exe"
            $ollamaPath = Join-Path $env:TEMP "OllamaSetup.exe"
            Invoke-WebRequest -Uri $ollamaUrl -OutFile $ollamaPath -UseBasicParsing
            Write-Host "  Running Ollama installer..."
            Start-Process -FilePath $ollamaPath -Wait
            Write-Host "  Ollama installed. Run 'ollama pull llama3.2' after setup."
        }
    }
} else {
    Write-Host "  No dedicated GPU detected. Ollama (local models) skipped."
    Write-Host "  Cloud models will handle all requests."
}

# ── Build UI ────────────────────────────────────────────────
Write-Host "[7/8] Building UI..."
if (Test-Path "build_ui.py") {
    & $venvPython build_ui.py
    Write-Host "  UI built successfully."
} else {
    Write-Host "  build_ui.py not found — skipping UI build."
}

# ── Start script + shortcut ─────────────────────────────────
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

# ── Done ────────────────────────────────────────────────────
Write-Host ""
Write-Host "`e[32m  Installation complete!`e[0m"
Write-Host ""
Write-Host "  To start Agent Friday:"
Write-Host "    1. Double-click 'Agent Friday' on your desktop, or"
Write-Host "    2. Run: $startBat"
Write-Host ""
Write-Host "  Friday will open at http://localhost:3000"
Write-Host ""
Write-Host "  First run? The setup wizard will guide you through"
Write-Host "  API keys, model selection, and voice configuration."
Write-Host ""
