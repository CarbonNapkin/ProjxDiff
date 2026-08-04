"""
HTML report generation for DriveWorks comparison.
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
    
    # Build full HTML
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Projx Diff — Project Comparison</title>
    <style>
        :root {{
            --added-bg: #e6ffec;
            --added-bg-strong: #c8f0d3;
            --added-border: #2e9b40;
            --removed-bg: #ffebe9;
            --removed-bg-strong: #f7c8c4;
            --removed-border: #d33b30;
            --modified-bg: #fff8e1;
            --modified-bg-strong: #ffe9a8;
            --modified-border: #e6890c;
            --unchanged-bg: #f5f5f5;
            --rule-bg: #f7f8fa;
            --header-stack: 44px;
        }}

        * {{ box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.4;
            max-width: 1400px;
            margin: 0 auto;
            padding: 14px 16px 32px;
            background: #fafafa;
            color: #1f2024;
        }}

        h1 {{
            color: #1a237e;
            border-bottom: 2px solid #3f51b5;
            padding-bottom: 6px;
            font-size: 22px;
            margin: 0 0 8px;
        }}

        .meta {{
            color: #555;
            font-size: 13px;
            margin: 0 0 10px;
        }}

        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px;
            margin: 10px 0 12px;
        }}

        .stat-card {{
            padding: 8px 12px;
            border-radius: 6px;
            font-weight: 600;
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            font-size: 13px;
        }}
        .stat-card .stat-num {{ font-size: 20px; font-weight: 700; }}

        .stat-added {{ background: var(--added-bg); border-left: 4px solid var(--added-border); }}
        .stat-removed {{ background: var(--removed-bg); border-left: 4px solid var(--removed-border); }}
        .stat-modified {{ background: var(--modified-bg); border-left: 4px solid var(--modified-border); }}
        .stat-unchanged {{ background: var(--unchanged-bg); border-left: 4px solid #9e9e9e; }}

        /* Filter bar sticks at top of viewport while scrolling. */
        .filter-bar {{
            position: sticky;
            top: 0;
            z-index: 10;
            margin: 0 0 14px;
            padding: 8px 10px;
            background: rgba(255,255,255,0.92);
            backdrop-filter: saturate(180%) blur(8px);
            border: 1px solid #e3e5e9;
            border-radius: 8px;
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
            cursor: pointer;
            border: 1px solid #c8ccd2;
            background: #fff;
            border-radius: 6px;
        }}
        .filter-bar button:hover {{ background: #f0f1f4; }}

        #searchBox {{
            flex: 1 0 220px;
            min-width: 220px;
            padding: 5px 10px;
            border: 1px solid #c8ccd2;
            border-radius: 6px;
            font-size: 13px;
        }}
        #searchBox:focus {{
            outline: 2px solid #3f51b5;
            border-color: #3f51b5;
        }}

        .section {{
            background: white;
            border-radius: 8px;
            margin: 10px 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            overflow: hidden;
        }}

        /* Sections with no changes hide entirely by default; toggle restores them. */
        body.hide-quiet .section[data-quiet="1"] {{ display: none; }}

        .section-header {{
            background: #3f51b5;
            color: white;
            padding: 8px 14px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
        }}
        .section-header:hover {{ background: #303f9f; }}
        .section-header .title {{ font-weight: 600; }}

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
            background: #fafbfd;
            border-top: 1px solid #e6e8ec;
            border-bottom: 1px solid #e6e8ec;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }}
        .section-content > h3:first-child {{ border-top: none; }}
        .section-content > h3.added {{ background: var(--added-bg); }}
        .section-content > h3.removed {{ background: var(--removed-bg); }}
        .section-content > h3.modified {{ background: var(--modified-bg); }}
        .section-content > h3 small {{ color: #555; font-weight: 500; margin-left: 6px; }}

        .section-content > p.empty {{ padding: 12px 14px; margin: 0; }}

        .badge {{
            display: inline-block;
            padding: 1px 8px;
            border-radius: 10px;
            font-size: 11px;
            margin-left: 6px;
            vertical-align: middle;
            white-space: nowrap;
        }}
        .badge-added {{ background: var(--added-border); color: white; }}
        .badge-removed {{ background: var(--removed-border); color: white; }}
        .badge-modified {{ background: var(--modified-border); color: white; }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        th, td {{
            padding: 5px 10px;
            text-align: left;
            border-bottom: 1px solid #ececef;
            vertical-align: top;
        }}
        td:first-child {{ word-break: break-word; }}

        /* Table column header sticks just under any sticky h3. */
        th {{
            background: #f0f2f5;
            font-weight: 600;
            position: sticky;
            top: var(--header-stack);
            z-index: 2;
            box-shadow: inset 0 -1px 0 #d8dbe0;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.02em;
            color: #495160;
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
            background: #3f51b5;
            opacity: 0.5;
        }}
        body.col-resizing, body.col-resizing * {{ cursor: col-resize !important; user-select: none !important; }}

        tbody tr:hover {{ background: #eef1f5; }}
        tr.added {{ background: var(--added-bg); }}
        tr.added:hover {{ background: var(--added-bg-strong); }}
        tr.removed {{ background: var(--removed-bg); }}
        tr.removed:hover {{ background: var(--removed-bg-strong); }}
        tr.modified {{ background: var(--modified-bg); }}
        tr.modified:hover {{ background: var(--modified-bg-strong); }}

        /* Group-start marks the first row of a parent-child group (Form ->
           Control -> Property, Macro -> Task -> Property, CalcTable Column
           -> Scope). Repeated cells are blank on later rows, so this border
           draws the visible parent boundary. */
        tbody tr.group-start td {{ border-top: 2px solid #b8bcc4; }}
        tbody tr.group-start:first-child td {{ border-top: none; }}
        /* The first one or two cells of a row are identity cells; on later
           rows in a group they are blank. Give them slightly muted styling so
           the eye reads the grouped chunk as one block. */
        td.grouper {{ font-weight: 500; color: #2a2c30; background: rgba(0,0,0,0.015); }}
        tr.added td.grouper, tr.removed td.grouper, tr.modified td.grouper {{
            background: rgba(0,0,0,0.04);
        }}

        /* Lookup-table grids render the actual CSV data with per-cell
           highlighting. Unchanged rows are hidden by default; the
           "Show unchanged lookup rows" filter brings them back. */
        table.lookup-grid {{ table-layout: auto; }}
        table.lookup-grid th {{
            top: var(--header-stack);
            white-space: nowrap;
        }}
        table.lookup-grid th.col-added {{ background: var(--added-bg); }}
        table.lookup-grid th.col-removed {{ background: var(--removed-bg); }}
        table.lookup-grid td {{
            font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
            font-size: 12px;
            max-width: 320px;
            overflow-wrap: anywhere;
        }}
        table.lookup-grid td.cell-changed {{
            background: var(--modified-bg-strong);
            font-weight: 500;
        }}
        table.lookup-grid td.cell-added {{ background: var(--added-bg-strong); }}
        table.lookup-grid td.cell-removed {{ background: var(--removed-bg-strong); }}
        body:not(.show-lookup-unchanged) table.lookup-grid tbody tr.unchanged {{ display: none; }}

        .formula {{
            font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
            font-size: 12px;
            white-space: pre-wrap;
            word-break: break-word;
            background: var(--rule-bg);
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

        span.added {{
            background: #b6e8c1;
            padding: 0 3px;
            border-radius: 3px;
        }}
        span.removed {{
            background: #f6c1c1;
            padding: 0 3px;
            border-radius: 3px;
            text-decoration: line-through;
        }}

        .empty {{ color: #888; font-style: italic; }}
        .attr-note {{ color: #888; font-size: 11px; margin-top: 3px; }}
        .toggle {{ font-size: 18px; user-select: none; }}
    </style>
</head>
<body>
    <h1>🔄 Projx Diff — Project Comparison</h1>
    
    <div class="meta">
        <strong>Old:</strong> {escape(old_name)} &nbsp;→&nbsp; <strong>New:</strong> {escape(new_name)}<br>
        Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by
        <a href="{__url__}" style="color:#3f51b5;text-decoration:none;">Projx Diff v{__version__}</a>
    </div>
    
    <div class="summary">
        <div class="stat-card stat-added"><span>➕ Added</span><span class="stat-num">{summary['added']}</span></div>
        <div class="stat-card stat-removed"><span>➖ Removed</span><span class="stat-num">{summary['removed']}</span></div>
        <div class="stat-card stat-modified"><span>✏️ Modified</span><span class="stat-num">{summary['modified']}</span></div>
        <div class="stat-card stat-unchanged"><span>✓ Unchanged</span><span class="stat-num">{summary['unchanged']}</span></div>
    </div>

    <div class="filter-bar">
        <input type="text" id="searchBox" placeholder="🔍 Search names, formulas..." oninput="filterRows()">
        <label><input type="checkbox" id="showAdded" checked onchange="filterRows()"> Added</label>
        <label><input type="checkbox" id="showRemoved" checked onchange="filterRows()"> Removed</label>
        <label><input type="checkbox" id="showModified" checked onchange="filterRows()"> Modified</label>
        <label><input type="checkbox" id="showUnchanged" onchange="filterRows()"> Unchanged rows</label>
        <label><input type="checkbox" id="showQuietSections" onchange="applySectionVisibility()"> Show unchanged sections</label>
        <label><input type="checkbox" id="showLookupUnchanged" onchange="applyLookupRowVisibility()"> Show unchanged lookup rows</label>
        <button onclick="expandAll(true)">Expand all</button>
        <button onclick="expandAll(false)">Collapse all</button>
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
            badges += f'<span class="badge" style="background:#dadde2;color:#3b3f48">{unchanged_count} unchanged</span>'

        html += f'''
    <div class="section {collapsed}" data-quiet="{1 if quiet else 0}">
        <div class="section-header" onclick="this.parentElement.classList.toggle('collapsed')">
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
                    sec => sec.classList.remove('collapsed')
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
        }

        function applySectionVisibility() {
            const show = document.getElementById('showQuietSections').checked;
            document.body.classList.toggle('hide-quiet', !show);
        }

        function applyLookupRowVisibility() {
            const show = document.getElementById('showLookupUnchanged').checked;
            document.body.classList.toggle('show-lookup-unchanged', show);
        }

        function expandAll(open) {
            document.querySelectorAll('.section').forEach(s => {
                if (open) s.classList.remove('collapsed');
                else s.classList.add('collapsed');
            });
        }

        function initColumnResize() {{
            document.querySelectorAll('table').forEach(table => {{
                const headerRow = table.querySelector('thead tr');
                if (!headerRow) return;
                const ths = Array.from(headerRow.children);
                ths.forEach((th, i) => {{
                    if (i === ths.length - 1) return;  // last column fills remaining space
                    const handle = document.createElement('div');
                    handle.className = 'col-resizer';
                    th.appendChild(handle);

                    handle.addEventListener('mousedown', e => {{
                        e.preventDefault();
                        e.stopPropagation();
                        const startX = e.clientX;
                        // Freeze every column's CURRENT rendered width as an
                        // inline px value before switching to fixed layout,
                        // so dragging one column doesn't reflow the rest of
                        // the table. Measuring here (not at page load) means
                        // this works correctly even for tables inside a
                        // collapsed/hidden section, since it's only measured
                        // once the user can actually see and click it.
                        table.style.tableLayout = 'fixed';
                        ths.forEach(h => {{ h.style.width = h.offsetWidth + 'px'; }});
                        const startWidth = th.offsetWidth;
                        // The table itself was still CSS width:100% - with
                        // every column now pinned to an explicit px width,
                        // that 100% constraint forced the browser to
                        // proportionally stretch/shrink every OTHER column
                        // to keep the total at 100% whenever the dragged
                        // one changed (this is what made columns to the
                        // LEFT appear to grow when dragging left). Freezing
                        // the table's own width in px too, and adjusting it
                        // by the same delta as the dragged column, means
                        // only that one column ever changes - the table
                        // grows/shrinks instead of redistributing.
                        const startTableWidth = table.offsetWidth;
                        table.style.width = startTableWidth + 'px';
                        handle.classList.add('resizing');
                        document.body.classList.add('col-resizing');

                        function onMove(ev) {{
                            const delta = ev.clientX - startX;
                            const newWidth = Math.max(40, startWidth + delta);
                            const actualDelta = newWidth - startWidth;
                            th.style.width = newWidth + 'px';
                            table.style.width = (startTableWidth + actualDelta) + 'px';
                        }}
                        function onUp() {{
                            document.removeEventListener('mousemove', onMove);
                            document.removeEventListener('mouseup', onUp);
                            handle.classList.remove('resizing');
                            document.body.classList.remove('col-resizing');
                        }}
                        document.addEventListener('mousemove', onMove);
                        document.addEventListener('mouseup', onUp);
                    }});
                }});
            }});
        }}

        // Default: hide sections with no changes; user can toggle them back on.
        applySectionVisibility();
        applyLookupRowVisibility();
        filterRows();
        initColumnResize();
    </script>
    <footer style="margin-top:24px;padding-top:12px;border-top:1px solid #e3e5e9;color:#888;font-size:12px;text-align:center;">
        Projx Diff v''' + __version__ + ''' &middot;
        <a href="''' + __url__ + '''" style="color:#888;">''' + __url__ + '''</a>
        <div style="margin-top:8px;font-size:11px;line-height:1.5;">
            Projx Diff is an independent tool by Base 10 Consultants. It is not
            affiliated with, endorsed by, or tested by DriveWorks&trade; Ltd.
            DriveWorks&trade; is a trademark of DriveWorks Ltd.
        </div>
    </footer>
</body>
</html>
'''
    return html