#!/usr/bin/env python3
"""Create a minimal fake site (share + config) for the release workflow's
packaged-binary smoke test: one tiny .driveprojx and a sync config pointing
at folders under the given root."""

import json
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
share = root / 'share'
share.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(share / 'Smoke.driveprojx', 'w') as zf:
    zf.writestr('driveProj/project.xml',
                '<Project><Variables>'
                '<Variable DisplayName="W" StoreName="W" Rule="=1"/>'
                '</Variables></Project>')

(root / 'config.json').write_text(json.dumps({
    'source_dir': str(share),
    'archive_repo': str(root / 'repo'),
    'data_dir': str(root / 'data'),
}), encoding='utf-8')
print(f'smoke site created at {root}')
