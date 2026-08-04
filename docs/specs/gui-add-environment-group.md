# Spec: "Add environment group" flow in Manage Nightly Sync

**Status: implemented** (gui.py, tests/test_gui_sync_setup.py) — kept for
rationale; the flow description below matches what shipped.

**For:** the GUI session. **Engine support:** already on `main` (pull first).
Goal: make sync setup fully clickable — no human ever authors config JSON.

## The flow

1. **Entry.** Tools ▸ Manage Nightly Sync currently opens a file dialog and
   dead-ends when no config exists. Replace with a chooser: *Open existing
   config…* / *Create new…* (remember the last-used config path between
   launches — e.g. a line in `~/.projxdiff` — so returning users skip the
   dialog entirely).
2. **Create new** asks ONE question: *"Where should Projx Diff keep its
   data?"* (folder picker, suggest `C:\ProjxArchive`). Call
   `sync.init_site(root)` → writes `<root>/config.json`, returns its path.
   The user never sees `data_dir`/`archive_repo` as concepts.
3. **Add environment group** (a button in the manager window — same button
   for the first and the fifth group): a small dialog with a *Name* field
   ("prod", "staging", "Dayton plant"…) and a *Projects folder* picker.
   On OK call `sync.add_source(config_path, name, folder)` → slugs the name
   (whitespace→hyphen, safe charset), auto-places the archive at
   `<root>/repos/<slug>`, atomic-writes, validates, returns the slug.
   Surface `SystemExit` messages from it verbatim (duplicate name, missing
   folder, legacy config) — they're written for humans.
   Show the slug and a hint: *"Group names are permanent — pick something
   boring."*
4. **Scan immediately** after a successful add — census scan only, no
   archiving: reload config with `load_config`, then
   `census.scan(cfg, census, sync.find_projects)`, save the census, and
   refresh the table. Discovered projects appear as *New* for triage in the
   same window; new saver names appear in the unmapped-users section. This
   is the moment the product clicks: add → see everything → triage.
5. **(Optional, nice)** After first save, offer scheduling: *"Run nightly at
   [02:00] — [Register scheduled task]"* shelling `schtasks /Create ... py -3
   -m dw_compare --sync <config>`, with the manual command shown as a
   copyable fallback. Windows-only; hide elsewhere.

## Engine API (in `dw_compare.sync`)

- `init_site(root: Path) -> Path` — creates `<root>/config.json` (site-shape,
  empty `sources`, `data_dir=<root>/data`, `derive_author_from_file=true`).
  Raises `SystemExit` if the config already exists.
- `add_source(config_path, name, source_dir) -> str` — appends a group,
  derives `archive_repo=<root>/repos/<slug>`, atomic write + validation.
  Raises `SystemExit` with a human-readable reason on any problem.
- `slug_source_name(name) -> str` — exposed if you want live slug preview in
  the dialog.

CLI equivalents exist for headless setups and for testing your flow:
`--init-config FOLDER`, then `--census <config> --add-source "prod=DIR"`.

## Notes

- GUI-created configs are always site-shaped, even with one group — a lone
  group named by the user beats the legacy unnamed source. Legacy configs
  remain openable (the client's deployment).
- Namespaced census keys (`prod/Roof Curb`) already flow through your table;
  consider splitting the prefix into its own Group column when
  `cfg['sources_resolved']` has named sources.
- The Help dialog was restyled to the shared header+card idiom and now has
  a `_dialog`/`_finish_dialog` helper pair on `CompareApp` — reuse them for
  the chooser and add-group dialogs.

## UI modernization position (for the record)

Tier 1 (flat tk polish): done — main window (your v1.2.3), Help/About (this
push). Tier 2 (ttk + Sun Valley theme): skipped — ttk renders black frames
on older macOS Tk, which is why this GUI is plain tk. Tier 3 (the real
move, if the app goes client-facing): a webview shell (e.g. pywebview)
rendering HTML screens in the same design system as the report and
dashboard — one brand across every surface, dark mode included, engine
untouched. Treat as a deliberate "2.0 UI" project, not a tweak.
