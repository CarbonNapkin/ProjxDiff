# Changelog

All notable changes to Projx Diff are documented in this file.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [1.2.3] - 2026-08-04

### Changed

- **Main window restyled to match the Manage Nightly Sync dialog** — a header
  bar and the shared flat palette (accent Compare button, consistent entry and
  button styling) so the whole app reads as one cohesive piece. Plain-tk, so it
  still renders on older macOS Tk (button fills may fall back to native styling
  on macOS Aqua, which is expected).

## [1.2.2] - 2026-08-04

### Changed

- **Manage Nightly Sync table gains sorting and disposition filters.** Column
  headers (Project / Path / Modified / Last saved by / Disposition) are
  click-to-sort with an asc/desc indicator, and a segmented **All / New /
  Track / Ignore** control filters the list by disposition alongside the text
  filter. The per-row disposition control is now a flat, color-coded pill
  (no boxy border). The internal `pending` disposition is shown as **New** in
  the UI while remaining `pending` in the census data the engine reads.

## [1.2.1] - 2026-08-04

### Changed

- **Manage Nightly Sync shows every project, not just pending ones.** The
  Tools ▸ Manage Nightly Sync dialog now lists all track/pending/ignore
  projects in one table — name, source path, last-modified date, last saver,
  and an inline disposition dropdown — with a filter box and a flat, lighter
  layout. Previously only newly-discovered (pending) projects appeared, so an
  already-triaged config looked empty and confusing. The config-file picker
  now opens at `C:\ProjxArchive` (falling back to a per-user `ProjxArchive`,
  then the home folder). Plain-tk throughout, so it renders on older macOS Tk.

## [1.2.0] - 2026-08-04

### Added

- **The nightly sync pipeline is now part of the app.** The engine moved into
  the package (`dw_compare/sync.py`, `census.py`, `dashboard.py`) with new
  CLI modes — `--sync`, `--census`, `--dashboard` — so a packaged
  `ProjxDiff.exe` can run the whole pipeline with no Python install. The
  `scripts/nightly_sync/` scripts remain as back-compat wrappers; existing
  scheduled tasks keep working unchanged.
- **Managed census of projects and users** (`data_dir/census.json`).
  Discovery replaces hand-written config: new projects auto-register as
  *pending* (they sync immediately — the pipeline never waits on a human)
  and get a Track/Ignore decision later; every DriveWorks display name ever
  seen is collected for identity mapping; same-name collisions sync only the
  registered path and flag the copy. Scans and syncs only ever add entries —
  a human's mapping or disposition is never overwritten. Legacy
  `author_aliases` configs are imported automatically.
- **Needs-attention panel on the dashboard** — pending projects, unmapped
  users, and name conflicts, with the quiet steady state being no panel at
  all. The sync log prints the same summary nightly.
- **Manage Nightly Sync GUI** (Tools menu): triage pending projects and type
  identities for unmapped users in a form instead of editing JSON.
- **Retroactive metrics healing**: mapping a user updates all past metrics
  rows recorded under the raw display name, so per-user charts read as if
  the mapping had always existed. Git history is deliberately not rewritten —
  old commits keep the raw name the file carried at the time.
- Census CLI management: `--census --map "Raw=Name <email>" --track NAME
  --ignore NAME [--no-scan]`.
- A project file that moves on the share now follows automatically (the
  census updates its registered path) instead of archiving a duplicate.

## [1.1.2] - 2026-08-03

### Added

- **Automatic per-user attribution from the project file.** With
  `derive_author_from_file: true`, a changed project's commit is authored by
  the DriveWorks user who last saved it — read from `DWCurrentUserDisplayName`
  in `designMaster.xml` — instead of requiring a hand-maintained `owners` map.
  An `author_aliases` map collapses display-name spelling variants (e.g.
  `TusharShewale`/`Tushar`) onto one `"Name <email>"` identity; an explicit
  `owners` entry still overrides. Credits the last saver, so multiple editors
  between two runs land under one name. Covered by tests.

## [1.1.1] - 2026-08-03

### Added

- **`exclude` config option for the nightly sync** — a list of case-insensitive
  globs matched against each project's path relative to `source_dir` (`*`
  spans `/`); any match is skipped. Lets a deployment drop archive/backup and
  duplicate copies so the synced set has a unique filename per project, which
  matters because the archive keys one top-level folder per project *name* —
  two kept files sharing a name would otherwise collide. Covered by a test.

### Fixed

- **Nightly sync no longer crashes on a duplicate project name.** When two
  source files shared a filename stem, the per-project staging `mkdir` raised
  an unhandled `FileExistsError` and killed the entire run. The duplicate is
  now skipped with a recorded error (visible in the run's `errors` and exit
  code 1) so every other project still syncs. Covered by a test.

## [1.1.0] - 2026-08-03

### Added

