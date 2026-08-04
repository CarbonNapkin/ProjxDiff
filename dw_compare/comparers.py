"""
Comparison functions for DriveWorks project elements.
"""

import csv
import io
import re
from difflib import SequenceMatcher
from html import escape


# Tokenize a formula/value for a fine-grained diff: quoted strings, identifiers
# (incl. dotted names), numbers, whitespace runs, or any single other character
# (operators, parens, commas). Tokens concatenate back to the original string,
# so the joined diff output reproduces the source exactly.
_TOKEN_RE = re.compile(r'"[^"]*"|\'[^\']*\'|[A-Za-z_][\w.]*|\d[\d.]*|\s+|.', re.DOTALL)


def _tokenize(s: str) -> list:
    return _TOKEN_RE.findall(s)


def inline_diff(old: str, new: str) -> str:
    """Inline HTML diff at the token level. DriveWorks formulas rarely contain
    spaces, so a word-level diff would re-highlight the whole formula on any
    change; tokenizing on identifiers / numbers / operators keeps the highlight
    tight (e.g. only the changed number lights up)."""
    if old == new:
        return escape(new)

    if not old:
        return f'<span class="added">{escape(new)}</span>'
    if not new:
        return f'<span class="removed">{escape(old)}</span>'

    old_tokens = _tokenize(old)
    new_tokens = _tokenize(new)
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    result = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            result.append(escape(''.join(old_tokens[i1:i2])))
        elif tag == 'replace':
            result.append(f'<span class="removed">{escape("".join(old_tokens[i1:i2]))}</span>')
            result.append(f'<span class="added">{escape("".join(new_tokens[j1:j2]))}</span>')
        elif tag == 'delete':
            result.append(f'<span class="removed">{escape("".join(old_tokens[i1:i2]))}</span>')
        elif tag == 'insert':
            result.append(f'<span class="added">{escape("".join(new_tokens[j1:j2]))}</span>')

    return ''.join(result)


def _filename(path: str) -> str:
    """Just the file name — the segment after the last path separator —
    since that's what's actually useful to scan in a rule diff; the full
    path is kept as a hover tooltip in case the folder ever matters for
    telling two same-named parts apart. Handles both backslash (the DB
    stores Windows/UNC paths) and forward slash."""
    if not path:
        return path
    return re.split(r'[\\/]', path)[-1]


def compare_dicts(old: dict, new: dict) -> tuple[set, set, set]:
    """Compare two dicts, return (added, removed, common) keys"""
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    
    added = new_keys - old_keys
    removed = old_keys - new_keys
    common = old_keys & new_keys

    return added, removed, common


def _diff_rule_dicts(old_rules: dict, new_rules: dict) -> list:
    """Return [(rule_name, status, old_formula, new_formula)] for rules that
    differ between two {name: formula} dicts."""
    out = []
    for k in sorted(set(old_rules) | set(new_rules)):
        o, n = old_rules.get(k), new_rules.get(k)
        if o == n:
            continue
        if k not in old_rules:
            out.append((k, 'added', '', n))
        elif k not in new_rules:
            out.append((k, 'removed', o, ''))
        else:
            out.append((k, 'modified', o, n))
    return out


def _attr_notes(pairs: list) -> str:
    """Muted sub-notes for secondary attribute changes (e.g. store name,
    comment). `pairs` is [(label, old, new)]; only changed pairs render."""
    out = []
    for label, o, n in pairs:
        o, n = o or '', n or ''
        if o != n:
            out.append(f'<div class="attr-note">{escape(label)}: {inline_diff(o, n)}</div>')
    return ''.join(out)


