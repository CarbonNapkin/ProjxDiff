"""
Structured (JSON) diff between two DriveWorks projects.

The HTML comparers in comparers.py entangle change *detection* with change
*rendering*, so this module re-walks the parsed projects and emits plain-data
change records instead. It reuses the same helpers the HTML path uses
(_diff_rule_dicts, _compare_prop_dicts, _calc_table_changes, _lookup_diff,
_fmt_nav_target, ...) so both outputs agree on what counts as a change, and
tests/test_jsondiff.py locks the two to identical per-category counts.

Document shape (versioned via the top-level "schema" key):

{
  "schema": 1,
  "generator": "Projx Diff",
  "generator_version": "1.0.7",
  "generated_at": "2026-08-03T21:14:03-04:00",
  "old_project": "widgets_v1",
  "new_project": "widgets_v2",
  "summary": {
    "added": 1, "removed": 0, "modified": 2, "unchanged": 40,
    "categories": {"variables": {"added": 1, "removed": 0, ...}, ...}
  },
  "changes": [
    {"category": "variables", "name": "Width", "status": "modified",
     "details": [{"field": "formula", "status": "modified",
                  "old": "=800", "new": "=700"}]}
  ],
  "errors": []
}

The 'rules' category (driven-property rules — the HTML report's "Rule
Changes" section) joined in 1.10.0; the schema stays 1 because the change
is additive. Documents produced by earlier builds simply lack the key, which
is "not measured", not "no rule changes".

Only changed elements appear in "changes"; unchanged elements are counted in
the summary only. "details" names the changed fields / rules / properties with
raw (unescaped) old/new values; empty old/new strings are omitted. "errors"
lists categories whose comparison crashed (mirroring the HTML report's
degrade-per-section behavior) so a consumer can tell missing data from
no-changes.
"""

import traceback
from datetime import datetime

from ._version import __version__
from .models import DWProject
from .comparers import (
    compare_dicts,
    _diff_rule_dicts,
    _diff_props,
    _compare_prop_dicts,
    _calc_table_changes,
    _lookup_diff,
    _fmt_nav_target,
    _fmt_prop,
    _dedupe_labels,
)


def _record(name: str, status: str, details: list = None) -> dict:
    rec = {'name': name, 'status': status}
    if details:
        rec['details'] = details
    return rec


def _field(field: str, status: str, old: str = '', new: str = '') -> dict:
    d = {'field': field, 'status': status}
    if old:
        d['old'] = old
    if new:
        d['new'] = new
    return d


def _new_stats(added, removed) -> dict:
    return {'added': len(added), 'removed': len(removed), 'modified': 0, 'unchanged': 0}


def _attr_details(o, n, fields) -> list:
    """Detail entries for the named attributes that differ between two model
    objects. Matches the HTML comparers' treatment of None-vs-'' (equal)."""
    details = []
    for f in fields:
        ov = getattr(o, f) or ''
        nv = getattr(n, f) or ''
        if ov != nv:
            details.append(_field(f, 'modified', ov, nv))
    return details


