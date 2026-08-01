"""Python 3.9 is a supported floor, and it broke silently for eight changes.

CI runs 3.9 and 3.12. While the runner was blocked on billing, the floor was
checked by hand -- once -- and reported clean. It was clean when it ran, and
then `tests/test_run_duration.py` shipped `ts: str | None = None` in a
function signature with no `from __future__ import annotations`, and 3.9
raised `TypeError: unsupported operand type(s) for |` at import time.

A one-time manual sweep is not a gate. This is the same sweep, run every
time, in the suite itself -- so the floor cannot break again between CI
outages.
"""

from __future__ import annotations

import ast
import glob
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pep604_without_future(path: str) -> list:
    """Lines where an annotation uses `A | B`, in a module that does NOT
    postpone evaluation. Those are evaluated at import time, and 3.9 has no
    `types.UnionType`."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    if any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
           and any(a.name == "annotations" for a in n.names) for n in tree.body):
        return []
    hits = []
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            anns = [a.annotation for a in
                    [*args.args, *args.posonlyargs, *args.kwonlyargs,
                     args.vararg, args.kwarg] if a is not None and a.annotation]
            if node.returns:
                anns.append(node.returns)
        elif isinstance(node, ast.AnnAssign) and node.annotation:
            anns = [node.annotation]
        for ann in anns:
            for sub in ast.walk(ann):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                    hits.append(sub.lineno)
    return hits


class TestTheSupportedFloorHolds(unittest.TestCase):
    def _sources(self) -> list:
        return (glob.glob(os.path.join(REPO, "tests", "*.py"))
                + glob.glob(os.path.join(REPO, "plugins", "rein", "lib", "*.py"))
                + [os.path.join(REPO, "plugins", "rein", "bin", "rein")])

    def test_no_runtime_evaluated_pep604_union(self):
        offenders = {
            os.path.relpath(f, REPO): lines
            for f in self._sources() if (lines := _pep604_without_future(f))
        }
        self.assertEqual(
            offenders, {},
            "`A | B` in an annotation is evaluated at import time without "
            "`from __future__ import annotations`, and Python 3.9 raises "
            f"TypeError there: {offenders}",
        )

    def test_the_sweep_actually_reads_files(self):
        """Guard on the guard: an empty file list passes vacuously."""
        self.assertGreater(len(self._sources()), 20)
