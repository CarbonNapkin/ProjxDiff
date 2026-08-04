"""
Declarative ID -> name mapping, resolved against a DriveWorks group database.

The project XML refers to models/components by GUID. Those GUIDs are opaque in
a diff: "Component 8f3a-..." tells a reviewer nothing, and worse, the GUID is
baked into the diff *key* (see parsers.parse_component_tasks), so two databases
that assign different GUIDs to the same logical model produce a diff where
everything is removed-and-re-added.

This module turns GUIDs into names, and can optionally re-key on the resolved
name so the diff compares like with like across two groups.

FILL THIS IN: the ID_SOURCES table below is the only schema-specific part.
Run `--db-explore` (dbsource.find_id_name_tables) against a real group DB to
find the tables, then set them here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IdSource:
    """Where a given kind of ID becomes a real name."""
    table: str
    id_col: str
    name_col: str
    schema: str = "dbo"
    enabled: bool = False   # flipped True once table/columns are confirmed


# --------------------------------------------------------------------------
# The mapping. Keys are logical ID kinds used by the parsers.
#
# TODO(wade): confirm these against a live group database. Placeholders are
# left deliberately blank rather than guessed — a wrong table name here fails
# loudly at query time and silently produces nonsense names at review time,
# which is worse.
# --------------------------------------------------------------------------
ID_SOURCES: dict = {
    # ComponentTask.component_id -> master model / component name.
    # This is the big one: it's what makes "which model does this task drive?"
    # readable, and it's the ID currently embedded in the component-task key.
    "component": IdSource(table="", id_col="", name_col="", enabled=False),

    # If model files (SLDPRT/SLDASM) are tracked separately from components.
    "model": IdSource(table="", id_col="", name_col="", enabled=False),

    # Specification / project identity, if you want cross-group provenance.
    "project": IdSource(table="", id_col="", name_col="", enabled=False),
}


# --------------------------------------------------------------------------
# Group database schemas differ between DriveWorks releases, so mappings are
# additionally keyed by DriveWorks MAJOR version ("20", "21", "22", ...).
# Populate one entry per version actually deployed, from a
# scripts/db/discover_db.py run against a live group database of that
# version. Same no-guessing rule as above: absent means unconfirmed.
# --------------------------------------------------------------------------
ID_SOURCES_BY_DW_VERSION: dict = {
    # Field-tested baseline: DriveWorks 22 on SQL Server 2022 (16.0.1000.6) —
    # the file-level analysis in components.py and the SQL connector were
    # validated against that environment. Populate the "22" entry from a
    # discover_db.py --dw-version 22 run against that group database.
    # "22": {"component": IdSource(table="...", id_col="...", name_col="...",
    #                              enabled=True), ...},
}


def sources_for_version(dw_version: str = '') -> dict:
    """The ID mapping for a DriveWorks major version ('21', '21.2', 22, ...).
    Falls back to the default ID_SOURCES when the version is unknown or has
    no confirmed mapping yet."""
    key = str(dw_version).split('.')[0].strip()
    return ID_SOURCES_BY_DW_VERSION.get(key, ID_SOURCES)


class IdResolver:
    """Resolves IDs for ONE side of the diff (one project, one database)."""

    def __init__(self, db=None, sources: dict = None):
        self.db = db
        self.sources = sources if sources is not None else ID_SOURCES
        self.stats = {"resolved": 0, "unresolved": 0}

    @property
    def active(self) -> bool:
        return self.db is not None

    def resolve(self, kind: str, ids) -> dict:
        """Return {id: name} for a kind of ID. Empty dict if unavailable —
        callers must treat resolution as best-effort and fall back to the GUID.
        """
        ids = [i for i in ids if i]
        if not ids or not self.active:
            return {}
        src = self.sources.get(kind)
        if src is None or not src.enabled or not src.table:
            return {}
        mapping = self.db.lookup(
            table=src.table, id_col=src.id_col, name_col=src.name_col,
            ids=ids, schema=src.schema,
        )
        self.stats["resolved"] += len(mapping)
        self.stats["unresolved"] += len(set(map(str, ids)) - set(mapping))
        return mapping

    def name_for(self, kind: str, single_id: str, default: str = "") -> str:
        if not single_id:
            return default
        return self.resolve(kind, [single_id]).get(str(single_id), default or single_id)

    def report(self) -> str:
        if not self.active:
            return "  (no database attached — IDs shown as raw GUIDs)"
        r, u = self.stats["resolved"], self.stats["unresolved"]
        return f"  Resolved {r} ID(s) to names; {u} unresolved."


class NullResolver(IdResolver):
    """Used when no DB is supplied. Keeps call sites free of None-checks."""

    def __init__(self):
        super().__init__(db=None)

    def resolve(self, kind, ids):
        return {}


def label_for(resolver: "IdResolver", kind: str, raw_id: str, mapping: dict = None) -> str:
    """Display helper: prefer the resolved name, fall back to the raw GUID.

    Never returns empty — an unresolved ID must still be traceable, so the GUID
    survives when the DB can't name it.
    """
    if not raw_id:
        return ""
    if mapping and str(raw_id) in mapping:
        return mapping[str(raw_id)]
    return str(raw_id)
