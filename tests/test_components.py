"""Tests for model/component parsing and ID -> name resolution.

Fixtures are synthetic inline XML — real customer .driveprojx files must never
enter the repo (see .gitignore).
"""

import os

from dw_compare.components import (
    _norm, build_component_index, parse_component_sets, parse_placed_components,
    ComponentIndex, ComponentSet, PlacedComponent, resolve_names,
)
from dw_compare.idmap import NullResolver


PROJECT_XML = '''<?xml version="1.0" encoding="utf-8"?>
<p:Project xmlns:p="http://schemas.driveworks.co.uk/project/">
  <p:ComponentSets>
    <p:ComponentSet Type="DriveWorks.SolidWorks.Components.PartFactory, DriveWorks.SolidWorks"
                    Name="R1-Aluminum" RId="R387725e3359f46fe">
      <p:Rule>=If(Alu, "*" &amp; Name, "Delete")</p:Rule>
    </p:ComponentSet>
    <p:ComponentSet Type="DriveWorks.SolidWorks.Components.AssemblyFactory, DriveWorks.SolidWorks"
                    Name="CRITICAL ENVIRONMENT" RId="R5bf08022494c425a">
      <p:Rule>=If(Top, "*" &amp; Full, "Delete")</p:Rule>
    </p:ComponentSet>
  </p:ComponentSets>
</p:Project>'''

COMPONENT_XML = '''<?xml version="1.0" encoding="utf-8"?>
<pcomp:CS xmlns:pcomp="http://schemas.driveworks.co.uk/p-component/">
  <pcomp:PC CCRef="97c4d15c4f68438bb4d0d977755e9955" TrId="12023044ae504704b22b4ecafa3a1eb0">
    <pcomp:CN><pcomp:R>=Name</pcomp:R></pcomp:CN>
  </pcomp:PC>
  <pcomp:PC CCRef="b63e09d862414feab344f061bda88ada" TrId="fd15726481c543bb8407dba0c2c9f888">
    <pcomp:CN><pcomp:R>=Other</pcomp:R></pcomp:CN>
  </pcomp:PC>
</pcomp:CS>'''


def _make_project(tmp_path):
    d = tmp_path / "driveProj"
    (d / "components").mkdir(parents=True)
    (d / "project.xml").write_text(PROJECT_XML, encoding="utf-8")
    (d / "components" / "1.xml").write_text(COMPONENT_XML, encoding="utf-8")
    return str(tmp_path)


# ---- _norm ----

def test_norm_strips_dashes_case_and_r_prefix():
    # DriveWorks writes the same id three different ways across files.
    assert _norm("12023044-AE50-4704-B22B-4ECAFA3A1EB0") == "12023044ae504704b22b4ecafa3a1eb0"
    assert _norm("R387725e3359f46fe") == "387725e3359f46fe"
    assert _norm("") == ""
    assert _norm(None) == ""


def test_norm_makes_hyphenated_and_flat_ids_compare_equal():
    assert _norm("12023044-ae50-4704-b22b-4ecafa3a1eb0") == _norm("12023044ae504704b22b4ecafa3a1eb0")


# ---- ComponentSets (free names from XML) ----

def test_parse_component_sets_reads_names_types_and_rules(tmp_path):
    root = _make_project(tmp_path)
    sets = parse_component_sets(os.path.join(root, "driveProj", "project.xml"))
    assert len(sets) == 2
    names = {s.name for s in sets.values()}
    assert names == {"R1-Aluminum", "CRITICAL ENVIRONMENT"}
    alu = sets[_norm("R387725e3359f46fe")]
    assert alu.set_type == "PartFactory"
    assert alu.rule.startswith("=If(Alu")


def test_parse_component_sets_missing_file_degrades(tmp_path):
    # Must warn and return {}, not raise — matches the parser's fail-soft style.
    assert parse_component_sets(str(tmp_path / "nope.xml")) == {}


# ---- placed components ----

def test_parse_placed_components_links_trid_to_ccref(tmp_path):
    root = _make_project(tmp_path)
    placed, t2c = parse_placed_components(os.path.join(root, "driveProj", "components"))
    assert len(placed) == 2
    assert t2c[_norm("12023044ae504704b22b4ecafa3a1eb0")] == _norm("97c4d15c4f68438bb4d0d977755e9955")


def test_parse_placed_components_empty_dir(tmp_path):
    (tmp_path / "components").mkdir()
    placed, t2c = parse_placed_components(str(tmp_path / "components"))
    assert placed == {} and t2c == {}


# ---- index + label resolution ----

def test_build_index_finds_both_sets_and_placed(tmp_path):
    idx = build_component_index(_make_project(tmp_path))
    assert len(idx.sets) == 2
    assert len(idx.placed) == 2
    assert len(idx.all_lookup_keys()) == 4  # 2 trids + 2 ccrefs


def test_label_prefers_db_name_via_ccref(tmp_path):
    idx = build_component_index(_make_project(tmp_path))
    db = {_norm("97c4d15c4f68438bb4d0d977755e9955"): "FACE FRAME <2>"}
    assert idx.label("12023044-ae50-4704-b22b-4ecafa3a1eb0", db) == "FACE FRAME <2>"


def test_label_falls_back_to_db_name_keyed_on_the_id_itself(tmp_path):
    # Master-model macro tasks have no ccref; the id itself is the DB key.
    idx = build_component_index(_make_project(tmp_path))
    db = {_norm("fd15726481c543bb8407dba0c2c9f888"): "MASTER MODEL"}
    assert idx.label("fd15726481c543bb8407dba0c2c9f888", db) == "MASTER MODEL"


def test_label_falls_back_to_component_set_name(tmp_path):
    idx = build_component_index(_make_project(tmp_path))
    assert idx.label("R387725e3359f46fe") == "R1-Aluminum"


def test_label_falls_back_to_raw_guid_when_nothing_resolves(tmp_path):
    # An unresolved id must stay traceable rather than vanish.
    idx = build_component_index(_make_project(tmp_path))
    assert idx.label("deadbeefdeadbeefdeadbeefdeadbeef") == "deadbeefdeadbeefdeadbeefdeadbeef"


def test_label_empty_id_is_empty(tmp_path):
    idx = build_component_index(_make_project(tmp_path))
    assert idx.label("") == ""


def test_label_without_db_never_raises(tmp_path):
    idx = build_component_index(_make_project(tmp_path))
    assert idx.label("12023044ae504704b22b4ecafa3a1eb0", None)  # falls back, no crash


# ---- resolve_names with no DB ----

def test_resolve_names_with_null_resolver_returns_empty(tmp_path):
    idx = build_component_index(_make_project(tmp_path))
    assert resolve_names(idx, NullResolver()) == {}
    assert resolve_names(idx, None) == {}


def test_index_on_folder_without_components_still_works(tmp_path):
    d = tmp_path / "driveProj"
    d.mkdir(parents=True)
    (d / "project.xml").write_text(PROJECT_XML, encoding="utf-8")
    idx = build_component_index(str(tmp_path))
    assert len(idx.sets) == 2
    assert idx.placed == {}
