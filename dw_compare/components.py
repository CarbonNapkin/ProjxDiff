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
class PlacedComponent:
    """A <pcomp:PC> node: a captured component in the assembly tree.
    tr_id links it to the task's ComponentId; ccref is the DB lookup key."""
    tr_id: str
    ccref: str
    source_file: str = ""       # components/N.xml, useful for provenance
    name: str = ""              # filled in from the DB when available


@dataclass
class ComponentIndex:
    """Everything needed to turn a task ComponentId into a readable name."""
    sets: dict = field(default_factory=dict)          # norm(rid)  -> ComponentSet
    placed: dict = field(default_factory=dict)        # norm(trid) -> PlacedComponent
    trid_to_ccref: dict = field(default_factory=dict) # norm(trid) -> norm(ccref)

    # ---- resolution ----

    def all_lookup_keys(self) -> set:
        """Every id the DB might be asked to name (CCRefs + TrIds)."""
        keys = set(self.trid_to_ccref.values())
        keys |= set(self.placed.keys())
        return {k for k in keys if k}

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
