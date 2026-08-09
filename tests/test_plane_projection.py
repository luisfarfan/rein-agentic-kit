#!/usr/bin/env python3
"""Tests for T006 -- "deleting the Plane config changes nothing".

  AC1  `rein sync --plane` walks the selection (T005) and issues the
       upserts (T003): repo -> Project, change -> Module, task -> Work
       Item, transition -> the State whose `group` matches.
  D1   with `plane.json` absent, `rein state`, `rein-apply` and `rein-step`
       produce byte-identical output to a run where it is present. Tested
       at the CLI commands each literally invokes (`state`, `next`,
       `context`, `tasks` -- see SKILL.md for rein-step/rein-apply).
  AC3  three config problems exit differently because they mean different
       things: no `plane.json` (exit 0), `plane.json` but no
       `REIN_PLANE_API_KEY` (exit non-zero, names the variable), both
       present but the workspace does not exist in Plane (exit non-zero,
       prints the URL a human must use).
  AC4  a task's `Depends on` is a plain line in the work item body; no
       `/relation` call is ever attempted.
  AC5  a Plane failure is reported per entity, never aborts the sync; the
       local task-event log is untouched.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")
WORKFLOWS_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "workflows")
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")

sys.path.insert(0, LIB_DIR)

import plane_client as pc  # noqa: E402
import plane_sync as ps  # noqa: E402


def _load_rein_cli():
    """Loads `bin/rein` as an importable module (it has no `.py` suffix and
    guards `main()` behind `__name__ == "__main__"`, so this never runs the
    real CLI) -- lets cmd_sync's exit-code mapping be tested by monkeypatching
    `plane_sync.run_sync` directly, with no subprocess and no network."""
    loader = importlib.machinery.SourceFileLoader("rein_cli_under_test", REIN_BIN)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeTransport:
    """Replays a fixed queue of (status, body) responses, recording every
    (method, url, parsed_json_body) call in order. Same shape as
    tests/test_plane_client.py's -- no test here opens a socket."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body):
        parsed_body = json.loads(body) if body else None
        self.calls.append((method, url, parsed_body))
        if not self._responses:
            raise AssertionError(f"FakeTransport ran out of scripted responses for {method} {url}")
        status, resp_body = self._responses.pop(0)
        raw = json.dumps(resp_body).encode("utf-8") if resp_body is not None else b""
        return status, {}, raw


