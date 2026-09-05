# Changelog

All notable changes to Projx Diff are documented in this file.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [1.12.0] - 2026-09-05

### Added

- **The dashboard's charts answer clicks now.** A project or user bar
  filters the Recent-changes table to that project or user (shown as a
  removable chip); a column in the daily chart date-filters the table to
  that day. Every day — including quiet ones — has a hover target, and the
  daily tooltip now breaks the total down by category, so a spike is
  explainable in place. The table also gains a free-text project/user
  filter, and switching source tabs finally filters the table too (it used
  to silently keep showing every source). The dashboard's script block now
  goes through the same `node --check` gate as the report's.

- **Report views are shareable URLs.** The search text, status filters, and
  section toggles live in the URL hash and are restored on open; every
  changed row gets a stable id with a hover 🔗 button that copies a link
  landing the reader on that exact change, revealed and flashed. Stable
  because archived reports are immutable — the same file always yields the
  same ids. Section headers are real buttons now (Tab + Enter/Space, with
  `aria-expanded`), theme and filter controls carry focus outlines and
  pressed states.
- **Copy buttons on formula cells in the report.** Hover any formula row for
  a copy button; modified rows get separate "copy old" / "copy new" — the
  inline diff interleaves both versions, so select-copying a modified
  formula used to hand you the two mixed together, which is exactly wrong
  for pasting back into DriveWorks Administrator. Notes and the buttons
  themselves never leak into the copied text.
- **Change-to-change navigation in the report.** Prev/Next buttons (and
  `n`/`p` keys) step through every change that survives the current filters,
  auto-expanding collapsed sections and flashing the target row; a counter
  shows position. `/` focuses the search box, which now reports its match
  count — including an explicit "No matches" instead of a silently empty
  page.

- **The main window earns its keyboard and its memory.** Enter runs the
  compare from anywhere but the log pane; a running compare shows an
  activity bar and a Cancel button (cooperative — it takes effect at the
  next stage boundary and says so, and a cancelled run never writes a
  report); File ▸ Recent Comparisons reloads any of the last six pairs.
  Help gains **Check for Updates…** (the launch check stays passive, this
  one always answers) and **Run Diagnostics**, which puts the `--doctor`
  self-check's output in the log pane instead of asking anyone to find a
  terminal and a hidden flag.

- **Manage Nightly Sync triages in bulk and saves without closing.**
  "Set all shown to New / Track / Ignore" acts on whatever the filters
  leave visible (50 new projects used to mean 100 clicks); nothing is
  written until Apply or Save. **Apply** saves the census — and heals
  metrics — while keeping the window, its filters, and half-typed
  identities alive; Save keeps its close-when-done behavior. The
  Modified / Last-saved-by columns now fill in from a background scan
  instead of freezing the window while every project zip on the share is
  opened.

### Fixed

- **A failed compare now shows its details instead of directions.** The GUI
  reveals the log pane with the traceback scrolled into view and states the
  failure in the red status line — no more modal telling you to go find
  View ▸ Show Log.
- **Lookup-grid cell changes are no longer color-only.** Changed/added/
  removed cells carry a +/−/~ glyph in the corner, closing the one gap in
  the report's colorblind-safe design (every other change signal already
  had a text label or glyph).

## [1.11.0] - 2026-09-04

### Added

- **Date-range filter on the dashboard's Recent changes table.** The page
  now bakes in the full per-night history (about 120 bytes a row — years of
  nightly data stay small) instead of only the latest 20 rows, shows the
  usual latest-20 view by default, and adds From/To date inputs that filter
  client-side — the dashboard is a static file, so the data must ship with
  the page. A row counter says what's showing; Clear returns to the default
  view. The date inputs are bounded to the dates actually recorded.

## [1.10.0] - 2026-09-04

### Fixed

- **Rule changes now count in the metrics and on the dashboard.** The JSON
  diff (which feeds the nightly metrics database) had no category for
  driven-property rules — the HTML report's "Rule Changes" section, often
  the bulk of a night's work. The dashboard's tiles, "By category" chart,
  per-user counts and recent-changes rows all silently excluded them, so a
  diff the report showed as `+42 −2 ~8` could land on the dashboard as
  `+0 −0 ~3`. `build_diff` gains a `rules` category (detection identical to
  the report's `compare_property_rules`, locked by the same parity tests
  that guard every other category); the JSON schema stays 1 — the change is
  additive, and a document without the key means "not measured", not "no
  rule changes".

