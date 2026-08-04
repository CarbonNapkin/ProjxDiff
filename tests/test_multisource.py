"""Tests for site configs (multiple named sources).

The driving constraint: prod and staging trees share project names, so each
source needs its own archive repo and census namespace while users, metrics,
and the dashboard stay unified. Legacy single-source behavior is covered by
the existing suites and must not change.
"""

import json
import sqlite3
import subprocess
import zipfile
from datetime import date
from pathlib import Path

import pytest

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
    """A two-source site: prod and staging, both containing 'Roof Curb'."""
    prod = tmp_path / 'prod-share'
    staging = tmp_path / 'staging-share'
    _write_projx(prod / 'Roof Curb.driveprojx', {'W': '=100'}, last_saver=('Zach', ''))
    _write_projx(staging / 'Roof Curb.driveprojx', {'W': '=999'}, last_saver=('Zach', ''))

    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'sources': {
            'prod': {'source_dir': str(prod), 'archive_repo': str(tmp_path / 'repo-prod')},
            'staging': {'source_dir': str(staging), 'archive_repo': str(tmp_path / 'repo-staging')},
        },
        'data_dir': str(tmp_path / 'data'),
        'derive_author_from_file': True,
        **extra_cfg,
    }), encoding='utf-8')
    return {'prod': prod, 'staging': staging, 'cfg_path': cfg_path,
            'repo_prod': tmp_path / 'repo-prod', 'repo_staging': tmp_path / 'repo-staging',
            'data': tmp_path / 'data', 'census': tmp_path / 'data' / 'census.json'}


def _run(site, dry_run=False):
    return sync_mod.run(sync_mod.load_config(site['cfg_path']), dry_run)


def _git_authors(repo):
    out = subprocess.run(['git', '-C', str(repo), 'log', '--format=%s|%an <%ae>'],
                         capture_output=True, text=True, check=True).stdout
    return [line.split('|') for line in out.strip().splitlines()]


def test_same_project_name_in_two_sources_does_not_collide(tmp_path):
    site = _site(tmp_path)
    assert _run(site) == 0

    # Each source archives into its own repo; the contents stay distinct.
    prod_xml = (site['repo_prod'] / 'Roof Curb' / 'driveProj' / 'project.xml').read_text(encoding='utf-8')
    staging_xml = (site['repo_staging'] / 'Roof Curb' / 'driveProj' / 'project.xml').read_text(encoding='utf-8')
    assert '=100' in prod_xml and '=999' in staging_xml

    # Census keys are namespaced; users are shared (one entry, not two).
    census = census_mod.load_census(site['census'])
    assert set(census['projects']) == {'prod/Roof Curb', 'staging/Roof Curb'}
    assert census['users'] == {'Zach': None}


def test_changes_are_tagged_with_source_and_reports_namespaced(tmp_path):
    site = _site(tmp_path)
    assert _run(site) == 0
    _write_projx(site['prod'] / 'Roof Curb.driveprojx', {'W': '=101'}, last_saver=('Zach', ''))
    assert _run(site) == 0

    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        rows = db.execute('SELECT source, project, modified FROM category_changes').fetchall()
    assert rows == [('prod', 'Roof Curb', 1)]  # only prod changed, tagged prod

    day_dirs = list((site['data'] / 'reports' / 'prod').iterdir())
    assert len(day_dirs) == 1
    assert (day_dirs[0] / 'Roof Curb.html').is_file()
    assert not (site['data'] / 'reports' / 'staging').exists()


def test_mapping_a_user_once_heals_and_covers_all_sources(tmp_path):
    site = _site(tmp_path)
    assert _run(site) == 0
    # Change both sources so both record metrics under the raw name.
    _write_projx(site['prod'] / 'Roof Curb.driveprojx', {'W': '=101'}, last_saver=('Zach', ''))
    _write_projx(site['staging'] / 'Roof Curb.driveprojx', {'W': '=998'}, last_saver=('Zach', ''))
    assert _run(site) == 0

    census = census_mod.load_census(site['census'])
    census['users']['Zach'] = 'Zach Miller <z@x.com>'
    census_mod.save_census(site['census'], census)
    assert _run(site) == 0  # healing pass

    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        owners = {r[0] for r in db.execute('SELECT DISTINCT owner FROM element_changes')}
    assert owners == {'Zach Miller <z@x.com>'}

    # Subsequent commits in BOTH repos use the mapped identity.
    _write_projx(site['prod'] / 'Roof Curb.driveprojx', {'W': '=102'}, last_saver=('Zach', ''))
    _write_projx(site['staging'] / 'Roof Curb.driveprojx', {'W': '=997'}, last_saver=('Zach', ''))
    assert _run(site) == 0
    assert _git_authors(site['repo_prod'])[0][1] == 'Zach Miller <z@x.com>'
    assert _git_authors(site['repo_staging'])[0][1] == 'Zach Miller <z@x.com>'


