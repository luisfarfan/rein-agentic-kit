#!/usr/bin/env python3
"""Tests for plugins/rein/lib/plane_client.py -- the per-entity write
strategy measured against a live Plane 1.4.1 Community instance (D3).

No test here opens a socket: every `PlaneClient` is built with an injected
`FakeTransport` that replays scripted (status, body) pairs and records the
exact request sequence. `time.sleep` is likewise always injected.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "rein", "lib"))

import plane_client as pc  # noqa: E402


class FakeTransport:
    """Replays a fixed queue of (status, body) responses and records every
    (method, url, parsed_json_body) call made against it, in order."""

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

    @property
    def methods(self):
        return {m for m, _, _ in self.calls}


def _client(transport, **kwargs):
    kwargs.setdefault("env", {"REIN_PLANE_API_KEY": "test-key"})
    kwargs.setdefault("sleep", lambda _seconds: None)
    return pc.PlaneClient("https://plane.example.com", "acme", transport=transport, **kwargs)


class WorkItemAndModuleUpsertTests(unittest.TestCase):
    """Acceptance 1: POST -> 201 | 409-with-id -> PATCH, exact sequence."""

    def test_post_201_created_no_patch(self):
        transport = FakeTransport([(201, {"id": "wi-1", "name": "Fix the thing"})])
        client = _client(transport)
        result = client.upsert_work_item("proj-1", "Fix the thing", "rein:ws:repo:change:T001")
        self.assertEqual(result["id"], "wi-1")
        self.assertEqual(
            [(m, p) for m, _, p in transport.calls],
            [("POST", {"name": "Fix the thing", "external_id": "rein:ws:repo:change:T001", "external_source": "rein"})],
        )

    def test_post_409_reads_id_then_patches(self):
        transport = FakeTransport([
            (409, {"id": "wi-9", "name": "Fix the thing"}),
            (200, {"id": "wi-9", "name": "Fix the thing", "updated": True}),
        ])
        client = _client(transport)
        result = client.upsert_work_item("proj-1", "Fix the thing", "rein:ws:repo:change:T001")
        self.assertTrue(result["updated"])
        methods = [m for m, _, _ in transport.calls]
        urls = [u for _, u, _ in transport.calls]
        self.assertEqual(methods, ["POST", "PATCH"])
        self.assertTrue(urls[1].endswith("/issues/wi-9/"))

    def test_module_upsert_same_sequence(self):
        transport = FakeTransport([
            (409, {"id": "mod-2"}),
            (200, {"id": "mod-2"}),
        ])
        client = _client(transport)
        client.upsert_module("proj-1", "Change window", "rein:ws:repo:change:_")
        self.assertEqual([m for m, _, _ in transport.calls], ["POST", "PATCH"])

    def test_module_attach_is_separate_post(self):
        """Acceptance 7: creation never links -- a separate POST does."""
        transport = FakeTransport([(200, {"issues": ["wi-1"]})])
        client = _client(transport)
        client.attach_work_item_to_module("proj-1", "mod-2", "wi-1")
        method, url, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/modules/mod-2/module-issues/"))
        self.assertEqual(body, {"issues": ["wi-1"]})


class ProjectUpsertTests(unittest.TestCase):
    """Acceptance 2: list-and-match client side; never reads id from 409 (D3)."""

    def test_match_on_external_id_and_source_patches(self):
        transport = FakeTransport([
            (200, {"results": [
                {"id": "proj-old", "external_id": "rein:ws:other:c:_", "external_source": "rein"},
                {"id": "proj-1", "external_id": "rein:ws:repo:c:_", "external_source": "rein"},
            ]}),
            (200, {"id": "proj-1", "name": "repo", "updated": True}),
        ])
        client = _client(transport)
        result = client.upsert_project("repo", "rein:ws:repo:c:_")
        self.assertTrue(result["updated"])
        methods = [m for m, _, _ in transport.calls]
        self.assertEqual(methods, ["GET", "PATCH"])

    def test_no_match_creates_via_post(self):
        transport = FakeTransport([
            (200, {"results": []}),
            (201, {"id": "proj-new", "name": "repo"}),
        ])
        client = _client(transport)
        result = client.upsert_project("repo", "rein:ws:repo:c:_")
        self.assertEqual(result["id"], "proj-new")
        self.assertEqual([m for m, _, _ in transport.calls], ["GET", "POST"])

    def test_external_source_mismatch_is_not_a_match(self):
        transport = FakeTransport([
            (200, {"results": [{"id": "proj-x", "external_id": "rein:ws:repo:c:_", "external_source": "someone-else"}]}),
            (201, {"id": "proj-new", "name": "repo"}),
        ])
        client = _client(transport)
        result = client.upsert_project("repo", "rein:ws:repo:c:_")
        self.assertEqual(result["id"], "proj-new")

    def test_measured_409_without_id_raises_named_conflict_not_crash(self):
        """Replays the measured `409 {"name": "The project name is already
        taken"}` -- no `id` anywhere in the body -- and proves the project
        path surfaces a named error instead of crashing on a missing key."""
        transport = FakeTransport([
            (200, {"results": []}),
            (409, {"name": "The project name is already taken"}),
        ])
        client = _client(transport)
        with self.assertRaises(pc.PlaneConflictError) as ctx:
            client.upsert_project("repo", "rein:ws:repo:c:_")
        self.assertEqual(ctx.exception.entity_kind, "project")
        self.assertIn("repo", str(ctx.exception))
        # Never advanced past the POST to try reading/patching an id.
        self.assertEqual([m for m, _, _ in transport.calls], ["GET", "POST"])

    def test_project_flow_never_reads_id_out_of_a_409_body(self):
        """Even when the 409 body DOES happen to carry an `id`-shaped key,
        the project path must not use it -- that mechanism is exclusive to
        work items/modules (D3)."""
        transport = FakeTransport([
            (200, {"results": []}),
            (409, {"id": "should-be-ignored", "name": "The project name is already taken"}),
        ])
        client = _client(transport)
        with self.assertRaises(pc.PlaneConflictError):
            client.upsert_project("repo", "rein:ws:repo:c:_")
        # No PATCH was ever issued against "should-be-ignored".
        self.assertEqual([m for m, _, _ in transport.calls], ["GET", "POST"])


class SanitisingTests(unittest.TestCase):
    """Acceptance 3: safe_name / safe_identifier."""

    def test_safe_name_strips_every_forbidden_character(self):
        dirty = "".join(pc.FORBIDDEN_NAME_CHARS)
        self.assertEqual(pc.safe_name(f"repo{dirty}name"), "reponame")

    def test_safe_identifier_is_at_most_twelve_chars(self):
        self.assertLessEqual(len(pc.safe_identifier("a-very-long-repository-name-indeed")), 12)

    def test_safe_identifier_is_stem_plus_hash_of_full_name(self):
        ident = pc.safe_identifier("proxima-website")
        self.assertEqual(len(ident), 12)
        self.assertTrue(ident.startswith("PROXIMAWE"[:8]))

    # The 24 colliding repo names, literal in this file -- data, not a
    # repository to fetch. Naive truncate-to-12 collapses these to 9
    # distinct prefixes (verified below); safe_identifier() must not.
    COLLIDING_REPO_NAMES = [
        "proxima-website",
        "proxima-website-v2",
        "proxima-website-v3",
        "proxima-website2",
        "proxima-website-staging",
        "proxima-website-legacy",
        "proxima-webhooks",
        "proxima-webhooks-v2",
        "proxima-api-gateway",
        "proxima-api-gateway-v2",
        "proxima-api-gateway-internal",
        "proxima-api-service",
        "proxima-backend-core",
        "proxima-backend-core-v2",
        "proxima-backend-workers",
        "proxima-mobile-ios",
        "proxima-mobile-android",
        "proxima-mobile-shared",
        "proxima-data-pipeline",
        "proxima-data-pipeline-v2",
        "proxima-infra-terraform",
        "proxima-infra-terraform-v2",
        "proxima-design-system",
        "proxima-design-system-v2",
    ]

    def test_naive_truncation_actually_collides_on_this_fixture(self):
        """Sanity check on the fixture itself: naive alnum-uppercase-then-
        truncate-to-12 must NOT already be collision-free, or this test
        would prove nothing about safe_identifier()."""
        naive = {
            "".join(ch for ch in name if ch.isalnum()).upper()[:12]
            for name in self.COLLIDING_REPO_NAMES
        }
        self.assertLess(len(naive), len(self.COLLIDING_REPO_NAMES))

    def test_24_colliding_names_produce_24_distinct_identifiers(self):
        self.assertEqual(len(self.COLLIDING_REPO_NAMES), 24)
        identifiers = [pc.safe_identifier(name) for name in self.COLLIDING_REPO_NAMES]
        for ident in identifiers:
            self.assertLessEqual(len(ident), 12)
        self.assertEqual(len(set(identifiers)), 24)


class ConflictErrorTests(unittest.TestCase):
    """Acceptance 4: named error, never KeyError, message names the entity."""

    def test_work_item_conflict_without_id_is_named_error_not_keyerror(self):
        transport = FakeTransport([(400, {"name": "collides"})])
        client = _client(transport)
        with self.assertRaises(pc.PlaneConflictError) as ctx:
            client.upsert_work_item("proj-1", "Ship the thing", "rein:ws:repo:c:T009")
        self.assertNotIsInstance(ctx.exception, KeyError)
        self.assertEqual(ctx.exception.entity_kind, "work item")
        self.assertIn("Ship the thing", str(ctx.exception))


class RetryBackoffTests(unittest.TestCase):
    """Acceptance 5: exponential backoff on error_code 5900 and HTTP 429,
    bounded attempts, sleep injected -- never real."""

    def test_retries_on_error_code_5900_then_gives_up(self):
        transport = FakeTransport([(200, {"error_code": 5900})] * 3)
        sleeps = []
        client = _client(transport, sleep=sleeps.append, max_attempts=3, base_delay=0.1)
        with self.assertRaises(pc.PlaneRetryExhausted) as ctx:
            client.list_projects()
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [0.1, 0.2])  # exponential, one fewer than attempts

    def test_retries_on_http_429_then_succeeds(self):
        transport = FakeTransport([
            (429, {}),
            (429, {}),
            (200, {"results": []}),
        ])
        sleeps = []
        client = _client(transport, sleep=sleeps.append, max_attempts=5, base_delay=0.05)
        result = client.list_projects()
        self.assertEqual(result, [])
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [0.05, 0.1])

    def test_sleep_is_injected_never_real(self):
        real_sleep = mock.patch("time.sleep")
        with real_sleep as mocked:
            transport = FakeTransport([(429, {}), (200, {"results": []})])
            client = _client(transport, sleep=lambda _s: None, max_attempts=5)
            client.list_projects()
            mocked.assert_not_called()


class ModuleViewOrderingTests(unittest.TestCase):
    """Acceptance 6: Project creation is followed by
    PATCH {"module_view": true} before any Module is attempted."""

    def test_ensure_project_then_module_view_then_module(self):
        transport = FakeTransport([
            (200, {"results": []}),          # list_projects: no match
            (201, {"id": "proj-1"}),          # POST project
            (200, {"id": "proj-1", "module_view": True}),  # PATCH module_view
            (201, {"id": "mod-1"}),           # POST module
        ])
        client = _client(transport)
        project = client.ensure_project("repo", "rein:ws:repo:c:_")
        client.upsert_module(project["id"], "Change window", "rein:ws:repo:c:_")

        methods = [m for m, _, _ in transport.calls]
        urls = [u for _, u, _ in transport.calls]
        self.assertEqual(methods, ["GET", "POST", "PATCH", "POST"])
        self.assertTrue(urls[2].endswith("/projects/proj-1/"))
        self.assertEqual(transport.calls[2][2], {"module_view": True})
        self.assertTrue(urls[3].endswith("/modules/"))


class ExternalIdTests(unittest.TestCase):
    """Acceptance 8: rein:{workspace}:{repo}:{change}:{task}, escaped."""

    def test_shape(self):
        self.assertEqual(
            pc.make_external_id("acme", "web", "change-1", "T001"),
            "rein:acme:web:change-1:T001",
        )

    def test_colon_in_a_component_cannot_forge_another_identity(self):
        forged = pc.make_external_id("ws", "a:b", "change", "task")
        real = pc.make_external_id("ws:a", "b", "change", "task")
        self.assertNotEqual(forged, real)


class ApiKeyTests(unittest.TestCase):
    """Acceptance 9: REIN_PLANE_API_KEY only, never a file."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self._tmpdir)
        with open(os.path.join(self._tmpdir, "plane.json"), "w", encoding="utf-8") as fh:
            json.dump({"api_key": "file-secret-must-never-be-used"}, fh)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_env_var_raises_even_with_plane_json_present(self):
        with self.assertRaises(pc.PlaneAuthError):
            pc.PlaneClient("https://plane.example.com", "acme", env={})

    def test_env_var_wins_over_file_when_both_present(self):
        client = pc.PlaneClient(
            "https://plane.example.com", "acme", env={"REIN_PLANE_API_KEY": "env-secret"}
        )
        self.assertEqual(client._api_key, "env-secret")


