"""Static work-metrics dashboard for the nightly sync.

Reads data_dir/metrics.sqlite (and the census, when present) and regenerates
data_dir/dashboard.html: a fully self-contained page (inline CSS/SVG, no
external resources) showing change activity over time, the most-active
projects / users / categories, a needs-attention panel (pending projects,
unmapped users, name conflicts), and a recent-changes table that links into
the dated drill-down reports.

Runs automatically at the end of each nightly sync (config "dashboard": true),
or standalone:

    python -m dw_compare --dashboard config.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote

from . import census as census_mod

TIME_WINDOW_DAYS = 60      # daily activity chart
RANK_WINDOW_DAYS = 30      # projects / users / categories bars
RECENT_ROWS = 20

CATEGORY_LABELS = {
    'variables': 'Variables', 'constants': 'Constants',
    'calc_tables': 'Calculation Tables', 'component_tasks': 'Component Tasks',
    'documents': 'Documents', 'lookup_tables': 'Lookup Tables',
    'data_tables': 'Data Tables', 'spec_macros': 'Specification Macros',
    'nav_steps': 'Navigation Steps', 'forms': 'Forms', 'project': 'Project',
}


# ------------------------------------------------------------ svg helpers ----

def _col_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Column with a rounded data-end (top) anchored to the baseline."""
    r = min(r, w / 2, h)
    return (f'M{x:.1f},{y + h:.1f} V{y + r:.1f} A{r},{r} 0 0 1 {x + r:.1f},{y:.1f} '
            f'H{x + w - r:.1f} A{r},{r} 0 0 1 {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z')


