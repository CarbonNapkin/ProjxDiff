#!/usr/bin/env python3
"""Does a .driveprojx carry a stable project identity that survives a rename?

Same no-guessing rule as scripts/db/discover_db.py and idmap.ID_SOURCES: we
confirm against real files rather than hardcode an attribute name that would
fail silently at review time.

DriveWorks rewrites the file on save, so diffing two files tells you nothing.
Instead this extracts every GUID-shaped value with its location, then checks
each candidate against a truth table across a set of labelled samples.

Stage 1 -- does a project-level GUID exist at all? One real file answers it:

    python scripts/probe_project_id.pyprobe /path/to/Real.driveprojx

Stage 2 -- is it stable across a rename? Produce the samples below, then:

    python scripts/probe_project_id.pycompare \
        base=A.driveprojx \
        resaved=A_resaved.driveprojx \
        renamed=A_renamed.driveprojx \
        moved=A_moved.driveprojx \
        other=B.driveprojx \
        recreated=A_recreated.driveprojx

Labels and what each one proves (all optional except base; whatever is
missing is reported as untested rather than silently assumed):

    base       the baseline project
    resaved    base opened, trivially edited, saved -- NOT renamed.
               Must MATCH base, else the candidate is a per-save revision id.
    renamed    base renamed (Data Management, or Windows rename + DM remap).
               Must MATCH base -- this is the hypothesis under test.
    moved      base moved to another folder + DM remap. Must MATCH base.
    other      an unrelated project. Must DIFFER, else the candidate is a
               schema constant, not an identity.
    recreated  a NEW project created from scratch, given base's name.
               Must DIFFER, else the candidate is derived from the name.

Accepts either a .driveprojx (zip) or an already-extracted project folder.
Stdlib only; runs anywhere the app runs.
"""

from __future__ import annotations

import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

