# Build the Windows ProjxDiff.exe via PyInstaller.
#
# Run from the project root in PowerShell:
#     .\scripts\build_windows.ps1
#
# Requires Python 3.10+ with tkinter included (the python.org installer does
# this by default; check the "tcl/tk and IDLE" option if you customize).
# The build artifact lands in dist\ProjxDiff.exe.

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

& $python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: $python does not have tkinter available."
    Write-Host "Reinstall Python from https://www.python.org/downloads/ and"
    Write-Host "check the 'tcl/tk and IDLE' option during install."
    exit 1
}

& $python -m pip install --quiet --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }

& $python -m PyInstaller dw_compare.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Built: $(Resolve-Path dist\ProjxDiff.exe)"
Write-Host "Smoke test:"
Write-Host "    .\dist\ProjxDiff.exe"