def _hbar_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Horizontal bar with a rounded data-end (right) anchored to the axis."""
    r = min(r, h / 2, w)
    return (f'M{x:.1f},{y:.1f} H{x + w - r:.1f} A{r},{r} 0 0 1 {x + w:.1f},{y + r:.1f} '
            f'V{y + h - r:.1f} A{r},{r} 0 0 1 {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z')


def _nice_max(v: int) -> int:
    if v <= 5:
        return 5
    mag = 10 ** (len(str(v)) - 1)
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def _daily_chart(days: list, counts: dict) -> str:
    """Column chart of total changes per day. Single series, hover tooltips."""
    W, H, PAD_L, PAD_B, PAD_T = 720, 180, 34, 20, 8
    plot_w, plot_h = W - PAD_L - 6, H - PAD_T - PAD_B
    top = _nice_max(max(counts.values(), default=0))
    step = plot_w / len(days)
    bar_w = max(2.0, step - 2)  # 2px surface gap between bars

    parts = []
    for frac in (0, 0.5, 1.0):
        y = PAD_T + plot_h * (1 - frac)
        val = int(top * frac)
        parts.append(f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" x2="{W - 6}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{val}</text>')

    label_every = max(1, len(days) // 8)
    for i, d in enumerate(days):
        x = PAD_L + i * step
        n = counts.get(d, 0)
        if n:
            h = plot_h * n / top
            parts.append(f'<path class="mark" d="{_col_path(x + 1, PAD_T + plot_h - h, bar_w, h)}" '
                         f'data-tip="{escape(d)}: {n} change{"" if n == 1 else "s"}"/>')
        if i % label_every == 0:
            parts.append(f'<text class="tick" x="{x + step / 2:.1f}" y="{H - 4}" '
                         f'text-anchor="middle">{escape(d[5:])}</text>')

    parts.append(f'<line class="axis" x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - 6}" y2="{PAD_T + plot_h}"/>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="Total element changes per day, last {len(days)} days">'
            + ''.join(parts) + '</svg>')


def _rank_chart(rows: list, aria: str) -> str:
    """Horizontal single-hue bars: [(label, count)], largest first."""
    if not rows:
        return '<p class="empty">No activity in this window.</p>'
    ROW_H, BAR_H, W, LABEL_W = 26, 12, 320, 118
    H = len(rows) * ROW_H + 4
    top = max(n for _, n in rows)
    plot_w = W - LABEL_W - 40

    parts = []
    for i, (label, n) in enumerate(rows):
        y = i * ROW_H + 4
        w = max(2.0, plot_w * n / top)
        disp = label if len(label) <= 18 else label[:17] + '…'
        parts.append(f'<text class="rlabel" x="{LABEL_W - 6}" y="{y + BAR_H - 1}" '
                     f'text-anchor="end">{escape(disp)}</text>')
        parts.append(f'<path class="mark" d="{_hbar_path(LABEL_W, y, w, BAR_H)}" '
                     f'data-tip="{escape(label)}: {n} change{"" if n == 1 else "s"}"/>')
        parts.append(f'<text class="rvalue" x="{LABEL_W + w + 6:.1f}" y="{y + BAR_H - 1}">{n}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" style="max-width:{W}px" role="img" '
            f'aria-label="{escape(aria)}">' + ''.join(parts) + '</svg>')


# --------------------------------------------------------------- queries ----

def _display_name(owner: str) -> str:
    """Human name from an owner value — identity strings keep the part before
    '<'; raw display names pass through."""
    return (owner or '').split('<')[0].strip() or '(unassigned)'


def _has_source_column(conn: sqlite3.Connection) -> bool:
    return any(row[1] == 'source'
               for row in conn.execute('PRAGMA table_info(category_changes)'))


def _collect(conn: sqlite3.Connection, today: date, source: str = None) -> dict:
    """Chart/tile data, optionally filtered to one source (site configs)."""
    q = conn.execute
    time_cut = (today - timedelta(days=TIME_WINDOW_DAYS - 1)).isoformat()
    rank_cut = (today - timedelta(days=RANK_WINDOW_DAYS - 1)).isoformat()
    week_cut = (today - timedelta(days=6)).isoformat()

    src = ' AND source = ?' if source is not None else ''

    def params(*base):
        return base + ((source,) if source is not None else ())

    daily = dict(q('SELECT run_date, SUM(added+removed+modified) FROM category_changes '
                   f'WHERE run_date >= ?{src} GROUP BY run_date',
                   params(time_cut)).fetchall())

    def ranked(col, cut, limit=10):
        rows = q(f'SELECT {col}, SUM(added+removed+modified) AS n FROM category_changes '
                 f'WHERE run_date >= ?{src} GROUP BY {col} ORDER BY n DESC LIMIT {int(limit)}',
                 params(cut)).fetchall()
        return [(r[0] or '(unassigned)', r[1]) for r in rows]

    owners = [(_display_name(o), n) for o, n in ranked('owner', rank_cut)]
    categories = [(CATEGORY_LABELS.get(c, c), n) for c, n in ranked('category', rank_cut)]

    return {
        'daily': daily,
        'projects': ranked('project', rank_cut),
        'owners': owners,
        'categories': categories,
        'last_night': q('SELECT COALESCE(SUM(added+removed+modified), 0) FROM category_changes '
                        'WHERE run_date = (SELECT MAX(run_date) FROM category_changes)'
                        f'{src}', params()).fetchone()[0],
        'changes_30d': q('SELECT COALESCE(SUM(added+removed+modified), 0) '
                         f'FROM category_changes WHERE run_date >= ?{src}',
                         params(rank_cut)).fetchone()[0],
        'active_7d': q('SELECT COUNT(DISTINCT project) FROM category_changes '
                       f'WHERE run_date >= ?{src}', params(week_cut)).fetchone()[0],
        'active_30d': q('SELECT COUNT(DISTINCT project) FROM category_changes '
                        f'WHERE run_date >= ?{src}', params(rank_cut)).fetchone()[0],
    }


def _collect_globals(conn: sqlite3.Connection, has_source: bool) -> dict:
    q = conn.execute
    src_col = 'source' if has_source else "''"
    recent = q(f'SELECT run_date, project, owner, SUM(added), SUM(removed), '
               f'SUM(modified), {src_col} FROM category_changes '
               f'GROUP BY run_date, {src_col}, project '
               'ORDER BY run_date DESC, project LIMIT ?', (RECENT_ROWS,)).fetchall()
    last_run = q('SELECT run_date, finished_at, projects_seen, projects_changed, errors '
                 'FROM runs ORDER BY id DESC LIMIT 1').fetchone()
    return {'recent': recent, 'last_run': last_run}


def _attention_html(census: dict) -> str:
    """Needs-attention card: pending projects, unmapped users, conflicts.
    Empty string when there is nothing to review — the quiet steady state."""
    pending = census_mod.pending_projects(census)
    unmapped = census_mod.unmapped_users(census)
    conflicts = census.get('conflicts', [])
    if not pending and not unmapped and not conflicts:
        return ''

    items = []
    for name, path in pending:
        items.append(f'<li><b>New project:</b> {escape(name)} '
                     f'<span class="muted">({escape(path)})</span> — track or ignore?</li>')
    for raw in unmapped:
        items.append(f'<li><b>Unmapped user:</b> {escape(raw)} — who is this?</li>')
    for c in conflicts:
        items.append(f'<li><b>Name conflict:</b> {escape(c.get("project", ""))} — '
                     f'{escape(c.get("path", ""))} clashes with '
                     f'{escape(c.get("registered", ""))}</li>')
    return (f'<section class="card attn"><h2>&#9888; Needs attention '
            f'({len(items)})</h2><ul>{"".join(items)}</ul>'
            '<p class="muted">Resolve in Projx Diff &rsaquo; Tools &rsaquo; '
            'Manage Nightly Sync (or edit census.json).</p></section>')


# ---------------------------------------------------------------- render ----

_DARK_VARS = """
    color-scheme: dark;
    --surface: #1a1a19; --page: #0d0d0d;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series: #3987e5; --critical: #d03b3b; --attn: #c98500;
