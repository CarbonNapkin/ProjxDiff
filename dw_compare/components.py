"""
Model / component parsing for DriveWorks projects.

What we learned from real SSLF-DEV vs SSLF-PROD project files:

  * project.xml holds a <project:ComponentSets> block. Each <ComponentSet> has
    a human Name (e.g. "R1-Aluminum", "CRITICAL ENVIRONMENT") and an RId. These
    are the top-level model factories — names are FREE, no database needed.

  * componentTasks.xml Tasks carry a ComponentId (a 32-hex GUID). Most of those
    (59/72 in DEV) resolve to a <pcomp:PC> node inside components/N.xml, whose
    TrId == the ComponentId (dashes stripped). Each PC node also has a CCRef
    (captured-component reference). Neither TrId nor CCRef carries a static name
    in the files — the placed-component's real identity lives in the GROUP
    DATABASE. This is the part that needs the SQL connector.

  * The remaining ~14 ComponentIds are master-model macro tasks (sheet-metal
    gauge, DXF, etc.) whose ComponentId points at the driving model, again
    named only in the database.

  * IDs are STABLE across groups: 71/72 ComponentIds, 4/4 ComponentSet RIds and
    614/616 TrIds are shared between DEV and PROD. So name resolution is
    cosmetic (better labels) — it does NOT need to become part of the diff key,
    and parse_component_tasks does not need a structural rewrite.

Resolution priority for a task's ComponentId, best name wins:
    1. DB name for the CCRef it maps to           (needs connector)
    2. DB name for the ComponentId / TrId directly (needs connector)
    3. ComponentSet Name if the id is a set RId    (free, from XML)
    4. raw GUID                                    (always, last resort)
"""

from __future__ import annotations

import re
import glob
import os
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def _norm(guid: str) -> str:
    """DriveWorks writes the same id as '12023044-ae50-...' in one place and
    '12023044ae50...' in another, and RIds are prefixed with 'R'. Normalise so
    every id space compares apples to apples."""
    if not guid:
        return ""
    return guid.replace("-", "").strip().lower().lstrip("r")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class ComponentSet:
    """A named top-level model factory from project.xml. Name is free."""
    name: str
    rid: str
    set_type: str = ""          # PartFactory / AssemblyFactory
    rule: str = ""              # generation rule (already diffable as a formula)


@dataclass
class PropertyRule:
    """One driven rule on a captured component. Most commonly a <pcomp:PP>
    node - a driven property on a captured entity, what shows up in
    SolidWorks as something like D1@Sketch1. cp_ref is the property itself
    (the "D1"); ce_ref is the parent captured entity it belongs to (the
    "Sketch1"). Neither carries a static name in the files - same situation
    CCRef was in before CapturedComponents named it. owner_trid ties it back
    to which captured component (PC) it belongs to, so rules can be grouped
    and labeled per model.

    kind classifies what the rule actually drives:
      "dimension"      - a PP with a real (non-zero) ce_ref: D1@Sketch1-style
      "instance"       - a PP with an all-zero ce_ref: a component-level
                          custom property, not tied to a specific sub-entity
      "file_name"      - the PC's own <pcomp:CN> rule (what file name to use)
      "relative_path"  - the PC's own <pcomp:CP> rule (what folder to use)
      "tag"            - the PC's own <pcomp:CT> rule (component tag)
      "loop_control"   - the PC's own <pcomp:LC> rule (loop enable/disable)
    A "dimension" rule (real ce_ref) may get reclassified to "feature" at
    report time, in comparers.compare_property_rules — not here, since that
    needs the DB-resolved names: if only the entity half (ce_ref) resolves
    to a name and the property half (cp_ref) doesn't, it's a feature-level
    rule (e.g. bare "FaceHoleCenter"), not a dimension (both halves resolve,
    e.g. "D1@OrderSizeWidth"). PropertyRule.kind itself only ever gets set
    to "dimension" or "instance" by the parser below; "feature" only ever
    appears after that reclassification, and only when a database was
    supplied.

    file_name/relative_path/tag/loop_control rules have no CPRef/CERef of
    their own (there's exactly one per component, not per entity) - cp_ref
    and ce_ref are empty for those, and rule_id is synthesized from
    owner_trid instead of a real RId.

    owner_path is the full chain of ancestor TrIds from the top of the
    assembly down to owner_trid (inclusive). The same file can be placed
    more than once in a tree - e.g. the same hardware part used in four
    different sub-assemblies - and in that case they all share the same
    cp_ref/ce_ref (same dimension on the same captured file) but have
    different owner_path (different position in the tree) and different
    rule_id (a real, distinct rule instance per placement). Diffing or
    displaying by cp_ref/owner_trid alone would silently merge separate
    placements together; owner_path is what tells them apart for a human.
    """
    cp_ref: str
    ce_ref: str
    rule_id: str
    owner_trid: str
    owner_path: tuple = field(default_factory=tuple)
    formula: str = ""
    comment: str = ""
    kind: str = "dimension"


