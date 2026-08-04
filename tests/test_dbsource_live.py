"""LIVE integration tests for the SQL connector — real SQL Server required.

Skipped entirely unless DW_TEST_SQL_SERVER is set (plus DW_TEST_SQL_USER /
DW_SQL_PASSWORD for SQL auth). The sql-integration workflow runs this suite
against a matrix of real SQL Server engines (2017 / 2019 / 2022 containers),
which is the evidence behind "works across SQL Server versions".

What a containerized matrix can and cannot prove:
  - proves: TDS/driver compatibility per engine version, chunked IN() lookups,
    caching, discovery queries, identifier quoting against a live parser,
    fail-soft auth errors, SQL authentication.
  - cannot prove: Windows integrated auth (trusted=True) — exercised on the
    client's Windows deployment; Express/Standard editions (same engine as
    Developer, different limits); Azure SQL (same TDS protocol, untested).
"""

import os
import uuid

import pytest

from dw_compare.dbsource import DwDatabase, pick_driver

SERVER = os.environ.get('DW_TEST_SQL_SERVER', '')
USER = os.environ.get('DW_TEST_SQL_USER', 'sa')
PASSWORD = os.environ.get('DW_SQL_PASSWORD', '')

pytestmark = pytest.mark.skipif(
    not SERVER, reason='live SQL not configured (set DW_TEST_SQL_SERVER)')

DB_NAME = 'ProjxDiffLiveTest'
N_ROWS = 1200   # > 2x the connector's _CHUNK of 500, forces 3 chunked queries


def _admin_conn(database='master'):
    pyodbc = pytest.importorskip('pyodbc')
    driver = pick_driver()
    if not driver:
        pytest.skip('no SQL Server ODBC driver installed')
    conn = pyodbc.connect(
        f'DRIVER={{{driver}}};SERVER={SERVER};DATABASE={database};'
        f'UID={USER};PWD={PASSWORD};Encrypt=no;TrustServerCertificate=yes',
        timeout=15, autocommit=True)
    return conn


