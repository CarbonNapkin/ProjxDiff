"""
HTML report generation for DriveWorks comparison.

Layout: a sidebar shell — brand + diff totals + per-section navigation in a
left rail, content sections in the main pane. Theming is variable-driven
with a light theme (ink-navy rail, light content) and a dark theme, an
Auto/Light/Dark toggle persisted in localStorage, and Auto following the
viewer's OS via prefers-color-scheme.

Status colors are colorblind-safe by design: added = blue, removed =
orange, modified = violet-gray (the hue trio that stays distinct under
red-green color blindness), and status is never conveyed by color alone —
every badge carries a text label, and row badges add a +/−/~ glyph.
"""

import traceback
from datetime import datetime
from html import escape

from ._version import __version__, __url__
from .models import DWProject
from .comparers import (
    compare_variables,
    compare_constants,
    compare_calc_tables,
    compare_component_sets,
    compare_models,
    compare_component_tasks,
    compare_property_rules,
    compare_documents,
    compare_lookup_tables,
    compare_data_tables,
    compare_nav_steps,
    compare_spec_macros,
    compare_forms,
)


def _safe(fn, *args):
    """Run a section comparator, degrading to a placeholder instead of taking
    down the whole report if one section hits unexpected data."""
    try:
        return fn(*args)
    except Exception:
        traceback.print_exc()
        return ('<p class="empty">Could not render this section (see console for details).</p>',
                {'added': 0, 'removed': 0, 'modified': 0, 'unchanged': 0})


# --------------------------------------------------------------------------
# Theme variables. LIGHT is the ink-navy-rail light theme; DARK restates the
# same design for a dark surface (re-stepped colors, not inverted). The dark
# block is emitted twice: once under prefers-color-scheme (Auto mode) and
# once under [data-theme="dark"] (explicit choice), so the viewer's toggle
# beats the OS in both directions.
# --------------------------------------------------------------------------

_LIGHT_VARS = '''
    --page: #fbfcfd; --card: #ffffff; --border: #d8dde4; --rowline: #edf0f4;
    --ink: #14181d; --muted: #5f6a76; --sechead: #f8f9fb; --th-bg: #f4f6f9;
    --formula-bg: #f3f5f8; --hover: #eef2f7; --focus: #1c5cab;
    --rail-bg: #132a47; --rail-ink: #c9d6e8; --rail-muted: #7e93b0;
    --rail-on: #1e416b; --rail-on-ink: #ffffff;
    --rail-pill: #274d7d; --rail-pill-ink: #dbe6f4; --rail-chip: #1b3a61;
    --added: #1c5cab; --added-soft: #e3edfa; --added-deep: #14447e;
    --row-added: #f2f7fd; --row-added-strong: #e3edfa;
    --removed: #c34d0e; --removed-soft: #fdeadd; --removed-deep: #8d3a0c;
    --row-removed: #fdf5ee; --row-removed-strong: #fdeadd;
    --modified: #5b5471; --modified-soft: #ececf3; --modified-deep: #454060;
    --row-modified: #f6f6f9; --row-modified-strong: #ececf3;
    --ins-bg: #cfe1f7; --ins-ink: #14447e; --del-bg: #f9dcc6; --del-ink: #8d3a0c;
    --badge-unchanged-bg: #e6e9ee; --badge-unchanged-ink: #3b4350;
    --grouper-bg: rgba(0,0,0,0.02); --group-border: #c3cad3;
    --chip-added: #6da7ec; --chip-removed: #f09a63;
    --chip-modified: #b9b3d6; --chip-unchanged: #55677f;
'''

_DARK_VARS = '''
    --page: #17181a; --card: #1f2124; --border: #33363b; --rowline: #2a2d31;
    --ink: #e7e9ec; --muted: #8d95a0; --sechead: #24272b; --th-bg: #26292d;
    --formula-bg: #26292e; --hover: #282c31; --focus: #6da7ec;
    --rail-bg: #101114; --rail-ink: #b9bec7; --rail-muted: #6e7683;
    --rail-on: #23272e; --rail-on-ink: #ffffff;
    --rail-pill: #2c3138; --rail-pill-ink: #c9cfd8; --rail-chip: #1a1c20;
    --added: #6da7ec; --added-soft: #17273d; --added-deep: #a9c9f2;
    --row-added: #1a2330; --row-added-strong: #20304a;
    --removed: #f09a63; --removed-soft: #3a2313; --removed-deep: #f6bf97;
    --row-removed: #2b2118; --row-removed-strong: #3a2c1d;
    --modified: #b9b3d6; --modified-soft: #2a2836; --modified-deep: #d3cfe6;
    --row-modified: #242231; --row-modified-strong: #2e2b40;
    --ins-bg: #1d3a5f; --ins-ink: #b5d3f5; --del-bg: #4a2c14; --del-ink: #f6c9a4;
    --badge-unchanged-bg: #2c3036; --badge-unchanged-ink: #aeb6c0;
    --grouper-bg: rgba(255,255,255,0.02); --group-border: #454a52;
    --chip-added: #6da7ec; --chip-removed: #f09a63;
    --chip-modified: #b9b3d6; --chip-unchanged: #55677f;
'''


def _slug(title: str) -> str:
    return 'sec-' + ''.join(c if c.isalnum() else '-' for c in title.lower())


