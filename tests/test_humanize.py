#!/usr/bin/env python3
"""Tests for plugins/rein/lib/humanize.py -- decorative prose for the Plane
board, never load-bearing (D7, D9).

No test here shells out to a real agent: every `humanize()`/`HumanizeCache`
call in this file injects its own `invoke`, so the suite never spawns a
process and never needs credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "rein", "lib"))

import humanize  # noqa: E402
import plane_client as pc  # noqa: E402
import plane_sync as ps  # noqa: E402


def _write_plane_json(root: str, **fields) -> None:
    with open(os.path.join(root, "plane.json"), "w", encoding="utf-8") as fh:
        json.dump(fields, fh)


def _write_tasks_md(root: str, change: str, tasks: list[dict]) -> None:
    """A plain flat `tasks.md` at `root` -- enough for `plane_sync.run_sync`
    to resolve one implicit change with no `openspec` layout involved."""
    lines = [f"# Change: {change}", "", "## Why", "", "Fixture for plane_sync integration.", ""]
    for t in tasks:
        lines.append(f"- [ ] {t['id']} {t['title']}")
        lines.append("  - Type: implementation")
        lines.append("  - Depends on: none")
        lines.append("  - Human review: false")
        lines.append("  - Verification: `true`")
        lines.append("  - Acceptance:")
        lines.append("    - it works")
    with open(os.path.join(root, "tasks.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


class _FakeTransport:
    """Replays a fixed queue of (status, body) responses -- same shape as
    tests/test_plane_projection.py's, duplicated here so this file stays
    self-contained (existing project convention)."""

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


def _capturing_invoke(reply: str = "Prose from the agent"):
    calls = []

    def invoke(text, kind, lang, *, timeout):
        calls.append({"text": text, "kind": kind, "lang": lang, "timeout": timeout})
        return reply

    return invoke, calls


class LangDefaultAndOverrideTests(unittest.TestCase):
    """Acceptance 1: `lang` defaults to `plane.json`'s `lang` (itself
    defaulting to "es"); an explicit `lang` reaches the request unchanged."""

    def test_default_lang_is_es_with_no_plane_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoke, calls = _capturing_invoke()
            humanize.humanize("Fix the login bug", humanize.KIND_TITLE, root=tmp, invoke=invoke)
            self.assertEqual(calls[0]["lang"], "es")

    def test_default_lang_reads_plane_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_plane_json(tmp, base_url="https://plane.example.com", lang="fr")
            invoke, calls = _capturing_invoke()
            humanize.humanize("Fix the login bug", humanize.KIND_TITLE, root=tmp, invoke=invoke)
            self.assertEqual(calls[0]["lang"], "fr")

    def test_explicit_lang_reaches_request_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            # plane.json says "fr" -- an explicit "en" must win outright, not
            # merge or fall back to the file.
            _write_plane_json(tmp, lang="fr")
            invoke, calls = _capturing_invoke()
            humanize.humanize("Fix the login bug", humanize.KIND_TITLE, "en", root=tmp, invoke=invoke)
            self.assertEqual(calls[0]["lang"], "en")


class FailureModesReturnRawInputTests(unittest.TestCase):
    """Acceptance 2: every failure mode returns the raw input verbatim (D7)."""

    RAW = "Fix the login bug that drops the session on refresh"

    def _assert_falls_back(self, invoke, **kwargs):
        result = humanize.humanize(self.RAW, humanize.KIND_TITLE, "en", invoke=invoke, **kwargs)
        self.assertEqual(result, self.RAW)

    def test_no_agent_on_path(self):
        def invoke(text, kind, lang, *, timeout):
            raise FileNotFoundError("no such file: fake-agent")

        self._assert_falls_back(invoke)

    def test_non_zero_exit(self):
        def invoke(text, kind, lang, *, timeout):
            raise humanize.HumanizeAgentError("agent exited 1: something broke")

        self._assert_falls_back(invoke)

    def test_timeout(self):
        def invoke(text, kind, lang, *, timeout):
            raise subprocess.TimeoutExpired(cmd="fake-agent", timeout=timeout)

        self._assert_falls_back(invoke)

    def test_empty_output(self):
        def invoke(text, kind, lang, *, timeout):
            return "   "

        self._assert_falls_back(invoke)

    def test_output_past_configured_cap(self):
        def invoke(text, kind, lang, *, timeout):
            return "x" * 11

        self._assert_falls_back(invoke, cap=10)


class NeverRunsOnNamesOrIdentifiersTests(unittest.TestCase):
    """Acceptance 3: sanitising is mechanical (T003/D9) -- the humanizer is
    never on that path at all, so replacing it with a function that raises
    changes nothing about the legality of the result."""

    def test_bad_repo_name_still_legal_with_humanizer_raising(self):
        bad_name = "proxima-website! (v2) -- <beta>"

        def raising(*args, **kwargs):
            raise AssertionError("humanize() must never be called for names/identifiers")

        with mock.patch.object(humanize, "humanize", raising):
            clean = pc.safe_name(bad_name)
            identifier = pc.safe_identifier(bad_name)

        self.assertTrue(clean)
        self.assertFalse(any(ch in pc.FORBIDDEN_NAME_CHARS for ch in clean))
        self.assertLessEqual(len(identifier), 12)
        self.assertTrue(identifier)

    def _make_root(self, tasks):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        _write_tasks_md(root, "demo", tasks)
        _write_plane_json(root, base_url="https://plane.example.com", workspace_slug="acme", humanize=True)
        return root

    def _project_script(self):
        return _FakeTransport([
            (200, {"id": "ws-1"}),                                        # GET workspace
            (200, {"results": []}),                                        # GET list projects
            (201, {"id": "proj-1"}),                                        # POST create project
            (200, {"id": "proj-1"}),                                        # PATCH module_view
            (201, {"id": "mod-1"}),                                          # POST module
            (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),   # GET states
            (201, {"id": "wi-1"}),                                            # POST work item
            (200, {"issues": ["wi-1"]}),                                      # POST attach
        ])

    def _run_through_plane_sync(self, repo_name, invoke):
        root = self._make_root([{"id": "T001", "title": "Do the thing"}])
        transport = self._project_script()

        def factory(base_url, workspace_slug, env=None):
            return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                   env={"REIN_PLANE_API_KEY": "test-key"})

        cache = humanize.HumanizeCache(root=root, invoke=invoke)
        with mock.patch.object(ps, "repos_for", return_value=[(repo_name, root)]):
            report = ps.run_sync(root, client_factory=factory, humanize_cache=cache)
        return report, transport

    def test_project_name_and_identifier_are_mechanical_through_the_real_sync(self):
        """T003's guarantee (24 colliding repo names -> 24 distinct
        identifiers) only holds in production if `safe_identifier()` sees
        the raw repo name, never an LLM's reply -- and the identifier must
        not churn across syncs depending on what that reply happened to be.
        Proven here through `plane_sync.run_sync` itself, not a direct
        `safe_name`/`safe_identifier` call, so a future regression that
        routes the Project's `name` through the humanizer again is caught
        at the point it would actually happen."""
        bad_name = "proxima-website! (v2) -- <beta>"

        def working_invoke(text, kind, lang, *, timeout):
            return "Sitio web de Proxima"

        def raising_invoke(text, kind, lang, *, timeout):
            raise RuntimeError("the agent is unreachable")

        report_working, transport_working = self._run_through_plane_sync(bad_name, working_invoke)
        report_raising, transport_raising = self._run_through_plane_sync(bad_name, raising_invoke)

        self.assertEqual(report_working["failed"], [])
        self.assertEqual(report_raising["failed"], [])

        create_working = next(c for c in transport_working.calls if c[0] == "POST" and c[1].endswith("/projects/"))
        create_raising = next(c for c in transport_raising.calls if c[0] == "POST" and c[1].endswith("/projects/"))

        expected_name = pc.safe_name(bad_name)
        expected_identifier = pc.safe_identifier(bad_name)

        for body in (create_working[2], create_raising[2]):
            self.assertEqual(body["name"], expected_name)
            self.assertEqual(body["identifier"], expected_identifier)
            self.assertFalse(any(ch in pc.FORBIDDEN_NAME_CHARS for ch in body["name"]))

        # Identical name/identifier whether the humanizer worked or raised
        # outright -- the identity fields never depend on its output (D9).
        self.assertEqual(create_working[2]["name"], create_raising[2]["name"])
        self.assertEqual(create_working[2]["identifier"], create_raising[2]["identifier"])

        # The humanized text, when it worked, rides `description` -- a
        # field with no identity role -- never `name`.
        self.assertEqual(create_working[2]["description"], "Sitio web de Proxima")
        self.assertNotEqual(create_working[2]["name"], "Sitio web de Proxima")


class CachedByContentHashTests(unittest.TestCase):
    """Acceptance 4: the humanizer runs at most once per entity per sync,
    cached by content hash."""

    def test_repeated_title_invokes_agent_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoke, calls = _capturing_invoke("Session drops on refresh, now fixed.")
            cache = humanize.HumanizeCache(root=tmp, invoke=invoke)

            first = cache.humanize("Fix the login bug", humanize.KIND_TITLE)
            second = cache.humanize("Fix the login bug", humanize.KIND_TITLE)

            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)

    def test_different_content_invokes_agent_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoke, calls = _capturing_invoke("Prose")
            cache = humanize.HumanizeCache(root=tmp, invoke=invoke)

            cache.humanize("Fix the login bug", humanize.KIND_TITLE)
            cache.humanize("Add the export button", humanize.KIND_TITLE)

            self.assertEqual(len(calls), 2)


def _compute_upsert_set(entities, previous_hashes, humanizer):
    """Stand-in for the projection's "what changed" step (T005/T006):
    decides membership from `content_hash()` alone, mechanically, and only
    afterwards asks the humanizer for display text -- decoration that must
    never be able to change the set already decided above."""
    upsert = set()
    for entity in entities:
        current_hash = humanize.content_hash(entity["title"], humanize.KIND_TITLE, "es")
        if previous_hashes.get(entity["id"]) != current_hash:
            upsert.add(entity["id"])
            try:
                humanizer.humanize(entity["title"], humanize.KIND_TITLE, "es")
            except Exception:
                pass  # decorative only -- never allowed to reach the caller
    return upsert


class HumanizeNotOnTheSyncDecisionPathTests(unittest.TestCase):
    """Acceptance 5: `humanize()` is never on the path deciding *what* to
    sync -- the upsert set is identical whether the humanizer works or
    raises outright."""

    def test_upsert_set_identical_with_humanizer_raising(self):
        entities = [
            {"id": "T1", "title": "Fix the login bug"},
            {"id": "T2", "title": "Fix the login bug"},
            {"id": "T3", "title": "Add the export button"},
        ]
        previous_hashes = {
            "T1": humanize.content_hash("an older title", humanize.KIND_TITLE, "es"),
            # T2, T3 have no recorded hash yet -- both are new.
        }

        working = humanize.HumanizeCache(invoke=lambda *a, **k: "Prosa generada")
        upsert_with_working_humanizer = _compute_upsert_set(entities, previous_hashes, working)

        class RaisingHumanizer:
            def humanize(self, *args, **kwargs):
                raise RuntimeError("the agent is unreachable")

        upsert_with_raising_humanizer = _compute_upsert_set(entities, previous_hashes, RaisingHumanizer())

        self.assertEqual(upsert_with_working_humanizer, {"T1", "T2", "T3"})
        self.assertEqual(upsert_with_working_humanizer, upsert_with_raising_humanizer)

    def test_the_real_projection_selects_the_same_entities_with_humanizer_raising(self):
        """Round-1 review, finding 3: the test above only proves the
        *shape* of the guarantee against a local re-implementation --
        `_compute_upsert_set` is its own docstring's "stand-in for the
        projection". This calls T005's real `plane_projection.select()`
        directly, so a future regression that routes `select()` itself
        through a humanizer is caught here, not in a fixture the real path
        never runs (the defect class T002's acceptance already called out)."""
        import plane_projection as pp

        state = [{
            "root": "/repo",
            "change": "demo",
            "planPath": "/repo/changes/demo/tasks.md",
            "planExists": True,
            "lastTouchedDays": 5,
            "tasks": [
                {"taskId": "T001", "title": "Fix the login bug", "checked": False,
                 "transition": "planned", "when": "2026-01-01T00:00:00", "commit": "abc123"},
                {"taskId": "T002", "title": "Add the export button", "checked": False,
                 "transition": "planned", "when": "2026-01-01T00:00:00", "commit": "abc123"},
            ],
        }]

        entities_a = pp.select(state, window_days=30, record={})
        entities_b = pp.select(state, window_days=30, record={})
        self.assertTrue(entities_a)
        self.assertEqual(entities_a, entities_b)

        # Structural proof behind the byte-identical result: `select()`
        # has no way to reach a humanizer, working or raising, at all.
        with open(pp.__file__, encoding="utf-8") as fh:
            self.assertNotIn("humanize", fh.read())

    def test_run_sync_applies_the_same_entity_keys_and_hashes_regardless_of_the_humanizer(self):
        """The integration-level half of finding 3: two full
        `plane_sync.run_sync` passes over the same fixture -- one with a
        working humanizer, one with a `humanize_cache` whose `.humanize()`
        raises outright -- record the identical set of applied entity
        keys and content hashes. This is what actually got projected and
        persisted, not a re-derivation of it."""

        def make_root():
            root = tempfile.mkdtemp()
            self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
            _write_tasks_md(root, "demo", [
                {"id": "T001", "title": "Fix the login bug"},
                {"id": "T002", "title": "Add the export button"},
            ])
            _write_plane_json(root, base_url="https://plane.example.com", workspace_slug="acme", humanize=True)
            return root

        def script():
            return _FakeTransport([
                (200, {"id": "ws-1"}),
                (200, {"results": []}),
                (201, {"id": "proj-1"}),
                (200, {"id": "proj-1"}),
                (201, {"id": "mod-1"}),
                (200, {"results": [{"id": "s-backlog", "group": "backlog"}]}),
                (201, {"id": "wi-1"}),
                (200, {"issues": ["wi-1"]}),
                (201, {"id": "wi-2"}),
                (200, {"issues": ["wi-2"]}),
            ])

        def run(humanize_cache):
            root = make_root()
            transport = script()

            def factory(base_url, workspace_slug, env=None):
                return pc.PlaneClient(base_url, workspace_slug, transport=transport, sleep=lambda s: None,
                                       env={"REIN_PLANE_API_KEY": "test-key"})

            with mock.patch.object(ps, "repos_for", return_value=[("demo-repo", root)]):
                report = ps.run_sync(root, client_factory=factory, humanize_cache=humanize_cache)
            record_path = os.path.join(root, ps.RECORD_RELATIVE_PATH)
            with open(record_path, encoding="utf-8") as fh:
                record = json.load(fh)
            return report, record

        working_cache = humanize.HumanizeCache(invoke=lambda *a, **k: "Prosa generada")

        class RaisingHumanizeCache:
            """Unlike the real `HumanizeCache`, raises straight out of
            `.humanize()` -- proving `run_sync`'s selection/record
            bookkeeping does not route through it either."""

            def humanize(self, text, kind, lang=None):
                raise RuntimeError("the agent is unreachable")

        report_working, record_working = run(working_cache)
        report_raising, record_raising = run(RaisingHumanizeCache())

        self.assertEqual(report_working["failed"], [])
        self.assertEqual(report_raising["failed"], [])
        self.assertEqual(sorted(report_working["applied"]), sorted(report_raising["applied"]))
        self.assertEqual(record_working, record_raising)


if __name__ == "__main__":
    unittest.main()
