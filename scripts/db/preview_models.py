"""
Model-level preview — NO DATABASE REQUIRED.

Shows what ProjxDiff currently misses at the model/component level, using only
what's inside the .driveprojx files. Run this first: it proves the plumbing
works before you touch SQL, and it already finds real differences.

Usage:
    python preview_models.py SSLF-PROD.driveprojx SSLF-DEV.driveprojx

Reports:
  * Component Sets (named model factories) added / removed / rule-changed
  * How many component-task GUIDs would become readable names with a DB
  * Whether the ids are stable across the two groups (they should be)
"""

import sys
import os
import re
import tempfile
import zipfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dw_compare import components as C  # noqa: E402


def unzip(path: str) -> str:
    tmp = tempfile.mkdtemp(prefix="dw_preview_")
    with zipfile.ZipFile(path) as z:
        z.extractall(tmp)
    return tmp


def task_component_ids(root: str) -> list:
    """Every ComponentId referenced by a component task."""
    for dirpath, _d, files in os.walk(root):
        if "componentTasks.xml" in files:
            text = open(os.path.join(dirpath, "componentTasks.xml"), encoding="utf-8-sig").read()
            return re.findall(r'ComponentId="([^"]+)"', text)
    return []


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python preview_models.py OLD.driveprojx NEW.driveprojx")

    old_path, new_path = sys.argv[1], sys.argv[2]
    tmps = []
    try:
        old_root, new_root = unzip(old_path), unzip(new_path)
        tmps = [old_root, new_root]

        old_idx = C.build_component_index(old_root)
        new_idx = C.build_component_index(new_root)

        old_name = os.path.basename(old_path)
        new_name = os.path.basename(new_path)

        print("=" * 68)
        print(f"MODEL PREVIEW   old={old_name}   new={new_name}")
        print("=" * 68)

        # ---- Component Sets: names are free, straight from project.xml ----
        old_sets = {s.name: s for s in old_idx.sets.values()}
        new_sets = {s.name: s for s in new_idx.sets.values()}
        added = sorted(set(new_sets) - set(old_sets))
        removed = sorted(set(old_sets) - set(new_sets))
        common = sorted(set(old_sets) & set(new_sets))

        print("\n-- COMPONENT SETS (named model factories) --")
        for n in added:
            print(f"  + ADDED    {n}  ({new_sets[n].set_type})")
        for n in removed:
            print(f"  - REMOVED  {n}  ({old_sets[n].set_type})")
        for n in common:
            o, w = old_sets[n], new_sets[n]
            if o.rule != w.rule:
                print(f"  ~ MODIFIED {n}  (generation rule changed)")
            elif o.set_type != w.set_type:
                print(f"  ~ MODIFIED {n}  ({o.set_type} -> {w.set_type})")
            else:
                print(f"    same     {n}")
        if not (added or removed):
            print("  (no sets added or removed)")

        # ---- Component tasks: what the DB would buy you ----
        old_cids = task_component_ids(old_root)
        new_cids = task_component_ids(new_root)
        o_uniq = {C._norm(c) for c in old_cids if c}
        n_uniq = {C._norm(c) for c in new_cids if c}

        print("\n-- COMPONENT TASK IDs --")
        print(f"  old: {len(o_uniq)} unique   new: {len(n_uniq)} unique   shared: {len(o_uniq & n_uniq)}")
        only_new = n_uniq - o_uniq
        only_old = o_uniq - n_uniq
        for cid in sorted(only_new):
            print(f"  + only in new: {cid}  -> {new_idx.label(cid)}")
        for cid in sorted(only_old):
            print(f"  - only in old: {cid}  -> {old_idx.label(cid)}")

        stable = len(o_uniq & n_uniq) / max(1, len(o_uniq | n_uniq))
        print(f"\n  ID stability across groups: {stable*100:.1f}%")
        if stable > 0.9:
            print("  -> STABLE. Names are cosmetic; the diff key is safe as-is.")
        else:
            print("  -> UNSTABLE. Resolved names must become part of the diff key.")

        # ---- what still needs SQL ----
        resolvable_free = sum(1 for c in n_uniq if c in new_idx.sets)
        needs_db = len(n_uniq) - resolvable_free
        print("\n-- NAME RESOLUTION --")
        print(f"  nameable from XML alone (ComponentSets): {resolvable_free}")
        print(f"  still raw GUIDs, need the group DB:      {needs_db}")
        print(f"  ids the DB would be asked to name:       {len(new_idx.all_lookup_keys())}")
        print("\n  Sample of what stays unreadable without SQL:")
        for cid in sorted(n_uniq - set(new_idx.sets))[:3]:
            ccref = new_idx.trid_to_ccref.get(cid, "(no ccref)")
            print(f"    {cid}  ccref={ccref}")
        print()

    finally:
        for t in tmps:
            shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    main()