def generate_html_report(old_proj: DWProject, new_proj: DWProject,
                         old_name: str, new_name: str,
                         old_resolved: dict = None, new_resolved: dict = None,
                         old_prop_resolved: dict = None, new_prop_resolved: dict = None,
                         old_prop_types: dict = None, new_prop_types: dict = None) -> str:
    """Generate comprehensive HTML comparison report.

    old_resolved/new_resolved: {norm(id): name} for CCRef/TrId -> readable
    model name, from components.resolve_names() against a group database.
    old_prop_resolved/new_prop_resolved: {norm(id): name} for CPRef/CERef ->
    readable D1@Sketch1-style property name, from decoding
    CapturedComponents.Data. old_prop_types/new_prop_types: {norm(id):
    type_guid} from the SAME Data blobs' T attribute — the authoritative
    Dimension/Feature/Instance/Configuration classification for the Rule
    Changes Type column (see components.TYPE_GUID_KIND). All six are
    optional — pass None/{} (the default) when no database connection is
    available; the Models and Rule Changes sections still render, just
    with raw GUIDs instead of names and a best-effort Type guess instead
    of the authoritative one, matching the tool's existing
    warn-and-continue behaviour.
    """

    summary = {'added': 0, 'removed': 0, 'modified': 0, 'unchanged': 0}
    section_defs = [
        ('Variables', compare_variables, old_proj.variables, new_proj.variables),
        ('Constants', compare_constants, old_proj.constants, new_proj.constants),
        ('Calculation Tables', compare_calc_tables, old_proj.calc_tables, new_proj.calc_tables),
        ('Component Sets', compare_component_sets,
         {cs.name: cs for cs in old_proj.component_index.sets.values()},
         {cs.name: cs for cs in new_proj.component_index.sets.values()}),
        ('Models', compare_models, old_resolved or {}, new_resolved or {}),
        ('Component Tasks', compare_component_tasks, old_proj.component_tasks, new_proj.component_tasks),
        ('Documents', compare_documents, old_proj.documents, new_proj.documents),
        ('Lookup Tables', compare_lookup_tables, old_proj.lookup_tables, new_proj.lookup_tables),
        ('Data Tables', compare_data_tables, old_proj.data_tables, new_proj.data_tables),
        ('Specification Macros', compare_spec_macros, old_proj.spec_macros, new_proj.spec_macros),
        ('Navigation Steps', compare_nav_steps, old_proj.nav_steps, new_proj.nav_steps),
        ('Forms', compare_forms, old_proj.forms, new_proj.forms),
    ]
    sections = []
    for _title, _fn, _old, _new in section_defs:
        _html, _stats = _safe(_fn, _old, _new)
        sections.append((_title, _html, _stats))

    # Rule Changes takes eight args (two indexes, four resolved-name dicts,
    # two type-guid dicts), not the generic (old_data, new_data) shape the
    # loop above uses.
    _html, _stats = _safe(compare_property_rules, old_proj.component_index, new_proj.component_index,
                           old_resolved or {}, new_resolved or {}, old_prop_resolved or {}, new_prop_resolved or {},
                           old_prop_types or {}, new_prop_types or {})
    sections.insert(5, ('Rule Changes', _html, _stats))  # right after Models

    # Aggregate summary
    for _, _, stats in sections:
        summary['added'] += stats['added']
        summary['removed'] += stats['removed']
        summary['modified'] += stats['modified']
        summary['unchanged'] += stats['unchanged']

    old_esc, new_esc = escape(old_name), escape(new_name)

    # Rail navigation: one item per section with its change count; sections
    # with no changes render dimmed but stay clickable (the click reveals
    # them even while "Show unchanged sections" is off).
    nav_items = ''
    for title, _content, stats in sections:
        changes = stats['added'] + stats['removed'] + stats['modified']
        dim = '' if changes else ' dim'
        nav_items += (f'<a class="navitem{dim}" href="#{_slug(title)}" '
                      f'data-sec="{_slug(title)}">{escape(title)}'
                      f'<span class="n">{changes:,}</span></a>\n')

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Projx Diff — Project Comparison</title>
    <script>
        /* Apply the saved theme before first paint to avoid a flash. */
        try {{
            var t = localStorage.getItem('projxdiff-theme');
            if (t) document.documentElement.setAttribute('data-theme', t);
        }} catch (e) {{}}
    </script>
    <style>
        :root {{ --header-stack: 44px; {_LIGHT_VARS} }}
        @media (prefers-color-scheme: dark) {{
            :root:not([data-theme="light"]) {{ {_DARK_VARS} }}
        }}
        :root[data-theme="dark"] {{ {_DARK_VARS} }}

        * {{ box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.4;
            margin: 0;
            background: var(--page);
            color: var(--ink);
        }}

        .shell {{ display: flex; align-items: flex-start; min-height: 100vh; }}

        /* ---- Left rail: brand, totals, section nav, theme toggle ---- */
        nav.rail {{
            position: sticky;
            top: 0;
            width: 232px;
            height: 100vh;
            overflow-y: auto;
            flex-shrink: 0;
            background: var(--rail-bg);
            color: var(--rail-ink);
            padding: 18px 14px;
            display: flex;
            flex-direction: column;
        }}
        .brand {{ font-weight: 800; color: #ffffff; font-size: 16px; }}
        .brandsub {{ font-size: 11.5px; color: var(--rail-muted); margin: 2px 0 10px;
                     overflow-wrap: anywhere; }}
        .railcap {{ font-size: 10.5px; letter-spacing: .07em; color: var(--rail-muted);
                    margin: 12px 0 6px; text-transform: uppercase; }}
        .chip {{ display: flex; align-items: center; padding: 6px 10px; border-radius: 8px;
                 font-size: 12.5px; margin-bottom: 5px; background: var(--rail-chip);
                 color: var(--rail-pill-ink); }}
        .chip b {{ margin-left: auto; font-variant-numeric: tabular-nums; }}
        .ch-added {{ border-left: 3px solid var(--chip-added); }}
        .ch-removed {{ border-left: 3px solid var(--chip-removed); }}
        .ch-modified {{ border-left: 3px solid var(--chip-modified); }}
        .ch-unchanged {{ border-left: 3px solid var(--chip-unchanged); }}
        .navitem {{ display: flex; align-items: center; padding: 7px 10px; border-radius: 8px;
                    font-size: 13px; color: var(--rail-ink); text-decoration: none; }}
        .navitem:hover {{ background: var(--rail-on); color: var(--rail-on-ink); }}
        .navitem.dim {{ opacity: .5; }}
        .navitem .n {{ margin-left: auto; font-size: 11px; background: var(--rail-pill);
                       border-radius: 99px; padding: 1px 8px; color: var(--rail-pill-ink);
                       font-variant-numeric: tabular-nums; }}
        .railfoot {{ margin-top: auto; padding-top: 14px; }}
        .themeseg {{ display: flex; gap: 4px; }}
        .themeseg button {{ flex: 1; border: 1px solid var(--rail-pill); background: transparent;
                            color: var(--rail-ink); border-radius: 7px; padding: 5px 0;
                            font-size: 11.5px; font-weight: 600; cursor: pointer; }}
        .themeseg button.on {{ background: var(--rail-on); color: var(--rail-on-ink);
                               border-color: var(--rail-on); }}

        /* ---- Main pane ---- */
        main {{ flex: 1; min-width: 0; padding: 16px 22px 32px; max-width: 1400px; }}
        .pagehead {{ font-size: 14px; margin: 2px 0 12px; }}
        .pagehead .arr {{ color: var(--muted); margin: 0 4px; }}
        .pagehead .sub {{ color: var(--muted); font-size: 12px; margin-left: 10px; }}
        .pagehead a {{ color: var(--added); text-decoration: none; }}

        /* Filter bar sticks at top of viewport while scrolling. */
        .filter-bar {{
            position: sticky;
            top: 0;
            z-index: 10;
            margin: 0 0 14px;
            padding: 8px 10px;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 10px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .filter-bar label {{ cursor: pointer; font-size: 13px; }}
        .filter-bar button {{
            padding: 4px 10px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--ink);
            border-radius: 7px;
        }}
        .filter-bar button:hover {{ background: var(--hover); }}

        #searchBox {{
            flex: 1 0 200px;
            min-width: 200px;
            padding: 5px 10px;
            border: 1px solid var(--border);
            border-radius: 7px;
            font-size: 13px;
            background: var(--page);
            color: var(--ink);
        }}
        #searchBox:focus {{
            outline: 2px solid var(--focus);
            border-color: var(--focus);
        }}

        .section {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            margin: 10px 0;
            overflow: hidden;
        }}

        /* Sections with no changes hide entirely by default; toggle restores them. */
        body.hide-quiet .section[data-quiet="1"] {{ display: none; }}

        .section-header {{
            background: var(--sechead);
            color: var(--ink);
            border-bottom: 1px solid var(--border);
            padding: 9px 14px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
        }}
        .section-header:hover {{ background: var(--hover); }}
        .section-header .title {{ font-weight: 700; }}
        .section.collapsed .section-header {{ border-bottom: none; }}

        .section-content {{
            padding: 0;
            max-height: min(78vh, 780px);
            overflow-y: auto;
        }}
        .section.collapsed .section-content {{ display: none; }}

        /* Sticky per-group sub-headers (used by Forms, Macros, Calc Tables, etc.). */
        .section-content > h3 {{
            position: sticky;
            top: 0;
            z-index: 3;
            margin: 0;
            padding: 8px 14px;
            font-size: 14px;
            background: var(--sechead);
            color: var(--ink);
            border-top: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
        }}
        .section-content > h3:first-child {{ border-top: none; }}
        .section-content > h3.added {{ background: var(--added-soft); }}
        .section-content > h3.removed {{ background: var(--removed-soft); }}
        .section-content > h3.modified {{ background: var(--modified-soft); }}
        .section-content > h3 small {{ color: var(--muted); font-weight: 500; margin-left: 6px; }}

        .section-content > p.empty {{ padding: 12px 14px; margin: 0; }}

        /* Badges: labeled pills. Text is the accessible signal; row badges
           (inside table cells) additionally carry a +/−/~ glyph. Section
           header count badges already include their sign in the text. */
        .badge {{
            display: inline-block;
            padding: 1px 8px;
            border-radius: 99px;
            font-size: 11px;
            font-weight: 700;
            margin-left: 6px;
            vertical-align: middle;
            white-space: nowrap;
        }}
        .badge-added {{ background: var(--added-soft); color: var(--added-deep);
                        border: 1px solid var(--added); }}
        .badge-removed {{ background: var(--removed-soft); color: var(--removed-deep);
                          border: 1px solid var(--removed); }}
        .badge-modified {{ background: var(--modified-soft); color: var(--modified-deep);
                           border: 1px solid var(--modified); }}
        .badge-unchanged {{ background: var(--badge-unchanged-bg); color: var(--badge-unchanged-ink); }}
        td .badge-added::before {{ content: "+ "; }}
        td .badge-removed::before {{ content: "\\2212\\00a0"; }}
        td .badge-modified::before {{ content: "~ "; }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12.5px;
        }}

        th, td {{
            padding: 5px 10px;
            text-align: left;
            border-bottom: 1px solid var(--rowline);
            vertical-align: top;
        }}
        td:first-child {{ word-break: break-word; }}

        /* Table column header sticks just under any sticky h3. */
        th {{
            background: var(--th-bg);
            font-weight: 600;
            position: sticky;
            top: var(--header-stack);
            z-index: 2;
            box-shadow: inset 0 -1px 0 var(--border);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            color: var(--muted);
        }}
        /* When no h3 sub-header is present in a section, th sits at top: 0. */
        .section-content > table:first-child th,
        .section-content > table:only-child th {{ top: 0; }}

        /* Draggable column-resize handle — a thin strip on each th's right
           edge, sitting inside the sticky header (sticky is a positioning
           context too, so this anchors correctly without extra markup). */
        th .col-resizer {{
            position: absolute;
            top: 0;
            right: 0;
            width: 6px;
            height: 100%;
            cursor: col-resize;
            user-select: none;
            z-index: 3;
        }}
        th .col-resizer:hover, th .col-resizer.resizing {{
            background: var(--focus);
            opacity: 0.5;
        }}
        body.col-resizing, body.col-resizing * {{ cursor: col-resize !important; user-select: none !important; }}

        tbody tr:hover {{ background: var(--hover); }}
        tr.added {{ background: var(--row-added); }}
        tr.added:hover {{ background: var(--row-added-strong); }}
        tr.removed {{ background: var(--row-removed); }}
        tr.removed:hover {{ background: var(--row-removed-strong); }}
        tr.modified {{ background: var(--row-modified); }}
        tr.modified:hover {{ background: var(--row-modified-strong); }}

        /* Group-start marks the first row of a parent-child group (Form ->
           Control -> Property, Macro -> Task -> Property, CalcTable Column
           -> Scope). Repeated cells are blank on later rows, so this border
           draws the visible parent boundary. */
        tbody tr.group-start td {{ border-top: 2px solid var(--group-border); }}
        tbody tr.group-start:first-child td {{ border-top: none; }}
        /* The first one or two cells of a row are identity cells; on later
           rows in a group they are blank. Give them slightly muted styling so
           the eye reads the grouped chunk as one block. */
        td.grouper {{ font-weight: 500; background: var(--grouper-bg); }}

        /* Lookup-table grids render the actual CSV data with per-cell
           highlighting. Unchanged rows are hidden by default; the
           "Show unchanged lookup rows" filter brings them back. */
        table.lookup-grid {{ table-layout: auto; }}
        table.lookup-grid th {{
            top: var(--header-stack);
            white-space: nowrap;
        }}
        table.lookup-grid th.col-added {{ background: var(--added-soft); }}
        table.lookup-grid th.col-removed {{ background: var(--removed-soft); }}
        table.lookup-grid td {{
            font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
            font-size: 12px;
            max-width: 320px;
            overflow-wrap: anywhere;
        }}
        table.lookup-grid td.cell-changed {{
            background: var(--row-modified-strong);
            font-weight: 500;
        }}
        table.lookup-grid td.cell-added {{ background: var(--row-added-strong); }}
        table.lookup-grid td.cell-removed {{ background: var(--row-removed-strong); }}
        /* Colorblind-safe glyphs on changed lookup cells — everywhere else a
           change carries a labeled badge; a background wash alone must never
           be the only signal. Generated content stays out of copied text. */
        table.lookup-grid td.cell-added, table.lookup-grid td.cell-removed,
        table.lookup-grid td.cell-changed {{ position: relative; padding-right: 15px; }}
        table.lookup-grid td.cell-added::after, table.lookup-grid td.cell-removed::after,
        table.lookup-grid td.cell-changed::after {{
            position: absolute; top: 1px; right: 3px; font-size: 10px;
            font-weight: 700; opacity: 0.85;
        }}
        table.lookup-grid td.cell-added::after {{ content: "+"; color: var(--added); }}
        table.lookup-grid td.cell-removed::after {{ content: "\\2212"; color: var(--removed); }}
        table.lookup-grid td.cell-changed::after {{ content: "~"; color: var(--modified); }}
        body:not(.show-lookup-unchanged) table.lookup-grid tbody tr.unchanged {{ display: none; }}
        /* Copy buttons on formula cells: invisible until the row is hovered
           or the button is keyboard-focused, so tables stay scannable. */
        td.formula {{ position: relative; }}
        .copybtns {{ position: absolute; top: 2px; right: 2px; display: flex; gap: 3px;
            opacity: 0; transition: opacity 0.12s; }}
        tr:hover .copybtns, .copybtns:focus-within {{ opacity: 1; }}
        .copybtns button {{ border: 1px solid var(--border); background: var(--sechead);
            color: var(--muted); border-radius: 5px; font: inherit; font-size: 10px;
            padding: 1px 6px; cursor: pointer; }}
        .copybtns button:hover {{ color: var(--ink); }}
        /* Prev/next change navigation + search feedback in the filter bar. */
        .changenav {{ display: inline-flex; align-items: center; gap: 6px; }}
        #navPos, #matchCount {{ color: var(--muted); font-size: 12px; }}
        #matchCount.nomatch {{ color: var(--removed); font-weight: 600; }}
        td.linkcell {{ position: relative; }}
        .section-header:focus-visible {{ outline: 2px solid var(--added); outline-offset: -2px; }}
        .copybtns button:focus-visible, .filter-bar button:focus-visible,
        .filter-bar input:focus-visible {{ outline: 2px solid var(--added); }}
        tr.flash > td {{ animation: rowflash 1.6s ease-out; }}
        @keyframes rowflash {{
            0%, 35% {{ background: var(--added-soft); box-shadow: inset 0 0 0 1px var(--added); }}
            100% {{ }}
        }}

        .formula {{
            font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
            font-size: 12px;
            white-space: pre-wrap;
            word-break: break-word;
            background: var(--formula-bg);
            padding: 3px 7px;
            border-radius: 4px;
            max-width: min(60vw, 720px);
            line-height: 1.35;
        }}

        /* Rule Changes (driven-property diffs): the formula is the whole
           point, so it gets most of the width; Placement/Type/Property/
           Status are identity columns and stay compact. table-layout:
           fixed makes the percentages below actually apply. */
        table.rule-changes {{ table-layout: fixed; }}
        table.rule-changes th:nth-child(1), table.rule-changes td:nth-child(1) {{
            width: 14%; overflow-wrap: anywhere;
        }}
        table.rule-changes th:nth-child(2), table.rule-changes td:nth-child(2) {{ width: 8%; }}
        table.rule-changes th:nth-child(3), table.rule-changes td:nth-child(3) {{
            width: 10%; overflow-wrap: anywhere;
        }}
        table.rule-changes th:nth-child(4), table.rule-changes td:nth-child(4) {{ width: 8%; }}
        table.rule-changes th:nth-child(5), table.rule-changes td:nth-child(5) {{ width: 30%; }}
        table.rule-changes th:nth-child(6), table.rule-changes td:nth-child(6) {{ width: 30%; }}
        table.rule-changes .formula {{ max-width: none; }}

        /* Inline formula diffs: added = blue, removed = orange, matching the
           colorblind-safe status hues (never green/red). */
        span.added {{
            background: var(--ins-bg);
            color: var(--ins-ink);
            padding: 0 3px;
            border-radius: 3px;
        }}
        span.removed {{
            background: var(--del-bg);
            color: var(--del-ink);
            padding: 0 3px;
            border-radius: 3px;
            text-decoration: line-through;
        }}

        .empty {{ color: var(--muted); font-style: italic; }}
        .attr-note {{ color: var(--muted); font-size: 11px; margin-top: 3px; }}
        .toggle {{ font-size: 18px; user-select: none; color: var(--muted); }}

        footer {{ margin-top: 24px; padding-top: 12px; border-top: 1px solid var(--border);
                  color: var(--muted); font-size: 12px; text-align: center; }}
        footer a {{ color: var(--muted); }}

        @media print {{
            nav.rail {{ display: none; }}
            .filter-bar {{ display: none; }}
            .section-content {{ max-height: none; overflow: visible; }}
        }}
    </style>
