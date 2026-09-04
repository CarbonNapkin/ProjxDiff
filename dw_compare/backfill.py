"""Backfill rule-change metrics from the archive repos.

The metrics database (and therefore the dashboard) never counted driven-
property rules before the 'rules' category was added to jsondiff — the HTML
reports showed them, the dashboard silently didn't. The archived JSON reports
lack the category too, so they can't be replayed; but the archive git repos
hold every night's full project state, one commit per changed project, with
the run date in the commit message:

    <name>: +A -R ~M (nightly sync YYYY-MM-DD)

So the history CAN be reconstructed: for every such commit, extract the
project's tree at the commit and at its parent, re-parse both, diff just the
rules category, and insert the category_changes / element_changes rows the
original run would have written had it known how.

Only commits with that exact message shape are replayed. That skips, by
construction, exactly what the live sync skips: "added to archive" baselines
(diffing against nothing would poison the metrics), "rebuilt from scratch"
re-baselines (record_diff is deliberately not called for those), and
removals.

Idempotent: existing rules rows for a (run_date, project, source) are
replaced, so re-running the backfill — or running it after the fixed sync has
already recorded some nights — never double-counts.

Owner attribution prefers the owner already recorded on that night's other
rows (so the new rows agree with the old ones); a night with no rows at all
(only rules changed, so record_diff wrote nothing) falls back to the same
census/config resolution the sync uses, reading the last-saver from the
extracted historical tree. The census is read, never written.

Invoke: `python -m dw_compare --backfill-rules config.json [--dry-run]`
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import re
import subprocess
import tarfile
import tempfile
import shutil
from pathlib import Path

from .jsondiff import _diff_rules
from .parsers import load_project
from . import census as census_mod

log = logging.getLogger('backfill')

# The exact subject commit_project writes for a recorded diff. Project names
# may contain almost anything (they are file stems), so anchor on the tail.
_DIFF_SUBJECT = re.compile(
    r'^(?P<name>.+): \+\d+ -\d+ ~\d+ \(nightly sync (?P<date>\d{4}-\d{2}-\d{2})\)$')


def _git_bytes(repo: Path, *args: str) -> bytes:
    """Like sync.git() but with binary stdout — `git archive` emits a tar."""
    proc = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} failed: '
                           f'{proc.stderr.decode(errors="replace").strip()}')
    return proc.stdout


def _extract_tree(repo: Path, rev: str, name: str, dest: Path) -> Path:
    """Extract one project directory as it stood at rev into dest/<name>.
    :(literal) because project names are file stems and may contain glob
    characters a bare pathspec would expand."""
    tar_bytes = _git_bytes(repo, 'archive', rev, f':(literal){name}')
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        try:
            tf.extractall(dest, filter='data')
        except TypeError:      # Python < 3.12: no filter parameter
            tf.extractall(dest)
    return dest / name


def _load_quiet(folder: Path):
    """load_project prints a 'Found:' line per file — fine interactively,
    noise multiplied by every commit in a backfill."""
    with contextlib.redirect_stdout(io.StringIO()):
        return load_project(folder)


def _diff_commits(repo: Path) -> list:
    """Every recorded-diff commit in the repo, oldest first:
    (sha, project_name, run_date)."""
    out = _git_bytes(repo, 'log', '--reverse', '--format=%H%x09%s').decode(
        'utf-8', errors='replace')
    commits = []
    for line in out.splitlines():
        sha, _, subject = line.partition('\t')
        m = _DIFF_SUBJECT.match(subject)
        if m:
            commits.append((sha, m.group('name'), m.group('date')))
    return commits


def _existing_owner(conn, run_date: str, project: str, source: str):
    """The owner the original run recorded for this night, if any row of any
    category survives to say so. Returns None when the night has no rows."""
    for table in ('category_changes', 'element_changes'):
        row = conn.execute(
            f'SELECT owner FROM {table} WHERE run_date=? AND project=? AND source=? '
            'LIMIT 1', (run_date, project, source)).fetchone()
        if row is not None:
            return row[0]
    return None


def backfill_source(sname: str, repo: Path, cfg: dict, census: dict, conn,
                    dry_run: bool) -> dict:
    """Replay one archive repo. Returns counters for the summary line."""
    from .sync import census_key  # deferred: sync imports are heavyweight

    counts = {'commits': 0, 'inserted': 0, 'quiet': 0, 'errors': 0}
    label = sname or 'source'
    if not (repo / '.git').is_dir():
        log.error('[%s] archive repo not found or not a git repo: %s -- skipping',
                  label, repo)
        counts['errors'] += 1
        return counts

    commits = _diff_commits(repo)
    log.info('[%s] %d recorded-diff commit(s) in %s', label, len(commits), repo)

    # Replace-don't-append, but only once per night: a same-day re-run writes
    # two commits for one run_date, and both belong in that night's rows.
    cleared = set()

    for sha, name, run_date in commits:
        counts['commits'] += 1
        workdir = Path(tempfile.mkdtemp(prefix='projx_backfill_'))
        try:
            old_root = _extract_tree(repo, f'{sha}^', name, workdir / 'old')
            new_root = _extract_tree(repo, sha, name, workdir / 'new')
            old_proj = _load_quiet(old_root)
            new_proj = _load_quiet(new_root)
            records, stats = _diff_rules(old_proj.component_index,
                                         new_proj.component_index)

            key = (run_date, name, sname)
            if not dry_run and key not in cleared:
                cleared.add(key)
                for table in ('category_changes', 'element_changes'):
                    conn.execute(
                        f"DELETE FROM {table} WHERE category='rules' "
                        'AND run_date=? AND project=? AND source=?',
                        (run_date, name, sname))

            if not (stats['added'] or stats['removed'] or stats['modified']):
                counts['quiet'] += 1
                continue

            owner = _existing_owner(conn, run_date, name, sname)
            if owner is None:
                # Only rules changed that night, so record_diff wrote nothing
                # and there is no row to borrow from. Resolve exactly as the
                # sync would have, from the historical tree's last-saver.
                _author, owner, _unmapped = census_mod.resolve_owner(
                    census_key(sname, name), new_root, cfg, census)

            log.info('[%s] %s %s: +%d -%d ~%d rule change(s)%s',
                     label, run_date, name, stats['added'], stats['removed'],
                     stats['modified'], ' [dry run]' if dry_run else '')
            if dry_run:
                continue

            conn.execute(
                'INSERT INTO category_changes (run_date, project, owner, category,'
                ' added, removed, modified, unchanged, source) VALUES (?,?,?,?,?,?,?,?,?)',
                (run_date, name, owner, 'rules', stats['added'], stats['removed'],
                 stats['modified'], stats['unchanged'], sname))
            for rec in records:
                conn.execute(
                    'INSERT INTO element_changes (run_date, project, owner, category,'
                    ' element, status, source) VALUES (?,?,?,?,?,?,?)',
                    (run_date, name, owner, 'rules', rec['name'], rec['status'],
                     sname))
            counts['inserted'] += 1
        except Exception as e:
            log.exception('[%s] %s at %s: %s -- skipped', label, name, sha[:10], e)
            counts['errors'] += 1
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return counts


def run(cfg: dict, dry_run: bool) -> int:
    import time
    from .sync import open_db, RUN_LOCK_STALE_S
    from ._version import __version__

    data_dir = cfg['data_dir']
    log.info('Projx Diff %s rules backfill starting (%s)', __version__,
             'dry run' if dry_run else 'live')

    # Same one-writer lock as the sync, same semantics: a live backfill must
    # not interleave metrics writes with a running sync (or another backfill),
    # and a sync that starts mid-backfill aborts cleanly with exit 3 instead.
    # A dry run neither takes nor honours it — it writes nothing the lock
    # protects.
    lock = data_dir / 'sync.lock'
    if not dry_run:
        if lock.exists() and time.time() - lock.stat().st_mtime < RUN_LOCK_STALE_S:
            log.error('a sync or backfill appears to be running (lock: %s) '
                      '-- aborting', lock)
            return 3
        lock.write_text('backfill', encoding='utf-8')

    cpath = census_mod.census_path(cfg)
    census = census_mod.load_census(cpath)
    census_mod.seed_from_config(census, cfg)

    conn = open_db(data_dir / 'metrics.sqlite')
    totals = {'commits': 0, 'inserted': 0, 'quiet': 0, 'errors': 0}
    try:
        for sname, scfg in cfg['sources_resolved'].items():
            counts = backfill_source(sname, scfg['archive_repo'], cfg, census,
                                     conn, dry_run)
            for k in totals:
                totals[k] += counts[k]

        if not dry_run and cfg['dashboard'] and totals['inserted']:
            try:
                from . import dashboard
                legacy = set(cfg['sources_resolved']) == {''}
                out = data_dir / 'dashboard.html'
                out.write_text(
                    dashboard.generate_dashboard(
                        data_dir / 'metrics.sqlite', census_path=cpath,
                        sources=None if legacy else list(cfg['sources_resolved'])),
                    encoding='utf-8')
                log.info('dashboard regenerated: %s', out)
            except Exception:
                log.exception('dashboard generation failed (backfill itself succeeded)')
    finally:
        conn.close()
        if not dry_run:      # a dry run never took it; never remove someone else's
            lock.unlink(missing_ok=True)

    log.info('done: %d commit(s) replayed, %d night(s) gained rule rows, '
             '%d had no rule changes, %d error(s)%s',
             totals['commits'], totals['inserted'], totals['quiet'],
             totals['errors'], ' [dry run]' if dry_run else '')
    return 1 if totals['errors'] else 0


def main(argv=None) -> int:
    from .sync import load_config, setup_logging

    parser = argparse.ArgumentParser(
        description='Backfill historical rule-change metrics from the archive repos')
    parser.add_argument('config', type=Path)
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would be inserted without writing anything')
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg['data_dir'])
    return run(cfg, args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
