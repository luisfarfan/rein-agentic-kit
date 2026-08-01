"""Build the wheel, install it, run it. Nothing cheaper catches this.

Two packaging defects shipped past every unit test in this repo and were
found only by building and running:

  1. `[tool.setuptools.packages.find]` alongside a `package-dir` remap
     produced a wheel with metadata and NO package. It installed cleanly and
     failed at first run with ModuleNotFoundError.
  2. `bin/rein` has no `.py` extension, so `spec_from_file_location` returned
     None and the entry point could not load the CLI it wraps. Again: builds,
     installs, imports, fails on first use.

Both are invisible to any test that imports the modules directly, because
importing them directly is exactly what the packaged path does NOT do.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _has_build() -> bool:
    return subprocess.run(
        [sys.executable, "-c", "import build"], capture_output=True
    ).returncode == 0


@unittest.skipUnless(_has_build(), "python -m build not available")
class TestTheWheelInstallsAndRuns(unittest.TestCase):
    """Slow by nature (a real build and a real venv), and the only test in
    this repo that exercises the PyPI channel end to end."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rein-pkg-")
        build = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", cls.tmp, REPO],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise unittest.SkipTest(f"wheel build failed: {build.stderr[-400:]}")
        wheels = glob.glob(os.path.join(cls.tmp, "*.whl"))
        assert wheels, "build reported success but produced no wheel"
        cls.wheel = wheels[0]
        venv = os.path.join(cls.tmp, "venv")
        subprocess.run([sys.executable, "-m", "venv", venv], check=True, capture_output=True)
        cls.bin = os.path.join(venv, "bin")
        subprocess.run(
            [os.path.join(cls.bin, "pip"), "install", "--quiet", cls.wheel],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_console_script_runs(self):
        """Defect 2: it imported fine and could not load its own CLI."""
        proc = subprocess.run([os.path.join(self.bin, "rein"), "--version"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("rein ", proc.stdout)

    def test_the_wheel_actually_contains_the_package(self):
        """Defect 1: metadata only, no package."""
        import zipfile
        names = zipfile.ZipFile(self.wheel).namelist()
        for needed in ("rein_kit/_entry.py", "rein_kit/bin/rein", "rein_kit/lib/detect.py",
                       "rein_kit/workflows/loop.js"):
            self.assertIn(needed, names, f"the wheel is missing {needed}")

    def test_the_packaged_cli_is_the_same_code_as_the_plugins(self):
        """The whole reason `_entry` loads bin/rein instead of reimplementing
        it: two channels that drift are worse than one channel."""
        args = ["detect", REPO, "--json"]
        packaged = subprocess.run([os.path.join(self.bin, "rein"), *args],
                                  capture_output=True, text=True)
        plugin = subprocess.run(
            [sys.executable, os.path.join(REPO, "plugins", "rein", "bin", "rein"), *args],
            capture_output=True, text=True,
        )
        self.assertEqual(packaged.returncode, 0, packaged.stderr)
        self.assertEqual(json.loads(packaged.stdout), json.loads(plugin.stdout))

    def test_it_declares_no_runtime_dependencies(self):
        """A tool that runs inside other people's projects must not fight
        their environment."""
        proc = subprocess.run(
            [os.path.join(self.bin, "pip"), "show", "rein-agentic-kit"],
            capture_output=True, text=True,
        )
        requires = [l for l in proc.stdout.splitlines() if l.startswith("Requires:")]
        self.assertEqual(requires, ["Requires: "], f"unexpected dependencies: {requires}")
