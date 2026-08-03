#!/usr/bin/env python3
"""Nightly DriveWorks project archive + change tracking.

Copies every .driveprojx from a source directory (typically a UNC share) to a
local staging area, extracts each into a git archive repo (one top-level folder
per project), and — for projects whose content changed since the last run —
uses dw_compare to build a semantic diff, writes per-project HTML + JSON
reports, records per-category and per-element change rows in a SQLite metrics
database, and commits the new state (one commit per changed project, authored
by the project's owner when the config maps one).

Designed to run headless from Windows Task Scheduler; stdlib-only, and all
console output is plain ASCII so it survives a cp1252 console. Also runs on
macOS/Linux unchanged.

Usage:
    python nightly_sync.py config.json [--dry-run]

--dry-run: scan, extract, and report what changed, but do not touch the
archive repo, the metrics DB, or the reports directory.

See config.example.json and README.md in this folder for setup.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, date
from pathlib import Path

# The dw_compare package lives two levels up from this script
# (<repo>/scripts/nightly_sync/). A config key "tool_repo" overrides this if
# the script is deployed away from a checkout of the tool.
_DEFAULT_TOOL_REPO = Path(__file__).resolve().parents[2]

log = logging.getLogger('nightly_sync')

COPY_ATTEMPTS = 3
COPY_RETRY_DELAY_S = 10
LOCK_STALE_S = 6 * 3600


# ---------------------------------------------------------------- config ----

REQUIRED_KEYS = ('source_dir', 'archive_repo', 'data_dir')

DEFAULTS = {
    'recursive': True,
    'exclude': [],
    'derive_author_from_file': False,
    'author_aliases': {},
    'git_user_name': 'Projx Sync',
    'git_user_email': 'projx-sync@localhost',
    'push': False,
    'remote': '',
    'remove_missing': False,
    'dashboard': True,
    'owners': {},
    'tool_repo': str(_DEFAULT_TOOL_REPO),
}


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(path.read_text(encoding='utf-8')))
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise SystemExit(f'config error: missing required key(s): {", ".join(missing)}')
    for k in ('source_dir', 'archive_repo', 'data_dir', 'tool_repo'):
        cfg[k] = Path(cfg[k])
    return cfg


# ------------------------------------------------------------- utilities ----

def find_projects(source_dir: Path, recursive: bool,
                  exclude: list[str] = (),
                  excluded_out: list = None) -> list[Path]:
    """Every *.driveprojx under source_dir, minus any whose path (relative to
    source_dir, posix, case-insensitive) matches an `exclude` glob. `*` spans
    slashes, so "*archive*" drops anything with an archive folder anywhere in
    its path, and "*/backup/*" drops per-project Backup folders.

    If `excluded_out` is provided, each dropped file is appended to it as a
    (relative_posix_path, matching_pattern) tuple so the caller can report
    exactly what was skipped and why."""
    pattern = '**/*.driveprojx' if recursive else '*.driveprojx'
    pats = [e.lower() for e in exclude]
    out = []
    for p in source_dir.glob(pattern):
        if not p.is_file():
            continue
        rel = p.relative_to(source_dir).as_posix()
        matched = next((pat for pat in pats if fnmatch.fnmatchcase(rel.lower(), pat)), None)
        if matched is not None:
            if excluded_out is not None:
                excluded_out.append((rel, matched))
            continue
        out.append(p)
    return sorted(out)


def copy_with_retries(src: Path, dst: Path) -> None:
    """Copy one file, retrying because a project open in DriveWorks (or a
    share hiccup) can fail transiently."""
    for attempt in range(1, COPY_ATTEMPTS + 1):
        try:
            shutil.copy2(src, dst)
            return
        except OSError as e:
            if attempt == COPY_ATTEMPTS:
                raise
            log.warning('copy failed for %s (attempt %d/%d): %s -- retrying in %ds',
                        src.name, attempt, COPY_ATTEMPTS, e, COPY_RETRY_DELAY_S)
            time.sleep(COPY_RETRY_DELAY_S)


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract a .driveprojx (a zip), refusing path-traversal members."""
    dest.mkdir(parents=True, exist_ok=True)
    base = dest.resolve()
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.namelist():
            target = (base / member).resolve()
            if target != base and not target.is_relative_to(base):
                raise ValueError(f'unsafe path in archive (zip slip): {member}')
        zf.extractall(dest)