- **JSON output for scripting and change tracking** — `--format json` (or
  `--format both` for the HTML report and JSON side by side). Emits a
  machine-readable document with a versioned schema: per-category
  added/removed/modified/unchanged counts, plus a flat change list with one
  record per changed element and field-level details carrying raw old/new
  formulas. Unchanged elements are counted but not listed. JSON-only runs
  never open a browser, so the CLI can run headless in scheduled jobs.
  `build_diff` is exported from the package for library use.
- The JSON differ reuses the same change-detection helpers as the HTML
  report, and the test suite locks both outputs to identical per-category
  counts so they cannot drift apart.
- **Nightly sync script** (`scripts/nightly_sync/`) — archives every
  `.driveprojx` from a network share into a git repo each night (one commit
  per changed project, authored by the project's owner when configured),
  records per-category and per-element change rows in a SQLite metrics
  database, and writes dated HTML/JSON diff reports for drill-down. Unchanged
  projects produce nothing; new projects are archived without exploding the
  metrics. Stdlib-only, Windows Task Scheduler-ready, covered by an
  end-to-end lifecycle test.
- **Work-metrics dashboard** (`scripts/nightly_sync/dashboard.py`) —
  regenerated at the end of every sync run: a self-contained static HTML page
  (inline SVG charts, hover tooltips, light + dark mode, no external
  resources) with changes-per-day over 60 days, top projects / users /
  categories over 30 days, and a recent-changes table linking into the dated
  drill-down reports.

## [1.0.7] - 2026-06-22

### Changed

- **Renamed to Projx Diff.** The tool is no longer named after DriveWorks; it
  remains an independent comparison tool for DriveWorks™ projects. Build
  artifacts are now `ProjxDiff.app` / `ProjxDiff.exe`.
- Added a trademark notice and a disclaimer making explicit that Projx Diff is
  **not affiliated with, endorsed by, or tested by DriveWorks™ Ltd**.

## [1.0.6] - 2026-06-09

### Changed

- **The log pane is hidden by default** and toggled from **View ▸ Show Log**.
  The window is compact unless you open the log.
- **A status line shows the full output path** (wrapped, so it's all visible)
  and live progress — "Report will be saved to: …", "Comparing…", and a green
  "Report saved to: …" or red failure notice when it finishes. With the log
  hidden, a failed comparison also raises a dialog so it can't be missed.
- File pickers scroll to show the end of long paths (the filename stays in view).

## [1.0.5] - 2026-06-09

### Fixed

- **The app's version metadata now matches the running app.** The build read the
  version by scanning `__init__.py`, which only re-exports it, so the macOS
  bundle's version silently fell back to a default (Get Info showed `1.0.0`
  while About showed the real version). The build now reads `_version.py`, and
  the Windows `.exe` gets a proper version resource in its file properties too.

### Changed

- The project picker only accepts `.driveprojx` files. The **Folder…** option
  was removed (DriveWorks projects are stored as `.driveprojx`, not loose
  folders), and the file dialog shows a single `DriveWorks project (*.driveprojx)`
  filter with no "All files" fallback.
- **Help → How to Use** now opens concise in-app usage instructions instead of
  launching the GitHub repository in a browser.
- The "Save report as…" dialog no longer shows a file-type chooser; the report
  is always HTML and keeps the `.html` extension.

## [1.0.4] - 2026-06-09

### Fixed

- **The GUI no longer crashes with "Read-only file system" when launched from a
  double-clicked app** (macOS and Windows). The default report path was the
  *relative* `dw_comparison.html`, which resolved against the process working
  directory — which can be read-only for a double-clicked app (`/` on macOS via
  Finder; `C:\Windows\System32` or `Program Files` on Windows) — so clicking
  Compare with the default output failed. The default is now an absolute path in
  your **Downloads** (or home) folder, shown in full, and any bare filename you
  enter is anchored there rather than the working directory.

## [1.0.3] - 2026-06-09

### Fixed

- **Search and status filters no longer hide matches inside grouped sections.**
  In Forms, Specification Macros, Documents, and Calculation/Lookup tables, a
  search term (or a status filter) that matched a *row* was hidden whenever the
  term wasn't also in the section's header. Group headers now follow their rows:
  a group shows whenever any of its rows is visible.
- **Reports open reliably on Windows.** Auto-open built the `file://` URL by
  string concatenation, which produced a malformed URL on Windows (drive
  letters / backslashes) and left spaces unescaped. It now uses `Path.as_uri()`.
- `.driveprojx` temp-directory cleanup now drains its tracking list, so repeated
  comparisons in the GUI don't re-attempt deletion of already-removed folders.
- Lookup tables with **duplicate column-header names** are now diffed per
  column. Columns were keyed by header name, so repeated names collapsed to the
  last one and could miss a change or attribute it to the wrong column; columns
  are now matched positionally.

### Removed

- The **Special Variables** section. It added noise without signal for project
  comparison, so it is no longer parsed or shown.
- The **Flip Direction** button. The report already shows additions and removals
  side by side, so flipping only relabeled and recolored — while being a
  recurring source of subtle display bugs. Removed in favor of re-running with
  the projects swapped when the other framing is needed.

### Added

- Loading a project with **multiple/nested specifications** now prints a notice
  that their contents are merged into one view (identically named items across
  specifications can overwrite each other), instead of merging silently.
- A pytest test suite (`tests/`) covering the comparison layer, parsers
  (including the real-world TDM `designMaster` variable format, cross-file
  category-GUID resolution, spec-macro property binding, and the `.driveprojx`
  zip-slip guard), report rendering, the CLI entry point, and the update check,
  with a regression test for every bug fixed in 1.0.1, 1.0.2, and the items above.
- A **Tests** GitHub Actions workflow that runs pytest on every push and pull
  request (Python 3.10 and 3.12); release builds now run the tests first.

## [1.0.2] - 2026-06-09

### Added

- Variables table now shows a **Category** column (resolved category name).

### Fixed

- Document **Type** changes are now detected — a type-only change previously
  showed as Unchanged.
- Documents now show a **rule-level breakdown** (which rule changed and how)
  instead of only a rule count.
- Component Tasks now show a rule-level breakdown under each modified task.
- Variables and Constants now also compare store name and comment (shown as
  muted sub-notes); Calculation Tables now compare row count.

### Changed

- Each report section renders independently — one section hitting unexpected
  data degrades to a placeholder instead of failing the whole report.
- A malformed (non-numeric) calculation-table RowIndex is ignored rather than
  aborting the parse.
- `.driveprojx` extraction guards against path traversal (zip slip).
- GUI: extracted temp dirs are cleaned up after each comparison, and the
  launch-time update check no longer errors if the window is closed first.
- Removed the unused `CalcTableCell` model.

## [1.0.1] - 2026-06-09

### Added

- A free, fail-silent update check. On launch (GUI) and after a CLI run, the app
  queries the GitHub Releases API and shows a "newer version available" notice
  linking to the download page. It only notifies — it never downloads or
  installs — and is silent when offline.

### Fixed

- **Flip Direction** no longer corrupts the summary cards. It was swapping the
  card *labels* instead of the counts, leaving a green card labelled "Removed".
  It now swaps the Added/Removed counts and keeps labels and colors fixed.
- **Inline diffs are now token-level** instead of word-level. Because DriveWorks
  formulas rarely contain spaces, any change used to re-highlight the entire
  formula; now only the changed token (number, identifier, operator) lights up.
- **Flip Direction is now consistent for lookup-table grids** — per-cell and
  per-column highlight classes swap, and "New"/"Old" column badges flip too.
- **Duplicate task / component-task names no longer collapse.** Specification
  macro tasks (keyed by title + type) and component tasks (keyed by name +
  component) now disambiguate repeats so a change in a same-named task is not
  silently dropped.
- **No more no-op "Modified" rows.** Navigation steps and form/control
  properties that differ only in a non-displayed field (e.g. the IsStatic flag
  with an unchanged value) are now treated as unchanged.
- **Status filtering no longer orphans grouped rows.** A group's identity row
  (control/task/column name) stays visible whenever any of its child rows pass
  the filter.
- Removed em dashes from the report (the "unchanged" status marker).

## [1.0.0] - 2026-05-15

First public release. Compares two DriveWorks projects and generates a
self-contained HTML diff report.

### Added

- Direct `.driveprojx` support, files are auto-extracted to a temp directory.
- Recursive scanning, all `project.xml`, `designMaster.xml`, `componentTasks.xml`,
  and `.tdm` files in nested folders are picked up.
- Sections covered in the report:
  - Variables (with resolved category names)
  - Constants
  - Special Variables
  - Calculation Tables, including row-level rules
  - Component Tasks
  - Documents
  - Lookup Tables, rendered as cell-highlighted grids
  - Data Tables
  - Specification Macros, per-task and per-property
  - Navigation Steps
  - Forms, form-level rules plus per-control property formulas
- Hierarchical diff rendering. Forms, Macros, and Calculation Tables emit
  grouped rows where the parent identifier (control, task, column) appears
  once per group, with a visual separator between groups.
- Interactive HTML report:
  - Sticky filter bar with status filters, search, flip-direction, and toggles
    for "Show unchanged sections" and "Show unchanged lookup rows".
  - Sticky per-form and per-table sub-headers stay pinned while you scroll.
  - Auto-collapsed sections for empty diffs, click to expand.
- Three launch modes:
  - CLI, `python -m dw_compare ...`
  - GUI, `python -m dw_compare --gui` or double-click `run_compare.command`
    on macOS
  - Auto-detect, run with no args inside a folder containing two projects
- Tkinter GUI with file and folder pickers, live log pane, and a
  background worker so the window stays responsive.
- Help menu with Documentation link and an About dialog.
- `--version` flag on the CLI.
- Self-contained HTML output suitable for sharing by email or hosting on
  an internal share.

[1.0.7]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.7
[1.0.6]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.6
[1.0.5]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.5
[1.0.4]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.4
[1.0.3]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.3
[1.0.2]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.2
[1.0.1]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.1
[1.0.0]: https://github.com/CarbonNapkin/ProjxDiff/releases/tag/v1.0.0
