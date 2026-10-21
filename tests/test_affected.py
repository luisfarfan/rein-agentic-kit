"""Affected tests: what a task's gate runs instead of the whole suite."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import affected  # noqa: E402

PY = {"stack": "python", "commands": {"test": "uv run pytest -q tests/unit",
                                      "testOne": "uv run pytest -q {target}"}}


class Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, rel, body=""):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def compute(self, changed, resolved=PY):
        return affected.compute(self.root, resolved, changed)


class TestPytest(Repo):
    def setUp(self):
        super().setUp()
        self.write("src/app/catalog/service.py", "x = 1\n")
        self.write("src/app/orders/service.py", "x = 1\n")
        self.write("tests/unit/catalog/test_service.py", "def test_a(): pass\n")
        self.write("tests/unit/orders/test_service.py", "def test_b(): pass\n")
        self.write("tests/unit/test_api.py", "from app.catalog.service import x\n")
        self.write("tests/unit/test_pkg.py", "from app.catalog import (\n    other,\n    service,\n)\n")
        self.write("tests/unit/test_unrelated.py", "import app.orders\nfrom app.catalog import other\n")
        self.write("tests/unit/test_plain.py", "import app.catalog.service as s\n")

    def test_name_mirror_and_imports(self):
        out = self.compute(["src/app/catalog/service.py"])
        self.assertEqual(out["mode"], "run", out)
        self.assertEqual(out["targets"], ["tests/unit/catalog/test_service.py", "tests/unit/test_api.py",
                                          "tests/unit/test_pkg.py", "tests/unit/test_plain.py"])
        self.assertEqual(out["command"], "uv run pytest -q " + " ".join(out["targets"]))
        self.assertEqual(out["configured"], PY["commands"]["testOne"])

    def test_a_changed_test_file_runs_and_a_deleted_one_does_not(self):
        out = self.compute(["tests/unit/test_api.py", "tests/unit/test_gone.py"])
        self.assertEqual(out["targets"], ["tests/unit/test_api.py"])

    def test_nothing_python_changed_selects_nothing(self):
        out = self.compute(["README.md", "docs/x.txt"])
        self.assertEqual(out["mode"], "none")
        self.assertEqual(out["command"], "")

    def test_a_dependency_change_runs_the_full_suite(self):
        for path in ("pyproject.toml", "uv.lock", "requirements-dev.txt"):
            out = self.compute(["src/app/catalog/service.py", path])
            self.assertEqual(out["mode"], "full", path)
            self.assertIn(path, out["reason"])

    def test_a_conftest_change_selects_every_test_below_it(self):
        out = self.compute(["tests/unit/catalog/conftest.py"])
        self.assertEqual(out["targets"], ["tests/unit/catalog/test_service.py"])

    def test_over_the_cap_runs_the_full_suite(self):
        out = self.compute(["tests/unit/conftest.py"],
                           {**PY, "gate": {"affectedMax": 2}})
        self.assertEqual(out["mode"], "full")
        self.assertIn("affectedMax", out["reason"])

    def test_without_testone_the_full_suite(self):
        out = self.compute(["src/app/catalog/service.py"], {"stack": "python", "commands": {"test": "pytest"}})
        self.assertEqual(out["mode"], "full")
        self.assertIn("testOne", out["reason"])

    def test_a_deleted_module_still_runs_its_importers(self):
        os.remove(os.path.join(self.root, "src/app/catalog/service.py"))
        out = self.compute(["src/app/catalog/service.py"])
        self.assertIn("tests/unit/test_api.py", out["targets"])

    def test_paths_with_spaces_are_quoted(self):
        self.write("tests/unit/my tests/test_x.py", "")
        out = self.compute(["tests/unit/my tests/test_x.py"])
        self.assertIn("'tests/unit/my tests/test_x.py'", out["command"])


class TestStrategy(unittest.TestCase):
    def test_auto_detection(self):
        self.assertEqual(affected.strategy(PY), "pytest")
        self.assertEqual(affected.strategy({"stack": "node", "commands": {"test": "pnpm vitest run"}}), "vitest")
        self.assertEqual(affected.strategy({"stack": "node", "commands": {"test": "npx jest"}}), "jest")
        self.assertEqual(affected.strategy({"stack": "node", "commands": {"test": "ng test"}}), "full")
        self.assertEqual(affected.strategy({"stack": "go", "commands": {"test": "go test ./..."}}), "full")

    def test_config_per_language_and_as_a_string(self):
        cfg = {"stack": "node", "commands": {"test": "ng test"}, "gate": {"affected": {"typescript": "jest"}}}
        self.assertEqual(affected.strategy(cfg), "jest")
        self.assertEqual(affected.strategy({**PY, "gate": {"affected": "full"}}), "full")
        self.assertEqual(affected.strategy({**PY, "gate": {"affected": {"node": "vitest"}}}), "pytest")


class TestJs(Repo):
    NODE = {"stack": "node", "packageManager": "pnpm", "commands": {"test": "pnpm vitest run"}}

    def setUp(self):
        super().setUp()
        self.write("src/a.ts", "export const a = 1\n")
        self.write("src/a.spec.ts", "")
        self.write("src/b.tsx", "")

    def test_vitest_related_over_the_changed_sources(self):
        out = self.compute(["src/a.ts", "src/b.tsx", "README.md"], self.NODE)
        self.assertEqual(out["mode"], "run")
        self.assertEqual(out["command"], "pnpm exec vitest related --run src/a.ts src/b.tsx")

    def test_jest_find_related(self):
        cfg = {**self.NODE, "packageManager": "npm", "commands": {"test": "npx jest"}}
        self.assertEqual(self.compute(["src/a.ts"], cfg)["command"], "npx jest --findRelatedTests src/a.ts")

    def test_package_json_or_a_deleted_source_runs_full(self):
        self.assertEqual(self.compute(["src/a.ts", "package.json"], self.NODE)["mode"], "full")
        self.assertEqual(self.compute(["src/gone.ts"], self.NODE)["mode"], "full")

    def test_no_js_change_selects_nothing(self):
        self.assertEqual(self.compute(["docs/x.md"], self.NODE)["mode"], "none")

    def test_a_template_with_mapped_spec_files(self):
        cfg = {**self.NODE, "commands": {"test": "ng test"},
               "gate": {"affected": {"node": "npx ng test --watch=false --include {tests}"}}}
        out = self.compute(["src/a.ts", "src/b.tsx"], cfg)
        self.assertEqual(out["command"], "npx ng test --watch=false --include src/a.spec.ts")
        self.assertEqual(self.compute(["src/b.tsx"], cfg)["mode"], "none")

    def test_a_subproject_runs_from_its_directory(self):
        self.write("web/src/c.ts", "")
        cfg = {**self.NODE, "subproject": "web"}
        out = self.compute(["web/src/c.ts", "src/a.ts"], cfg)
        self.assertEqual(out["command"], "cd web && pnpm exec vitest related --run src/c.ts")


if __name__ == "__main__":
    unittest.main()