@pytest.fixture(scope='module')
def seeded_ids():
    """Create a synthetic DriveWorks-shaped schema and seed it: a captured-
    components table (GUID -> name) plus a spaced-name table to exercise
    bracket quoting against the live SQL parser."""
    conn = _admin_conn()
    cur = conn.cursor()
    cur.execute(f"IF DB_ID('{DB_NAME}') IS NOT NULL DROP DATABASE [{DB_NAME}]")
    cur.execute(f'CREATE DATABASE [{DB_NAME}]')
    cur.close()
    conn.close()

    conn = _admin_conn(DB_NAME)
    cur = conn.cursor()
    cur.execute('CREATE TABLE dbo.CapturedComponents '
                '(Id NVARCHAR(64) PRIMARY KEY, Name NVARCHAR(255) NOT NULL)')
    cur.execute('CREATE TABLE dbo.[Component Data] '
                '([Component Id] NVARCHAR(64) PRIMARY KEY, '
                '[Display Name] NVARCHAR(255) NOT NULL)')
    ids = {}
    rows = []
    for i in range(N_ROWS):
        gid = uuid.uuid4().hex
        ids[gid] = f'Component {i:04d}'
        rows.append((gid, ids[gid]))
    cur.fast_executemany = True
    cur.executemany('INSERT INTO dbo.CapturedComponents (Id, Name) VALUES (?, ?)', rows)
    cur.executemany('INSERT INTO dbo.[Component Data] ([Component Id], [Display Name]) '
                    'VALUES (?, ?)', rows[:10])

    # GUID-format matrix: the same ids keyed as uniqueidentifier (rejects
    # dashless strings outright) and as hyphenated-uppercase NVARCHAR.
    from dw_compare.dbsource import _hyphenated
    cur.execute('CREATE TABLE dbo.GuidKeyed '
                '(Id UNIQUEIDENTIFIER PRIMARY KEY, Name NVARCHAR(255) NOT NULL)')
    cur.execute('CREATE TABLE dbo.HyphenKeyed '
                '(Id NVARCHAR(64) PRIMARY KEY, Name NVARCHAR(255) NOT NULL)')
    cur.executemany('INSERT INTO dbo.GuidKeyed (Id, Name) VALUES (?, ?)',
                    [(_hyphenated(g), n) for g, n in rows[:50]])
    cur.executemany('INSERT INTO dbo.HyphenKeyed (Id, Name) VALUES (?, ?)',
                    [(_hyphenated(g).upper(), n) for g, n in rows[:50]])
    cur.close()
    conn.close()
    yield ids

    conn = _admin_conn()
    cur = conn.cursor()
    cur.execute(f"ALTER DATABASE [{DB_NAME}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
    cur.execute(f'DROP DATABASE [{DB_NAME}]')
    cur.close()
    conn.close()


@pytest.fixture
def db(seeded_ids):
    d = DwDatabase(label='live', server=SERVER, database=DB_NAME,
                   user=USER, password=PASSWORD, trusted=False, timeout=15)
    assert d.connect() is True
    yield d
    d.close()


def test_lookup_resolves_all_ids_across_chunks(db, seeded_ids):
    got = db.lookup('CapturedComponents', 'Id', 'Name', list(seeded_ids))
    assert got == seeded_ids  # all 1200, proving multi-chunk IN() handling


def test_second_lookup_is_served_from_cache(db, seeded_ids, monkeypatch):
    db.lookup('CapturedComponents', 'Id', 'Name', list(seeded_ids))
    calls = []
    real = db._select
    monkeypatch.setattr(db, '_select', lambda *a, **k: calls.append(1) or real(*a, **k))
    again = db.lookup('CapturedComponents', 'Id', 'Name', list(seeded_ids))
    assert again == seeded_ids
    assert calls == []  # zero queries the second time


def test_unknown_ids_are_absent_and_negative_cached(db, monkeypatch):
    ghosts = [uuid.uuid4().hex for _ in range(5)]
    assert db.lookup('CapturedComponents', 'Id', 'Name', ghosts) == {}
    calls = []
    real = db._select
    monkeypatch.setattr(db, '_select', lambda *a, **k: calls.append(1) or real(*a, **k))
    assert db.lookup('CapturedComponents', 'Id', 'Name', ghosts) == {}
    assert calls == []  # misses are negative-cached, no re-query storm


def test_spaced_identifiers_survive_live_parser(db, seeded_ids):
    some = dict(list(seeded_ids.items())[:10])
    got = db.lookup('Component Data', 'Component Id', 'Display Name', list(some))
    assert got == some


def test_discovery_finds_the_mapping_table(db):
    tables = db.list_tables()
    assert ('dbo', 'CapturedComponents') in tables
    cols = dict(db.columns_of('CapturedComponents'))
    assert cols.get('Id') == 'nvarchar' and cols.get('Name') == 'nvarchar'
    candidates = db.find_id_name_tables()
    hit = [c for c in candidates if c['table'] == 'CapturedComponents']
    assert hit and 'Id' in hit[0]['id_cols'] and 'Name' in hit[0]['name_cols']
    assert len(db.sample('CapturedComponents', 'Id', 'Name', n=5)) == 5


def test_dashless_file_ids_resolve_against_uniqueidentifier_column(db, seeded_ids):
    # THE discovery blocker: project files carry dashless 32-hex ids; group
    # databases key these tables on uniqueidentifier, which errors on a
    # dashless string and killed the whole IN() query before the format-
    # agnostic lookup. Now the hyphenated pass matches.
    subset = dict(list(seeded_ids.items())[:50])
    assert db.lookup('GuidKeyed', 'Id', 'Name', list(subset)) == subset


def test_dashless_file_ids_resolve_against_hyphenated_nvarchar(db, seeded_ids):
    subset = dict(list(seeded_ids.items())[:50])
    assert db.lookup('HyphenKeyed', 'Id', 'Name', list(subset)) == subset


def test_bad_credentials_fail_soft_not_raise(seeded_ids):
    d = DwDatabase(label='bad', server=SERVER, database=DB_NAME,
                   user=USER, password='definitely-wrong', trusted=False, timeout=8)
    assert d.connect() is False
    assert d.lookup('CapturedComponents', 'Id', 'Name', ['x']) == {}
