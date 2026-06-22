#!/usr/bin/env python3
"""
Projx Diff (DriveWorks project comparison) - CLI Entry Point
"""

from __future__ import annotations

import sys
import argparse
import webbrowser
import zipfile
import tempfile
import shutil
import atexit
from pathlib import Path

from ._version import __version__
from .parsers import load_project
from .report import generate_html_report
from .update_check import check_for_update, RELEASES_PAGE

# Track temp dirs for cleanup
_temp_dirs = []


def extract_driveprojx(file_path: Path) -> Path:
    """Extract .driveprojx file (it's just a zip) to temp directory"""
    temp_dir = tempfile.mkdtemp(prefix='dw_compare_')
    _temp_dirs.append(temp_dir)
    
    print(f"  Extracting {file_path.name}...")
    base = Path(temp_dir).resolve()
    with zipfile.ZipFile(file_path, 'r') as zf:
        for member in zf.namelist():
            dest = (base / member).resolve()
            if dest != base and not dest.is_relative_to(base):
                raise ValueError(f"Unsafe path in archive (zip slip): {member}")
        zf.extractall(temp_dir)

    return Path(temp_dir)


def cleanup_temp_dirs():
    """Remove any temp directories created during extraction. Pops as it goes so
    a second run (e.g. in the GUI) doesn't re-attempt rmtree on already-deleted
    dirs or let the list grow unbounded across runs."""
    while _temp_dirs:
        temp_dir = _temp_dirs.pop()
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


# Register cleanup on exit
atexit.register(cleanup_temp_dirs)


def resolve_input(path: Path) -> Path:
    """Resolve input - extract .driveprojx files, pass through folders"""
    if path.is_dir():
        return path
    elif path.suffix.lower() == '.driveprojx' and path.is_file():
        return extract_driveprojx(path)
    else:
        return path  # Let validation catch invalid paths


def _default_output_dir() -> Path:
    """A writable, discoverable directory for the GUI's default report path.
    Prefers the Downloads folder, falls back to the home folder. (Both exist by
    default on macOS and Windows.)"""
    downloads = Path.home() / 'Downloads'
    return downloads if downloads.is_dir() else Path.home()


def resolve_output_path(raw: str) -> Path:
    """Resolve a GUI output path to an absolute location in a writable folder.

    A bare filename (or empty input) must NOT resolve against the process cwd:
    a double-clicked packaged app launches with a cwd that may be read-only
    ('/' on macOS via Finder; C:\\Windows\\System32 or Program Files on Windows),
    so writing 'dw_comparison.html' there fails with 'Read-only file system'.
    Relative paths are anchored under Downloads/home instead; absolute paths
    (e.g. chosen via the Save dialog) are used as-is.
    """
    raw = (raw or '').strip()
    p = Path(raw) if raw else Path('dw_comparison.html')
    if not p.is_absolute():
        p = _default_output_dir() / p
    return p


EXCLUDED_DIRS = {
    'dw_compare', '__pycache__', 'node_modules',
    'dist', 'build', 'venv', '.venv', 'env',
}