class NoDeleteAndTransportSafetyTests(unittest.TestCase):
    """Acceptance 10 & 11: no DELETE, ever; the default transport is never
    constructed because every test injects its own."""

    def test_emitted_method_set_is_exactly_get_post_patch(self):
        transport = FakeTransport([
            (200, {"results": []}),          # list_projects
            (201, {"id": "proj-1"}),          # POST project
            (200, {"id": "proj-1"}),          # PATCH module_view
            (409, {"id": "mod-1"}),           # POST module -> conflict
            (200, {"id": "mod-1"}),           # PATCH module
            (201, {"id": "wi-1"}),            # POST work item
            (200, {"issues": ["wi-1"]}),      # POST module-issues
        ])
        client = _client(transport)
        project = client.ensure_project("repo", "rein:ws:repo:c:_")
        module = client.upsert_module(project["id"], "Window", "rein:ws:repo:c:_")
        client.upsert_work_item(project["id"], "Task", "rein:ws:repo:c:T001")
        client.attach_work_item_to_module(project["id"], module["id"], "wi-1")

        self.assertEqual(transport.methods, {"GET", "POST", "PATCH"})
        self.assertNotIn("DELETE", transport.methods)

    def test_default_transport_never_constructed(self):
        with mock.patch.object(pc, "PlaneTransport") as ctor:
            transport = FakeTransport([(200, {"results": []})])
            client = _client(transport)
            client.list_projects()
            ctor.assert_not_called()


