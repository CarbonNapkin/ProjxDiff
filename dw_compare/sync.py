"""Nightly DriveWorks project archive + change tracking (the sync engine).

Copies every .driveprojx from a source directory (typically a UNC share) to a
local staging area, extracts each into a git archive repo (one top-level folder
per project), and — for projects whose content changed since the last run —
builds a semantic diff, writes per-project HTML + JSON reports, records
per-category and per-element change rows in a SQLite metrics database, and
commits the new state (one commit per changed project, authored per the
census/config attribution rules — see census.py).

New projects and unseen user display names are auto-registered in the census
as pending/unmapped, surfaced on the dashboard's needs-attention panel, and
resolved in the GUI (Tools > Manage Nightly Sync) or via `--census`. Mapping
a user retroactively heals prior metrics rows recorded under the raw name.

Runs headless (Task Scheduler-friendly): stdlib-only, ASCII-only console
output, lock file against overlapping runs, meaningful exit codes (0 ok,
1 per-project errors, 2 source unreachable, 3 already running).

Invoke via the app: `python -m dw_compare --sync config.json [--dry-run]`
(or `ProjxDiff.exe --sync config.json` from a packaged build), or via the
back-compat wrapper scripts/nightly_sync/nightly_sync.py.
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

from .parsers import load_project
from .report import generate_html_report
from .jsondiff import build_diff
from . import census as census_mod
from .census import read_last_saver  # noqa: F401  (re-exported for back-compat)

log = logging.getLogger('nightly_sync')

COPY_ATTEMPTS = 3
COPY_RETRY_DELAY_S = 10
# How long the *run* lock (data_dir/sync.lock, one sync at a time) may sit
# untouched before a crashed run is assumed. Nothing to do with the per-project
# Administrator lock below, which is DriveWorks' and governed by
# `lock_stale_hours`.
RUN_LOCK_STALE_S = 6 * 3600

# DriveWorks Administrator drops a "<stem>.~driveproj" sidecar beside a project
# for as long as it has it open. Confirmed on a real 22.2 session: the sidecar
# appears with the project and is renamed alongside it.
PROJECT_LOCK_SUFFIX = '.~driveproj'


# ---------------------------------------------------------------- config ----

REQUIRED_KEYS = ('source_dir', 'archive_repo', 'data_dir')

# Source names become path segments (archive/report/census namespaces), so
# they are restricted to filesystem- and URL-safe characters.
_SOURCE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')

DEFAULTS = {
    'sources': {},
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
    'census_path': '',
    # Rebuild guard (see sync_one): a project whose element overlap with its
    # archived copy is at or below this is not the same project any more, it
    # is a fresh build wearing the old name. Only applied once the archived
    # copy has at least rebuild_min_elements, so small or near-empty projects
    # -- where a couple of shared defaults swing the ratio -- never trip it.
    'rebuild_similarity': 0.05,
    'rebuild_min_elements': 25,
    # A project open in Administrator is deferred (see _triage), but a session
    # that exits uncleanly leaves its lock behind and would defer that project
    # forever. Past this age the lock is treated as abandoned and the project
    # syncs anyway -- safe, because the sync only ever reads the file. 0
    # disables the ageing and defers for as long as any lock is present.
    'lock_stale_hours': 6,
    # Accepted for back-compat with pre-1.2.0 configs; the engine now lives
    # inside the dw_compare package so no path bootstrap is needed.
    'tool_repo': '',
}


def slug_source_name(name: str) -> str:
    """Turn a human group name into a valid source name: whitespace becomes
    hyphens, anything outside the safe charset is dropped."""
    slug = re.sub(r'\s+', '-', name.strip())
    slug = re.sub(r'[^A-Za-z0-9_-]', '', slug)
    if not slug:
        raise SystemExit(f'group name {name!r} has no usable characters '
                         '(letters, digits, underscore, hyphen)')
    return slug


def init_site(root: Path) -> Path:
    """Create the standard site layout under `root` and write a starter site
    config at <root>/config.json — no environment groups yet; those come from
    add_source (CLI: --census ... --add-source, GUI: Manage Nightly Sync).
    Refuses to overwrite an existing config."""
    root = Path(root)
    config_path = root / 'config.json'
    if config_path.exists():
        raise SystemExit(f'config already exists: {config_path}')
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        'sources': {},
        'data_dir': (root / 'data').as_posix(),
        'derive_author_from_file': True,
    }
    config_path.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
    return config_path


def add_source(config_path: Path, name: str, source_dir) -> str:
    """Append an environment group to a site config. The human name is
    slugged; the group's archive repo is auto-placed at
    <config dir>/repos/<slug> so callers never handle that concept. The
    write is atomic and re-validated through load_config. Returns the slug."""
    config_path = Path(config_path)
    try:
        raw = json.loads(config_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise SystemExit(f'config not found: {config_path}\n'
                         'Create one first with: python -m dw_compare '
                         f'--init-config {config_path.parent}')
    if 'sources' not in raw:
        raise SystemExit('this is a legacy single-source config; environment '
                         'groups can only be added to a site config '
                         '(see config.example.site.json)')
    slug = slug_source_name(name)
    if slug in raw['sources']:
        raise SystemExit(f'group "{slug}" already exists in {config_path}')
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise SystemExit(f'source folder does not exist: {source_dir}')
    raw['sources'][slug] = {
        'source_dir': source_dir.as_posix(),
        'archive_repo': (config_path.parent / 'repos' / slug).as_posix(),
    }
    tmp = config_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(raw, indent=2) + '\n', encoding='utf-8')
    tmp.replace(config_path)
    load_config(config_path)  # raises SystemExit if the result is invalid
    return slug


def _validate_tuning(cfg: dict) -> None:
    """Coerce and range-check the numeric knobs.

    These are hand-editable and GUI-written, and every one of them is used in
    arithmetic deep inside a run — `"6" * 3600` is a 3600-copy string, not a
    number, and the TypeError would surface an hour into the night rather than
    at load. Fail here instead, where the message can name the key."""
    for key, lo, hi in (('rebuild_similarity', 0.0, 1.0),
                        ('rebuild_min_elements', 0, None),
                        ('lock_stale_hours', 0, None)):
        raw = cfg[key]
        if isinstance(raw, bool):     # bool is an int; nobody means True here
            raise SystemExit(f'config error: {key} must be a number, got {raw!r}')
        try:
            val = float(raw) if key == 'rebuild_similarity' else int(raw)
        except (TypeError, ValueError):
            raise SystemExit(f'config error: {key} must be a number, got {raw!r}')
        if val < lo or (hi is not None and val > hi):
            span = f'{lo}-{hi}' if hi is not None else f'{lo} or more'
            raise SystemExit(f'config error: {key} must be {span}, got {raw!r}')
        cfg[key] = val


def load_config(path: Path) -> dict:
    """Load and validate a sync config. Two shapes are accepted:

    - Legacy single-source: top-level source_dir + archive_repo + data_dir.
    - Site config: a `sources` map of named environments, each with its own
      source_dir + archive_repo (and optional exclude/recursive overrides),
      sharing data_dir and every other setting. Each source syncs into its
      own archive repo and census namespace ("<name>/<project>"), so the same
      project name in prod and staging never collides.

    Either way, cfg['sources_resolved'] holds {name: {source_dir,
    archive_repo, exclude, recursive}} — the legacy shape uses the single
    name '' so census keys, report paths, and metrics rows stay exactly as
    they were before site configs existed."""
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(path.read_text(encoding='utf-8')))
    except FileNotFoundError:
        raise SystemExit(
            f'config not found: {path}\n'
            'Create one with: python -m dw_compare --init-config <folder>\n'
            '(or in the app: Tools > Manage Nightly Sync)')

    _validate_tuning(cfg)

    if cfg['sources']:
        if not cfg.get('data_dir'):
            raise SystemExit('config error: missing required key(s): data_dir')
        resolved = {}
        for name, src in cfg['sources'].items():
            if not _SOURCE_NAME_RE.match(name):
                raise SystemExit(f'config error: source name {name!r} must be '
                                 'letters/digits/underscore/hyphen only')
            missing = [k for k in ('source_dir', 'archive_repo') if not src.get(k)]
            if missing:
                raise SystemExit(f'config error: source "{name}" missing '
                                 f'{", ".join(missing)}')
            resolved[name] = {
                'source_dir': Path(src['source_dir']),
                'archive_repo': Path(src['archive_repo']),
                'exclude': src.get('exclude', cfg['exclude']),
                'recursive': src.get('recursive', cfg['recursive']),
                # Optional group database for name resolution in nightly
                # reports; per-source (prod and staging usually have
                # different group DBs), falling back to top-level keys.
                'db_server': str(src.get('db_server', cfg.get('db_server', '')) or ''),
                'db_database': str(src.get('db_database', cfg.get('db_database', '')) or ''),
            }
        cfg['data_dir'] = Path(cfg['data_dir'])
        cfg['sources_resolved'] = resolved
        return cfg

    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise SystemExit(f'config error: missing required key(s): {", ".join(missing)}')
    for k in ('source_dir', 'archive_repo', 'data_dir'):
        cfg[k] = Path(cfg[k])
    cfg['sources_resolved'] = {'': {
        'source_dir': cfg['source_dir'],
        'archive_repo': cfg['archive_repo'],
        'exclude': cfg['exclude'],
        'recursive': cfg['recursive'],
        'db_server': str(cfg.get('db_server') or ''),
        'db_database': str(cfg.get('db_database') or ''),
    }}
    return cfg


def census_key(source_name: str, project: str) -> str:
    """Census key for a project: plain name for legacy configs, namespaced
    "<source>/<project>" for site configs — the same project name in two
    sources is two distinct census entries."""
    return f'{source_name}/{project}' if source_name else project


# ------------------------------------------------------------- utilities ----

def find_projects(source_dir: Path, recursive: bool,
                  exclude: list = (),
                  excluded_out: list = None) -> list:
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


def project_lock(zip_path: Path):
    """(holder, age_seconds) while Administrator holds this project open,
    else None.

    `holder` is the "user|machine" DriveWorks writes into the sidecar (13-28
    bytes on a real share; it carries no project data, so there is nothing to
    recover from it and nothing lost by ignoring it). Its value to us is
    diagnostic only: an abandoned lock names whoever left it behind.

    We never delete these. The sidecar is what stops a second person opening
    the project, and on a shared site it usually belongs to another user on
    another machine -- removing it risks two concurrent editors and a lost
    afternoon, which is a far worse outcome than a deferred sync."""
    lock = zip_path.with_suffix(PROJECT_LOCK_SUFFIX)
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return None
    try:
        holder = lock.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        holder = ''
    return holder, age


def _lock_is_stale(age: float, lock_stale_s: float) -> bool:
    """Whether a lock this old should be treated as abandoned. `lock_stale_s`
    of 0 disables the ageing: defer for as long as any lock is present."""
    return 0 < lock_stale_s <= age


# Every dict on DWProject that holds diffable, name-keyed elements. Component
# placements are deliberately absent: they are keyed by GUID and churn on
# re-save, which would drag the overlap ratio down on an unchanged project.
_ELEMENT_COLLECTIONS = (
    'variables', 'constants', 'calc_tables', 'component_tasks', 'documents',
    'lookup_tables', 'spec_macros', 'nav_steps', 'data_tables', 'forms',
)


def _element_keys(proj) -> set:
    """The identity of every element in a project, namespaced by collection.

    Comparing two of these answers "is this the same project?" independently
    of the filename, which is the one thing a .driveprojx does not record --
    DWProjectName is only stamped with the filename at each save, so it is
    stale after a rename and useless after the next save."""
    keys = set()
    for attr in _ELEMENT_COLLECTIONS:
        for k in (getattr(proj, attr, None) or {}):
            keys.add(f'{attr}:{k}')
    return keys


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
    unchanged INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS element_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    project TEXT NOT NULL,
    owner TEXT,
    category TEXT NOT NULL,
    element TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT ''
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
    # Migrate pre-1.3.0 databases in place: the source column arrived with
    # site configs. Existing rows keep '' — the legacy source name.
    for table in ('category_changes', 'element_changes'):
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        if 'source' not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    return conn


def record_diff(conn: sqlite3.Connection, run_date: str, project: str,
                owner: str, diff: dict, source: str = '') -> None:
    for cat, stats in diff['summary']['categories'].items():
        if stats['added'] or stats['removed'] or stats['modified']:
            conn.execute(
                'INSERT INTO category_changes (run_date, project, owner, category,'
                ' added, removed, modified, unchanged, source) VALUES (?,?,?,?,?,?,?,?,?)',
                (run_date, project, owner, cat, stats['added'], stats['removed'],
                 stats['modified'], stats['unchanged'], source))
    for rec in diff['changes']:
        conn.execute(
            'INSERT INTO element_changes (run_date, project, owner, category,'
            ' element, status, source) VALUES (?,?,?,?,?,?,?)',
            (run_date, project, owner, rec['category'], rec['name'], rec['status'],
             source))


def record_project_event(conn: sqlite3.Connection, run_date: str, project: str,
                         owner: str, status: str, source: str = '') -> None:
    """A whole project appeared in / vanished from the source share."""
    conn.execute(
        'INSERT INTO element_changes (run_date, project, owner, category,'
        ' element, status, source) VALUES (?,?,?,?,?,?,?)',
        (run_date, project, owner, 'project', project, status, source))


# ---------------------------------------------------------- attribution ----

def resolve_author(name: str, project_root: Path, cfg: dict) -> str:
    """Back-compat shim (pre-census API): git author for a project's change
    using config-only rules. New code goes through census.resolve_owner."""
    author, _owner, _unmapped = census_mod.resolve_owner(
        name, project_root, cfg, {'users': {}})
    return author


# ------------------------------------------------------------------ sync ----

def open_group_db(scfg: dict):
    """Open the source's optional DriveWorks group database for name
    resolution in nightly reports (config keys db_server/db_database).

    WINDOWS INTEGRATED AUTH ONLY, by design: the app never stores
    passwords, and an unattended nightly task has nobody to type one — so
    the account the scheduled task runs as must itself be granted
    read-only access to the group database. Without integrated auth there
    is no way to run the nightly compares AND connect to the database;
    the sync still runs fully, its reports just show raw ids.

    Returns a connected DwDatabase or None; failure is logged, never
    fatal — a DB outage at 2 a.m. must not stop the archive run."""
    server, database = scfg.get('db_server', ''), scfg.get('db_database', '')
    if not server or not database:
        return None
    from . import dbsource
    db = dbsource.DwDatabase(label='sync', server=server, database=database,
                             trusted=True)
    if not db.connect():
        log.warning('group db unavailable (%s / %s): %s -- reports will '
                    'show raw ids this run', server, database, db.last_error)
        return None
    log.info('group db connected: %s / %s (integrated auth)', server, database)
    return db


def _resolve_names(db, proj):
    """(resolved, prop_names, prop_types) for one project via an open
    group-db connection; (None, None, None) without one."""
    if db is None:
        return None, None, None
    from . import idmap, components
    resolver = idmap.IdResolver(db=db)
    resolved = components.resolve_names(proj.component_index, resolver)
    ccrefs = set(proj.component_index.trid_to_ccref.values())
    if ccrefs:
        props, types = db.fetch_captured_property_names_and_types(ccrefs)
    else:
        props, types = {}, {}
    return resolved, props, types


def _write_reports(name: str, source: str, run_date: str, reports_dir: Path,
                   diff: dict, old_proj, new_proj, group_db) -> None:
    """Write the dated HTML + JSON reports for one project's diff.

    Site configs namespace the dated report folders by source so the same
    project name in two sources cannot clobber the other's report."""
    day_dir = (reports_dir / source / run_date) if source else (reports_dir / run_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f'{name}.json').write_text(
        json.dumps(diff, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    # Group-db name resolution feeds the HTML report only; the JSON diff
    # keeps raw ids (its schema is versioned and consumed by pipelines).
    old_resolved, old_props, old_types = _resolve_names(group_db, old_proj)
    new_resolved, new_props, new_types = _resolve_names(group_db, new_proj)
    (day_dir / f'{name}.html').write_text(
        generate_html_report(old_proj, new_proj, f'{name} (previous)', f'{name} (current)',
                             old_resolved, new_resolved, old_props, new_props,
                             old_types, new_types),
        encoding='utf-8')


def sync_one(zip_path: Path, repo: Path, staging: Path, cfg: dict,
             run_date: str, conn, reports_dir: Path, dry_run: bool,
             census: dict, source: str = '', group_db=None):
    """Sync a single project. Returns 'changed', 'new', 'rebuilt',
    'unchanged', or 'deferred'."""
    name = zip_path.stem
    key = census_key(source, name)

    local_zip = staging / zip_path.name
    copy_with_retries(zip_path, local_zip)

    # _triage checked for an Administrator lock before this project reached
    # the queue, but a user can open a project in the minutes between. Re-check
    # now that the copy is taken: a lock that appeared during it means the copy
    # may be a half-written save, and the next run will pick the project up.
    held = project_lock(zip_path)
    if held is not None and not _lock_is_stale(held[1], cfg['lock_stale_hours'] * 3600):
        log.warning('[OPEN] %s -- opened in DriveWorks Administrator while the '
                    'sync was copying it; deferred to the next run', key)
        return 'deferred'

    new_dir = staging / name
    safe_extract(local_zip, new_dir)

    # Attribution comes from the freshly-extracted copy (its last-saver), so a
    # changed project is credited to whoever most recently saved it. An
    # unmapped display name is registered in the census (shows up on the
    # dashboard) and recorded raw in the metrics so mapping it later heals.
    author, owner, unmapped = census_mod.resolve_owner(key, new_dir, cfg, census)
    if unmapped and not dry_run:
        census_mod.ensure_user(census, unmapped)

    repo_dir = repo / name
    is_new = not repo_dir.is_dir()

    if not is_new and dir_digest(repo_dir) == dir_digest(new_dir):
        return 'unchanged'

    if is_new:
        # First appearance: archive it, but record only a project-level event.
        # Diffing against nothing would count every element as "added" and
        # poison the work metrics with a one-time explosion.
        log.info('[NEW] %s -- adding to archive', key)
        if not dry_run:
            shutil.copytree(new_dir, repo_dir)
            commit_project(repo, name, f'{name}: added to archive', author)
            record_project_event(conn, run_date, name, owner, 'added', source)
        return 'new'

    old_proj = load_project(repo_dir)
    new_proj = load_project(new_dir)

    # A project deleted and rebuilt under the same name keeps its archive
    # directory, so is_new stays False and the diff below would compare the
    # rebuild against a stranger -- reporting every element of the old project
    # as removed and every element of the new one as added, which is exactly
    # the metrics explosion the is_new branch above exists to prevent. If
    # essentially none of the archived project survives, re-baseline instead.
    #
    # The test is containment, not similarity: what fraction of the ARCHIVED
    # project is still there. A Jaccard ratio would answer "how alike are these
    # two", which is a different question and gets the answer wrong in the one
    # direction that matters -- a project that keeps every element it had and
    # grows sixtyfold shares a vanishing fraction of the union, and would be
    # torn down and re-baselined despite being unmistakably itself.
    old_keys, new_keys = _element_keys(old_proj), _element_keys(new_proj)
    survived = len(old_keys & new_keys) / len(old_keys) if old_keys else 1.0
    if (len(old_keys) >= cfg['rebuild_min_elements']
            and survived <= cfg['rebuild_similarity']):
        if len(new_keys) < cfg['rebuild_min_elements']:
            # Nothing of the old project left AND barely anything in its place.
            # A truncated copy, a half-written save, or a parser that no longer
            # understands the file explains this at least as well as a rebuild
            # does -- and re-baselining would replace a good archive with a bad
            # copy, silently, for every project on the share. Refuse: the run
            # exits non-zero, the archive is untouched, a human looks.
            raise RuntimeError(
                f'archived copy holds {len(old_keys)} elements, the file on the '
                f'share parses to {len(new_keys)} and shares none of them -- '
                'refusing to re-baseline over a good archive; check the source '
                'file (and this tool\'s parsers against the DriveWorks version)')
        log.warning('[REBUILT] %s -- only %.1f%% of the archived copy survives '
                    'in the file on the share (%d -> %d elements); re-baselining '
                    'instead of diffing against a stranger',
                    key, survived * 100, len(old_keys), len(new_keys))
        if dry_run:
            return 'rebuilt'
        # The reports still get written: they are the only record of what the
        # rebuild replaced. Only record_diff is skipped, and that is the one
        # thing that would poison the work metrics.
        rebuild_diff = build_diff(old_proj, new_proj,
                                  f'{name} (previous)', f'{name} (rebuilt)')
        _write_reports(name, source, run_date, reports_dir, rebuild_diff,
                       old_proj, new_proj, group_db)
        shutil.rmtree(repo_dir)
        shutil.copytree(new_dir, repo_dir)
        commit_project(repo, name, f'{name}: rebuilt from scratch, re-baselined '
                                   f'(nightly sync {run_date})', author)
        record_project_event(conn, run_date, name, owner, 'rebuilt', source)
        return 'rebuilt'

    diff = build_diff(old_proj, new_proj, f'{name} (previous)', f'{name} (current)')
    s = diff['summary']
    log.info('[CHANGED] %s: +%d -%d ~%d', key, s['added'], s['removed'], s['modified'])

    if dry_run:
        return 'changed'

    _write_reports(name, source, run_date, reports_dir, diff,
                   old_proj, new_proj, group_db)

    record_diff(conn, run_date, name, owner, diff, source)

    shutil.rmtree(repo_dir)
    shutil.copytree(new_dir, repo_dir)
    commit_project(repo, name,
                   f'{name}: +{s["added"]} -{s["removed"]} ~{s["modified"]} '
                   f'(nightly sync {run_date})', author)
    return 'changed'


def _already_marked_removed(conn, project: str, source: str = '') -> bool:
    row = conn.execute(
        "SELECT status FROM element_changes WHERE project=? AND source=? "
        "AND category='project' ORDER BY id DESC LIMIT 1",
        (project, source)).fetchone()
    return row is not None and row[0] == 'removed'


def handle_missing(seen: set, repo: Path, cfg: dict, run_date: str,
                   conn, dry_run: bool, census: dict = None,
                   source: str = '') -> list:
    """Projects present in the archive but no longer on the share. The
    "removed" event is recorded once per disappearance, not re-recorded every
    night the project stays gone. Projects the census marks "ignore" are
    intentionally unsynced, not missing."""
    census = census or {'users': {}, 'projects': {}}
    missing = []
    for d in sorted(repo.iterdir()):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        if d.name in seen:
            continue
        key = census_key(source, d.name)
        if census['projects'].get(key, {}).get('disposition') == 'ignore':
            continue
        missing.append(d.name)
        owner = cfg['owners'].get(key) or cfg['owners'].get(d.name, '')
        if dry_run:
            log.info('[MISSING] %s -- gone from source (dry run, no action)', key)
            continue
        if not _already_marked_removed(conn, d.name, source):
            record_project_event(conn, run_date, d.name, owner, 'removed', source)
        if cfg['remove_missing']:
            log.info('[MISSING] %s -- removing from archive (remove_missing=true)', key)
            shutil.rmtree(d)
            commit_project(repo, d.name, f'{d.name}: removed (gone from source)', owner)
        else:
            log.info('[MISSING] %s -- gone from source; kept in archive', key)
    return missing


def _deferral(key: str, zip_path: Path, source_dir: Path, holder: str,
              age: float) -> dict:
    """One deferred project, in the shape the attention panel renders.
    `project` is the census key so it reads the same as a conflict does;
    `name` is the bare project name, which is what `seen` is keyed by; `holder`
    is whatever the lock file named, which is usually who to go and ask."""
    try:
        rel = zip_path.relative_to(source_dir).as_posix()
    except ValueError:
        rel = zip_path.name
    return {'project': key, 'name': zip_path.stem, 'path': rel,
            'holder': holder, 'hours': round(age / 3600, 1)}


def _triage(zips: list, source_dir: Path, census: dict, errors: list,
            source: str = '', lock_stale_s: float = 0) -> tuple:
    """Apply census dispositions to the discovered files.

    Returns (to_sync, ignored_count, conflicts, deferred). Grouping by project
    name: a lone file whose path moved updates the census entry; multiple files
    sharing a name sync only the census-registered path (or the first, if
    none registered) — the rest are recorded as conflicts for the attention
    panel and as run errors (back-compat with the pre-census duplicate
    handling). Projects open in Administrator go to `deferred` (as the dicts
    _deferral builds, for the attention panel): still present on the share,
    just not worth archiving mid-edit. A lock older than `lock_stale_s` is
    treated as abandoned and its project synced anyway, so a session that died
    without cleaning up cannot defer a project forever."""
    by_name = {}
    for z in zips:
        by_name.setdefault(z.stem, []).append(z)

    to_sync = []
    ignored = 0
    conflicts = []
    deferred = []
    for name in sorted(by_name):
        key = census_key(source, name)
        group = sorted(by_name[name])
        rels = [z.relative_to(source_dir).as_posix() for z in group]
        entry = census_mod.ensure_project(census, key, rels[0])

        if len(group) == 1:
            if entry.get('path') != rels[0]:
                log.info('[MOVED] %s: %s -> %s', key, entry.get('path'), rels[0])
                entry['path'] = rels[0]
            chosen = group[0]
        else:
            registered = entry.get('path')
            idx = rels.index(registered) if registered in rels else 0
            chosen = group[idx]
            for z, rel in zip(group, rels):
                if z is chosen:
                    continue
                log.error('[DUPLICATE] %s -- name "%s" already taken by %s; '
                          'skipped (resolve in Manage Nightly Sync or add an exclude)',
                          rel, key, rels[idx])
                errors.append(f'{z.name}: duplicate project name "{name}" -- skipped')
                conflicts.append({'project': key, 'path': rel, 'registered': rels[idx]})

        if entry.get('disposition') == 'ignore':
            ignored += 1
            continue
        held = project_lock(chosen)
        if held is not None:
            holder, age = held
            who = f'{holder}, ' if holder else ''
            if _lock_is_stale(age, lock_stale_s):
                log.warning('[STALE LOCK] %s -- lock (%s%.1fh old) looks '
                            'abandoned; syncing anyway and leaving the lock '
                            'file alone for DriveWorks to clear',
                            key, who, age / 3600)
            else:
                log.warning('[OPEN] %s -- open in DriveWorks Administrator '
                            '(%s%.1fh); deferred to the next run',
                            key, who, age / 3600)
                deferred.append(_deferral(key, chosen, source_dir, holder, age))
                continue
        to_sync.append(chosen)

    return to_sync, ignored, conflicts, deferred


def _run_source(sname: str, scfg: dict, cfg: dict, census: dict, conn,
                run_date: str, reports_dir: Path, dry_run: bool,
                counts: dict, errors: list) -> tuple:
    """Sync one named source. Returns (files_found, conflicts, deferred)."""
    label = sname or 'source'
    excluded = []
    zips = find_projects(scfg['source_dir'], scfg['recursive'],
                         scfg['exclude'], excluded)
    log.info('[%s] found %d project file(s) under %s (%d excluded by %d pattern(s))',
             label, len(zips), scfg['source_dir'], len(excluded), len(scfg['exclude']))
    # On a dry run, spell out exactly what was skipped and by which pattern
    # so the exclude list can be audited before it governs a real sync.
    if dry_run and excluded:
        for rel, pat in sorted(excluded):
            log.info('  excluded: %s  [matched "%s"]', rel, pat)

    to_sync, ignored, conflicts, deferred = _triage(
        zips, scfg['source_dir'], census, errors, sname,
        lock_stale_s=cfg['lock_stale_hours'] * 3600)
    if ignored:
        log.info('[%s] %d project(s) skipped as census-ignored', label, ignored)
    counts['deferred'] += len(deferred)
    # Deferred projects are still on the share, so they belong in `seen`:
    # without this a project that happened to be open at sync time would be
    # recorded as removed and, once closed, re-added as new.
    seen = {z.stem for z in to_sync} | {d['name'] for d in deferred}

    staging_root = Path(tempfile.mkdtemp(prefix='projx_sync_'))
    group_db = None if dry_run else open_group_db(scfg)
    try:
        for zip_path in to_sync:
            try:
                proj_staging = staging_root / zip_path.stem
                proj_staging.mkdir()
                outcome = sync_one(zip_path, scfg['archive_repo'], proj_staging,
                                   cfg, run_date, conn, reports_dir, dry_run,
                                   census, sname, group_db=group_db)
                counts[outcome] += 1
                if outcome == 'deferred':
                    # Opened while the sync was copying it (see sync_one). It
                    # is already in `seen`, it just needs to reach the panel.
                    held = project_lock(zip_path) or ('', 0.0)
                    deferred.append(_deferral(census_key(sname, zip_path.stem),
                                              zip_path, scfg['source_dir'], *held))
            except Exception as e:
                log.exception('[ERROR] %s: %s', zip_path.name, e)
                errors.append(f'{zip_path.name}: {e}')
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if group_db is not None:
            group_db.close()

    if deferred:
        log.info('[%s] %d project(s) deferred as open in Administrator',
                 label, len(deferred))

    handle_missing(seen, scfg['archive_repo'], cfg, run_date, conn, dry_run,
                   census, sname)
    return len(zips), conflicts, deferred


def run(cfg: dict, dry_run: bool) -> int:
    started = datetime.now()
    run_date = date.today().isoformat()

    sources = cfg['sources_resolved']
    legacy = set(sources) == {''}

    # A legacy config's single unreachable source keeps its dedicated exit
    # code. A site config syncs whatever is reachable and flags the rest.
    if legacy and not sources['']['source_dir'].is_dir():
        log.error('source_dir does not exist or is unreachable: %s',
                  sources['']['source_dir'])
        return 2

    data_dir = cfg['data_dir']
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = data_dir / 'reports'

    # One run at a time; a crashed run's lock goes stale after RUN_LOCK_STALE_S.
    lock = data_dir / 'sync.lock'
    if lock.exists() and time.time() - lock.stat().st_mtime < RUN_LOCK_STALE_S:
        log.error('another sync appears to be running (lock: %s) -- aborting', lock)
        return 3
    lock.write_text(str(started), encoding='utf-8')

    cpath = census_mod.census_path(cfg)
    census = census_mod.load_census(cpath)
    census_mod.seed_from_config(census, cfg)
    census_before = census_mod.snapshot(census)

    conn = None
    try:
        conn = open_db(data_dir / 'metrics.sqlite')

        if not dry_run:
            healed = census_mod.heal_metrics(conn, census)
            if healed:
                log.info('healed %d metrics row(s) with newly mapped identities', healed)

        counts = {'changed': 0, 'new': 0, 'unchanged': 0,
                  'rebuilt': 0, 'deferred': 0}
        errors = []
        all_conflicts = []
        all_deferred = []
        total_found = 0

        for sname, scfg in sources.items():
            if not scfg['source_dir'].is_dir():
                log.error('[%s] source_dir unreachable: %s -- skipping this source',
                          sname or 'source', scfg['source_dir'])
                errors.append(f'{sname or "source"}: source_dir unreachable '
                              f'({scfg["source_dir"]})')
                continue
            ensure_repo(scfg['archive_repo'], cfg)
            found, conflicts, deferred = _run_source(sname, scfg, cfg, census,
                                                     conn, run_date, reports_dir,
                                                     dry_run, counts, errors)
            total_found += found
            all_conflicts.extend(conflicts)
            all_deferred.extend(deferred)

        census['conflicts'] = all_conflicts
        # Deferrals are this run's state, not a standing decision: each run
        # replaces the list wholesale, so a project that closed overnight
        # drops off the panel by itself.
        census['deferred'] = all_deferred

        pending = census_mod.pending_projects(census)
        unmapped = census_mod.unmapped_users(census)
        if pending or unmapped or census['conflicts'] or census['deferred']:
            log.info('needs attention: %d pending project(s), %d unmapped user(s), '
                     '%d name conflict(s), %d deferred as open -- resolve in '
                     'Projx Diff > Tools > Manage Nightly Sync',
                     len(pending), len(unmapped), len(census['conflicts']),
                     len(census['deferred']))

        if not dry_run:
            if census_mod.snapshot(census) != census_before:
                census_mod.save_census(cpath, census)
                log.info('census updated: %s', cpath)

            conn.execute(
                'INSERT INTO runs (run_date, started_at, finished_at, projects_seen,'
                ' projects_changed, errors) VALUES (?,?,?,?,?,?)',
                (run_date, started.isoformat(timespec='seconds'),
                 datetime.now().isoformat(timespec='seconds'),
                 total_found, counts['changed'], '; '.join(errors)))

            if cfg['dashboard']:
                try:
                    from . import dashboard
                    out = data_dir / 'dashboard.html'
                    out.write_text(
                        dashboard.generate_dashboard(
                            data_dir / 'metrics.sqlite', census_path=cpath,
                            sources=None if legacy else list(sources)),
                        encoding='utf-8')
                    log.info('dashboard regenerated: %s', out)
                except Exception:
                    log.exception('dashboard generation failed (sync itself succeeded)')

            if cfg['push']:
                for sname, scfg in sources.items():
                    if not (scfg['archive_repo'] / '.git').is_dir():
                        continue
                    proc = git(scfg['archive_repo'], 'push', '-u', 'origin', 'HEAD',
                               check=False)
                    if proc.returncode != 0:
                        log.warning('[%s] git push failed (sync itself succeeded): %s',
                                    sname or 'source', proc.stderr.strip())

        log.info('done: %d changed, %d new, %d rebuilt, %d unchanged, '
                 '%d deferred, %d error(s)%s',
                 counts['changed'], counts['new'], counts['rebuilt'],
                 counts['unchanged'], counts['deferred'], len(errors),
                 ' [dry run]' if dry_run else '')
        return 1 if errors else 0
    finally:
        if conn is not None:
            conn.close()
        lock.unlink(missing_ok=True)


def setup_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(data_dir / 'sync.log', encoding='utf-8')])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Nightly DriveWorks project archive + change tracking')
    parser.add_argument('config', type=Path, help='Path to config JSON')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report changes without committing or recording anything')
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg['data_dir'])
    return run(cfg, args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
