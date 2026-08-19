"""PyInstaller entry point.

PyInstaller drives a top-level script, not a package's __main__. Pointing
it at this file lets it discover the dw_compare package and bundle every
module that ends up imported from main().

A bare double-click of the packaged executable arrives here with no
command-line arguments, which would otherwise fall into the CLI auto-
detect path and fail because the executable's working directory does not
contain two project files. Promote that case to --gui so the typical end
user gets the graphical UI on launch. Anyone passing real arguments
(running the .exe from a terminal) keeps the CLI behavior.

The Windows build ships a second, console-subsystem copy as
ProjxDiff-cli.exe (see dw_compare.spec). Bare-launching THAT one is
someone looking for the command line, not the GUI, so it gets --help.
"""

import sys
from pathlib import Path

from dw_compare.__main__ import main


def _is_cli_build() -> bool:
    """Whether this process is the console-subsystem ProjxDiff-cli.exe."""
    return Path(sys.executable).stem.lower().endswith('-cli')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        sys.argv.append('--help' if (getattr(sys, 'frozen', False)
                                     and _is_cli_build()) else '--gui')
    main()
