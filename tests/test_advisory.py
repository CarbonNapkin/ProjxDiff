"""Advisories: telling installed copies that their version has a problem.

The properties that matter: an advisory reaches the right versions and only
those; nothing about the advisory list being absent, unreachable or malformed
changes how the tool runs; and a critical one puts the nightly sync on hold
without losing anything."""

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

import dw_compare.advisory as advisory
import dw_compare.sync as sync_mod
from dw_compare._version import __version__

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'nightly_sync'))
import nightly_sync  # noqa: E402


def _adv(**kw):
    base = {'id': 'test-1', 'severity': 'critical', 'summary': 'Something is wrong.'}
    base.update(kw)
    return base


def _doc(*entries):
    return {'schema': 1, 'advisories': list(entries)}


@pytest.mark.parametrize('introduced,fixed,version,hit', [
    (None, '1.12.1', '1.12.0', True),
    (None, '1.12.1', '1.12.1', False),      # fixed is the first safe version
    (None, '1.12.1', '1.13.0', False),
    ('1.2.0', '1.12.1', '1.1.9', False),    # before it was introduced
    ('1.2.0', '1.12.1', '1.2.0', True),
    ('1.2.0', None, '9.0.0', True),         # no fix yet: everything since
    (None, None, '0.0.1', True),
    (None, 'v1.12.1', '1.9.0', True),       # tolerated spelling
    (None, '1.10.0', '1.9.0', True),        # numeric, not string, comparison
    (None, '1.9.0', '1.10.0', False),
])
def test_version_ranges(introduced, fixed, version, hit):
    entry = _adv(**{k: v for k, v in (('introduced', introduced), ('fixed', fixed)) if v})
    assert bool(advisory.applicable(_doc(entry), version)) is hit


@pytest.mark.parametrize('entry', [
    'not a dict',
    {'id': 'x', 'severity': 'critical'},                           # no summary
    {'id': 'x', 'summary': 's', 'severity': 'catastrophic'},        # unknown severity
    {'severity': 'critical', 'summary': 's'},                       # no id
    {'id': 'x', 'severity': 'critical', 'summary': 's', 'fixed': 'soon'},
    {'id': 'x', 'severity': 'critical', 'summary': 's', 'introduced': '1.x'},
])
def test_a_malformed_entry_is_dropped_and_the_rest_still_count(entry):
    good = _adv(id='good')
    assert [a['id'] for a in advisory.applicable(_doc(entry, good), '1.0.0')] == ['good']


@pytest.mark.parametrize('doc', [None, [], 'x', {}, {'advisories': None},
                                 {'advisories': 'nope'}])
def test_a_malformed_document_means_no_advisories(doc):
    assert advisory.applicable(doc, '1.0.0') == []


def test_critical_sorts_first_and_the_summary_is_plain_bounded_text():
    doc = _doc(_adv(id='b', severity='important'),
               _adv(id='a', summary='line one\n\n<b>line</b>\ttwo ' + 'x' * 5000))
    out = advisory.applicable(doc, '1.0.0')
    assert [a['id'] for a in out] == ['a', 'b']
    assert '\n' not in out[0]['summary'] and len(out[0]['summary']) <= 600


def test_fetch_fails_open(monkeypatch):
    monkeypatch.undo()                       # the real fetch, not conftest's stub

    def boom(*a, **k):
        raise OSError('no network')
    monkeypatch.setattr(advisory, '_get', boom)
    monkeypatch.delenv(advisory.URL_ENV, raising=False)
    assert advisory.fetch() is None
    assert advisory.check() == []


def test_fetch_reads_a_local_override(monkeypatch, tmp_path):
    monkeypatch.undo()
    f = tmp_path / 'adv.json'
    f.write_text(json.dumps(_doc(_adv())), encoding='utf-8')
    monkeypatch.setenv(advisory.URL_ENV, str(f))
    assert [a['id'] for a in advisory.check(version='1.0.0')] == ['test-1']
    f.write_text('{not json', encoding='utf-8')
    assert advisory.fetch() is None


def test_the_shipped_advisory_file_parses():
    doc = json.loads((Path(__file__).resolve().parents[1] / 'advisories.json')
                     .read_text(encoding='utf-8'))
    assert isinstance(doc['advisories'], list)
    for entry in doc['advisories']:
        assert advisory._parse(entry) is not None, entry


def test_an_unattended_run_remembers_the_last_list_it_read(monkeypatch, tmp_path):
    cache = tmp_path / 'advisory_cache.json'
    monkeypatch.setattr(advisory, 'fetch', lambda *a, **k: _doc(_adv()))
    assert advisory.load(cache, version='1.0.0')[1] == 'live'
    monkeypatch.setattr(advisory, 'fetch', lambda *a, **k: None)   # offline tonight
    advs, source = advisory.load(cache, version='1.0.0')
    assert source == 'cached' and [a['id'] for a in advs] == ['test-1']
    monkeypatch.setattr(advisory, 'fetch', lambda *a, **k: _doc())  # withdrawn
    assert advisory.load(cache, version='1.0.0') == ([], 'live')
    monkeypatch.setattr(advisory, 'fetch', lambda *a, **k: None)
    assert advisory.load(cache, version='1.0.0') == ([], 'cached')


# ----------------------------------------------------------- nightly sync ----

