#!/usr/bin/env bash
# Build the macOS ProjxDiff.app via PyInstaller.
#
# Run from the project root:
#     ./scripts/build_mac.sh
#
# Requires Python 3.10+ with tkinter and PyInstaller available.
# The build artifact lands in dist/ProjxDiff.app.

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
    echo "Error: $PYTHON does not have tkinter available."
    echo "On macOS the bundled /usr/bin/python3 ships with tkinter."
    echo "If you use Homebrew Python, install Tk with:"
    echo "    brew install python-tk"
    exit 1
fi

"$PYTHON" -m pip install --quiet --upgrade pyinstaller

rm -rf build dist

"$PYTHON" -m PyInstaller dw_compare.spec --clean --noconfirm

echo
echo "Built: $(pwd)/dist/ProjxDiff.app"
echo "Smoke test:"
echo "    open dist/ProjxDiff.app"