def compare_variables(old: dict, new: dict) -> tuple[str, dict]:
    """Compare variables. Shows the resolved Category and the formula, plus any
    store-name / comment change as muted sub-notes. A variable counts as
    modified if its formula, category, store name, or comment changed."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    rows = []

    def cat(v):
        return escape(v.category) if v.category else ''

    for name in sorted(added):
        v = new[name]
        rows.append(f'<tr class="added"><td>{escape(name)}</td><td>{cat(v)}</td>'
                    f'<td><span class="badge badge-added">Added</span></td>'
                    f'<td class="formula">{escape(v.formula)}</td></tr>')

    for name in sorted(removed):
        v = old[name]
        rows.append(f'<tr class="removed"><td>{escape(name)}</td><td>{cat(v)}</td>'
                    f'<td><span class="badge badge-removed">Removed</span></td>'
                    f'<td class="formula">{escape(v.formula)}</td></tr>')

    for name in sorted(common):
        o, n = old[name], new[name]
        notes = _attr_notes([('store', o.store_name, n.store_name),
                             ('comment', o.comment, n.comment)])
        cat_cell = cat(n) if o.category == n.category else inline_diff(o.category, n.category)
        if o.formula != n.formula or o.category != n.category or notes:
            stats['modified'] += 1
            formula_cell = inline_diff(o.formula, n.formula) if o.formula != n.formula else escape(n.formula)
            rows.append(f'<tr class="modified"><td>{escape(name)}</td><td>{cat_cell}</td>'
                        f'<td><span class="badge badge-modified">Modified</span></td>'
                        f'<td class="formula">{formula_cell}{notes}</td></tr>')
        else:
            stats['unchanged'] += 1
            rows.append(f'<tr class="unchanged"><td>{escape(name)}</td><td>{cat(n)}</td>'
                        f'<td>·</td><td class="formula">{escape(n.formula)}</td></tr>')

    html = f'''<table>
        <thead><tr><th>Variable Name</th><th>Category</th><th>Status</th><th>Formula</th></tr></thead>
        <tbody>{"".join(rows) if rows else '<tr><td colspan="4" class="empty">No variables found</td></tr>'}</tbody>
    </table>'''

    return html, stats


def compare_constants(old: dict, new: dict) -> tuple[str, dict]:
    """Compare constants"""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    
    rows = []
    
    for name in sorted(added):
        c = new[name]
        rows.append(f'<tr class="added"><td>{escape(name)}</td><td><span class="badge badge-added">Added</span></td>'
                   f'<td>{escape(c.value)}</td></tr>')
    
    for name in sorted(removed):
        c = old[name]
        rows.append(f'<tr class="removed"><td>{escape(name)}</td><td><span class="badge badge-removed">Removed</span></td>'
                   f'<td>{escape(c.value)}</td></tr>')
    
    for name in sorted(common):
        o, n = old[name], new[name]
        notes = _attr_notes([('store', o.store_name, n.store_name),
                             ('comment', o.comment, n.comment)])
        if o.value != n.value or notes:
            stats['modified'] += 1
            value_cell = inline_diff(o.value, n.value) if o.value != n.value else escape(n.value)
            rows.append(f'<tr class="modified"><td>{escape(name)}</td><td><span class="badge badge-modified">Modified</span></td>'
                       f'<td>{value_cell}{notes}</td></tr>')
        else:
            stats['unchanged'] += 1
            rows.append(f'<tr class="unchanged"><td>{escape(name)}</td><td>·</td>'
                       f'<td>{escape(n.value)}</td></tr>')

    html = f'''<table>
        <thead><tr><th>Constant Name</th><th>Status</th><th>Value</th></tr></thead>
        <tbody>{"".join(rows) if rows else '<tr><td colspan="3" class="empty">No constants found</td></tr>'}</tbody>
    </table>'''
    
    return html, stats


def _calc_table_changes(old_tbl, new_tbl) -> list:
    """Flat list of (column, scope, status, old_val, new_val) covering a
    row-count change and every per-column difference between two CalcTables.
    Empty list means the tables are identical. Shared by the HTML renderer
    and the JSON diff (jsondiff.py) so both agree on what changed."""
    changes = []
    if old_tbl.row_count != new_tbl.row_count:
        changes.append(('(row count)', '', 'modified',
                        str(old_tbl.row_count), str(new_tbl.row_count)))
    all_cols = set(old_tbl.columns.keys()) | set(new_tbl.columns.keys())

    for col in sorted(all_cols):
        old_col = old_tbl.columns.get(col, {'common': '', 'rows': {}})
        new_col = new_tbl.columns.get(col, {'common': '', 'rows': {}})

        if col not in old_tbl.columns:
            changes.append((col, 'Common', 'added', '', new_col['common']))
            for idx in sorted(new_col['rows']):
                changes.append((col, f'Row {idx}', 'added', '', new_col['rows'][idx]))
        elif col not in new_tbl.columns:
            changes.append((col, 'Common', 'removed', old_col['common'], ''))
            for idx in sorted(old_col['rows']):
                changes.append((col, f'Row {idx}', 'removed', old_col['rows'][idx], ''))
        else:
            if old_col['common'] != new_col['common']:
                changes.append((col, 'Common', 'modified', old_col['common'], new_col['common']))
            for idx in sorted(set(old_col['rows']) | set(new_col['rows'])):
                o = old_col['rows'].get(idx, '')
                n = new_col['rows'].get(idx, '')
                if o == n:
                    continue
                if not o:
                    changes.append((col, f'Row {idx}', 'added', '', n))
                elif not n:
                    changes.append((col, f'Row {idx}', 'removed', o, ''))
                else:
                    changes.append((col, f'Row {idx}', 'modified', o, n))
    return changes


def _calc_row(col: str, scope: str, status: str, old_val: str, new_val: str,
              first_in_group: bool = False) -> str:
    """Emit one row of a calculation-table diff. The Column cell is blanked
    on rows after the first row in that column's group, matching the same
    grouping treatment used by Forms and Macros."""
    badge = f'<span class="badge badge-{status}">{status.title()}</span>'
    if status == 'modified':
        diff = inline_diff(old_val, new_val)
    else:
        diff = escape(new_val or old_val)
    cls = f'{status}{" group-start" if first_in_group else ""}'
    col_cell = escape(col) if first_in_group else ''
    return (
        f'<tr class="{cls}"><td class="grouper">{col_cell}</td>'
        f'<td>{escape(scope)}</td>'
        f'<td>{badge}</td><td class="formula">{diff}</td></tr>'
    )


def compare_calc_tables(old: dict, new: dict) -> tuple[str, dict]:
    """Compare calculation tables"""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}

    html_parts = []

    for name in sorted(added):
        html_parts.append(
            f'<h3 class="added">➕ {escape(name)} <span class="badge badge-added">Added</span></h3>'
        )

    for name in sorted(removed):
        html_parts.append(
            f'<h3 class="removed">➖ {escape(name)} <span class="badge badge-removed">Removed</span></h3>'
        )

    for name in sorted(common):
        changes = _calc_table_changes(old[name], new[name])
        rows_html = []
        # A new column value in the flat change list starts a visual group.
        prev_col = object()
        for col, scope, status, old_v, new_v in changes:
            rows_html.append(_calc_row(col, scope, status, old_v, new_v,
                                       first_in_group=(col != prev_col)))
            prev_col = col

        if rows_html:
            stats['modified'] += 1
            html_parts.append(
                f'<h3 class="modified">📊 {escape(name)} <span class="badge badge-modified">Modified</span></h3>'
                f'<table><thead><tr><th>Column</th><th>Scope</th><th>Status</th><th>Formula</th></tr></thead>'
                f'<tbody>{"".join(rows_html)}</tbody></table>'
            )
        else:
            stats['unchanged'] += 1

    return ''.join(html_parts) if html_parts else '<p class="empty">No calculation tables found</p>', stats


def compare_component_tasks(old: dict, new: dict) -> tuple[str, dict]:
    """Compare component tasks, with a rule-level breakdown under each modified
    task (which rule changed and how). Unchanged tasks are counted but not
    listed, matching the Forms/Macros sections."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    rows = []

    def head_row(t, status):
        badge = f'<span class="badge badge-{status}">{status.title()}</span>'
        where = escape(t.component_id or t.scope)
        extra = f'{where} · {len(t.rules)} rules' if status != 'modified' else where
        return (f'<tr class="{status} group-start"><td class="grouper">{escape(t.name)}</td>'
                f'<td class="grouper">{escape(t.task_type)}</td>'
                f'<td colspan="2">{badge}</td><td>{extra}</td></tr>')

    for key in sorted(added):
        rows.append(head_row(new[key], 'added'))
    for key in sorted(removed):
        rows.append(head_row(old[key], 'removed'))

    for key in sorted(common):
        ot, nt = old[key], new[key]
        rule_changes = _diff_rule_dicts(ot.rules, nt.rules)
        if not rule_changes:
            stats['unchanged'] += 1
            continue
        stats['modified'] += 1
        rows.append(head_row(nt, 'modified'))
        for rid, status, of, nf in rule_changes:
            badge = f'<span class="badge badge-{status}">{status.title()}</span>'
            cell = inline_diff(of, nf) if status == 'modified' else escape(nf or of)
            rows.append(f'<tr class="{status}"><td class="grouper"></td><td class="grouper"></td>'
                        f'<td>{escape(rid)}</td><td>{badge}</td><td class="formula">{cell}</td></tr>')

    body = "".join(rows) if rows else '<tr><td colspan="5" class="empty">No component tasks found</td></tr>'
    html = ('<table class="grouped"><thead><tr><th>Task</th><th>Type</th><th>Rule</th>'
            '<th>Status</th><th>Formula</th></tr></thead>'
            f'<tbody>{body}</tbody></table>')
    return html, stats


