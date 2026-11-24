"""Advisories: a way to tell copies already in the field that the version
they are running has a known problem.

An update notice says "something newer exists". It says the same thing for a
new dashboard filter as for a bug that can delete project files, and the
nightly sync -- the one mode that deletes anything, and the one nobody is
watching -- never saw even that. An advisory closes both gaps: it names the
affected versions, says how bad it is, and is read by the app, the command
line and every nightly run.

The list lives in `advisories.json` at the root of the repository, on the
default branch. Publishing one is a commit, not a release, so it reaches
installed copies within minutes of being pushed:

    {
      "schema": 1,
      "advisories": [
        {
          "id": "2026-11-archive-overlap",
          "severity": "critical",
          "introduced": "1.2.0",
          "fixed": "1.12.1",
          "summary": "The nightly sync can delete live project folders ..."
        }
      ]
    }

`introduced` is optional (absent = every earlier version); `fixed` is the
first version that is no longer affected (absent = no fix yet, every version
from `introduced` on is affected). `severity` is "critical" or "important".

What an advisory can do is deliberately small. It cannot stop the tool, run
anything, or send anyone to a link it supplies -- it carries a sentence of
text, shown as text. The only behaviour it changes is in the nightly sync,
where a CRITICAL advisory puts the run on hold: nothing in the archive is
replaced or removed until the copy is updated (see sync.run). The remedy is
always the same and is never taken from the advisory: update.

Everything here fails open. No network, a rate limit, a file that does not
parse, an entry with a field missing: the answer is "no advisories" and the
tool runs exactly as it would have. A safety notice that can itself take a
site down is not a safety feature.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from ._version import __version__, __url__
from .update_check import _as_tuple

SCHEMA = 1
SEVERITIES = ('critical', 'important')
ADVISORY_FILE = 'advisories.json'
_MAX_BYTES = 256 * 1024
_MAX_SUMMARY = 600

# Same host as the update check (api.github.com), so a site that can see one
# can see the other; the raw host is the fallback for when the API is
# rate-limited (60 unauthenticated requests an hour per address).
_API_URL = (__url__.rstrip('/').replace('github.com', 'api.github.com/repos')
            + f'/contents/{ADVISORY_FILE}')
_RAW_URL = (__url__.rstrip('/').replace('github.com', 'raw.githubusercontent.com')
            + f'/HEAD/{ADVISORY_FILE}')

# Testing hook: point a copy at a file or URL of your own to see how it
# behaves under an advisory without publishing one.
URL_ENV = 'PROJXDIFF_ADVISORY_URL'


def _get(url: str, timeout: float, accept: str = None) -> bytes:
    headers = {'User-Agent': 'ProjxDiff'}
    if accept:
        headers['Accept'] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(_MAX_BYTES + 1)


def fetch(timeout: float = 4.0):
    """The published advisory document as a dict, or None when it could not
    be read (offline, rate-limited, malformed). None means "unknown", which
    callers must treat differently from an empty list only when they hold a
    cached copy -- see load()."""
    override = os.environ.get(URL_ENV, '').strip()
    sources = ([(override, None)] if override else
               [(_API_URL, 'application/vnd.github.raw+json'), (_RAW_URL, None)])
    for url, accept in sources:
        try:
            if override and '://' not in url:
                raw = Path(url).read_bytes()[:_MAX_BYTES + 1]
            else:
                raw = _get(url, timeout, accept)
            if len(raw) > _MAX_BYTES:
                continue
            doc = json.loads(raw.decode('utf-8'))
            if isinstance(doc, dict) and isinstance(doc.get('advisories'), list):
                return doc
        except Exception:
            continue
    return None


def _clean(text) -> str:
    """One line of plain text, bounded. The summary is shown in dialogs, logs
    and an HTML page; it is never markup and never more than a few sentences."""
    text = ' '.join(str(text).split())
    text = ''.join(c for c in text if c.isprintable())
    return text[:_MAX_SUMMARY]


def _parse(entry):
    """A normalised advisory, or None for an entry that cannot be trusted to
    mean what it says. A typo in one entry drops that entry, not the list."""
    if not isinstance(entry, dict):
        return None
    severity = str(entry.get('severity', '')).strip().lower()
    summary = _clean(entry.get('summary', ''))
    adv_id = _clean(entry.get('id', ''))[:80]
    if severity not in SEVERITIES or not summary or not adv_id:
        return None
    out = {'id': adv_id, 'severity': severity, 'summary': summary,
           'introduced': '', 'fixed': ''}
    for key in ('introduced', 'fixed'):
        val = entry.get(key)
        if val in (None, ''):
            continue
        val = str(val).strip().lstrip('vV')
        if not val or not all(p.isdigit() for p in val.split('.')):
            return None          # an unreadable range could match anything
        out[key] = val
    return out


def affects(adv: dict, version: str = __version__) -> bool:
    v = _as_tuple(version)
    if adv['introduced'] and v < _as_tuple(adv['introduced']):
        return False
    if adv['fixed'] and v >= _as_tuple(adv['fixed']):
        return False
    return True


def applicable(doc, version: str = __version__) -> list:
    """Advisories in `doc` that affect `version`, critical first."""
    if not isinstance(doc, dict):
        return []
    out = []
    for entry in doc.get('advisories') or []:
        adv = _parse(entry)
        if adv is not None and affects(adv, version):
            out.append(adv)
    out.sort(key=lambda a: (SEVERITIES.index(a['severity']), a['id']))
    return out


def check(timeout: float = 4.0, version: str = __version__) -> list:
    """Advisories affecting this copy right now; [] when there are none or
    the list could not be read."""
    return applicable(fetch(timeout), version)


def load(cache_path: Path = None, timeout: float = 4.0,
         version: str = __version__, write_cache: bool = True) -> tuple:
    """(advisories, source) for an unattended run, where `source` is
    'live', 'cached' or 'none'.

    The interactive tools ask fresh each time and shrug when they cannot. A
    nightly run should not: a site whose internet is down on the one night it
    matters would otherwise run a version it was told yesterday to hold. So
    the last document successfully read is kept beside the data, and used
    when the live one cannot be had. A hold therefore ends in exactly two
    ways -- the advisory is withdrawn and a run sees that, or the copy is
    updated to a version it does not cover."""
    doc = fetch(timeout)
    if doc is not None:
        if cache_path is not None and write_cache:
            try:
                cache_path.write_text(json.dumps(doc, indent=2) + '\n',
                                      encoding='utf-8')
            except OSError:
                pass
        return applicable(doc, version), 'live'
    if cache_path is not None:
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cached = None
        if cached is not None:
            return applicable(cached, version), 'cached'
    return [], 'none'


def critical(advisories: list) -> list:
    return [a for a in advisories if a['severity'] == 'critical']


def describe(adv: dict) -> str:
    """One line for a log or a console."""
    fix = (f' Fixed in {adv["fixed"]} -- update.' if adv['fixed']
           else ' No fixed version yet.')
    return f'[{adv["severity"].upper()}] {adv["summary"]}{fix} ({adv["id"]})'