"""

_CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb; --page: #f9f9f7;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series: #2a78d6; --critical: #d03b3b; --attn: #eda100;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { """ + _DARK_VARS + """ }
}
:root[data-theme="dark"] { """ + _DARK_VARS + """ }
.themeseg { float: right; display: inline-flex; gap: 4px; }
.themeseg button { border: 1px solid var(--border); background: var(--surface);
  color: var(--ink-2); border-radius: 7px; padding: 3px 12px; font: inherit;
  font-size: 12px; font-weight: 600; cursor: pointer; }
.themeseg button.on { background: var(--series); border-color: var(--series); color: #fff; }
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 1120px; padding: 20px 20px 48px;
  background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.4; }
h1 { font-size: 20px; margin: 0 0 2px; }
h2 { font-size: 14px; font-weight: 600; margin: 0 0 10px; color: var(--ink); }
.sub { color: var(--ink-2); font-size: 13px; margin: 0 0 18px; }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-bottom: 14px; }
.tile .v { font-size: 28px; font-weight: 650; }
.tile .l { font-size: 12px; color: var(--ink-2); }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 12px; margin-top: 14px; }
section.card { margin-top: 14px; }
.attn { border-left: 3px solid var(--attn); }
.attn ul { margin: 0 0 8px; padding-left: 20px; font-size: 13px; }
.attn li { margin: 3px 0; }
svg { width: 100%; height: auto; display: block; }
.mark { fill: var(--series); }
.mark:hover { opacity: 0.8; }
.grid { stroke: var(--grid); stroke-width: 1; }
.axis { stroke: var(--axis); stroke-width: 1; }
.tick, .rvalue { fill: var(--muted); font-size: 10px; }
.rlabel { fill: var(--ink-2); font-size: 11px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--ink-2); font-weight: 600;
  border-bottom: 1px solid var(--axis); padding: 5px 8px; }
td { border-bottom: 1px solid var(--grid); padding: 5px 8px; }
td.n { text-align: right; font-variant-numeric: tabular-nums; }
a { color: var(--series); }
.err { color: var(--critical); font-size: 13px; }
.empty { color: var(--muted); font-size: 13px; }
.muted { color: var(--muted); }
.tabs { display: flex; gap: 6px; margin: 0 0 14px; }
.tab { border: 1px solid var(--border); background: var(--surface); color: var(--ink-2);
  border-radius: 8px; padding: 6px 16px; font: inherit; font-size: 13px;
  font-weight: 600; cursor: pointer; }
.tab.active { background: var(--series); border-color: var(--series); color: #fff; }
#tip { position: absolute; display: none; pointer-events: none; z-index: 10;
  background: var(--ink); color: var(--page); font-size: 12px;
  padding: 4px 8px; border-radius: 6px; white-space: nowrap; }
footer { margin-top: 18px; color: var(--muted); font-size: 12px; }
"""

