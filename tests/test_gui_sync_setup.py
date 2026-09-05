"""Tests for the GUI sync-setup flow: settings persistence, the
empty-site-tolerant config loader behind Tools > Manage Nightly Sync, and
the add-environment-group path (real Tk widgets where a display exists,
matching test_census.py's GUI test)."""

import json
import sys
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


# ---------- the pane layout, test-connection, and path display ----------

def test_pane_layout_and_min_width():
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        assert app.old_pane.grid_info()['column'] == 0   # old = left
        assert app.new_pane.grid_info()['column'] == 1   # new = right
        assert hasattr(app, 'old_db_section') and hasattr(app, 'new_db_section')
        root.update_idletasks()
        assert root.minsize() == (640, 340)
    finally:
        root.destroy()


class _FakeDb:
    """Stands in for dbsource.DwDatabase in test-connection tests."""
    calls = []

    def __init__(self, **kw):
        type(self).calls.append(kw)
        self.last_error = '' if kw['server'] == 'GOOD' else 'Login failed — check the username and password.'

    def connect(self):
        return self.calls[-1]['server'] == 'GOOD'

    def close(self):
        pass


def _run_test_connection(root, app, side):
    import time
    t = app._test_db_connection(side)
    status = getattr(app, f'{side}_test_status')
    if t is not None:
        t.join(timeout=5)
        for _ in range(50):  # pump until the main-thread poll delivers
            root.update()
            if status.cget('text') != 'Connecting…':
                break
            time.sleep(0.05)
    return status


def test_test_connection_success_and_failure(monkeypatch):
    from dw_compare import dbsource
    monkeypatch.setattr(dbsource, 'DwDatabase', _FakeDb)
    _FakeDb.calls = []
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        app.old_db_server.set('GOOD')
        app.old_db_database.set('DWGroup')
        status = _run_test_connection(root, app, 'old')
        assert 'Connected — DWGroup on GOOD' in status.cget('text')

        app.new_db_server.set('BAD')
        app.new_db_database.set('DWGroup')
        status = _run_test_connection(root, app, 'new')
        assert 'Login failed' in status.cget('text')
        # str(): a ttk widget (the Windows button factory) answers cget with a
        # Tcl object, not a str, so a bare == silently fails there only.
        assert str(app.new_test_btn.cget('state')) == 'normal'
    finally:
        root.destroy()


def test_test_connection_requires_server_and_database():
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        assert app._test_db_connection('old') is None
        assert 'server and database' in app.old_test_status.cget('text')
    finally:
        root.destroy()


def test_test_connection_routes_old_side_login(monkeypatch):
    from dw_compare import dbsource
    monkeypatch.setattr(dbsource, 'DwDatabase', _FakeDb)
    _FakeDb.calls = []
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        app.old_db_server.set('GOOD')
        app.old_db_database.set('DWGroup')
        app.db_user.set('shared-user')
        app.db_password.set('shared-pw')
        app.old_db_user.set('old-user')
        app.old_db_password.set('old-pw')

        app.db_diff_creds.set(True)
        _run_test_connection(root, app, 'old')
        assert _FakeDb.calls[-1]['user'] == 'old-user'
        assert _FakeDb.calls[-1]['password'] == 'old-pw'

        app.db_diff_creds.set(False)
        _run_test_connection(root, app, 'old')
        assert _FakeDb.calls[-1]['user'] == 'shared-user'
    finally:
        root.destroy()


def test_ellipsize_middle_keeps_root_and_filename():
    import tkinter as tk
    from tkinter import font as tkfont
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available')
    try:
        f = tkfont.Font(family='Helvetica', size=12)
        full = r'C:\DriveWorksFiles\Production\Conveyors\2026\Conveyor RX (rev C).driveprojx'
        out = gui_mod._ellipsize_middle(full, f, 260)
        assert out != full and '…' in out
        assert out.startswith('C:')
        assert out.endswith('.driveprojx')
        assert f.measure(out) <= 260
        # Plenty of room -> untouched.
        assert gui_mod._ellipsize_middle('short', f, 500) == 'short'
    finally:
        root.destroy()


def test_path_display_ellipsizes_but_variable_keeps_full_value():
    root, app = _make_app()
    try:
        full = r'C:\Some\Extremely\Deep\Folder\Tree\That\Never\Ends\Project.driveprojx'
        app.old_path.set(full)
        root.update_idletasks()
        disp, var = app._path_entries[app.old_entry]
        assert var.get() == full            # the real value is untruncated
        assert app.old_entry.get() != ''    # something is displayed
    finally:
        root.destroy()


