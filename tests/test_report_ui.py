"""Tests for the report's UI shell: the sidebar rail (totals + section nav),
light/dark theming, colorblind-safe status treatment, and preservation of
every interactive behavior (filters, resizers, toggles). Also runs the
report's embedded JavaScript through `node --check` so a syntax break in the
script block can never ship silently again."""

import re
import shutil
import subprocess

import pytest

from dw_compare.models import DWProject, Variable
from dw_compare.report import generate_html_report, _slug

SECTION_TITLES = [
    'Variables', 'Constants', 'Calculation Tables', 'Component Sets',
    'Models', 'Rule Changes', 'Component Tasks', 'Documents',
    'Lookup Tables', 'Data Tables', 'Specification Macros',
    'Navigation Steps', 'Forms',
]


def _report(old=None, new=None, **kw):
    return generate_html_report(old or DWProject(), new or DWProject(),
                                'OLD-PROJ', 'NEW-PROJ', **kw)


def _changed_projects():
    old = DWProject()
    new = DWProject()
    old.variables = {'W': Variable('W', formula='=800'),
                     'Gone': Variable('Gone', formula='=1')}
    new.variables = {'W': Variable('W', formula='=600'),
                     'New': Variable('New', formula='=2')}
    return old, new


# ---------- rail: totals + navigation ----------

def test_rail_has_every_section_with_counts():
    html = _report()
    for title in SECTION_TITLES:
        assert f'data-sec="{_slug(title)}"' in html
        assert f'id="{_slug(title)}"' in html   # nav target exists


def test_rail_totals_chips_reflect_summary():
    old, new = _changed_projects()
    html = _report(old, new)
    assert '<span>+ Added</span><b>1</b>' in html
    assert '<span>&minus; Removed</span><b>1</b>' in html
    assert '<span>~ Modified</span><b>1</b>' in html


def test_rail_dims_sections_without_changes():
    old, new = _changed_projects()
    html = _report(old, new)
    # Variables changed -> not dim; Forms untouched -> dim.
    var_item = re.search(r'<a class="navitem([^"]*)"[^>]*data-sec="sec-variables"', html)
    forms_item = re.search(r'<a class="navitem([^"]*)"[^>]*data-sec="sec-forms"', html)
    assert 'dim' not in var_item.group(1)
    assert 'dim' in forms_item.group(1)


# ---------- theming ----------

def test_theme_scopes_cover_auto_and_explicit_choice():
    html = _report()
    # Auto: dark via the OS, unless the viewer explicitly picked light.
    assert '@media (prefers-color-scheme: dark)' in html
    assert ':root:not([data-theme="light"])' in html
    # Explicit: the toggle must beat the OS in both directions.
    assert ':root[data-theme="dark"]' in html


def test_theme_toggle_buttons_and_persistence():
    html = _report()
    for mode in ('Auto', 'Light', 'Dark'):
        assert f'>{mode}</button>' in html
    assert "localStorage.getItem('projxdiff-theme')" in html
    assert "localStorage.setItem('projxdiff-theme'" in html
    # The saved theme applies before first paint (no flash).
    head = html.split('</head>')[0]
    assert 'projxdiff-theme' in head


# ---------- colorblind-safe status ----------

def test_old_green_red_palette_is_gone():
    html = _report(*_changed_projects())
    for old_color in ('#2e9b40', '#d33b30', '#e6ffec', '#ffebe9', '#3f51b5', '#1a237e'):
        assert old_color not in html, old_color


def test_status_is_never_color_alone():
    old, new = _changed_projects()
    html = _report(old, new)
    # Text labels on row badges...
    assert '>Added</span>' in html and '>Removed</span>' in html
    # ...plus glyphs added by CSS on table-row badges.
    assert 'td .badge-added::before' in html
    assert 'td .badge-removed::before' in html
    assert 'td .badge-modified::before' in html


def test_inline_diff_spans_preserved():
    old, new = _changed_projects()
    html = _report(old, new)
    assert '<span class="added">600</span>' in html
    assert '<span class="removed">800</span>' in html


# ---------- interactive behaviors preserved ----------

def test_all_interactive_machinery_present():
    html = _report()
    for needle in ('function filterRows', 'function initColumnResize',
                   'function expandAll', 'function initNav', 'function setTheme',
                   'anyVisibleRow', 'showQuietSections', 'showLookupUnchanged',
                   'col-resizer'):
        assert needle in html, needle


def test_quiet_sections_marked_and_unchanged_badge_classed():
    old, new = _changed_projects()
    html = _report(old, new)
    assert 'data-quiet="1"' in html
    assert 'badge-unchanged' in html
    assert 'style="background:#dadde2' not in html  # old inline style gone


# ---------- escaping (new surfaces: rail + page head) ----------

def test_hostile_project_names_are_escaped_everywhere():
    html = generate_html_report(DWProject(), DWProject(),
                                '<script>alert(1)</script>', 'B&B <img>')
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert 'B&amp;B &lt;img&gt;' in html


# ---------- resolved-names path ----------

def test_report_accepts_resolved_name_dicts():
    # A model present only on the new side renders as Added by file name.
    html = _report(old_resolved={},
                   new_resolved={'aa': 'T:/M/Part.SLDPRT'})
    assert 'Part.SLDPRT' in html


# ---------- the embedded JavaScript actually parses ----------

@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_report_javascript_is_syntactically_valid(tmp_path):
    html = _report(*_changed_projects())
    scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    assert scripts, 'no script blocks found'
    for i, js in enumerate(scripts):
        p = tmp_path / f'block{i}.js'
        p.write_text(js, encoding='utf-8')
        proc = subprocess.run(['node', '--check', str(p)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f'script block {i} failed: {proc.stderr}'
