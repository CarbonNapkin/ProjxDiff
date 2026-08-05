"""Tests for the GUI sync-setup flow: settings persistence, the
empty-site-tolerant config loader behind Tools > Manage Nightly Sync, and
the add-environment-group path (real Tk widgets where a display exists,
matching test_census.py's GUI test)."""

import json
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from dw_compare import sync as sync_mod

pytest.importorskip('tkinter')
from dw_compare import gui as gui_mod  # noqa: E402  (needs tkinter present)


def _write_projx(path: Path, saver='Jane'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('driveProj/project.xml',
                    '<Project><Variables><Variable DisplayName="W" StoreName="W" Rule="=1"/></Variables></Project>')
        zf.writestr('driveProj/designMaster.xml',
                    f'<DesignMaster><SpecialVariable StoreName="DWCurrentUserDisplayName" Value="{saver}" /></DesignMaster>')


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_mod, '_SETTINGS_PATH', tmp_path / '.projxdiff')


# ---------- settings ----------

def test_settings_roundtrip():
    assert gui_mod._load_settings() == {}
    gui_mod._save_setting('last_sync_config', 'C:/ProjxArchive/config.json')
    gui_mod._save_setting('schedule_offered', ['a'])
    settings = gui_mod._load_settings()
    assert settings['last_sync_config'] == 'C:/ProjxArchive/config.json'
    assert settings['schedule_offered'] == ['a']


def test_settings_survive_corruption(tmp_path):
    gui_mod._SETTINGS_PATH.write_text('not json', encoding='utf-8')
    assert gui_mod._load_settings() == {}
    gui_mod._save_setting('k', 'v')  # must not raise
    assert gui_mod._load_settings() == {'k': 'v'}


# ---------- manager config loading ----------

def test_load_manager_config_accepts_groupless_fresh_site(tmp_path):
    cfg_path = sync_mod.init_site(tmp_path / 'site')
    cfg = gui_mod._load_manager_config(cfg_path)
    assert cfg['sources_resolved'] == {}
    assert cfg['data_dir'] == tmp_path / 'site' / 'data'
    # The sync-facing loader stays strict about the very same file.
    with pytest.raises(SystemExit):
        sync_mod.load_config(cfg_path)


def test_load_manager_config_delegates_once_groups_exist(tmp_path):
    cfg_path = sync_mod.init_site(tmp_path / 'site')
    src = tmp_path / 'src'
    src.mkdir()
    sync_mod.add_source(cfg_path, 'prod', src)
    cfg = gui_mod._load_manager_config(cfg_path)
    assert set(cfg['sources_resolved']) == {'prod'}


def test_load_manager_config_missing_file_keeps_human_message(tmp_path):
    with pytest.raises(SystemExit, match='--init-config'):
        gui_mod._load_manager_config(tmp_path / 'nope.json')


# ---------- scheduled-task command ----------

def test_sync_command_dev_form():
    mgr = SimpleNamespace(config_path='C:/ProjxArchive/config.json')
    cmd = gui_mod._SyncManager._sync_command(mgr)
    assert '-m dw_compare --sync' in cmd
    assert 'C:/ProjxArchive/config.json' in cmd


# ---------- the add-group flow, on real widgets ----------