# ---------- native (ttk) widget mode and the server finder ----------

def test_native_widget_mode_builds_and_toggles(monkeypatch):
    # Windows runs this path for real; here the vista theme is absent so
    # the style falls back, but construction and visibility must work.
    from tkinter import ttk
    monkeypatch.setattr(gui_mod, '_NATIVE_WIDGETS', True)
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        assert isinstance(app.out_entry, ttk.Entry)
        assert isinstance(app.old_test_btn, ttk.Button)
        assert isinstance(app.old_server_combo, ttk.Combobox)
        assert not hasattr(app, 'old_find_btn')  # combobox replaces the button
        assert isinstance(app.db_diff_creds_check, ttk.Checkbutton)
        assert isinstance(app.db_pass_entry, ttk.Entry)
        assert app.db_pass_entry.cget('show') == '*'
        app.db_diff_creds.set(True)
        app.db_windows_auth.set(True)
        app._apply_windows_auth_visibility()   # must not raise on ttk widgets
        app.old_test_btn.configure(state='disabled')
        app.old_test_btn.configure(state='normal')
    finally:
        root.destroy()


def test_find_servers_populates_picker(monkeypatch):
    """The classic-Tk picker: a '▾' button posting a tk.Menu. Windows builds a
    real ttk.Combobox instead and has no find button at all (see
    test_native_server_combobox_lists_discovered), so pin the flag rather than
    letting the platform decide which path this exercises."""
    from dw_compare import dbsource
    monkeypatch.setattr(gui_mod, '_NATIVE_WIDGETS', False)
    monkeypatch.setattr(dbsource, 'discover_servers', lambda timeout=2.0: [
        {'server': 'KEES-DB', 'host': 'KEES-DB', 'instance': '', 'version': '15.0.4043.16'},
        {'server': 'KEES-DB\\STAGING', 'host': 'KEES-DB', 'instance': 'STAGING', 'version': ''},
    ])
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        import time
        t = app._find_servers('old')
        t.join(timeout=5)
        for _ in range(50):
            root.update()
            if str(app.old_find_btn.cget('state')) != 'disabled':
                break
            time.sleep(0.05)
        menu = app._servers_menu
        assert 'KEES-DB' in menu.entrycget(0, 'label')
        menu.invoke(1)   # pick the named instance
        assert app.old_db_server.get() == 'KEES-DB\\STAGING'
    finally:
        root.destroy()


def test_find_servers_empty_network_reports_gently(monkeypatch):
    from dw_compare import dbsource
    monkeypatch.setattr(gui_mod, '_NATIVE_WIDGETS', False)   # classic-Tk picker
    monkeypatch.setattr(dbsource, 'discover_servers', lambda timeout=2.0: [])
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        import time
        t = app._find_servers('new')
        t.join(timeout=5)
        for _ in range(50):
            root.update()
            if 'Scanning' not in app.new_test_status.cget('text'):
                break
            time.sleep(0.05)
        assert 'No SQL Servers announced themselves' in app.new_test_status.cget('text')
    finally:
        root.destroy()


def test_native_server_combobox_lists_discovered(monkeypatch):
    from tkinter import ttk
    from dw_compare import dbsource
    monkeypatch.setattr(gui_mod, '_NATIVE_WIDGETS', True)
    monkeypatch.setattr(dbsource, 'discover_servers', lambda timeout=2.0: [
        {'server': 'KEES-DB', 'host': 'KEES-DB', 'instance': '', 'version': '15.0'},
        {'server': 'KEES-DB\\STAGING', 'host': 'KEES-DB', 'instance': 'STAGING', 'version': ''},
    ])
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        assert isinstance(app.old_server_combo, ttk.Combobox)
        # First open may race the prefetch; wait for the scan, then repopulate.
        if app._server_scan is not None:
            app._server_scan.join(timeout=5)
        app._populate_server_combo('old')
        values = list(app.old_server_combo['values'])
        assert 'KEES-DB' in values and 'KEES-DB\\STAGING' in values
        # Both sides share one cache and one scan.
        app._populate_server_combo('new')
        assert 'KEES-DB' in list(app.new_server_combo['values'])
    finally:
        root.destroy()


