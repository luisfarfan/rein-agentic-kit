"""A plan root in a temporary directory loses its `.rein/` state when the machine restarts.

`rein ship` keeps its state, gate log and the owner's answers under `<root>/.rein/`, and the
review artifacts of a plan next to it; a root under /tmp, /private/tmp, /var/folders or $TMPDIR
(a planning session's scratch checkout) is cleaned on reboot, and every earlier ship there is gone.
"""

from __future__ import annotations

import os
import tempfile

# Where the system keeps files it may delete: the usual temp roots, macOS's per-user folders included.
_FIXED = ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", "/var/folders", "/private/var/folders")


def _roots() -> list:
    roots = list(_FIXED)
    for extra in (os.environ.get("TMPDIR", ""), tempfile.gettempdir()):
        if extra and extra not in ("/", ""):
            roots.append(extra)
    return list(dict.fromkeys(os.path.realpath(r) for r in roots))


def temp_root(path: str) -> str:
    """The temporary directory `path` lives under, else "". `path` need not exist."""
    if not path:
        return ""
    real = os.path.realpath(os.path.abspath(path))
    for root in _roots():
        if real == root or real.startswith(root.rstrip(os.sep) + os.sep):
            return root
    return ""


def warning(path: str) -> str:
    """The sentence that says why `path` is a bad place for a plan root; "" when it is fine."""
    root = temp_root(path)
    if not root:
        return ""
    return ("%s is under the temporary directory %s: its .rein/ state (ship runs, gate logs, answers) "
            "is lost when the machine restarts" % (os.path.abspath(path), root))
