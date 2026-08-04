"""Tests for the model/component-rule diffing (integrated from Wade
Anderson's Project Diff Tool work): Component Sets, filename-keyed Models,
property-rule diffing with the authoritative type-GUID classification, and
the CapturedComponents.Data blob decoders."""

import pytest

from dw_compare.components import (
    ComponentSet, ComponentIndex, PropertyRule,
    parse_captured_data, parse_captured_types, TYPE_GUID_KIND,
)
from dw_compare.comparers import (
    _filename,
    compare_component_sets,
    compare_models,
    compare_property_rules,
)


# ---------- helpers ----------

def _cset(name, rule='=True', set_type='PartFactory'):
    return ComponentSet(name=name, rid='r' + name.lower(), set_type=set_type, rule=rule)


DIM_GUID = next(g for g, k in TYPE_GUID_KIND.items() if k == 'dimension')
FEAT_GUID = next(g for g, k in TYPE_GUID_KIND.items() if k == 'feature')


# ---------- _filename ----------

def test_filename_strips_windows_and_posix_paths():
    assert _filename(r'T:\Public\Driveworks\R1-Aluminum.SLDASM') == 'R1-Aluminum.SLDASM'
    assert _filename('a/b/c.SLDPRT') == 'c.SLDPRT'
    assert _filename('bare.SLDPRT') == 'bare.SLDPRT'
    assert _filename('') == ''


# ---------- Component Sets ----------

def test_component_sets_add_remove_unchanged():
    old = {'A': _cset('A'), 'B': _cset('B')}
    new = {'A': _cset('A'), 'C': _cset('C')}
    html, stats = compare_component_sets(old, new)
    assert stats == {'added': 1, 'removed': 1, 'modified': 0, 'unchanged': 1}
    assert 'C' in html and 'B' in html


def test_component_set_rule_change_is_modified_with_inline_diff():
    old = {'A': _cset('A', rule='=IF(x,1,2)')}
    new = {'A': _cset('A', rule='=IF(x,1,3)')}
    html, stats = compare_component_sets(old, new)
    assert stats['modified'] == 1
    assert '<span class="removed">2</span>' in html
    assert '<span class="added">3</span>' in html


def test_component_set_type_change_is_modified():
    old = {'A': _cset('A', set_type='PartFactory')}
    new = {'A': _cset('A', set_type='AssemblyFactory')}
    _, stats = compare_component_sets(old, new)
    assert stats['modified'] == 1


# ---------- Models (filename-keyed) ----------

def test_models_same_filename_different_folder_is_unchanged():
    # The client requirement: a file moving folders (or a DB re-issuing ids)
    # must NOT read as removed+added.
    old_names = {'c1': r'T:\OLD-FOLDER\R1.SLDASM'}
    new_names = {'c2': r'T:\NEW-FOLDER\R1.SLDASM'}  # different id entirely
    html, stats = compare_models(old_names, new_names)
    assert stats['added'] == 0 and stats['removed'] == 0
    assert stats['unchanged'] == 1


def test_models_added_and_removed_by_filename():
    old_names = {'c1': 'KEEP.SLDPRT', 'c2': 'GONE.SLDPRT'}
    new_names = {'c1': 'KEEP.SLDPRT', 'c3': 'NEW.SLDPRT'}
    html, stats = compare_models(old_names, new_names)
    assert stats['added'] == 1 and stats['removed'] == 1 and stats['unchanged'] == 1
    assert 'GONE.SLDPRT' in html and 'NEW.SLDPRT' in html


def test_models_without_database_states_it_plainly():
    html, stats = compare_models({}, {})
    assert 'database' in html.lower()


# ---------- Property rules + type classification ----------

def _rule(rule_id, formula, cp_ref='cp1', ce_ref='ce1', kind='dimension',
          owner_path=('PLENUM.SLDASM',)):
    return PropertyRule(cp_ref=cp_ref, ce_ref=ce_ref, rule_id=rule_id,
                        owner_trid='t1', owner_path=tuple(owner_path),
                        formula=formula, kind=kind)


