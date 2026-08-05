"""End-to-end tests for scripts/nightly_sync/nightly_sync.py.

Each test drives the real sync flow — real zips, a real git repo, a real
SQLite database — through the full nightly lifecycle: first run archives
everything as new, an idle run does nothing, a change produces a diff +
reports + metrics + an authored commit, and a vanished project is flagged
once.
"""

import json
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'nightly_sync'))
import nightly_sync  # noqa: E402


def _write_projx(path: Path, variables: dict, last_saver=None):
    """Build a minimal .driveprojx: a zip holding a TDM-format project.xml.
    last_saver=(display_name, email) also embeds a designMaster.xml carrying
    the DriveWorks last-saved-user special variables."""
    rows = ''.join(f'<Variable DisplayName="{n}" StoreName="{n}" Rule="{v}"/>'
                   for n, v in variables.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('driveProj/project.xml', f'<Project><Variables>{rows}</Variables></Project>')
        if last_saver is not None:
            disp, email = last_saver
            zf.writestr('driveProj/designMaster.xml',
                        '<DesignMaster>'
                        f'<SpecialVariable StoreName="DWCurrentUserDisplayName" Value="{disp}" />'
                        f'<SpecialVariable StoreName="DWCurrentUserEmailAddress" Value="{email}" />'
                        '</DesignMaster>')


def _git_log(repo: Path):
    out = subprocess.run(['git', '-C', str(repo), 'log', '--format=%s|%an <%ae>'],
                         capture_output=True, text=True, check=True).stdout
    return [line.split('|') for line in out.strip().splitlines()]


@pytest.fixture
def site(tmp_path):
    """A configured fake site: source share with two projects, config file."""
    source = tmp_path / 'share'
    _write_projx(source / 'Alpha.driveprojx', {'Width': '=800', 'Height': '=600'})
    _write_projx(source / 'Beta.driveprojx', {'Depth': '=300'})

    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(source),
        'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'),
        'owners': {'Alpha': 'Jane Smith <jane@example.com>'},
    }), encoding='utf-8')
    return {'tmp': tmp_path, 'source': source, 'cfg_path': cfg_path,
            'repo': tmp_path / 'repo', 'data': tmp_path / 'data'}


def _run(site, dry_run=False):
    cfg = nightly_sync.load_config(site['cfg_path'])
    return nightly_sync.run(cfg, dry_run)


def _db(site):
    return sqlite3.connect(site['data'] / 'metrics.sqlite')


