@echo off
REM ============================================================
REM  Agent Friday by FutureSpeak.AI - Windows installer (cmd)
REM  Creates a venv, installs Friday + all optional capabilities,
REM  builds the UI, and runs a post-install health check.
REM
REM  Prefer install.ps1 for the full guided experience (GPU/Ollama
REM  detection, API-key prompts, desktop shortcut). This .bat is the
REM  minimal no-PowerShell path.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ==== Agent Friday installer (Windows) ====
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.10+ was not found on PATH.
  echo         Install it from https://www.python.org/downloads/ and tick
  echo         "Add python.exe to PATH" during setup, then re-run this script.
  exit /b 1
)

if not exist venv (
  echo [1/4] Creating virtual environment ^(venv^)...
  python -m venv venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    echo         Reinstall Python from python.org ^(includes the venv module^).
    exit /b 1
  )
) else (
  echo [1/4] Reusing existing venv
)

call venv\Scripts\activate.bat

echo [2/4] Installing Agent Friday + all optional capabilities...
echo       ^(first run downloads a few hundred MB - this can take several minutes^)
python -m pip install --upgrade pip --quiet
python -m pip install -e .[all]
if errorlevel 1 (
  echo [WARN] Full install hit an error; falling back to requirements.txt core set.
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check your network/proxy, then retry:
    echo         venv\Scripts\pip.exe install -e .
    exit /b 1
  )
)

REM Tier-1 local voice (CPU) is included above. Premium GPU voice (NVIDIA NeMo,
REM Tier-2) is a large opt-in download not installed here - use install.ps1 for
REM the guided GPU prompt, or install manually:
REM   venv\Scripts\pip.exe install torch --index-url https://download.pytorch.org/whl/cu124
REM   venv\Scripts\pip.exe install -e .[voice-local-gpu]
REM Then pick Settings -^> Audio ^& Voice -^> Voice Engine -^> Local GPU (NeMo).

echo [3/4] Building the UI ^(index.html^)...
python build_ui.py

echo [4/4] Post-install health check...
python friday_cli.py health

echo.
echo ==== Done ====
echo Start Agent Friday with:   python server.py
echo (or)                       friday
echo Friday opens at http://localhost:3000
echo (If port 3000 is busy, Friday picks the next free port and prints the URL.)
echo.
endlocal
