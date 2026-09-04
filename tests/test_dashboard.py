"""Tests for the static metrics dashboard generator."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'nightly_sync'))
import dashboard   # noqa: E402
import nightly_sync  # noqa: E402


def _seed_db(tmp_path):
    conn = nightly_sync.open_db(tmp_path / 'metrics.sqlite')
    conn.execute(
        'INSERT INTO runs (run_date, started_at, finished_at, projects_seen,'
        ' projects_changed, errors) VALUES (?,?,?,?,?,?)',
        ('2026-08-02', '2026-08-02T02:00:00', '2026-08-02T02:03:00', 12, 2, ''))
    rows = [
        ('2026-08-02', 'Model 630', 'Jane Smith <jane@example.com>', 'variables', 3, 1, 5, 40),
        ('2026-08-02', 'Model 630', 'Jane Smith <jane@example.com>', 'forms', 0, 0, 2, 8),
        ('2026-08-01', 'Conveyor', '', 'spec_macros', 1, 0, 0, 3),
    ]
    conn.executemany(
        'INSERT INTO category_changes (run_date, project, owner, category,'
        ' added, removed, modified, unchanged) VALUES (?,?,?,?,?,?,?,?)', rows)
    conn.close()
    return tmp_path / 'metrics.sqlite'


def test_dashboard_renders_activity(tmp_path):
    db = _seed_db(tmp_path)
    html = dashboard.generate_dashboard(db, today=date(2026, 8, 3))

    assert '<!DOCTYPE html>' in html
    # Stat tiles: latest active night = 2026-08-02 -> 3+1+5 + 0+0+2 = 11.
    assert '>11<' in html
    # Rank charts name the entities.
    assert 'Model 630' in html and 'Conveyor' in html
    assert 'Jane Smith' in html and '(unassigned)' in html
    assert 'Specification Macros' in html
    # Recent-changes table links into the dated drill-down reports,
    # URL-encoding the space in the project name.
    assert 'reports/2026-08-02/Model%20630.html' in html
    # Last-run status line from the runs table.
    assert '12 project(s) scanned' in html


def test_dashboard_is_self_contained(tmp_path):
    db = _seed_db(tmp_path)
    html = dashboard.generate_dashboard(db, today=date(2026, 8, 3))
    for marker in ('http://', 'https://', '@import', 'url('):
        assert marker not in html  # no external resources: works from a file share


def test_dashboard_empty_db_degrades_gracefully(tmp_path):
    conn = nightly_sync.open_db(tmp_path / 'metrics.sqlite')
    conn.close()
    html = dashboard.generate_dashboard(tmp_path / 'metrics.sqlite', today=date(2026, 8, 3))
    assert 'No sync runs recorded yet' in html
    assert 'No activity recorded yet' in html
    assert 'No changes recorded yet' in html


def test_recent_changes_embed_full_history_with_date_filter(tmp_path):
    # The page bakes EVERY per-night row in, shows the latest RECENT_ROWS by
    # default (.extra hides the rest), and the date inputs filter client-side
    # — a static file has no server to ask for more.
    conn = nightly_sync.open_db(tmp_path / 'metrics.sqlite')
    rows = [(f'2026-07-{d:02d}', f'Proj{d}', '', 'variables', 1, 0, 0, 3)
            for d in range(1, 26)]     # 25 nights, one project each
    conn.executemany(
        'INSERT INTO category_changes (run_date, project, owner, category,'
        ' added, removed, modified, unchanged) VALUES (?,?,?,?,?,?,?,?)', rows)
    conn.close()
    html = dashboard.generate_dashboard(tmp_path / 'metrics.sqlite',
                                        today=date(2026, 7, 26))

    assert html.count('data-d="') == 25          # full history is in the page
    assert html.count('class="extra"') == 25 - dashboard.RECENT_ROWS
    # Oldest date is hidden by default but present for the filter to find.
    assert '<tr class="extra" data-d="2026-07-01">' in html
    # Newest rows are not marked extra.
    assert '<tr data-d="2026-07-25">' in html
    # Inputs are bounded by the data actually in the page.
    assert 'id="rcFrom" min="2026-07-01" max="2026-07-25"' in html
    assert 'id="rcTo" min="2026-07-01" max="2026-07-25"' in html
    assert 'id="rcClear"' in html


def test_empty_db_renders_no_date_filter(tmp_path):
    conn = nightly_sync.open_db(tmp_path / 'metrics.sqlite')
    conn.close()
    html = dashboard.generate_dashboard(tmp_path / 'metrics.sqlite',
                                        today=date(2026, 8, 3))
    assert 'id="rcFrom"' not in html   # the JS guards on this id being absent


def test_dashboard_shows_last_run_errors(tmp_path):
    conn = nightly_sync.open_db(tmp_path / 'metrics.sqlite')
    conn.execute(
        'INSERT INTO runs (run_date, started_at, finished_at, projects_seen,'
        ' projects_changed, errors) VALUES (?,?,?,?,?,?)',
        ('2026-08-02', 'x', 'y', 5, 0, 'Broken.driveprojx: not a zip'))
    conn.close()
    html = dashboard.generate_dashboard(tmp_path / 'metrics.sqlite', today=date(2026, 8, 3))
    assert 'Last run had errors' in html
    assert 'Broken.driveprojx' in html
