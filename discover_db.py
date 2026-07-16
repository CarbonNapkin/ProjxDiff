"""
Find which table in a DriveWorks GROUP database maps a captured-component
reference (CCRef) or component id (TrId) to a readable name.

Run this ONCE per group, pointed at the group database, using the CCRef/TrId
values ProjxDiff extracted from the project files. It only issues SELECTs.

Usage (Windows integrated auth):
    python discover_db.py --server SQLBOX\\DWGROUP --database DriveWorksGroup \\
        --projx SSLF-DEV.driveprojx

What it does:
  1. lists tables that have both an id-ish and a name-ish column
  2. pulls the CCRef/TrId ids out of the .driveprojx
  3. for each candidate table, checks how many of those ids actually appear
     -> the table with the highest hit rate is your mapping table
  4. prints a ready-to-paste idmap.ID_SOURCES entry
"""

import argparse
import sys
import os
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "dw_compare"))
import dbsource            # noqa: E402
import components as C     # noqa: E402


def ids_from_projx(path: str) -> set:
    tmp = tempfile.mkdtemp(prefix="dw_disc_")
    with zipfile.ZipFile(path) as z:
        z.extractall(tmp)
    idx = C.build_component_index(tmp)
    return idx.all_lookup_keys()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--database", required=True)
    ap.add_argument("--user", default="")
    ap.add_argument("--sql-auth", action="store_true",
                    help="use SQL auth instead of Windows; password read from "
                         "the DW_SQL_PASSWORD environment variable")
    ap.add_argument("--projx", required=True, help=".driveprojx to pull test ids from")
    ap.add_argument("--min-hit", type=float, default=0.25, help="min id hit-rate to report a table")
    args = ap.parse_args()

    # Never take a password on the command line: it lands in shell history and
    # is visible in the process list. Windows integrated auth is the default and
    # needs no secret at all.
    password = os.environ.get("DW_SQL_PASSWORD", "")
    if args.sql_auth and not password:
        sys.exit("--sql-auth given but DW_SQL_PASSWORD is not set.\n"
                 "  PowerShell:  $env:DW_SQL_PASSWORD = Read-Host -AsSecureString\n"
                 "  or simply drop --sql-auth to use Windows authentication.")

    db = dbsource.DwDatabase(
        label="discover", server=args.server, database=args.database,
        user=args.user, password=password, trusted=not args.sql_auth,
    )
    if not db.connect():
        sys.exit("Could not connect. Check server/database/driver.")

    ids = ids_from_projx(args.projx)
    print(f"Pulled {len(ids)} candidate ids (CCRef/TrId) from {os.path.basename(args.projx)}\n")

    candidates = db.find_id_name_tables()
    print(f"{len(candidates)} table(s) have both an id-ish and a name-ish column.\n")

    # Sample the ids so we don't build a giant IN() while probing.
    probe = list(ids)[:400]
    results = []
    for c in candidates:
        for id_col in c["id_cols"]:
            for name_col in c["name_cols"]:
                got = db.lookup(c["table"], id_col, name_col, probe, schema=c["schema"])
                # lookup normalises nothing, but ids in DB may be hyphenated/upper;
                # match on normalised form for an honest hit-rate.
                norm_got = {C._norm(k) for k in got}
                hit = len(norm_got & {C._norm(i) for i in probe})
                rate = hit / max(1, len(probe))
                if rate >= args.min_hit:
                    results.append((rate, c["schema"], c["table"], id_col, name_col, hit))

    results.sort(reverse=True)
    if not results:
        print("No table matched. The id column may store hyphenated/uppercased")
        print("GUIDs, or the mapping may be keyed on a different column. Try")
        print("db.columns_of('<table>') and db.sample('<table>', id_col, name_col).")
        return

    print("Best mapping candidates (highest id hit-rate first):\n")
    for rate, schema, table, id_col, name_col, hit in results[:8]:
        print(f"  {rate*100:5.1f}%  {schema}.{table}  ({id_col} -> {name_col})  [{hit} ids]")

    rate, schema, table, id_col, name_col, _ = results[0]
    print("\nPaste into dw_compare/idmap.py ID_SOURCES:\n")
    print('    "component": IdSource(')
    print(f'        table="{table}", id_col="{id_col}", name_col="{name_col}",')
    print(f'        schema="{schema}", enabled=True),')


if __name__ == "__main__":
    main()