class WorkspaceExistenceUsesAReachableEndpointTests(unittest.TestCase):
    """The URL itself has to be one that exists.

    An injected transport replays whatever URL it is handed, so it will
    happily confirm an endpoint that is not there. This suite was green
    against `GET /api/v1/workspaces/{slug}/` — a path Plane does not serve at
    all (`apps/api/plane/api/urls/` has no workspace module), so a live run
    answered `401 "credentials were not provided"` for a workspace that
    existed, with a valid key. Measured against a real 1.4.1 instance:

        GET /v1/workspaces/<real>/projects/    -> 200
        GET /v1/workspaces/<absent>/projects/  -> 403   (not 404)
        GET /v1/workspaces/<real>/             -> 401   (no such route)

    These tests pin the path and both status codes so the endpoint cannot
    silently become an invented one again.
    """

    def test_it_asks_the_projects_collection_not_the_workspace_itself(self):
        transport = FakeTransport([(200, {"results": []})])
        client = _client(transport)
        client.get_workspace()
        method, url, _ = transport.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(
            url.endswith("/api/v1/workspaces/acme/projects/"),
            f"asked {url!r} — /api/v1/workspaces/<slug>/ is not a route Plane serves",
        )

    def test_403_means_absent_because_that_is_what_plane_answers(self):
        client = _client(FakeTransport([(403, {"detail": "not a member"})]))
        self.assertIsNone(client.get_workspace())

    def test_404_also_means_absent(self):
        client = _client(FakeTransport([(404, {"detail": "not found"})]))
        self.assertIsNone(client.get_workspace())

    def test_401_is_raised_not_swallowed_as_absent(self):
        """A bad key must not read as "the workspace does not exist"."""
        client = _client(FakeTransport([(401, {"detail": "no credentials"})]))
        with self.assertRaises(pc.PlaneRequestError):
            client.get_workspace()