@dataclass
class PlacedComponent:
    """A <pcomp:PC> node: a captured component in the assembly tree.
    tr_id links it to the task's ComponentId; ccref is the DB lookup key."""
    tr_id: str
    ccref: str
    source_file: str = ""       # components/N.xml, useful for provenance
    name: str = ""              # filled in from the DB when available


_ZERO_CE = "0" * 32

# Human-readable Type column labels, keyed by PropertyRule.kind. "feature" is
# deliberately absent - see the kind docstring on PropertyRule for why it's
# not currently distinguishable from "dimension".
KIND_LABELS = {
    "dimension": "Dimension",
    "feature": "Feature",
    "instance": "Instance",
    "configuration": "Configuration",
    "file_name": "File Name",
    "relative_path": "Relative Path",
    "tag": "Tag",
    "loop_control": "Loop Control",
}


def property_label(pr, prop_names: dict = None) -> str:
    """Best readable label for a driven property, e.g. 'D1@Sketch1'.
    prop_names maps norm(id) -> name for both cp_ref and ce_ref ids (same
    shape as the model resolution dict) and may be empty or partial. A raw
    GUID isn't useful to a reader, so if only one half resolved, show just
    that one rather than pairing a real name with an unresolved id (e.g.
    'FaceHoleCenter', not '17cd347c-...@FaceHoleCenter'). Only falls back to
    a raw id if NEITHER half resolved, so the row still shows something.

    Only meaningful for kind in ("dimension", "instance") - cp_ref/ce_ref
    are empty for file_name/relative_path/tag/loop_control rules (see
    PropertyRule.kind), so callers should use KIND_LABELS[pr.kind] for those
    instead of calling this.
    """
    prop_names = prop_names or {}
    cp = _norm(pr.cp_ref)
    ce = _norm(pr.ce_ref)
    cp_name = prop_names.get(cp)
    ce_name = prop_names.get(ce) if (ce and ce != _ZERO_CE) else None
    if cp_name and ce_name:
        return f"{cp_name}@{ce_name}"
    if cp_name:
        return cp_name
    if ce_name:
        return ce_name
    return pr.cp_ref or pr.ce_ref or "(unnamed property)"



@dataclass
class ComponentIndex:
    """Everything needed to turn a task ComponentId into a readable name."""
    sets: dict = field(default_factory=dict)          # norm(rid)  -> ComponentSet
    placed: dict = field(default_factory=dict)        # norm(trid) -> PlacedComponent
    trid_to_ccref: dict = field(default_factory=dict) # norm(trid) -> norm(ccref)
    property_rules: list = field(default_factory=list)  # [PropertyRule, ...]

    # ---- resolution ----

    def all_lookup_keys(self) -> set:
        """Every id the DB might be asked to name (CCRefs + TrIds)."""
        keys = set(self.trid_to_ccref.values())
        keys |= set(self.placed.keys())
        return {k for k in keys if k}

    def all_property_keys(self) -> set:
        """Every CPRef/CERef a property-name table would need to resolve -
        the D1 / Sketch1 halves of a D1@Sketch1-style label."""
        keys = set()
        for pr in self.property_rules:
            if pr.cp_ref:
                keys.add(_norm(pr.cp_ref))
            if pr.ce_ref:
                keys.add(_norm(pr.ce_ref))
        return keys

    def label(self, component_id: str, db_names: dict = None) -> str:
        """Best readable label for a task's ComponentId. db_names maps
        norm(id) -> name and may be empty (no DB / unresolved)."""
        cid = _norm(component_id)
        if not cid:
            return ""
        db_names = db_names or {}
        # 1. DB name via the captured-component ref
        ccref = self.trid_to_ccref.get(cid)
        if ccref and db_names.get(ccref):
            return db_names[ccref]
        # 2. DB name keyed directly on the id (covers master-model tasks)
        if db_names.get(cid):
            return db_names[cid]
        # 3. named ComponentSet
        if cid in self.sets:
            return self.sets[cid].name
        # 4. raw guid, re-hyphenated for readability
        return component_id

    def breadcrumb(self, owner_path: tuple, db_names: dict = None) -> str:
        """Turn a PropertyRule's owner_path into a human path like
        'Top Assembly > Sub-Assembly 2 > H1Z25-FLX.SLDPRT' by labeling
        each ancestor TrId the same way a task's ComponentId is labeled.
        This is what tells two placements of the same file apart - they
        share a cp_ref/ce_ref but not a tree position."""
        parts = [self.label(trid, db_names) for trid in owner_path if trid]
        return " > ".join(p for p in parts if p)


