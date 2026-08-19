# Nightly Sync

Archives every `.driveprojx` from a network share into a git repo each night
and records what changed — per project, per category, per element — in a
SQLite metrics database, with per-project HTML/JSON diff reports for
drill-down and a self-contained dashboard regenerated after every run.

As of v1.2.0 the engine lives **inside the app** (`dw_compare/sync.py` et
al.); the scripts in this folder are thin back-compat wrappers so existing
scheduled tasks keep working. New setups invoke the app directly:

```powershell
py -3 -m dw_compare --sync C:\ProjxArchive\config.json [--dry-run]
py -3 -m dw_compare --census C:\ProjxArchive\config.json
py -3 -m dw_compare --dashboard C:\ProjxArchive\config.json
```

A packaged build works too. The installed folder holds two copies of the
app, the `python.exe`/`pythonw.exe` split:

| | |
|---|---|
| `ProjxDiff.exe` | Windowed. What the Start Menu shortcut and the scheduled task run. Launched from a terminal it detaches and prints **nothing**; output still reaches `data_dir\sync.log` and the exit code still reaches Task Scheduler. |
| `ProjxDiff-cli.exe` | Console. Same app, same flags — use this one by hand: `ProjxDiff-cli.exe --sync config.json`, `ProjxDiff-cli.exe --doctor`. Its output can be piped, redirected and waited on. |

`--doctor` prints the running version, whether it is a frozen build, and
whether `pyodbc` and the SQL Server ODBC drivers are present — the quickest
way to confirm which build an installed copy actually is.

## What one run does

1. Finds every `*.driveprojx` under `source_dir` (recursively by default),
   minus `exclude` glob matches.
2. Applies the **census** (see below): ignored projects are skipped; new
   projects are auto-registered as *pending* and sync normally; same-name
   collisions sync only the registered path and flag the other file.
   A project open in DriveWorks Administrator (it has a `<name>.~driveproj`
   lock beside it) is **archived anyway**, with an `[OPEN]` line in the log
   naming whoever has it. The `.driveprojx` on the share is the last *saved*
   state — DriveWorks rewrites it on save, not continuously — so there is no
   half-written state to avoid, and waiting on a lock left behind by a
   crashed session would freeze that project out of the archive
   indefinitely. The lock file is never deleted: it is what stops a second
   person opening the project, and it usually belongs to another user on
   another machine.

   > Until 1.8.0 an open project was *deferred* to the next run, governed by
   > a `lock_stale_hours` setting. That key is now inert; a config that still
   > carries it loads with a warning and is otherwise unaffected.
3. Copies each file to local staging (3 attempts, 10s apart) and extracts it.
4. Compares extracted content (by file hash) against the archive repo:
   - **Unchanged** — nothing happens. No commit, no rows, no reports.
   - **New** — added to the archive with a `<name>: added to archive` commit
     and a single project-level "added" event (never diffed against nothing,
     which would poison the metrics).
   - **Rebuilt** — almost none of the *archived* project survives in the file
     on the share (at or below `rebuild_similarity` of it, default 5%, once
     the archived copy has at least `rebuild_min_elements`, default 25). That
     is a project deleted and rebuilt under its old name, not an edit, so it
     is re-baselined with a `rebuilt` event instead of being diffed against
     its predecessor — which would report every element of both projects as
     churn. The HTML + JSON reports are still written (they are the only
     record of what the rebuild replaced); only the metrics rows are skipped.
     Note this measures survival, not resemblance: a project that keeps
     everything it had and grows tenfold is still itself, and is diffed
     normally. If nothing survives *and* the new file parses to fewer than
     `rebuild_min_elements`, the sync refuses to touch that project and
     reports an error — a truncated copy or a parser that no longer
     understands the file explains that at least as well as a rebuild does,
     and re-baselining would replace a good archive with a bad copy.
   - **Changed** — a semantic diff is built; HTML + JSON reports land in
     `data_dir/reports/<date>/`; per-category counts and per-element rows are
     inserted into `data_dir/metrics.sqlite`; the new state is committed as
     `<name>: +A -R ~M (nightly sync <date>)`.
5. Projects that vanished from the share get a one-time project-level
   "removed" event; they stay in the archive unless `remove_missing` is true.
6. The dashboard is regenerated; `push: true` pushes the archive to `remote`.

## Multiple locations (site config)

When projects live in several trees — prod and staging, or multiple share
roots — use a **site config**: one file, one scheduled task, one dashboard,
with a named entry per location (see `config.example.site.json`):

```json
{
  "sources": {
    "prod":    { "source_dir": "C:/DriveWorksFiles",        "archive_repo": "C:/ProjxArchive/repo-prod" },
    "staging": { "source_dir": "C:/StagingDriveWorksFiles", "archive_repo": "C:/ProjxArchive/repo-staging" }
  },
  "data_dir": "C:/ProjxArchive/data"
}
```

Each source gets its own archive repo and census namespace
(`prod/Roof Curb` vs `staging/Roof Curb`), so identical project names in two
locations never collide — while users, the metrics DB (rows carry a
`source`), reports (`reports/<source>/<date>/`), and the dashboard stay
unified. Map a user once and it applies (and heals) everywhere. The
dashboard grows tabs — All / prod / staging — and a Source column in the
recent-changes table. Per-source `exclude`/`recursive` override the shared
top-level values; `owners` entries match either `"Roof Curb"` (all sources)
or `"prod/Roof Curb"` (one source). An unreachable source is flagged and
skipped; the others still sync.

