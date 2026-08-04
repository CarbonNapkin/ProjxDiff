"""
webapp.py — local web UI for ProjxDiff's model/component preview.

Runs a small web server on your own PC (nothing leaves your machine) and
opens it in your browser. One page: pick the old and new .driveprojx
files, optionally fill in each one's SQL Server / database, click Compare.

  - Component Set changes (added/removed/modified) — always shown, no
    database needed.
  - How many component/model IDs would need a database to get real names.
  - If you filled in a server + database: which table in that database
    actually maps those IDs to names (read-only — SELECT only, nothing is
    ever written).

No third-party packages are required for the base flow (this file only
uses the Python standard library). The database-lookup step additionally
needs `pyodbc` installed (`pip install pyodbc`) plus a SQL Server ODBC
driver on the PC — if that's missing, the page still works, it just skips
the database part and tells you why.

Usage:
    python webapp.py
    -> opens http://127.0.0.1:8765/ in your browser automatically

Windows: double-click run_web.bat instead of typing anything.
"""

from __future__ import annotations

import email
import email.policy
import html
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# Two levels up: this file lives in scripts/db/ inside the repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dw_compare import components as C
from dw_compare import dbsource

PORT = 8765
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp_settings.json")
# Everything except the password is remembered between runs. Password is
# never written to disk - matches dbsource.py's "no stored credentials" rule.
REMEMBERED_SUFFIXES = [
    "server", "database", "auth", "user", "table", "idcol", "namecol", "schema",
    "prop_table", "prop_idcol", "prop_namecol", "prop_schema",
]

# --------------------------------------------------------------- template
#
# Design language: this tool redlines two engineering project revisions
# against each other, so the styling borrows from drawing-review
# conventions rather than generic SaaS chrome - a title-block header,
# monospace for identifiers (ids/tables/columns read like a parts
# schedule), and add/remove/modify treated as redline markup (green/
# red/amber left-edge bars) instead of plain colored text.