def compare_component_sets(old: dict, new: dict) -> tuple[str, dict]:
    """Compare Component Sets — named top-level model factories from
    project.xml (e.g. 'R1-Aluminum', 'CRITICAL ENVIRONMENT'). Names and
    generation rules are free: no database lookup needed. A set counts as
    modified if its generation rule or type (PartFactory/AssemblyFactory)
    changed."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    rows = []

    for name in sorted(added):
        s = new[name]
        rows.append(f'<tr class="added"><td>{escape(name)}</td><td>{escape(s.set_type)}</td>'
                    f'<td><span class="badge badge-added">Added</span></td>'
                    f'<td class="formula">{escape(s.rule)}</td></tr>')

    for name in sorted(removed):
        s = old[name]
        rows.append(f'<tr class="removed"><td>{escape(name)}</td><td>{escape(s.set_type)}</td>'
                    f'<td><span class="badge badge-removed">Removed</span></td>'
                    f'<td class="formula">{escape(s.rule)}</td></tr>')

    for name in sorted(common):
        o, n = old[name], new[name]
        type_cell = escape(n.set_type) if o.set_type == n.set_type else inline_diff(o.set_type, n.set_type)
        if o.rule != n.rule or o.set_type != n.set_type:
            stats['modified'] += 1
            rule_cell = inline_diff(o.rule, n.rule) if o.rule != n.rule else escape(n.rule)
            rows.append(f'<tr class="modified"><td>{escape(name)}</td><td>{type_cell}</td>'
                        f'<td><span class="badge badge-modified">Modified</span></td>'
                        f'<td class="formula">{rule_cell}</td></tr>')
        else:
            stats['unchanged'] += 1
            rows.append(f'<tr class="unchanged"><td>{escape(name)}</td><td>{escape(n.set_type)}</td>'
                        f'<td>·</td><td class="formula">{escape(n.rule)}</td></tr>')

    html = f'''<table>
        <thead><tr><th>Component Set</th><th>Type</th><th>Status</th><th>Generation Rule</th></tr></thead>
        <tbody>{"".join(rows) if rows else '<tr><td colspan="4" class="empty">No component sets found</td></tr>'}</tbody>
    </table>'''

    return html, stats


def compare_models(old_resolved: dict, new_resolved: dict) -> tuple[str, dict]:
    """Diff two sides' resolved CCRef/TrId -> name maps by FILE NAME, not the
    full path and not the raw id. Different DriveWorks group databases can
    assign a different id to what a person would call the same file, so id
    equality isn't a safe way to tell "unchanged" from "id just churned"
    apart — and the folder a file lives in isn't part of its identity either
    (the same model moved to a different folder shouldn't read as removed +
    added). File name is the portable one; full path is kept as a hover
    tooltip in case the folder ever matters for telling two same-named parts
    apart. old_resolved/new_resolved come from components.resolve_names()
    and are empty when no database was supplied, in which case this section
    has nothing to compare."""
    stats = {'added': 0, 'removed': 0, 'modified': 0, 'unchanged': 0}
    if not old_resolved and not new_resolved:
        return ('<p class="empty">No database connection supplied — model names were not resolved. '
                'Raw component/model ids still appear under Component Tasks.</p>', stats)

    # Map filename -> one example full path (for the hover tooltip); when
    # multiple captured files share a name, the last one wins, which is fine
    # since it's just a tooltip, not part of the comparison itself.
    old_by_name = {_filename(v): v for v in old_resolved.values() if v}
    new_by_name = {_filename(v): v for v in new_resolved.values() if v}
    old_names = set(old_by_name)
    new_names = set(new_by_name)
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    stats['added'] = len(added)
    stats['removed'] = len(removed)
    stats['unchanged'] = len(old_names & new_names)

    rows = []
    for n in added:
        title_attr = f' title="{escape(new_by_name[n], quote=True)}"'
        rows.append(f'<tr class="added"><td><span class="badge badge-added">Added</span></td>'
                    f'<td{title_attr}>{escape(n)}</td></tr>')
    for n in removed:
        title_attr = f' title="{escape(old_by_name[n], quote=True)}"'
        rows.append(f'<tr class="removed"><td><span class="badge badge-removed">Removed</span></td>'
                    f'<td{title_attr}>{escape(n)}</td></tr>')

    body = "".join(rows) if rows else '<tr><td colspan="2" class="empty">No models added or removed</td></tr>'
    html = (f'<table><thead><tr><th>Status</th><th>Model</th></tr></thead><tbody>{body}</tbody></table>'
            f'<p class="attr-note">{stats["unchanged"]} model(s) unchanged — matched by file name, '
            f'not folder location or database id, since the same real file can live in a different '
            f'folder or carry a different id in each database.</p>')
    return html, stats


def compare_property_rules(old_idx, new_idx, old_resolved: dict = None, new_resolved: dict = None,
                            old_prop_resolved: dict = None, new_prop_resolved: dict = None,
                            old_prop_types: dict = None, new_prop_types: dict = None) -> tuple[str, dict]:
    """Diff every driven property (D1@Sketch1-style) between the two
    projects' components.ComponentIndex objects. Matched by rule_id, which
    is confirmed unique per placement — even when the same file is placed
    in the tree multiple times, each placement's rules have their own
    rule_id, so this never silently merges two placements together. Each
    row is labeled with a breadcrumb (tree position, built from the model
    names already resolved) so repeated placements of the same file are
    told apart. old/new_resolved name the models in the breadcrumb;
    old/new_prop_resolved (from decoding CapturedComponents.Data) turn
    cp_ref/ce_ref into a D1@Sketch1-style property name — both fall back to
    raw GUIDs when no database was supplied. old/new_prop_types (from the
    SAME Data blobs' T attribute) are the authoritative Type-column source
    — see rule_type_for and components.TYPE_GUID_KIND. Rows where neither
    side has a formula (an unbound placeholder) are skipped; those aren't
    real rules.
    """
    old_resolved = old_resolved or {}
    new_resolved = new_resolved or {}
    old_prop_resolved = old_prop_resolved or {}
    new_prop_resolved = new_prop_resolved or {}
    old_prop_types = old_prop_types or {}
    new_prop_types = new_prop_types or {}

    from . import components as C

    old_by_rid = {C._norm(p.rule_id): p for p in old_idx.property_rules if p.rule_id}
    new_by_rid = {C._norm(p.rule_id): p for p in new_idx.property_rules if p.rule_id}

    added_rids = set(new_by_rid) - set(old_by_rid)
    removed_rids = set(old_by_rid) - set(new_by_rid)
    shared_rids = set(old_by_rid) & set(new_by_rid)
    modified_rids = {rid for rid in shared_rids if old_by_rid[rid].formula != new_by_rid[rid].formula}

    total_compared = len(shared_rids) + len(added_rids) + len(removed_rids)
    stats = {'added': 0, 'removed': 0, 'modified': 0,
             'unchanged': total_compared - len(modified_rids)}

    def crumb(pr, idx, resolved):
        """(short, full) breadcrumb pair. short shortens each ancestor to
        just its file name (e.g. 'PLENUM.SLDASM' not the full T:\\... path)
        since that's what's actually useful to scan in a rule diff; full is
        kept for a hover tooltip in case the folder ever matters."""
        full = idx.breadcrumb(pr.owner_path, resolved)
        if not full:
            return "(unresolved placement)", ""
        short = " > ".join(_filename(p) for p in full.split(" > "))
        return short, full

    def rule_type_for(pr, resolved_props, resolved_types):
        """Type-column label for a "dimension" or "instance" kind rule (the
        two PP-based kinds — see PropertyRule.kind).

        AUTHORITATIVE PATH: CapturedComponents.Data's own T attribute on
        the cp_ref's <ccomp:P> element is a stable, per-category
        type-classification GUID DriveWorks itself writes — see
        components.TYPE_GUID_KIND. Confirmed directly against a real
        decoded Data blob: Dimension/Feature/Instance each have their own
        constant T value, and critically, a resolved cp_ref name does NOT
        imply Dimension — the confirmed "Instance" example (an
        instance-naming property, cp_ref resolving to e.g. "MyPart-1") has
        a real name AND its own distinct T-guid, disproving that shortcut.
        This is checked first and is authoritative whenever available.

        FALLBACK (no database, or an unrecognized T-guid — e.g. an older
        DriveWorks schema): the structural ce_ref split from parsing (kind
        == "instance" vs "dimension") plus whether names resolve. This is
        a best-effort guess, not authoritative — kept only so the column
        still shows something plausible rather than nothing when the real
        signal isn't available.
        """
        type_guid = resolved_types.get(C._norm(pr.cp_ref))
        authoritative_kind = C.TYPE_GUID_KIND.get(type_guid)
        if authoritative_kind:
            return C.KIND_LABELS[authoritative_kind]

        # --- fallback: no authoritative type-guid available ---
        if pr.kind not in ("dimension", "instance"):
            # file_name/relative_path/tag/loop_control have no cp_ref/ce_ref
            # at all (see PropertyRule.kind) - nothing to resolve, their
            # kind IS the answer, and it's structural (not a guess).
            return C.KIND_LABELS.get(pr.kind, pr.kind)
        cp_name = resolved_props.get(C._norm(pr.cp_ref))
        if pr.kind == "instance":
            return C.KIND_LABELS["dimension"] if cp_name else C.KIND_LABELS["instance"]
        # pr.kind == "dimension" from here on: ce_ref is a real entity GUID.
        if cp_name:
            return C.KIND_LABELS["dimension"]
        ce = C._norm(pr.ce_ref)
        ce_name = resolved_props.get(ce) if ce else None
        if ce_name:
            return C.KIND_LABELS["feature"]
        return C.KIND_LABELS["dimension"]

    def prop_label_for(pr, resolved_props):
        """Property-column text. Dimension/instance rules show the actual
        resolved D1@Sketch1-style name; the four component-level rules
        (file_name/relative_path/tag/loop_control) have no per-entity name
        to show — that classification lives in the Type column instead, so
        showing it again here would just duplicate the Type cell."""
        if pr.kind in ("dimension", "instance"):
            return C.property_label(pr, resolved_props)
        return "—"

    rows = []
    for rid in sorted(added_rids):
        pr = new_by_rid[rid]
        if pr.formula:
            stats['added'] += 1
            rows.append(('added', crumb(pr, new_idx, new_resolved), rule_type_for(pr, new_prop_resolved, new_prop_types),
                        prop_label_for(pr, new_prop_resolved), '', pr.formula))
    for rid in sorted(removed_rids):
        pr = old_by_rid[rid]
        if pr.formula:
            stats['removed'] += 1
            rows.append(('removed', crumb(pr, old_idx, old_resolved), rule_type_for(pr, old_prop_resolved, old_prop_types),
                        prop_label_for(pr, old_prop_resolved), pr.formula, ''))
    for rid in sorted(modified_rids):
        op, npr = old_by_rid[rid], new_by_rid[rid]
        stats['modified'] += 1
        rows.append(('modified', crumb(npr, new_idx, new_resolved), rule_type_for(npr, new_prop_resolved, new_prop_types),
                    prop_label_for(npr, new_prop_resolved), op.formula, npr.formula))

    if not rows:
        html = f'<p class="empty">No rule content changes found ({total_compared} driven properties compared).</p>'
        return html, stats

    body_rows = []
    for status, (crumb_short, crumb_full), rule_type, prop_label, of, nf in rows:
        badge = f'<span class="badge badge-{status}">{status.title()}</span>'
        old_cell = escape(of) if of else '<span class="attr-note">(blank)</span>'
        new_cell = inline_diff(of, nf) if (of and nf) else (escape(nf) if nf else '<span class="attr-note">(blank)</span>')
        title_attr = f' title="{escape(crumb_full, quote=True)}"' if crumb_full else ''
        body_rows.append(f'<tr class="{status}"><td{title_attr}>{escape(crumb_short)}</td><td>{escape(rule_type)}</td>'
                         f'<td>{escape(prop_label)}</td><td>{badge}</td>'
                         f'<td class="formula">{old_cell}</td><td class="formula">{new_cell}</td></tr>')

    html = ('<table class="rule-changes"><thead><tr><th>Placement</th><th>Type</th><th>Property</th>'
            '<th>Status</th><th>Old formula</th><th>New formula</th></tr></thead>'
            f'<tbody>{"".join(body_rows)}</tbody></table>')
    return html, stats


def compare_documents(old: dict, new: dict) -> tuple[str, dict]:
    """Compare documents / triggered actions with a rule-level breakdown (and a
    type-change row) under each modified document."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    html_parts = []

    for name in sorted(added):
        d = new[name]
        html_parts.append(
            f'<h3 class="added">➕ {escape(name)} <span class="badge badge-added">Added</span> '
            f'<small>({escape(d["type"])}, {len(d["rules"])} rules)</small></h3>'
        )

    for name in sorted(removed):
        d = old[name]
        html_parts.append(
            f'<h3 class="removed">➖ {escape(name)} <span class="badge badge-removed">Removed</span> '
            f'<small>({escape(d["type"])}, {len(d["rules"])} rules)</small></h3>'
        )

    for name in sorted(common):
        od, nd = old[name], new[name]
        rows = []
        if od['type'] != nd['type']:
            rows.append(
                '<tr class="modified"><td class="grouper">(type)</td>'
                '<td><span class="badge badge-modified">Modified</span></td>'
                f'<td class="formula">{inline_diff(od["type"], nd["type"])}</td></tr>'
            )
        for rid, status, of, nf in _diff_rule_dicts(od['rules'], nd['rules']):
            badge = f'<span class="badge badge-{status}">{status.title()}</span>'
            cell = inline_diff(of, nf) if status == 'modified' else escape(nf or of)
            rows.append(
                f'<tr class="{status}"><td class="grouper">{escape(rid)}</td>'
                f'<td>{badge}</td><td class="formula">{cell}</td></tr>'
            )
        if rows:
            stats['modified'] += 1
            html_parts.append(
                f'<h3 class="modified">📄 {escape(name)} <span class="badge badge-modified">Modified</span> '
                f'<small>({escape(nd["type"])})</small></h3>'
                '<table><thead><tr><th>Rule</th><th>Status</th><th>Formula</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table>'
            )
        else:
            stats['unchanged'] += 1

    body = ''.join(html_parts) if html_parts else '<p class="empty">No documents found</p>'
    return body, stats


def _parse_csv_table(body: str) -> tuple:
    """Parse a CSV string into (headers, rows). Rows are lists of strings."""
    if not body or not body.strip():
        return [], []
    try:
        all_rows = list(csv.reader(io.StringIO(body)))
    except Exception:
        return [], []
    if not all_rows:
        return [], []
    return all_rows[0], all_rows[1:]


def _row_at(row: list, idx: int) -> str:
    """Safe cell access, padding short rows with empty strings."""
    if idx is None or idx < 0 or idx >= len(row):
        return ''
    return row[idx]


def _lookup_diff(old_body: str, new_body: str) -> tuple:
    """Column matching + keyed row diff between two lookup-table CSV bodies.
    Returns (display_cols, diff_rows, duplicate_keys) where display_cols is
    [(header, status, old_idx, new_idx)] and diff_rows is
    [(status, old_row_or_None, new_row_or_None)]. Shared by the HTML grid
    renderer and the JSON diff (jsondiff.py) so both agree on what changed."""
    old_headers, old_rows = _parse_csv_table(old_body)
    new_headers, new_rows = _parse_csv_table(new_body)

    # Build the display columns positionally so duplicate header names are not
    # collapsed (a name->index map would keep only the last index for a repeated
    # header and silently diff every duplicate against the same source column).
    # New columns come first (in order), then any old-only columns. Old columns
    # match new ones by name, consuming matches left to right so repeated names
    # pair up by position. Each entry carries its own source indices, so the body
    # never re-looks-up a column by name.
    old_positions = {}
    for i, h in enumerate(old_headers):
        old_positions.setdefault(h, []).append(i)
    consumed = {}
    matched_old = set()
    display_cols = []  # (header, status, old_idx, new_idx)
    for j, h in enumerate(new_headers):
        avail = old_positions.get(h, [])
        k = consumed.get(h, 0)
        if k < len(avail):
            oi = avail[k]
            consumed[h] = k + 1
            matched_old.add(oi)
            display_cols.append((h, 'common', oi, j))
        else:
            display_cols.append((h, 'added', None, j))
    for i, h in enumerate(old_headers):
        if i not in matched_old:
            display_cols.append((h, 'removed', i, None))

    # Row keys = first column. If duplicates exist, fall back to row index.
    old_keys = [r[0] if r else '' for r in old_rows]
    new_keys = [r[0] if r else '' for r in new_rows]
    duplicate_keys = (
        len(set(old_keys)) != len(old_keys) or
        len(set(new_keys)) != len(new_keys)
    )

    # Build the diff_rows list. Each entry is (status, old_row_or_None, new_row_or_None).
    diff_rows = []
    if duplicate_keys:
        # Pair by index. Beyond either length, the missing side is None.
        n = max(len(old_rows), len(new_rows))
        for i in range(n):
            o = old_rows[i] if i < len(old_rows) else None
            n_row = new_rows[i] if i < len(new_rows) else None
            if o is None:
                diff_rows.append(('added', None, n_row))
            elif n_row is None:
                diff_rows.append(('removed', o, None))
            elif o == n_row:
                diff_rows.append(('unchanged', o, n_row))
            else:
                diff_rows.append(('modified', o, n_row))
    else:
        old_by_key = dict(zip(old_keys, old_rows))
        new_by_key = dict(zip(new_keys, new_rows))
        for key, new_row in zip(new_keys, new_rows):
            if key in old_by_key:
                old_row = old_by_key[key]
                status = 'unchanged' if old_row == new_row else 'modified'
                diff_rows.append((status, old_row, new_row))
            else:
                diff_rows.append(('added', None, new_row))
        for key, old_row in zip(old_keys, old_rows):
            if key not in new_by_key:
                diff_rows.append(('removed', old_row, None))

    return display_cols, diff_rows, duplicate_keys


def _render_lookup_grid(name: str, top_status: str, old_body: str, new_body: str) -> str:
    """Render one lookup table as a cell-highlighted grid. top_status is the
    table-level status ('added' / 'removed' / 'modified') used to color the
    h3 header. The diff between old_body and new_body decides per-cell
    coloring inside the grid."""
    display_cols, diff_rows, duplicate_keys = _lookup_diff(old_body, new_body)

    counts = {'added': 0, 'removed': 0, 'modified': 0, 'unchanged': 0}
    for status, *_ in diff_rows:
        counts[status] += 1

    # Header row with per-column status badges
    header_cells = []
    for h, s, _oi, _nj in display_cols:
        suffix = ''
        if s == 'added':
            suffix = ' <span class="badge badge-added">New</span>'
        elif s == 'removed':
            suffix = ' <span class="badge badge-removed">Old</span>'
        cls = f' class="col-{s}"' if s != 'common' else ''
        header_cells.append(f'<th{cls}>{escape(h)}{suffix}</th>')

    # Body rows
    body_rows = []
    for status, old_row, new_row in diff_rows:
        cells = []
        for h, col_status, old_idx, new_idx in display_cols:
            old_val = _row_at(old_row, old_idx) if old_row is not None else ''
            new_val = _row_at(new_row, new_idx) if new_row is not None else ''

            if col_status == 'added':
                # Column only exists in new. Show new value (blank for removed rows).
                cells.append(f'<td class="cell-added">{escape(new_val)}</td>')
            elif col_status == 'removed':
                # Column only exists in old. Show old value (blank for added rows).
                cells.append(f'<td class="cell-removed">{escape(old_val)}</td>')
            else:
                # Common column. Compare cells.
                if status == 'added':
                    cells.append(f'<td>{escape(new_val)}</td>')
                elif status == 'removed':
                    cells.append(f'<td>{escape(old_val)}</td>')
                elif old_val != new_val:
                    cells.append(f'<td class="cell-changed">{inline_diff(old_val, new_val)}</td>')
                else:
                    cells.append(f'<td>{escape(new_val)}</td>')
        body_rows.append(f'<tr class="{status}">{"".join(cells)}</tr>')

    # Header h3 badges show change counts
    sub_badges = ''
    if counts['added']: sub_badges += f' <span class="badge badge-added">+{counts["added"]}</span>'
    if counts['removed']: sub_badges += f' <span class="badge badge-removed">-{counts["removed"]}</span>'
    if counts['modified']: sub_badges += f' <span class="badge badge-modified">~{counts["modified"]}</span>'

    if top_status == 'added':
        icon = '➕'
        label = '<span class="badge badge-added">Added</span>'
    elif top_status == 'removed':
        icon = '➖'
        label = '<span class="badge badge-removed">Removed</span>'
    else:
        icon = '📋'
        label = '<span class="badge badge-modified">Modified</span>'

    dimension_note = (
        f'<small>{len(diff_rows)} rows × {len(display_cols)} cols'
        + (', keyed by row index (duplicate first-column values)' if duplicate_keys else '')
        + '</small>'
    )

    return (
        f'<h3 class="{top_status}">{icon} {escape(name)} {label}{sub_badges} {dimension_note}</h3>'
        f'<table class="lookup-grid"><thead><tr>{"".join(header_cells)}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
    )


def compare_lookup_tables(old: dict, new: dict) -> tuple[str, dict]:
    """Compare lookup tables, rendering each modified table as a cell-
    highlighted grid keyed by the first column. Unchanged rows are emitted
    with class="unchanged" so the global lookup-row toggle can hide them."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    html_parts = []

    for name in sorted(added):
        html_parts.append(_render_lookup_grid(name, 'added', '', new[name]))

    for name in sorted(removed):
        html_parts.append(_render_lookup_grid(name, 'removed', old[name], ''))

    for name in sorted(common):
        if old[name] == new[name]:
            stats['unchanged'] += 1
            continue
        stats['modified'] += 1
        html_parts.append(_render_lookup_grid(name, 'modified', old[name], new[name]))

    body = ''.join(html_parts) if html_parts else '<p class="empty">No lookup tables found</p>'
    return body, stats


def compare_data_tables(old: dict, new: dict) -> tuple[str, dict]:
    """Compare data table definitions (name + type, row data lives elsewhere)."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    rows = []

    for name in sorted(added):
        d = new[name]
        rows.append(
            f'<tr class="added"><td>{escape(name)}</td>'
            f'<td><span class="badge badge-added">Added</span></td>'
            f'<td>{escape(d.table_type)}</td></tr>'
        )

    for name in sorted(removed):
        d = old[name]
        rows.append(
            f'<tr class="removed"><td>{escape(name)}</td>'
            f'<td><span class="badge badge-removed">Removed</span></td>'
            f'<td>{escape(d.table_type)}</td></tr>'
        )

    for name in sorted(common):
        old_d, new_d = old[name], new[name]
        if old_d.table_type != new_d.table_type:
            stats['modified'] += 1
            rows.append(
                f'<tr class="modified"><td>{escape(name)}</td>'
                f'<td><span class="badge badge-modified">Modified</span></td>'
                f'<td class="formula">{inline_diff(old_d.table_type, new_d.table_type)}</td></tr>'
            )
        else:
            stats['unchanged'] += 1
            rows.append(
                f'<tr class="unchanged"><td>{escape(name)}</td><td>·</td>'
                f'<td>{escape(new_d.table_type)}</td></tr>'
            )

    body = ''.join(rows) if rows else '<tr><td colspan="3" class="empty">No data tables found</td></tr>'
    html = (
        '<table><thead><tr><th>Data Table</th><th>Status</th><th>Type</th></tr></thead>'
        f'<tbody>{body}</tbody></table>'
    )
    return html, stats


def _fmt_nav_target(s) -> str:
    """Render a nav step's resolved Next/Previous wiring as a compact string.
    Module-level (not nested in compare_nav_steps) because the JSON diff
    (jsondiff.py) uses the same rendering to decide what counts as a change."""
    bits = []
    if s.next_step_value:
        bits.append(f'next={s.next_step_value}')
    if s.next_step_rule and s.next_step_rule != f'"{s.next_step_value}"':
        bits.append(f'nextRule={s.next_step_rule}')
    if s.next_macro_value:
        bits.append(f'nextMacro={s.next_macro_value}')
    if s.previous_macro_value:
        bits.append(f'prevMacro={s.previous_macro_value}')
    return ', '.join(bits)


def compare_nav_steps(old: dict, new: dict) -> tuple[str, dict]:
    """Compare navigation steps (the form flow graph)."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    rows = []

    fmt_target = _fmt_nav_target

    for name in sorted(added):
        s = new[name]
        rows.append(
            f'<tr class="added"><td>{escape(name)}</td><td>{escape(s.step_type)}</td>'
            f'<td><span class="badge badge-added">Added</span></td>'
            f'<td class="formula">{escape(fmt_target(s))}</td></tr>'
        )

    for name in sorted(removed):
        s = old[name]
        rows.append(
            f'<tr class="removed"><td>{escape(name)}</td><td>{escape(s.step_type)}</td>'
            f'<td><span class="badge badge-removed">Removed</span></td>'
            f'<td class="formula">{escape(fmt_target(s))}</td></tr>'
        )

    for name in sorted(common):
        o, n = old[name], new[name]
        o_target, n_target = fmt_target(o), fmt_target(n)
        type_changed = o.step_type != n.step_type
        if o_target != n_target or type_changed:
            stats['modified'] += 1
            diff = inline_diff(o_target, n_target)
            type_cell = escape(n.step_type) if not type_changed else inline_diff(o.step_type, n.step_type)
            rows.append(
                f'<tr class="modified"><td>{escape(name)}</td><td>{type_cell}</td>'
                f'<td><span class="badge badge-modified">Modified</span></td>'
                f'<td class="formula">{diff}</td></tr>'
            )
        else:
            stats['unchanged'] += 1
            rows.append(
                f'<tr class="unchanged"><td>{escape(name)}</td><td>{escape(n.step_type)}</td>'
                f'<td>·</td><td class="formula">{escape(fmt_target(n))}</td></tr>'
            )

    body = ''.join(rows) if rows else '<tr><td colspan="4" class="empty">No navigation steps found</td></tr>'
    html = (
        '<table><thead><tr><th>Step</th><th>Type</th><th>Status</th><th>Wiring</th></tr></thead>'
        f'<tbody>{body}</tbody></table>'
    )
    return html, stats


def _dedupe_labels(labels: list) -> list:
    """Make repeated task labels unique by appending an occurrence index, so a
    macro with two same-named tasks (e.g. two 'Create Folder' tasks) does not
    collapse to one during matching. Deterministic by source order, so the same
    task lines up across old/new."""
    seen = {}
    out = []
    for lbl in labels:
        seen[lbl] = seen.get(lbl, 0) + 1
        out.append(lbl if seen[lbl] == 1 else f'{lbl} #{seen[lbl]}')
    return out


def compare_spec_macros(old: dict, new: dict) -> tuple[str, dict]:
    """Compare Specification Macros at the macro level, with task-level
    add/remove/modify rows under each modified macro."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    html_parts = []

    def task_label(t):
        return f'{t.title or "(untitled)"} [{t.task_type or "?"}]'

    for name in sorted(added):
        m = new[name]
        html_parts.append(
            f'<h3 class="added">➕ {escape(name)} <span class="badge badge-added">Added</span> '
            f'<small>({len(m.tasks)} tasks)</small></h3>'
        )

    for name in sorted(removed):
        m = old[name]
        html_parts.append(
            f'<h3 class="removed">➖ {escape(name)} <span class="badge badge-removed">Removed</span> '
            f'<small>({len(m.tasks)} tasks)</small></h3>'
        )

    for name in sorted(common):
        o, n = old[name], new[name]
        # Task identity is title + task_type. Position matters too (order),
        # but we surface order by listing tasks in source order with positions.
        old_keys = _dedupe_labels([task_label(t) for t in o.tasks])
        new_keys = _dedupe_labels([task_label(t) for t in n.tasks])

        old_by_key = {k: t for k, t in zip(old_keys, o.tasks)}
        new_by_key = {k: t for k, t in zip(new_keys, n.tasks)}

        all_keys = list(dict.fromkeys(old_keys + new_keys))  # preserve first-seen order
        row_html = []
        macro_modified = False

        for key in all_keys:
            ot = old_by_key.get(key)
            nt = new_by_key.get(key)
            if ot is None:
                macro_modified = True
                row_html.append(_macro_task_rows(key, 'added', None, nt))
            elif nt is None:
                macro_modified = True
                row_html.append(_macro_task_rows(key, 'removed', ot, None))
            else:
                prop_changes = _diff_props(ot.properties, nt.properties)
                if prop_changes:
                    macro_modified = True
                    row_html.append(_macro_task_rows(key, 'modified', ot, nt, prop_changes))

        if old_keys != new_keys and not macro_modified:
            # Task set identical but reordered.
            macro_modified = True
            row_html.append(
                '<tr class="modified"><td colspan="3"><em>Tasks reordered</em></td>'
                f'<td class="formula">old order: {escape(", ".join(old_keys))}<br>'
                f'new order: {escape(", ".join(new_keys))}</td></tr>'
            )

        if macro_modified:
            stats['modified'] += 1
            html_parts.append(
                f'<h3 class="modified">⚙️ {escape(name)} <span class="badge badge-modified">Modified</span></h3>'
                '<table><thead><tr><th>Task</th><th>Status</th><th>Property</th><th>Formula</th></tr></thead>'
                f'<tbody>{"".join(row_html)}</tbody></table>'
            )
        else:
            stats['unchanged'] += 1

    body = ''.join(html_parts) if html_parts else '<p class="empty">No specification macros found</p>'
    return body, stats


def _diff_props(old_props: dict, new_props: dict) -> list:
    """Return list of (prop_name, status, old_val, new_val) tuples for prop
    keys that differ between the two property dicts."""
    out = []
    all_keys = sorted(set(old_props) | set(new_props))
    for k in all_keys:
        o = old_props.get(k, '')
        n = new_props.get(k, '')
        if o == n:
            continue
        if k not in old_props:
            out.append((k, 'added', '', n))
        elif k not in new_props:
            out.append((k, 'removed', o, ''))
        else:
            out.append((k, 'modified', o, n))
    return out


def _fmt_prop(p):
    """Format a (is_static, value) tuple for display."""
    if p is None:
        return ''
    _is_static, val = p
    return val or ''


def _compare_prop_dicts(old_props: dict, new_props: dict) -> list:
    """Compare two property dicts whose values are (is_static, formula_or_value)
    tuples. Returns list of (prop_name, status, old_tuple, new_tuple) for
    properties whose tuples differ. Empty-vs-empty pairs are skipped, even
    when is_static differs, to keep the report focused on real changes."""
    out = []
    for k in sorted(set(old_props) | set(new_props)):
        o = old_props.get(k)
        n = new_props.get(k)
        if o == n:
            continue
        o_val = _fmt_prop(o)
        n_val = _fmt_prop(n)
        if not o_val and not n_val:
            # Both effectively empty (likely a static-flag-only change). Skip
            # to keep the diff readable on real-world projects.
            continue
        if o_val == n_val:
            # Same rendered value (only the IsStatic flag toggled). The report
            # shows just the value, so this would be a no-op "modified" row.
            continue
        if k not in old_props or o_val == '':
            out.append((k, 'added', o, n))
        elif k not in new_props or n_val == '':
            out.append((k, 'removed', o, n))
        else:
            out.append((k, 'modified', o, n))
    return out


def _form_change_row(control: str, ctrl_type: str, prop: str, status: str,
                     old_p, new_p, first_in_group: bool = True) -> str:
    """Render one property change row. Only the first row in a control's group
    shows the control name and type; later rows blank those cells so the
    repeated identity does not visually drown out the per-property changes."""
    badge = f'<span class="badge badge-{status}">{status.title()}</span>'
    old_v = _fmt_prop(old_p)
    new_v = _fmt_prop(new_p)
    if status == 'modified':
        cell = inline_diff(old_v, new_v)
    else:
        cell = escape(new_v or old_v)
    classes = status + (' group-start' if first_in_group else '')
    name_cell = escape(control) if first_in_group else ''
    type_cell = escape(ctrl_type) if first_in_group else ''
    return (
        f'<tr class="{classes}">'
        f'<td class="grouper">{name_cell}</td>'
        f'<td class="grouper">{type_cell}</td>'
        f'<td>{escape(prop)}</td>'
        f'<td>{badge}</td>'
        f'<td class="formula">{cell}</td>'
        '</tr>'
    )


def compare_forms(old: dict, new: dict) -> tuple[str, dict]:
    """Compare Forms and their controls. Each modified form gets a table of
    property-level change rows covering both form-level rules and per-control
    properties."""
    added, removed, common = compare_dicts(old, new)
    stats = {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}
    html_parts = []

    for name in sorted(added):
        f = new[name]
        html_parts.append(
            f'<h3 class="added">➕ {escape(name)} <span class="badge badge-added">Added</span> '
            f'<small>({len(f.controls)} controls)</small></h3>'
        )

    for name in sorted(removed):
        f = old[name]
        html_parts.append(
            f'<h3 class="removed">➖ {escape(name)} <span class="badge badge-removed">Removed</span> '
            f'<small>({len(f.controls)} controls)</small></h3>'
        )

    for name in sorted(common):
        of, nf = old[name], new[name]
        change_rows = []

        # Form-level property changes form a single group keyed on "(form)".
        form_prop_changes = _compare_prop_dicts(of.form_props, nf.form_props)
        for i, (prop, status, op, np) in enumerate(form_prop_changes):
            change_rows.append(_form_change_row(
                '(form-level rules)', 'Form', prop, status, op, np,
                first_in_group=(i == 0),
            ))

        c_added, c_removed, c_common = compare_dicts(of.controls, nf.controls)

        for ctrl_name in sorted(c_added):
            c = nf.controls[ctrl_name]
            badge = '<span class="badge badge-added">Added</span>'
            change_rows.append(
                f'<tr class="added group-start">'
                f'<td class="grouper">{escape(ctrl_name)}</td>'
                f'<td class="grouper">{escape(c.control_type)}</td>'
                f'<td colspan="2">{badge}</td>'
                f'<td>{len(c.props)} properties</td></tr>'
            )

        for ctrl_name in sorted(c_removed):
            c = of.controls[ctrl_name]
            badge = '<span class="badge badge-removed">Removed</span>'
            change_rows.append(
                f'<tr class="removed group-start">'
                f'<td class="grouper">{escape(ctrl_name)}</td>'
                f'<td class="grouper">{escape(c.control_type)}</td>'
                f'<td colspan="2">{badge}</td>'
                f'<td>{len(c.props)} properties</td></tr>'
            )

        for ctrl_name in sorted(c_common):
            o_ctrl = of.controls[ctrl_name]
            n_ctrl = nf.controls[ctrl_name]
            type_changed = o_ctrl.control_type != n_ctrl.control_type
            prop_changes = _compare_prop_dicts(o_ctrl.props, n_ctrl.props)
            if not type_changed and not prop_changes:
                continue

            first_done = False

            if type_changed:
                # First row of the group is the type-swap notice; later prop
                # rows are blank in the name/type cells.
                badge = '<span class="badge badge-modified">Type changed</span>'
                change_rows.append(
                    f'<tr class="modified group-start">'
                    f'<td class="grouper">{escape(ctrl_name)}</td>'
                    f'<td class="grouper formula">{inline_diff(o_ctrl.control_type, n_ctrl.control_type)}</td>'
                    f'<td>(control type)</td>'
                    f'<td>{badge}</td>'
                    f'<td></td></tr>'
                )
                first_done = True

            for prop, status, op, np in prop_changes:
                change_rows.append(_form_change_row(
                    ctrl_name, n_ctrl.control_type, prop, status, op, np,
                    first_in_group=not first_done,
                ))
                first_done = True

        if change_rows:
            stats['modified'] += 1
            html_parts.append(
                f'<h3 class="modified">📝 {escape(name)} <span class="badge badge-modified">Modified</span></h3>'
                '<table class="grouped"><thead><tr><th>Control</th><th>Type</th><th>Property</th>'
                '<th>Status</th><th>Formula / Value</th></tr></thead>'
                f'<tbody>{"".join(change_rows)}</tbody></table>'
            )
        else:
            stats['unchanged'] += 1

    body = ''.join(html_parts) if html_parts else '<p class="empty">No forms found</p>'
    return body, stats


def _macro_task_rows(task_key: str, status: str, old_task, new_task, prop_changes=None) -> str:
    """Render one or more <tr> rows for a single macro task."""
    badge = f'<span class="badge badge-{status}">{status.title()}</span>'
    if status == 'added' or status == 'removed':
        t = new_task if status == 'added' else old_task
        n_props = len(t.properties)
        return (
            f'<tr class="{status} group-start"><td class="grouper">{escape(task_key)}</td>'
            f'<td>{badge}</td><td colspan="2">{n_props} properties</td></tr>'
        )
    # Modified case, emit one row per changed property. First row carries the
    # task name and a group-start marker so the table groups visually.
    rows = []
    first = True
    for prop_name, p_status, old_val, new_val in prop_changes or []:
        p_badge = f'<span class="badge badge-{p_status}">{p_status.title()}</span>'
        if p_status == 'modified':
            cell = inline_diff(old_val, new_val)
        else:
            cell = escape(new_val or old_val)
        task_cell = escape(task_key) if first else ''
        status_cell = badge if first else ''
        cls = f'{p_status}{" group-start" if first else ""}'
        rows.append(
            f'<tr class="{cls}"><td class="grouper">{task_cell}</td><td>{status_cell}</td>'
            f'<td>{escape(prop_name)} {p_badge}</td><td class="formula">{cell}</td></tr>'
        )
        first = False
    return ''.join(rows)
