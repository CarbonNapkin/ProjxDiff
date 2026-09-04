"""End-to-end tests for the rules-metrics backfill (dw_compare/backfill.py).

The scenario the backfill exists for: nights synced by a build whose jsondiff
had no rules category left the metrics database blind to rule changes, while
the archive repo recorded every night's full state. Each test drives the real
sync (real zips, real git repo, real SQLite), then simulates the legacy hole
by deleting the rules rows the fixed sync wrote, and asserts the backfill
reconstructs them from the archive — identically.
"""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from dw_compare import backfill
from dw_compare.sync import load_config, run as sync_run


def _write_projx(path: Path, variables: dict, rule_formulas: dict):
    """A minimal .driveprojx with driven-property rules: rule_formulas maps
    rule_id -> formula, parsed by components.parse_property_rules."""
    var_rows = ''.join(f'<Variable DisplayName="{n}" StoreName="{n}" Rule="{v}"/>'
                       for n, v in variables.items())
    pps = ''.join(f'<PP CPRef="cp-{rid}" RId="{rid}"><R>{formula}</R></PP>'
                  for rid, formula in rule_formulas.items())
    comp = ('<Components><PC TrId="aaaa1111" CCRef="cccc2222">'
            f'<PE CERef="eeee3333">{pps}</PE></PC></Components>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('driveProj/project.xml',
                    f'<Project><Variables>{var_rows}</Variables></Project>')
        zf.writestr('driveProj/components/1.xml', comp)


@pytest.fixture
def site(tmp_path):
    source = tmp_path / 'share'
    _write_projx(source / 'Alpha.driveprojx', {'Width': '=800'},
                 {'aaa1': '=Width', 'aaa2': '=Width*2'})
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(source),
        'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'),
        'owners': {'Alpha': 'Jane Smith <jane@example.com>'},
    }), encoding='utf-8')
    return {'tmp': tmp_path, 'source': source, 'cfg_path': cfg_path,
            'data': tmp_path / 'data'}


def _rules_rows(site, table='category_changes'):
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        cols = ('run_date, project, owner, category, added, removed, modified,'
                ' unchanged, source' if table == 'category_changes' else
                'run_date, project, owner, category, element, status, source')
        return db.execute(f"SELECT {cols} FROM {table} WHERE category='rules' "
                          'ORDER BY id').fetchall()


def _delete_rules_rows(site):
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        for table in ('category_changes', 'element_changes'):
            db.execute(f"DELETE FROM {table} WHERE category='rules'")


def _synced_night_with_rule_changes(site):
    """First run baselines; a rules-only edit plus second run records a diff
    commit and (with the fixed jsondiff) live rules rows."""
    assert sync_run(load_config(site['cfg_path']), dry_run=False) == 0
    _write_projx(site['source'] / 'Alpha.driveprojx', {'Width': '=800'},
                 {'aaa1': '=Width+1', 'aaa3': '=Height'})  # ~1, +1, -1
    assert sync_run(load_config(site['cfg_path']), dry_run=False) == 0


def test_backfill_reconstructs_deleted_rules_rows_exactly(site):
    _synced_night_with_rule_changes(site)
    live_cat = _rules_rows(site)
    live_elem = _rules_rows(site, 'element_changes')
    assert live_cat, 'the fixed sync should have recorded rules rows live'
    assert live_cat[0][4:7] == (1, 1, 1)   # +1 added, -1 removed, ~1 modified

    _delete_rules_rows(site)               # simulate a pre-fix metrics DB
    assert _rules_rows(site) == []

    assert backfill.run(load_config(site['cfg_path']), dry_run=False) == 0
    assert _rules_rows(site) == live_cat
    assert _rules_rows(site, 'element_changes') == live_elem


def test_backfill_is_idempotent(site):
    _synced_night_with_rule_changes(site)
    assert backfill.run(load_config(site['cfg_path']), dry_run=False) == 0
    once_cat = _rules_rows(site)
    once_elem = _rules_rows(site, 'element_changes')
    assert backfill.run(load_config(site['cfg_path']), dry_run=False) == 0
    assert _rules_rows(site) == once_cat
    assert _rules_rows(site, 'element_changes') == once_elem


def test_backfill_dry_run_writes_nothing(site):
    _synced_night_with_rule_changes(site)
    _delete_rules_rows(site)
    assert backfill.run(load_config(site['cfg_path']), dry_run=True) == 0
    assert _rules_rows(site) == []
    assert _rules_rows(site, 'element_changes') == []


def test_backfill_honours_the_sync_lock(site):
    _synced_night_with_rule_changes(site)
    _delete_rules_rows(site)
    lock = site['data'] / 'sync.lock'
    lock.write_text('another run', encoding='utf-8')
    assert backfill.run(load_config(site['cfg_path']), dry_run=False) == 3
    assert _rules_rows(site) == []
    assert lock.read_text(encoding='utf-8') == 'another run'  # never removed
    # A dry run neither takes nor honours it — mirrors the sync's semantics.
    assert backfill.run(load_config(site['cfg_path']), dry_run=True) == 0
    lock.unlink()
    assert backfill.run(load_config(site['cfg_path']), dry_run=False) == 0
    assert _rules_rows(site) != []
    assert not lock.exists()   # released on the way out


def test_backfill_skips_the_baseline_commit(site):
    # Only the first run has happened: one "added to archive" commit, no diff
    # commits. Replaying nothing must be a clean no-op, not an error.
    assert sync_run(load_config(site['cfg_path']), dry_run=False) == 0
    assert backfill.run(load_config(site['cfg_path']), dry_run=False) == 0
    assert _rules_rows(site) == []
