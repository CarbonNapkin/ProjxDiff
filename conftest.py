"""Make the repo root importable so `import dw_compare` works under pytest
regardless of how/where pytest is invoked."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_advisories(monkeypatch):
    """Every sync run asks the published advisory list whether this version
    is safe. The suite must never depend on -- or be changed by -- whatever
    happens to be published today; tests that exercise advisories install
    their own document."""
    import dw_compare.advisory as advisory
    monkeypatch.delenv(advisory.URL_ENV, raising=False)
    monkeypatch.setattr(advisory, 'fetch', lambda *a, **k: None)