### Group database name resolution in nightly reports (optional)

Give a source its DriveWorks group database and that source's nightly HTML
reports resolve captured model/rule references to real component and model
names instead of raw ids:

```json
"prod": { "source_dir": "...", "archive_repo": "...",
          "db_server": "KEES-DB", "db_database": "KEES" }
```

`db_server` accepts `HOST`, `HOST\\INSTANCE`, or `HOST,PORT`. Top-level
`db_server`/`db_database` apply to every source that doesn't set its own
(prod and staging usually point at different group databases, so per-source
is the norm). Lookups are read-only; if the database is unreachable during
a run, the sync completes normally and that night's reports show raw ids.
The JSON diffs always keep raw ids — their schema is versioned and consumed
by pipelines.

> **Windows integrated authentication only.** The app never stores
> passwords, and an unattended nightly task has nobody to type one — so
> the Windows account the scheduled task runs as must itself be granted
> read-only access to the group database (in SSMS:
> `CREATE USER [DOMAIN\account] FOR LOGIN [DOMAIN\account]`, then add it
> to the `db_datareader` role). **If you can't use integrated auth, you
> cannot run the nightly compares and connect to the database** — the
> sync itself still runs fine, but its reports will show raw ids; SQL
> Server logins work only in interactive compares (GUI or CLI), where
> the password is supplied at run time.

Existing single-source configs are untouched by all of this — same behavior,
paths, and census layout as before. To consolidate two deployed single-source
setups into one site config, point the site config at a fresh `data_dir`
(or keep one of the old ones and let the other source's projects register as
pending on first run), and retire one of the two scheduled tasks.

## The census (projects & users, managed not hand-written)

`data_dir/census.json` (override with config key `census_path`) is generated
by scanning and curated by a human — the sync only ever *adds* entries:

- **`projects`** — every project name, its source path, and a disposition:
  `pending` (newly discovered, syncs normally, awaiting review), `track`, or
  `ignore` (stop syncing; captured history is kept). A project whose file
  moved is followed automatically; two files sharing a name become a flagged
  conflict, and only the registered path syncs.
- **`users`** — every DriveWorks display name ever seen (read from each
  project's `DWCurrentUserDisplayName`, written on save), mapped to
  `"Name <email>"` — or `null` while unmapped.

Anything unresolved — pending projects, unmapped users, conflicts — appears
in the sync log and in the dashboard's **Needs attention** panel. Resolve it:

- **In the app:** Projx Diff ▸ Tools ▸ Manage Nightly Sync — pick the config,
  decide Track/Ignore per pending project, type identities for unmapped
  users, save.
- **From the command line:**

  ```powershell
  py -3 -m dw_compare --census C:\ProjxArchive\config.json ^
    --map "Zach=Zach Miller <zach@example.com>" --track "Model 630" --ignore "Old Test Rig"
  ```

  (`--no-scan` applies edits without re-walking the share.)

**Attribution** (with `derive_author_from_file: true`): each changed
project's commit is authored by its last saver, resolved as explicit
`owners` entry → census users map → legacy `author_aliases` → the raw
display name. Unmapped names are recorded raw in the metrics; **mapping a
user retroactively heals those rows**, so per-user charts read as if the
mapping always existed. Git history is deliberately not rewritten — old
commits keep the raw name the file carried. Note attribution credits the
*last* saver; several editors between two runs land under one name.

## Setup (Windows)

Requirements: Python 3.10+ and Git for Windows, plus read access to the
share for the account running the task.

1. Copy `config.example.json` somewhere (e.g. `C:\ProjxArchive\config.json`)
   and edit. Use forward slashes — they work on Windows, including UNC paths
   (`//SERVER/share/...`), and avoid JSON escaping headaches.
2. First runs by hand:

   ```powershell
   py -3 -m dw_compare --sync C:\ProjxArchive\config.json --dry-run
   py -3 -m dw_compare --sync C:\ProjxArchive\config.json
   py -3 -m dw_compare --census C:\ProjxArchive\config.json   # see what needs triage
   ```

   The first real run archives every project as new (one-time), then triage
   the census in the GUI or with `--map`/`--track`/`--ignore`.
3. Schedule it:

   ```powershell
   schtasks /Create /TN "ProjxDiff Nightly Sync" /SC DAILY /ST 02:00 ^
     /TR "py -3 -m dw_compare --sync C:\ProjxArchive\config.json" ^
     /RU <service-account>
   ```

   (If `py -3 -m` needs a working directory, set /TR to
   `cmd /c cd /d C:\Tools\ProjxDiff && py -3 -m dw_compare --sync ...`.)

Logs go to `data_dir/sync.log`; a lock file prevents overlapping runs (stale
after 6h); exit codes: 0 ok, 1 per-project errors, 2 source unreachable,
3 already running.

## The dashboard

`data_dir/dashboard.html` — self-contained, no server, safe to open off a
file share. Shows the needs-attention panel, changes per day (60d), top
projects/users/categories (30d), and a recent-changes table linking into the
dated drill-down reports. Share the whole `data_dir` so the report links
work.

## The metrics database

`data_dir/metrics.sqlite`: `runs` (one row per sync), `category_changes`
(per date/project/category counts), `element_changes` (one row per changed
element; whole-project add/remove events use `category='project'`). This
feeds the dashboard and Power BI. Field-level detail lives in the dated JSON
reports; the archive repo holds every historical state, so any two dates can
be re-diffed on demand with the app.
