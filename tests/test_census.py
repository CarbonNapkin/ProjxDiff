"""Tests for the census: discovery, dispositions, attribution, and healing.

Drives the real sync engine (dw_compare.sync) plus the census layer through
the triage lifecycle: new projects register as pending and still sync, ignore
stops syncing, unmapped users are recorded raw and flagged, mapping a user
heals prior metrics rows, and the dashboard surfaces it all.
"""

import json
import sqlite3
import subprocess
import zipfile
from pathlib import Path

from dw_compare import census as census_mod
from dw_compare import sync as sync_mod
from dw_compare import dashboard as dashboard_mod


def _write_projx(path: Path, variables: dict, last_saver=None):
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


def _site(tmp_path, **extra_cfg):
    source = tmp_path / 'share'
    source.mkdir(parents=True, exist_ok=True)
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(source),
        'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'),
        'derive_author_from_file': True,
        **extra_cfg,
    }), encoding='utf-8')
    return {'source': source, 'cfg_path': cfg_path, 'repo': tmp_path / 'repo',
            'data': tmp_path / 'data', 'census': tmp_path / 'data' / 'census.json'}


def _run(site, dry_run=False):
    return sync_mod.run(sync_mod.load_config(site['cfg_path']), dry_run)


def _census(site):
    return census_mod.load_census(site['census'])


def _git_authors(repo):
    out = subprocess.run(['git', '-C', str(repo), 'log', '--format=%s|%an <%ae>'],
                         capture_output=True, text=True, check=True).stdout
    return [line.split('|') for line in out.strip().splitlines()]


def test_sync_registers_new_projects_and_users_as_pending(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'Alpha.driveprojx', {'W': '=1'}, last_saver=('Zach', ''))
    assert _run(site) == 0

    census = _census(site)
    assert census['projects']['Alpha']['disposition'] == 'pending'
    assert census['projects']['Alpha']['path'] == 'Alpha.driveprojx'
    assert census['users'] == {'Zach': None}          # unmapped, awaiting identity
    # Pending projects still sync — the pipeline never waits on a human.
    assert (site['repo'] / 'Alpha').is_dir()
    # The commit is authored with the raw name; metrics record the raw name.
    assert _git_authors(site['repo'])[0][1] == 'Zach <>'


def test_ignored_project_stops_syncing_but_keeps_archive(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'Junk.driveprojx', {'W': '=1'})
    assert _run(site) == 0
    assert (site['repo'] / 'Junk').is_dir()

    census = _census(site)
    census['projects']['Junk']['disposition'] = 'ignore'
    census_mod.save_census(site['census'], census)

    # Change the source; an ignored project must not produce a commit,
    # metrics, or a "missing" event.
    _write_projx(site['source'] / 'Junk.driveprojx', {'W': '=2'})
    n_before = len(_git_authors(site['repo']))
    assert _run(site) == 0
    assert len(_git_authors(site['repo'])) == n_before
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM category_changes').fetchone()[0] == 0
        removed = db.execute("SELECT COUNT(*) FROM element_changes "
                             "WHERE status='removed'").fetchone()[0]
        assert removed == 0


def test_mapping_a_user_heals_past_metrics(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'Alpha.driveprojx', {'W': '=1'}, last_saver=('Zach', ''))
    assert _run(site) == 0
    _write_projx(site['source'] / 'Alpha.driveprojx', {'W': '=2'}, last_saver=('Zach', ''))
    assert _run(site) == 0  # night 2: a change recorded under raw name 'Zach'

    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        owners = {r[0] for r in db.execute('SELECT DISTINCT owner FROM element_changes')}
    assert 'Zach' in owners

    census = _census(site)
    census['users']['Zach'] = 'Zach Miller <zach@x.com>'
    census_mod.save_census(site['census'], census)

    assert _run(site) == 0  # night 3: healing pass runs at start
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        owners = {r[0] for r in db.execute('SELECT DISTINCT owner FROM element_changes')}
    assert 'Zach' not in owners
    assert 'Zach Miller <zach@x.com>' in owners

    # New changes now commit under the mapped identity.
    _write_projx(site['source'] / 'Alpha.driveprojx', {'W': '=3'}, last_saver=('Zach', ''))
    assert _run(site) == 0
    assert _git_authors(site['repo'])[0][1] == 'Zach Miller <zach@x.com>'