def test_full_nightly_lifecycle(site):
    # --- Night 1: everything is new -------------------------------------
    assert _run(site) == 0

    assert (site['repo'] / 'Alpha' / 'driveProj' / 'project.xml').is_file()
    subjects = [s for s, _ in _git_log(site['repo'])]
    assert 'Alpha: added to archive' in subjects
    assert 'Beta: added to archive' in subjects

    with _db(site) as db:
        # New projects record a project-level event, NOT per-element rows —
        # the first sync must not explode the work metrics.
        assert db.execute('SELECT COUNT(*) FROM category_changes').fetchone()[0] == 0
        events = db.execute("SELECT project, status FROM element_changes "
                            "WHERE category='project'").fetchall()
        assert set(events) == {('Alpha', 'added'), ('Beta', 'added')}
        assert db.execute('SELECT projects_seen, projects_changed FROM runs').fetchone() == (2, 0)

    n_commits_night1 = len(_git_log(site['repo']))

    # --- Night 2: nothing changed ----------------------------------------
    assert _run(site) == 0
    assert len(_git_log(site['repo'])) == n_commits_night1  # no commit churn
    with _db(site) as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 2

    # --- Night 3: Alpha changed ------------------------------------------
    _write_projx(site['source'] / 'Alpha.driveprojx',
                 {'Width': '=700', 'Height': '=600', 'Extra': '=1'})
    assert _run(site) == 0

    log = _git_log(site['repo'])
    assert len(log) == n_commits_night1 + 1
    subject, author = log[0]
    assert subject.startswith('Alpha: +1 -0 ~1')
    assert author == 'Jane Smith <jane@example.com>'  # owner map drives authorship

    with _db(site) as db:
        cats = db.execute('SELECT project, category, added, removed, modified '
                          'FROM category_changes').fetchall()
        assert cats == [('Alpha', 'variables', 1, 0, 1)]
        elems = db.execute("SELECT element, status FROM element_changes "
                           "WHERE project='Alpha' AND category='variables'").fetchall()
        assert set(elems) == {('Extra', 'added'), ('Width', 'modified')}

    # Drill-down reports landed in the dated folder.
    day_dirs = list((site['data'] / 'reports').iterdir())
    assert len(day_dirs) == 1
    assert (day_dirs[0] / 'Alpha.json').is_file()
    assert '<!DOCTYPE html>' in (day_dirs[0] / 'Alpha.html').read_text(encoding='utf-8')
    doc = json.loads((day_dirs[0] / 'Alpha.json').read_text(encoding='utf-8'))
    assert doc['schema'] == 1

    # The repo state is the new content.
    text = (site['repo'] / 'Alpha' / 'driveProj' / 'project.xml').read_text(encoding='utf-8')
    assert '=700' in text and '=800' not in text

    # The dashboard regenerated as part of the run and reflects the change.
    dash = (site['data'] / 'dashboard.html').read_text(encoding='utf-8')
    assert 'Projx Work Dashboard' in dash
    assert 'Alpha' in dash

    # --- Night 4: Beta vanished from the share ----------------------------
    (site['source'] / 'Beta.driveprojx').unlink()
    assert _run(site) == 0
    assert (site['repo'] / 'Beta').is_dir()  # remove_missing defaults to false
    with _db(site) as db:
        removed = db.execute("SELECT COUNT(*) FROM element_changes "
                             "WHERE project='Beta' AND status='removed'").fetchone()[0]
        assert removed == 1

    # --- Night 5: still gone — the removed event is not re-recorded -------
    assert _run(site) == 0
    with _db(site) as db:
        removed = db.execute("SELECT COUNT(*) FROM element_changes "
                             "WHERE project='Beta' AND status='removed'").fetchone()[0]
        assert removed == 1


def test_dry_run_touches_nothing(site):
    assert _run(site) == 0  # baseline
    n_commits = len(_git_log(site['repo']))

    _write_projx(site['source'] / 'Alpha.driveprojx', {'Width': '=1'})
    assert _run(site, dry_run=True) == 0

    assert len(_git_log(site['repo'])) == n_commits
    with _db(site) as db:
        assert db.execute('SELECT COUNT(*) FROM category_changes').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1  # baseline only
    # The archive still holds the OLD content.
    text = (site['repo'] / 'Alpha' / 'driveProj' / 'project.xml').read_text(encoding='utf-8')
    assert '=800' in text


def test_one_bad_zip_does_not_kill_the_run(site):
    (site['source'] / 'Corrupt.driveprojx').write_bytes(b'this is not a zip')
    assert _run(site) == 1  # error exit code, but...

    # ...the good projects still synced fully.
    subjects = [s for s, _ in _git_log(site['repo'])]
    assert 'Alpha: added to archive' in subjects
    assert 'Beta: added to archive' in subjects
    with _db(site) as db:
        errors = db.execute('SELECT errors FROM runs').fetchone()[0]
        assert 'Corrupt.driveprojx' in errors


def test_unreachable_source_is_a_clean_failure(site):
    site['cfg_path'].write_text(json.dumps({
        'source_dir': str(site['tmp'] / 'no-such-share'),
        'archive_repo': str(site['repo']),
        'data_dir': str(site['data']),
    }), encoding='utf-8')
    assert _run(site) == 2


