@echo off
setlocal EnableDelayedExpansion
set "REAL7ZA=%~dp0..\node_modules\7zip-bin\win\x64\7za.exe"
set "ARGS="
set "SKIP="
for %%A in (%*) do (
    set "ARG=%%~A"
    if /I "!ARG!"=="-snld" (
        rem skip this flag
    ) else (
        set "ARGS=!ARGS! "%%~A""
    )
)
"%REAL7ZA%" %ARGS%
exit /b 0
