"""Lightweight, free update check against the GitHub Releases API.

No third-party dependencies and fully fail-silent: any network/parse error (or
being offline) simply returns "no update", so it can never break a run or delay
it beyond the short timeout. It only *notifies* — it never downloads or installs.
"""

from __future__ import annotations

import json
import urllib.request

from ._version import __version__, __url__

# https://github.com/OWNER/REPO -> https://api.github.com/repos/OWNER/REPO/releases/latest
_LATEST_API = __url__.rstrip('/').replace('github.com', 'api.github.com/repos') + '/releases/latest'
RELEASES_PAGE = __url__.rstrip('/') + '/releases/latest'

# Where update notices send humans. The version *check* stays on the GitHub
# API (it's the source of truth for what's published), but the click lands on
# our download page rather than tossing users into a GitHub repo.
DOWNLOAD_PAGE = 'https://base10consultants.com/tools/projx-diff/'


def _as_tuple(v: str) -> tuple:
    out = []
    for part in (v or '').strip().lstrip('vV').split('.'):
        digits = ''.join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def latest_release(timeout: float = 2.5) -> str | None:
    """Return the latest *published* release version (e.g. '1.0.1'), or None on
    any error (offline, rate-limited, draft-only, etc.)."""
    try:
        req = urllib.request.Request(
            _LATEST_API,
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ProjxDiff'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tag = (json.load(resp).get('tag_name') or '').strip()
        return tag.lstrip('vV') or None
    except Exception:
        return None


def check_for_update(timeout: float = 2.5) -> str | None:
    """Return the newer version string if an update is available, else None."""
    latest = latest_release(timeout)
    if latest and _as_tuple(latest) > _as_tuple(__version__):
        return latest
    return None