# --------------------------------------------------------------------------

def parse_component_sets(project_xml_path: str) -> dict:
    """Free names: {norm(rid): ComponentSet} from project.xml."""
    out = {}
    try:
        tree = ET.parse(project_xml_path)
    except Exception as e:
        print(f"  Warning: could not read ComponentSets from {project_xml_path}: {e}")
        return out
    for el in tree.iter():
        if _local(el.tag) != "ComponentSet":
            continue
        name = el.get("Name", "")
        rid = el.get("RId", "")
        if not rid:
            continue
        rule = ""
        for child in el:
            if _local(child.tag) == "Rule" and child.text:
                rule = child.text.strip()
                break
        out[_norm(rid)] = ComponentSet(
            name=name, rid=rid,
            set_type=(el.get("Type", "").split(",")[0].rsplit(".", 1)[-1]),
            rule=rule,
        )
    return out


def parse_placed_components(components_dir: str) -> tuple:
    """Scan components/*.xml for PC nodes.
    Returns (placed{norm(trid): PlacedComponent}, trid_to_ccref{norm:norm})."""
    placed, t2c = {}, {}
    pc_re = re.compile(r'<pcomp:PC\b[^>]*?CCRef="([^"]*)"[^>]*?TrId="([^"]*)"')
    for f in sorted(glob.glob(os.path.join(components_dir, "*.xml"))):
        try:
            text = open(f, encoding="utf-8-sig").read()
        except Exception:
            continue
        base = os.path.basename(f)
        for ccref, trid in pc_re.findall(text):
            nt, nc = _norm(trid), _norm(ccref)
            if not nt:
                continue
            placed[nt] = PlacedComponent(tr_id=nt, ccref=nc, source_file=base)
            if nc:
                t2c[nt] = nc
    return placed, t2c