def test_unreachable_source_skips_but_others_sync(tmp_path):
    site = _site(tmp_path)
    cfg = json.loads(site['cfg_path'].read_text(encoding='utf-8'))
    cfg['sources']['prod']['source_dir'] = str(tmp_path / 'no-such-share')
    site['cfg_path'].write_text(json.dumps(cfg), encoding='utf-8')

    assert _run(site) == 1  # flagged as an error...
    assert (site['repo_staging'] / 'Roof Curb').is_dir()  # ...but staging synced
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        errors = db.execute('SELECT errors FROM runs').fetchone()[0]
    assert 'prod' in errors and 'unreachable' in errors


def test_ignore_one_source_copy_keeps_the_other_syncing(tmp_path):
    site = _site(tmp_path)
    assert _run(site) == 0

    census = census_mod.load_census(site['census'])
    census['projects']['staging/Roof Curb']['disposition'] = 'ignore'
    census_mod.save_census(site['census'], census)

    _write_projx(site['prod'] / 'Roof Curb.driveprojx', {'W': '=101'}, last_saver=('Zach', ''))
    _write_projx(site['staging'] / 'Roof Curb.driveprojx', {'W': '=998'}, last_saver=('Zach', ''))
    n_staging = len(_git_authors(site['repo_staging']))
    assert _run(site) == 0

    assert len(_git_authors(site['repo_staging'])) == n_staging  # ignored: no commit
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        sources = {r[0] for r in db.execute('SELECT DISTINCT source FROM category_changes')}
    assert sources == {'prod'}


def test_owners_override_matches_plain_name_across_sources(tmp_path):
    site = _site(tmp_path, owners={'Roof Curb': 'Boss <boss@x.com>'})
    assert _run(site) == 0
    assert _git_authors(site['repo_prod'])[0][1] == 'Boss <boss@x.com>'
    assert _git_authors(site['repo_staging'])[0][1] == 'Boss <boss@x.com>'


def test_census_scan_namespaces_site_config_keys(tmp_path):
    site = _site(tmp_path)
    cfg = sync_mod.load_config(site['cfg_path'])
    census = census_mod.load_census(site['census'])
    summary = census_mod.scan(cfg, census, sync_mod.find_projects)
    assert summary['new_projects'] == ['prod/Roof Curb', 'staging/Roof Curb']
    assert summary['new_users'] == ['Zach']


def test_dashboard_gets_tabs_source_column_and_namespaced_links(tmp_path):
    site = _site(tmp_path)
    assert _run(site) == 0
    _write_projx(site['prod'] / 'Roof Curb.driveprojx', {'W': '=101'}, last_saver=('Zach', ''))
    assert _run(site) == 0

    html = (site['data'] / 'dashboard.html').read_text(encoding='utf-8')
    assert 'class="tab' in html and '>prod</button>' in html and '>staging</button>' in html
    assert '<th>Source</th>' in html
    assert 'reports/prod/' in html  # report links carry the source segment


def test_legacy_db_gains_source_column_on_open(tmp_path):
    # A pre-1.3.0 metrics DB has no source column; open_db must migrate it
    # in place with existing rows defaulting to the legacy source ''.
    db_path = tmp_path / 'metrics.sqlite'
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE category_changes (id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL, project TEXT NOT NULL, owner TEXT,
            category TEXT NOT NULL, added INTEGER NOT NULL, removed INTEGER NOT NULL,
            modified INTEGER NOT NULL, unchanged INTEGER NOT NULL);
        CREATE TABLE element_changes (id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL, project TEXT NOT NULL, owner TEXT,
            category TEXT NOT NULL, element TEXT NOT NULL, status TEXT NOT NULL);
        INSERT INTO category_changes (run_date, project, owner, category,
            added, removed, modified, unchanged)
            VALUES ('2026-08-01', 'P', 'Zach', 'variables', 1, 0, 0, 5);
    """)
    conn.commit()
    conn.close()

    conn = sync_mod.open_db(db_path)
    try:
        row = conn.execute('SELECT source FROM category_changes').fetchone()
    finally:
        conn.close()
    assert row == ('',)


def test_load_config_validates_site_shape(tmp_path):
    def cfg_with(sources):
        p = tmp_path / 'c.json'
        p.write_text(json.dumps({'sources': sources, 'data_dir': str(tmp_path / 'd')}),
                     encoding='utf-8')
        return p

    with pytest.raises(SystemExit, match='must be'):
        sync_mod.load_config(cfg_with({'bad name!': {
            'source_dir': 'x', 'archive_repo': 'y'}}))
    with pytest.raises(SystemExit, match='missing'):
        sync_mod.load_config(cfg_with({'prod': {'source_dir': 'x'}}))

    p = tmp_path / 'no_data.json'
    p.write_text(json.dumps({'sources': {'prod': {'source_dir': 'x',
                                                  'archive_repo': 'y'}}}),
                 encoding='utf-8')
    with pytest.raises(SystemExit, match='data_dir'):
        sync_mod.load_config(p)