def _write_tasks_md(change_dir: str, change: str, tasks: list[dict]) -> None:
    os.makedirs(change_dir, exist_ok=True)
    lines = [f"# Change: {change}", "", "## Why", "", "Fixture change for T006 tests.", ""]
    for t in tasks:
        lines.append(f"- [ ] {t['id']} {t['title']}")
        lines.append("  - Type: implementation")
        deps = ", ".join(t.get("dependsOn", [])) or "none"
        lines.append(f"  - Depends on: {deps}")
        lines.append("  - Human review: false")
        lines.append("  - Verification: `true`")
        lines.append("  - Acceptance:")
        lines.append("    - it works")
    with open(os.path.join(change_dir, "tasks.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _make_repo(root: str, change: str, tasks: list[dict]) -> str:
    change_dir = os.path.join(root, "openspec", "changes", change)
    _write_tasks_md(change_dir, change, tasks)
    return change_dir


def _make_flat_repo(root: str, change: str, tasks: list[dict]) -> str:
    """A plain `tasks.md` at `root` -- the source `rein state`/`next`/
    `tasks`/`context` resolve with no `--change` needed, unlike the
    `openspec` layout `_make_repo` builds."""
    _write_tasks_md(root, change, tasks)
    return root


def _write_plane_json(root: str, **fields) -> str:
    path = os.path.join(root, "plane.json")
    payload = {"base_url": "https://plane.example.com", "workspace_slug": "acme"}
    payload.update(fields)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def _no_network_env(root: str) -> dict:
    env = dict(os.environ)
    env["HOME"] = root
    return env


# ------------------------------------------------------------ AC1: mapping --


class TransitionGroupMappingTests(unittest.TestCase):
    """AC1: the full transition -> State GROUP mapping, against the five
    groups measured on a fresh Plane project."""

    FRESH_PROJECT_STATES = [
        {"id": "s-backlog", "group": "backlog"},
        {"id": "s-unstarted", "group": "unstarted"},
        {"id": "s-started", "group": "started"},
        {"id": "s-completed", "group": "completed"},
        {"id": "s-cancelled", "group": "cancelled"},
    ]

    def test_every_transition_maps_into_a_measured_group(self):
        """Six transitions, five groups -- and every target must be one of
        the five a freshly created project was measured to have, or the sync
        would look up a state id that does not exist."""
        self.assertEqual(
            ps.TRANSITION_GROUP,
            {
                "planned": "backlog",
                "started": "started",
                "verified": "completed",
                "merged": "completed",
                "blocked": "unstarted",
                # Emitted only by the projection, for a task still open when
                # its change ages past the window.
                "cancelled": "cancelled",
            },
        )
        measured = {s["group"] for s in self.FRESH_PROJECT_STATES}
        self.assertTrue(set(ps.TRANSITION_GROUP.values()) <= measured)

    def test_every_emitted_transition_has_a_mapping(self):
        """A transition with no group silently loses its state on the board."""
        import events as _ev
        import plane_projection as _pp
        emitted = set(_ev.TASK_TRANSITIONS) | {"planned", "cancelled"}
        self.assertEqual(emitted - set(ps.TRANSITION_GROUP), set())

    def test_resolves_the_matching_state_id_per_transition(self):
        expected = {
            "planned": "s-backlog",
            "started": "s-started",
            "verified": "s-completed",
            "merged": "s-completed",
            "blocked": "s-unstarted",
        }
        for transition, state_id in expected.items():
            with self.subTest(transition=transition):
                self.assertEqual(
                    ps._state_id_for_transition(self.FRESH_PROJECT_STATES, transition),
                    state_id,
                )

    def test_unknown_transition_falls_back_to_backlog_group(self):
        self.assertEqual(
            ps._state_id_for_transition(self.FRESH_PROJECT_STATES, "made-up"),
            "s-backlog",
        )


class RunSyncWiringTests(unittest.TestCase):
    """AC1: `run_sync` walks a repo/change/task tree and issues, in order,
    the Project -> Module -> Work-Item -> attach upserts T003 defines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _make_repo(self.root, "demo", [{"id": "T001", "title": "Build the thing"}])
        _write_plane_json(self.root)

    def test_repo_to_project_change_to_module_task_to_work_item(self):
        # get_workspace, then: list_projects, create project, patch module_view,
        # upsert module, list_states, upsert work item, attach.
        transport = FakeTransport([
            # Existence check IS a projects listing: Plane serves no
            # /api/v1/workspaces/{slug}/ route -- asking for one answered
            # 401 against a live instance holding a valid key.
            # One GET, not two: the existence check IS the first page of
            # the project listing, and priming the cache from it saves a
            # call out of a 60/minute budget.
            (200, {"results": []}),                          # GET workspace + projects
            (201, {"id": "proj-1"}),                          # POST create project
            (200, {"id": "proj-1", "module_view": True}),     # PATCH module_view
            (201, {"id": "mod-1"}),                            # POST module
            (200, {"results": [
                {"id": "s-backlog", "group": "backlog"},
                {"id": "s-started", "group": "started"},
                {"id": "s-completed", "group": "completed"},
                {"id": "s-unstarted", "group": "unstarted"},
                {"id": "s-cancelled", "group": "cancelled"},
            ]}),                                                # GET states
            (201, {"id": "wi-1"}),                              # POST work item
            (200, {"issues": ["wi-1"]}),                        # POST attach
        ])

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        report = ps.run_sync(self.root, client_factory=factory)

        self.assertEqual(report["failed"], [])
        self.assertEqual(len(report["applied"]), 2)  # module + 1 work item

        methods_and_paths = [(m, u.split("acme", 1)[1]) for m, u, _ in transport.calls]
        self.assertEqual(methods_and_paths, [
            ("GET", "/projects/"),
            ("POST", "/projects/"),
            ("PATCH", "/projects/proj-1/"),
            ("POST", "/projects/proj-1/modules/"),
            ("GET", "/projects/proj-1/states/"),
            ("POST", "/projects/proj-1/issues/"),
            ("POST", "/projects/proj-1/modules/mod-1/module-issues/"),
        ])

        # Work item upsert carried the state id matching its transition
        # (planned -> backlog, per AC1).
        work_item_call = transport.calls[5]
        self.assertEqual(work_item_call[2]["state"], "s-backlog")


class FlatRepoGetsARealModuleNameTests(unittest.TestCase):
    """Round-2 review, finding 1: a flat `tasks.md` repo (no
    `openspec/changes` directory -- rein's own layout) resolves to the
    single implicit change `changes_for()` returns as `[""]`. Before the
    fix, `product_state.state()` folded that `""` straight into
    `state["change"]`, `_module_entity` built a payload of `{"name": ""}`,
    and Plane's API 400s on a blank Module name -- which
    `_upsert_post_then_conflict` turns into a `PlaneConflictError` with no
    id, which cascaded to every work item too (each one resolves its
    module id through `_resolve_module_id`, which re-upserts the same
    blank name). `plan.read_plan()` now falls back to the plan's own
    `# Change: <name>` header, so the flat layout's Module gets a real
    name and `rein sync --plane` applies cleanly end to end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _make_flat_repo(self.root, "my-flat-change", [{"id": "T001", "title": "Do the thing"}])
        _write_plane_json(self.root)

    def test_module_gets_a_real_name_and_everything_applies(self):
        transport = FakeTransport([
            # Existence check IS a projects listing: Plane serves no
            # /api/v1/workspaces/{slug}/ route -- asking for one answered
            # 401 against a live instance holding a valid key.
            # One GET, not two: the existence check IS the first page of
            # the project listing, and priming the cache from it saves a
            # call out of a 60/minute budget.
            (200, {"results": []}),                          # GET workspace + projects
            (201, {"id": "proj-1"}),                          # POST create project
            (200, {"id": "proj-1"}),                          # PATCH module_view
            (201, {"id": "mod-1"}),                            # POST module
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),  # GET states
            (201, {"id": "wi-1"}),                              # POST work item
            (200, {"issues": ["wi-1"]}),                        # POST attach
        ])

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        report = ps.run_sync(self.root, client_factory=factory)

        # Everything applies -- no PlaneConflictError cascading from a
        # blank module name.
        self.assertEqual(report["failed"], [])
        self.assertEqual(
            [k.split("::", 1)[-1] for k in sorted(report["applied"])],
            ["item:my-flat-change:T001", "module:my-flat-change"],
        )

        module_call = next(c for c in transport.calls if c[1].endswith("/modules/"))
        self.assertEqual(module_call[2]["name"], "my-flat-change")

        # The module's identity must not forge the project's -- both used to
        # collapse to the same external_id when the change component was "".
        repo_name = os.path.basename(os.path.abspath(self.root))
        project_ext_id = pc.make_external_id("acme", repo_name, "", "")
        module_ext_id = pc.make_external_id("acme", repo_name, "my-flat-change", "")
        self.assertNotEqual(
            project_ext_id, module_ext_id,
            "an empty change component let the module's identity forge the project's",
        )
        self.assertEqual(module_call[2]["external_id"], module_ext_id)


class RecordIsScopedPerRepoTests(unittest.TestCase):
    """AC1's repo -> Project mapping means a workspace with two repos that
    happen to name a change the same must not let one repo's synced record
    hide the other's -- `plane_projection`'s own entity keys
    (`module:<change>`, `item:<change>:<task>`) carry no repo at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _make_repo(self.root, "demo", [{"id": "T001", "title": "Same-named change"}])

    def _script(self):
        return FakeTransport([
            (200, {"id": "ws-1"}),
            (200, {"results": []}),
            (201, {"id": "proj-1"}),
            (200, {"id": "proj-1"}),
            (201, {"id": "mod-1"}),
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),
            (201, {"id": "wi-1"}),
            (200, {"issues": ["wi-1"]}),
        ])

    def _run_for_repo(self, repo_name):
        transport = self._script()

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        with mock.patch.object(ps, "repos_for", return_value=[(repo_name, self.root)]):
            return ps.run_sync(self.root, client_factory=factory)

    def test_two_repos_with_an_identically_named_change_both_sync(self):
        _write_plane_json(self.root)

        report_a = self._run_for_repo("repo-a")
        self.assertEqual(report_a["failed"], [])
        self.assertEqual(len(report_a["applied"]), 2)  # module + T001

        # repo-b's identically-shaped "demo" change must NOT be skipped as
        # already-synced just because repo-a's record carries the same
        # unscoped `module:demo` / `item:demo:T001` keys.
        report_b = self._run_for_repo("repo-b")
        self.assertEqual(report_b["failed"], [])
        self.assertEqual(len(report_b["applied"]), 2)

        record_path = os.path.join(self.root, ps.RECORD_RELATIVE_PATH)
        with open(record_path, encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual(len(record), 4)  # 2 entities x 2 repos, distinctly keyed
        self.assertTrue(any(k.startswith("repo-a::") for k in record))
        self.assertTrue(any(k.startswith("repo-b::") for k in record))


class ModuleAttachSurvivesUnchangedModuleHashTests(unittest.TestCase):
    """Round-1 review, finding 1: a work item whose change's Module hash is
    unchanged must still resolve and attach to that Module.

    T005's live-module payload is `{"name": change_name}` alone -- it never
    varies with the change's tasks, so its content hash never changes after
    the first sync. Before the fix, `module_ids` was populated only from a
    module ENTITY processed in the *current* `select()` batch; a later run
    that only added a task (never touching the module) would then find
    `module_ids` empty for that change and skip `attach_work_item_to_module`
    entirely -- while still recording the work item as applied. The card
    would exist in Plane forever, unlinked from any Module."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.change_dir = _make_repo(self.root, "demo", [{"id": "T001", "title": "First task"}])
        _write_plane_json(self.root)
        self.repo_name = "acme-repo"

    def _factory(self, transport):
        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})
        return factory

    def _run(self, transport):
        with mock.patch.object(ps, "repos_for", return_value=[(self.repo_name, self.root)]):
            return ps.run_sync(self.root, client_factory=self._factory(transport))

    def test_second_run_still_attaches_new_task_to_the_existing_module(self):
        first_transport = FakeTransport([
            (200, {"id": "ws-1"}),                                            # GET workspace
            (200, {"results": []}),                                            # GET list projects
            (201, {"id": "proj-1"}),                                            # POST create project
            (200, {"id": "proj-1"}),                                            # PATCH module_view
            (201, {"id": "mod-1"}),                                              # POST module (demo)
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),       # GET states
            (201, {"id": "wi-1"}),                                                # POST T001 work item
            (200, {"issues": ["wi-1"]}),                                          # POST attach T001
        ])
        report1 = self._run(first_transport)
        self.assertEqual(report1["failed"], [])
        self.assertEqual(
            [k.split("::", 1)[-1] for k in sorted(report1["applied"])],
            ["item:demo:T001", "module:demo"],
        )

        # A second task is added. The module's payload (name only, for a
        # live change) is unaffected, so `select()` will not re-emit the
        # module entity this run -- only the new work item.
        _write_tasks_md(self.change_dir, "demo", [
            {"id": "T001", "title": "First task"},
            {"id": "T002", "title": "Second task"},
        ])

        project_ext_id = pc.make_external_id("acme", self.repo_name, "", "")
        second_transport = FakeTransport([
            (200, {"id": "ws-1"}),                                              # GET workspace
            (200, {"results": [{"id": "proj-1", "external_id": project_ext_id,
                                 "external_source": pc.EXTERNAL_SOURCE}]}),      # GET list projects (match)
            (200, {"id": "proj-1"}),                                             # PATCH project (upsert match)
            (200, {"id": "proj-1"}),                                             # PATCH module_view
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),       # GET states
            (201, {"id": "wi-2"}),                                                # POST T002 work item
            (409, {"id": "mod-1"}),                                               # POST module -- resolve, 409-with-id
            (200, {"id": "mod-1"}),                                               # PATCH module (resolve's patch)
            (200, {"issues": ["wi-2"]}),                                          # POST attach T002
        ])
        report2 = self._run(second_transport)

        self.assertEqual(report2["failed"], [])
        self.assertEqual(
            [k.split("::", 1)[-1] for k in report2["applied"]],
            ["item:demo:T002"],
        )  # module NOT re-emitted

        methods_and_paths = [(m, u.split("acme", 1)[1]) for m, u, _ in second_transport.calls]
        self.assertEqual(methods_and_paths[-1], ("POST", "/projects/proj-1/modules/mod-1/module-issues/"))