</head>
<body>
<div class="shell">
    <nav class="rail">
        <div class="brand">Projx Diff</div>
        <div class="brandsub">{old_esc} &rarr; {new_esc}</div>

        <div class="railcap">This diff</div>
        <div class="chip ch-added"><span>+ Added</span><b>{summary['added']:,}</b></div>
        <div class="chip ch-removed"><span>&minus; Removed</span><b>{summary['removed']:,}</b></div>
        <div class="chip ch-modified"><span>~ Modified</span><b>{summary['modified']:,}</b></div>
        <div class="chip ch-unchanged"><span>&check; Unchanged</span><b>{summary['unchanged']:,}</b></div>

        <div class="railcap">Sections</div>
        {nav_items}
        <div class="railfoot">
            <div class="railcap">Theme</div>
            <div class="themeseg">
                <button type="button" data-set="" onclick="setTheme('')">Auto</button>
                <button type="button" data-set="light" onclick="setTheme('light')">Light</button>
                <button type="button" data-set="dark" onclick="setTheme('dark')">Dark</button>
            </div>
        </div>
    </nav>

    <main>
    <div class="pagehead">
        <strong>{old_esc}</strong><span class="arr">&rarr;</span><strong>{new_esc}</strong>
        <span class="sub">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by
        <a href="{__url__}">Projx Diff v{__version__}</a></span>
    </div>

    <div class="filter-bar">
        <input type="text" id="searchBox" placeholder="Search names, formulas..." oninput="filterRows()">
        <label><input type="checkbox" id="showAdded" checked onchange="filterRows()"> Added</label>
        <label><input type="checkbox" id="showRemoved" checked onchange="filterRows()"> Removed</label>
        <label><input type="checkbox" id="showModified" checked onchange="filterRows()"> Modified</label>
        <label><input type="checkbox" id="showUnchanged" onchange="filterRows()"> Unchanged rows</label>
        <label><input type="checkbox" id="showQuietSections" onchange="applySectionVisibility()"> Show unchanged sections</label>
        <label><input type="checkbox" id="showLookupUnchanged" onchange="applyLookupRowVisibility()"> Show unchanged lookup rows</label>
        <button onclick="expandAll(true)">Expand all</button>
        <button onclick="expandAll(false)">Collapse all</button>
        <span class="changenav">
            <button onclick="jumpChange(-1)" title="Previous change (p)">&#8593; Prev</button>
            <button onclick="jumpChange(1)" title="Next change (n)">Next &#8595;</button>
            <span id="navPos"></span>
        </span>
        <span id="matchCount" role="status"></span>
    </div>