def _diff_variables(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        details = _attr_details(old[name], new[name],
                                ('formula', 'category', 'store_name', 'comment'))
        if details:
            stats['modified'] += 1
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_constants(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        details = _attr_details(old[name], new[name], ('value', 'store_name', 'comment'))
        if details:
            stats['modified'] += 1
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_calc_tables(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        changes = _calc_table_changes(old[name], new[name])
        if changes:
            stats['modified'] += 1
            details = [_field(col if not scope else f'{col} · {scope}', status, ov, nv)
                       for col, scope, status, ov, nv in changes]
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_component_tasks(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(new[k].name or k, 'added') for k in sorted(added)]
    records += [_record(old[k].name or k, 'removed') for k in sorted(removed)]
    for key in sorted(common):
        rule_changes = _diff_rule_dicts(old[key].rules, new[key].rules)
        if rule_changes:
            stats['modified'] += 1
            details = [_field(rid, status, of, nf) for rid, status, of, nf in rule_changes]
            records.append(_record(new[key].name or key, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_documents(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        od, nd = old[name], new[name]
        details = []
        if od['type'] != nd['type']:
            details.append(_field('(type)', 'modified', od['type'], nd['type']))
        for rid, status, of, nf in _diff_rule_dicts(od['rules'], nd['rules']):
            details.append(_field(rid, status, of, nf))
        if details:
            stats['modified'] += 1
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_lookup_tables(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        if old[name] == new[name]:
            stats['unchanged'] += 1
            continue
        stats['modified'] += 1
        display_cols, diff_rows, duplicate_keys = _lookup_diff(old[name], new[name])
        details = []
        for header, col_status, _oi, _nj in display_cols:
            if col_status in ('added', 'removed'):
                details.append(_field(f'(column) {header}', col_status))
        for i, (status, old_row, new_row) in enumerate(diff_rows):
            if status == 'unchanged':
                continue
            if duplicate_keys:
                label = f'(row {i})'
            else:
                row = new_row if new_row is not None else old_row
                label = f'(row) {row[0] if row else ""}'
            details.append(_field(label, status))
        records.append(_record(name, 'modified', details))
    return records, stats


def _diff_data_tables(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        if old[name].table_type != new[name].table_type:
            stats['modified'] += 1
            records.append(_record(name, 'modified',
                                   [_field('table_type', 'modified',
                                           old[name].table_type, new[name].table_type)]))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_nav_steps(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        o, n = old[name], new[name]
        details = []
        if o.step_type != n.step_type:
            details.append(_field('step_type', 'modified', o.step_type, n.step_type))
        o_target, n_target = _fmt_nav_target(o), _fmt_nav_target(n)
        if o_target != n_target:
            details.append(_field('wiring', 'modified', o_target, n_target))
        if details:
            stats['modified'] += 1
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _task_label(t) -> str:
    return f'{t.title or "(untitled)"} [{t.task_type or "?"}]'


def _diff_spec_macros(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        o, n = old[name], new[name]
        old_keys = _dedupe_labels([_task_label(t) for t in o.tasks])
        new_keys = _dedupe_labels([_task_label(t) for t in n.tasks])
        old_by_key = dict(zip(old_keys, o.tasks))
        new_by_key = dict(zip(new_keys, n.tasks))

        details = []
        for key in dict.fromkeys(old_keys + new_keys):  # preserve first-seen order
            ot = old_by_key.get(key)
            nt = new_by_key.get(key)
            if ot is None:
                details.append(_field(key, 'added'))
            elif nt is None:
                details.append(_field(key, 'removed'))
            else:
                for prop, status, ov, nv in _diff_props(ot.properties, nt.properties):
                    details.append(_field(f'{key} · {prop}', status, ov, nv))

        if not details and old_keys != new_keys:
            # Task set identical but reordered.
            details.append(_field('(task order)', 'modified',
                                  ', '.join(old_keys), ', '.join(new_keys)))

        if details:
            stats['modified'] += 1
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_forms(old: dict, new: dict) -> tuple:
    added, removed, common = compare_dicts(old, new)
    stats = _new_stats(added, removed)
    records = [_record(n, 'added') for n in sorted(added)]
    records += [_record(n, 'removed') for n in sorted(removed)]
    for name in sorted(common):
        of, nf = old[name], new[name]
        details = []

        for prop, status, op, np_ in _compare_prop_dicts(of.form_props, nf.form_props):
            details.append(_field(f'(form) · {prop}', status, _fmt_prop(op), _fmt_prop(np_)))

        c_added, c_removed, c_common = compare_dicts(of.controls, nf.controls)
        for ctrl in sorted(c_added):
            details.append(_field(ctrl, 'added'))
        for ctrl in sorted(c_removed):
            details.append(_field(ctrl, 'removed'))
        for ctrl in sorted(c_common):
            o_ctrl, n_ctrl = of.controls[ctrl], nf.controls[ctrl]
            if o_ctrl.control_type != n_ctrl.control_type:
                details.append(_field(f'{ctrl} · (control type)', 'modified',
                                      o_ctrl.control_type, n_ctrl.control_type))
            for prop, status, op, np_ in _compare_prop_dicts(o_ctrl.props, n_ctrl.props):
                details.append(_field(f'{ctrl} · {prop}', status, _fmt_prop(op), _fmt_prop(np_)))

        if details:
            stats['modified'] += 1
            records.append(_record(name, 'modified', details))
        else:
            stats['unchanged'] += 1
    return records, stats


def _diff_rules(old_idx, new_idx) -> tuple:
    """Driven-property rules — the HTML report's "Rule Changes" section.

    Detection mirrors comparers.compare_property_rules exactly (matched by
    rule_id; added/removed rows without a formula are unbound placeholders,
    not real rules, and stay out of the counts). Records use the raw rule_id
    as the element name — the JSON diff keeps raw ids by design; readable
    names are a report-time, database-backed concern — with the placement
    breadcrumb (raw TrIds when unresolved) and kind in the details."""
    from . import components as C

    old_by_rid = {C._norm(p.rule_id): p for p in old_idx.property_rules if p.rule_id}
    new_by_rid = {C._norm(p.rule_id): p for p in new_idx.property_rules if p.rule_id}

    added_rids = set(new_by_rid) - set(old_by_rid)
    removed_rids = set(old_by_rid) - set(new_by_rid)
    shared_rids = set(old_by_rid) & set(new_by_rid)
    modified_rids = {rid for rid in shared_rids
                     if old_by_rid[rid].formula != new_by_rid[rid].formula}

    total_compared = len(shared_rids) + len(added_rids) + len(removed_rids)
    stats = {'added': 0, 'removed': 0, 'modified': len(modified_rids),
             'unchanged': total_compared - len(modified_rids)}

    def details_for(pr, idx, status, old_formula, new_formula):
        return [_field('formula', status, old_formula, new_formula),
                _field('kind', status, '', pr.kind),
                _field('placement', status, '', idx.breadcrumb(pr.owner_path))]

    records = []
    for rid in sorted(added_rids):
        pr = new_by_rid[rid]
        if pr.formula:
            stats['added'] += 1
            records.append(_record(pr.rule_id, 'added',
                                   details_for(pr, new_idx, 'added', '', pr.formula)))
    for rid in sorted(removed_rids):
        pr = old_by_rid[rid]
        if pr.formula:
            stats['removed'] += 1
            records.append(_record(pr.rule_id, 'removed',
                                   details_for(pr, old_idx, 'removed', pr.formula, '')))
    for rid in sorted(modified_rids):
        op, npr = old_by_rid[rid], new_by_rid[rid]
        records.append(_record(npr.rule_id, 'modified',
                               details_for(npr, new_idx, 'modified',
                                           op.formula, npr.formula)))
    return records, stats


# Category key on DWProject -> differ. Order matches the HTML report's sections.
_CATEGORY_FUNCS = [
    ('variables', _diff_variables),
    ('constants', _diff_constants),
    ('calc_tables', _diff_calc_tables),
    ('rules', _diff_rules),
    ('component_tasks', _diff_component_tasks),
    ('documents', _diff_documents),
    ('lookup_tables', _diff_lookup_tables),
    ('data_tables', _diff_data_tables),
    ('spec_macros', _diff_spec_macros),
    ('nav_steps', _diff_nav_steps),
    ('forms', _diff_forms),
]

_EMPTY_STATS = {'added': 0, 'removed': 0, 'modified': 0, 'unchanged': 0}

# Most category keys ARE the DWProject attribute; 'rules' diffs the projects'
# ComponentIndex objects (driven-property rules live there, not in a dict).
_CATEGORY_ATTRS = {'rules': 'component_index'}


def build_diff(old_proj: DWProject, new_proj: DWProject,
               old_name: str = 'old', new_name: str = 'new') -> dict:
    """Build the structured diff document for two parsed projects."""
    changes = []
    cat_stats = {}
    errors = []

    for cat_key, fn in _CATEGORY_FUNCS:
        attr = _CATEGORY_ATTRS.get(cat_key, cat_key)
        try:
            records, stats = fn(getattr(old_proj, attr), getattr(new_proj, attr))
        except Exception:
            traceback.print_exc()
            errors.append(cat_key)
            records, stats = [], dict(_EMPTY_STATS)
        cat_stats[cat_key] = stats
        for rec in records:
            changes.append({'category': cat_key, **rec})

    summary = {k: sum(s[k] for s in cat_stats.values()) for k in _EMPTY_STATS}
    summary['categories'] = cat_stats

    return {
        'schema': 1,
        'generator': 'Projx Diff',
        'generator_version': __version__,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'old_project': old_name,
        'new_project': new_name,
        'summary': summary,
        'changes': changes,
        'errors': errors,
    }