### Added

- **`--backfill-rules config.json [--dry-run]`** repairs the history: the
  archived JSON reports predate the category, but the archive git repos
  hold every night's full project state, so the backfill replays each
  recorded-diff commit (`<name>: +A -R ~M (nightly sync <date>)`), re-diffs
  just the rules category, and inserts the rows that night's run would have
  written. Baselines, rebuilds and removals are skipped exactly as the live
  sync skips them; owner attribution reuses the owner already recorded on
  that night's other rows (falling back to the archived tree's last-saver
  through the census, read-only). Idempotent — re-running, or running after
  the fixed sync has recorded some nights live, never double-counts. The
  dashboard is regenerated at the end when anything changed. A live
  backfill takes and honours the sync's one-writer lock (`sync.lock`), so
  it cannot interleave metrics writes with the 02:00 run; a dry run, as
  with the sync, neither takes nor honours it.

## [1.9.0] - 2026-08-19

### Changed

- **Projects open in DriveWorks Administrator are archived, not skipped.**
  1.8.0 deferred them to the next run on the theory that copying a project
  mid-edit captured a half-written state. It does not: the `.driveprojx` on
  the share is the last *saved* state, and DriveWorks rewrites it on save
  rather than continuously — so deferring bought no atomicity (someone who
  saves at 01:59 and keeps working is captured mid-thought whether or not a
  lock exists) while costing real coverage. A lock left behind by a session
  that exited uncleanly froze its project out of the archive; a single night
  at a live site showed three such locks, aged 1.9h, 17.6h and 85.3h. The
  sync now archives whatever is on the share at run time and logs an `[OPEN]`
  line naming whoever holds the lock, so a surprising diff can still be
  traced back. A partial save is still caught by the three defenses that
  actually address it: the copy is retried, a truncated zip fails loudly on
  its central directory, and the rebuild guard refuses to re-baseline a good
  archive over a copy that parses to nothing. The `.~driveproj` lock file is
  still never deleted — it is what stops a second person opening the project.
- `lock_stale_hours` is retired. A config that still sets it loads normally
  and logs one warning that the key no longer does anything. The dashboard
  and Manage Nightly Sync no longer carry a "Not synced last run" section,
  and a `deferred` list left in an existing `census.json` is cleared on the
  first 1.9.0 run rather than naming projects that have long since synced.

### Added

- **Every run names the build that produced it.** `sync.log` opens with
  `Projx Diff <version> starting (live|dry run)`, and the metrics database's
  `runs` table gains a `version` column (migrated in place; rows written by
  an earlier build stay empty rather than being guessed at). Confirming that
  an upgrade actually took effect previously meant fingerprinting an
  incidental change in the summary line, which only worked by accident and
  stopped working the moment two versions shared a format.

### Fixed