def test_lock_prevents_overlapping_runs(site):
    site['data'].mkdir(parents=True, exist_ok=True)
    (site['data'] / 'sync.lock').write_text('other run', encoding='utf-8')
    assert _run(site) == 3


def test_safe_extract_rejects_zip_slip(tmp_path):
    evil = tmp_path / 'evil.driveprojx'
    with zipfile.ZipFile(evil, 'w') as zf:
        zf.writestr('../escape.txt', 'pwned')
    with pytest.raises(ValueError):
        nightly_sync.safe_extract(evil, tmp_path / 'out')


def test_duplicate_project_name_is_skipped_not_crash(tmp_path):
    # Two source files share a stem ("Dup"); the archive can only hold one
    # project by that name. The run must skip the second with a recorded error
    # rather than crashing (a bare mkdir would raise FileExistsError).
    source = tmp_path / 'share'
    _write_projx(source / 'A' / 'Dup.driveprojx', {'X': '=1'})
    _write_projx(source / 'B' / 'Dup.driveprojx', {'Y': '=2'})
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(source),
        'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'),
    }), encoding='utf-8')

    cfg = nightly_sync.load_config(cfg_path)
    assert nightly_sync.run(cfg, dry_run=False) == 1  # error exit, but no crash

    # Exactly one 'Dup' archived; the duplicate is flagged in the run errors.
    assert (tmp_path / 'repo' / 'Dup').is_dir()
    with sqlite3.connect(tmp_path / 'data' / 'metrics.sqlite') as db:
        errors = db.execute('SELECT errors FROM runs').fetchone()[0]
        assert 'duplicate project name "Dup"' in errors


def test_resolve_author_precedence(tmp_path):
    proj = tmp_path / 'P'
    _write_projx(tmp_path / 'P.driveprojx', {'X': '=1'},
                 last_saver=('TusharShewale', ''))
    # extract so resolve_author can read designMaster.xml
    nightly_sync.safe_extract(tmp_path / 'P.driveprojx', proj)

    base = {'owners': {}, 'author_aliases': {}, 'derive_author_from_file': False}
    # derive off -> empty (falls back to committer identity)
    assert nightly_sync.resolve_author('P', proj, base) == ''
    # derive on, no alias, no email -> "Display <>"
    on = {**base, 'derive_author_from_file': True}
    assert nightly_sync.resolve_author('P', proj, on) == 'TusharShewale <>'
    # alias collapses the spelling variant onto one identity
    aliased = {**on, 'author_aliases': {'TusharShewale': 'Tushar Shewale <t@x.com>'}}
    assert nightly_sync.resolve_author('P', proj, aliased) == 'Tushar Shewale <t@x.com>'
    # explicit owners entry always wins over the file
    override = {**aliased, 'owners': {'P': 'Boss <boss@x.com>'}}
    assert nightly_sync.resolve_author('P', proj, override) == 'Boss <boss@x.com>'


def test_author_derived_from_file_end_to_end(tmp_path):
    source = tmp_path / 'share'
    _write_projx(source / 'Zeta.driveprojx', {'W': '=1'},
                 last_saver=('DaveDewey', 'dave@x.com'))
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(source),
        'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'),
        'derive_author_from_file': True,
        'author_aliases': {'DaveDewey': 'Dave Dewey <dave@x.com>'},
    }), encoding='utf-8')

    cfg = nightly_sync.load_config(cfg_path)
    assert nightly_sync.run(cfg, dry_run=False) == 0
    # the first-run "added" commit is authored by the resolved last-saver
    log = _git_log(tmp_path / 'repo')
    subject, author = log[0]
    assert subject == 'Zeta: added to archive'
    assert author == 'Dave Dewey <dave@x.com>'