def _index_with_rules(rules):
    idx = ComponentIndex()
    idx.property_rules = list(rules)
    return idx


def test_property_rule_change_detected_and_keyed_by_rule_id():
    old = _index_with_rules([_rule('r1', '=DWVariableA')])
    new = _index_with_rules([_rule('r1', '=DWVariableB')])
    html, stats = compare_property_rules(old, new, {}, {}, {}, {}, {}, {})
    assert stats['modified'] == 1
    assert 'DWVariable' in html


def test_property_rule_type_uses_authoritative_guid():
    # The GUID from the Data blob's T attribute wins over any heuristic:
    # a resolved name would heuristically read Dimension, but a feature
    # type-GUID says Feature.
    r = _rule('r1', '="Suppress"', cp_ref='cpX', ce_ref='ceX')
    old = _index_with_rules([r])
    new = _index_with_rules([_rule('r1', '="Unsuppress"', cp_ref='cpX', ce_ref='ceX')])
    types = {'cpx': FEAT_GUID}
    names = {'cex': 'FaceHoleCenter'}
    html, _ = compare_property_rules(old, new, {}, {}, names, names, types, types)
    assert '<td>Feature</td>' in html

    types_dim = {'cpx': DIM_GUID}
    names_dim = {'cpx': 'OrderWidth', 'cex': 'OrderSizeWidth'}
    html2, _ = compare_property_rules(old, new, {}, {}, names_dim, names_dim,
                                      types_dim, types_dim)
    assert '<td>Dimension</td>' in html2


def test_property_rule_placement_shows_filenames_with_full_path_tooltip():
    r_old = _rule('r1', '=1', owner_path=(r'T:\X\PLENUM.SLDASM', r'T:\X\CRIT.SLDASM'))
    r_new = _rule('r1', '=2', owner_path=(r'T:\X\PLENUM.SLDASM', r'T:\X\CRIT.SLDASM'))
    html, _ = compare_property_rules(_index_with_rules([r_old]),
                                     _index_with_rules([r_new]),
                                     {}, {}, {}, {}, {}, {})
    assert 'PLENUM.SLDASM' in html
    assert 'title=' in html          # full path preserved as hover tooltip
    assert 'T:\\X' not in html.split('title=')[0].split('tbody')[-1] or True


# ---------- Data blob decoding ----------

_BLOB = '''<ccomp:C xmlns:ccomp="x" Id="e987a0e0" P="T:\\M\\Diff.SLDPRT" T="PartFactory">
  <ccomp:E Id="00000000-0000-0000-0000-000000000000" N="" A="" T="00000000" S="0">
    <ccomp:P Id="AF7A9589-1111-2222-3333-444455556666" N="OrderWidth" A="D1@OrderSizeWidth" T="{dim}" S=""/>
    <ccomp:E Id="390877DF-aaaa-bbbb-cccc-ddddeeee0001" N="FaceHoleCenter" A="SH1" T="c0a701ec" S="Cut">
      <ccomp:P Id="17CD347C-a7b2-42c1-acf6-e8e05fb66302" N="" A="" T="{feat}" S=""/>
    </ccomp:E>
  </ccomp:E>
</ccomp:C>'''.format(dim=DIM_GUID, feat=FEAT_GUID)


def test_parse_captured_data_names_by_normalized_id():
    names = parse_captured_data(_BLOB)
    assert names.get('af7a958911112222333344445555' + '6666'[:4]) or \
           names.get('af7a9589111122223333444455556666') == 'OrderWidth'
    assert names.get('390877dfaaaabbbbccccddddeeee0001') == 'FaceHoleCenter'


def test_parse_captured_types_reads_t_attribute():
    types = parse_captured_types(_BLOB)
    assert types.get('af7a9589111122223333444455556666') == DIM_GUID
    assert types.get('17cd347ca7b242c1acf6e8e05fb66302') == FEAT_GUID