def dir_digest(root: Path) -> dict:
    """{relative_posix_path: sha256} for every file under root. Content-only,
    so zip timestamp churn from a no-op re-save does not count as a change."""
    digest = {}
    if not root.is_dir():
        return digest
    for p in sorted(root.rglob('*')):
        if p.is_file():
            digest[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return digest


# ------------------------------------------------------------------- git ----

def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(['git', '-C', str(repo), *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} failed: {proc.stderr.strip()}')
    return proc


def ensure_repo(repo: Path, cfg: dict) -> None:
    init = not (repo / '.git').is_dir()
    if init:
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, 'init')
        # DriveWorks XML must round-trip byte-for-byte; never CRLF-normalize.
        (repo / '.gitattributes').write_text('* -text\n', encoding='utf-8')
    git(repo, 'config', 'user.name', cfg['git_user_name'])
    git(repo, 'config', 'user.email', cfg['git_user_email'])
    if init:
        git(repo, 'add', '.gitattributes')
        git(repo, 'commit', '-m', 'Initialize project archive')
        log.info('initialized archive repo at %s', repo)
    if cfg['remote']:
        have = git(repo, 'remote', check=False).stdout.split()
        if 'origin' not in have:
            git(repo, 'remote', 'add', 'origin', cfg['remote'])
        else:
            git(repo, 'remote', 'set-url', 'origin', cfg['remote'])


def commit_project(repo: Path, project_dir: str, message: str, author: str) -> None:
    git(repo, 'add', '-A', '--', project_dir)
    args = ['commit', '-m', message]
    if author:
        args += ['--author', author]
    git(repo, *args)


# --------------------------------------------------------------- metrics ----

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    projects_seen INTEGER,
    projects_changed INTEGER,
    errors TEXT
);
CREATE TABLE IF NOT EXISTS category_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    project TEXT NOT NULL,
    owner TEXT,
    category TEXT NOT NULL,
    added INTEGER NOT NULL,
    removed INTEGER NOT NULL,
    modified INTEGER NOT NULL,
    unchanged INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS element_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    project TEXT NOT NULL,
    owner TEXT,
    category TEXT NOT NULL,
    element TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cat_date ON category_changes (run_date, project);
CREATE INDEX IF NOT EXISTS idx_elem_date ON element_changes (run_date, project);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Autocommit: each insert lands as it happens, so a crash mid-run cannot
    # leave the metrics DB behind the git commits already made.
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.executescript(SCHEMA)
    return conn


def record_diff(conn: sqlite3.Connection, run_date: str, project: str,
                owner: str, diff: dict) -> None:
    for cat, stats in diff['summary']['categories'].items():
        if stats['added'] or stats['removed'] or stats['modified']:
            conn.execute(
                'INSERT INTO category_changes (run_date, project, owner, category,'
                ' added, removed, modified, unchanged) VALUES (?,?,?,?,?,?,?,?)',
                (run_date, project, owner, cat, stats['added'], stats['removed'],
                 stats['modified'], stats['unchanged']))
    for rec in diff['changes']:
        conn.execute(
            'INSERT INTO element_changes (run_date, project, owner, category,'
            ' element, status) VALUES (?,?,?,?,?,?)',
            (run_date, project, owner, rec['category'], rec['name'], rec['status']))


def record_project_event(conn: sqlite3.Connection, run_date: str, project: str,
                         owner: str, status: str) -> None:
    """A whole project appeared in / vanished from the source share."""
    conn.execute(
        'INSERT INTO element_changes (run_date, project, owner, category,'
        ' element, status) VALUES (?,?,?,?,?,?)',
        (run_date, project, owner, 'project', project, status))


# ---------------------------------------------------------- attribution ----

# DriveWorks writes the user who last saved the project into designMaster.xml
# as "special variables". DWCurrentUserDisplayName is the human name; the email
# is often blank, so name is the primary key.
_DISPLAY_RE = re.compile(r'StoreName="DWCurrentUserDisplayName"\s+Value="([^"]*)"')
_EMAIL_RE = re.compile(r'StoreName="DWCurrentUserEmailAddress"\s+Value="([^"]*)"')


def read_last_saver(project_root: Path) -> tuple[str, str]:
    """(display_name, email) of the DriveWorks user who last saved the project,
    read from its designMaster.xml. ('', '') if unavailable."""
    dm = project_root / 'driveProj' / 'designMaster.xml'
    if not dm.is_file():
        return ('', '')
    text = dm.read_bytes().decode('utf-8', 'replace')
    d = _DISPLAY_RE.search(text)
    e = _EMAIL_RE.search(text)
    return (d.group(1).strip() if d else '',
            e.group(1).strip() if e else '')


