"""Tests for the JSON diff layer.

The core guarantee: for every category, the JSON differ and the HTML comparer
agree on per-category counts. Each equivalence case below reuses a fixture
shape from test_comparers.py (including every REGRESSION fixture) so a future
change to one side's change-detection logic fails here instead of silently
drifting the metrics away from the report.
"""

import pytest

from dw_compare import jsondiff
from dw_compare.jsondiff import (
    build_diff,
    _diff_variables,
    _diff_constants,
    _diff_calc_tables,
    _diff_component_tasks,
    _diff_documents,
    _diff_lookup_tables,
    _diff_data_tables,
    _diff_nav_steps,
    _diff_spec_macros,
    _diff_forms,
)
from dw_compare.comparers import (
    compare_variables,
    compare_constants,
    compare_calc_tables,
    compare_component_tasks,
    compare_documents,
    compare_lookup_tables,
    compare_data_tables,
    compare_nav_steps,
    compare_spec_macros,
    compare_forms,
)
from dw_compare.models import (
    DWProject, Variable, Constant, CalcTable, ComponentTask, SpecMacro,
    SpecMacroTask, NavStep, DataTableDef, Form, FormControl,
)


def _doc(t, rules):
    return {"type": t, "rules": rules}


def _task(name, rules):
    return ComponentTask(id="t", name=name, task_type="T", component_id="c", rules=rules)


def _mtask(path, title="Create Folder"):
    return SpecMacroTask(title=title, task_type="CreateFolder", properties={"Path": path})