if __name__ == "__main__":
    unittest.main()


class PacingStaysUnderTheServerWindowTests(unittest.TestCase):
    """Backoff alone cannot recover from a window you have already saturated.

    Measured: the instance enforces `API_KEY_RATE_LIMIT=60/minute` over a
    sliding window. With no pacing the client burns all 60 permits in about
    three seconds; from there every call throttles, and the old budget of
    1+2+4+8 = 15s never reached the ~57s needed for the oldest permit to
    expire. A real sync of the 30-day window (~900-1200 calls) would have
    spent hours sleeping and failed most entities, while
    `test_retries_on_http_429_then_succeeds` stayed green on a scripted
    `429, 429, 200`.
    """

    def _paced_client(self, responses, limit=3):
        self.slept = []
        self.now = [0.0]

        def sleep(seconds):
            self.slept.append(seconds)
            self.now[0] += seconds

        transport = FakeTransport(responses)
        client = pc.PlaneClient(
            "https://plane.example.com", "acme", transport=transport,
            sleep=sleep, clock=lambda: self.now[0], rate_limit=limit,
            env={"REIN_PLANE_API_KEY": "k"},
        )
        return client, transport

    def test_it_waits_rather_than_exceeding_the_window(self):
        client, _ = self._paced_client([(200, {"results": []})] * 4, limit=3)
        for _ in range(4):
            client._request("GET", "/x")
        self.assertTrue(self.slept, "the 4th call in a 3-per-window budget must wait")
        self.assertAlmostEqual(sum(self.slept), pc.RATE_WINDOW_SECONDS, places=3)

    def test_calls_within_the_budget_never_sleep(self):
        client, _ = self._paced_client([(200, {"results": []})] * 3, limit=3)
        for _ in range(3):
            client._request("GET", "/x")
        self.assertEqual(self.slept, [])

    def test_cumulative_backoff_budget_exceeds_the_server_window(self):
        """The arithmetic the old defaults failed: retries must outlast 60s."""
        budget = sum(
            min(pc.DEFAULT_BASE_DELAY * (2 ** i), pc.MAX_BACKOFF_SECONDS)
            for i in range(pc.DEFAULT_MAX_ATTEMPTS - 1)
        )
        self.assertGreater(budget, pc.RATE_WINDOW_SECONDS)

    def test_retry_after_header_is_obeyed_over_the_guess(self):
        """The server knows when the window frees; we do not."""
        self.slept = []
        transport = FakeTransport([(429, {"detail": "slow down"}), (200, {"ok": True})])
        transport.header_queue = [{"Retry-After": "7"}, {}]
        original = transport.request

        def with_headers(method, url, headers, body):
            status, _h, raw = original(method, url, headers, body)
            return status, (transport.header_queue.pop(0) if transport.header_queue else {}), raw

        transport.request = with_headers
        client = pc.PlaneClient(
            "https://plane.example.com", "acme", transport=transport,
            sleep=self.slept.append, rate_limit=None, env={"REIN_PLANE_API_KEY": "k"},
        )
        client._request("GET", "/x")
        self.assertEqual(self.slept, [7.0])


