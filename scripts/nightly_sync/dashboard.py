#!/usr/bin/env python3
"""Back-compat wrapper: the dashboard generator moved into the dw_compare
package in v1.2.0 (dw_compare/dashboard.py). New setups should call
`python -m dw_compare --dashboard config.json`."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dw_compare.dashboard import *   # noqa: F401,F403,E402
from dw_compare.dashboard import main, generate_dashboard  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
