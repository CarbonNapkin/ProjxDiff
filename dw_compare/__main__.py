#!/usr/bin/env python3
"""
Projx Diff (DriveWorks project comparison) - CLI Entry Point
"""

from __future__ import annotations

import sys
import json
import os
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
from .jsondiff import build_diff
from .update_check import check_for_update, DOWNLOAD_PAGE

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


def resolve_db_names(label: str, server: str, database: str, index,
                     user: str = '', password: str = '', sql_auth: bool = False):
    """Resolve component/model names (CCRef/TrId), property names
    (CPRef/CERef), and property TYPES for one side of the diff against a
    DriveWorks group database. Returns
    (resolved, prop_resolved, prop_types, error) — error is None when a
    database was supplied and connected (even if it resolved 0 ids — that's
    not a connection failure), or a short, actionable message when the
    connection itself failed (see dbsource._classify_connect_error).
    Returns ({}, {}, {}, None) — not an error — when server/database are
    blank, since that just means this side wasn't configured to use a
    database. prop_types is {norm(id): type_guid}, the authoritative signal
    for the Rule Changes Type column (see components.TYPE_GUID_KIND). This
    is the shared primitive behind both the CLI's --old-db-*/--new-db-*
    flags and the GUI's database panel."""
    if not server or not database:
        return {}, {}, {}, None

    from . import dbsource, idmap, components

    db = dbsource.DwDatabase(label=label, server=server, database=database,
                             user=user, password=password, trusted=not sql_auth)
    # Connect eagerly so a failure is caught deterministically here and
    # reported clearly, instead of surfacing later as "0 ids resolved" with
    # the real reason buried in the log.
    if not db.connect():
        return {}, {}, {}, db.last_error or "Could not connect to the database."

    resolver = idmap.IdResolver(db=db)
    resolved = components.resolve_names(index, resolver)

    ccrefs = set(index.trid_to_ccref.values())
    if ccrefs:
        prop_resolved, prop_types = db.fetch_captured_property_names_and_types(ccrefs)
    else:
        prop_resolved, prop_types = {}, {}
    db.close()
    print(f"  [{label}] {resolver.report().strip()}")
    return resolved, prop_resolved, prop_types, None