def test_help_mentions_db_only_when_enabled():
    root, app = _make_app()   # flag off
    try:
        app._show_help()
        texts = _dialog_texts(root)
        assert not any('Database name resolution' in t for t in texts)
    finally:
        root.destroy()
    gui_mod._save_setting('enable_db', True)
    root, app = _make_app()
    try:
        app._show_help()
        texts = _dialog_texts(root)
        assert any('Database name resolution' in t for t in texts)
        assert any('Passwords are never saved' in t for t in texts)
    finally:
        root.destroy()


# ---------- scheduled-task repair ----------

def test_repair_task_line_quoting():
    line = gui_mod._SyncManager._repair_task_line(
        '"C:\\Program Files\\Projx Diff\\ProjxDiff.exe" --sync "C:\\ProjxArchive\\config.json"',
        '02:00')
    assert line.startswith('schtasks /Create /F /SC DAILY /TN "ProjxDiff Nightly Sync" '
                           '/ST 02:00 /RU SYSTEM /RL HIGHEST /TR "')
    # Inner quotes escaped the way schtasks /TR requires.
    assert '\\"C:\\Program Files\\Projx Diff\\ProjxDiff.exe\\" --sync' in line
    assert line.endswith('\\"C:\\ProjxArchive\\config.json\\""')


def test_register_task_elevated_is_windows_only(monkeypatch):
    """The guard itself, pinned on every platform. Without the forced platform
    this passed only where it was vacuous: on Windows the guard let the call
    through to the schtasks line below — see the next test for what that cost."""
    monkeypatch.setattr(gui_mod.sys, 'platform', 'darwin')
    mgr = SimpleNamespace(_sync_command=lambda: 'x',
                          _repair_task_line=gui_mod._SyncManager._repair_task_line)
    ok, msg = gui_mod._SyncManager._register_task_elevated(mgr, '02:00')
    assert ok is False
    assert 'Windows' in msg


def test_register_task_refuses_a_command_that_is_not_this_executable(monkeypatch):
    """REGRESSION: the schtasks line hardcodes the production task name and
    runs with /F, so anything reaching it rewrites the real nightly task with
    whatever command it was handed — and a junk command does not fail loudly,
    it quietly replaces a working task with a broken one.

    This is not hypothetical. The Windows-only failure of the test above ran
    exactly that path on every Windows CI run, and on a deployed box it
    re-registered the client's live nightly sync to run the command 'x',
    costing three nights of archiving before anyone noticed. Nothing must
    reach schtasks without the command naming this executable."""
    monkeypatch.setattr(gui_mod.sys, 'platform', 'win32')

    def explode(*a, **k):                      # nothing may shell out
        raise AssertionError('schtasks/PowerShell must not be reached')
    monkeypatch.setattr(gui_mod.subprocess, 'run', explode)

    mgr = SimpleNamespace(_sync_command=lambda: 'x',
                          _command_is_ours=gui_mod._SyncManager._command_is_ours,
                          _repair_task_line=gui_mod._SyncManager._repair_task_line)
    ok, msg = gui_mod._SyncManager._register_task_elevated(mgr, '02:00')
    assert ok is False
    assert 'Refusing to re-register' in msg


def test_register_task_accepts_the_real_sync_command(monkeypatch):
    """The guard must not block the actual repair, which is the whole feature.
    Stops at the shell-out — running it for real wants a UAC prompt."""
    monkeypatch.setattr(gui_mod.sys, 'platform', 'win32')
    reached = {}

    def fake_run(cmd, *a, **k):
        reached['cmd'] = cmd
        raise RuntimeError('stop here')        # caught as a launch failure
    monkeypatch.setattr(gui_mod.subprocess, 'run', fake_run)

    real = f'"{sys.executable}" --sync "C:\\ProjxArchive\\config.json"'
    mgr = SimpleNamespace(_sync_command=lambda: real,
                          _command_is_ours=gui_mod._SyncManager._command_is_ours,
                          _repair_task_line=gui_mod._SyncManager._repair_task_line)
    ok, msg = gui_mod._SyncManager._register_task_elevated(mgr, '02:00')
    assert ok is False
    assert 'Could not launch' in msg           # got past the guard, not blocked
    assert 'powershell' in reached['cmd'][0]