_JS = """
const tip = document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el => {
  el.addEventListener('mousemove', e => {
    tip.textContent = el.dataset.tip;
    tip.style.display = 'block';
    tip.style.left = (e.pageX + 12) + 'px';
    tip.style.top = (e.pageY - 28) + 'px';
  });
  el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
});
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.scope').forEach(s => {
      s.style.display = (s.id === btn.dataset.scope) ? '' : 'none';
    });
  });
});
function setTheme(mode) {
  if (mode) document.documentElement.setAttribute('data-theme', mode);
  else document.documentElement.removeAttribute('data-theme');
  try {
    if (mode) localStorage.setItem('projxdiff-theme', mode);
    else localStorage.removeItem('projxdiff-theme');
  } catch (e) {}
  document.querySelectorAll('.themeseg button').forEach(b =>
    b.classList.toggle('on', (b.dataset.set || '') === (mode || '')));
}
setTheme((() => { try { return localStorage.getItem('projxdiff-theme') || ''; } catch (e) { return ''; } })());
"""


def _scope_sections(d: dict, days: list) -> str:
    """Tiles + charts for one scope (all data, or one source's slice)."""
    tiles = f'''<div class="tiles">
      <div class="card tile"><div class="v">{d['last_night']}</div><div class="l">changes, latest active night</div></div>
      <div class="card tile"><div class="v">{d['changes_30d']}</div><div class="l">changes, last {RANK_WINDOW_DAYS} days</div></div>
      <div class="card tile"><div class="v">{d['active_7d']}</div><div class="l">projects active, last 7 days</div></div>
      <div class="card tile"><div class="v">{d['active_30d']}</div><div class="l">projects active, last {RANK_WINDOW_DAYS} days</div></div>
    </div>'''

    if any(d['daily'].values()):
        activity = _daily_chart(days, d['daily'])
    else:
        activity = ('<p class="empty">No activity recorded yet — this fills in after '
                    'the first nightly sync that finds changes.</p>')

    return f'''{tiles}
<section class="card">
  <h2>Changes per day &middot; last {TIME_WINDOW_DAYS} days</h2>
  {activity}
</section>
<div class="row">
  <div class="card"><h2>Top projects &middot; {RANK_WINDOW_DAYS}d</h2>
    {_rank_chart(d['projects'], 'Changes by project')}</div>
  <div class="card"><h2>By user &middot; {RANK_WINDOW_DAYS}d</h2>
    {_rank_chart(d['owners'], 'Changes by user')}</div>
  <div class="card"><h2>By category &middot; {RANK_WINDOW_DAYS}d</h2>
    {_rank_chart(d['categories'], 'Changes by category')}</div>
</div>'''