PAGE_HEAD = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProjxDiff — Model &amp; Database Preview</title>
<style>
  :root {
    --bg: #F5F8FA;
    --panel: #FFFFFF;
    --ink: #17222C;
    --ink-muted: #5C6B78;
    --border: #D7E0E7;
    --border-strong: #B7C4CE;
    --accent: #1D4E77;
    --accent-hover: #163C5C;
    --accent-soft: #E6EEF4;
    --old: #5C6B78;
    --old-soft: #EEF1F3;
    --ok: #1F7A4D;
    --ok-soft: #E7F4ED;
    --err: #C23B22;
    --err-soft: #FBEAE6;
    --warn: #B8790A;
    --warn-soft: #FBF1DF;
    --radius: 6px;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "IBM Plex Sans", Arial, sans-serif;
    --font-mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, "SFMono-Regular", monospace;
  }
  * { box-sizing: border-box; }
  body {
    font-family: var(--font-sans);
    background: var(--bg);
    color: var(--ink);
    max-width: 980px;
    margin: 0 auto;
    padding: 40px 20px 80px;
    line-height: 1.45;
  }
  .titleblock {
    border-top: 3px solid var(--accent);
    border-bottom: 1px solid var(--border-strong);
    padding: 18px 0 16px;
    margin-bottom: 28px;
  }
  .eyebrow {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    margin: 0 0 6px;
  }
  h1 {
    font-size: 23px;
    font-weight: 650;
    letter-spacing: -0.01em;
    margin: 0 0 6px;
  }
  .lede { color: var(--ink-muted); font-size: 14px; max-width: 62ch; margin: 0; }
  h2 {
    font-family: var(--font-mono);
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink-muted);
    margin: 30px 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .row { display: flex; gap: 20px; flex-wrap: wrap; align-items: stretch; }
  .panel {
    flex: 1;
    min-width: 320px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    border-top: 3px solid var(--old);
    padding: 18px 20px 20px;
  }
  .panel.side-new { border-top-color: var(--accent); }
  .panel-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-muted);
    margin: 0 0 14px;
  }
  label { display: block; margin-top: 12px; font-size: 13.5px; font-weight: 500; }
  label .muted { font-weight: 400; }
  input[type=text], input[type=password], input[type=file] {
    width: 100%;
    padding: 8px 10px;
    margin-top: 4px;
    font-size: 14px;
    font-family: var(--font-sans);
    color: var(--ink);
    background: var(--panel);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    transition: border-color 120ms ease, box-shadow 120ms ease;
  }
  input[type=text], input[type=password] { font-family: var(--font-mono); font-size: 13.5px; }
  input[type=file] { font-family: var(--font-sans); padding: 6px 8px; }
  input:focus-visible {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  .subtle-block {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px dashed var(--border);
  }
  .subtle-block > p.muted { margin: 0 0 2px; }
  .radio-row { margin-top: 10px; display: flex; gap: 18px; flex-wrap: wrap; }
  .radio-row label {
    display: flex; align-items: center; gap: 6px;
    margin-top: 0; font-weight: 400; font-size: 13.5px; cursor: pointer;
  }
  .radio-row input[type=radio] { accent-color: var(--accent); width: 15px; height: 15px; }
  .sqlauth { margin-top: 4px; }
  button {
    background: var(--accent);
    color: #fff;
    border: none;
    padding: 11px 26px;
    border-radius: var(--radius);
    font-size: 14.5px;
    font-weight: 600;
    letter-spacing: 0.01em;
    cursor: pointer;
    margin-top: 22px;
    transition: background 120ms ease;
  }
  button:hover { background: var(--accent-hover); }
  button:focus-visible { outline: 3px solid var(--accent-soft); outline-offset: 2px; }
  table { border-collapse: collapse; width: 100%; margin-top: 4px; font-size: 13px; }
  td, th {
    padding: 7px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-family: var(--font-mono);
  }
  th {
    font-family: var(--font-sans);
    font-weight: 600;
    font-size: 11.5px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-muted);
    background: var(--old-soft);
    border-bottom: 1px solid var(--border-strong);
  }
  tr.diff-added  { border-left: 3px solid var(--ok); }
  tr.diff-removed { border-left: 3px solid var(--err); }
  tr.diff-modified { border-left: 3px solid var(--warn); }
  tr.diff-added td:first-child,
  tr.diff-removed td:first-child,
  tr.diff-modified td:first-child { font-family: var(--font-sans); font-weight: 600; }
  .status { border-radius: var(--radius); padding: 12px 14px; font-size: 13.5px; margin-top: 4px; }
  .status.ok   { background: var(--ok-soft);   color: #145536; }
  .status.warn { background: var(--warn-soft); color: #8A5B08; }
  .status.err  { background: var(--err-soft);  color: #932A17; }
  .status.muted { background: var(--old-soft); color: var(--ink-muted); }
  .ok { color: var(--ok); } .warn { color: var(--warn); } .err { color: var(--err); }
  .muted { color: var(--ink-muted); font-size: 13px; }
  .formula-cell {
    font-family: var(--font-mono);
    font-size: 12.5px;
    white-space: pre-wrap;
    word-break: break-word;
    max-width: 320px;
    vertical-align: top;
  }
  .crumb-cell { font-size: 12.5px; vertical-align: top; max-width: 220px; }
  code {
    font-family: var(--font-mono);
    background: var(--accent-soft);
    color: var(--accent-hover);
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.93em;
  }
  a { color: var(--accent); }
  a:hover { color: var(--accent-hover); }
  .backlink { display: inline-block; margin-bottom: 4px; font-size: 13.5px; }
  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; }
  }
</style>
</head><body>
"""
PAGE_TAIL = "</body></html>"


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(fields: dict) -> None:
    values = {}
    for side in ("old", "new"):
        for suf in REMEMBERED_SUFFIXES:
            key = f"{side}_{suf}"
            values[key] = text_field(fields, key)
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
    except Exception:
        pass  # remembering is a convenience, never worth failing the request over


def side_fieldset(side: str, title: str, values: dict) -> str:
    def v(suffix: str) -> str:
        return html.escape(values.get(f"{side}_{suffix}", ""), quote=True)

    auth_val = values.get(f"{side}_auth", "windows")
    windows_checked = "checked" if auth_val != "sql" else ""
    sql_checked = "checked" if auth_val == "sql" else ""
    sql_display = "block" if auth_val == "sql" else "none"
    panel_class = "panel side-new" if side == "new" else "panel"

    if side == "new":
        server_hint = "(leave blank to reuse Old/PROD's server + database below)"
    else:
        server_hint = "(optional — leave blank to skip the database step)"

    return f"""
    <div class="{panel_class}">
      <p class="panel-title">{title}</p>
      <label>Project file <span class="muted">(.driveprojx)</span>
        <input type="file" name="{side}_file" accept=".driveprojx" required>
      </label>
      <label>SQL Server <span class="muted">{server_hint}</span>
        <input type="text" name="{side}_server" value="{v('server')}" placeholder="e.g. SQLBOX\\DWGROUP">
      </label>
      <label>Database
        <input type="text" name="{side}_database" value="{v('database')}" placeholder="e.g. DriveWorksGroup">
      </label>
      <div class="radio-row">
        <label><input type="radio" name="{side}_auth" value="windows" {windows_checked}
                 onchange="toggleAuth('{side}')"> Windows login (this PC's account)</label>
        <label><input type="radio" name="{side}_auth" value="sql" {sql_checked}
                 onchange="toggleAuth('{side}')"> SQL Server login</label>
      </div>
      <div id="{side}_sqlauth" class="sqlauth" style="display:{sql_display};">
        <label>SQL username
          <input type="text" name="{side}_user" value="{v('user')}">
        </label>
        <label>SQL password <span class="muted">(never saved — re-enter each time)</span>
          <input type="password" name="{side}_password" autocomplete="off">
        </label>
      </div>
      <div class="subtle-block">
        <p class="muted">Already know the model mapping table? Fill this in to use it directly instead of guessing:</p>
        <label>Table name <span class="muted">e.g. CapturedComponents</span>
          <input type="text" name="{side}_table" value="{v('table')}">
        </label>
        <label>Id column <span class="muted">e.g. Id</span>
          <input type="text" name="{side}_idcol" value="{v('idcol')}">
        </label>
        <label>Name column <span class="muted">e.g. Path</span>
          <input type="text" name="{side}_namecol" value="{v('namecol')}">
        </label>
        <label>Schema <span class="muted">(default dbo)</span>
          <input type="text" name="{side}_schema" value="{v('schema')}" placeholder="dbo">
        </label>
      </div>
      <div class="subtle-block">
        <p class="muted">Know the property/dimension mapping table too (for D1@Sketch1-style names)?
        Separate from the model table above, since it's usually a different table:</p>
        <label>Table name <span class="muted">e.g. CapturedProperties</span>
          <input type="text" name="{side}_prop_table" value="{v('prop_table')}">
        </label>
        <label>Id column <span class="muted">e.g. Id</span>
          <input type="text" name="{side}_prop_idcol" value="{v('prop_idcol')}">
        </label>
        <label>Name column <span class="muted">e.g. Name</span>
          <input type="text" name="{side}_prop_namecol" value="{v('prop_namecol')}">
        </label>
        <label>Schema <span class="muted">(default dbo)</span>
          <input type="text" name="{side}_prop_schema" value="{v('prop_schema')}" placeholder="dbo">
        </label>
      </div>
    </div>
    """


def build_form(values: dict) -> str:
    forget_link = ('<p class="muted"><a href="/reset">Forget saved values</a></p>' if values else "")
    return (
        PAGE_HEAD
        + """
        <div class="titleblock">
          <p class="eyebrow">DriveWorks project comparison</p>
          <h1>ProjxDiff — Model &amp; Database Preview</h1>
          <p class="lede">Pick the two project files, and (optionally) the SQL Server each one connects
          to. Nothing is written to either database — only SELECT statements are ever issued — and
          nothing here leaves your PC. Server, database, and table fields are remembered between runs
          on this PC; passwords never are.</p>
        </div>
        """
        + forget_link
        + '<p class="muted"><a href="/probe">Debug: test a single id directly against the database &rarr;</a></p>'
        + '<form action="/compare" method="post" enctype="multipart/form-data">'
        + '<div class="row">'
        + side_fieldset("old", "Old / PROD project", values)
        + side_fieldset("new", "New / DEV project", values)
        + "</div>"
        + '<button type="submit">Compare</button>'
        + "</form>"
        + """<script>
function toggleAuth(side) {
  var sql = document.querySelector('input[name="' + side + '_auth"][value="sql"]').checked;
  document.getElementById(side + '_sqlauth').style.display = sql ? 'block' : 'none';
}
</script>"""
        + PAGE_TAIL
    )


# ------------------------------------------------------------------ probe
# A standalone diagnostic: test one already-confirmed-real id against a
# table/column guess, bypassing project files entirely. Useful when an id
# came from SSMS/DriveWorks Administrator rather than a specific project,
# so it may not be one the comparison flow's own project files reference.

def build_probe_form(values: dict) -> str:
    def v(key: str) -> str:
        return html.escape(values.get(key, ""), quote=True)

    auth_val = values.get("old_auth", "windows")
    windows_checked = "checked" if auth_val != "sql" else ""
    sql_checked = "checked" if auth_val == "sql" else ""
    sql_display = "block" if auth_val == "sql" else "none"

    return (
        PAGE_HEAD
        + """
        <div class="titleblock">
          <p class="eyebrow">Diagnostic</p>
          <h1>Test a single id directly</h1>
          <p class="lede">Skip the project file entirely. Point this at a table/column guess and one
          id you already know is really in the database (e.g. one you pulled in SSMS), and see
          exactly what query ran and what came back — including the real SQL error, if there is one.</p>
        </div>
        <p class="muted"><a href="/">&larr; back to comparison</a></p>
        """
        + '<form action="/probe-result" method="post">'
        + '<div class="panel" style="max-width:480px;">'
        + f"""
          <label>SQL Server
            <input type="text" name="server" value="{v('old_server')}" placeholder="e.g. SQLBOX\\DWGROUP">
          </label>
          <label>Database
            <input type="text" name="database" value="{v('old_database')}" placeholder="e.g. AJPL_DEV">
          </label>
          <div class="radio-row">
            <label><input type="radio" name="auth" value="windows" {windows_checked}
                     onchange="toggleProbeAuth()"> Windows login</label>
            <label><input type="radio" name="auth" value="sql" {sql_checked}
                     onchange="toggleProbeAuth()"> SQL Server login</label>
          </div>
          <div id="probe_sqlauth" class="sqlauth" style="display:{sql_display};">
            <label>SQL username
              <input type="text" name="user" value="{v('old_user')}">
            </label>
            <label>SQL password
              <input type="password" name="password" autocomplete="off">
            </label>
          </div>
          <label>Table name
            <input type="text" name="table" value="{v('old_table')}" placeholder="e.g. CapturedComponents">
          </label>
          <label>Id column
            <input type="text" name="idcol" value="{v('old_idcol')}" placeholder="e.g. Id">
          </label>
          <label>Name column
            <input type="text" name="namecol" value="{v('old_namecol')}" placeholder="e.g. Path">
          </label>
          <label>Schema
            <input type="text" name="schema" value="{v('old_schema') or 'dbo'}" placeholder="dbo">
          </label>
          <label>Id to test <span class="muted">a real id you already confirmed is in the table</span>
            <input type="text" name="test_id" placeholder="365EBC8C-82FF-44F0-AFBE-82848B4275B1" required>
          </label>
        """
        + '<button type="submit">Test this id</button>'
        + "</div></form>"
        + """<script>
function toggleProbeAuth() {
  var sql = document.querySelector('input[name="auth"][value="sql"]').checked;
  document.getElementById('probe_sqlauth').style.display = sql ? 'block' : 'none';
}
</script>"""
        + PAGE_TAIL
    )


def run_probe(params: dict) -> str:
    server = params.get("server", "").strip()
    database = params.get("database", "").strip()
    auth = params.get("auth", "windows")
    trusted = auth != "sql"
    user = params.get("user", "").strip()
    password = params.get("password", "").strip()
    table = params.get("table", "").strip()
    idcol = params.get("idcol", "").strip()
    namecol = params.get("namecol", "").strip()
    schema = params.get("schema", "").strip() or "dbo"
    test_id = params.get("test_id", "").strip()

    head = (
        PAGE_HEAD
        + """
        <div class="titleblock">
          <p class="eyebrow">Diagnostic</p>
          <h1>Single-id test result</h1>
          <p class="lede"><a href="/probe">&larr; test another</a> &nbsp;·&nbsp;
          <a href="/">back to comparison</a></p>
        </div>
        """
    )

    if not (server and database and table and idcol and namecol and test_id):
        return (head + "<div class='status err'>Server, database, table, both columns, and the id "
                "to test are all required.</div>" + PAGE_TAIL)

    db = dbsource.DwDatabase(label="probe", server=server, database=database,
                              user=user, password=password, trusted=trusted)
    info = db.debug_lookup(table, idcol, namecol, test_id, schema=schema)
    db.close()

    variants = " &nbsp; ".join(f"<code>{html.escape(x)}</code>" for x in info["variants_tried"])
    rows_html = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in info["raw_rows"]
    )

    if info["error"]:
        status = f"<div class='status err'>Query failed: {html.escape(info['error'])}</div>"
    elif info["raw_rows"]:
        status = f"<div class='status ok'>Matched — {len(info['raw_rows'])} row(s) came back.</div>"
    else:
        status = ("<div class='status warn'>Connected and the query ran clean, but zero rows came back "
                   "for this id. Double check the table/schema is the one this id actually lives in.</div>")

    rows_section = (
        f"<h2>Rows returned</h2><table><tr><th>{html.escape(idcol)}</th><th>{html.escape(namecol)}</th></tr>"
        f"{rows_html}</table>" if info["raw_rows"] else ""
    )

    return (
        head
        + status
        + "<h2>What was tried</h2>"
        + f"<p>Normalised id: <code>{html.escape(info['normalized_id'])}</code></p>"
        + f"<p>Parameter forms sent: {variants}</p>"
        + f"<p>SQL: <code>{html.escape(info['sql'])}</code></p>"
        + rows_section
        + PAGE_TAIL
    )


# ------------------------------------------------------------- multipart

def parse_multipart(content_type: str, body: bytes) -> dict:
    """Minimal multipart/form-data parser (stdlib only, via email).
    Returns {field_name: {"value": bytes, "filename": str|None}}."""
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    msg = email.message_from_bytes(header + body, policy=email.policy.default)
    fields: dict = {}
    if not msg.is_multipart():
        return fields
    for part in msg.iter_parts():
        cd = part.get("Content-Disposition", "")
        name = None
        filename = None
        for piece in cd.split(";"):
            piece = piece.strip()
            if piece.startswith('name="'):
                name = piece[6:-1]
            elif piece.startswith('filename="'):
                filename = piece[10:-1]
        if name is None:
            continue
        fields[name] = {"value": part.get_payload(decode=True), "filename": filename}
    return fields


def text_field(fields: dict, name: str) -> str:
    v = fields.get(name, {}).get("value")
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace").strip()
    return str(v).strip()


def connection_fields(side: str, fields: dict) -> dict:
    """Server/database/auth/user/password to use for this side. If this
    is the "new" side and its own server/database were left blank, falls
    back to the "old" side's connection - covers the common case where
    both environments live in the same database, without making the
    person type the same server/database twice."""
    server = text_field(fields, f"{side}_server")
    database = text_field(fields, f"{side}_database")
    src = side
    if side == "new" and not (server and database):
        server = text_field(fields, "old_server")
        database = text_field(fields, "old_database")
        src = "old"
    return {
        "server": server,
        "database": database,
        "auth": text_field(fields, f"{src}_auth") or "windows",
        "user": text_field(fields, f"{src}_user"),
        "password": text_field(fields, f"{src}_password"),
    }


# -------------------------------------------------------------- analysis

def extract_projx(data: bytes) -> str:
    tmp = tempfile.mkdtemp(prefix="dw_web_")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    return tmp


def sets_table(added, removed, modified, common, old_sets, new_sets) -> str:
    rows = []
    for n in added:
        rows.append(f"<tr class='diff-added'><td>+ added</td><td>{html.escape(n)}</td>"
                     f"<td>{html.escape(new_sets[n].set_type)}</td></tr>")
    for n in removed:
        rows.append(f"<tr class='diff-removed'><td>- removed</td><td>{html.escape(n)}</td>"
                     f"<td>{html.escape(old_sets[n].set_type)}</td></tr>")
    for n in modified:
        rows.append(f"<tr class='diff-modified'><td>~ modified</td><td>{html.escape(n)}</td>"
                     f"<td>generation rule or type changed</td></tr>")
    if not rows:
        rows.append("<tr><td colspan=3 class='muted'>No sets added, removed, or modified.</td></tr>")
    unchanged = len(common) - len(modified)
    return (f"<table><tr><th>Change</th><th>Name</th><th>Detail</th></tr>{''.join(rows)}</table>"
            f"<p class='muted' style='margin-top:8px;'>{unchanged} unchanged set(s) not shown above.</p>")


def _filename(path: str) -> str:
    """Just the file name - the segment after the last backslash - since
    that's what's actually useful to scan when comparing model lists
    against the real assembly tree. The full path is kept as a hover
    tooltip in case the folder ever matters for telling two same-named
    parts apart."""
    return path.rsplit("\\", 1)[-1] if path else path


def db_section(side: str, fields: dict, ids: set, kind: str = "") -> tuple:
    """Returns (status_and_listing_html, resolved_dict) where resolved_dict
    is {norm_id: name} - empty if nothing could be resolved. Returning the
    resolved dict (not just HTML) is what lets run_analysis diff the two
    sides by NAME rather than by raw id, since two different DriveWorks
    databases can assign a different id to what a person would call the
    same file.

    kind picks which set of override fields to read: "" (the original
    model lookup, field names unchanged for backward compatibility with
    existing saved settings) or "prop" (a second, independent table/
    column override for the CPRef/CERef property-name lookup, so the two
    don't collide on the same side)."""
    conn = connection_fields(side, fields)
    if not conn["server"] or not conn["database"]:
        return "<div class='status muted'>No server/database given — skipped.</div>", {}

    db = dbsource.DwDatabase(label=side, server=conn["server"], database=conn["database"],
                              user=conn["user"], password=conn["password"], trusted=conn["auth"] != "sql")
    if not db.connect():
        return ("<div class='status err'>Could not connect. Check the server/database name, and that "
                "<code>pyodbc</code> plus a SQL Server ODBC driver are installed on this PC.</div>", {})

    prefix = f"{side}_{kind}" if kind else side
    override_table = text_field(fields, f"{prefix}_table")
    override_idcol = text_field(fields, f"{prefix}_idcol")
    override_namecol = text_field(fields, f"{prefix}_namecol")
    override_schema = text_field(fields, f"{prefix}_schema") or "dbo"

    if override_table and override_idcol and override_namecol:
        id_list = list(ids)
        got = db.lookup(override_table, override_idcol, override_namecol, id_list, schema=override_schema)
        db.close()
        norm_ids = {C._norm(i) for i in id_list}
        hit = len(set(got) & norm_ids)
        rate = hit / max(1, len(norm_ids))
        cls = "ok" if rate >= 0.5 else "warn"

        all_rows = "".join(
            f"<tr><td>{html.escape(k)}</td>"
            f'<td title="{html.escape(v, quote=True)}">{html.escape(_filename(v))}</td></tr>'
            for k, v in sorted(got.items(), key=lambda kv: _filename(kv[1]).lower())
        )
        table_id = f"{prefix}_resolved_table"
        filter_id = f"{prefix}_resolved_filter"
        listing = (
            f"""
            <label style="margin-top:0;">Filter these {len(got)} resolved name(s)
              <input type="text" id="{filter_id}" placeholder="type to filter by name or id"
                     oninput="filterResolvedTable('{table_id}', '{filter_id}')">
            </label>
            <table id="{table_id}" style="margin-top:8px;">
              <tr><th>Id</th><th>Resolved name</th></tr>{all_rows}
            </table>
            """
            if got else "<p class='muted'>No ids from this project matched.</p>"
        )
        status = (
            f"<div class='status {cls}'>Using <code>{html.escape(override_schema)}.{html.escape(override_table)}</code> "
            f"({html.escape(override_idcol)} &rarr; {html.escape(override_namecol)}): "
            f"resolved {hit} of {len(norm_ids)} id(s) ({rate*100:.1f}%).</div>"
            f"<div style='margin-top:10px;'>{listing}</div>"
        )
        return status, got

    candidates = db.find_id_name_tables()
    probe = list(ids)[:400]
    results = []
    for c in candidates:
        for id_col in c["id_cols"]:
            for name_col in c["name_cols"]:
                got = db.lookup(c["table"], id_col, name_col, probe, schema=c["schema"])
                norm_got = {C._norm(k) for k in got}
                hit = len(norm_got & {C._norm(i) for i in probe})
                rate = hit / max(1, len(probe))
                if rate >= 0.25:
                    results.append((rate, c["schema"], c["table"], id_col, name_col, hit))
    results.sort(reverse=True)

    if not results:
        db.close()
        return (f"<div class='status warn'>Connected, but no table matched well enough. "
                f"{len(candidates)} table(s) had an id-ish + name-ish column, none hit &ge;25%.</div>", {})

    rows = "".join(
        f"<tr><td>{rate*100:.1f}%</td><td>{html.escape(schema)}.{html.escape(table)}</td>"
        f"<td>{html.escape(id_col)} &rarr; {html.escape(name_col)}</td><td>{hit}</td></tr>"
        for rate, schema, table, id_col, name_col, hit in results[:8]
    )
    best_rate, best_schema, best_table, best_idcol, best_namecol, best_hit = results[0]

    resolved = {}
    auto_note = ""
    if best_rate >= 0.5:
        resolved = db.lookup(best_table, best_idcol, best_namecol, list(ids), schema=best_schema)
        auto_note = (f"<p class='muted' style='margin-top:6px;'>Auto-resolved all {len(ids)} id(s) "
                      f"against the best match for the models diff below.</p>")
    db.close()

    status = (
        f"<div class='status ok'>Connected. Best match: "
        f"<code>{html.escape(best_schema)}.{html.escape(best_table)}</code> "
        f"({html.escape(best_idcol)} &rarr; {html.escape(best_namecol)}), {best_rate*100:.1f}% hit rate.</div>"
        f"<table style='margin-top:10px;'><tr><th>Hit rate</th><th>Table</th><th>Columns</th><th># ids matched</th></tr>"
        f"{rows}</table>"
        f"{auto_note}"
    )
    return status, resolved


def resolve_captured_property_names(side: str, fields: dict, ccrefs: set) -> tuple:
    """Automatically resolve D1@Sketch1-style property/entity names by
    decoding CapturedComponents.Data for every captured file (ccref) the
    project actually uses. Reuses the SAME server/database/auth fields as
    the model lookup, since it's the same table - no separate override
    needed for this to work, unlike the generic table-guessing path.
    Returns (status_html, resolved_dict)."""
    conn = connection_fields(side, fields)
    if not conn["server"] or not conn["database"] or not ccrefs:
        return "", {}

    db = dbsource.DwDatabase(label=f"{side}-propdata", server=conn["server"], database=conn["database"],
                              user=conn["user"], password=conn["password"], trusted=conn["auth"] != "sql")
    if not db.connect():
        return "", {}

    names = db.fetch_captured_property_names(ccrefs)
    db.close()

    if not names:
        return ("<p class='muted'>Auto-decode of <code>CapturedComponents.Data</code> found no named "
                "properties for this project's captured files.</p>", {})
    return (f"<p class='ok'>Auto-decoded <code>CapturedComponents.Data</code> for "
            f"{len(ccrefs)} captured file(s): {len(names)} named propert{'y' if len(names)==1 else 'ies'}/"
            f"entities found.</p>", names)


def models_diff_table(old_resolved: dict, new_resolved: dict) -> str:
    """Diff two sides' resolved id->name maps by NAME, not by raw id.
    Different DriveWorks databases can assign a different id to what a
    person would call the same file, so id equality isn't a safe way to
    tell "unchanged" from "id just churned" apart - name equality is the
    portable one."""
    if not old_resolved and not new_resolved:
        return ("<p class='muted'>Nothing to diff yet — fill in a server/database (and, if needed, "
                "the table override) for at least one side above, then re-run Compare.</p>")

    old_names = {v for v in old_resolved.values() if v}
    new_names = {v for v in new_resolved.values() if v}
    added = sorted((new_names - old_names), key=lambda n: _filename(n).lower())
    removed = sorted((old_names - new_names), key=lambda n: _filename(n).lower())
    unchanged = len(old_names & new_names)

    rows = []
    for n in added:
        rows.append(f"<tr class='diff-added'><td>+ added</td>"
                     f'<td title="{html.escape(n, quote=True)}">{html.escape(_filename(n))}</td></tr>')
    for n in removed:
        rows.append(f"<tr class='diff-removed'><td>- removed</td>"
                     f'<td title="{html.escape(n, quote=True)}">{html.escape(_filename(n))}</td></tr>')
    if not rows:
        rows.append("<tr><td colspan=2 class='muted'>No models added or removed.</td></tr>")

    return (
        f"<table><tr><th>Change</th><th>Model</th></tr>{''.join(rows)}</table>"
        f"<p class='muted' style='margin-top:8px;'>{unchanged} model(s) unchanged — matched by resolved "
        f"name, not by database id, since the same real file can carry a different id in each database.</p>"
    )


def property_rules_diff(old_idx, new_idx, old_resolved: dict, new_resolved: dict,
                         old_prop_resolved: dict = None, new_prop_resolved: dict = None) -> str:
    """Diff every driven property (D1@Sketch1-style) between the two
    projects. Matched by rule_id, which is confirmed unique per
    placement - even when the same file is placed in the tree multiple
    times, each placement's rules have their own rule_id, so this never
    silently merges two placements into one row. Each row is labeled
    with a breadcrumb (built from the models we already resolved) so a
    repeated file's placements are told apart by tree position, since
    cp_ref/ce_ref alone can't do that - they're shared across every
    placement of the same file. A second, independent lookup
    (old/new_prop_resolved) turns cp_ref/ce_ref themselves into a
    D1@Sketch1-style property name when that mapping table is known;
    without it, the raw cp_ref/ce_ref guid is shown instead. Rows where
    neither side has a formula (an unbound placeholder) are skipped;
    those aren't real rules."""
    old_prop_resolved = old_prop_resolved or {}
    new_prop_resolved = new_prop_resolved or {}
    old_by_rid = {C._norm(p.rule_id): p for p in old_idx.property_rules if p.rule_id}
    new_by_rid = {C._norm(p.rule_id): p for p in new_idx.property_rules if p.rule_id}

    added_rids = set(new_by_rid) - set(old_by_rid)
    removed_rids = set(old_by_rid) - set(new_by_rid)
    shared_rids = set(old_by_rid) & set(new_by_rid)
    modified_rids = {rid for rid in shared_rids if old_by_rid[rid].formula != new_by_rid[rid].formula}

    def crumb(pr, idx, resolved):
        return idx.breadcrumb(pr.owner_path, resolved) or "(unresolved placement)"

    rows = []  # (kind, breadcrumb, property_label, old_formula, new_formula)
    for rid in sorted(added_rids):
        pr = new_by_rid[rid]
        if pr.formula:
            rows.append(("added", crumb(pr, new_idx, new_resolved),
                         C.property_label(pr, new_prop_resolved), "", pr.formula))
    for rid in sorted(removed_rids):
        pr = old_by_rid[rid]
        if pr.formula:
            rows.append(("removed", crumb(pr, old_idx, old_resolved),
                         C.property_label(pr, old_prop_resolved), pr.formula, ""))
    for rid in sorted(modified_rids):
        op, npr = old_by_rid[rid], new_by_rid[rid]
        rows.append(("modified", crumb(npr, new_idx, new_resolved),
                     C.property_label(npr, new_prop_resolved), op.formula, npr.formula))

    total_compared = len(shared_rids) + len(added_rids) + len(removed_rids)
    if not rows:
        return f"<p class='muted'>No rule content changes found ({total_compared} driven properties compared).</p>"

    kind_label = {"added": "+ added", "removed": "- removed", "modified": "~ modified"}

    def fcell(text):
        return html.escape(text) if text else "<span class='muted'>(blank)</span>"

    table_rows = "".join(
        f"<tr class='diff-{kind}'><td>{kind_label[kind]}</td>"
        f"<td class='crumb-cell'>{html.escape(c)}</td>"
        f"<td class='crumb-cell'>{html.escape(p)}</td>"
        f"<td class='formula-cell'>{fcell(of)}</td>"
        f"<td class='formula-cell'>{fcell(nf)}</td></tr>"
        for kind, c, p, of, nf in rows
    )
    unchanged = total_compared - len(modified_rids)
    return (
        '<label style="margin-top:0;">Filter these rule changes\n'
        '  <input type="text" id="rulechanges_filter" placeholder="type to filter by name or formula"\n'
        '         oninput="filterResolvedTable(\'rulechanges_table\', \'rulechanges_filter\')">\n'
        "</label>"
        f"<table id='rulechanges_table' style='margin-top:8px;'>"
        f"<tr><th>Change</th><th>Placement</th><th>Property</th><th>Old formula</th><th>New formula</th></tr>"
        f"{table_rows}</table>"
        f"<p class='muted' style='margin-top:8px;'>{unchanged} driven propert{'y' if unchanged==1 else 'ies'} "
        f"unchanged out of {total_compared} compared, not shown above.</p>"
    )


def run_analysis(fields: dict) -> str:
    tmp_dirs = []
    try:
        old_bytes = fields.get("old_file", {}).get("value")
        new_bytes = fields.get("new_file", {}).get("value")
        if not old_bytes or not new_bytes:
            return (PAGE_HEAD + "<h1>Error</h1><p class='err'>Both project files are required."
                     "</p><p><a href='/'>&larr; back</a></p>" + PAGE_TAIL)

        old_root = extract_projx(old_bytes)
        new_root = extract_projx(new_bytes)
        tmp_dirs += [old_root, new_root]

        old_idx = C.build_component_index(old_root)
        new_idx = C.build_component_index(new_root)

        old_sets = {s.name: s for s in old_idx.sets.values()}
        new_sets = {s.name: s for s in new_idx.sets.values()}
        added = sorted(set(new_sets) - set(old_sets))
        removed = sorted(set(old_sets) - set(new_sets))
        common = sorted(set(old_sets) & set(new_sets))
        modified = [n for n in common if old_sets[n].rule != new_sets[n].rule
                    or old_sets[n].set_type != new_sets[n].set_type]

        old_keys = old_idx.all_lookup_keys()
        new_keys = new_idx.all_lookup_keys()
        old_prop_keys = old_idx.all_property_keys()
        new_prop_keys = new_idx.all_property_keys()

        old_db_html, old_resolved = db_section("old", fields, old_keys)
        new_db_html, new_resolved = db_section("new", fields, new_keys)
        old_prop_html, old_prop_resolved = db_section("old", fields, old_prop_keys, kind="prop")
        new_prop_html, new_prop_resolved = db_section("new", fields, new_prop_keys, kind="prop")

        old_auto_html, old_auto_names = resolve_captured_property_names(
            "old", fields, set(old_idx.trid_to_ccref.values()))
        new_auto_html, new_auto_names = resolve_captured_property_names(
            "new", fields, set(new_idx.trid_to_ccref.values()))
        # Auto-decoding CapturedComponents.Data is the confirmed real
        # mechanism, so it takes priority; a manual table-guess override
        # (if one was given) only fills in anything the blobs didn't name.
        old_prop_resolved = {**old_prop_resolved, **old_auto_names}
        new_prop_resolved = {**new_prop_resolved, **new_auto_names}

        return (
            PAGE_HEAD
            + """
            <div class="titleblock">
              <p class="eyebrow">DriveWorks project comparison</p>
              <h1>Comparison result</h1>
              <p class="lede"><a class="backlink" href="/">&larr; back to form</a></p>
            </div>
            """
            + "<h2>Component Sets</h2>"
            + sets_table(added, removed, modified, common, old_sets, new_sets)
            + "<h2>Models</h2>"
            + models_diff_table(old_resolved, new_resolved)
            + "<h2>Rule Changes</h2>"
            + property_rules_diff(old_idx, new_idx, old_resolved, new_resolved,
                                   old_prop_resolved, new_prop_resolved)
            + "<h2>Component / model IDs needing the database</h2>"
            + f"<p>Old project: <b>{len(old_keys)}</b> id(s). New project: <b>{len(new_keys)}</b> id(s).</p>"
            + "<h2>Old / PROD database</h2>"
            + old_db_html
            + "<h2>New / DEV database</h2>"
            + new_db_html
            + "<h2>Property / dimension names — Old / PROD</h2>"
            + old_auto_html + old_prop_html
            + "<h2>Property / dimension names — New / DEV</h2>"
            + new_auto_html + new_prop_html
            + """<script>
function filterResolvedTable(tableId, filterId) {
  var q = document.getElementById(filterId).value.toLowerCase();
  var rows = document.getElementById(tableId).querySelectorAll('tr');
  for (var i = 1; i < rows.length; i++) {  // skip header row
    var text = rows[i].textContent.toLowerCase();
    rows[i].style.display = text.indexOf(q) === -1 ? 'none' : '';
  }
}
</script>"""
            + PAGE_TAIL
        )
    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", ""):
            self._send_html(build_form(load_settings()))
        elif self.path == "/probe":
            self._send_html(build_probe_form(load_settings()))
        elif self.path == "/reset":
            try:
                os.remove(SETTINGS_PATH)
            except FileNotFoundError:
                pass
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/compare":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            try:
                fields = parse_multipart(content_type, body)
                save_settings(fields)
                out = run_analysis(fields)
            except Exception as e:
                out = (PAGE_HEAD + f"<h1>Error</h1><p class='err'>{html.escape(str(e))}</p>"
                       "<p><a href='/'>&larr; back</a></p>" + PAGE_TAIL)
            self._send_html(out)
        elif self.path == "/probe-result":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            params = {k: v[0] for k, v in parse_qs(body).items()}
            try:
                out = run_probe(params)
            except Exception as e:
                out = (PAGE_HEAD + f"<h1>Error</h1><p class='err'>{html.escape(str(e))}</p>"
                       "<p><a href='/probe'>&larr; back</a></p>" + PAGE_TAIL)
            self._send_html(out)
        else:
            self.send_error(404)

    def _send_html(self, content: str):
        data = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"ProjxDiff web preview running at {url}")
    print("Leave this window open. Close it (or press Ctrl+C) to stop.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()