class BaseUrlDecidesWhereTheKeyTravelsTests(unittest.TestCase):
    """D4 calls `plane.json` "safe to commit" — so it is low-trust input.

    Every request carries `X-Api-Key`, and `base_url` alone chooses the host
    it goes to. A contributor editing that one field in a pull request would
    exfiltrate the key on the next `rein sync --plane`. Plain http is allowed
    only to loopback, where nothing leaves the machine.
    """

    def _build(self, base_url):
        return pc.PlaneClient(base_url, "acme", transport=FakeTransport([]),
                              sleep=lambda _s: None, env={"REIN_PLANE_API_KEY": "k"})

    def test_https_anywhere_is_fine(self):
        self.assertTrue(self._build("https://plane.example.com/"))

    def test_http_to_localhost_is_fine(self):
        for url in ("http://localhost:8080", "http://127.0.0.1:8080"):
            self.assertTrue(self._build(url))

    def test_http_to_a_remote_host_is_refused(self):
        with self.assertRaises(pc.PlaneConfigError) as ctx:
            self._build("http://evil.example.com")
        self.assertIn("clear text", str(ctx.exception))

    def test_empty_and_schemeless_are_refused_by_name(self):
        for bad in ("", "   ", "plane.example.com", "ftp://x/"):
            with self.assertRaises(pc.PlaneConfigError):
                self._build(bad)

    def test_the_key_is_absent_from_every_config_error(self):
        """A distinctive key, not "k" — a one-letter secret matches any word
        and would make this assertion pass without proving anything."""
        secret = "plane_api_ZZQQXX0000deadbeef"
        for bad in ("", "http://evil.example.com", "ftp://x/"):
            with self.assertRaises(pc.PlaneConfigError) as ctx:
                pc.PlaneClient(bad, "acme", transport=FakeTransport([]),
                               sleep=lambda _s: None,
                               env={"REIN_PLANE_API_KEY": secret})
            self.assertNotIn(secret, str(ctx.exception))
            self.assertNotIn(secret, repr(ctx.exception))