def find_project_folders() -> tuple[Path, Path] | None:
    """Auto-detect two project folders or .driveprojx files in current directory"""
    cwd = Path.cwd()

    # Get all subdirectories (excluding hidden, the package itself, and common build dirs)
    subdirs = sorted(
        d for d in cwd.iterdir()
        if d.is_dir() and not d.name.startswith('.') and d.name not in EXCLUDED_DIRS
    )
    
    # Get all .driveprojx files
    projx_files = sorted(cwd.glob('*.driveprojx'))
    
    # Look for common naming patterns in folders
    patterns = [
        ('old', 'new'),
        ('prod', 'dev'),
        ('production', 'development'),
        ('master', 'branch'),
        ('v1', 'v2'),
        ('before', 'after'),
    ]
    
    for old_name, new_name in patterns:
        old_matches = [d for d in subdirs if old_name in d.name.lower()]
        new_matches = [d for d in subdirs if new_name in d.name.lower()]
        if old_matches and new_matches:
            return old_matches[0], new_matches[0]
    
    # Check .driveprojx files for patterns
    for old_name, new_name in patterns:
        old_matches = [f for f in projx_files if old_name in f.stem.lower()]
        new_matches = [f for f in projx_files if new_name in f.stem.lower()]
        if old_matches and new_matches:
            return old_matches[0], new_matches[0]
    
    # If exactly two folders, use them
    if len(subdirs) == 2:
        return subdirs[0], subdirs[1]
    
    # If exactly two .driveprojx files, use them
    if len(projx_files) == 2:
        return projx_files[0], projx_files[1]
    
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Compare two DriveWorks™ projects and generate HTML report',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
    python -m dw_compare old.driveprojx new.driveprojx
    python -m dw_compare old_folder/ new_folder/ -o comparison.html
    
    # Or just run in a folder containing two projects:
    python -m dw_compare
        ''')
    
    parser.add_argument('old_project', type=Path, nargs='?', 
                       help='Path to old project folder or .driveprojx file')
    parser.add_argument('new_project', type=Path, nargs='?', 
                       help='Path to new project folder or .driveprojx file')
    parser.add_argument('-o', '--output', type=Path, default=Path('dw_comparison.html'),
                       help='Output HTML file (default: dw_comparison.html)')
    parser.add_argument('--no-open', action='store_true',
                       help='Do not auto-open report in browser')
    parser.add_argument('--gui', action='store_true',
                       help='Launch the graphical UI instead of running on the command line')
    parser.add_argument('-V', '--version', action='version',
                       version=f'Projx Diff {__version__}')

    args = parser.parse_args()

    if args.gui:
        from .gui import main as gui_main
        gui_main()
        return
    
    # Auto-detect projects if not provided
    if args.old_project is None or args.new_project is None:
        print("No projects specified, looking for project folders or .driveprojx files...")
        found = find_project_folders()
        if found:
            args.old_project, args.new_project = found
            print(f"  Found: {args.old_project.name} → {args.new_project.name}")
        else:
            print("Error: Could not auto-detect projects.")
            print("Provide two folder or .driveprojx arguments, or place exactly two")
            print("projects in the same directory as this script.")
            sys.exit(1)
    
    # Store original names before extracting
    old_name = args.old_project.stem if args.old_project.suffix.lower() == '.driveprojx' else args.old_project.name
    new_name = args.new_project.stem if args.new_project.suffix.lower() == '.driveprojx' else args.new_project.name
    
    # Resolve inputs (extract .driveprojx if needed)
    old_folder = resolve_input(args.old_project)
    new_folder = resolve_input(args.new_project)
    
    if not old_folder.is_dir():
        print(f"Error: {args.old_project} is not a directory or .driveprojx file")
        sys.exit(1)
    if not new_folder.is_dir():
        print(f"Error: {args.new_project} is not a directory or .driveprojx file")
        sys.exit(1)
    
    print(f"Loading old project: {old_name}")
    old_proj = load_project(old_folder)
    
    print(f"Loading new project: {new_name}")
    new_proj = load_project(new_folder)
    
    print("Generating comparison report...")
    html = generate_html_report(old_proj, new_proj, old_name, new_name)
    
    args.output.write_text(html, encoding='utf-8')
    print(f"✅ Report saved to: {args.output}")
    
    # Free, fail-silent update check (notify only — never downloads/installs).
    newer = check_for_update()
    if newer:
        print(f"\nℹ️  Update available: v{newer} — {RELEASES_PAGE}")

    # Auto-open in browser. Use as_uri() so the file:// URL is well-formed on
    # Windows (drive letters / backslashes) and has spaces percent-encoded.
    if not args.no_open:
        webbrowser.open(args.output.resolve().as_uri())


if __name__ == '__main__':
    main()