def resolve_side_names(side: str, args, index):
    """CLI wrapper around resolve_db_names: reads the --{side}-db-* argparse
    fields and the DW_SQL_PASSWORD_{SIDE} env var (never a command-line
    password — see dbsource.py)."""
    server = getattr(args, f'{side}_db_server')
    database = getattr(args, f'{side}_db_database')
    if not server or not database:
        return {}, {}, {}, None

    sql_auth = getattr(args, f'{side}_db_sql_auth')
    user = getattr(args, f'{side}_db_user')
    password = os.environ.get(f'DW_SQL_PASSWORD_{side.upper()}', '') if sql_auth else ''
    if sql_auth and not password:
        error = (f"--{side}-db-sql-auth given but DW_SQL_PASSWORD_{side.upper()} "
                 f"is not set — skipping database name resolution for this side.")
        print(f"  ⚠ [{side}] {error}")
        return {}, {}, {}, error

    return resolve_db_names(side, server, database, index,
                            user=user, password=password, sql_auth=sql_auth)


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
    parser.add_argument('-o', '--output', type=Path, default=None,
                       help='Output file (default: dw_comparison.html / .json). '
                            'With --format both, the JSON lands next to the HTML '
                            'with a .json extension.')
    parser.add_argument('-f', '--format', choices=['html', 'json', 'both'], default='html',
                       help='Output format: html report, json change data, or both '
                            '(default: html)')
    parser.add_argument('--no-open', action='store_true',
                       help='Do not auto-open report in browser')
    parser.add_argument('--gui', action='store_true',
                       help='Launch the graphical UI instead of running on the command line')
    parser.add_argument('--sync', type=Path, metavar='CONFIG',
                       help='Run the nightly archive sync with the given config JSON')
    parser.add_argument('--census', type=Path, metavar='CONFIG',
                       help='Scan projects/users into the sync census; combine with '
                            '--map/--track/--ignore to manage dispositions')
    parser.add_argument('--dashboard', type=Path, metavar='CONFIG',
                       help='Regenerate the work-metrics dashboard for the given config')
    parser.add_argument('--dry-run', action='store_true',
                       help='(with --sync) report changes without committing or recording')
    parser.add_argument('--map', action='append', default=[], metavar='RAW=IDENTITY',
                       help='(with --census) map a display name to "Name <email>"')
    parser.add_argument('--track', action='append', default=[], metavar='PROJECT',
                       help='(with --census) set a project disposition to track')
    parser.add_argument('--ignore', action='append', default=[], metavar='PROJECT',
                       help='(with --census) set a project disposition to ignore')
    parser.add_argument('--no-scan', action='store_true',
                       help='(with --census) apply edits only; skip the share scan')
    parser.add_argument('--init-config', type=Path, metavar='FOLDER',
                       help='Create a starter site config at FOLDER/config.json '
                            '(archives, metrics, and dashboard live under FOLDER)')
    parser.add_argument('--add-source', action='append', default=[], metavar='NAME=FOLDER',
                       help='(with --census) add an environment group to the config, '
                            'then scan it')
    # Optional group-database connections, one per side, for resolving
    # CCRef/TrId component ids and CPRef/CERef property ids to readable
    # names (Models and Rule Changes sections). Either side may be omitted;
    # those sections then fall back to raw GUIDs. Windows integrated auth
    # is the default — no password is ever accepted on the command line
    # (see dbsource.py); use --old-db-sql-auth / --new-db-sql-auth plus the
    # DW_SQL_PASSWORD_OLD / DW_SQL_PASSWORD_NEW env vars for SQL auth.
    for side in ('old', 'new'):
        parser.add_argument(f'--{side}-db-server', default='',
                           help=f'SQL Server for the {side} project\'s group database '
                                f'(e.g. SQLBOX\\DWGROUP)')
        parser.add_argument(f'--{side}-db-database', default='',
                           help=f'Group database name for the {side} project')
        parser.add_argument(f'--{side}-db-user', default='',
                           help=f'SQL Server login for the {side} project '
                                f'(only with --{side}-db-sql-auth)')
        parser.add_argument(f'--{side}-db-sql-auth', action='store_true',
                           help=f'Use SQL Server auth for the {side} project '
                                'instead of Windows integrated auth')

    parser.add_argument('-V', '--version', action='version',
                       version=f'Projx Diff {__version__}')

    args = parser.parse_args()

    if args.gui:
        from .gui import main as gui_main
        gui_main()
        return

    if args.init_config:
        from .sync import init_site
        cfg_path = init_site(args.init_config)
        print(f'Created {cfg_path}')
        print('Next, add an environment group and scan it:')
        print(f'  python -m dw_compare --census "{cfg_path}" '
              '--add-source "prod=C:/Path/To/Projects"')
        print('(or in the app: Tools > Manage Nightly Sync)')
        return

    if args.sync:
        from .sync import main as sync_main
        sys.exit(sync_main([str(args.sync)] + (['--dry-run'] if args.dry_run else [])))

    if args.census:
        from .census import main as census_main
        argv = [str(args.census)]
        for s in args.add_source:
            argv += ['--add-source', s]
        for m in args.map:
            argv += ['--map', m]
        for t in args.track:
            argv += ['--track', t]
        for i in args.ignore:
            argv += ['--ignore', i]
        if args.no_scan:
            argv += ['--no-scan']
        sys.exit(census_main(argv))

    if args.dashboard:
        from .dashboard import main as dashboard_main
        sys.exit(dashboard_main([str(args.dashboard)]))
    
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
    
    if args.output is None:
        args.output = Path('dw_comparison.json' if args.format == 'json'
                           else 'dw_comparison.html')

    html_path = None
    old_db_error = new_db_error = None
    if args.format in ('html', 'both'):
        old_resolved, old_prop_resolved, old_prop_types, old_db_error = \
            resolve_side_names('old', args, old_proj.component_index)
        new_resolved, new_prop_resolved, new_prop_types, new_db_error = \
            resolve_side_names('new', args, new_proj.component_index)

        html_path = args.output
        print("Generating comparison report...")
        html = generate_html_report(old_proj, new_proj, old_name, new_name,
                                    old_resolved, new_resolved,
                                    old_prop_resolved, new_prop_resolved,
                                    old_prop_types, new_prop_types)
        html_path.write_text(html, encoding='utf-8')
        print(f"✅ Report saved to: {html_path}")
        # A DB connection failure doesn't stop the report (it still generates
        # with raw GUIDs), so restate it plainly next to the success message.
        if old_db_error:
            print(f"⚠️  Old-side database: {old_db_error}")
        if new_db_error:
            print(f"⚠️  New-side database: {new_db_error}")
        if old_db_error or new_db_error:
            print("   Models and Rule Changes will show raw ids instead of names for that side.")

    if args.format in ('json', 'both'):
        json_path = args.output.with_suffix('.json') if args.format == 'both' else args.output
        print("Building JSON diff...")
        diff = build_diff(old_proj, new_proj, old_name, new_name)
        json_path.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + '\n',
                             encoding='utf-8')
        s = diff['summary']
        print(f"✅ JSON diff saved to: {json_path} "
              f"(+{s['added']} -{s['removed']} ~{s['modified']}, {s['unchanged']} unchanged)")

    # Free, fail-silent update check (notify only — never downloads/installs).
    newer = check_for_update()
    if newer:
        print(f"\nℹ️  Update available: v{newer} — {DOWNLOAD_PAGE}")

    # Auto-open in browser. Use as_uri() so the file:// URL is well-formed on
    # Windows (drive letters / backslashes) and has spaces percent-encoded.
    # JSON-only runs are for scripting; they never launch a browser.
    if html_path is not None and not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == '__main__':
    main()
