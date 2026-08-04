"""
Read-only SQL connectors for resolving DriveWorks IDs to human-readable names.

Two independent connections are supported (one per side of the diff), because
the old/new projects usually live in different groups — prod vs dev — and each
has its own database.

Design rules:
  * READ ONLY. Only SELECT is ever issued; anything else raises.
  * FAIL SOFT. Any connection/query error degrades to "no names resolved" and
    the diff still runs with raw GUIDs, matching the tool's existing behaviour
    of warning-and-continuing rather than dying.
  * BATCHED. IDs are resolved with chunked `WHERE id IN (...)` queries and
    cached, so a project with 900 component tasks costs a couple of queries,
    not 900 round trips.
  * NO STORED CREDENTIALS. Connection details arrive at runtime. Prefer Windows
    integrated auth (trusted=True); if you must use SQL auth, pass the password
    in from an env var rather than committing it.
"""

from __future__ import annotations

import base64
import re
import uuid
from typing import Iterable

# Identifiers (table/column names) cannot be passed as query parameters, so any
# identifier coming from config is validated against this before interpolation.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*$")

# Newest first; DriveWorks boxes almost always have at least one of these.
_DRIVER_CANDIDATES = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
]

_CHUNK = 500  # max ids per IN() clause

# The files write ids as dashless lowercase 32-hex; databases key the same
# values as uniqueidentifier (which REQUIRES hyphenated form — a dashless
# string fails conversion and kills the whole query) or as NVARCHAR in either
# form. lookup() therefore queries hyphenated first, dashless second, and
# matches results by normalized value, so callers can pass any format.
_GUID32 = re.compile(r'^[0-9a-fA-F]{32}$')


def _norm_id(s) -> str:
    return str(s).replace('-', '').strip().lower()


def _hyphenated(norm32: str) -> str:
    return (f'{norm32[0:8]}-{norm32[8:12]}-{norm32[12:16]}-'
            f'{norm32[16:20]}-{norm32[20:32]}')


# A GUID's string form and its .NET/SQL byte form differ: Guid.ToByteArray()
# (= uniqueidentifier's binary layout) stores the first three fields
# little-endian, so the same value can legitimately appear as
# 00112233-... in the XML and 33221100-... (or its base64) in a database
# column. `encoding` names how the DB stores the file-side value:
#   'plain'   — same value, any plain format          (default)
#   'swapped' — .NET byte-order hex                    (bytes_le)
#   'base64'  — base64 of the .NET byte order
# discover_db.py probes all three and reports which one actually hits.
GUID_ENCODINGS = ('plain', 'swapped', 'base64')


def _encode_guid(norm32: str, encoding: str) -> str:
    if encoding == 'swapped':
        return uuid.UUID(hex=norm32).bytes_le.hex()
    if encoding == 'base64':
        return base64.b64encode(uuid.UUID(hex=norm32).bytes_le).decode('ascii')
    return norm32


def _quote_ident(name: str) -> str:
    """Validate then bracket-quote a SQL identifier."""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"Unsafe or empty SQL identifier: {name!r}")
    return "[" + name.replace("]", "]]") + "]"


def available_drivers() -> list:
    try:
        import pyodbc
        return list(pyodbc.drivers())
    except Exception:
        return []


def pick_driver() -> str | None:
    have = set(available_drivers())
    for d in _DRIVER_CANDIDATES:
        if d in have:
            return d
    return None