class ListProjectsReadsEveryPageTests(unittest.TestCase):
    """D3 matches projects client-side over this listing.

    Plane paginates at 100. Reading only the first page made `_match_project`
    miss an existing project — and since the plan measured that projects have
    NO `external_id` uniqueness, the follow-up POST does not 409 on identity:
    it collides on the name (that repo fails forever) or silently creates a
    DUPLICATE project.
    """

    def _page(self, ids, more, cursor=None):
        body = {
            "results": [
                {"id": i, "external_id": f"rein:ws:{i}:c:_", "external_source": "rein"} for i in ids
            ],
            "next_page_results": more,
        }
        if cursor:
            body["next_cursor"] = cursor
        return (200, body)

    def test_it_follows_the_cursor_to_the_last_page(self):
        transport = FakeTransport([
            self._page(["p1", "p2"], True, "c1"),
            self._page(["p3"], False),
        ])
        client = _client(transport)
        self.assertEqual([p["id"] for p in client.list_projects()], ["p1", "p2", "p3"])
        self.assertEqual(len([c for c in transport.calls if c[0] == "GET"]), 2)

    def test_a_project_on_the_second_page_is_matched_not_recreated(self):
        transport = FakeTransport([
            self._page(["p1"], True, "c1"),
            self._page(["target"], False),
            (200, {"id": "target", "updated": True}),
        ])
        client = _client(transport)
        result = client.upsert_project("repo", "rein:ws:target:c:_")
        self.assertTrue(result["updated"])
        self.assertEqual([m for m, _, _ in transport.calls], ["GET", "GET", "PATCH"])

    def test_a_repeated_cursor_stops_instead_of_spinning(self):
        transport = FakeTransport([self._page(["p1"], True, "same")] * 5)
        client = _client(transport)
        client.list_projects()
        self.assertLessEqual(len(transport.calls), 3)


class ReSyncOfAnExistingEntityKeepsTheIdTests(unittest.TestCase):
    """The 409 hands us the id; the PATCH response does not give it back.

    Measured on a live instance: Plane's module PATCH serializer omits `id`
    entirely — the body opens with `name`, `description`, `start_date`. So
    returning the PATCH body verbatim produced a dict with no `id`, and
    `plane_sync`'s `module["id"]` raised `KeyError: 'id'` for every entity on
    the second sync of an already-existing change.

    Nothing caught it: a fake transport returns whatever body the test author
    imagined (and every fixture here imagined an `id`), and a FIRST sync only
    ever takes the 201 path. It took a real re-sync against a real instance.
    """

    def test_id_survives_a_patch_body_that_omits_it(self):
        transport = FakeTransport([
            (409, {"error": "Module with the same external id ... exists", "id": "mod-7"}),
            # The measured shape: no `id` anywhere.
            (200, {"name": "product-observability", "description": "x",
                   "start_date": None, "status": "planned"}),
        ])
        client = _client(transport)
        result = client.upsert_module("proj-1", "product-observability", "rein:ws:r:c:_")
        self.assertEqual(result["id"], "mod-7")
        self.assertEqual(result["name"], "product-observability")

    def test_a_patch_body_that_does_carry_an_id_is_left_alone(self):
        transport = FakeTransport([
            (409, {"id": "wi-3"}),
            (200, {"id": "wi-3", "name": "renamed"}),
        ])
        client = _client(transport)
        result = client.upsert_work_item("proj-1", "renamed", "rein:ws:r:c:T001")
        self.assertEqual(result["id"], "wi-3")
