"""Tests for site setup: init_site, add_source, and the CLI onboarding flow
(--init-config, --census --add-source) that replaces hand-written configs."""

import json
import zipfile
from pathlib import Path

import pytest

from dw_compare import census as census_mod
from dw_compare import sync as sync_mod


def _write_projx(path: Path, saver='Jane'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('driveProj/project.xml',
                    '<Project><Variables><Variable DisplayName="W" StoreName="W" Rule="=1"/></Variables></Project>')
        zf.writestr('driveProj/designMaster.xml',
                    f'<DesignMaster><SpecialVariable StoreName="DWCurrentUserDisplayName" Value="{saver}" /></DesignMaster>')


# ---------- slugging ----------

def test_slug_source_name():
    assert sync_mod.slug_source_name('prod') == 'prod'
    assert sync_mod.slug_source_name('Dayton plant') == 'Dayton-plant'
    assert sync_mod.slug_source_name('  East  Wing 2 ') == 'East-Wing-2'
    assert sync_mod.slug_source_name('a&b!') == 'ab'
    with pytest.raises(SystemExit, match='no usable characters'):
        sync_mod.slug_source_name('!!!')


# ---------- init_site ----------

def test_init_site_creates_valid_empty_config(tmp_path):
    cfg_path = sync_mod.init_site(tmp_path / 'ProjxArchive')
    assert cfg_path == tmp_path / 'ProjxArchive' / 'config.json'
    raw = json.loads(cfg_path.read_text(encoding='utf-8'))
    assert raw['sources'] == {}
    assert raw['derive_author_from_file'] is True
    assert raw['data_dir'].endswith('ProjxArchive/data')


def test_init_site_refuses_overwrite(tmp_path):
    sync_mod.init_site(tmp_path)
    with pytest.raises(SystemExit, match='already exists'):
        sync_mod.init_site(tmp_path)


# ---------- add_source ----------

def test_add_source_derives_paths_and_validates(tmp_path):
    cfg_path = sync_mod.init_site(tmp_path)
    share = tmp_path / 'shares' / 'prod'
    share.mkdir(parents=True)

    slug = sync_mod.add_source(cfg_path, 'Dayton plant', share)
    assert slug == 'Dayton-plant'
    cfg = sync_mod.load_config(cfg_path)
    resolved = cfg['sources_resolved']['Dayton-plant']
    assert resolved['source_dir'] == share
    assert resolved['archive_repo'] == tmp_path / 'repos' / 'Dayton-plant'


def test_add_source_refusals(tmp_path):
    cfg_path = sync_mod.init_site(tmp_path)
    share = tmp_path / 'share'
    share.mkdir()
    sync_mod.add_source(cfg_path, 'prod', share)

    with pytest.raises(SystemExit, match='already exists'):
        sync_mod.add_source(cfg_path, 'prod', share)
    with pytest.raises(SystemExit, match='does not exist'):
        sync_mod.add_source(cfg_path, 'staging', tmp_path / 'nope')
    with pytest.raises(SystemExit, match='config not found'):
        sync_mod.add_source(tmp_path / 'missing.json', 'x', share)

    legacy = tmp_path / 'legacy.json'
    legacy.write_text(json.dumps({'source_dir': 'a', 'archive_repo': 'b',
                                  'data_dir': 'c'}), encoding='utf-8')
    with pytest.raises(SystemExit, match='legacy'):
        sync_mod.add_source(legacy, 'prod', share)


# ---------- the full CLI onboarding flow ----------

def test_onboarding_flow_init_add_scan_sync(tmp_path, capsys):
    # 1. init the site
    cfg_path = sync_mod.init_site(tmp_path / 'archive')

    # 2. --census --add-source: adds the group AND scans it in one command
    share = tmp_path / 'projects'
    _write_projx(share / 'Conveyor.driveprojx', saver='Jane')
    assert census_mod.main([str(cfg_path), '--add-source', f'prod={share}']) == 0
    out = capsys.readouterr().out
    assert 'added group: prod' in out
    assert 'Conveyor' in out and 'Jane' in out  # pending + unmapped listed

    census = census_mod.load_census(tmp_path / 'archive' / 'data' / 'census.json')
    assert census['projects']['prod/Conveyor']['disposition'] == 'pending'

    # 3. a real sync runs green against the generated config
    assert sync_mod.run(sync_mod.load_config(cfg_path), dry_run=False) == 0
    assert (tmp_path / 'archive' / 'repos' / 'prod' / 'Conveyor').is_dir()


def test_missing_config_error_is_friendly(tmp_path):
    with pytest.raises(SystemExit, match='config not found'):
        sync_mod.load_config(tmp_path / 'nope.json')
    with pytest.raises(SystemExit, match='--init-config'):
        sync_mod.load_config(tmp_path / 'nope.json')


# ---------- group-db keys in configs (nightly name resolution) ----------

def test_load_config_carries_db_keys_per_source_with_fallback(tmp_path):
    for d in ('p', 'q', 'r1', 'r2'):
        (tmp_path / d).mkdir()
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'sources': {
            'prod': {'source_dir': str(tmp_path / 'p'),
                     'archive_repo': str(tmp_path / 'r1'),
                     'db_server': 'KEES-DB', 'db_database': 'KEES'},
            'staging': {'source_dir': str(tmp_path / 'q'),
                        'archive_repo': str(tmp_path / 'r2')},
        },
        'db_server': 'SHARED', 'db_database': 'SharedDB',
        'data_dir': str(tmp_path / 'data'),
    }), encoding='utf-8')
    cfg = sync_mod.load_config(cfg_path)
    assert cfg['sources_resolved']['prod']['db_server'] == 'KEES-DB'
    assert cfg['sources_resolved']['prod']['db_database'] == 'KEES'
    # Sources without their own keys inherit the top-level ones.
    assert cfg['sources_resolved']['staging']['db_server'] == 'SHARED'
    assert cfg['sources_resolved']['staging']['db_database'] == 'SharedDB'


def test_load_config_db_keys_default_empty(tmp_path):
    (tmp_path / 'src').mkdir()
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(tmp_path / 'src'),
        'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'),
    }), encoding='utf-8')
    cfg = sync_mod.load_config(cfg_path)
    assert cfg['sources_resolved']['']['db_server'] == ''
    assert cfg['sources_resolved']['']['db_database'] == ''


def test_open_group_db_absent_config_is_none():
    assert sync_mod.open_group_db({'db_server': '', 'db_database': ''}) is None
    assert sync_mod.open_group_db({}) is None


def test_open_group_db_connect_failure_is_none_not_fatal(monkeypatch):
    from dw_compare import dbsource

    class Dead:
        last_error = 'Server not found or not reachable'

        def __init__(self, **kw):
            assert kw['trusted'] is True      # integrated auth only
            assert 'password' not in kw or not kw.get('password')

        def connect(self):
            return False

    monkeypatch.setattr(dbsource, 'DwDatabase', Dead)
    assert sync_mod.open_group_db({'db_server': 'S', 'db_database': 'D'}) is None


def test_resolve_names_without_db_is_none_triple():
    assert sync_mod._resolve_names(None, object()) == (None, None, None)