def generate_dashboard(db_path: Path, census_path: Path = None,
                       today: date = None, sources: list = None) -> str:
    """Render the dashboard. `sources` (a list of source names from a site
    config) turns on the source tabs; None keeps the legacy single-source
    layout byte-compatible."""
    today = today or date.today()
    conn = sqlite3.connect(db_path)
    try:
        has_source = _has_source_column(conn)
        multi = bool(sources) and has_source
        g = _collect_globals(conn, has_source)
        if multi:
            scoped = {'All': _collect(conn, today)}
            for s in sources:
                scoped[s] = _collect(conn, today, source=s)
        else:
            scoped = {'All': _collect(conn, today)}
    finally:
        conn.close()

    census = (census_mod.load_census(census_path)
              if census_path else {'users': {}, 'projects': {}, 'conflicts': []})

    days = [(today - timedelta(days=i)).isoformat()
            for i in range(TIME_WINDOW_DAYS - 1, -1, -1)]

    if g['last_run']:
        run_date, finished, seen, changed, errors = g['last_run']
        status = (f'Last sync {escape(run_date)} — {seen} project(s) scanned, '
                  f'{changed} changed.')
        err_html = (f'<p class="err">&#9888; Last run had errors: {escape(errors)}</p>'
                    if errors else '')
    else:
        status = 'No sync runs recorded yet.'
        err_html = ''

    if multi:
        tabs = '<div class="tabs">' + ''.join(
            f'<button class="tab{" active" if name == "All" else ""}" '
            f'data-scope="scope-{i}">{escape(name)}</button>'
            for i, name in enumerate(scoped)) + '</div>'
        hidden = ' style="display:none"'  # f-string expressions can't hold
        scopes_html = ''.join(            # backslashes before Python 3.12
            f'<div class="scope" id="scope-{i}"'
            f'{"" if name == "All" else hidden}>'
            f'{_scope_sections(d, days)}</div>'
            for i, (name, d) in enumerate(scoped.items()))
    else:
        tabs = ''
        scopes_html = _scope_sections(scoped['All'], days)

    rows_html = []
    for run_date, project, owner, a, r, m, src in g['recent']:
        owner_disp = (owner or '').split('<')[0].strip() or '—'
        href = (f'reports/{quote(src)}/{quote(run_date)}/{quote(project)}.html'
                if src else f'reports/{quote(run_date)}/{quote(project)}.html')
        src_cell = f'<td>{escape(src or "—")}</td>' if multi else ''
        rows_html.append(
            f'<tr><td>{escape(run_date)}</td>{src_cell}<td>{escape(project)}</td>'
            f'<td>{escape(owner_disp)}</td>'
            f'<td class="n">+{a}</td><td class="n">-{r}</td><td class="n">~{m}</td>'
            f'<td><a href="{href}">report</a></td></tr>')
    src_head = '<th>Source</th>' if multi else ''
    recent_html = (f'<table><thead><tr><th>Date</th>{src_head}<th>Project</th><th>User</th>'
                   '<th>Added</th><th>Removed</th><th>Modified</th><th></th></tr></thead>'
                   f'<tbody>{"".join(rows_html)}</tbody></table>' if rows_html
                   else '<p class="empty">No changes recorded yet.</p>')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Projx Work Dashboard</title>
<script>
try {{
    var t = localStorage.getItem('projxdiff-theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
}} catch (e) {{}}
</script>
<style>{_CSS}</style>
</head>
<body>
<div class="themeseg">
  <button type="button" data-set="" onclick="setTheme('')">Auto</button>
  <button type="button" data-set="light" onclick="setTheme('light')">Light</button>
  <button type="button" data-set="dark" onclick="setTheme('dark')">Dark</button>
</div>
<h1>Projx Work Dashboard</h1>
<p class="sub">{status} Generated {escape(datetime.now().strftime('%Y-%m-%d %H:%M'))}.</p>
{err_html}
{_attention_html(census)}
{tabs}
{scopes_html}
<section class="card">
  <h2>Recent changes</h2>
  {recent_html}
</section>
<footer>Counts are element-level changes (added / removed / modified) from the nightly
semantic diff. They measure activity, not effort — use the linked reports and the
archive repo for the real story.</footer>
<div id="tip"></div>
<script>{_JS}</script>
</body>
</html>
'''


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('config', type=Path, help='Path to the nightly sync config JSON')
    parser.add_argument('-o', '--output', type=Path, default=None,
                        help='Output HTML path (default: <data_dir>/dashboard.html)')
    args = parser.parse_args(argv)

    try:
        cfg = json.loads(args.config.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise SystemExit(f'config not found: {args.config}\n'
                         'Create one with: python -m dw_compare --init-config <folder>')
    data_dir = Path(cfg['data_dir'])
    cpath = Path(cfg['census_path']) if cfg.get('census_path') else data_dir / 'census.json'
    sources = list(cfg['sources']) if cfg.get('sources') else None
    out = args.output or data_dir / 'dashboard.html'
    out.write_text(generate_dashboard(data_dir / 'metrics.sqlite', census_path=cpath,
                                      sources=sources),
                   encoding='utf-8')
    print(f'dashboard written to {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
