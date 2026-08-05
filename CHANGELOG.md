# Changelog

All notable changes to Projx Diff are documented in this file.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [1.5.5] - 2026-08-05

### Added

- **Per-side database logins in the GUI.** When the old and new group
  databases use different SQL logins, check "Different login for the old
  database" in the Database Options panel to reveal a second
  username/password pair for the old side; the main pair then applies to
  the new side. As always, usernames are remembered between sessions and
  passwords never are.

### Changed

- **Windows installs launch without unpacking to Temp.** The installer
  now lays the app's runtime files down in Program Files (a "onedir"
  build) instead of shipping a self-extracting exe, which eliminates the
  antivirus race behind the "Failed to load Python DLL" error some
  machines hit on first launch after an update — and makes startup
  faster. The portable single-file ProjxDiff.exe is unchanged.

## [1.5.4] - 2026-08-05

### Fixed

- **A hand-edited settings file can no longer stop the app from
  launching.** A wrong-typed value in `~/.projxdiff` (say, `last_dirs`
  as a string instead of an object) crashed the app before the window
  appeared; every value is now read tolerantly and junk is treated as
  absent.
- **The shared `DW_SQL_PASSWORD` environment variable now works from
  the CLI**, as the README always said: `DW_SQL_PASSWORD_OLD`/`_NEW`
  win when set, and the shared variable fills in when both sides use
  the same login. Previously only the per-side variables were read.

### Added

- **`--doctor` self-check** (hidden flag): prints version, platform,
  and database-connectivity readiness (pyodbc + ODBC drivers). Release
  builds now run it in CI, so a Windows build missing pyodbc can never
  ship again (the 1.5.1 bug class).

## [1.5.3] - 2026-08-05

### Changed

- **The app now creates its settings file on first launch** (`~/.projxdiff`,
  `%USERPROFILE%\.projxdiff` on Windows) with `"enable_db": false`, so
  enabling the Database Options panel is a one-word edit instead of
  creating the file by hand.
- **Browse folders are remembered across sessions** — each picker (old
  project, new project, output) reopens in the folder you last used.
- **In-app links now go to the download page** — the Help and About
  links open base10consultants.com/tools/projx-diff instead of the
  GitHub repository. (Update notices already did.)

## [1.5.2] - 2026-08-04

### Changed

- **The GUI's Database Options panel is now behind a feature flag**, off by
  default — most installs have no DriveWorks group database and shouldn't
  see SQL connection fields. Enable it with `{"enable_db": true}` in the
  settings file (`~/.projxdiff`, `%USERPROFILE%\.projxdiff` on Windows).
  Machines that already saved a database server keep the panel
  automatically; an explicit `"enable_db": false` always hides it. The
  CLI `--old-db-*`/`--new-db-*` flags are unaffected — using them is
  opting in. Includes the 1.5.1 fix (pyodbc bundled in the Windows exe).

## [1.5.1] - 2026-08-04

### Fixed

- **The Windows exe now bundles pyodbc**, so database name resolution
  (Models / Rule Changes) works from the packaged binary out of the box.
  The 1.5.0 exe silently fell back to raw GUIDs because the CI build
  environment lacked pyodbc at bundle time — the app degraded cleanly, but
  the advertised feature required a separate Python install. (The system
  ODBC driver is still a prerequisite, as on any SQL Server client.)

## [1.5.0] - 2026-08-04

### Added

- **The report is redesigned** — the "ugly blue" is gone. A sidebar shell
  puts the diff totals and a per-section navigator (with live change
  counts) in a left rail; sections are cards in the main pane. **Light and
  dark themes** with an Auto/Light/Dark toggle: Auto follows the viewer's
  OS, an explicit choice persists in the browser. Status colors are
  **colorblind-safe by design** — added is blue, removed is orange,
  modified is violet-gray (distinct under red-green color blindness), and
  status is never color alone: every badge is labeled, row badges carry
  +/−/~ glyphs. All interactive behavior carries over unchanged (search,
  status filters, unchanged toggles, expand/collapse, draggable columns).
  The work dashboard gains the same Auto/Light/Dark toggle.