'''

    for section_name, section_content, stats in sections:
        badges = ''
        if stats['added']: badges += f'<span class="badge badge-added">+{stats["added"]}</span>'
        if stats['removed']: badges += f'<span class="badge badge-removed">-{stats["removed"]}</span>'
        if stats['modified']: badges += f'<span class="badge badge-modified">~{stats["modified"]}</span>'

        quiet = stats['added'] + stats['removed'] + stats['modified'] == 0
        collapsed = 'collapsed' if quiet else ''
        unchanged_count = stats.get('unchanged', 0)
        if quiet and unchanged_count:
            badges += f'<span class="badge badge-unchanged">{unchanged_count} unchanged</span>'

        html += f'''
    <div class="section {collapsed}" id="{_slug(section_name)}" data-quiet="{1 if quiet else 0}">
        <div class="section-header" role="button" tabindex="0"
             aria-expanded="{'false' if collapsed else 'true'}"
             onclick="toggleSection(this)">
            <span class="title">{section_name}{badges}</span>
            <span class="toggle">▼</span>
        </div>
        <div class="section-content">
            {section_content}
        </div>
    </div>
'''

    html += '''
    <script>
        function setTheme(mode) {
            if (mode) document.documentElement.setAttribute('data-theme', mode);
            else document.documentElement.removeAttribute('data-theme');
            try {
                if (mode) localStorage.setItem('projxdiff-theme', mode);
                else localStorage.removeItem('projxdiff-theme');
            } catch (e) {}
            document.querySelectorAll('.themeseg button').forEach(b => {
                const on = (b.dataset.set || '') === (mode || '');
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }

        function setCollapsed(sec, collapsed) {
            sec.classList.toggle('collapsed', collapsed);
            const h = sec.querySelector('.section-header');
            if (h) h.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }

        function toggleSection(header) {
            const sec = header.parentElement;
            setCollapsed(sec, !sec.classList.contains('collapsed'));
        }

        // ---- Filter state and deep links live in the URL hash ----
        // Reports are immutable archive files, so a URL that carries the
        // current search/filters (and optionally one change's id) can be
        // pasted into an email and lands the reader on the same view.
        let hashTarget = '';

        function syncHash() {
            const parts = [];
            if (hashTarget) parts.push(hashTarget);
            const q = document.getElementById('searchBox').value.trim();
            if (q) parts.push('q=' + encodeURIComponent(q));
            const st = ['showAdded', 'showRemoved', 'showModified', 'showUnchanged']
                .map((id, i) => document.getElementById(id).checked ? 'armu'[i] : '')
                .join('');
            if (st !== 'arm') parts.push('st=' + st);
            if (document.getElementById('showQuietSections').checked) parts.push('qs=1');
            if (document.getElementById('showLookupUnchanged').checked) parts.push('lu=1');
            history.replaceState(null, '',
                parts.length ? '#' + parts.join('&') : location.pathname + location.search);
        }

        function applyHashState() {
            const raw = location.hash.slice(1);
            if (!raw) return;
            for (const tok of raw.split('&')) {
                if (!tok) continue;
                const eq = tok.indexOf('=');
                if (eq < 0) { hashTarget = tok; continue; }
                const k = tok.slice(0, eq), v = decodeURIComponent(tok.slice(eq + 1));
                if (k === 'q') document.getElementById('searchBox').value = v;
                else if (k === 'st') {
                    ['showAdded', 'showRemoved', 'showModified', 'showUnchanged'].forEach(
                        (id, i) => { document.getElementById(id).checked = v.includes('armu'[i]); });
                }
                else if (k === 'qs') document.getElementById('showQuietSections').checked = v === '1';
                else if (k === 'lu') document.getElementById('showLookupUnchanged').checked = v === '1';
            }
        }

        function revealTarget(id) {
            const el = document.getElementById(id);
            if (!el) return;
            const sec = el.closest('.section') || el;
            if (sec.dataset && sec.dataset.quiet === '1') {
                const qb = document.getElementById('showQuietSections');
                if (!qb.checked) { qb.checked = true; applySectionVisibility(); }
            }
            if (sec.classList) setCollapsed(sec, false);
            el.scrollIntoView({ behavior: 'smooth', block: el.tagName === 'TR' ? 'center' : 'start' });
            if (el.tagName === 'TR') {
                el.classList.remove('flash');
                void el.offsetWidth;
                el.classList.add('flash');
            }
        }

        function filterRows() {
            const showAdded = document.getElementById('showAdded').checked;
            const showRemoved = document.getElementById('showRemoved').checked;
            const showModified = document.getElementById('showModified').checked;
            const showUnchanged = document.getElementById('showUnchanged').checked;
            const searchText = document.getElementById('searchBox').value.toLowerCase().trim();

            // A section with nothing added/removed/modified (e.g. Component
            // Sets when nothing changed) is "quiet" and hidden entirely by a
            // separate toggle ("Show unchanged sections"), so ticking
            // "Unchanged rows" alone wouldn't reveal it. Keep them in sync
            // and open the section, so checking "Unchanged rows" reveals it
            // as expected instead of silently doing nothing.
            const quietBox = document.getElementById('showQuietSections');
            if (showUnchanged && !quietBox.checked) {
                quietBox.checked = true;
            }
            document.body.classList.toggle('hide-quiet', !quietBox.checked);
            if (showUnchanged) {
                document.querySelectorAll('.section[data-quiet="1"]').forEach(
                    sec => setCollapsed(sec, false)
                );
            }

            document.querySelectorAll('tbody tr').forEach(row => {
                // Skip empty placeholder rows
                if (row.querySelector('.empty')) return;

                // Check status filter
                let statusMatch = false;
                if (row.classList.contains('added') && showAdded) statusMatch = true;
                else if (row.classList.contains('removed') && showRemoved) statusMatch = true;
                else if (row.classList.contains('modified') && showModified) statusMatch = true;
                else if (row.classList.contains('unchanged') && showUnchanged) statusMatch = true;

                // Check search filter
                let searchMatch = true;
                if (searchText) {
                    const rowText = row.textContent.toLowerCase();
                    searchMatch = rowText.includes(searchText);
                }

                row.style.display = (statusMatch && searchMatch) ? '' : 'none';
            });

            // Keep a group's identity row visible whenever any row in that
            // group is still visible, so filtered child rows are not orphaned.
            document.querySelectorAll('.section-content tbody').forEach(tb => {
                const rows = Array.from(tb.rows);
                let i = 0;
                while (i < rows.length) {
                    if (!rows[i].classList.contains('group-start')) { i++; continue; }
                    let j = i + 1;
                    let anyVisible = rows[i].style.display !== 'none';
                    while (j < rows.length && !rows[j].classList.contains('group-start')) {
                        if (rows[j].style.display !== 'none') anyVisible = true;
                        j++;
                    }
                    if (anyVisible) rows[i].style.display = '';
                    i = j;
                }
            });

            // Group headers (h3). A header that precedes a detail table follows
            // its rows: show it iff the table still has a visible row after
            // filtering. This stops a search/status match *inside* a grouped
            // section (Forms, Macros, Documents, Calc/Lookup tables) from being
            // hidden by a header that doesn't itself contain the search term.
            // A standalone header (an added/removed item with no detail table)
            // is filtered by its own status and header text.
            document.querySelectorAll('.section-content h3').forEach(h3 => {
                const table = h3.nextElementSibling;
                if (table && table.tagName === 'TABLE') {
                    const anyVisibleRow = Array.from(table.querySelectorAll('tbody tr')).some(
                        r => !r.querySelector('.empty') && r.style.display !== 'none'
                    );
                    h3.style.display = anyVisibleRow ? '' : 'none';
                    table.style.display = anyVisibleRow ? '' : 'none';
                    return;
                }

                let statusMatch = true;
                if (h3.classList.contains('added')) statusMatch = showAdded;
                else if (h3.classList.contains('removed')) statusMatch = showRemoved;
                else if (h3.classList.contains('modified')) statusMatch = showModified;

                const searchMatch = !searchText || h3.textContent.toLowerCase().includes(searchText);
                h3.style.display = (statusMatch && searchMatch) ? '' : 'none';
            });

            updateChangeNav(searchText);
            syncHash();
        }

        // ---- Prev/next change navigation + search feedback ----
        let changeRows = [];
        let changeIdx = -1;

        function updateChangeNav(searchText) {
            changeRows = Array.from(document.querySelectorAll(
                'tbody tr.added, tbody tr.removed, tbody tr.modified')).filter(r =>
                    r.style.display !== 'none' &&
                    (!r.closest('table') || r.closest('table').style.display !== 'none'));
            changeIdx = -1;
            document.getElementById('navPos').textContent =
                changeRows.length ? changeRows.length + ' changes' : '';
            const mc = document.getElementById('matchCount');
            if (searchText) {
                const visible = Array.from(document.querySelectorAll('tbody tr')).filter(r =>
                    !r.querySelector('.empty') && r.style.display !== 'none').length;
                mc.textContent = visible === 0 ? 'No matches'
                    : visible + (visible === 1 ? ' match' : ' matches');
                mc.classList.toggle('nomatch', visible === 0);
            } else {
                mc.textContent = '';
                mc.classList.remove('nomatch');
            }
        }

        function jumpChange(dir) {
            if (!changeRows.length) return;
            changeIdx = (changeIdx + dir + changeRows.length) % changeRows.length;
            const row = changeRows[changeIdx];
            // Reveal the section the target lives in (a changed row is never
            // in a quiet section, but its section may be collapsed).
            const sec = row.closest('.section');
            if (sec) setCollapsed(sec, false);
            row.scrollIntoView({ behavior: 'smooth', block: 'center' });
            row.classList.remove('flash');
            void row.offsetWidth;  // restart the animation
            row.classList.add('flash');
            document.getElementById('navPos').textContent =
                (changeIdx + 1) + ' / ' + changeRows.length;
        }

        document.addEventListener('keydown', e => {
            const t = e.target;
            if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) {
                if (e.key === 'Escape') t.blur();
                return;
            }
            if (e.key === 'n') jumpChange(1);
            else if (e.key === 'p') jumpChange(-1);
            else if (e.key === '/') {
                e.preventDefault();
                document.getElementById('searchBox').focus();
            }
        });

        // ---- Copy buttons on formula cells ----
        // A modified cell interleaves old (span.removed) and new (span.added)
        // tokens, so select-copy grabs both mixed together; these extract one
        // clean side. Notes and the buttons themselves never leak into the
        // copied text.
        function formulaText(td, side) {
            const clone = td.cloneNode(true);
            clone.querySelectorAll('.attr-note, .copybtns').forEach(n => n.remove());
            if (side === 'old') clone.querySelectorAll('span.added').forEach(n => n.remove());
            if (side === 'new') clone.querySelectorAll('span.removed').forEach(n => n.remove());
            return clone.textContent.trim();
        }

        function copyText(text, btn) {
            const done = () => {
                const label = btn.textContent;
                btn.textContent = '✓';
                setTimeout(() => { btn.textContent = label; }, 900);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
            } else {
                fallbackCopy(text, done);
            }
        }

        function fallbackCopy(text, done) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); done(); } catch (e) {}
            ta.remove();
        }

        function initCopyButtons() {
            document.querySelectorAll('td.formula').forEach(td => {
                const hasDiff = td.querySelector('span.added') && td.querySelector('span.removed');
                const plain = formulaText(td, 'all');
                if (!plain || plain === '(blank)') return;
                const wrap = document.createElement('span');
                wrap.className = 'copybtns';
                const add = (label, side, title) => {
                    const b = document.createElement('button');
                    b.type = 'button';
                    b.textContent = label;
                    b.title = title;
                    b.addEventListener('click', e => {
                        e.stopPropagation();
                        copyText(formulaText(td, side), b);
                    });
                    wrap.appendChild(b);
                };
                if (hasDiff) {
                    add('⧉ old', 'old', 'Copy the old formula');
                    add('⧉ new', 'new', 'Copy the new formula');
                } else {
                    add('⧉', 'all', 'Copy formula');
                }
                td.appendChild(wrap);
            });
        }

        function applySectionVisibility() {
            const show = document.getElementById('showQuietSections').checked;
            document.body.classList.toggle('hide-quiet', !show);
            syncHash();
        }

        function applyLookupRowVisibility() {
            const show = document.getElementById('showLookupUnchanged').checked;
            document.body.classList.toggle('show-lookup-unchanged', show);
            syncHash();
        }

        function expandAll(open) {
            document.querySelectorAll('.section').forEach(s => setCollapsed(s, !open));
        }

        function initNav() {
            document.querySelectorAll('.navitem').forEach(a => {
                a.addEventListener('click', e => {
                    e.preventDefault();
                    hashTarget = a.dataset.sec;
                    syncHash();
                    revealTarget(a.dataset.sec);
                });
            });
        }

        function initSectionKeyboard() {
            document.querySelectorAll('.section-header').forEach(h => {
                h.addEventListener('keydown', e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        toggleSection(h);
                    }
                });
            });
        }

        // Stable per-file permalinks: reports are immutable once archived, so
        // an index-based id identifies the same change for every reader of
        // this file. The 🔗 button stamps the id into the hash and copies the
        // full URL.
        function initPermalinks() {
            document.querySelectorAll('.section').forEach(sec => {
                let n = 0;
                sec.querySelectorAll('tbody tr.added, tbody tr.removed, tbody tr.modified')
                    .forEach(tr => {
                        const id = sec.id + '-c' + (++n);
                        tr.id = id;
                        const cell = tr.cells[0];
                        if (!cell) return;
                        cell.classList.add('linkcell');
                        const wrap = document.createElement('span');
                        wrap.className = 'copybtns';
                        const b = document.createElement('button');
                        b.type = 'button';
                        b.textContent = '🔗';
                        b.title = 'Copy a link to this change';
                        b.addEventListener('click', e => {
                            e.stopPropagation();
                            hashTarget = id;
                            syncHash();
                            copyText(location.href, b);
                        });
                        wrap.appendChild(b);
                        cell.appendChild(wrap);
                    });
            });
        }

        function initColumnResize() {
            document.querySelectorAll('table').forEach(table => {
                const headerRow = table.querySelector('thead tr');
                if (!headerRow) return;
                const ths = Array.from(headerRow.children);
                ths.forEach((th, i) => {
                    if (i === ths.length - 1) return;  // last column fills remaining space
                    const handle = document.createElement('div');
                    handle.className = 'col-resizer';
                    th.appendChild(handle);

                    handle.addEventListener('mousedown', e => {
                        e.preventDefault();
                        e.stopPropagation();
                        const startX = e.clientX;
                        // Freeze every column's CURRENT rendered width as an
                        // inline px value before switching to fixed layout,
                        // so dragging one column doesn't reflow the rest of
                        // the table.
                        table.style.tableLayout = 'fixed';
                        ths.forEach(h => { h.style.width = h.offsetWidth + 'px'; });
                        const startWidth = th.offsetWidth;
                        // Freeze the table's own width too and adjust it by
                        // the same delta as the dragged column, so only that
                        // one column ever changes size — the table grows or
                        // shrinks instead of redistributing (the Excel-style
                        // behaviour).
                        const startTableWidth = table.offsetWidth;
                        table.style.width = startTableWidth + 'px';
                        handle.classList.add('resizing');
                        document.body.classList.add('col-resizing');

                        function onMove(ev) {
                            const delta = ev.clientX - startX;
                            const newWidth = Math.max(40, startWidth + delta);
                            const actualDelta = newWidth - startWidth;
                            th.style.width = newWidth + 'px';
                            table.style.width = (startTableWidth + actualDelta) + 'px';
                        }
                        function onUp() {
                            document.removeEventListener('mousemove', onMove);
                            document.removeEventListener('mouseup', onUp);
                            handle.classList.remove('resizing');
                            document.body.classList.remove('col-resizing');
                        }
                        document.addEventListener('mousemove', onMove);
                        document.addEventListener('mouseup', onUp);
                    });
                });
            });
        }

        // Default: hide sections with no changes; user can toggle them back on.
        setTheme((() => { try { return localStorage.getItem('projxdiff-theme') || ''; } catch (e) { return ''; } })());
        applyHashState();          // restore filters/target from a shared URL
        applySectionVisibility();
        applyLookupRowVisibility();
        filterRows();
        initNav();
        initSectionKeyboard();
        initColumnResize();
        initCopyButtons();
        initPermalinks();          // ids must exist before the jump below
        if (hashTarget) revealTarget(hashTarget);
    </script>
    <footer>
        Projx Diff v''' + __version__ + ''' &middot;
        <a href="''' + __url__ + '''">''' + __url__ + '''</a>
        <div style="margin-top:8px;font-size:11px;line-height:1.5;">
            Projx Diff is an independent tool by Base 10 Consultants. It is not
            affiliated with, endorsed by, or tested by DriveWorks&trade; Ltd.
            DriveWorks&trade; is a trademark of DriveWorks Ltd.
        </div>
    </footer>
    </main>
</div>
</body>
</html>
'''
    return html
