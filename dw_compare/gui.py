"""
Simple Tkinter GUI for Projx Diff (a DriveWorks project comparison tool).

Lets the user pick two .driveprojx projects, choose an output path, and run a
comparison without using the command line.
"""

from __future__ import annotations

import queue
import sys
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
from .update_check import check_for_update, RELEASES_PAGE

try:
    from .__main__ import resolve_input, cleanup_temp_dirs, resolve_output_path
except ImportError:
    resolve_input = None  # type: ignore
    cleanup_temp_dirs = None  # type: ignore
    resolve_output_path = None  # type: ignore


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
        self._geom_compact = '760x392'
        self._geom_with_log = '760x620'
        root.geometry(self._geom_compact)
        self.show_log = BooleanVar(value=False)
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

    def _show_help(self) -> None:
        """Concise in-app usage help, so the menu offers real guidance rather
        than just opening a code repository in the browser."""
        top = tk.Toplevel(self.root)
        top.title('How to Use')
        top.resizable(False, False)
        bg = '#f4f4f4'
        top.configure(bg=bg)

        steps = (
            "Compare two DriveWorks™ projects into one shareable HTML report.\n\n"
            "1.  Old project — click Browse… and pick the baseline .driveprojx.\n"
            "2.  New project — click Browse… and pick the version to compare.\n"
            "3.  Output — defaults to your Downloads folder; change it with\n"
            "      Save as… if you like.\n"
            "4.  Click Compare. The report opens in your browser when it finishes.\n\n"
            "The report groups every change (added / removed / modified) by\n"
            "section — variables, tables, component tasks, documents, macros,\n"
            "navigation, and form rules — with search and status filters on top.\n\n"
            "Everything runs locally; your project files never leave your computer."
        )
        tk.Label(top, text='How to Use Projx Diff', bg=bg,
                 font=('TkDefaultFont', 14, 'bold')).pack(padx=16, pady=(14, 6), anchor='w')
        tk.Label(top, text=steps, bg=bg, justify='left', anchor='w').pack(padx=16, pady=(0, 8), anchor='w')

        link = tk.Label(top, text='More at ' + __url__, bg=bg, fg='#3f51b5', cursor='hand2')
        link.pack(padx=16, pady=(0, 4), anchor='w')
        link.bind('<Button-1>', lambda _e: webbrowser.open(__url__))

        tk.Button(top, text='Close', command=top.destroy).pack(pady=(8, 12))

        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f'+{max(0, x)}+{max(0, y)}')
        top.transient(self.root)
        top.grab_set()

    def _manage_sync(self) -> None:
        """Tools > Manage Nightly Sync: triage the census — decide pending
        projects' dispositions and map unmapped DriveWorks user names. Saving
        writes census.json and retroactively heals metrics rows recorded
        under raw names."""
        cfg_path = filedialog.askopenfilename(
            title='Select the nightly sync config',
            initialdir=_default_config_dir(),
            filetypes=[('Sync config (JSON)', '*.json')])
        if not cfg_path:
            return
        try:
            from .sync import load_config
            from . import census as census_mod
            cfg = load_config(Path(cfg_path))
            cpath = census_mod.census_path(cfg)
            census = census_mod.load_census(cpath)
            census_mod.seed_from_config(census, cfg)
        except (SystemExit, Exception) as e:  # noqa: B014 (SystemExit from config validation)
            messagebox.showerror('Manage Nightly Sync', f'Could not load config:\n{e}')
            return
        _SyncManager(self.root, cfg, cpath, census)

    def _show_about(self) -> None:
        """Custom About window. messagebox.showinfo works but a Toplevel
        gives us a clickable repo link and slightly nicer typography."""
        top = tk.Toplevel(self.root)
        top.title('About')
        top.resizable(False, False)
        bg = '#f4f4f4'
        top.configure(bg=bg)

        pad = {'padx': 16, 'pady': 4}
        tk.Label(top, text='Projx Diff', bg=bg,
                 font=('TkDefaultFont', 14, 'bold')).pack(**pad, anchor='w')
        tk.Label(top, text=f'Version {__version__}', bg=bg).pack(padx=16, pady=(0, 8), anchor='w')
        tk.Label(top, text=f'© {__author__}', bg=bg).pack(padx=16, anchor='w')
        tk.Label(top, text=f'Licensed under {__license__}', bg=bg, fg='#555').pack(padx=16, anchor='w')
        tk.Label(top, text='An independent tool. Not affiliated with, endorsed by, or\n'
                          'tested by DriveWorks™ Ltd. DriveWorks™ is a trademark of\n'
                          'DriveWorks Ltd.',
                 bg=bg, fg='#777', justify='left').pack(padx=16, pady=(8, 0), anchor='w')

        link = tk.Label(top, text=__url__, bg=bg, fg='#3f51b5', cursor='hand2')
        link.pack(padx=16, pady=(8, 4), anchor='w')
        link.bind('<Button-1>', lambda _e: webbrowser.open(__url__))

        tk.Button(top, text='Close', command=top.destroy).pack(pady=(8, 12))

        # Center the dialog over the main window.
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f'+{max(0, x)}+{max(0, y)}')
        top.transient(self.root)
        top.grab_set()

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
        button('Browse…', lambda: self._pick_file(self.old_path, self.old_entry)).grid(row=0, column=2, columnspan=2, sticky='ew', **pad)

        label('New project:').grid(row=1, column=0, sticky='w', **pad)
        self.new_entry.grid(row=1, column=1, sticky='ew', **pad)
        button('Browse…', lambda: self._pick_file(self.new_path, self.new_entry)).grid(row=1, column=2, columnspan=2, sticky='ew', **pad)

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

        # Status line — wraps so the full output path is always visible, and
        # carries run progress/results now that the log is hidden by default.
        self.status_label = tk.Label(frm, text='', bg=bg, anchor='w', justify='left',
                                     wraplength=660)
        self.status_label.grid(row=4, column=0, columnspan=4, sticky='w', padx=8, pady=(2, 2))

        # Filled by a background update check (notify-only; see _check_updates).
        self.update_label = tk.Label(frm, text='', bg=bg, fg='#3f51b5', anchor='w', cursor='hand2')
        self.update_label.grid(row=5, column=0, columnspan=4, sticky='w', padx=8, pady=(0, 6))

        # Log pane — hidden by default; toggled via View ▸ Show Log.
        self._log_row = 6
        self.log_label = label('Log:')
        self.log_label.grid(row=self._log_row, column=0, sticky='nw', **pad)
        self.log_box = ScrolledText(frm, height=14, wrap='word', state=DISABLED, bd=1, relief='solid')
        self.log_box.grid(row=self._log_row, column=1, columnspan=3, sticky='nsew', **pad)

        frm.columnconfigure(1, weight=1)

        # Apply the initial (hidden) log state and seed the status line with the
        # full destination; keep the status in sync when the output path changes.
        self._apply_log_visibility()
        self.output_path.trace_add('write', lambda *a: self._update_status_idle())
        self._update_status_idle()

    def _apply_log_visibility(self) -> None:
        """Show or hide the log pane (View ▸ Show Log) and resize the window."""
        if self.show_log.get():
            self.log_label.grid()
            self.log_box.grid()
            self._frm.rowconfigure(self._log_row, weight=1)
            self.root.geometry(self._geom_with_log)
        else:
            self.log_label.grid_remove()
            self.log_box.grid_remove()
            self._frm.rowconfigure(self._log_row, weight=0)
            self.root.geometry(self._geom_compact)

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

    def _pick_file(self, target: StringVar, entry_widget=None) -> None:
        path = filedialog.askopenfilename(
            title='Select project file',
            filetypes=PROJX_FILETYPES,
        )
        if path:
            target.set(path)
            if entry_widget is not None:
                entry_widget.xview_moveto(1.0)  # show the filename end, not the start

    def _pick_output(self) -> None:
        current = Path(self.output_path.get().strip() or 'dw_comparison.html')
        # The report is always HTML, so no file-type chooser is shown; the
        # default extension keeps the .html suffix.
        path = filedialog.asksaveasfilename(
            title='Save report as',
            defaultextension='.html',
            initialdir=str(current.parent) if current.is_absolute() else '',
            initialfile=current.name,
        )
        if path:
            self.output_path.set(path)
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

        # Clear log, disable button, show progress in the status line.
        self.log_box.configure(state=NORMAL)
        self.log_box.delete('1.0', END)
        self.log_box.configure(state=DISABLED)
        self.compare_btn.configure(state=DISABLED, text='Comparing…')
        self._busy = True
        self._set_status('⏳ Comparing…', '#3f51b5')

        self._worker = threading.Thread(
            target=self._run_compare,
            args=(old, new, output, open_browser),
            daemon=True,
        )
        self._worker.start()

    def _run_compare(self, old: Path, new: Path, output: Path, open_browser: bool) -> None:
        writer = _QueueWriter(self._log_queue)
        prev_stdout = sys.stdout
        sys.stdout = writer
        saved = None
        error = None
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

            print('Generating comparison report...')
            html = generate_html_report(old_proj, new_proj, old_name, new_name)

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
            self.root.after(0, lambda: self._on_done(saved=saved, error=error))

    def _on_done(self, saved: str | None = None, error: str | None = None) -> None:
        self._busy = False
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
            self._set_status('✅ Report saved to:  ' + saved + note, '#1b7a3d')
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
        self.update_label.configure(text=f'⬆ Update available: v{newer} — click to download')
        self.update_label.bind('<Button-1>', lambda _e: webbrowser.open(RELEASES_PAGE))


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

    def __init__(self, parent, cfg: dict, cpath: Path, census: dict):
        from . import census as census_mod
        self._census_mod = census_mod
        self.cfg = cfg
        self.cpath = cpath
        self.census = census

        self.top = tk.Toplevel(parent)
        self.top.title('Manage Nightly Sync')
        self.top.configure(bg=_SM_BG)
        self.top.geometry('960x640')
        self.top.minsize(780, 460)

        self._build_header()
        self._build_toolbar()
        # Bottom-anchored pieces first so the table (packed last) fills the
        # middle and grows with the window.
        self._build_footer()
        self._build_users_and_conflicts()
        self._build_table()

        self.top.transient(parent)

    # ------------------------------------------------------------ sections ----

    def _build_header(self) -> None:
        header = tk.Frame(self.top, bg=_SM_HEADER_BG)
        header.pack(side='top', fill='x')
        tk.Label(header, text='Manage Nightly Sync', bg=_SM_HEADER_BG, fg=_SM_HEADER_FG,
                 font=('TkDefaultFont', 15, 'bold')).pack(anchor='w', padx=18, pady=(12, 0))
        tk.Label(header, text=f'{self.cfg.get("source_dir", "")}  ·  {self.cpath}',
                 bg=_SM_HEADER_BG, fg=_SM_HEADER_SUB,
                 font=('TkDefaultFont', 9)).pack(anchor='w', padx=18, pady=(1, 12))

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.top, bg=_SM_BG)
        bar.pack(side='top', fill='x', padx=16, pady=(12, 0))
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
        tk.Button(bar, text='Save', command=self._save, bg=_SM_ACCENT, fg='#ffffff',
                  activebackground=_SM_ACCENT_ACT, activeforeground='#ffffff',
                  relief='flat', font=('TkDefaultFont', 11, 'bold'),
                  padx=20, pady=6, cursor='hand2').pack(side='right')
        tk.Button(bar, text='Cancel', command=self.top.destroy, bg='#e6e8ec',
                  relief='flat', padx=16, pady=6, cursor='hand2').pack(side='right', padx=8)

    def _build_table(self) -> None:
        # Real configs always carry source_dir (load_config validates it); guard
        # anyway so the dialog degrades to '—' metadata rather than crashing.
        source = Path(self.cfg.get('source_dir', ''))

        outer = tk.Frame(self.top, bg=_SM_CARD, highlightbackground=_SM_DIVIDER,
                         highlightthickness=1)
        outer.pack(side='top', fill='both', expand=True, padx=16, pady=(4, 6))
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
        for col, weight in enumerate(self._COL_WEIGHT):
            lbl = tk.Label(table, bg=_SM_CARD, fg=_SM_MUTED, anchor='w',
                           font=('TkDefaultFont', 8, 'bold'), cursor='hand2')
            lbl.grid(row=0, column=col, sticky='w', padx=10, pady=(10, 4))
            lbl.bind('<Button-1>', lambda _e, c=col: self._sort_by(c))
            self._header_labels.append(lbl)
            table.columnconfigure(col, weight=weight)
        tk.Frame(table, bg=_SM_DIVIDER, height=1).grid(
            row=1, column=0, columnspan=len(self._COLS), sticky='ew', padx=6)

        self.proj_vars: dict[str, StringVar] = {}
        self._rows: list = []
        for name in sorted(self.census['projects'].keys()):
            entry = self.census['projects'][name]
            rel = entry.get('path', '')
            modified, saver = self._file_meta(source / rel)
            var = StringVar(value=self._DISP_TO_LABEL.get(entry.get('disposition', 'pending'), 'New'))
            self.proj_vars[name] = var
            cells = [
                tk.Label(table, text=name, bg=_SM_CARD, fg=_SM_TEXT, anchor='w',
                         font=('TkDefaultFont', 10, 'bold')),
                tk.Label(table, text=self._ellipsize(rel, 54), bg=_SM_CARD, fg=_SM_MUTED, anchor='w'),
                tk.Label(table, text=modified, bg=_SM_CARD, fg=_SM_TEXT, anchor='w'),
                tk.Label(table, text=saver, bg=_SM_CARD, fg=_SM_TEXT, anchor='w'),
                self._disposition_menu(table, var),
            ]
            self._rows.append({'name': name, 'rel': rel, 'modified': modified,
                               'saver': saver, 'var': var, 'cells': cells,
                               'search': f'{name} {rel} {saver}'.lower()})

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
        if col == 0:
            return row['name'].lower()
        if col == 1:
            return row['rel'].lower()
        if col == 2:  # missing dates sort to the end
            return (row['modified'] == '—', row['modified'])
        if col == 3:
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
            lbl.configure(text=self._COLS[c].upper() + (arrow if c == self._sort_col else ''))

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
        self.top.destroy()


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
