"""
Simple Tkinter GUI for Projx Diff (a DriveWorks project comparison tool).

Lets the user pick two .driveprojx projects, choose an output path, and run a
comparison without using the command line.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import Tk, StringVar, BooleanVar, END, DISABLED, NORMAL, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from ._version import __version__, __author__, __url__, __license__
from .parsers import load_project
from .report import generate_html_report
from .update_check import check_for_update, DOWNLOAD_PAGE

try:
    from .__main__ import (resolve_input, cleanup_temp_dirs, resolve_output_path,
                           resolve_db_names)
except ImportError:
    resolve_input = None  # type: ignore
    cleanup_temp_dirs = None  # type: ignore
    resolve_output_path = None  # type: ignore
    resolve_db_names = None  # type: ignore


PROJX_FILETYPES = [('DriveWorks™ project', '*.driveprojx')]
APP_TITLE = f'Projx Diff {__version__}'

# Shared palette for the Manage Nightly Sync window — a flat, light look that
# reads as more modern than raw Tk defaults while staying plain-tk (no ttk, so
# it renders correctly on older macOS Tk too).
_SM_BG = '#f4f5f7'
_SM_CARD = '#ffffff'
_SM_HEADER_BG = '#2b3242'
_SM_HEADER_FG = '#ffffff'
_SM_HEADER_SUB = '#aeb6c6'
_SM_TEXT = '#2b2f36'
_SM_MUTED = '#8a94a6'
_SM_DIVIDER = '#e3e6eb'
_SM_ACCENT = '#2d6cdf'
_SM_ACCENT_ACT = '#245bc0'


def _default_config_dir() -> str:
    """Sensible starting folder for the sync-config file picker: the standard
    deployment path when present, else a per-user ProjxArchive, else home."""
    for candidate in ('C:/ProjxArchive', str(Path.home() / 'ProjxArchive')):
        if Path(candidate).is_dir():
            return candidate
    return str(Path.home())


def _resource_path(rel: str) -> Path:
    """Locate a bundled resource in both dev and PyInstaller-frozen runs.
    Frozen builds unpack datas under sys._MEIPASS; in dev, assets/ sits next to
    the repo root (one level above this package)."""
    base = getattr(sys, '_MEIPASS', None) or Path(__file__).resolve().parent.parent
    return Path(base) / rel


def _set_window_icon(win) -> None:
    """Set the live window/taskbar icon from assets/icon.png. No-op when the
    asset is absent (before branding lands) or the Tk build can't read PNGs
    (older macOS Tk) — the packaged .exe/.app icon is handled by the spec."""
    try:
        png = _resource_path('assets/icon.png')
        if png.is_file():
            img = tk.PhotoImage(file=str(png))
            win.iconphoto(True, img)   # True → default for all toplevels too
            win._icon_ref = img        # keep a reference so Tk doesn't GC it
    except Exception:
        pass


# Tiny per-user settings file (remembers e.g. the last-used sync config so
# Tools > Manage Nightly Sync reopens it without asking again).
_SETTINGS_PATH = Path.home() / '.projxdiff'


def _load_settings() -> dict:
    try:
        return json.loads(_SETTINGS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_setting(key: str, value) -> None:
    settings = _load_settings()
    settings[key] = value
    try:
        _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + '\n',
                                  encoding='utf-8')
    except Exception:
        pass  # settings are a convenience; never fail an action over them


def _load_manager_config(path: Path) -> dict:
    """load_config, except a site config with no groups yet (fresh Create
    new… / --init-config) loads as an empty site so the manager can open and
    offer the first Add-environment-group instead of erroring. load_config
    itself must stay strict: a *sync* against zero groups is meaningless."""
    from .sync import DEFAULTS, load_config
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return load_config(path)  # its error messages are written for humans
    if raw.get('sources') == {} and raw.get('data_dir'):
        cfg = dict(DEFAULTS)
        cfg.update(raw)
        cfg['data_dir'] = Path(cfg['data_dir'])
        cfg['sources_resolved'] = {}
        return cfg
    return load_config(path)


def _styled_dialog(parent, title: str, subtitle: str) -> tuple:
    """Toplevel styled like the rest of the app — dark header bar, light
    body card. Returns (window, body_frame); the caller packs content into
    the body and finishes with its own button bar + _center_over."""
    top = tk.Toplevel(parent)
    top.title(title)
    top.resizable(False, False)
    top.configure(bg=_SM_BG)
    header = tk.Frame(top, bg=_SM_HEADER_BG)
    header.pack(fill='x')
    tk.Label(header, text=title, bg=_SM_HEADER_BG, fg=_SM_HEADER_FG,
             font=('TkDefaultFont', 15, 'bold')).pack(anchor='w', padx=18, pady=(12, 0))
    tk.Label(header, text=subtitle, bg=_SM_HEADER_BG, fg=_SM_HEADER_SUB,
             font=('TkDefaultFont', 9)).pack(anchor='w', padx=18, pady=(1, 12))
    body = tk.Frame(top, bg=_SM_CARD, highlightbackground=_SM_DIVIDER,
                    highlightthickness=1, padx=18, pady=14)
    body.pack(fill='both', expand=True, padx=14, pady=(12, 0))
    return top, body


def _center_over(parent, top) -> None:
    """Center a dialog over its parent window and make it modal."""
    top.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - top.winfo_height()) // 3
    top.geometry(f'+{max(0, x)}+{max(0, y)}')
    top.transient(parent)
    top.grab_set()


class _QueueWriter:
    """File-like object that pushes writes onto a queue."""

    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, s: str) -> int:
        if s:
            self._q.put(s)
        return len(s)

    def flush(self) -> None:
        pass


class CompareApp:
    def __init__(self, root: Tk):
        self.root = root
        root.title(APP_TITLE)
        _set_window_icon(root)
        # Compact by default; the log pane is hidden (View ▸ Show Log) and the
        # window grows to fit it when shown.
        root.geometry('760x392')   # _sync_window_size grows this per panel
        self.show_log = BooleanVar(value=False)
        self.show_db = BooleanVar(value=True)
        self._busy = False
        self._build_menu()

        self.old_path = StringVar()
        self.new_path = StringVar()
        # Default to an absolute path in a writable folder (Downloads/home),
        # shown in full so the user knows where the report lands. A relative
        # default would resolve against cwd, which can be read-only for a
        # double-clicked app ('/' on macOS, System32/Program Files on Windows).
        default_out = str(resolve_output_path('')) if resolve_output_path else 'dw_comparison.html'
        self.output_path = StringVar(value=default_out)
        self.open_in_browser = BooleanVar(value=True)

        # Optional group-database connection for resolving model/rule names
        # (Models and Rule Changes report sections). Old and new projects
        # can live on different SQL servers, so each side has its own
        # server + database. Server/database/auth-mode/username are a
        # one-time-per-user setup, remembered in ~/.projxdiff — the
        # password is the one exception: in-memory only, never written to
        # disk (dbsource.py's "no stored credentials" rule).
        saved = _load_settings()
        self.old_db_server = StringVar(value=saved.get('old_db_server', ''))
        self.new_db_server = StringVar(value=saved.get('new_db_server', ''))
        self.old_db_database = StringVar(value=saved.get('old_db_database', ''))
        self.new_db_database = StringVar(value=saved.get('new_db_database', ''))
        # SQL Server auth is the default (the common case for a group DB);
        # the checkbox switches to Windows integrated auth.
        self.db_windows_auth = BooleanVar(value=saved.get('db_windows_auth', False))
        self.db_user = StringVar(value=saved.get('db_user', ''))
        self.db_password = StringVar()

        # Last folder used per file-picker field ('old' / 'new' / 'output'),
        # so each Browse… remembers its own location instead of sharing
        # Tk's single global one. Session-only.
        self._last_dirs: dict[str, str] = {}

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None

        self._build_ui()
        self._drain_log()
        threading.Thread(target=self._check_updates, daemon=True).start()

    def _build_menu(self) -> None:
        """Standard menubar with Help. Integrates with the macOS global menu
        automatically. The File menu carries only Quit so the keyboard
        shortcut shows up where users expect it."""
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label='Quit', accelerator='Cmd+Q' if sys.platform == 'darwin' else 'Ctrl+Q',
                              command=self.root.destroy)
        menubar.add_cascade(label='File', menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_checkbutton(label='Show Log', variable=self.show_log,
                                  command=self._apply_log_visibility)
        view_menu.add_checkbutton(label='Show Database Options', variable=self.show_db,
                                  command=self._apply_db_visibility)
        menubar.add_cascade(label='View', menu=view_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(label='Manage Nightly Sync…', command=self._manage_sync)
        menubar.add_cascade(label='Tools', menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=False, name='help')
        help_menu.add_command(label='How to Use', command=self._show_help)
        help_menu.add_separator()
        help_menu.add_command(label='About Projx Diff', command=self._show_about)
        menubar.add_cascade(label='Help', menu=help_menu)

        self.root.config(menu=menubar)

    def _dialog(self, title: str, subtitle: str) -> tuple:
        """Toplevel styled like the rest of the app — dark header bar, light
        body card. Returns (window, body_frame); the caller packs content
        into the body and finishes with _finish_dialog."""
        return _styled_dialog(self.root, title, subtitle)

    def _finish_dialog(self, top) -> None:
        """Accent Close button, centered placement, modal grab."""
        bar = tk.Frame(top, bg=_SM_BG)
        bar.pack(fill='x', pady=(8, 12))
        tk.Button(bar, text='Close', command=top.destroy, bg=_SM_ACCENT, fg='#ffffff',
                  activebackground=_SM_ACCENT_ACT, activeforeground='#ffffff',
                  relief='flat', padx=18, pady=4, cursor='hand2',
                  font=('TkDefaultFont', 10, 'bold')).pack(side='right', padx=14)
        _center_over(self.root, top)

    def _show_help(self) -> None:
        """Concise in-app usage help covering both halves of the app: the
        one-off comparison and the nightly sync / dashboard pipeline."""
        top, body = self._dialog('How to Use', 'Two tools in one window')

        def heading(text, first=False):
            tk.Label(body, text=text, bg=_SM_CARD, fg=_SM_TEXT, anchor='w',
                     font=('TkDefaultFont', 11, 'bold')).pack(
                fill='x', pady=((0 if first else 10), 2))

        def para(text):
            tk.Label(body, text=text, bg=_SM_CARD, fg=_SM_TEXT, justify='left',
                     anchor='w', wraplength=520).pack(fill='x')

        heading('Compare two projects', first=True)
        para('1.  Pick the baseline and the newer .driveprojx with Browse…\n'
             '2.  Choose where the HTML report lands (defaults to Downloads).\n'
             '3.  Click Compare — the report opens in your browser.\n'
             'The report groups every added / removed / modified element by '
             'section — variables, tables, tasks, documents, macros, navigation, '
             'and form rules — with search and status filters on top.')

        heading('Track your library nightly')
        para('Tools ▸ Manage Nightly Sync sets up automatic tracking: each '
             'environment group (production, staging, …) is archived into '
             'version control every night, changes are measured per project and '
             'per user, and a work dashboard is generated. New projects and '
             'unfamiliar DriveWorks user names show up there for one-click '
             'triage. Command-line equivalents: --sync, --census, --dashboard.')

        heading('Private by design')
        para('Everything runs locally; your project files never leave your '
             'machines.')

        link = tk.Label(body, text='More at ' + __url__, bg=_SM_CARD, fg=_SM_ACCENT,
                        cursor='hand2', anchor='w')
        link.pack(fill='x', pady=(10, 0))
        link.bind('<Button-1>', lambda _e: webbrowser.open(__url__))

        self._finish_dialog(top)

    def _manage_sync(self) -> None:
        """Tools > Manage Nightly Sync. Returning users go straight to the
        manager on their remembered config; everyone else gets a chooser:
        open an existing config or create a new site (one folder question,
        no JSON authored by humans)."""
        last = _load_settings().get('last_sync_config', '')
        if last and Path(last).is_file() and self._open_sync_manager(Path(last), quiet=True):
            return
        self._sync_chooser()

    def _sync_chooser(self) -> None:
        top, body = self._dialog('Manage Nightly Sync',
                                 'Track your project library automatically')

        tk.Label(body, text='Projx Diff archives every environment group nightly, '
                            'measures what changed per project and per user, and '
                            'builds a team dashboard. Everything lives in one '
                            'folder you pick once.',
                 bg=_SM_CARD, fg=_SM_TEXT, justify='left', anchor='w',
                 wraplength=460).pack(fill='x', pady=(0, 14))

        def choice(text, sub, cmd, accent):
            tk.Button(body, text=text, command=cmd, relief='flat', cursor='hand2',
                      bg=_SM_ACCENT if accent else '#e6e8ec',
                      fg='#ffffff' if accent else _SM_TEXT,
                      activebackground=_SM_ACCENT_ACT if accent else '#dcdfe4',
                      activeforeground='#ffffff' if accent else _SM_TEXT,
                      font=('TkDefaultFont', 10, 'bold'),
                      padx=14, pady=5).pack(fill='x')
            tk.Label(body, text=sub, bg=_SM_CARD, fg=_SM_MUTED, anchor='w',
                     wraplength=460, justify='left').pack(fill='x', pady=(2, 10))

        choice('Create new…',
               'First time here — pick one folder where Projx Diff keeps its '
               'config, archives, and dashboard.',
               lambda: (top.destroy(), self._sync_create_new()), accent=True)
        choice('Open existing config…',
               'You already have a config.json from a previous setup or '
               'another machine.',
               lambda: (top.destroy(), self._sync_open_existing()), accent=False)

        self._finish_dialog(top)

    def _sync_open_existing(self) -> None:
        cfg_path = filedialog.askopenfilename(
            title='Select the nightly sync config',
            initialdir=_default_config_dir(),
            filetypes=[('Sync config (JSON)', '*.json')])
        if cfg_path:
            self._open_sync_manager(Path(cfg_path))

    def _sync_create_new(self) -> None:
        """The one question: where should Projx Diff keep its data? init_site
        does the rest; the user never meets data_dir or archive_repo."""
        folder = filedialog.askdirectory(
            title='Where should Projx Diff keep its data?',
            initialdir=_default_config_dir(), mustexist=False)
        if not folder:
            return
        from .sync import init_site
        try:
            cfg_path = init_site(Path(folder))
        except SystemExit as e:
            existing = Path(folder) / 'config.json'
            if existing.is_file():
                if messagebox.askyesno('Manage Nightly Sync',
                                       f'{e}\n\nOpen the existing config instead?'):
                    self._open_sync_manager(existing)
            else:
                messagebox.showerror('Manage Nightly Sync', str(e))
            return
        self._open_sync_manager(cfg_path)

    def _open_sync_manager(self, cfg_path: Path, quiet: bool = False) -> bool:
        """Load a config (tolerating a groupless fresh site) and open the
        manager; remembers the path for next launch. With quiet=True a load
        failure just returns False so the caller can fall back to the
        chooser instead of error-boxing a stale remembered path."""
        try:
            from . import census as census_mod
            cfg = _load_manager_config(cfg_path)
            cpath = census_mod.census_path(cfg)
            census = census_mod.load_census(cpath)
            census_mod.seed_from_config(census, cfg)
        except (SystemExit, Exception) as e:  # noqa: B014 (SystemExit from config validation)
            if not quiet:
                messagebox.showerror('Manage Nightly Sync', f'Could not load config:\n{e}')
            return False
        _save_setting('last_sync_config', str(cfg_path))
        _SyncManager(self.root, cfg, cpath, census, config_path=cfg_path, app=self)
        return True

    def _show_about(self) -> None:
        """About window in the app's shared dialog style."""
        top, body = self._dialog('About Projx Diff', f'Version {__version__}')

        tk.Label(body, text=f'© {__author__}  ·  {__license__} license',
                 bg=_SM_CARD, fg=_SM_TEXT, anchor='w').pack(fill='x')
        tk.Label(body, text='An independent tool. Not affiliated with, endorsed '
                            'by, or tested by DriveWorks™ Ltd. DriveWorks™ is a '
                            'trademark of DriveWorks Ltd.',
                 bg=_SM_CARD, fg=_SM_MUTED, justify='left', anchor='w',
                 wraplength=440).pack(fill='x', pady=(8, 0))

        link = tk.Label(body, text=__url__, bg=_SM_CARD, fg=_SM_ACCENT,
                        cursor='hand2', anchor='w')
        link.pack(fill='x', pady=(10, 0))
        link.bind('<Button-1>', lambda _e: webbrowser.open(__url__))

        self._finish_dialog(top)

    def _build_ui(self) -> None:
        # Plain tk widgets (not ttk) because ttk + Tk 8.5 on modern macOS often
        # renders as an empty / black frame. tk widgets are uglier but draw.
        self.root.configure(bg=_SM_BG)

        # Header bar — same look as the Manage Nightly Sync window, so the app
        # reads as one consistent piece.
        header = tk.Frame(self.root, bg=_SM_HEADER_BG)
        header.pack(side='top', fill='x')
        tk.Label(header, text='Projx Diff', bg=_SM_HEADER_BG, fg=_SM_HEADER_FG,
                 font=('TkDefaultFont', 15, 'bold')).pack(anchor='w', padx=16, pady=(10, 0))
        tk.Label(header, text='Compare two DriveWorks™ projects into one shareable report',
                 bg=_SM_HEADER_BG, fg=_SM_HEADER_SUB,
                 font=('TkDefaultFont', 9)).pack(anchor='w', padx=16, pady=(1, 10))

        pad = {'padx': 8, 'pady': 6}
        bg = _SM_BG

        frm = tk.Frame(self.root, bg=bg, padx=14, pady=12)
        frm.pack(fill='both', expand=True)

        def label(text, **kw):
            return tk.Label(frm, text=text, bg=bg, fg=_SM_TEXT, anchor='w', **kw)

        def entry(var):
            return tk.Entry(frm, textvariable=var, highlightthickness=1,
                            highlightcolor=_SM_ACCENT, relief='solid', bd=1)

        def button(text, cmd):
            return tk.Button(frm, text=text, command=cmd, highlightthickness=0,
                             relief='flat', bg='#e6e8ec', activebackground='#dcdfe4',
                             cursor='hand2', padx=10, pady=3)

        self._frm = frm
        self.old_entry = entry(self.old_path)
        self.new_entry = entry(self.new_path)
        self.out_entry = entry(self.output_path)

        label('Old project:').grid(row=0, column=0, sticky='w', **pad)
        self.old_entry.grid(row=0, column=1, sticky='ew', **pad)
        button('Browse…', lambda: self._pick_file(self.old_path, self.old_entry, 'old')).grid(row=0, column=2, columnspan=2, sticky='ew', **pad)

        label('New project:').grid(row=1, column=0, sticky='w', **pad)
        self.new_entry.grid(row=1, column=1, sticky='ew', **pad)
        button('Browse…', lambda: self._pick_file(self.new_path, self.new_entry, 'new')).grid(row=1, column=2, columnspan=2, sticky='ew', **pad)

        label('Output HTML:').grid(row=2, column=0, sticky='w', **pad)
        self.out_entry.grid(row=2, column=1, sticky='ew', **pad)
        button('Save as…', self._pick_output).grid(row=2, column=2, columnspan=2, sticky='ew', **pad)

        tk.Checkbutton(
            frm, text='Open report in browser when done',
            variable=self.open_in_browser, bg=bg, fg=_SM_TEXT, anchor='w',
            highlightthickness=0, activebackground=bg, selectcolor='#ffffff',
        ).grid(row=3, column=1, sticky='w', **pad)

        self.compare_btn = tk.Button(
            frm, text='Compare', command=self._on_compare,
            highlightthickness=0, relief='flat', bg=_SM_ACCENT, fg='#ffffff',
            activebackground=_SM_ACCENT_ACT, activeforeground='#ffffff',
            font=('TkDefaultFont', 13, 'bold'), cursor='hand2', pady=4,
        )
        self.compare_btn.grid(row=3, column=2, columnspan=2, sticky='ew', **pad)

        # Database Options panel — optional per-side group-DB connection for
        # resolving model/rule names. Shown by default (View ▸ Show Database
        # Options hides it). SQL auth fields hide when Windows auth is on.
        self._db_row = 4
        db_frame = tk.Frame(frm, bg='#eef1f5', highlightthickness=1,
                            highlightbackground=_SM_DIVIDER)
        self.db_frame = db_frame
        db_frame.grid(row=self._db_row, column=0, columnspan=4, sticky='ew',
                      padx=8, pady=(2, 6))

        def db_label(text, **kw):
            kw.setdefault('fg', _SM_TEXT)
            return tk.Label(db_frame, text=text, bg='#eef1f5', anchor='w', **kw)

        def db_entry(var, show=None):
            return tk.Entry(db_frame, textvariable=var, highlightthickness=1,
                            highlightcolor=_SM_ACCENT, relief='solid', bd=1,
                            show=show)

        db_label('DATABASE (OPTIONAL — RESOLVES MODEL/RULE NAMES)', fg=_SM_MUTED,
                 font=('TkDefaultFont', 8, 'bold')).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(8, 2))

        db_label('Old DB server:').grid(row=1, column=0, sticky='w', padx=10, pady=3)
        db_entry(self.old_db_server).grid(row=1, column=1, sticky='ew', padx=(0, 10), pady=3)
        db_label('Old DB name:').grid(row=1, column=2, sticky='w', padx=10, pady=3)
        db_entry(self.old_db_database).grid(row=1, column=3, sticky='ew', padx=(0, 10), pady=3)

        db_label('New DB server:').grid(row=2, column=0, sticky='w', padx=10, pady=3)
        db_entry(self.new_db_server).grid(row=2, column=1, sticky='ew', padx=(0, 10), pady=3)
        db_label('New DB name:').grid(row=2, column=2, sticky='w', padx=10, pady=3)
        db_entry(self.new_db_database).grid(row=2, column=3, sticky='ew', padx=(0, 10), pady=3)

        tk.Checkbutton(
            db_frame, text='Use Windows Authentication (instead of SQL Server login)',
            variable=self.db_windows_auth, bg='#eef1f5', fg=_SM_TEXT,
            activebackground='#eef1f5', selectcolor='#ffffff', anchor='w',
            highlightthickness=0, cursor='hand2',
            command=self._apply_windows_auth_visibility,
        ).grid(row=3, column=0, columnspan=4, sticky='w', padx=10, pady=(2, 0))

        self.db_user_label = db_label('SQL username:')
        self.db_user_entry = db_entry(self.db_user)
        self.db_pass_label = db_label('SQL password:')
        self.db_pass_entry = db_entry(self.db_password, show='*')
        self.db_user_label.grid(row=4, column=0, sticky='w', padx=10, pady=(2, 8))
        self.db_user_entry.grid(row=4, column=1, sticky='ew', padx=(0, 10), pady=(2, 8))
        self.db_pass_label.grid(row=4, column=2, sticky='w', padx=10, pady=(2, 8))
        self.db_pass_entry.grid(row=4, column=3, sticky='ew', padx=(0, 10), pady=(2, 8))

        db_frame.columnconfigure(1, weight=1)
        db_frame.columnconfigure(3, weight=1)

        # Status line — wraps so the full output path is always visible, and
        # carries run progress/results now that the log is hidden by default.
        self.status_label = tk.Label(frm, text='', bg=bg, anchor='w', justify='left',
                                     wraplength=660)
        self.status_label.grid(row=5, column=0, columnspan=4, sticky='w', padx=8, pady=(2, 2))

        # Filled by a background update check (notify-only; see _check_updates).
        self.update_label = tk.Label(frm, text='', bg=bg, fg='#3f51b5', anchor='w', cursor='hand2')
        self.update_label.grid(row=6, column=0, columnspan=4, sticky='w', padx=8, pady=(0, 6))

        # Log pane — hidden by default; toggled via View ▸ Show Log.
        self._log_row = 7
        self.log_label = label('Log:')
        self.log_label.grid(row=self._log_row, column=0, sticky='nw', **pad)
        self.log_box = ScrolledText(frm, height=14, wrap='word', state=DISABLED, bd=1, relief='solid')
        self.log_box.grid(row=self._log_row, column=1, columnspan=3, sticky='nsew', **pad)

        frm.columnconfigure(1, weight=1)

        # Apply the initial panel states and seed the status line with the
        # full destination; keep the status in sync when the output path changes.
        self._apply_db_visibility()
        self._apply_windows_auth_visibility()
        self._apply_log_visibility()
        self.output_path.trace_add('write', lambda *a: self._update_status_idle())
        self._update_status_idle()

    def _sync_window_size(self) -> None:
        """Recompute window height from which optional panels are open."""
        height = 392
        if self.show_db.get():
            height += 168
        if self.show_log.get():
            height += 228
        self.root.geometry(f'760x{height}')

    def _apply_db_visibility(self) -> None:
        """Show or hide the Database Options panel and resize the window."""
        if self.show_db.get():
            self.db_frame.grid()
        else:
            self.db_frame.grid_remove()
        self._sync_window_size()

    def _apply_windows_auth_visibility(self) -> None:
        """Show the SQL username/password fields unless Windows integrated
        auth is selected — SQL Server login is the default here."""
        widgets = (self.db_user_label, self.db_user_entry,
                   self.db_pass_label, self.db_pass_entry)
        for w in widgets:
            w.grid_remove() if self.db_windows_auth.get() else w.grid()

    def _apply_log_visibility(self) -> None:
        """Show or hide the log pane (View ▸ Show Log) and resize the window."""
        if self.show_log.get():
            self.log_label.grid()
            self.log_box.grid()
            self._frm.rowconfigure(self._log_row, weight=1)
        else:
            self.log_label.grid_remove()
            self.log_box.grid_remove()
            self._frm.rowconfigure(self._log_row, weight=0)
        self._sync_window_size()

    def _set_status(self, text: str, color: str = '#444') -> None:
        self.status_label.configure(text=text, fg=color)

    def _update_status_idle(self) -> None:
        """When not mid-comparison, show where the report will be saved (full,
        resolved path), wrapped so the whole thing is visible."""
        if self._busy:
            return
        raw = self.output_path.get().strip()
        full = str(resolve_output_path(raw)) if resolve_output_path else (raw or 'dw_comparison.html')
        self._set_status('Report will be saved to:  ' + full, '#444')

    def _pick_file(self, target: StringVar, entry_widget=None, key: str = '') -> None:
        path = filedialog.askopenfilename(
            title='Select project file',
            filetypes=PROJX_FILETYPES,
            initialdir=self._last_dirs.get(key, ''),
        )
        if path:
            # normpath shows the native backslash form on Windows — Tk's
            # picker returns forward slashes (it's Tcl underneath).
            norm = os.path.normpath(path)
            target.set(norm)
            if key:
                self._last_dirs[key] = os.path.dirname(norm)
            if entry_widget is not None:
                entry_widget.xview_moveto(1.0)  # show the filename end, not the start

    def _pick_output(self) -> None:
        current = Path(self.output_path.get().strip() or 'dw_comparison.html')
        # The report is always HTML, so no file-type chooser is shown; the
        # default extension keeps the .html suffix.
        path = filedialog.asksaveasfilename(
            title='Save report as',
            defaultextension='.html',
            initialdir=self._last_dirs.get('output')
                       or (str(current.parent) if current.is_absolute() else ''),
            initialfile=current.name,
        )
        if path:
            norm = os.path.normpath(path)
            self.output_path.set(norm)
            self._last_dirs['output'] = os.path.dirname(norm)
            self.out_entry.xview_moveto(1.0)

    def _log(self, msg: str) -> None:
        self.log_box.configure(state=NORMAL)
        self.log_box.insert(END, msg)
        self.log_box.see(END)
        self.log_box.configure(state=DISABLED)

    def _drain_log(self) -> None:
        try:
            while True:
                self._log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self._drain_log)

    def _on_compare(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        old_raw = self.old_path.get().strip()
        new_raw = self.new_path.get().strip()
        out_raw = self.output_path.get().strip()

        if not old_raw or not new_raw:
            messagebox.showwarning('Missing input', 'Pick both an old and a new project.')
            return

        old = Path(old_raw)
        new = Path(new_raw)
        if not old.exists():
            messagebox.showerror('Not found', f'Old project not found:\n{old}')
            return
        if not new.exists():
            messagebox.showerror('Not found', f'New project not found:\n{new}')
            return

        # Anchor a bare/relative filename to a writable folder so a
        # double-clicked app (read-only cwd) can't fail with a read-only error.
        output = resolve_output_path(out_raw) if resolve_output_path else Path(out_raw or 'dw_comparison.html')
        open_browser = self.open_in_browser.get()

        # Snapshot DB fields on the UI thread (Tk vars aren't thread-safe)
        # and remember the one-time setup — never the password.
        db = {
            'old_server': self.old_db_server.get().strip(),
            'old_database': self.old_db_database.get().strip(),
            'new_server': self.new_db_server.get().strip(),
            'new_database': self.new_db_database.get().strip(),
            'windows_auth': self.db_windows_auth.get(),
            'user': self.db_user.get().strip(),
            'password': self.db_password.get(),
        }
        for key, value in (('old_db_server', db['old_server']),
                           ('new_db_server', db['new_server']),
                           ('old_db_database', db['old_database']),
                           ('new_db_database', db['new_database']),
                           ('db_windows_auth', db['windows_auth']),
                           ('db_user', db['user'])):
            _save_setting(key, value)

        # Clear log, disable button, show progress in the status line.
        self.log_box.configure(state=NORMAL)
        self.log_box.delete('1.0', END)
        self.log_box.configure(state=DISABLED)
        self.compare_btn.configure(state=DISABLED, text='Comparing…')
        self._busy = True
        self._set_status('⏳ Comparing…', '#3f51b5')

        self._worker = threading.Thread(
            target=self._run_compare,
            args=(old, new, output, open_browser, db),
            daemon=True,
        )
        self._worker.start()

    def _run_compare(self, old: Path, new: Path, output: Path, open_browser: bool,
                     db: dict = None) -> None:
        writer = _QueueWriter(self._log_queue)
        prev_stdout = sys.stdout
        sys.stdout = writer
        saved = None
        error = None
        db_warnings: list = []
        try:
            old_name = old.stem if old.suffix.lower() == '.driveprojx' else old.name
            new_name = new.stem if new.suffix.lower() == '.driveprojx' else new.name

            old_folder = resolve_input(old) if resolve_input else old
            new_folder = resolve_input(new) if resolve_input else new

            if not old_folder.is_dir():
                raise ValueError(f'{old} is not a directory or .driveprojx file')
            if not new_folder.is_dir():
                raise ValueError(f'{new} is not a directory or .driveprojx file')

            print(f'Loading old project: {old_name}')
            old_proj = load_project(old_folder)

            print(f'Loading new project: {new_name}')
            new_proj = load_project(new_folder)

            old_resolved = new_resolved = None
            old_props = new_props = old_types = new_types = None
            if resolve_db_names and db:
                sql_auth = not db['windows_auth']
                for side, proj in (('old', old_proj), ('new', new_proj)):
                    resolved, props, types, err = resolve_db_names(
                        side, db[f'{side}_server'], db[f'{side}_database'],
                        proj.component_index, user=db['user'],
                        password=db['password'], sql_auth=sql_auth)
                    if err:
                        db_warnings.append(f'{side.title()} database: {err}')
                    if side == 'old':
                        old_resolved, old_props, old_types = resolved, props, types
                    else:
                        new_resolved, new_props, new_types = resolved, props, types

            print('Generating comparison report...')
            html = generate_html_report(old_proj, new_proj, old_name, new_name,
                                        old_resolved, new_resolved,
                                        old_props, new_props,
                                        old_types, new_types)

            output.write_text(html, encoding='utf-8')
            saved = str(output.resolve())
            print(f'Report saved to: {saved}')

            if open_browser:
                # as_uri() keeps the file:// URL well-formed on Windows and
                # percent-encodes spaces.
                webbrowser.open(output.resolve().as_uri())
        except Exception as e:
            error = str(e)
            print('\nERROR: ' + error)
            print(traceback.format_exc())
        finally:
            sys.stdout = prev_stdout
            if cleanup_temp_dirs:
                cleanup_temp_dirs()  # remove .driveprojx extractions from this run
            self.root.after(0, lambda: self._on_done(saved=saved, error=error,
                                                     db_warnings=db_warnings))

    def _on_done(self, saved: str | None = None, error: str | None = None,
                 db_warnings: list = None) -> None:
        self._busy = False
        db_warnings = db_warnings or []
        self.compare_btn.configure(state=NORMAL, text='Compare')
        if error:
            # The traceback is in the (possibly hidden) log; make sure the user
            # can't miss the failure itself.
            self._set_status('⚠ Comparison failed — open View ▸ Show Log for details', '#c0392b')
            messagebox.showerror(
                'Comparison failed',
                error + '\n\nOpen View ▸ Show Log for the full details.',
            )
        elif saved:
            note = ' — opened in browser' if self.open_in_browser.get() else ''
            msg = '✅ Report saved to:  ' + saved + note
            if db_warnings:
                # A DB failure doesn't stop the report, so say it right here
                # in amber — not only in the hidden log.
                msg += '\n⚠ ' + '  ·  '.join(db_warnings) + \
                       '\n   Models and Rule Changes show raw ids for that side.'
                self._set_status(msg, '#b8860b')
            else:
                self._set_status(msg, '#1b7a3d')
        else:
            self._update_status_idle()

    def _check_updates(self) -> None:
        """Free, fail-silent update check; runs off the UI thread on launch."""
        newer = check_for_update()
        if newer:
            try:
                self.root.after(0, lambda: self._show_update(newer))
            except Exception:
                pass  # window already closed

    def _show_update(self, newer: str) -> None:
        self.update_label.configure(
            text=f'⬆ Update available: v{newer} — click to download & install')
        self.update_label.bind('<Button-1>', lambda _e: self._start_update(newer))

    def _start_update(self, newer: str) -> None:
        """One-click update: download the platform's packaged asset, verify
        its checksum, then hand off (Windows: run the installer and exit so
        it can replace us; macOS: reveal the zip in Downloads). Any failure
        falls back to opening the download page — never a dead end."""
        self.update_label.unbind('<Button-1>')  # no double-starts
        threading.Thread(target=self._download_update, args=(newer,),
                         daemon=True).start()

    def _download_update(self, newer: str) -> None:
        from .update_check import download_update

        def progress(done, total):
            note = f'{done * 100 // total}%' if total else f'{done // 1048576} MB'
            self.root.after(0, lambda: self.update_label.configure(
                text=f'⬇ Downloading v{newer}…  {note}'))

        try:
            if sys.platform == 'win32':
                dest_dir = Path(tempfile.mkdtemp(prefix='projxdiff_update_'))
            else:
                dest_dir = Path.home() / 'Downloads'
            path = download_update(newer, dest_dir, progress=progress)
        except Exception:
            # Unverifiable or failed download: back to the website, and the
            # notice stays clickable for another try.
            self.root.after(0, lambda: (
                self.update_label.configure(
                    text=f'⬆ Update v{newer} — opening the download page…'),
                self.update_label.bind('<Button-1>',
                                       lambda _e: webbrowser.open(DOWNLOAD_PAGE)),
                webbrowser.open(DOWNLOAD_PAGE)))
            return
        self.root.after(0, lambda: self._install_update(path))

    def _install_update(self, path: Path) -> None:
        if sys.platform == 'win32':
            try:
                os.startfile(path)      # launch the verified installer...
            except Exception:
                webbrowser.open(DOWNLOAD_PAGE)
                return
            self.root.destroy()          # ...and get out of its way
        else:
            try:
                subprocess.run(['open', '-R', str(path)], check=False)
            except Exception:
                pass
            self.update_label.configure(
                text=f'✅ Downloaded to {path.parent} — quit and replace the app to update')


class _SyncManager:
    """Toplevel census manager: one table of *every* project with its path,
    last-modified date, last saver, and an inline disposition control; below
    it, identity fields for unmapped users and any name conflicts. Saving
    persists census.json and heals the metrics DB."""

    _COLS = ('Project', 'Path', 'Modified', 'Last saved by', 'Disposition')
    _COL_WEIGHT = (0, 1, 0, 0, 0)  # only the path column absorbs slack

    # The UI shows "New" for the engine's internal "pending" disposition; the
    # data model keeps "pending" (that's the value sync.py acts on).
    _DISP_TO_LABEL = {'pending': 'New', 'track': 'Track', 'ignore': 'Ignore'}
    _LABEL_TO_DISP = {'New': 'pending', 'Track': 'track', 'Ignore': 'ignore'}
    _MENU_LABELS = ('New', 'Track', 'Ignore')
    _DISP_COLORS = {'New': _SM_ACCENT, 'Track': '#1b7a3d', 'Ignore': _SM_MUTED}
    _FILTERS = (('All', 'all'), ('New', 'New'), ('Track', 'Track'), ('Ignore', 'Ignore'))

    def __init__(self, parent, cfg: dict, cpath: Path, census: dict,
                 config_path: Path | None = None, app=None):
        from . import census as census_mod
        self._census_mod = census_mod
        self.cfg = cfg
        self.cpath = cpath
        self.census = census
        # Path of the config itself — needed to add environment groups. None
        # when a caller has census data but no config file (tests).
        self.config_path = config_path
        self._app = app  # owning CompareApp, for Switch config… (optional)

        self.top = tk.Toplevel(parent)
        self.top.title('Manage Nightly Sync')
        self.top.configure(bg=_SM_BG)
        self.top.geometry('960x640')
        self.top.minsize(780, 460)

        self._build_all()
        self.top.transient(parent)

    def _build_all(self) -> None:
        """Build every section; _reload destroys and calls this again after
        an environment group is added, so each builder records its outermost
        frame(s) in self._frames (in pack order)."""
        self._frames: list = []
        self._build_header()
        self._build_toolbar()
        # Bottom-anchored pieces first so the table (packed last) fills the
        # middle and grows with the window.
        self._build_footer()
        self._build_users_and_conflicts()
        self._build_table()

    def _reload(self, cfg: dict, census: dict) -> None:
        """Swap in freshly scanned config + census and rebuild the window in
        place. In-progress edits (dispositions set, identities typed) are
        carried over so adding a group never throws away triage work."""
        for name, var in self.proj_vars.items():
            if name in census['projects']:
                census['projects'][name]['disposition'] = \
                    self._LABEL_TO_DISP.get(var.get(), 'pending')
        typed = {raw: e.get().strip() for raw, e in self.user_entries.items()
                 if e.get().strip()}
        self.cfg, self.census = cfg, census
        self.top.unbind_all('<MouseWheel>')  # the old table's wheel binding
        for f in self._frames:
            f.destroy()
        self._build_all()
        for raw, text in typed.items():
            if raw in self.user_entries:
                self.user_entries[raw].insert(0, text)

    # ------------------------------------------------------------ sections ----

    def _build_header(self) -> None:
        header = tk.Frame(self.top, bg=_SM_HEADER_BG)
        header.pack(side='top', fill='x')
        self._frames.append(header)
        tk.Label(header, text='Manage Nightly Sync', bg=_SM_HEADER_BG, fg=_SM_HEADER_FG,
                 font=('TkDefaultFont', 15, 'bold')).pack(anchor='w', padx=18, pady=(12, 0))
        # Site configs have several source dirs; legacy has one; a fresh site
        # has none yet.
        resolved = self.cfg.get('sources_resolved') or {}
        dirs = ', '.join(f'{n}: {s["source_dir"]}' if n else str(s['source_dir'])
                         for n, s in resolved.items()) or self.cfg.get('source_dir', '') \
            or 'no environment groups yet'
        tk.Label(header, text=f'{dirs}  ·  {self.cpath}',
                 bg=_SM_HEADER_BG, fg=_SM_HEADER_SUB,
                 font=('TkDefaultFont', 9)).pack(anchor='w', padx=18, pady=(1, 12))

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.top, bg=_SM_BG)
        bar.pack(side='top', fill='x', padx=16, pady=(12, 0))
        self._frames.append(bar)
        tk.Label(bar, text='Filter', bg=_SM_BG, fg=_SM_TEXT).pack(side='left')
        self.filter_var = StringVar()
        entry = tk.Entry(bar, textvariable=self.filter_var, highlightthickness=1,
                         highlightcolor=_SM_ACCENT, relief='solid', bd=1)
        entry.pack(side='left', fill='x', expand=True, padx=(8, 12))
        self.filter_var.trace_add('write', lambda *_a: self._render())
        self.count_label = tk.Label(bar, text='', bg=_SM_BG, fg=_SM_MUTED)
        self.count_label.pack(side='right')

        # Disposition segmented filter: All / New / Track / Ignore.
        seg = tk.Frame(self.top, bg=_SM_BG)
        seg.pack(side='top', fill='x', padx=16, pady=(8, 2))
        self._frames.append(seg)
        tk.Label(seg, text='Show', bg=_SM_BG, fg=_SM_TEXT).pack(side='left', padx=(0, 8))
        self._disp_filter = 'all'
        self._disp_buttons: dict[str, tk.Label] = {}
        for label, value in self._FILTERS:
            btn = tk.Label(seg, text=label, bg='#e6e8ec', fg=_SM_TEXT, padx=14, pady=3,
                           cursor='hand2', font=('TkDefaultFont', 9, 'bold'))
            btn.pack(side='left', padx=(0, 4))
            btn.bind('<Button-1>', lambda _e, v=value: self._set_disp_filter(v))
            self._disp_buttons[value] = btn
        self._style_disp_buttons()

    def _build_footer(self) -> None:
        bar = tk.Frame(self.top, bg=_SM_BG)
        bar.pack(side='bottom', fill='x', padx=16, pady=(4, 12))
        self._frames.append(bar)
        tk.Button(bar, text='Save', command=self._save, bg=_SM_ACCENT, fg='#ffffff',
                  activebackground=_SM_ACCENT_ACT, activeforeground='#ffffff',
                  relief='flat', font=('TkDefaultFont', 11, 'bold'),
                  padx=20, pady=6, cursor='hand2').pack(side='right')
        tk.Button(bar, text='Cancel', command=self.top.destroy, bg='#e6e8ec',
                  relief='flat', padx=16, pady=6, cursor='hand2').pack(side='right', padx=8)
        # Group management lives left of Save/Cancel; hidden for callers with
        # census data but no config file.
        if self.config_path is not None:
            tk.Button(bar, text='Add environment group…', command=self._add_group,
                      bg='#e6e8ec', relief='flat', padx=14, pady=6,
                      cursor='hand2').pack(side='left')
        if self._app is not None:
            tk.Button(bar, text='Switch config…', command=self._switch_config,
                      bg=_SM_BG, fg=_SM_MUTED, relief='flat', bd=0,
                      highlightthickness=0, padx=8, pady=6,
                      cursor='hand2').pack(side='left', padx=(8, 0))

    def _source_root(self, key: str) -> Path:
        """Source dir for a census key. Site configs namespace keys as
        "<source>/<project>"; legacy keys are plain names under the single
        source. Guarded so missing config data degrades to '—' metadata
        rather than crashing."""
        resolved = self.cfg.get('sources_resolved') or {}
        if '/' in key:
            sname = key.split('/', 1)[0]
            if sname in resolved:
                return Path(resolved[sname]['source_dir'])
        if '' in resolved:
            return Path(resolved['']['source_dir'])
        return Path(self.cfg.get('source_dir', ''))

    def _build_table(self) -> None:
        # A Group column appears once the config has named environment groups
        # (site config); legacy single-source windows keep the original five.
        self._named = any(self.cfg.get('sources_resolved') or {})
        self._cols = (('Group',) + self._COLS) if self._named else self._COLS
        self._col_weight = ((0,) + self._COL_WEIGHT) if self._named else self._COL_WEIGHT

        outer = tk.Frame(self.top, bg=_SM_CARD, highlightbackground=_SM_DIVIDER,
                         highlightthickness=1)
        outer.pack(side='top', fill='both', expand=True, padx=16, pady=(4, 6))
        self._frames.append(outer)
        canvas = tk.Canvas(outer, bg=_SM_CARD, highlightthickness=0)
        vs = tk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        table = tk.Frame(canvas, bg=_SM_CARD)
        table.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        win = canvas.create_window((0, 0), window=table, anchor='nw')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=vs.set)
        canvas.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        # Wheel scroll only while the pointer is over the list, so it never
        # leaks to the main window after this dialog closes.
        canvas.bind('<Enter>', lambda _e: canvas.bind_all(
            '<MouseWheel>', lambda e: canvas.yview_scroll(int(-e.delta / 120), 'units')))
        canvas.bind('<Leave>', lambda _e: canvas.unbind_all('<MouseWheel>'))

        self._table = table
        # Clickable header row (click to sort, click again to reverse) + divider.
        self._header_labels: list = []
        if self.census['projects']:
            for col, weight in enumerate(self._col_weight):
                lbl = tk.Label(table, bg=_SM_CARD, fg=_SM_MUTED, anchor='w',
                               font=('TkDefaultFont', 8, 'bold'), cursor='hand2')
                lbl.grid(row=0, column=col, sticky='w', padx=10, pady=(10, 4))
                lbl.bind('<Button-1>', lambda _e, c=col: self._sort_by(c))
                self._header_labels.append(lbl)
                table.columnconfigure(col, weight=weight)
            tk.Frame(table, bg=_SM_DIVIDER, height=1).grid(
                row=1, column=0, columnspan=len(self._cols), sticky='ew', padx=6)

        self.proj_vars: dict[str, StringVar] = {}
        self._rows: list = []
        for name in sorted(self.census['projects'].keys()):
            entry = self.census['projects'][name]
            rel = entry.get('path', '')
            # Census keys are namespaced "<group>/<project>" in site configs.
            group, title = (name.split('/', 1) if self._named and '/' in name
                            else ('', name))
            modified, saver = self._file_meta(self._source_root(name) / rel)
            var = StringVar(value=self._DISP_TO_LABEL.get(entry.get('disposition', 'pending'), 'New'))
            self.proj_vars[name] = var
            cells = ([tk.Label(table, text=group, bg=_SM_CARD, fg=_SM_MUTED, anchor='w',
                               font=('TkDefaultFont', 9, 'bold'))] if self._named else []) + [
                tk.Label(table, text=title, bg=_SM_CARD, fg=_SM_TEXT, anchor='w',
                         font=('TkDefaultFont', 10, 'bold')),
                tk.Label(table, text=self._ellipsize(rel, 54), bg=_SM_CARD, fg=_SM_MUTED, anchor='w'),
                tk.Label(table, text=modified, bg=_SM_CARD, fg=_SM_TEXT, anchor='w'),
                tk.Label(table, text=saver, bg=_SM_CARD, fg=_SM_TEXT, anchor='w'),
                self._disposition_menu(table, var),
            ]
            self._rows.append({'name': name, 'group': group, 'title': title,
                               'rel': rel, 'modified': modified,
                               'saver': saver, 'var': var, 'cells': cells,
                               'search': f'{name} {rel} {saver}'.lower()})

        if not self._rows:
            # Empty state: a fresh site has no groups; a scanned-but-empty
            # source has no projects. Either way, point at the next action.
            msg = ('No environment groups yet.\nAdd one — Projx Diff will scan '
                   'it and list every project here for triage.'
                   if not (self.cfg.get('sources_resolved') or {})
                   else 'No projects found in the configured folders yet.')
            tk.Label(table, text=msg, bg=_SM_CARD, fg=_SM_MUTED, justify='center',
                     font=('TkDefaultFont', 11)).grid(
                row=2, column=0, columnspan=len(self._cols), pady=(48, 14))
            if self.config_path is not None:
                tk.Button(table, text='Add environment group…', command=self._add_group,
                          bg=_SM_ACCENT, fg='#ffffff', activebackground=_SM_ACCENT_ACT,
                          activeforeground='#ffffff', relief='flat',
                          font=('TkDefaultFont', 10, 'bold'), padx=16, pady=5,
                          cursor='hand2').grid(row=3, column=0,
                                               columnspan=len(self._cols), pady=(0, 48))
            table.columnconfigure(0, weight=1)  # center the empty-state cell

        self._sort_col, self._sort_desc = 0, False
        self._render()

    def _disposition_menu(self, parent, var: StringVar) -> tk.OptionMenu:
        # Flat, borderless "pill" — no rectangle — with a color-coded label.
        om = tk.OptionMenu(parent, var, *self._MENU_LABELS)
        om.configure(relief='flat', bd=0, highlightthickness=0, bg='#eef1f5',
                     activebackground='#e3e8f0', font=('TkDefaultFont', 9, 'bold'),
                     width=7, anchor='w', padx=10, cursor='hand2')
        om['menu'].configure(bg=_SM_CARD, activebackground=_SM_ACCENT,
                             activeforeground='#ffffff', font=('TkDefaultFont', 9))

        def _recolor(*_a):
            om.configure(fg=self._DISP_COLORS.get(var.get(), _SM_TEXT))
        var.trace_add('write', _recolor)
        _recolor()
        return om

    def _build_users_and_conflicts(self) -> None:
        cm = self._census_mod
        wrap = tk.Frame(self.top, bg=_SM_BG)
        wrap.pack(side='bottom', fill='x', padx=16, pady=(0, 2))
        self._frames.append(wrap)

        self.user_entries: dict[str, tk.Entry] = {}
        unmapped = cm.unmapped_users(self.census)
        tk.Label(wrap, text=f'UNMAPPED USERS ({len(unmapped)})', bg=_SM_BG, fg=_SM_MUTED,
                 anchor='w', font=('TkDefaultFont', 8, 'bold')).pack(fill='x', pady=(6, 2))
        if unmapped:
            tk.Label(wrap, text='Fill in as  Name <email>  — leave blank to keep a shared '
                               'account unattributed. Past metrics under the raw name are healed.',
                     bg=_SM_BG, fg=_SM_MUTED, anchor='w', justify='left',
                     wraplength=880).pack(fill='x')
            for raw in unmapped:
                row = tk.Frame(wrap, bg=_SM_BG)
                row.pack(fill='x', pady=2)
                tk.Label(row, text=raw, bg=_SM_BG, fg=_SM_TEXT, width=22, anchor='w',
                         font=('TkDefaultFont', 10, 'bold')).pack(side='left')
                e = tk.Entry(row, highlightthickness=1, highlightcolor=_SM_ACCENT,
                             relief='solid', bd=1)
                e.pack(side='left', fill='x', expand=True)
                self.user_entries[raw] = e
        else:
            tk.Label(wrap, text='All user names are mapped.', bg=_SM_BG, fg=_SM_MUTED,
                     anchor='w').pack(fill='x')

        conflicts = self.census.get('conflicts', [])
        if conflicts:
            tk.Label(wrap, text=f'NAME CONFLICTS ({len(conflicts)})', bg=_SM_BG, fg='#b4462d',
                     anchor='w', font=('TkDefaultFont', 8, 'bold')).pack(fill='x', pady=(8, 2))
            tk.Label(wrap, text='Two source files share a name; only the registered path syncs. '
                               'Rename/remove the copy on the share, or add an exclude pattern.',
                     bg=_SM_BG, fg=_SM_MUTED, anchor='w', justify='left',
                     wraplength=880).pack(fill='x')
            for c in conflicts:
                tk.Label(wrap, text=f'· {c.get("project", "")}: {c.get("path", "")}  '
                                    f'(registered: {c.get("registered", "")})',
                         bg=_SM_BG, fg=_SM_MUTED, anchor='w', wraplength=880,
                         justify='left').pack(fill='x')

    # ------------------------------------------------------------- helpers ----

    def _file_meta(self, abs_path: Path) -> tuple:
        """(modified-date, last-saver-name) for a project's source file. Reads
        the last saver straight from the .driveprojx; '—' when unavailable."""
        modified, saver = '—', '—'
        try:
            if abs_path.is_file():
                modified = datetime.fromtimestamp(abs_path.stat().st_mtime).strftime('%Y-%m-%d')
                display, _email = self._census_mod.read_last_saver_from_zip(abs_path)
                if display:
                    ident = self.census['users'].get(display) or display
                    saver = ident.split('<')[0].strip() or display
        except Exception:
            pass
        return modified, saver

    @staticmethod
    def _ellipsize(text: str, limit: int) -> str:
        """Keep the informative tail (folder + filename) of a long path."""
        return text if len(text) <= limit else '…' + text[-(limit - 1):]

    def _sort_by(self, col: int) -> None:
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        self._render()

    def _sort_key(self, row: dict, col: int):
        field = self._cols[col]
        if field == 'Group':
            return (row['group'].lower(), row['title'].lower())
        if field == 'Project':
            return row['title'].lower()
        if field == 'Path':
            return row['rel'].lower()
        if field == 'Modified':  # missing dates sort to the end
            return (row['modified'] == '—', row['modified'])
        if field == 'Last saved by':
            return (row['saver'] == '—', row['saver'].lower())
        return {'New': 0, 'Track': 1, 'Ignore': 2}.get(row['var'].get(), 0)

    def _set_disp_filter(self, value: str) -> None:
        self._disp_filter = value
        self._style_disp_buttons()
        self._render()

    def _style_disp_buttons(self) -> None:
        for value, btn in self._disp_buttons.items():
            active = value == self._disp_filter
            btn.configure(bg=_SM_ACCENT if active else '#e6e8ec',
                          fg='#ffffff' if active else _SM_TEXT)

    def _render(self) -> None:
        """Lay out the rows that pass the text + disposition filters, in the
        current sort order. Rows are persistent widgets re-gridded in place."""
        query = self.filter_var.get().strip().lower()
        df = self._disp_filter
        rows = [r for r in self._rows
                if (not query or query in r['search'])
                and (df == 'all' or r['var'].get() == df)]
        rows.sort(key=lambda r: self._sort_key(r, self._sort_col), reverse=self._sort_desc)

        for r in self._rows:
            for widget in r['cells']:
                widget.grid_remove()
        for i, r in enumerate(rows):
            for col, widget in enumerate(r['cells']):
                widget.grid(row=i + 2, column=col, sticky='w', padx=10, pady=4)

        self._update_count(len(rows), len(self._rows))
        arrow = ' ▼' if self._sort_desc else ' ▲'
        for c, lbl in enumerate(self._header_labels):
            lbl.configure(text=self._cols[c].upper() + (arrow if c == self._sort_col else ''))

    def _update_count(self, shown: int, total: int) -> None:
        self.count_label.configure(
            text=f'{total} projects' if shown == total else f'{shown} of {total} projects')

    def _save(self) -> None:
        cm = self._census_mod
        for name, var in self.proj_vars.items():
            self.census['projects'][name]['disposition'] = self._LABEL_TO_DISP.get(var.get(), 'pending')
        mapped = 0
        for raw, entry in self.user_entries.items():
            ident = entry.get().strip()
            if ident:
                self.census['users'][raw] = ident
                mapped += 1

        try:
            cm.save_census(self.cpath, self.census)
            healed = 0
            db_path = Path(self.cfg['data_dir']) / 'metrics.sqlite'
            if mapped and db_path.is_file():
                import sqlite3
                conn = sqlite3.connect(db_path, isolation_level=None)
                try:
                    healed = cm.heal_metrics(conn, self.census)
                finally:
                    conn.close()
        except Exception as e:
            messagebox.showerror('Manage Nightly Sync', f'Save failed:\n{e}')
            return

        summary = f'Census saved to {self.cpath}.'
        if healed:
            summary += f'\n{healed} past metrics row(s) updated with mapped identities.'
        messagebox.showinfo('Manage Nightly Sync', summary)
        if self._should_offer_schedule():
            self._offer_schedule()
        self.top.destroy()

    # ------------------------------------------------- environment groups ----

    def _apply_add_group(self, name: str, folder: str) -> tuple:
        """Engine work behind the add-group dialog: add the source to the
        config, census-scan every source (no archiving), persist the
        discoveries, and rebuild the window. Raises SystemExit with a
        human-readable message on any problem. Returns (slug, scan summary)."""
        from .sync import add_source, load_config, find_projects
        cm = self._census_mod
        slug = add_source(self.config_path, name, folder)
        cfg = load_config(self.config_path)
        cpath = cm.census_path(cfg)
        census = cm.load_census(cpath)
        cm.seed_from_config(census, cfg)
        summary = cm.scan(cfg, census, find_projects)
        cm.save_census(cpath, census)  # discoveries survive even if the user closes now
        self.cpath = cpath
        self._reload(cfg, census)
        return slug, summary

    def _add_group(self) -> None:
        """Dialog: group name + projects folder. On OK the group is added and
        scanned immediately — discovered projects land in the table as New
        and unseen saver names in the unmapped-users section, ready for
        triage in this same window."""
        from .sync import slug_source_name
        top, body = _styled_dialog(self.top, 'Add Environment Group',
                                   'A folder of projects to track — prod, staging, a plant…')
        name_var, dir_var = StringVar(), StringVar()

        grid = tk.Frame(body, bg=_SM_CARD)
        grid.pack(fill='x')
        tk.Label(grid, text='Name', bg=_SM_CARD, fg=_SM_TEXT,
                 anchor='w').grid(row=0, column=0, sticky='w', pady=4)
        name_entry = tk.Entry(grid, textvariable=name_var, highlightthickness=1,
                              highlightcolor=_SM_ACCENT, relief='solid', bd=1)
        name_entry.grid(row=0, column=1, sticky='ew', padx=(10, 0), pady=4)
        slug_lbl = tk.Label(grid, text='', bg=_SM_CARD, fg=_SM_MUTED, anchor='w',
                            font=('TkDefaultFont', 9))
        slug_lbl.grid(row=1, column=1, columnspan=2, sticky='w', padx=(10, 0))

        def _preview(*_a):
            name = name_var.get().strip()
            if not name:
                slug_lbl.configure(text='Group names are permanent — pick something boring.')
                return
            try:
                slug = slug_source_name(name)
            except SystemExit:
                slug_lbl.configure(text='Needs at least one letter or digit.')
                return
            note = f'Saved as "{slug}" — permanent, so pick something boring.'
            slug_lbl.configure(text=note if slug != name else
                               'Group names are permanent — pick something boring.')
        name_var.trace_add('write', _preview)
        _preview()

        tk.Label(grid, text='Projects folder', bg=_SM_CARD, fg=_SM_TEXT,
                 anchor='w').grid(row=2, column=0, sticky='w', pady=(10, 4))
        dir_entry = tk.Entry(grid, textvariable=dir_var, highlightthickness=1,
                             highlightcolor=_SM_ACCENT, relief='solid', bd=1, width=38)
        dir_entry.grid(row=2, column=1, sticky='ew', padx=(10, 0), pady=(10, 4))

        def _browse():
            folder = filedialog.askdirectory(
                title='Folder containing .driveprojx projects', parent=top)
            if folder:
                dir_var.set(folder)
                dir_entry.xview_moveto(1.0)
        tk.Button(grid, text='Browse…', command=_browse, bg='#e6e8ec', relief='flat',
                  padx=10, pady=2, cursor='hand2').grid(row=2, column=2,
                                                        padx=(8, 0), pady=(10, 4))
        grid.columnconfigure(1, weight=1)

        def _ok():
            folder = dir_var.get().strip()
            if not folder:
                messagebox.showerror('Add Environment Group',
                                     'Pick the projects folder first.', parent=top)
                return
            try:
                slug, summary = self._apply_add_group(name_var.get(), folder)
            except SystemExit as e:  # engine messages are written for humans
                messagebox.showerror('Add Environment Group', str(e), parent=top)
                return
            top.destroy()
            messagebox.showinfo(
                'Add Environment Group',
                f'Group "{slug}" added and scanned.\n'
                f'{len(summary["pending"])} project(s) pending disposition · '
                f'{len(summary["unmapped"])} unmapped user(s).',
                parent=self.top)

        bar = tk.Frame(top, bg=_SM_BG)
        bar.pack(fill='x', pady=(8, 12))
        tk.Button(bar, text='Add group', command=_ok, bg=_SM_ACCENT, fg='#ffffff',
                  activebackground=_SM_ACCENT_ACT, activeforeground='#ffffff',
                  relief='flat', padx=18, pady=4, cursor='hand2',
                  font=('TkDefaultFont', 10, 'bold')).pack(side='right', padx=14)
        tk.Button(bar, text='Cancel', command=top.destroy, bg='#e6e8ec',
                  relief='flat', padx=14, pady=4, cursor='hand2').pack(side='right')
        _center_over(self.top, top)
        name_entry.focus_set()

    def _switch_config(self) -> None:
        """Close this manager and reopen the chooser (the remembered config
        otherwise skips it forever)."""
        self.top.destroy()
        self._app._sync_chooser()

    # ------------------------------------------------------- scheduling ----

    def _sync_command(self) -> str:
        """Command line for the nightly scheduled task. Frozen builds invoke
        the installed exe directly; dev runs go through the interpreter."""
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}" --sync "{self.config_path}"'
        return f'"{sys.executable}" -m dw_compare --sync "{self.config_path}"'

    def _should_offer_schedule(self) -> bool:
        """Offer Task Scheduler registration after the first save of each
        config — Windows only; elsewhere the nightly run is cron territory."""
        if sys.platform != 'win32' or self.config_path is None:
            return False
        return str(self.config_path) not in _load_settings().get('schedule_offered', [])

    def _offer_schedule(self) -> None:
        offered = _load_settings().get('schedule_offered', [])
        _save_setting('schedule_offered', offered + [str(self.config_path)])

        top, body = _styled_dialog(self.top, 'Run Nightly',
                                   'Register a Windows scheduled task for the sync')
        row = tk.Frame(body, bg=_SM_CARD)
        row.pack(fill='x')
        tk.Label(row, text='Run nightly at', bg=_SM_CARD, fg=_SM_TEXT).pack(side='left')
        time_var = StringVar(value='02:00')
        tk.Entry(row, textvariable=time_var, width=6, justify='center',
                 highlightthickness=1, highlightcolor=_SM_ACCENT, relief='solid',
                 bd=1).pack(side='left', padx=8)
        status = tk.Label(body, text='', bg=_SM_CARD, fg=_SM_MUTED, anchor='w',
                          wraplength=460, justify='left')

        def _register():
            t = time_var.get().strip()
            if not re.fullmatch(r'[0-2]?\d:[0-5]\d', t):
                status.configure(text='Time must be HH:MM (24-hour).', fg='#c0392b')
                return
            try:
                proc = subprocess.run(
                    ['schtasks', '/Create', '/F', '/SC', 'DAILY',
                     '/TN', 'ProjxDiff Nightly Sync', '/ST', t,
                     '/TR', self._sync_command()],
                    capture_output=True, text=True)
            except OSError as e:
                status.configure(text=f'Could not run schtasks: {e}', fg='#c0392b')
                return
            if proc.returncode == 0:
                status.configure(text=f'Registered — runs nightly at {t}.', fg='#1b7a3d')
            else:
                status.configure(text=(proc.stderr or proc.stdout or 'schtasks failed').strip(),
                                 fg='#c0392b')

        tk.Button(row, text='Register scheduled task', command=_register,
                  bg=_SM_ACCENT, fg='#ffffff', activebackground=_SM_ACCENT_ACT,
                  activeforeground='#ffffff', relief='flat', padx=14, pady=3,
                  cursor='hand2', font=('TkDefaultFont', 10, 'bold')).pack(
            side='left', padx=(8, 0))
        status.pack(fill='x', pady=(8, 0))

        tk.Label(body, text='Or register it yourself (e.g. on a server):',
                 bg=_SM_CARD, fg=_SM_MUTED, anchor='w').pack(fill='x', pady=(12, 2))
        manual = tk.Entry(body, highlightthickness=1, relief='solid', bd=1)
        manual.insert(0, 'schtasks /Create /SC DAILY /TN "ProjxDiff Nightly Sync" '
                         f'/ST 02:00 /TR {self._sync_command()}')
        manual.configure(state='readonly')
        manual.pack(fill='x')

        bar = tk.Frame(top, bg=_SM_BG)
        bar.pack(fill='x', pady=(8, 12))
        tk.Button(bar, text='Close', command=top.destroy, bg='#e6e8ec',
                  relief='flat', padx=14, pady=4, cursor='hand2').pack(side='right', padx=14)
        _center_over(self.top, top)
        self.top.wait_window(top)


def main() -> None:
    root = Tk()
    CompareApp(root)
    # On macOS a Tk window launched from a Terminal child process opens behind
    # everything else. Force it to the front, then drop the topmost flag so the
    # user can still move other windows on top of it normally.
    root.lift()
    root.attributes('-topmost', True)
    root.after(300, lambda: root.attributes('-topmost', False))
    try:
        root.focus_force()
    except Exception:
        pass
    root.mainloop()


if __name__ == '__main__':
    main()