# --------------------------------------------------------------- AC4: deps --


class DependsOnBodyTests(unittest.TestCase):
    """AC4: `Depends on` is a plain body line; no `/relation` path is ever
    hit, and the emitted method set stays inside {GET, POST, PATCH}."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _make_repo(self.root, "demo", [
            {"id": "T001", "title": "Base task"},
            {"id": "T002", "title": "Depends on base", "dependsOn": ["T001"]},
        ])
        _write_plane_json(self.root)

    def test_dependency_line_present_no_relation_call(self):
        transport = FakeTransport([
            (200, {"id": "ws-1"}),
            (200, {"results": []}),
            (201, {"id": "proj-1"}),
            (200, {"id": "proj-1"}),
            (201, {"id": "mod-1"}),
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),
            (201, {"id": "wi-1"}),                        # T001
            (200, {"issues": ["wi-1"]}),
            (201, {"id": "wi-2"}),                        # T002
            (200, {"issues": ["wi-2"]}),
        ])

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        report = ps.run_sync(self.root, client_factory=factory)
        self.assertEqual(report["failed"], [])

        # The T002 work item POST is the 9th call (index 8): body must carry
        # the plain "Depends on: T001" line.
        method, url, body = transport.calls[8]
        self.assertEqual(method, "POST")
        self.assertIn("Depends on: T001", body.get("description", ""))

        methods = {m for m, _, _ in transport.calls}
        self.assertEqual(methods, {"GET", "POST", "PATCH"})  # D6: never DELETE
        for _, call_url, _ in transport.calls:
            self.assertNotIn("/relation", call_url)  # D8: no relation call, ever


# ------------------------------------------------------- AC5: per-entity ---


class PartialFailureTests(unittest.TestCase):
    """AC5: a failure on one entity is reported against that entity alone;
    the other four in the same batch are still attempted, and the local
    task-event log is never touched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _make_repo(self.root, "demo", [
            {"id": "T001", "title": "Task one"},
            {"id": "T002", "title": "Task two"},
            {"id": "T003", "title": "Task three"},
            {"id": "T004", "title": "Task four"},
        ])
        _write_plane_json(self.root)

    def test_third_of_five_upserts_fails_other_four_still_applied(self):
        # Entities, in select()'s order: module, T001, T002, T003, T004.
        # The THIRD upsert -- T002's work-item POST -- 500s.
        transport = FakeTransport([
            (200, {"id": "ws-1"}),                                  # GET workspace
            (200, {"results": []}),                                  # GET list projects
            (201, {"id": "proj-1"}),                                  # POST create project
            (200, {"id": "proj-1"}),                                  # PATCH module_view
            (201, {"id": "mod-1"}),                                    # upsert #1: module
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),  # GET states (cached)
            (201, {"id": "wi-1"}),                                      # upsert #2: T001 work item
            (200, {"issues": ["wi-1"]}),                                #   attach
            (500, {"error": "boom"}),                                    # upsert #3: T002 FAILS
            (201, {"id": "wi-3"}),                                       # upsert #4: T003 work item
            (200, {"issues": ["wi-3"]}),                                #   attach
            (201, {"id": "wi-4"}),                                       # upsert #5: T004 work item
            (200, {"issues": ["wi-4"]}),                                #   attach
        ])

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        with mock.patch("events.record_event") as fake_event, \
             mock.patch("events.record_task_event") as fake_task_event:
            report = ps.run_sync(self.root, client_factory=factory)

        self.assertEqual(len(report["applied"]), 4)   # module + T001 + T003 + T004
        self.assertEqual(len(report["failed"]), 1)
        self.assertIn("T002", report["failed"][0]["key"])

        # AC5: the local task-event log was never touched by the sync.
        fake_event.assert_not_called()
        fake_task_event.assert_not_called()

        # The on-disk projection record carries only what actually applied.
        record_path = os.path.join(self.root, ps.RECORD_RELATIVE_PATH)
        with open(record_path, encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual(len(record), 4)
        self.assertFalse(any("T002" in key for key in record))

    def test_plane_sync_never_imports_events(self):
        """Structural guarantee behind the above: this module has no way to
        reach the event log even if a future change forgot to mock it."""
        with open(os.path.join(LIB_DIR, "plane_sync.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("import events", src)


# ------------------------------------------------------------- AC3: exits --


class ConfigLoadingTests(unittest.TestCase):
    """AC3, library level: the three config problems raise distinctly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_no_plane_json_raises_not_configured(self):
        with self.assertRaises(ps.NotConfigured):
            ps.run_sync(self.root, env={})

    def test_plane_json_present_no_api_key_raises_auth_error(self):
        _write_plane_json(self.root)
        with self.assertRaises(pc.PlaneAuthError) as ctx:
            ps.run_sync(self.root, env={})
        self.assertIn("REIN_PLANE_API_KEY", str(ctx.exception))

    def test_workspace_absent_raises_workspace_missing_with_url(self):
        _write_plane_json(self.root)
        transport = FakeTransport([(404, {"error": "not found"})])

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        with self.assertRaises(ps.WorkspaceMissing) as ctx:
            ps.run_sync(self.root, client_factory=factory)
        self.assertIn("acme", str(ctx.exception))
        self.assertIn("https://plane.example.com/create-workspace/", str(ctx.exception))


class CmdSyncExitCodeTests(unittest.TestCase):
    """AC3, CLI level: `cmd_sync` maps each of the three config problems to
    its own exit code and message, with `plane_sync.run_sync` monkeypatched
    so no network or plane.json parsing is involved here at all."""

    @classmethod
    def setUpClass(cls):
        cls.rein_cli = _load_rein_cli()

    def _run(self, side_effect):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.rein_cli._psync, "run_sync", side_effect=side_effect), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.rein_cli.cmd_sync(["--plane", "."])
        return code, out.getvalue(), err.getvalue()

    def test_not_configured_exits_zero(self):
        code, out, _err = self._run(ps.NotConfigured())
        self.assertEqual(code, 0)
        self.assertIn("not configured", out)

    def test_missing_api_key_exits_nonzero_naming_variable(self):
        code, _out, err = self._run(pc.PlaneAuthError("REIN_PLANE_API_KEY is not set"))
        self.assertNotEqual(code, 0)
        self.assertIn("REIN_PLANE_API_KEY", err)

    def test_workspace_missing_exits_nonzero_with_url(self):
        code, _out, err = self._run(ps.WorkspaceMissing("acme", "https://plane.example.com"))
        self.assertNotEqual(code, 0)
        self.assertIn("acme", err)
        self.assertIn("create-workspace", err)


class SyncCliSubprocessTests(unittest.TestCase):
    """AC3, real subprocess: the two config problems that need no network
    reached through the actual `rein` binary, HOME isolated to a tmpdir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _run(self, env):
        return subprocess.run(
            [sys.executable, REIN_BIN, "sync", "--plane", self.root],
            capture_output=True, text=True, env=env,
        )

    def test_no_plane_json_exits_zero(self):
        proc = self._run(_no_network_env(self.root))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("not configured", proc.stdout)

    def test_missing_api_key_exits_nonzero(self):
        _write_plane_json(self.root)
        env = _no_network_env(self.root)
        env.pop("REIN_PLANE_API_KEY", None)
        proc = self._run(env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("REIN_PLANE_API_KEY", proc.stderr)


# --------------------------------------------------------- D1: byte-identical


class ByteIdenticalWithoutPlaneJsonTests(unittest.TestCase):
    """D1: `rein state`, and the CLI commands `rein-step`/`rein-apply`'s own
    SKILL.md literally shell out to (`next`, `tasks`, `context`), produce
    byte-identical output whether or not `plane.json` is present -- proof
    that adding `rein sync --plane` was fully additive.

    Same directory both times (only `plane.json`'s presence toggles) so the
    comparison is never confused by two tmpdirs' names differing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        _make_flat_repo(self.root, "demo", [{"id": "T001", "title": "Do the thing"}])
        self.plane_json_path = os.path.join(self.root, "plane.json")

    def _run(self, *args):
        env = _no_network_env(self.root)
        proc = subprocess.run(
            [sys.executable, REIN_BIN, *args, self.root],
            capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _compare(self, *args):
        self.assertFalse(os.path.exists(self.plane_json_path))
        without = self._run(*args)
        _write_plane_json(self.root, workspace_slug="whatever", window=7, lang="en")
        try:
            with_plane = self._run(*args)
        finally:
            os.remove(self.plane_json_path)
        self.assertEqual(without, with_plane)

    def test_state_is_byte_identical(self):
        self._compare("state")

    def test_next_is_byte_identical(self):
        self._compare("next")

    def test_tasks_is_byte_identical(self):
        self._compare("tasks")

    def test_context_is_byte_identical(self):
        self._compare("context")


class NoPlaneReachableFromReinApplyOrReinStepTests(unittest.TestCase):
    """D1, mechanically, for the two commands `rein-apply`/`rein-step`
    actually run (per their own SKILL.md) rather than the four
    `ByteIdenticalWithoutPlaneJsonTests` substitutes for them (`state`,
    `next`, `tasks`, `context` -- the substitution is defensible, since the
    two skills are agent-driven and have no deterministic output to diff,
    but it leaves the two commands the criterion actually names unproven).

    A source-level guarantee is cheap and can name them directly: none of
    `loop.js` (the workflow both commands drive), `plan.py` (what T002
    touched), `verify.py`, `gate.py`, or `plan_check.py` mentions
    `plane.json` or imports `plane_client`/`plane_sync`/`plane_projection`/
    `humanize` -- the same shape as `PartialFailureTests.
    test_plane_sync_never_imports_events`, which pins the write direction
    only (round-2 review finding 3)."""

    FILES = {
        "loop.js": os.path.join(WORKFLOWS_DIR, "loop.js"),
        "plan.py": os.path.join(LIB_DIR, "plan.py"),
        "verify.py": os.path.join(LIB_DIR, "verify.py"),
        "gate.py": os.path.join(LIB_DIR, "gate.py"),
        "plan_check.py": os.path.join(LIB_DIR, "plan_check.py"),
    }

    # Actual import statements only -- not a bare "plane" substring, which
    # would also match harmless prose like a comment citing
    # "test_plane_projection.py" by name.
    _IMPORT_RE = re.compile(
        r"^\s*(?:import|from)\s+(plane_client|plane_sync|plane_projection|humanize)\b",
        re.MULTILINE,
    )

    def test_files_exist_where_expected(self):
        for label, path in self.FILES.items():
            self.assertTrue(os.path.isfile(path), f"{label} not found at {path}")

    def test_no_plane_json_literal_and_no_plane_or_humanize_import(self):
        for label, path in self.FILES.items():
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn("plane.json", src, f"{label} names plane.json")
            match = self._IMPORT_RE.search(src)
            self.assertIsNone(
                match, f"{label} imports {match.group(1) if match else ''}"
            )


if __name__ == "__main__":
    unittest.main()


class RemovingADependencyClearsItOnTheBoardTests(unittest.TestCase):
    """Re-emitting is only half of it -- the PATCH has to carry the field.

    `_work_item_entity` hashes `dependsOn`, so editing one re-emits the work
    item. But the sync built `description` only `if body:`, and
    `_work_item_body` returns `""` when a task has no dependencies. So the
    PATCH went out with no `description` key at all, Plane kept the previous
    value, and a removed dependency stayed on the card forever. D8 makes
    that line the ONLY place a dependency is visible, so a stale one reads
    as current -- worse than showing none.

    `ContentHashDedupTests.test_editing_a_dependency_re_emits_the_work_item`
    stops at re-emission and never inspects the payload, which is why the
    suite was green.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.change_dir = _make_repo(self.root, "demo", [
            {"id": "T001", "title": "First"},
            {"id": "T002", "title": "Second", "dependsOn": ["T001"]},
        ])
        _write_plane_json(self.root)

    def _sync(self):
        """A URL-aware fake, not a fixed queue: the second run emits fewer
        entities than the first, so a positional script silently desyncs and
        the test stops measuring what it claims to."""

        class Adaptive:
            def __init__(self):
                self.calls = []

            def request(self, method, url, headers, body):
                parsed = json.loads(body) if body else None
                self.calls.append((method, url, parsed))
                if method == "GET" and url.endswith("/states/"):
                    payload = {"results": [{"id": "sb", "group": "backlog"}]}
                elif method == "GET" and url.endswith("/projects/"):
                    payload = {"results": []}
                elif "/module-issues/" in url:
                    payload = {"issues": []}
                elif "/modules/" in url:
                    payload = {"id": "m1"}
                elif "/issues/" in url:
                    payload = {"id": "w-" + str(len(self.calls))}
                else:
                    payload = {"id": "p1"}
                return (201 if method == "POST" else 200), {}, json.dumps(payload).encode()

        transport = Adaptive()

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport,
                                  sleep=lambda s: None, rate_limit=None,
                                  env={"REIN_PLANE_API_KEY": "k"})

        ps.run_sync(self.root, client_factory=factory)
        return transport

    def _bodies_for(self, transport, external_suffix):
        return [
            b for _m, _u, b in transport.calls
            if isinstance(b, dict) and str(b.get("external_id", "")).endswith(external_suffix)
        ]

    def test_the_dependency_reaches_plane_in_the_first_place(self):
        transport = self._sync()
        bodies = self._bodies_for(transport, ":T002")
        self.assertTrue(bodies)
        self.assertEqual(bodies[0].get("description"), "Depends on: T001")

    def test_removing_it_sends_an_empty_description_rather_than_omitting_it(self):
        self._sync()
        # Same plan, dependency removed.
        _write_tasks_md(self.change_dir, "demo", [
            {"id": "T001", "title": "First"},
            {"id": "T002", "title": "Second"},
        ])
        transport = self._sync()
        bodies = self._bodies_for(transport, ":T002")
        self.assertTrue(bodies)
        self.assertIn(
            "description", bodies[0],
            "omitting the key makes the PATCH partial -- Plane keeps the stale line",
        )
        self.assertEqual(bodies[0]["description"], "")