class DwDatabase:
    """A single read-only connection to one DriveWorks group database."""

    def __init__(
        self,
        label: str,
        server: str = "",
        database: str = "",
        user: str = "",
        password: str = "",
        trusted: bool = True,
        conn_str: str = "",
        timeout: int = 10,
        encrypt: bool = False,
    ):
        self.label = label            # "old" / "new", used in log lines
        self.server = server
        self.database = database
        self.user = user
        self._password = password
        self.trusted = trusted
        self.timeout = timeout
        self.encrypt = encrypt
        self._explicit_conn_str = conn_str
        self._conn = None
        self._cache: dict = {}        # (schema, table, id_col, name_col) -> {id: name}
        self._dead = False            # set after a failure; stops retry storms

    # ---------- connection ----------

    def _build_conn_str(self) -> str:
        if self._explicit_conn_str:
            return self._explicit_conn_str
        driver = pick_driver()
        if not driver:
            raise RuntimeError(
                "No SQL Server ODBC driver found. Install 'ODBC Driver 17 for SQL Server'."
            )
        parts = [f"DRIVER={{{driver}}}", f"SERVER={self.server}", f"DATABASE={self.database}"]
        if self.trusted:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.user}")
            parts.append(f"PWD={self._password}")
        # Driver 18 defaults to Encrypt=yes and will refuse self-signed certs,
        # which is the usual "it worked in SSMS but not here" trap on LAN boxes.
        parts.append("Encrypt=yes" if self.encrypt else "Encrypt=no")
        parts.append("TrustServerCertificate=yes")
        return ";".join(parts) + ";"

    def connect(self) -> bool:
        """Open the connection. Returns True on success, False on any failure."""
        if self._conn is not None:
            return True
        if self._dead:
            return False
        try:
            import pyodbc
        except ImportError:
            print(f"  [!] [{self.label}] pyodbc not installed -- ID names will stay as raw GUIDs.")
            self._dead = True
            return False
        try:
            conn = pyodbc.connect(self._build_conn_str(), timeout=self.timeout, readonly=True)
            conn.timeout = self.timeout
            self._conn = conn
            return True
        except Exception as e:
            print(f"  [!] [{self.label}] Could not connect: {e}")
            print(f"  [!] [{self.label}] Continuing with unresolved IDs.")
            self._dead = True
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------- querying ----------

    def _select(self, sql: str, params: Iterable = ()) -> list:
        rows = self._try_select(sql, params)
        return rows if rows is not None else []

    def _try_select(self, sql: str, params: Iterable = ()):
        """Like _select, but distinguishes failure (None) from no rows ([]) —
        lookup() uses this to stop retrying query shapes a table's column
        type can never satisfy."""
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ValueError("DwDatabase issues SELECT statements only.")
        if not self.connect():
            return None
        try:
            cur = self._conn.cursor()
            cur.execute(sql, *params) if params else cur.execute(sql)
            rows = cur.fetchall()
            cur.close()
            return rows
        except Exception as e:
            print(f"  [!] [{self.label}] Query failed: {e}")
            return None

    def lookup(
        self,
        table: str,
        id_col: str,
        name_col: str,
        ids: Iterable,
        schema: str = "dbo",
        encoding: str = "plain",
    ) -> dict:
        """Resolve {id: name} for the given ids. Unknown ids are simply absent.

        GUID-format agnostic: ids may arrive dashless (as the project files
        write them) or hyphenated (as databases return them); matching is by
        normalized value, and the returned keys are the ids as the caller
        passed them. `encoding` names how the DB *stores* the file-side value
        (see GUID_ENCODINGS): 'swapped' handles the .NET/uniqueidentifier
        byte-order difference, 'base64' its base64 form. Results are cached
        (by normalized requested id) per (schema, table, id_col, name_col,
        encoding), so repeated calls only query ids not already seen.
        """
        wanted = {str(i).strip() for i in ids if i and str(i).strip()}
        if not wanted:
            return {}

        ck = (schema, table, id_col, name_col, encoding)
        cache = self._cache.setdefault(ck, {})   # norm(requested id) -> name/None
        by_norm: dict = {}
        for w in wanted:
            by_norm.setdefault(_norm_id(w), []).append(w)

        todo = sorted(n for n in by_norm if n not in cache)
        if todo:
            t = f"{_quote_ident(schema)}.{_quote_ident(table)}"
            ic, nc = _quote_ident(id_col), _quote_ident(name_col)
            guids = [n for n in todo if _GUID32.match(n)]
            other = [by_norm[n][0] for n in todo if not _GUID32.match(n)]

            if guids and encoding == 'base64':
                qmap = {}
                vals = []
                for n in guids:
                    q = _encode_guid(n, 'base64')
                    qmap[_norm_id(q)] = n
                    vals.append(q)
                self._lookup_chunks(t, ic, nc, ck, 'base64', vals, cache, qmap)
            elif guids:
                # query-form 32-hex -> requested norm ('plain' is identity)
                qmap = {_encode_guid(n, encoding): n for n in guids}
                # Pass 1: hyphenated form — the ONLY format a uniqueidentifier
                # column accepts (dashless kills the whole query with a
                # conversion error), and it also matches hyphenated NVARCHAR
                # under the default case-insensitive collation.
                self._lookup_chunks(t, ic, nc, ck, 'hyphenated',
                                    [_hyphenated(q) for q in qmap], cache, qmap)
                # Pass 2: dashless form for whatever is still unresolved —
                # matches NVARCHAR storing bare 32-hex. Skipped for this
                # table's lifetime once the column type provably rejects it.
                still = [q for q, n in qmap.items() if n not in cache]
                self._lookup_chunks(t, ic, nc, ck, 'dashless', still, cache, qmap)
            if other:
                self._lookup_chunks(t, ic, nc, ck, 'raw', other, cache,
                                    {_norm_id(o): _norm_id(o) for o in other})

            # Negative-cache misses so we don't re-query ids this DB lacks.
            for n in todo:
                cache.setdefault(n, None)

        return {w: cache[n] for n, ws in by_norm.items() if cache.get(n)
                for w in ws}

    def _lookup_chunks(self, t: str, ic: str, nc: str, ck, shape: str,
                       values: list, cache: dict, qmap: dict) -> None:
        """Run chunked IN() queries for one id format ('hyphenated',
        'dashless', 'base64', 'raw'). Returned rows are translated back to
        the *requested* id via qmap {normalized query form -> requested norm}
        and stored in the cache. A format that errors (e.g. dashless strings
        against a uniqueidentifier column) is remembered and never retried
        for this table."""
        if not hasattr(self, '_dead_shapes'):
            self._dead_shapes = set()
        if not values or (ck, shape) in self._dead_shapes:
            return
        for i in range(0, len(values), _CHUNK):
            chunk = values[i:i + _CHUNK]
            marks = ",".join("?" * len(chunk))
            sql = f"SELECT {ic}, {nc} FROM {t} WHERE {ic} IN ({marks})"
            rows = self._try_select(sql, (chunk,))
            if rows is None:
                self._dead_shapes.add((ck, shape))
                return
            for row in rows:
                if row[0] is None:
                    continue
                requested = qmap.get(_norm_id(row[0]))
                if requested is not None:
                    cache[requested] = (
                        "" if row[1] is None else str(row[1])).strip()

    # ---------- discovery ----------
    # Used by `--db-explore` to find which tables actually hold the ID -> name
    # mapping, without guessing at schema.

    def list_tables(self) -> list:
        rows = self._select(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        return [(str(r[0]), str(r[1])) for r in rows]

    def columns_of(self, table: str, schema: str = "dbo") -> list:
        rows = self._select(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME=? AND TABLE_SCHEMA=? ORDER BY ORDINAL_POSITION",
            ([table, schema],),
        )
        return [(str(r[0]), str(r[1])) for r in rows]

    def find_id_name_tables(self) -> list:
        """Candidate tables that have both an id-ish and a name-ish column.

        This is the practical way to locate the mapping tables: rather than
        trusting documentation, ask the live database which tables could
        plausibly turn a GUID into a name.
        """
        rows = self._select(
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        by_table: dict = {}
        for s, t, c, d in ((str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows):
            by_table.setdefault((s, t), []).append((c, d))

        out = []
        for (s, t), cols in sorted(by_table.items()):
            names = [c for c, _ in cols]
            id_cols = [c for c in names if re.search(r"(^|_)(id|guid|uniqueid)$", c, re.I)]
            name_cols = [c for c in names if re.search(r"name|title|caption", c, re.I)]
            if id_cols and name_cols:
                out.append({"schema": s, "table": t, "id_cols": id_cols, "name_cols": name_cols})
        return out

    def sample(self, table: str, id_col: str, name_col: str, schema: str = "dbo", n: int = 5) -> list:
        t = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        sql = f"SELECT TOP {int(n)} {_quote_ident(id_col)}, {_quote_ident(name_col)} FROM {t}"
        return [(str(r[0]), str(r[1])) for r in self._select(sql)]


def open_pair(old_cfg: dict, new_cfg: dict):
    """Build the two connectors — one per side of the diff.

    Each cfg is a dict of DwDatabase kwargs. Either side may be None/empty to
    run that side without name resolution.
    """
    old_db = DwDatabase(label="old", **old_cfg) if old_cfg else None
    new_db = DwDatabase(label="new", **new_cfg) if new_cfg else None
    return old_db, new_db