- **Report UI test suite** — the rail, theming scopes, colorblind
  guarantees, escaping of the new surfaces, and every interactive hook are
  pinned by tests, and the report's embedded JavaScript now runs through
  `node --check` in the suite so a script-block syntax break can never
  ship silently.

- **Model and model-rule diffing in the real report** (integrated from Wade
  Anderson's Project Diff Tool work; field-verified against live DriveWorks
  22 environments at two sites). Three new report sections:
  **Component Sets** (named model factories with their generation rules —
  free from project.xml, no database needed), **Models** (the resolved
  captured-file inventory, matched by *file name* — not folder location and
  not database id, since the same real file can carry a different id in
  each group database), and **Rule Changes** (every driven property —
  dimensions, features, instances, file-name/relative-path/tag/loop-control
  rules — keyed by per-placement rule id, with filename breadcrumbs and
  full-path tooltips). The Type column uses the **authoritative
  classification GUID** decoded from CapturedComponents.Data's `T`
  attribute rather than heuristics, with a structural fallback when no
  database is attached. Everything degrades gracefully offline: raw GUIDs
  instead of names, report still runs.
- **Per-side database connections**: CLI `--old-db-*` / `--new-db-*` flags
  (Windows auth default; SQL auth via DW_SQL_PASSWORD_OLD/NEW env vars —
  never a password on the command line) and a GUI **Database Options
  panel** (shown by default; two servers, SQL-auth-first with a Windows-
  auth checkbox; server/database/username remembered per user, password
  never written to disk). Connection failures classify into short
  actionable messages (server unreachable / login failed / database not
  found / no driver) surfaced in the status line and CLI recap — a failed
  side falls back to GUIDs instead of failing the report.
- **Report ergonomics**: draggable column resizing on every table
  (Excel-style — only the dragged column changes), and the "Unchanged
  rows" toggle now also reveals fully-unchanged sections. GUI file pickers
  normalize to native Windows paths and remember a last-used folder per
  field.
- `scripts/db/webapp.py` — Wade's standalone local web preview for
  component-set diffs and database probing.

- **One-click in-app updates.** The update notice is now a download button:
  the app fetches the new version's installer (Windows) or app zip (macOS),
  verifies it against the release's new `SHA256SUMS.txt` — an unverifiable
  download is never run — then launches the installer and steps aside
  (Windows) or reveals the zip in Downloads (macOS). Any failure falls back
  to the download page. Bonus: app-fetched installers skip the browser's
  Mark-of-the-Web, so SmartScreen doesn't interrupt the update.
