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


# ---------- hand-edited settings robustness ----------

def test_setting_helpers_treat_wrong_types_as_absent():
    junk = {'s': ['not', 'a', 'string'], 'l': 'not-a-list', 'n': 7}
    assert gui_mod._setting_str(junk, 's') == ''
    assert gui_mod._setting_str(junk, 'n') == ''
    assert gui_mod._setting_str(junk, 'missing') == ''
    assert gui_mod._setting_list(junk, 'l') == []
    assert gui_mod._setting_list(junk, 'missing') == []
    assert gui_mod._setting_list({'l': ['a']}, 'l') == ['a']


def test_hand_edited_junk_settings_do_not_crash_startup():
    # ~/.projxdiff is the documented hand-edit surface (the enable_db
    # flag), so valid-JSON-wrong-type values must degrade, never crash.
    # REGRESSION: "last_dirs" as a string made dict() raise ValueError
    # and the app died before the window appeared.
    gui_mod._SETTINGS_PATH.write_text(json.dumps({
        'enable_db': 'yes',
        'last_dirs': 'C:/somewhere',
        'old_db_server': 123,
        'new_db_server': ['x'],
        'db_windows_auth': 'banana',
        'db_user': {'nested': True},
        'schedule_offered': 'not-a-list',
        'last_sync_config': ['not', 'a', 'string'],
    }), encoding='utf-8')
    root, app = _make_app()
    try:
        assert app._last_dirs == {}
        assert app.old_db_server.get() == ''
        assert app.db_user.get() == ''
        assert app.db_windows_auth.get() is True  # truthy junk -> bool()
        assert app.db_enabled is True             # truthy flag still enables
    finally:
        root.destroy()


def test_junk_last_dirs_values_are_filtered_not_fatal():
    gui_mod._save_setting('last_dirs', {'old': '/good/dir', 'new': 42})
    root, app = _make_app()
    try:
        assert app._last_dirs == {'old': '/good/dir'}
    finally:
        root.destroy()


# ---------- human-facing links point at the download page ----------

def _dialog_texts(root):
    import tkinter as tk
    texts = []

    def walk(widget):
        for child in widget.winfo_children():
            try:
                texts.append(str(child.cget('text')))
            except tk.TclError:
                pass
            walk(child)

    for top in root.winfo_children():
        if isinstance(top, tk.Toplevel):
            walk(top)
    return texts


@pytest.mark.parametrize('opener', ['_show_about', '_show_help'])
def test_dialog_links_go_to_download_page_not_github(opener):
    # The site is the front door; GitHub is plumbing. A refactor that
    # points users back at the repo should fail loudly here.
    from dw_compare.update_check import DOWNLOAD_PAGE
    root, app = _make_app()
    try:
        getattr(app, opener)()
        texts = _dialog_texts(root)
        assert any(DOWNLOAD_PAGE in t for t in texts)
        assert not any('github.com' in t for t in texts)
    finally:
        root.destroy()


# ---------- compare kickoff: what gets remembered (and what never is) ----------

def test_compare_saves_db_setup_but_never_the_password(tmp_path):
    # dbsource.py's NO STORED CREDENTIALS rule, enforced at the GUI seam:
    # the one-time DB setup is remembered, the password must never touch
    # disk in any form.
    gui_mod._save_setting('enable_db', True)
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    root, app = _make_app()
    try:
        app._run_compare = lambda *a, **k: None  # no real compare
        app.old_path.set(str(tmp_path / 'a'))
        app.new_path.set(str(tmp_path / 'b'))
        app.old_db_server.set('SQLBOX\\DW')
        app.old_db_database.set('DWGroup')
        app.db_user.set('reader')
        app.db_password.set('hunter2')
        app._on_compare()
        app._worker.join(timeout=5)

        raw = gui_mod._SETTINGS_PATH.read_text(encoding='utf-8')
        settings = gui_mod._load_settings()
        assert settings['old_db_server'] == 'SQLBOX\\DW'
        assert settings['db_user'] == 'reader'
        assert 'hunter2' not in raw
        assert 'password' not in raw.lower()
    finally:
        root.destroy()


