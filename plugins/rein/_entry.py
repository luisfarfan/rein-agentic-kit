"""Console-script entry point for the PyPI distribution.

The plugin and the package must run the SAME code, or they drift and a bug
gets fixed in one channel only. So this does not reimplement the CLI: it
loads `bin/rein` -- the identical file the plugin's own sessions execute --
and calls its `main`. `bin/rein` puts its `lib/` on `sys.path` itself, from
a path relative to its own location, so it behaves the same wherever it was
installed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "bin", "rein")


def main() -> int:
    # An EXPLICIT loader: `bin/rein` has no `.py` extension (it is a shebang
    # script the plugin executes directly), and spec_from_file_location
    # returns None for an extension it does not recognise -- verified. The
    # package installed and imported cleanly and then failed at first run.
    spec = importlib.util.spec_from_file_location(
        "_rein_cli", CLI, loader=SourceFileLoader("_rein_cli", CLI)
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging fault
        print(f"rein: cannot load its own CLI at {CLI}", file=sys.stderr)
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