# (label, json_differ, html_comparer, old, new) — one entry per fixture shape.
EQUIVALENCE_CASES = [
    ("variables add/remove/unchanged", _diff_variables, compare_variables,
     {"A": Variable("A", formula="=1"), "B": Variable("B", formula="=2")},
     {"A": Variable("A", formula="=1"), "C": Variable("C", formula="=3")}),
    ("variables category change", _diff_variables, compare_variables,
     {"A": Variable("A", formula="=1", category="Dims")},
     {"A": Variable("A", formula="=1", category="Sizes")}),
    ("variables store-only change", _diff_variables, compare_variables,
     {"A": Variable("A", formula="=1", store_name="S1")},
     {"A": Variable("A", formula="=1", store_name="S2")}),
    ("constants value change", _diff_constants, compare_constants,
     {"M": Constant("M", value="2")}, {"M": Constant("M", value="5")}),
    ("constants comment-only change", _diff_constants, compare_constants,
     {"M": Constant("M", value="2", comment="old")},
     {"M": Constant("M", value="2", comment="new")}),
    ("calc row-count change", _diff_calc_tables, compare_calc_tables,
     {"T": CalcTable("T", row_count=3, columns={"C": {"common": "=1", "rows": {}}})},
     {"T": CalcTable("T", row_count=5, columns={"C": {"common": "=1", "rows": {}}})}),
    ("calc common-rule change", _diff_calc_tables, compare_calc_tables,
     {"T": CalcTable("T", row_count=20, columns={"C": {"common": "=A*1", "rows": {}}})},
     {"T": CalcTable("T", row_count=20, columns={"C": {"common": "=A*2", "rows": {}}})}),
    ("calc identical", _diff_calc_tables, compare_calc_tables,
     {"T": CalcTable("T", row_count=20, columns={"C": {"common": "=A*1", "rows": {0: "=B"}}})},
     {"T": CalcTable("T", row_count=20, columns={"C": {"common": "=A*1", "rows": {0: "=B"}}})}),
    ("calc column added", _diff_calc_tables, compare_calc_tables,
     {"T": CalcTable("T", row_count=2, columns={"C": {"common": "=1", "rows": {}}})},
     {"T": CalcTable("T", row_count=2, columns={"C": {"common": "=1", "rows": {}},
                                                "D": {"common": "=2", "rows": {1: "=3"}}})}),
    ("component task rule change", _diff_component_tasks, compare_component_tasks,
     {"k": _task("Gen", {"Tmpl": '="A3"'})}, {"k": _task("Gen", {"Tmpl": '="A2"'})}),
    ("document type change", _diff_documents, compare_documents,
     {"D": _doc("PrintDocument", {"r1": "=1"})}, {"D": _doc("ExportDocument", {"r1": "=1"})}),
    ("document rule change", _diff_documents, compare_documents,
     {"D": _doc("X", {"FileName": '="a"'})}, {"D": _doc("X", {"FileName": '="b"'})}),
    ("document unchanged", _diff_documents, compare_documents,
     {"D": _doc("X", {"r1": "=1"})}, {"D": _doc("X", {"r1": "=1"})}),
    ("lookup cell change", _diff_lookup_tables, compare_lookup_tables,
     {"L": "Material,Cost\nSteel,2.5\nAlu,4.1"}, {"L": "Material,Cost\nSteel,2.5\nAlu,4.4"}),
    ("lookup duplicate headers", _diff_lookup_tables, compare_lookup_tables,
     {"L": "Key,Val,Val\nA,1,2\nB,3,4"}, {"L": "Key,Val,Val\nA,1,9\nB,3,4"}),
    ("lookup column added", _diff_lookup_tables, compare_lookup_tables,
     {"L": "Key,Val\nA,1"}, {"L": "Key,Val,Extra\nA,1,x"}),
    ("data table type change", _diff_data_tables, compare_data_tables,
     {"D": DataTableDef("D", table_type="A")}, {"D": DataTableDef("D", table_type="B")}),
    ("nav step display no-op is unchanged", _diff_nav_steps, compare_nav_steps,
     {"S": NavStep("S", step_type="Form", next_step_value="X", next_step_rule='"X"')},
     {"S": NavStep("S", step_type="Form", next_step_value="X", next_step_rule="")}),
    ("nav step real change", _diff_nav_steps, compare_nav_steps,
     {"S": NavStep("S", step_type="Form", next_step_value="X")},
     {"S": NavStep("S", step_type="Form", next_step_value="Y")}),
    ("macro duplicate task labels", _diff_spec_macros, compare_spec_macros,
     {"M": SpecMacro("M", tasks=[_mtask("=A"), _mtask("=B")])},
     {"M": SpecMacro("M", tasks=[_mtask("=A2"), _mtask("=B")])}),
    ("macro reorder only", _diff_spec_macros, compare_spec_macros,
     {"M": SpecMacro("M", tasks=[_mtask("=A", "First"), _mtask("=B", "Second")])},
     {"M": SpecMacro("M", tasks=[_mtask("=B", "Second"), _mtask("=A", "First")])}),
    ("form is_static-only no-op is unchanged", _diff_forms, compare_forms,
     {"F": Form("F", controls={"C": FormControl("C", "TextBox", {"VisibleRule": (False, "=True")})})},
     {"F": Form("F", controls={"C": FormControl("C", "TextBox", {"VisibleRule": (True, "=True")})})}),
    ("form real prop change", _diff_forms, compare_forms,
     {"F": Form("F", controls={"C": FormControl("C", "TextBox", {"VisibleRule": (False, "=True")})})},
     {"F": Form("F", controls={"C": FormControl("C", "TextBox", {"VisibleRule": (False, "=False")})})}),
    ("form control added", _diff_forms, compare_forms,
     {"F": Form("F", controls={"C": FormControl("C", "TextBox", {})})},
     {"F": Form("F", controls={"C": FormControl("C", "TextBox", {}),
                               "D": FormControl("D", "Label", {"Text": (True, "hi")})})}),
]


@pytest.mark.parametrize("label,json_fn,html_fn,old,new",
                         EQUIVALENCE_CASES, ids=[c[0] for c in EQUIVALENCE_CASES])
def test_json_stats_match_html_stats(label, json_fn, html_fn, old, new):
    _, html_stats = html_fn(old, new)
    _, json_stats = json_fn(old, new)
    assert json_stats == html_stats


# ---------- detail records ----------

def test_variable_formula_change_detail_carries_raw_values():
    records, _ = _diff_variables({"A": Variable("A", formula="=1")},
                                 {"A": Variable("A", formula="=2")})
    assert records == [{"name": "A", "status": "modified",
                        "details": [{"field": "formula", "status": "modified",
                                     "old": "=1", "new": "=2"}]}]


def test_added_and_removed_records_have_no_details():
    records, _ = _diff_variables({"Gone": Variable("Gone")}, {"New": Variable("New")})
    assert records == [{"name": "New", "status": "added"},
                       {"name": "Gone", "status": "removed"}]


