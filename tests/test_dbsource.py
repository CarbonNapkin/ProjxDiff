"""Tests for the SQL connector layer and ID mapping.

No database required: these cover the injection guard, the fail-soft paths, and
the resolver contract. Anything touching a live server is out of scope for CI.
"""

import pytest

from dw_compare.dbsource import _quote_ident, DwDatabase, open_pair, pick_driver
from dw_compare.idmap import IdSource, IdResolver, NullResolver, label_for


# ---- identifier quoting / injection guard ----
# Table and column names cannot be passed as query parameters, so they are
# interpolated — which makes this guard load-bearing.

def test_quote_ident_wraps_valid_identifier():
    assert _quote_ident("Components") == "[Components]"
    assert _quote_ident("Component Name") == "[Component Name]"


@pytest.mark.parametrize("bad", [
    "Comp; DROP TABLE x",
    "a'b",
    "x--",
    "tbl)",
    "",
    "1abc",
])
def test_quote_ident_rejects_injection_and_junk(bad):
    with pytest.raises(ValueError):
        _quote_ident(bad)


# ---- read-only enforcement ----

def test_select_only_rejects_non_select():
    db = DwDatabase(label="t", server="x", database="y")
    with pytest.raises(ValueError):
        db._select("DELETE FROM Components")
    with pytest.raises(ValueError):
        db._select("UPDATE x SET y=1")


# ---- fail-soft ----

def test_connect_failure_returns_false_not_raise():
    db = DwDatabase(label="t", server="no.such.host", database="nope", timeout=1)
    assert db.connect() is False
    # stays dead, no retry storm
    assert db.connect() is False


def test_lookup_without_connection_returns_empty():
    db = DwDatabase(label="t", server="no.such.host", database="nope", timeout=1)
    assert db.lookup("T", "id", "name", ["abc"]) == {}


def test_lookup_with_no_ids_short_circuits():
    db = DwDatabase(label="t", server="no.such.host", database="nope", timeout=1)
    assert db.lookup("T", "id", "name", []) == {}
    assert db.lookup("T", "id", "name", [None, ""]) == {}


def test_close_is_safe_when_never_connected():
    DwDatabase(label="t", server="x", database="y").close()


def test_context_manager_survives_bad_host():
    with DwDatabase(label="t", server="no.such.host", database="nope", timeout=1) as db:
        assert db.lookup("T", "id", "name", ["a"]) == {}


def test_pick_driver_returns_none_or_string():
    assert pick_driver() is None or isinstance(pick_driver(), str)


# ---- open_pair: one connector per side ----

def test_open_pair_builds_two_independent_connectors():
    old, new = open_pair(
        {"server": "A", "database": "ProdGroup"},
        {"server": "B", "database": "DevGroup"},
    )
    assert old.label == "old" and new.label == "new"
    assert old.database == "ProdGroup" and new.database == "DevGroup"
    assert old is not new


def test_open_pair_allows_a_side_without_a_database():
    old, new = open_pair(None, {"server": "B", "database": "DevGroup"})
    assert old is None
    assert new.label == "new"


# ---- resolver ----

def test_null_resolver_is_inactive_and_returns_empty():
    r = NullResolver()
    assert r.active is False
    assert r.resolve("component", ["a", "b"]) == {}


def test_resolver_without_db_is_inactive():
    assert IdResolver(db=None).active is False


def test_resolver_skips_disabled_sources():
    # A source with enabled=False must never be queried, even with a live db.
    r = IdResolver(db=object(), sources={"component": IdSource("T", "i", "n", enabled=False)})
    assert r.resolve("component", ["a"]) == {}


def test_resolver_unknown_kind_returns_empty():
    assert IdResolver(db=object(), sources={}).resolve("nope", ["a"]) == {}


def test_label_for_prefers_mapping_then_falls_back_to_guid():
    r = NullResolver()
    assert label_for(r, "component", "abc", {"abc": "Frame"}) == "Frame"
    assert label_for(r, "component", "abc", {}) == "abc"
    assert label_for(r, "component", "") == ""


def test_report_mentions_raw_guids_when_no_db():
    assert "raw GUID" in NullResolver().report()