def _projx(path: Path, variables: dict):
    rows = ''.join(f'<Variable DisplayName="{n}" StoreName="{n}" Rule="{v}"/>'
                   for n, v in variables.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('driveProj/project.xml',
                    f'<Project><Variables>{rows}</Variables></Project>')


@pytest.fixture
def site(tmp_path):
    source = tmp_path / 'share'
    _projx(source / 'Alpha.driveprojx', {'Width': '=800'})
    _projx(source / 'Beta.driveprojx', {'Depth': '=300'})
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({
        'source_dir': str(source), 'archive_repo': str(tmp_path / 'repo'),
        'data_dir': str(tmp_path / 'data'), 'remove_missing': True}), encoding='utf-8')
    return {'source': source, 'cfg_path': cfg_path, 'repo': tmp_path / 'repo',
            'data': tmp_path / 'data'}


def _run(site, dry_run=False):
    return nightly_sync.run(nightly_sync.load_config(site['cfg_path']), dry_run)


def _archived(site, name):
    return (site['repo'] / name / 'driveProj' / 'project.xml').read_text(encoding='utf-8')


def test_a_critical_advisory_holds_the_sync_and_loses_nothing(site, monkeypatch, caplog):
    assert _run(site) == 0
    before = _archived(site, 'Alpha')

    _projx(site['source'] / 'Alpha.driveprojx', {'Width': '=900'})     # changed
    (site['source'] / 'Beta.driveprojx').unlink()                      # gone
    _projx(site['source'] / 'Gamma.driveprojx', {'New': '=1'})         # new

    monkeypatch.setattr(advisory, 'fetch',
                        lambda *a, **k: _doc(_adv(id='hold-me', fixed='99.0.0')))
    with caplog.at_level('WARNING'):
        assert _run(site) == 1
    assert 'ON HOLD' in caplog.text and 'hold-me' in caplog.text
    assert _archived(site, 'Alpha') == before            # not replaced
    assert (site['repo'] / 'Beta').is_dir()              # not pruned
    assert (site['repo'] / 'Gamma').is_dir()             # adding is still fine
    census = json.loads((site['data'] / 'census.json').read_text(encoding='utf-8'))
    assert census['advisories'][0]['id'] == 'hold-me' and census['advisories'][0]['held']
    assert 'on hold' in (site['data'] / 'dashboard.html').read_text(encoding='utf-8')

    # Updated (the advisory no longer covers this version): the held change
    # is diffed against the untouched archive and recorded in full.
    monkeypatch.setattr(advisory, 'fetch',
                        lambda *a, **k: _doc(_adv(id='hold-me', fixed=__version__)))
    assert _run(site) == 0
    assert '=900' in _archived(site, 'Alpha')
    assert not (site['repo'] / 'Beta').exists()
    with sqlite3.connect(site['data'] / 'metrics.sqlite') as db:
        assert db.execute("SELECT COUNT(*) FROM element_changes WHERE "
                          "project='Alpha' AND status='modified'").fetchone()[0] == 1
    census = json.loads((site['data'] / 'census.json').read_text(encoding='utf-8'))
    assert 'advisories' not in census


def test_an_important_advisory_warns_and_changes_nothing_else(site, monkeypatch, caplog):
    assert _run(site) == 0
    _projx(site['source'] / 'Alpha.driveprojx', {'Width': '=900'})
    monkeypatch.setattr(advisory, 'fetch',
                        lambda *a, **k: _doc(_adv(severity='important')))
    with caplog.at_level('WARNING'):
        assert _run(site) == 0
    assert 'IMPORTANT' in caplog.text and 'ON HOLD' not in caplog.text
    assert '=900' in _archived(site, 'Alpha')


def test_a_hold_survives_a_night_without_internet(site, monkeypatch):
    assert _run(site) == 0
    monkeypatch.setattr(advisory, 'fetch',
                        lambda *a, **k: _doc(_adv(fixed='99.0.0')))
    assert _run(site) == 1
    before = _archived(site, 'Alpha')
    _projx(site['source'] / 'Alpha.driveprojx', {'Width': '=900'})
    monkeypatch.setattr(advisory, 'fetch', lambda *a, **k: None)
    assert _run(site) == 1
    assert _archived(site, 'Alpha') == before


def test_an_unreachable_list_with_no_history_runs_normally(site):
    assert _run(site) == 0                    # conftest: fetch -> None
    _projx(site['source'] / 'Alpha.driveprojx', {'Width': '=900'})
    assert _run(site) == 0
    assert '=900' in _archived(site, 'Alpha')


def test_a_dry_run_reports_the_hold_and_writes_no_cache(site, monkeypatch, caplog):
    monkeypatch.setattr(advisory, 'fetch',
                        lambda *a, **k: _doc(_adv(fixed='99.0.0')))
    with caplog.at_level('WARNING'):
        assert _run(site, dry_run=True) == 1
    assert 'ON HOLD' in caplog.text
    assert not (site['data'] / 'advisory_cache.json').exists()


# -------------------------------------------------------------------- app ----

def test_the_app_interrupts_only_for_critical():
    from dw_compare.gui import advisory_prompt
    crit = advisory.applicable(_doc(_adv(summary='Deletes things.')), '1.0.0')
    imp = advisory.applicable(_doc(_adv(severity='important')), '1.0.0')

    p = advisory_prompt(crit, '9.9.9')
    assert p['modal'] and p['offer_update'] and 'Deletes things.' in p['text']
    assert '9.9.9' in p['notice']

    p = advisory_prompt(crit, None)             # no fix published yet
    assert p['modal'] and not p['offer_update'] and p['notice'] == ''

    p = advisory_prompt(imp, '9.9.9')
    assert not p['modal'] and not p['offer_update'] and '9.9.9' in p['notice']
