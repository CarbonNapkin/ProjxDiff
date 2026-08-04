#!/usr/bin/env python3
"""Back-compat wrapper: the sync engine moved into the dw_compare package in
v1.2.0 (dw_compare/sync.py). Existing scheduled tasks pointing here keep
working; new setups should call `python -m dw_compare --sync config.json`
(or `ProjxDiff.exe --sync config.json` from a packaged build)."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dw_compare.sync import *   # noqa: F401,F403,E402
from dw_compare.sync import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