GUID_RE = re.compile(
    r'^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
    r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$')

# Members whose GUIDs are placement/component churn, not project identity.
# Excluded from stage 2 only (stage 1 still lists them) to keep the candidate
# set readable -- a real project has thousands of component GUIDs.
NOISY_MEMBERS = ('components/', 'componenttasks.xml')


def _norm_guid(v: str) -> str:
    return v.strip().strip('{}').lower()


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _members(target: Path) -> dict:
    """{member_name: xml_bytes} from a .driveprojx or an extracted folder."""
    out = {}
    if target.is_dir():
        for p in sorted(target.rglob('*')):
            if p.is_file() and p.suffix.lower() in ('.xml', '.tdm'):
                out[p.relative_to(target).as_posix()] = p.read_bytes()
        return out
    with zipfile.ZipFile(target, 'r') as zf:
        for name in sorted(zf.namelist()):
            if name.lower().endswith(('.xml', '.tdm')):
                out[name] = zf.read(name)
    return out


def _walk(el: ET.Element, path: str, depth: int, found: list) -> None:
    """Record every GUID-shaped attribute value and element text, keyed by
    structural location so the same slot can be compared across files."""
    counts = defaultdict(int)
    for attr, val in el.attrib.items():
        if GUID_RE.match(val or ''):
            found.append((f'{path}@{_local(attr)}', _norm_guid(val), depth))
    text = (el.text or '').strip()
    if GUID_RE.match(text):
        found.append((f'{path}#text', _norm_guid(text), depth))
    for child in el:
        name = _local(child.tag)
        idx = counts[name]
        counts[name] += 1
        _walk(child, f'{path}/{name}[{idx}]', depth + 1, found)


def extract(target: Path, skip_noisy: bool = False) -> dict:
    """{location_key: guid} for one project."""
    result = {}
    for member, data in _members(target).items():
        low = member.lower()
        if skip_noisy and any(n in low for n in NOISY_MEMBERS):
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            print(f'  ! {member}: unparseable ({exc})', file=sys.stderr)
            continue
        found = []
        _walk(root, f'{member}:/{_local(root.tag)}', 0, found)
        for key, guid, _depth in found:
            result.setdefault(key, guid)
    return result


# ------------------------------------------------------------------ probe ---

def probe(target: Path) -> int:
    print(f'== {target}\n')

    if target.is_dir():
        names = [p.relative_to(target).as_posix()
                 for p in sorted(target.rglob('*')) if p.is_file()]
    else:
        with zipfile.ZipFile(target, 'r') as zf:
            names = sorted(zf.namelist())
    print(f'-- {len(names)} member(s):')
    for n in names[:60]:
        print(f'   {n}')
    if len(names) > 60:
        print(f'   ... and {len(names) - 60} more')

    # Root-element attributes are where a project id would most plausibly
    # live, so show them in full regardless of whether they look like GUIDs.
    print('\n-- root element of each XML member:')
    for member, data in _members(target).items():
        if any(n in member.lower() for n in NOISY_MEMBERS):
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        print(f'   {member}: <{_local(root.tag)}>')
        for attr, val in root.attrib.items():
            mark = '  <== GUID' if GUID_RE.match(val or '') else ''
            print(f'       @{_local(attr)} = {val!r}{mark}')
        if not root.attrib:
            print('       (no attributes)')

    shallow = {k: v for k, v in extract(target, skip_noisy=True).items()
               if k.count('/') <= 3}
    print(f'\n-- {len(shallow)} shallow GUID candidate(s) '
          f'(depth <= 3, excluding component churn):')
    for key, guid in sorted(shallow.items()):
        print(f'   {guid}  {key}')
    if not shallow:
        print('   (none -- if this holds for a real project, there is no '
              'project-level GUID and the rename design needs the '
              'heuristic branch)')
    return 0


# ---------------------------------------------------------------- compare ---

# label -> must the candidate match the baseline?
CONSTRAINTS = {
    'resaved': True, 'renamed': True, 'moved': True,
    'other': False, 'recreated': False,
}


def compare(samples: dict) -> int:
    if 'base' not in samples:
        print('error: a base=FILE sample is required', file=sys.stderr)
        return 2

    extracted = {label: extract(path, skip_noisy=True)
                 for label, path in samples.items()}
    base = extracted['base']
    tested = [l for l in CONSTRAINTS if l in extracted]
    missing = [l for l in CONSTRAINTS if l not in extracted]

    print(f'baseline: {samples["base"]}  ({len(base)} candidate location(s))')
    print(f'compared against: {", ".join(tested) or "nothing"}\n')

    survivors = []
    for key, guid in sorted(base.items()):
        verdict = []
        ok = True
        for label in tested:
            other = extracted[label].get(key)
            if other is None:
                ok = False
                verdict.append(f'{label}=ABSENT')
                continue
            same = (other == guid)
            if same != CONSTRAINTS[label]:
                ok = False
            verdict.append(f'{label}={"same" if same else "differs"}')
        if ok:
            survivors.append((key, guid, verdict))

    if survivors:
        print(f'{len(survivors)} candidate(s) satisfy every tested condition:\n')
        for key, guid, verdict in survivors:
            print(f'  {guid}')
            print(f'    at {key}')
            print(f'    {"  ".join(verdict)}\n')
    else:
        print('NO candidate satisfies every tested condition.\n'
              'If a renamed sample was included, the project GUID does not\n'
              'survive renames -- the identity must come from elsewhere.\n')

    if missing:
        print(f'UNTESTED (no sample supplied): {", ".join(missing)}')
        for label in missing:
            why = ('cannot rule out per-save revision ids' if label == 'resaved'
                   else 'cannot rule out schema constants' if label == 'other'
                   else 'cannot rule out name-derived values' if label == 'recreated'
                   else f'the {label} hypothesis is unconfirmed')
            print(f'  {label}: {why}')
        print('\nA survivor above is only as strong as the conditions actually '
              'tested. Treat it as unconfirmed until the list is empty.')
    return 0


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    mode, rest = argv[1], argv[2:]
    if mode == 'probe':
        if len(rest) != 1:
            print('usage: probe FILE_OR_FOLDER', file=sys.stderr)
            return 2
        return probe(Path(rest[0]))
    if mode == 'compare':
        samples = {}
        for arg in rest:
            label, _, path = arg.partition('=')
            if not path:
                print(f'expected label=path, got {arg!r}', file=sys.stderr)
                return 2
            if label != 'base' and label not in CONSTRAINTS:
                print(f'unknown label {label!r}; expected base or one of '
                      f'{", ".join(CONSTRAINTS)}', file=sys.stderr)
                return 2
            samples[label] = Path(path)
        return compare(samples)
    print(f'unknown mode {mode!r}; expected probe or compare', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
