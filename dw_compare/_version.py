"""Single source of truth for the package version and identity.

Kept dependency-free (no imports from other dw_compare modules) so that
__init__.py and any submodule can read these constants without creating
import cycles.
"""

__version__ = '1.1.0'
__author__ = 'Base 10 Consultants'
__url__ = 'https://github.com/CarbonNapkin/ProjxDiff'
__license__ = 'MIT'
