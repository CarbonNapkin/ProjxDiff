"""Lightweight, free update check against the GitHub Releases API.

No third-party dependencies and fully fail-silent: any network/parse error (or
being offline) simply returns "no update", so it can never break a run or delay
it beyond the short timeout.

The check itself only notifies. The GUI's one-click flow additionally calls
download_update(), which fetches the platform's packaged asset from the
release and verifies it against the release's SHA256SUMS.txt before handing
it back — a binary that cannot be verified is never returned. (The checksums
ride the same release as the binaries, so this guards integrity — a corrupt
or truncated download — not a compromise of the release host itself.)
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

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


# ------------------------------------------------------- one-click update ----

# Which release asset updates this platform. Linux is deliberately absent:
# those are headless servers updated by their admins.
INSTALL_ASSETS = {
    'win32': 'ProjxDiff-setup.exe',
    'darwin': 'ProjxDiff-macos.zip',
}


def platform_asset(platform: str = None) -> str | None:
    return INSTALL_ASSETS.get(platform or sys.platform)


def asset_url(version: str, asset: str) -> str:
    return f'{__url__.rstrip("/")}/releases/download/v{version}/{asset}'


def fetch_checksums(version: str, timeout: float = 10) -> dict:
    """{filename: sha256hex} parsed from the release's SHA256SUMS.txt;
    {} on any error (older releases don't publish one)."""
    try:
        req = urllib.request.Request(asset_url(version, 'SHA256SUMS.txt'),
                                     headers={'User-Agent': 'ProjxDiff'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode('utf-8', 'replace')
        out = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[1].lstrip('*')] = parts[0].lower()
        return out
    except Exception:
        return {}


def download_update(version: str, dest_dir, asset: str = None,
                    progress=None, timeout: float = 30) -> Path:
    """Download the platform's update asset for `version` into dest_dir,
    verifying it against the release's SHA256SUMS.txt. Returns the downloaded
    path. Raises on ANY failure — including a release without a checksum for
    the asset, or a mismatch (the file is deleted) — so callers can fall back
    to the download page rather than run an unverified binary.

    progress, if given, is called as progress(bytes_done, bytes_total)
    (total may be 0 when the server doesn't say)."""
    asset = asset or platform_asset()
    if not asset:
        raise RuntimeError('no packaged update for this platform')
    expected = fetch_checksums(version).get(asset)
    if not expected:
        raise RuntimeError(f'release v{version} has no verifiable checksum for {asset}')

    dest = Path(dest_dir) / asset
    req = urllib.request.Request(asset_url(version, asset),
                                 headers={'User-Agent': 'ProjxDiff'})
    digest = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, 'wb') as fh:
        total = int(resp.headers.get('Content-Length') or 0)
        while True:
            block = resp.read(65536)
            if not block:
                break
            fh.write(block)
            digest.update(block)
            done += len(block)
            if progress:
                progress(done, total)
    if digest.hexdigest().lower() != expected:
        dest.unlink(missing_ok=True)
        raise RuntimeError('checksum mismatch -- download discarded')
    return dest
