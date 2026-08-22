@echo off
rem ======================================================================
rem  Agent Friday - double-click this to install.
rem
rem  This tiny wrapper exists for one reason: PowerShell's default execution
rem  policy blocks unsigned .ps1 files, and the standard workaround is to
rem  tell the user to run Set-ExecutionPolicy first. That is a sentence no
rem  non-technical person should ever have to read, and telling someone to
rem  lower a security setting to install your software is a bad habit to
rem  teach.
rem
rem  -ExecutionPolicy Bypass here applies to THIS ONE PowerShell process and
rem  nothing else. It does not change any setting on the computer, and it
rem  leaves nothing behind when the process ends.
rem
rem  Nothing in here needs administrator rights. If Windows asks you to
rem  approve an administrator prompt while running this, something is wrong -
rem  stop and ask Stephen.
rem ======================================================================

title Agent Friday - Setup

rem Some corporate images ship PowerShell 5.1 only; that is what this
rem installer targets, so we deliberately do NOT prefer pwsh.exe here.
where powershell.exe >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Setup could not start.
  echo.
  echo   This computer does not seem to have Windows PowerShell, which
  echo   Windows normally includes. Please tell Stephen.
  echo.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -NoLogo -File "%~dp0install.ps1" %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo   Setup did not finish. Nothing was left half-installed.
  echo.
  pause
)

exit /b %RC%