def test_macro_reorder_detail():
    old = {"M": SpecMacro("M", tasks=[_mtask("=A", "First"), _mtask("=B", "Second")])}
    new = {"M": SpecMacro("M", tasks=[_mtask("=B", "Second"), _mtask("=A", "First")])}
    records, stats = _diff_spec_macros(old, new)
    assert stats["modified"] == 1
    assert records[0]["details"][0]["field"] == "(task order)"


def test_calc_table_detail_fields_name_column_and_scope():
    old = {"T": CalcTable("T", row_count=3, columns={"C": {"common": "=A*1", "rows": {}}})}
    new = {"T": CalcTable("T", row_count=5, columns={"C": {"common": "=A*2", "rows": {}}})}
    records, _ = _diff_calc_tables(old, new)
    fields = [d["field"] for d in records[0]["details"]]
    assert fields == ["(row count)", "C · Common"]


def test_lookup_details_identify_columns_and_rows():
    records, _ = _diff_lookup_tables({"L": "Key,Val\nA,1\nB,2"},
                                     {"L": "Key,Val,Extra\nA,9,x\nC,3,y"})
    fields = {(d["field"], d["status"]) for d in records[0]["details"]}
    assert ("(column) Extra", "added") in fields
    assert ("(row) A", "modified") in fields
    assert ("(row) C", "added") in fields
    assert ("(row) B", "removed") in fields


def test_form_details_name_control_and_property():
    old = {"F": Form("F", controls={"C": FormControl("C", "TextBox", {"VisibleRule": (False, "=True")})})}
    new = {"F": Form("F", controls={"C": FormControl("C", "TextBox", {"VisibleRule": (False, "=False")})})}
    records, _ = _diff_forms(old, new)
    assert records[0]["details"] == [{"field": "C · VisibleRule", "status": "modified",
                                      "old": "=True", "new": "=False"}]


# ---------- build_diff document ----------

def _sample_projects():
    old = DWProject(name="old")
    new = DWProject(name="new")
    old.variables = {"A": Variable("A", formula="=1"), "B": Variable("B", formula="=2")}
    new.variables = {"A": Variable("A", formula="=9"), "C": Variable("C", formula="=3")}
    old.constants = {"M": Constant("M", value="2")}
    new.constants = {"M": Constant("M", value="2")}
    return old, new


def test_build_diff_document_shape_and_summary():
    old, new = _sample_projects()
    doc = build_diff(old, new, "proj_v1", "proj_v2")

    assert doc["schema"] == 1
    assert doc["old_project"] == "proj_v1"
    assert doc["new_project"] == "proj_v2"
    assert doc["errors"] == []

    # Summary totals equal the sum of the per-category stats.
    cats = doc["summary"]["categories"]
    for key in ("added", "removed", "modified", "unchanged"):
        assert doc["summary"][key] == sum(s[key] for s in cats.values())

    assert cats["variables"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 0}
    assert cats["constants"] == {"added": 0, "removed": 0, "modified": 0, "unchanged": 1}

    # Every change record carries category/name/status; unchanged never listed.
    assert doc["changes"], "expected change records"
    for rec in doc["changes"]:
        assert rec["category"] in cats
        assert rec["status"] in ("added", "removed", "modified")


def test_build_diff_is_json_serializable():
    import json
    old, new = _sample_projects()
    doc = build_diff(old, new)
    round_tripped = json.loads(json.dumps(doc))
    assert round_tripped["summary"] == doc["summary"]


def test_build_diff_degrades_per_category_on_error(monkeypatch):
    # Mirror the HTML report's _safe behavior: one crashing category must not
    # take down the run, and must be flagged in "errors" so consumers can tell
    # missing data from no-changes.
    def boom(old, new):
        raise RuntimeError("bad data")
    patched = [(k, boom if k == "forms" else fn) for k, fn in jsondiff._CATEGORY_FUNCS]
    monkeypatch.setattr(jsondiff, "_CATEGORY_FUNCS", patched)

    old, new = _sample_projects()
    doc = build_diff(old, new)
    assert doc["errors"] == ["forms"]
    assert doc["summary"]["categories"]["forms"] == {"added": 0, "removed": 0,
                                                     "modified": 0, "unchanged": 0}