def test_compare_with_flag_off_leaves_saved_db_values_untouched(tmp_path):
    # Flag off = no panel to read: a compare must not blank out the DB
    # setup a previously-enabled machine had saved.
    gui_mod._save_setting('old_db_server', 'KEEP\\ME')
    gui_mod._save_setting('enable_db', False)
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    root, app = _make_app()
    try:
        captured = {}
        app._run_compare = lambda old, new, output, open_browser, db=None: captured.update(db=db)
        app.old_path.set(str(tmp_path / 'a'))
        app.new_path.set(str(tmp_path / 'b'))
        app._on_compare()
        app._worker.join(timeout=5)

        assert captured['db'] is None
        assert gui_mod._load_settings()['old_db_server'] == 'KEEP\\ME'
    finally:
        root.destroy()


# ---------- per-side database logins ----------

def _visible(widget):
    return bool(widget.grid_info())


def test_old_side_login_hidden_until_checked_and_labels_flip():
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        assert not _visible(app.old_db_user_entry)
        assert app.db_user_label.cget('text') == 'SQL username:'

        app.db_diff_creds.set(True)
        app._apply_windows_auth_visibility()
        assert _visible(app.old_db_user_entry)
        assert _visible(app.old_db_pass_entry)
        assert app.db_user_label.cget('text') == 'New DB username:'
        # Both password entries are masked.
        assert app.db_pass_entry.cget('show') == '*'
        assert app.old_db_pass_entry.cget('show') == '*'

        # Windows auth hides every SQL-login widget, including the pair.
        app.db_windows_auth.set(True)
        app._apply_windows_auth_visibility()
        assert not _visible(app.db_user_entry)
        assert not _visible(app.db_diff_creds_check)
        assert not _visible(app.old_db_user_entry)
    finally:
        root.destroy()


def test_neither_password_is_ever_saved(tmp_path):
    gui_mod._save_setting('enable_db', True)
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    root, app = _make_app()
    try:
        app._run_compare = lambda *a, **k: None
        app.old_path.set(str(tmp_path / 'a'))
        app.new_path.set(str(tmp_path / 'b'))
        app.db_diff_creds.set(True)
        app.db_user.set('new-reader')
        app.db_password.set('new-secret')
        app.old_db_user.set('old-reader')
        app.old_db_password.set('old-secret')
        app._on_compare()
        app._worker.join(timeout=5)

        raw = gui_mod._SETTINGS_PATH.read_text(encoding='utf-8')
        settings = gui_mod._load_settings()
        assert settings['db_user'] == 'new-reader'
        assert settings['old_db_user'] == 'old-reader'
        assert settings['db_diff_creds'] is True
        assert 'new-secret' not in raw and 'old-secret' not in raw
        assert 'password' not in raw.lower()
    finally:
        root.destroy()


def test_run_compare_routes_the_old_side_login(tmp_path, monkeypatch):
    calls = {}

    def fake_resolve(side, server, database, index, user='', password='', sql_auth=False):
        calls[side] = (user, password)
        return {}, {}, {}, None

    monkeypatch.setattr(gui_mod, 'resolve_db_names', fake_resolve)
    _write_projx(tmp_path / 'old.driveprojx')
    _write_projx(tmp_path / 'new.driveprojx')
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        db = {'old_server': 'S1', 'old_database': 'D1',
              'new_server': 'S2', 'new_database': 'D2',
              'windows_auth': False,
              'user': 'new-reader', 'password': 'new-secret',
              'diff_creds': True,
              'old_user': 'old-reader', 'old_password': 'old-secret'}
        app._run_compare(tmp_path / 'old.driveprojx', tmp_path / 'new.driveprojx',
                         tmp_path / 'out.html', open_browser=False, db=db)
        assert calls['old'] == ('old-reader', 'old-secret')
        assert calls['new'] == ('new-reader', 'new-secret')

        # Checkbox off: the shared login applies to both sides.
        calls.clear()
        db['diff_creds'] = False
        app._run_compare(tmp_path / 'old.driveprojx', tmp_path / 'new.driveprojx',
                         tmp_path / 'out.html', open_browser=False, db=db)
        assert calls['old'] == calls['new'] == ('new-reader', 'new-secret')
    finally:
        root.destroy()