def parse_captured_data(raw) -> dict:
    """Decode ONE CapturedComponents.Data blob into {norm(id): name} for
    every named <ccomp:E> (entity) and <ccomp:P> (property) it defines.

    This is the actual source of D1@Sketch1-style names, confirmed
    against real project data: CPRef matches a ccomp:P element's Id;
    CERef matches a ccomp:E element's Id. CapturedComponents.Path only
    names the captured FILE itself - this per-row Data column is a small
    embedded XML document naming everything captured INSIDE that file.
    Entries with an empty N (unnamed) are skipped and fall back to their
    raw guid elsewhere, same as an unresolved id anywhere else in the tool.

    raw may be bytes (what a real pyodbc varbinary/image column hands
    back) or str (e.g. in tests). Anything that doesn't decode to XML
    fails soft to an empty dict rather than raising - one bad/corrupt
    blob shouldn't take down resolution for every other captured file.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return {}
    elif isinstance(raw, str):
        text = raw
    else:
        return {}

    text = text.strip()
    if not text.startswith("<"):
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}

    out = {}
    for el in root.iter():
        if _local(el.tag) in ("E", "P"):
            id_ = el.get("Id", "")
            name = el.get("N", "")
            if id_ and name:
                out[_norm(id_)] = name
    return out


# Confirmed by decoding a real CapturedComponents.Data blob (see git history
# / chat log for the raw hex): every <ccomp:E>/<ccomp:P> element carries a T
# attribute that's a STABLE type-classification GUID, constant per category
# - not a per-element identity GUID. This is the actual, authoritative
# signal for the Rule Changes Type column; it replaces every previous
# heuristic (ce_ref zero-ness, whether cp_ref resolves, formula content),
# all of which turned out to be proxies that broke on real data. Confirmed
# against real examples per category - cp_ref is always what gets looked up
# (not ce_ref/the entity's own T, which is a separate SolidWorks
# feature-type classifier, e.g. "this is a Cut feature" - irrelevant here):
#   Dimension: OrderWidth, OrderHeight, FaceHoleEndOffset (D1@.../D3@...) -
#     cp_ref points directly at a named <ccomp:P>
#   Feature:   FaceHoleCenter/FaceHoleEnd/FaceHoleEndPattern's suppress
#     rules - cp_ref points at the blank-named <ccomp:P> NESTED under the
#     feature <ccomp:E> (SH1/SH2/SH2PAT), not the entity's own T
#   Instance:  the "Instance Check" test rule - cp_ref resolves to
#     N="DiffFA5A-A1-1", proving a resolved cp_ref name does NOT imply
#     Dimension (a prior heuristic's exact failure case)
# "configuration" is a good-faith label for a 4th recurring GUID seen with a
# blank name/address, matching the "Configuration" row in a DriveWorks
# Administrator grid export - less directly confirmed than the other three,
# since that row had nothing to decode a name from either way.
TYPE_GUID_KIND = {
    "4ee71b52374c40f6a28fe97326eb46a4": "dimension",
    "d1d950c05a6a44e1b316a9a6ed3470d4": "feature",
    "7849e9c8e07146938b2636da17112d5c": "instance",
    "16512885644f463fb548b53e6df9ba67": "configuration",
}


def parse_captured_types(raw) -> dict:
    """Decode ONE CapturedComponents.Data blob into {norm(id): type_guid}
    for every <ccomp:E>/<ccomp:P> element's T attribute - the authoritative
    Dimension/Feature/Instance/etc. signal (see TYPE_GUID_KIND). Sibling to
    parse_captured_data, which extracts N (name) from the same elements;
    kept as a separate function/dict rather than merging into one richer
    structure so every existing {id: name} caller (property_label,
    compare_models, breadcrumbs, ...) is untouched. Same raw/failure
    handling as parse_captured_data - bytes or str in, empty dict on
    anything that doesn't decode.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return {}
    elif isinstance(raw, str):
        text = raw
    else:
        return {}

    text = text.strip()
    if not text.startswith("<"):
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}

    out = {}
    for el in root.iter():
        if _local(el.tag) in ("E", "P"):
            id_ = el.get("Id", "")
            type_guid = _norm(el.get("T", ""))
            if id_ and type_guid:
                out[_norm(id_)] = type_guid
    return out


