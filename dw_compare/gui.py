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
        # Compact by default; the log pane is hidden (View ▸ Show Log) and the
        # window grows to fit it when shown.
        self._geom_compact = '720x320'
        self._geom_with_log = '720x560'
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
        pad = {'padx': 8, 'pady': 6}
        bg = '#f4f4f4'
        self.root.configure(bg=bg)

        frm = tk.Frame(self.root, bg=bg, padx=12, pady=12)
        frm.pack(fill='both', expand=True)

        def label(text, **kw):
            return tk.Label(frm, text=text, bg=bg, anchor='w', **kw)

        def entry(var):
            return tk.Entry(frm, textvariable=var, highlightthickness=1, relief='solid', bd=1)

        def button(text, cmd):
            return tk.Button(frm, text=text, command=cmd, highlightthickness=0)

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
            variable=self.open_in_browser, bg=bg, anchor='w',
            highlightthickness=0,
        ).grid(row=3, column=1, sticky='w', **pad)

        self.compare_btn = tk.Button(
            frm, text='Compare', command=self._on_compare,
            highlightthickness=0, font=('TkDefaultFont', 13, 'bold'),
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
    """Toplevel that renders the census triage: pending projects get a
    Track / Ignore decision, unmapped users get an identity field, and name
    conflicts are listed with a hint. Saving persists census.json and heals
    the metrics DB."""

    def __init__(self, parent, cfg: dict, cpath: Path, census: dict):
        from . import census as census_mod
        self._census_mod = census_mod
        self.cfg = cfg
        self.cpath = cpath
        self.census = census

        bg = '#f4f4f4'
        self.top = tk.Toplevel(parent)
        self.top.title('Manage Nightly Sync')
        self.top.configure(bg=bg)
        self.top.geometry('640x520')

        # Scrollable body: a canvas hosting a frame, so long triage lists work.
        canvas = tk.Canvas(self.top, bg=bg, highlightthickness=0)
        scroll = tk.Scrollbar(self.top, orient='vertical', command=canvas.yview)
        body = tk.Frame(canvas, bg=bg, padx=14, pady=10)
        body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=body, anchor='nw')
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

        def heading(text):
            tk.Label(body, text=text, bg=bg, font=('TkDefaultFont', 12, 'bold'),
                     anchor='w').pack(fill='x', pady=(10, 2))

        def note(text):
            tk.Label(body, text=text, bg=bg, fg='#777', justify='left',
                     anchor='w', wraplength=560).pack(fill='x')

        self.proj_vars: dict[str, StringVar] = {}
        pending = census_mod.pending_projects(census)
        heading(f'New projects awaiting disposition ({len(pending)})')
        if pending:
            note('Track = archive and measure nightly. Ignore = stop syncing '
                 '(history already captured is kept).')
            for name, path in pending:
                row = tk.Frame(body, bg=bg)
                row.pack(fill='x', pady=2)
                var = StringVar(value='pending')
                self.proj_vars[name] = var
                tk.Label(row, text=name, bg=bg, width=24, anchor='w',
                         font=('TkDefaultFont', 11, 'bold')).pack(side='left')
                for label, val in (('Decide later', 'pending'), ('Track', 'track'),
                                   ('Ignore', 'ignore')):
                    tk.Radiobutton(row, text=label, value=val, variable=var,
                                   bg=bg, highlightthickness=0).pack(side='left')
                tk.Label(body, text=path, bg=bg, fg='#999', anchor='w').pack(fill='x')
        else:
            note('None — every project has a disposition.')

        self.user_entries: dict[str, tk.Entry] = {}
        unmapped = census_mod.unmapped_users(census)
        heading(f'Unmapped DriveWorks users ({len(unmapped)})')
        if unmapped:
            note('Fill in as  Name <email>  — past metrics recorded under the '
                 'raw name are updated too.')
            for raw in unmapped:
                row = tk.Frame(body, bg=bg)
                row.pack(fill='x', pady=2)
                tk.Label(row, text=raw, bg=bg, width=24, anchor='w',
                         font=('TkDefaultFont', 11, 'bold')).pack(side='left')
                e = tk.Entry(row, highlightthickness=1, relief='solid', bd=1)
                e.pack(side='left', fill='x', expand=True)
                self.user_entries[raw] = e
        else:
            note('None — every user name seen so far is mapped.')

        conflicts = census.get('conflicts', [])
        if conflicts:
            heading(f'Name conflicts ({len(conflicts)})')
            note('Two source files share a project name; only the registered '
                 'path syncs. Fix the share (rename/remove the copy) or add '
                 'an exclude pattern in the config.')
            for c in conflicts:
                tk.Label(body, text=f'{c.get("project", "")}: {c.get("path", "")}  '
                                    f'(registered: {c.get("registered", "")})',
                         bg=bg, fg='#999', anchor='w', wraplength=560,
                         justify='left').pack(fill='x')

        bar = tk.Frame(self.top, bg=bg, pady=8)
        bar.pack(side='bottom', fill='x')
        tk.Button(bar, text='Cancel', command=self.top.destroy).pack(side='right', padx=8)
        tk.Button(bar, text='Save', font=('TkDefaultFont', 12, 'bold'),
                  command=self._save).pack(side='right')

        self.top.transient(parent)

    def _save(self) -> None:
        census_mod = self._census_mod
        for name, var in self.proj_vars.items():
            self.census['projects'][name]['disposition'] = var.get()
        mapped = 0
        for raw, entry in self.user_entries.items():
            ident = entry.get().strip()
            if ident:
                self.census['users'][raw] = ident
                mapped += 1

        try:
            census_mod.save_census(self.cpath, self.census)
            healed = 0
            db_path = Path(self.cfg['data_dir']) / 'metrics.sqlite'
            if mapped and db_path.is_file():
                import sqlite3
                conn = sqlite3.connect(db_path, isolation_level=None)
                try:
                    healed = census_mod.heal_metrics(conn, self.census)
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