def resolve_author(name: str, project_root: Path, cfg: dict) -> str:
    """Git author ("Name <email>") for a project's change. An explicit
    `owners` entry always wins (manual override); otherwise, when
    `derive_author_from_file` is on, use the project's last saver, mapped
    through `author_aliases` to collapse spelling variants onto one identity.
    Returns '' to fall back to the sync's own committer identity."""
    override = cfg['owners'].get(name)
    if override:
        return override
    if not cfg.get('derive_author_from_file'):
        return ''
    display, email = read_last_saver(project_root)
    if not display:
        return ''
    alias = cfg['author_aliases'].get(display)
    if alias:
        return alias
    return f'{display} <{email}>' if email else f'{display} <>'


# ------------------------------------------------------------------ sync ----

def sync_one(zip_path: Path, repo: Path, staging: Path, cfg: dict,
             run_date: str, conn, reports_dir: Path, dry_run: bool):
    """Sync a single project. Returns 'changed', 'new', or 'unchanged'."""
    from dw_compare import load_project, generate_html_report, build_diff

    name = zip_path.stem

    local_zip = staging / zip_path.name
    copy_with_retries(zip_path, local_zip)

    new_dir = staging / name
    safe_extract(local_zip, new_dir)

    # Attribution comes from the freshly-extracted copy (its last-saver), so a
    # changed project is credited to whoever most recently saved it.
    owner = resolve_author(name, new_dir, cfg)

    repo_dir = repo / name
    is_new = not repo_dir.is_dir()

    if not is_new and dir_digest(repo_dir) == dir_digest(new_dir):
        return 'unchanged'

    if is_new:
        # First appearance: archive it, but record only a project-level event.
        # Diffing against nothing would count every element as "added" and
        # poison the work metrics with a one-time explosion.
        log.info('[NEW] %s -- adding to archive', name)
        if not dry_run:
            shutil.copytree(new_dir, repo_dir)
            commit_project(repo, name, f'{name}: added to archive', owner)
            record_project_event(conn, run_date, name, owner, 'added')
        return 'new'

    old_proj = load_project(repo_dir)
    new_proj = load_project(new_dir)
    diff = build_diff(old_proj, new_proj, f'{name} (previous)', f'{name} (current)')
    s = diff['summary']
    log.info('[CHANGED] %s: +%d -%d ~%d', name, s['added'], s['removed'], s['modified'])

    if dry_run:
        return 'changed'

    day_dir = reports_dir / run_date
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f'{name}.json').write_text(
        json.dumps(diff, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (day_dir / f'{name}.html').write_text(
        generate_html_report(old_proj, new_proj, f'{name} (previous)', f'{name} (current)'),
        encoding='utf-8')

    record_diff(conn, run_date, name, owner, diff)

    shutil.rmtree(repo_dir)
    shutil.copytree(new_dir, repo_dir)
    commit_project(repo, name,
                   f'{name}: +{s["added"]} -{s["removed"]} ~{s["modified"]} '
                   f'(nightly sync {run_date})', owner)
    return 'changed'


def _already_marked_removed(conn, project: str) -> bool:
    row = conn.execute(
        "SELECT status FROM element_changes WHERE project=? AND category='project' "
        'ORDER BY id DESC LIMIT 1', (project,)).fetchone()
    return row is not None and row[0] == 'removed'


def handle_missing(seen: set, repo: Path, cfg: dict, run_date: str,
                   conn, dry_run: bool) -> list:
    """Projects present in the archive but no longer on the share. The
    "removed" event is recorded once per disappearance, not re-recorded every
    night the project stays gone."""
    missing = []
    for d in sorted(repo.iterdir()):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        if d.name in seen:
            continue
        missing.append(d.name)
        owner = cfg['owners'].get(d.name, '')
        if dry_run:
            log.info('[MISSING] %s -- gone from source (dry run, no action)', d.name)
            continue
        if not _already_marked_removed(conn, d.name):
            record_project_event(conn, run_date, d.name, owner, 'removed')
        if cfg['remove_missing']:
            log.info('[MISSING] %s -- removing from archive (remove_missing=true)', d.name)
            shutil.rmtree(d)
            commit_project(repo, d.name, f'{d.name}: removed (gone from source)', owner)
        else:
            log.info('[MISSING] %s -- gone from source; kept in archive', d.name)
    return missing


def run(cfg: dict, dry_run: bool) -> int:
    started = datetime.now()
    run_date = date.today().isoformat()

    if not cfg['source_dir'].is_dir():
        log.error('source_dir does not exist or is unreachable: %s', cfg['source_dir'])
        return 2

    data_dir = cfg['data_dir']
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = data_dir / 'reports'

    # One run at a time; a crashed run's lock goes stale after LOCK_STALE_S.
    lock = data_dir / 'sync.lock'
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_STALE_S:
        log.error('another sync appears to be running (lock: %s) -- aborting', lock)
        return 3
    lock.write_text(str(started), encoding='utf-8')

    conn = None
    try:
        ensure_repo(cfg['archive_repo'], cfg)
        conn = open_db(data_dir / 'metrics.sqlite')

        excluded = []
        zips = find_projects(cfg['source_dir'], cfg['recursive'], cfg['exclude'], excluded)
        log.info('found %d project file(s) under %s (%d excluded by %d pattern(s))',
                 len(zips), cfg['source_dir'], len(excluded), len(cfg['exclude']))
        # On a dry run, spell out exactly what was skipped and by which pattern
        # so the exclude list can be audited before it governs a real sync.
        if dry_run and excluded:
            for rel, pat in sorted(excluded):
                log.info('  excluded: %s  [matched "%s"]', rel, pat)

        counts = {'changed': 0, 'new': 0, 'unchanged': 0}
        errors = []
        seen = set()

        staging_root = Path(tempfile.mkdtemp(prefix='projx_sync_'))
        try:
            for zip_path in zips:
                name = zip_path.stem
                # The archive keys one folder per project name, so two source
                # files with the same stem cannot both be archived. Skip the
                # duplicate with a recorded error instead of letting mkdir
                # crash the whole run; the fix is an exclude pattern.
                if name in seen:
                    log.error('[DUPLICATE] %s -- name "%s" already taken this run; '
                              'skipped (add an exclude to resolve)', zip_path, name)
                    errors.append(f'{zip_path.name}: duplicate project name "{name}" -- skipped')
                    continue
                seen.add(name)
                try:
                    proj_staging = staging_root / name
                    proj_staging.mkdir()
                    outcome = sync_one(zip_path, cfg['archive_repo'], proj_staging,
                                       cfg, run_date, conn, reports_dir, dry_run)
                    counts[outcome] += 1
                except Exception as e:
                    log.exception('[ERROR] %s: %s', zip_path.name, e)
                    errors.append(f'{zip_path.name}: {e}')
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

        handle_missing(seen, cfg['archive_repo'], cfg, run_date, conn, dry_run)

        if not dry_run:
            conn.execute(
                'INSERT INTO runs (run_date, started_at, finished_at, projects_seen,'
                ' projects_changed, errors) VALUES (?,?,?,?,?,?)',
                (run_date, started.isoformat(timespec='seconds'),
                 datetime.now().isoformat(timespec='seconds'),
                 len(zips), counts['changed'], '; '.join(errors)))

            if cfg['dashboard']:
                try:
                    import dashboard
                    out = data_dir / 'dashboard.html'
                    out.write_text(dashboard.generate_dashboard(data_dir / 'metrics.sqlite'),
                                   encoding='utf-8')
                    log.info('dashboard regenerated: %s', out)
                except Exception:
                    log.exception('dashboard generation failed (sync itself succeeded)')

            if cfg['push']:
                proc = git(cfg['archive_repo'], 'push', '-u', 'origin', 'HEAD',
                           check=False)
                if proc.returncode != 0:
                    log.warning('git push failed (sync itself succeeded): %s',
                                proc.stderr.strip())

        log.info('done: %d changed, %d new, %d unchanged, %d error(s)%s',
                 counts['changed'], counts['new'], counts['unchanged'], len(errors),
                 ' [dry run]' if dry_run else '')
        return 1 if errors else 0
    finally:
        if conn is not None:
            conn.close()
        lock.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('config', type=Path, help='Path to config JSON')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report changes without committing or recording anything')
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    tool_repo = Path(cfg['tool_repo'])
    if str(tool_repo) not in sys.path:
        sys.path.insert(0, str(tool_repo))

    cfg['data_dir'].mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(cfg['data_dir'] / 'sync.log', encoding='utf-8')])

    return run(cfg, args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
