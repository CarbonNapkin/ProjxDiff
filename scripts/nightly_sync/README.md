# Nightly Sync

Archives every `.driveprojx` from a network share into a git repo each night
and records what changed — per project, per category, per element — in a
SQLite metrics database, with per-project HTML/JSON diff reports for
drill-down.

This is site infrastructure, not part of the shipped Projx Diff app. It
imports `dw_compare` from this repository, so keep a checkout of the repo on
the machine that runs it (or point `tool_repo` in the config at one).

## What one run does

1. Finds every `*.driveprojx` under `source_dir` (recursively by default).
2. Copies each to local staging (3 attempts, 10s apart, so a project open in
   DriveWorks or a share hiccup doesn't kill the run) and extracts it.
3. Compares extracted content (by file hash) against the archive repo:
   - **Unchanged** — nothing happens. No commit, no rows, no reports.
   - **New** — added to the archive with a `<name>: added to archive` commit
     and a single project-level "added" event. It is *not* diffed against
     nothing — that would count every element as "added" and poison the work
     metrics with a one-time explosion.
   - **Changed** — a semantic diff (previous vs. current) is built with
     `dw_compare`; the HTML + JSON reports land in
     `data_dir/reports/<date>/<name>.{html,json}`; per-category counts and
     per-element change rows are inserted into `data_dir/metrics.sqlite`; the
     new state is committed as `<name>: +A -R ~M (nightly sync <date>)`.
4. Projects that vanished from the share get a project-level "removed" event.
   They stay in the archive unless `remove_missing` is `true`.
5. Optionally pushes to `remote` if `push` is `true` (a failed push is logged
   but does not fail the sync).

Each changed project is its own commit. If `owners` maps the project name to
`"Name <email>"`, that becomes the commit author — which makes `git log
--author` a per-user work record.

## Setup (Windows)

Requirements: Python 3.10+ ([python.org](https://python.org) or the `py`
launcher) and [Git for Windows](https://gitforwindows.org), plus read access
to the share for the account that runs the task.

1. Copy `config.example.json` to `config.json` (anywhere you like) and edit
   it. Use forward slashes in paths — they work fine on Windows, including
   UNC paths (`//SERVER/share/...`), and avoid JSON escaping headaches.
2. Do a first run by hand and eyeball the log:

   ```powershell
   py -3 nightly_sync.py C:\ProjxArchive\config.json --dry-run
   py -3 nightly_sync.py C:\ProjxArchive\config.json
   ```

   The first real run archives every project as **new** (one-time).
3. Schedule it (run the shell as the service account, or set `/RU`):

   ```powershell
   schtasks /Create /TN "ProjxDiff Nightly Sync" /SC DAILY /ST 02:00 ^
     /TR "py -3 C:\path\to\ProjxDiff\scripts\nightly_sync\nightly_sync.py C:\ProjxArchive\config.json"
   ```

The script logs to `data_dir/sync.log` as well as the console, refuses to
start if a previous run is still going (stale locks expire after 6 hours),
and exits 0 on success, 1 if any individual project errored, 2/3 for
unreachable source / concurrent run — so Task Scheduler's "last run result"
is meaningful.

## The dashboard

Every run finishes by regenerating `data_dir/dashboard.html` (disable with
`"dashboard": false`): a self-contained static page — no server, no external
resources, safe to open straight off a file share — showing changes per day
over the last 60 days, the most-active projects / users / categories over the
last 30, and a recent-changes table whose rows link into the dated drill-down
reports. Light and dark mode both supported. Regenerate it by hand any time:

```powershell
py -3 dashboard.py C:\ProjxArchive\config.json
```

Point people at `data_dir\dashboard.html` (or copy/publish it wherever your
team looks); each row's "report" link needs the neighboring `reports\` folder,
so share the `data_dir` rather than the lone file if you want drill-down.

## The metrics database

`data_dir/metrics.sqlite`, three tables:

- `runs` — one row per sync: date, timing, projects seen/changed, errors.
- `category_changes` — one row per (date, project, category) with
  added/removed/modified/unchanged counts. Only categories with activity are
  recorded.
- `element_changes` — one row per changed element: (date, project, owner,
  category, element name, status). Whole-project add/remove events appear
  here with `category='project'`.

This is the feed for the dashboard / Power BI. The full field-level detail
(old/new formulas) lives in the dated JSON reports; the archive repo itself
holds every historical state, so any two dates can be re-diffed on demand:

```bash
python -m dw_compare <archive>/ProjectA_old_checkout ProjectA -o then_vs_now.html
```