def parse_property_rules(components_dir: str) -> list:
    """Walk components/*.xml for every driven rule on every captured
    component - both entity-level driven properties (what SolidWorks calls
    D1@Sketch1) and the four component-level rules DriveWorks attaches
    directly to a PC. Structure is nested and PE (captured entity) can
    nest inside another PE, with PP (captured property) nodes as siblings
    at any level:
    <PC TrId=... CCRef=...>       (a captured component, possibly with sub-<PC>s)
      <CN><R>=formula</R><C>comment</C></CN>   (file NAME rule)
      <CP><R>=formula</R><C>comment</C></CP>   (relative PATH rule)
      <CT><R>=formula</R><C>comment</C></CT>   (component TAG rule)
      <LC><R>=formula</R><C>comment</C></LC>   (LOOP control rule)
      <PE CERef=...>                (a captured entity, e.g. a feature)
        <PP CPRef=... RId=...>      (a captured property, e.g. dimension D1)
          <R>=formula</R>
          <C>comment</C>
        <PE CERef=...>              (a sub-entity, e.g. a sketch on a feature)
          <PP .../>
    A CERef of all-zeros means "the component itself" rather than a named
    sub-entity - its direct PP children are still real driven properties
    (e.g. component-level custom properties, kind="instance"), just not a
    specific D1@Sketch1-style dimension (kind="dimension"). CN/CP/CT/LC have
    no CPRef/RId of their own (exactly one per component, not per entity),
    so their rule_id is synthesized from owner_trid + kind instead.

    "feature" is not distinguished from "dimension" at this parsing stage -
    it's reclassified later, at report time, once a resolved name is
    available. See the kind docstring on PropertyRule for why.
    """
    out = []

    def read_formula_comment(el):
        formula, comment = "", ""
        for gc in el:
            gt = _local(gc.tag)
            if gt == "R" and gc.text:
                formula = gc.text.strip()
            elif gt == "C" and gc.text:
                comment = gc.text.strip()
        return formula, comment

    def walk_pe(el, owner_trid, owner_path, ce_ref):
        for child in el:
            tag = _local(child.tag)
            if tag == "PP":
                formula, comment = read_formula_comment(child)
                kind = "instance" if _norm(ce_ref) == _ZERO_CE else "dimension"
                out.append(PropertyRule(
                    cp_ref=child.get("CPRef", ""),
                    ce_ref=ce_ref,
                    rule_id=child.get("RId", ""),
                    owner_trid=owner_trid,
                    owner_path=owner_path,
                    formula=formula,
                    comment=comment,
                    kind=kind,
                ))
            elif tag == "PE":
                walk_pe(child, owner_trid, owner_path, child.get("CERef", ""))

    # Tag name -> (kind label, synthetic rule_id suffix).
    _COMPONENT_RULE_KINDS = {
        "CN": "file_name",
        "CP": "relative_path",
        "CT": "tag",
        "LC": "loop_control",
    }

    def walk_pc(el, owner_trid, owner_path):
        for child in el:
            tag = _local(child.tag)
            if tag == "PC":
                child_trid = _norm(child.get("TrId", "")) or owner_trid
                child_path = owner_path + (child_trid,)
                walk_pc(child, child_trid, child_path)
            elif tag == "PE":
                walk_pe(child, owner_trid, owner_path, child.get("CERef", ""))
            elif tag in _COMPONENT_RULE_KINDS:
                kind = _COMPONENT_RULE_KINDS[tag]
                formula, comment = read_formula_comment(child)
                out.append(PropertyRule(
                    cp_ref="",
                    ce_ref="",
                    # No real RId exists for these - there's exactly one per
                    # component per kind, so owner_trid+kind is already a
                    # stable, unique key without needing one.
                    rule_id=f"{owner_trid}:{tag}",
                    owner_trid=owner_trid,
                    owner_path=owner_path,
                    formula=formula,
                    comment=comment,
                    kind=kind,
                ))

    for f in sorted(glob.glob(os.path.join(components_dir, "*.xml"))):
        try:
            tree = ET.parse(f)
        except Exception as e:
            print(f"  Warning: could not read property rules from {f}: {e}")
            continue
        root = tree.getroot()
        # top-level PCs may sit directly under the root
        for child in root:
            if _local(child.tag) == "PC":
                top_trid = _norm(child.get("TrId", ""))
                walk_pc(child, top_trid, (top_trid,))
    return out


def build_component_index(project_folder: str) -> ComponentIndex:
    """project_folder is the extracted root that contains driveProj/. Robust to
    the file living at driveProj/ or the folder root."""
    # locate project.xml and components/
    proj_xml = None
    comp_dir = None
    for root, _dirs, files in os.walk(project_folder):
        if "project.xml" in files and proj_xml is None:
            proj_xml = os.path.join(root, "project.xml")
        if os.path.basename(root) == "components":
            comp_dir = root
    idx = ComponentIndex()
    if proj_xml:
        idx.sets = parse_component_sets(proj_xml)
    if comp_dir:
        idx.placed, idx.trid_to_ccref = parse_placed_components(comp_dir)
        idx.property_rules = parse_property_rules(comp_dir)
    return idx


def resolve_names(index: ComponentIndex, resolver) -> dict:
    """Ask the DB resolver to name every CCRef/TrId in one batched round.
    resolver is an idmap.IdResolver (or NullResolver). Returns {norm(id): name}.
    """
    keys = index.all_lookup_keys()
    if not keys or resolver is None or not getattr(resolver, "active", False):
        return {}
    names = {}
    # CCRef is the primary DB key; TrId/ComponentId is the fallback key.
    for kind in ("component", "model"):
        got = resolver.resolve(kind, keys)
        for k, v in got.items():
            names.setdefault(_norm(k), v)
    return names