def test_filter_change_scrolls_back_to_top(tmp_path):
    """REGRESSION: filtering re-grids from row 0, so a viewport left
    scrolled down showed blank space under a short result set — which
    reads as 'the filter found nothing'."""
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available')
    root.withdraw()
    try:
        from dw_compare import census as census_mod
        cfg_path = sync_mod.init_site(tmp_path / 'site')
        src = tmp_path / 'Projects'
        for i in range(25):
            _write_projx(src / f'Proj{i:02d}.driveprojx')
        cfg = gui_mod._load_manager_config(cfg_path)
        cpath = census_mod.census_path(cfg)
        mgr = gui_mod._SyncManager(root, cfg, cpath,
                                   census_mod.load_census(cpath), config_path=cfg_path)
        mgr._apply_add_group('prod', str(src))
        mgr.proj_vars['prod/Proj00'].set('Track')
        root.update_idletasks()

        mgr._canvas.yview_moveto(1.0)     # user scrolled to the bottom
        root.update_idletasks()
        assert mgr._canvas.yview()[0] > 0.0

        mgr._set_disp_filter('Track')      # one row survives
        root.update_idletasks()
        assert mgr._canvas.yview()[0] == 0.0   # snapped back into view
        shown = [r for r in mgr._rows if r['cells'][0].grid_info()]
        assert [r['title'] for r in shown] == ['Proj00']
    finally:
        root.destroy()


def test_plain_registration_refuses_a_foreign_command_too(monkeypatch):
    """The non-elevated 'Run Nightly' path writes the same schtasks /Create /F
    against the same hardcoded task name, so it needs the same refusal — it
    just isn't elevated. Guarding only the repair path would leave the class
    of bug open on the other half."""
    monkeypatch.setattr(gui_mod.sys, 'platform', 'win32')
    assert gui_mod._SyncManager._command_is_ours('x') is False
    assert gui_mod._SyncManager._command_is_ours(
        f'"{sys.executable}" --sync "C:\\ProjxArchive\\config.json"') is True
    assert gui_mod._SyncManager._command_is_ours('') is False
    assert gui_mod._SyncManager._command_is_ours(None) is False


def test_help_explains_the_nightly_archive_and_triage():
    """The nightly half is the part people need explained: what the archive
    actually is, what a run does, and what New/Track/Ignore mean."""
    root, app = _make_app()
    try:
        app._show_help()
        blob = ' '.join(_dialog_texts(root))
        # The repo idea — the archive is a git repo they own, not a black box.
        assert 'git repository' in blob
        assert 'authored to the DriveWorks user who last saved' in blob
        # How a run works, including the cases that are not a plain diff.
        assert 'Unchanged projects do nothing at all' in blob
        assert 'Rebuilt' in blob and 're-baselined' in blob
        assert '.~driveproj' in blob
        # Triage vocabulary must match the manager's own labels.
        assert 'All / New / Track / Ignore' in blob
        assert 'heals past metrics retroactively' in blob
        # The windowed-exe gotcha (#9) — documented until it is fixed.
        assert 'prints nothing to the console' in blob
    finally:
        root.destroy()


def test_help_scrolls_instead_of_growing_off_screen():
    """REGRESSION GUARD: the dialog sizes itself to its content and is
    centred, so an uncapped help runs off the top and bottom of a laptop
    screen with the Close button out of reach."""
    import tkinter as tk
    gui_mod._save_setting('enable_db', True)      # longest variant
    root, app = _make_app()
    try:
        app._show_help()
        top = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        canvases = []

        def walk(w):
            for child in w.winfo_children():
                if isinstance(child, tk.Canvas):
                    canvases.append(child)
                walk(child)
        walk(top)

        assert canvases, 'help body is not scrollable'
        canvas = canvases[0]
        top.update_idletasks()
        assert int(canvas.cget('height')) <= app._HELP_MAX_BODY_H
        # And the other axis: a Canvas does not propagate its content's
        # requested width, so the dialog shrinks and clips the text unless
        # the width is set explicitly — there is no horizontal scrolling.
        inner = canvas.winfo_children()[0]
        assert top.winfo_reqwidth() >= inner.winfo_reqwidth(), 'help text is clipped'
    finally:
        root.destroy()


# ---------- compare failure surfaces the log, not a modal ----------

def test_compare_failure_reveals_log_without_modal(tmp_path, monkeypatch):
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available')
    root.withdraw()
    try:
        app = gui_mod.CompareApp(root)
        # A modal here would block a headless run and hide the details behind
        # an extra click; the failure path must never open one.
        monkeypatch.setattr(gui_mod.messagebox, 'showerror',
                            lambda *a, **k: pytest.fail('failure path opened a modal'))
        assert not app.show_log.get()
        app._on_done(error='synthetic parse failure')
        assert app.show_log.get()               # log pane revealed
        assert 'failed' in app.status_label.cget('text')
    finally:
        root.destroy()