def test_manager_add_group_scans_refreshes_and_keeps_edits(tmp_path):
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available')
    root.withdraw()
    try:
        from dw_compare import census as census_mod
        cfg_path = sync_mod.init_site(tmp_path / 'site')
        src = tmp_path / 'DaytonProjects'
        _write_projx(src / 'Roof Curb.driveprojx', saver='Jane')

        cfg = gui_mod._load_manager_config(cfg_path)
        cpath = census_mod.census_path(cfg)
        census = census_mod.load_census(cpath)
        mgr = gui_mod._SyncManager(root, cfg, cpath, census, config_path=cfg_path)
        assert mgr._rows == []  # fresh site opens on the empty state

        slug, summary = mgr._apply_add_group('Dayton plant', str(src))
        assert slug == 'Dayton-plant'
        assert summary['pending'] == [('Dayton-plant/Roof Curb', 'Roof Curb.driveprojx')]
        assert summary['unmapped'] == ['Jane']

        # Window rebuilt in place: group column, the discovered row, the user.
        assert 'Group' in mgr._cols
        assert list(mgr.proj_vars) == ['Dayton-plant/Roof Curb']
        assert mgr._rows[0]['group'] == 'Dayton-plant'
        assert mgr._rows[0]['title'] == 'Roof Curb'
        assert 'Jane' in mgr.user_entries

        # Discoveries persisted immediately (before any Save click) and the
        # config grew the group with an auto-placed archive.
        saved = json.loads(cpath.read_text(encoding='utf-8'))
        assert saved['projects']['Dayton-plant/Roof Curb']['disposition'] == 'pending'
        raw = json.loads(cfg_path.read_text(encoding='utf-8'))
        assert raw['sources']['Dayton-plant']['archive_repo'].endswith('repos/Dayton-plant')

        # Engine errors surface as SystemExit with their human message.
        with pytest.raises(SystemExit, match='already exists'):
            mgr._apply_add_group('Dayton plant', str(src))

        # In-progress triage survives adding another group.
        mgr.proj_vars['Dayton-plant/Roof Curb'].set('Track')
        mgr.user_entries['Jane'].insert(0, 'Jane Doe <j@x.com>')
        src2 = tmp_path / 'StagingProjects'
        _write_projx(src2 / 'Other.driveprojx', saver='Jane')
        mgr._apply_add_group('staging', str(src2))
        assert set(mgr.proj_vars) == {'Dayton-plant/Roof Curb', 'staging/Other'}
        assert mgr.proj_vars['Dayton-plant/Roof Curb'].get() == 'Track'
        assert mgr.user_entries['Jane'].get() == 'Jane Doe <j@x.com>'
    finally:
        root.destroy()


# ---------- the enable_db feature flag ----------

def _make_app():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available')
    root.withdraw()
    return root, gui_mod.CompareApp(root)


def test_db_panel_hidden_by_default():
    root, app = _make_app()
    try:
        assert app.db_enabled is False
        assert not hasattr(app, 'db_frame')
        assert app.show_db.get() is False
        # The visibility helpers must be safe no-ops with no panel built.
        app._apply_db_visibility()
        app._apply_windows_auth_visibility()
    finally:
        root.destroy()


def test_db_panel_enabled_by_config_flag():
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        assert app.db_enabled is True
        assert hasattr(app, 'db_frame')
        assert app.show_db.get() is True
    finally:
        root.destroy()


def test_db_panel_auto_enables_for_legacy_saved_server():
    # Machines that configured a DB server before the flag existed keep
    # their panel without editing ~/.projxdiff.
    gui_mod._save_setting('old_db_server', 'sqlserver\\dw')
    root, app = _make_app()
    try:
        assert app.db_enabled is True
        assert hasattr(app, 'db_frame')
        assert app.old_db_server.get() == 'sqlserver\\dw'
    finally:
        root.destroy()


def test_db_panel_explicit_false_beats_saved_server():
    gui_mod._save_setting('old_db_server', 'sqlserver\\dw')
    gui_mod._save_setting('enable_db', False)
    root, app = _make_app()
    try:
        assert app.db_enabled is False
        assert not hasattr(app, 'db_frame')
    finally:
        root.destroy()


def test_first_launch_seeds_settings_file():
    # A fresh install must leave an editable file behind: enabling the DB
    # panel is "flip false to true", not "create a bare dotfile by hand".
    assert not gui_mod._SETTINGS_PATH.exists()
    root, app = _make_app()
    try:
        assert gui_mod._load_settings() == {'enable_db': False}
    finally:
        root.destroy()


def test_first_launch_does_not_clobber_existing_settings():
    gui_mod._save_setting('enable_db', True)
    gui_mod._save_setting('db_user', 'reader')
    root, app = _make_app()
    try:
        settings = gui_mod._load_settings()
        assert settings['enable_db'] is True
        assert settings['db_user'] == 'reader'
    finally:
        root.destroy()


def test_last_dirs_persist_across_sessions(tmp_path):
    projx = str(tmp_path / 'Widgets' / 'old.driveprojx')
    root, app = _make_app()
    try:
        app._remember_dir('old', projx)
    finally:
        root.destroy()
    assert gui_mod._load_settings()['last_dirs'] == {'old': str(tmp_path / 'Widgets')}
    root, app = _make_app()
    try:
        assert app._last_dirs == {'old': str(tmp_path / 'Widgets')}
    finally:
        root.destroy()
