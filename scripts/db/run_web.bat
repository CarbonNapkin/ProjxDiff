@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on this PC.
    echo Install it from https://www.python.org/downloads/
    echo   ^(check "Add python.exe to PATH" during install^)
    echo then double-click this file again.
    pause
    exit /b 1
)

python webapp.py
pause