def test_census_scan_discovers_without_syncing(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'}, last_saver=('Jane', ''))
    _write_projx(site['source'] / 'sub' / 'B.driveprojx', {'Y': '=2'}, last_saver=('Mark', ''))

    cfg = sync_mod.load_config(site['cfg_path'])
    census = census_mod.load_census(site['census'])
    summary = census_mod.scan(cfg, census, sync_mod.find_projects)

    assert summary['new_projects'] == ['A', 'B']
    assert summary['new_users'] == ['Jane', 'Mark']
    assert summary['unmapped'] == ['Jane', 'Mark']
    assert census['projects']['B']['path'] == 'sub/B.driveprojx'
    assert not (site['repo'] / 'A').exists()  # scan alone archives nothing


def test_rescan_preserves_human_entries(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'}, last_saver=('Jane', ''))

    cfg = sync_mod.load_config(site['cfg_path'])
    census = census_mod.load_census(site['census'])
    census_mod.scan(cfg, census, sync_mod.find_projects)
    census['users']['Jane'] = 'Jane Smith <jane@x.com>'
    census['projects']['A']['disposition'] = 'track'

    census_mod.scan(cfg, census, sync_mod.find_projects)  # rescan
    assert census['users']['Jane'] == 'Jane Smith <jane@x.com>'
    assert census['projects']['A']['disposition'] == 'track'


def test_moved_project_updates_path_without_conflict(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'})
    assert _run(site) == 0
    (site['source'] / 'sub').mkdir()
    (site['source'] / 'A.driveprojx').rename(site['source'] / 'sub' / 'A.driveprojx')

    assert _run(site) == 0  # no error, no duplicate archive
    census = _census(site)
    assert census['projects']['A']['path'] == 'sub/A.driveprojx'
    assert census['conflicts'] == []


def test_name_conflict_syncs_registered_path_and_flags_other(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A' / 'Dup.driveprojx', {'X': '=1'})
    assert _run(site) == 0  # registers A/Dup.driveprojx

    _write_projx(site['source'] / 'B' / 'Dup.driveprojx', {'Y': '=2'})
    assert _run(site) == 1  # duplicate recorded as an error (exit 1), not a crash

    census = _census(site)
    assert census['conflicts'] == [{'project': 'Dup', 'path': 'B/Dup.driveprojx',
                                    'registered': 'A/Dup.driveprojx'}]
    # The registered file is the one in the archive.
    text = (site['repo'] / 'Dup' / 'driveProj' / 'project.xml').read_text(encoding='utf-8')
    assert 'X' in text and 'Y' not in text


def test_census_cli_map_and_disposition(tmp_path, capsys):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'}, last_saver=('Zach', ''))

    assert census_mod.main([str(site['cfg_path'])]) == 0
    out = capsys.readouterr().out
    assert '1 project(s) pending disposition' in out
    assert 'Zach' in out

    assert census_mod.main([str(site['cfg_path']), '--no-scan',
                            '--map', 'Zach=Zach Miller <z@x.com>',
                            '--track', 'A']) == 0
    census = _census(site)
    assert census['users']['Zach'] == 'Zach Miller <z@x.com>'
    assert census['projects']['A']['disposition'] == 'track'
    assert 'nothing needs attention' in capsys.readouterr().out


def test_dashboard_shows_attention_panel(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'NewThing.driveprojx', {'X': '=1'}, last_saver=('Zach', ''))
    assert _run(site) == 0

    html = (site['data'] / 'dashboard.html').read_text(encoding='utf-8')
    assert 'Needs attention' in html
    assert 'NewThing' in html
    assert 'Zach' in html
    assert 'Manage Nightly Sync' in html


def test_dashboard_attention_panel_absent_when_quiet(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'}, last_saver=('Jane', ''))
    assert _run(site) == 0

    census = _census(site)
    census['users']['Jane'] = 'Jane Smith <jane@x.com>'
    census['projects']['A']['disposition'] = 'track'
    census_mod.save_census(site['census'], census)
    assert _run(site) == 0

    html = (site['data'] / 'dashboard.html').read_text(encoding='utf-8')
    assert 'Needs attention' not in html


def test_legacy_author_aliases_seed_the_census(tmp_path):
    site = _site(tmp_path, author_aliases={'Zach': 'Zach Miller <z@x.com>'})
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'}, last_saver=('Zach', ''))
    assert _run(site) == 0

    # The legacy alias is imported as a mapped census user, so nothing is
    # flagged and the commit is authored with the mapped identity.
    census = _census(site)
    assert census['users']['Zach'] == 'Zach Miller <z@x.com>'
    assert census_mod.unmapped_users(census) == []
    assert _git_authors(site['repo'])[0][1] == 'Zach Miller <z@x.com>'


def test_unmapped_user_with_email_records_raw_but_commits_with_email(tmp_path):
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'},
                 last_saver=('Tushar', 'tushar@x.com'))
    assert _run(site) == 0

    # The commit carries the email from the file; the metrics record the raw
    # display name so a later mapping can heal them.
    assert _git_authors(site['repo'])[0][1] == 'Tushar <tushar@x.com>'
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        owners = {r[0] for r in db.execute('SELECT DISTINCT owner FROM element_changes')}
    assert owners == {'Tushar'}
    assert _census(site)['users'] == {'Tushar': None}


def test_heal_covers_legacy_owner_format(tmp_path):
    # Pre-census syncs (v1.1.2) stored unmapped owners as "Raw <>"; healing
    # must catch those rows too, not just the bare raw name.
    site = _site(tmp_path)
    site['data'].mkdir(parents=True, exist_ok=True)
    conn = sync_mod.open_db(site['data'] / 'metrics.sqlite')
    conn.execute("INSERT INTO element_changes (run_date, project, owner, category,"
                 " element, status) VALUES ('2026-08-01', 'P', 'Zach <>', 'variables',"
                 " 'W', 'modified')")
    conn.close()

    census = {'schema': 1, 'users': {'Zach': 'Zach Miller <z@x.com>'},
              'projects': {}, 'conflicts': []}
    conn = sqlite3.connect(site['data'] / 'metrics.sqlite', isolation_level=None)
    try:
        assert census_mod.heal_metrics(conn, census) == 1
        owner = conn.execute('SELECT owner FROM element_changes').fetchone()[0]
    finally:
        conn.close()
    assert owner == 'Zach Miller <z@x.com>'


def test_project_reappearing_after_removal_resumes_cleanly(tmp_path):
    # Removed from the share (flagged once, archive kept), then it comes back
    # with changes: the sync diffs against the retained archive state — no
    # spurious second "removed", no metric explosion, history continuous.
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'W': '=1'}, last_saver=('Jane', ''))
    assert _run(site) == 0
    (site['source'] / 'A.driveprojx').unlink()
    assert _run(site) == 0  # removed event recorded

    _write_projx(site['source'] / 'A.driveprojx', {'W': '=2'}, last_saver=('Jane', ''))
    assert _run(site) == 0
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        removed = db.execute("SELECT COUNT(*) FROM element_changes "
                             "WHERE status='removed' AND category='project'").fetchone()[0]
        assert removed == 1
        cats = db.execute("SELECT category, modified FROM category_changes").fetchall()
        assert cats == [('variables', 1)]  # a normal diff, not a re-add explosion


def test_sync_manager_gui_save_writes_census_and_heals(tmp_path, monkeypatch):
    # Real Tk widgets driving the real save path; skipped where no display
    # exists (headless Linux CI) and exercised on the Windows/macOS runners.
    import pytest
    tk = pytest.importorskip('tkinter')
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available')
    root.withdraw()

    from dw_compare import gui as gui_mod

    data_dir = tmp_path / 'data'
    conn = sync_mod.open_db(data_dir / 'metrics.sqlite')
    conn.execute("INSERT INTO element_changes (run_date, project, owner, category,"
                 " element, status) VALUES ('2026-08-01', 'NewThing', 'Zach',"
                 " 'variables', 'W', 'modified')")
    conn.close()

    census = {'schema': 1, 'users': {'Zach': None},
              'projects': {'NewThing': {'path': 'NewThing.driveprojx',
                                        'disposition': 'pending'}},
              'conflicts': []}
    cpath = data_dir / 'census.json'
    mgr = gui_mod._SyncManager(root, {'data_dir': str(data_dir)}, cpath, census)

    mgr.proj_vars['NewThing'].set('Track')  # UI label; saved as 'track'
    mgr.user_entries['Zach'].insert(0, 'Zach Miller <z@x.com>')

    shown = {}
    monkeypatch.setattr(gui_mod.messagebox, 'showinfo',
                        lambda *a, **k: shown.setdefault('ok', a))
    try:
        mgr._save()
    finally:
        root.destroy()

    saved = json.loads(cpath.read_text(encoding='utf-8'))
    assert saved['projects']['NewThing']['disposition'] == 'track'
    assert saved['users']['Zach'] == 'Zach Miller <z@x.com>'
    with sqlite3.connect(data_dir / 'metrics.sqlite') as db:
        assert db.execute('SELECT owner FROM element_changes').fetchone()[0] == \
            'Zach Miller <z@x.com>'
    assert 'ok' in shown


def test_cli_modes_are_wired(tmp_path, monkeypatch, capsys):
    import sys as _sys
    import dw_compare.__main__ as cli
    site = _site(tmp_path)
    _write_projx(site['source'] / 'A.driveprojx', {'X': '=1'})

    monkeypatch.setattr(_sys, 'argv',
                        ['dw_compare', '--census', str(site['cfg_path'])])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code == 0
    assert 'census saved' in capsys.readouterr().out