- **`--doctor` and `--sync` produce output when run from a terminal on
  Windows.** Both spec branches build `console=False`, so the exe is a
  windowed binary: launched from `cmd.exe` it detached immediately, printed
  nothing, and returned a prompt that looked exactly like a healthy run —
  making the documented post-install check ("run `--doctor`, confirm it
  reports the version") impossible to actually perform. The Windows install
  now ships a second, console-subsystem copy as **`ProjxDiff-cli.exe`**
  beside `ProjxDiff.exe`, the same split as `python.exe`/`pythonw.exe`. Use
  it for anything you run by hand; unlike attaching to the parent console it
  also behaves correctly when piped, redirected or waited on. Both exes come
  from one PyInstaller build and share one `_internal\`, so the download does
  not grow by a second runtime. `ProjxDiff.exe` is unchanged — the Start Menu
  shortcut and the scheduled task still run it, and a double-click still opens
  the app without flashing a console.
- **Upgrading from pre-1.5.x no longer leaves a stale 32-bit install behind.**
  1.3.0 predates `ArchitecturesInstallIn64BitMode`, so it registered in 32-bit
  mode and its uninstall key lives under `WOW6432Node`; Inno treated it as a
  separate product and every upgrade since installed *alongside* it in
  `Program Files`, leaving 1.3.0 sitting in `Program Files (x86)` with its own
  Add/Remove entry. The installer now finds that key and runs its uninstaller
  before installing. On its own the orphan was wasted disk — the damage was in
  combination with the next item, where a nightly task pointing into the x86
  path kept running 1.3.0 after every "successful" upgrade.
- **An upgrade no longer leaves the nightly task running the old binary.** The
  installer never looked at Task Scheduler, so a site whose "ProjxDiff Nightly
  Sync" task pointed somewhere the upgrade did not replace upgraded cleanly and
  went on running the old build at 02:00 with no error and nothing to see. The
  installer — which is already elevated, and so is the one moment in the
  lifecycle where re-registering a SYSTEM task needs no separate UAC prompt —
  now compares the task's command against the copy it just installed and
  repoints it if they differ, leaving the schedule, the arguments (your config
  path) and the principal exactly as they were. It is deliberately narrow: a
  machine with no such task is left alone rather than having a nightly job
  invented for it, and a task running something that is not a Projx Diff
  executable is reported and left untouched.

## [1.8.0] - 2026-08-16

### Fixed

- **Running the test suite on a deployed Windows machine no longer breaks that
  machine's nightly sync.** `Repair scheduled task` builds a `schtasks /Create
  /F` line that hardcodes the production task name, so any code path reaching
  it rewrites the real nightly task with whatever command it is handed — and a
  junk command does not fail loudly, it quietly replaces a working task with a
  broken one. A Windows-only test defect (see below) did exactly that on a
  deployed box, re-registering the live task to run the command `x`; the task
  then failed nightly with `0x80070002` (file not found) and the archive
  stopped moving for three nights before anyone noticed. The repair now
  verifies the command actually runs the current executable *before* touching
  the task, not only afterwards. If your nightly task shows result
  `-2147024894`, re-run Tools ▸ Manage Nightly Sync ▸ Repair scheduled task
  from an elevated session — the archive catches up on the next run, though
  the missed nights collapse into a single diff. Both registration paths now
  refuse, not just the repair one.
- **`--dry-run` no longer aborts that night's real sync.** It took the
  one-run-at-a-time lock, so checking a config at 01:59 made the 02:00 run
  exit 3. A dry run now neither takes the lock nor honours one. It also no
  longer calls `ensure_repo`, which would git-init a missing archive and
  rewrite an existing one's `user.name`/`user.email` — "report changes
  without recording anything" now means it, apart from the run log.

- **A rebuilt project is no longer diffed against a stranger.** Deleting a
  project and recreating it under the same name kept the old archive
  directory, so the nightly sync treated the rebuild as an edit and reported
  every element of the old project as removed and every element of the new
  one as added — a one-off explosion in the work metrics, the exact thing the
  first-appearance path already guards against. The sync now measures how much
  of the *archived* project survives in the file on the share; at or below
  `rebuild_similarity` of it (default 5%, applied once the archive holds
  `rebuild_min_elements`, default 25) the project is re-baselined and recorded
  as `rebuilt` instead. Survival, not resemblance: a project that keeps every
  element it had and grows tenfold is unmistakably itself and is diffed
  normally. The rebuild's HTML + JSON reports are still written — they are the
  only record of what it replaced — and only the metrics rows are skipped.
- **A bad copy can no longer overwrite a good archive.** If nothing of the
  archived project survives *and* the file on the share parses to fewer than
  `rebuild_min_elements`, the sync now refuses to re-baseline that project and
  reports an error instead. A truncated copy, a half-written save, or a parser
  that no longer understands the file explains that at least as well as a
  rebuild does — and left unguarded, a DriveWorks version this tool cannot
  read would have silently re-baselined every project on the share in one
  night.

### Added

- **Projects open in DriveWorks Administrator are deferred.** A project with
  a `<name>.~driveproj` lock beside it is skipped for that run rather than
  archived mid-edit, and is not mistaken for a project that vanished from the
  share. Lock files are never deleted: the sidecar holds no project data
  (just `user|machine`), but it is what prevents a second person opening the
  project, and on a shared site it usually belongs to another user on another
  machine. Locks older than `lock_stale_hours` (default 6, `0` to disable)
  are treated as abandoned so an unclean exit cannot defer a project forever.
  The lock is checked again after the file is copied, so a project opened
  mid-sync is deferred too.
- **Deferred projects are listed on the dashboard and in Manage Nightly
  Sync**, with whoever the lock names, so "why didn't that one sync?" has an
  answer without reading the log. The list is this run's state, not a standing
  decision: a project closed overnight drops off by itself.

### Changed

- **Help ▸ How to Use now explains the nightly side properly.** It was one
  paragraph for a feature with an archive format, a nightly lifecycle, and a
  triage workflow. It now covers what the archive actually is (an ordinary git
  repository you own, one per environment group, each night a commit authored
  to whoever last saved the project), what a run does step by step, the four
  cases that are not a plain edit (new, rebuilt, open in Administrator, gone
  from the share), and what New / Track / Ignore mean — including that mapping
  a user heals past metrics retroactively. The dialog scrolls past a height
  cap, since it sizes itself to its content and would otherwise run off a
  laptop screen with the Close button out of reach.
- **The schedule dialog shows the command it will register.** Run from a
  source checkout rather than the installed app, the nightly task gets
  `python.exe -m dw_compare …` instead of the installed exe — reasonable for a
  developer, wrong on a deployed machine, and previously invisible either way.
- The nightly sync's numeric settings (`rebuild_similarity`,
  `rebuild_min_elements`, `lock_stale_hours`) are coerced and range-checked
  when the config loads, so a hand-edited `"6"` works and a `"lots"` fails
  with a message naming the key — rather than as a `TypeError` an hour into
  the night.
- **Windows CI is green again**, for the first time since 1.7.0. Four GUI
  tests written on macOS assumed macOS widget behaviour and failed against
  the native Windows widget factory 1.7.0 introduced — so the two releases
  that were *about* native Windows controls both shipped with Windows
  unverified, and a red baseline meant nobody read the result. Three of the
  four were harmless; the fourth was the scheduled-task defect above. The
  tests now pin the widget factory and platform explicitly instead of
  inheriting whichever the runner happened to have. Suite 294 → 313 tests.

## [1.7.1] - 2026-08-06

### Fixed

- **Filtering the project list no longer looks empty.** In Manage
  Nightly Sync, switching to New / Track / Ignore while scrolled down
  left the view below the matching rows — which read as "no matches".
  The list now snaps back to the top on every filter change.

### Added

- **Repair scheduled task** in Tools ▸ Manage Nightly Sync (Windows):
  re-registers the "ProjxDiff Nightly Sync" task to run the installed
  app — nightly, as SYSTEM — for deployments whose task still points at
  an old copy of the tool. Triggers one administrator (UAC) approval;
  the dialog also shows the exact command for running it by hand in an
  elevated terminal.

## [1.7.0] - 2026-08-05

### Changed

- **Native controls on Windows.** Buttons, text fields, and checkboxes
  now render through the Windows theme engine — rounded corners on
  Windows 11, real focus states — instead of the flat squared custom
  style. macOS keeps the classic widgets, which render reliably there.

### Added

- **The Server field finds servers for you.** On Windows it's a
  combobox — the drop-down arrow lists SQL Server instances found on
  your network (the same broadcast SSMS's server dropdown uses), with
  a background scan so opening it never blocks; on macOS a slim ▾
  posts the same pick list. Best-effort by nature: if nothing answers
  (SQL Browser service off, UDP 1434 blocked) you type the name
  instead. Hovering the field shows the accepted formats — HOST,
  HOST\INSTANCE, or HOST,PORT.
- **Help ▸ How to Use grows a database section** — server formats, the
  finder, Test connection, per-side logins, and the passwords-never-
  saved rule — shown only on installs that have the database surface
  enabled.
- **Nightly sync reports can resolve names too.** Give a sync source its
  group database (`db_server`/`db_database` in the config, per source or
  shared) and that source's nightly HTML reports resolve captured
  model/rule references to real names. **Windows integrated
  authentication only**: passwords are never stored, so the account the
  scheduled task runs as must itself have read access to the database —
  without integrated auth, the nightly compares cannot also connect to
  the database (the sync still runs; reports show raw ids). One
  read-only connection per source per run; a database outage never
  stops the sync. JSON diffs keep raw ids — their schema is versioned.

## [1.6.0] - 2026-08-05

### Changed

- **Redesigned main window: old on the left, new on the right.** Each
  side's project picker, group database settings, and (when different)
  its login now live together in that side's pane, color-matched to the
  report's orange/blue. The shared SQL login sits below both panes.
- **Long paths stay readable.** File paths show with the middle
  ellipsized — the folder root and filename stay visible at any window
  width — with the full path on hover; the real value is always used.
  The window also now has a minimum width so the panes never crush.

### Added

- **Test connection buttons, one per database.** Each side's Database
  section can verify its settings on the spot with the same read-only
  connector a compare uses: a green confirmation naming the database
  and server, or the same friendly one-line explanation the report
  shows when a connection fails. Nothing is saved by testing.

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