- **Model/component ID resolution** (from the Solveshop branch, by Wade
  Anderson): ComponentSet names parsed free from project.xml, placed-
  component indexing from components/*.xml, and an optional read-only SQL
  connector that resolves component GUIDs to names from a DriveWorks group
  database — fail-soft (no pyodbc/driver/DB → raw GUIDs, diff still runs),
  injection-guarded, batched and cached. Discovery tooling lives in
  scripts/db/ (`discover_db.py` finds the mapping tables empirically;
  `preview_models.py` needs no database). Mappings are keyed per DriveWorks
  major version (`ID_SOURCES_BY_DW_VERSION`); DriveWorks 22 on SQL Server
  2022 is the field-tested baseline. A new `sql-integration` workflow proves
  the connector against real SQL Server 2017, 2019, and 2022 engines — and
  caught (now fixed) a cache-path bug where negative-cached misses leaked as
  None labels.

- **DriveWorks 22 component mapping CONFIRMED against a live group
  database** (SQL Server 2019, 15.0.4043): 12/12 CCRefs from a real project
  keyed `dbo.CapturedComponents.Id` as plain string-order GUIDs, with the
  master model file **Path** as the identity — so resolved labels are the
  actual SolidWorks files. Byte-order and base64 encodings were probed live
  and ruled out for DW22; the connector nevertheless supports them
  (`IdSource.encoding`) and the discovery script now probes every encoding
  automatically, so a future DriveWorks version with different storage
  can't hide. `ID_SOURCES_BY_DW_VERSION["22"]` ships enabled.

### Changed

- **Update notices now link to the Projx Diff download page** on
  base10consultants.com instead of the GitHub releases page. The version
  check itself still uses the GitHub Releases API.

## [1.4.0] - 2026-08-04

### Added

- **Clickable-setup engine layer**: `--init-config FOLDER` creates a starter
  site config (config, archives, metrics, and dashboard all live under one
  folder), and `--census <config> --add-source "Name=FOLDER"` adds an
  environment group — human names are slugged to safe form, the group's
  archive repo is auto-placed, and the same command scans the new group so
  its projects and user names are immediately listed for triage. A missing
  config now produces a clear pointer to `--init-config` instead of a
  traceback. These back the upcoming "Add environment group" GUI flow
  (spec in docs/specs/).
- **Manage Nightly Sync is fully clickable** — Tools ▸ Manage Nightly Sync
  now opens a chooser (*Open existing config…* / *Create new…*) instead of
  dead-ending when no config exists; *Create new* asks exactly one question
  (where should Projx Diff keep its data?) and builds the whole site from
  it. An *Add environment group…* button — same button for the first group
  and the fifth — names a group (live slug preview), picks its projects
  folder, and scans it immediately: discovered projects land in the table
  as *New* and unseen saver names in the unmapped-users list, ready for
  triage in the same window without losing in-progress edits. The last-used
  config is remembered (`~/.projxdiff`), so returning users skip straight
  to the manager; *Switch config…* gets back to the chooser. The table
  grows a *Group* column once named groups exist.
- **One-click nightly scheduling (Windows)** — after the first census save
  the app offers to register the Task Scheduler job, with the manual
  `schtasks` command shown as a copyable fallback for servers.

### Changed

- **Help and About dialogs restyled** to the app's shared look (dark header,
  card body), and the Help content now covers the nightly sync, census
  triage, and dashboard — not just the compare flow.

## [1.3.0] - 2026-08-04

### Added

- **Site configs: multiple named sources in one config** (`sources` map —
  see `config.example.site.json`). Prod, staging, and future locations each
  get their own `source_dir` + `archive_repo` (+ optional exclude/recursive
  overrides) while sharing one data_dir, census, metrics DB, dashboard, and
  scheduled task. Census keys are namespaced (`prod/Roof Curb` vs
  `staging/Roof Curb`) so identical project names in two locations never
  collide; users stay a single shared map, so mapping an identity once
  applies — and retroactively heals — across every source. Metrics rows
  carry a `source` column (pre-existing databases migrate in place); dated
  reports land under `reports/<source>/`; an unreachable source is flagged
  and skipped while the rest sync. `owners` entries match by plain name
  (all sources) or namespaced key (one source).
- **Dashboard source tabs** — All / per-source views of the tiles and
  charts, plus a Source column and source-aware report links in the
  recent-changes table. Single-source dashboards are unchanged.
- **App icon** — `assets/` now ships the Projx Diff icon (two overlapping
  project pages with removed/added marks); the packaged .exe/.app and the
  live window pick it up automatically.
- **Windows installer** (`ProjxDiff-setup.exe`, Inno Setup) — Program Files
  install, Start Menu entry, optional desktop shortcut, and a clean
  uninstaller, so the app shows up where Windows users expect instead of
  living as a loose portable .exe (which is still published for those who
  prefer it). No `.driveprojx` file association is registered — that
  extension belongs to DriveWorks. The release workflow compiles the
  installer and verifies it end-to-end: silent install, then a real `--sync`
  run from the installed copy.
- **Linux binary** (`ProjxDiff-linux`) — for running the nightly sync on
  Linux servers without a Python install. Built and smoke-tested in the
  release workflow alongside the Windows and macOS artifacts.

Legacy single-source configs, censuses, report layouts, and databases
behave exactly as before; site features activate only when a config
declares `sources`.

## [1.2.4] - 2026-08-04

### Added

- **App-icon plumbing.** The live window/taskbar icon is loaded at start-up from
  `assets/icon.png` (bundled into frozen builds), complementing the packaged
  `.exe`/`.app` icons the build spec already picks up from `assets/icon.ico` and
  `assets/icon.icns`. All are optional and skipped silently until the branding
  assets are added to the repo.

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