def test_find_projects_honours_exclude(tmp_path):
    for rel in ('Projects/Alpha.driveprojx',
                'Projects/Alpha/Alpha.driveprojx',
                'Projects/Beta/Beta.driveprojx',
                'Projects/Beta/Backup/Beta.driveprojx',
                '_Archive/OldThing.driveprojx',
                'DriveWorks Archive Files/Archived Projects/Gamma.driveprojx'):
        _write_projx(tmp_path / rel, {'X': '=1'})

    # No exclude: every file is found, including the archive/backup/dupes.
    assert len(nightly_sync.find_projects(tmp_path, True)) == 6

    excluded = []
    kept = nightly_sync.find_projects(
        tmp_path, True,
        ['*archive*', '*/backup/*', 'projects/alpha.driveprojx'],
        excluded_out=excluded)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in kept)
    # archive folders gone, Backup gone, the loose Alpha dupe gone; the
    # foldered Alpha and Beta survive with unique stems.
    assert rels == ['Projects/Alpha/Alpha.driveprojx', 'Projects/Beta/Beta.driveprojx']

    # excluded_out reports each dropped file with the pattern that matched it,
    # which is what a --dry-run prints for auditing.
    assert len(excluded) == 4
    assert ('Projects/Alpha.driveprojx', 'projects/alpha.driveprojx') in excluded
    assert ('Projects/Beta/Backup/Beta.driveprojx', '*/backup/*') in excluded


def test_load_config_rejects_missing_keys(tmp_path):
    p = tmp_path / 'config.json'
    p.write_text('{"source_dir": "x"}', encoding='utf-8')
    with pytest.raises(SystemExit):
        nightly_sync.load_config(p)


def test_group_db_resolution_flows_into_nightly_html(site, monkeypatch):
    """With db_server/db_database configured, a changed project's HTML
    report gets name resolution (one connection per source, closed after);
    the JSON diff keeps raw ids."""
    from dw_compare import sync as sync_mod

    cfg = json.loads(site['cfg_path'].read_text(encoding='utf-8'))
    cfg['db_server'], cfg['db_database'] = 'KEES-DB', 'KEES'
    site['cfg_path'].write_text(json.dumps(cfg), encoding='utf-8')

    events = []

    class FakeDb:
        def close(self):
            events.append('closed')

    def fake_open(scfg):
        events.append(f"open:{scfg['db_server']}/{scfg['db_database']}")
        return FakeDb()

    captured = {}
    monkeypatch.setattr(sync_mod, 'open_group_db', fake_open)
    monkeypatch.setattr(sync_mod, '_resolve_names',
                        lambda db, proj: ({'guid-1': 'Bracket Assembly'},
                                          {'prop-1': 'Material'}, {}))
    monkeypatch.setattr(sync_mod, 'generate_html_report',
                        lambda old, new, on, nn, *res: captured.update(res=res)
                        or '<html>ok</html>')

    assert _run(site) == 0                     # night 1: all new, no diffs
    _write_projx(site['source'] / 'Alpha.driveprojx', {'Width': '=999'})
    assert _run(site) == 0                     # night 2: Alpha changed

    old_resolved, new_resolved, old_props, new_props, old_types, new_types = captured['res']
    assert old_resolved == new_resolved == {'guid-1': 'Bracket Assembly'}
    assert old_props == {'prop-1': 'Material'}
    assert events.count('open:KEES-DB/KEES') == 2   # once per run, not per project
    assert events.count('closed') == 2


def test_dry_run_never_opens_group_db(site, monkeypatch):
    from dw_compare import sync as sync_mod
    cfg = json.loads(site['cfg_path'].read_text(encoding='utf-8'))
    cfg['db_server'], cfg['db_database'] = 'KEES-DB', 'KEES'
    site['cfg_path'].write_text(json.dumps(cfg), encoding='utf-8')

    def boom(scfg):
        raise AssertionError('dry run must not touch the database')

    monkeypatch.setattr(sync_mod, 'open_group_db', boom)
    assert _run(site, dry_run=True) == 0
