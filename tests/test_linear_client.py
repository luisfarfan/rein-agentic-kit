#!/usr/bin/env python3
"""Tests for the Linear client. No network: the transport is injected.

Three of these assert things that were MEASURED against the live API and
that no amount of reading the docs would have settled:

  * the personal key goes in `Authorization` with **no** `Bearer` prefix.
    With the prefix the API answers 400, which reads like a malformed
    query and sends you debugging the wrong thing entirely.
  * GraphQL reports failure inside a **200**. Treating any 200 as success
    turns a rejected query into an empty result set -- an intake that
    quietly finds no issues instead of one that says why.
  * a 4xx that is not 429 must NOT be retried. A revoked key returns the
    same answer four times; retrying only makes the operator wait four
    times as long for it.

The write path is exercised the same way. It is small on purpose: this
client reads, moves a state, and comments. `test_no_destructive_operation`
asserts the absence of anything else against the source, because that is a
property of the module rather than of any one call.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")

sys.path.insert(0, LIB_DIR)
import linear_client as lc  # noqa: E402

ENV = {lc.API_KEY_ENV: "lin_api_test"}


class FakeTransport:
    """Replays a queued list of `(status, headers, body)`."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers, body):
        self.calls.append({
            "url": url, "headers": headers, "body": json.loads(body.decode("utf-8")),
        })
        if not self.responses:
            raise AssertionError("the client made more requests than were queued")
        return self.responses.pop(0)


def _ok(data):
    return (200, {}, json.dumps({"data": data}))


def _client(*responses, sleeps=None):
    transport = FakeTransport(*responses)
    recorded = sleeps if sleeps is not None else []
    client = lc.LinearClient(transport=transport, env=ENV, sleep=recorded.append)
    return client, transport


class TheKeyIsReadOnlyFromTheEnvironmentTests(unittest.TestCase):
    def test_a_missing_key_names_the_variable(self):
        with self.assertRaises(lc.LinearAuthError) as caught:
            lc.LinearClient(transport=FakeTransport(), env={})
        self.assertIn(lc.API_KEY_ENV, str(caught.exception))

    def test_a_blank_key_is_the_same_as_no_key(self):
        with self.assertRaises(lc.LinearAuthError):
            lc.LinearClient(transport=FakeTransport(), env={lc.API_KEY_ENV: "   "})

    def test_the_header_carries_no_bearer_prefix(self):
        # Measured: with `Bearer ` the API answers 400, not 401.
        client, transport = _client(_ok({"issue": {"id": "x", "identifier": "PPR-1"}}))
        client.issue("PPR-1")
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "lin_api_test")

    def test_the_key_is_never_placed_in_the_url_or_the_body(self):
        client, transport = _client(_ok({"issue": {"id": "x", "identifier": "PPR-1"}}))
        client.issue("PPR-1")
        call = transport.calls[0]
        self.assertNotIn("lin_api_test", call["url"])
        self.assertNotIn("lin_api_test", json.dumps(call["body"]))


class AGraphqlErrorInsideATwoHundredIsAFailureTests(unittest.TestCase):
    def test_errors_in_the_body_raise_rather_than_return_empty(self):
        body = json.dumps({"data": None, "errors": [{"message": "bad field"}]})
        client, _ = _client((200, {}, body))
        with self.assertRaises(lc.LinearQueryError) as caught:
            client.execute("query { x }")
        self.assertIn("bad field", str(caught.exception))

    def test_an_unparseable_body_is_named(self):
        client, _ = _client((200, {}, "not json at all"))
        with self.assertRaises(lc.LinearRequestError):
            client.execute("query { x }")


class RetryPolicyTests(unittest.TestCase):
    def test_a_429_is_retried_and_then_succeeds(self):
        sleeps = []
        client, transport = _client(
            (429, {}, "slow down"), _ok({"issue": {"id": "x", "identifier": "PPR-1"}}),
            sleeps=sleeps,
        )
        client.issue("PPR-1")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(sleeps), 1)

    def test_retry_after_is_honoured_over_the_backoff_guess(self):
        # The server knows when its window reopens; this code does not.
        sleeps = []
        client, _ = _client(
            (429, {"Retry-After": "7"}, ""), _ok({"issue": {"id": "x"}}), sleeps=sleeps,
        )
        client.issue("PPR-1")
        self.assertEqual(sleeps, [7.0])

    def test_a_five_hundred_is_retried(self):
        sleeps = []
        client, transport = _client(
            (500, {}, "boom"), _ok({"issue": {"id": "x"}}), sleeps=sleeps)
        client.issue("PPR-1")
        self.assertEqual(len(transport.calls), 2)

    def test_a_four_hundred_is_not_retried(self):
        # Retrying a malformed query or a revoked key just multiplies the
        # wait before the same message arrives.
        client, transport = _client((400, {}, "malformed"))
        with self.assertRaises(lc.LinearRequestError):
            client.execute("query { x }")
        self.assertEqual(len(transport.calls), 1)

    def test_a_401_is_not_retried(self):
        client, transport = _client((401, {}, "unauthorized"))
        with self.assertRaises(lc.LinearRequestError):
            client.execute("query { x }")
        self.assertEqual(len(transport.calls), 1)

    def test_retries_are_bounded(self):
        client, transport = _client(*[(429, {}, "")] * lc.MAX_ATTEMPTS)
        with self.assertRaises(lc.LinearRequestError):
            client.execute("query { x }")
        self.assertEqual(len(transport.calls), lc.MAX_ATTEMPTS)

    def test_a_nonsense_retry_after_falls_back_to_backoff(self):
        sleeps = []
        client, _ = _client(
            (429, {"Retry-After": "soon"}, ""), _ok({"issue": {"id": "x"}}), sleeps=sleeps)
        client.issue("PPR-1")
        self.assertEqual(sleeps, [lc.BACKOFF_BASE_SECONDS])


class ReadsTests(unittest.TestCase):
    def test_an_issue_is_looked_up_by_its_human_identifier(self):
        client, transport = _client(_ok({"issue": {"id": "uuid", "identifier": "PPR-86"}}))
        self.assertEqual(client.issue("PPR-86")["identifier"], "PPR-86")
        self.assertEqual(transport.calls[0]["body"]["variables"], {"id": "PPR-86"})

    def test_a_missing_issue_is_named(self):
        client, _ = _client(_ok({"issue": None}))
        with self.assertRaises(lc.IssueNotFound) as caught:
            client.issue("PPR-99999")
        self.assertIn("PPR-99999", str(caught.exception))

    def test_a_rejected_lookup_reads_as_not_found(self):
        body = json.dumps({"errors": [{"message": "Entity not found"}]})
        client, _ = _client((200, {}, body))
        with self.assertRaises(lc.IssueNotFound):
            client.issue("nope")

    def test_pagination_follows_every_page(self):
        page1 = _ok({"issues": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                "nodes": [{"identifier": "PPR-1"}]}})
        page2 = _ok({"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [{"identifier": "PPR-2"}]}})
        client, transport = _client(page1, page2)
        got = client.issues()
        self.assertEqual([i["identifier"] for i in got], ["PPR-1", "PPR-2"])
        self.assertEqual(transport.calls[1]["body"]["variables"]["after"], "c1")

    def test_pagination_stops_when_the_cursor_goes_missing(self):
        # `hasNextPage: true` with no cursor would otherwise loop forever
        # asking for the same page.
        page = _ok({"issues": {"pageInfo": {"hasNextPage": True, "endCursor": None},
                               "nodes": [{"identifier": "PPR-1"}]}})
        client, transport = _client(page)
        self.assertEqual(len(client.issues()), 1)
        self.assertEqual(len(transport.calls), 1)

    def test_states_are_scoped_to_one_team(self):
        client, _ = _client(_ok({"workflowStates": {"nodes": [
            {"id": "1", "name": "Done", "type": "completed", "team": {"key": "PPR"}},
            {"id": "2", "name": "Done", "type": "completed", "team": {"key": "OTHER"}},
        ]}}))
        self.assertEqual([s["id"] for s in client.states("PPR")], ["1"])


class WritesTests(unittest.TestCase):
    def _state_responses(self, team="PPR", states=(("s1", "In Progress"),)):
        return [
            _ok({"issue": {"id": "uuid", "identifier": "PPR-86"}}),
            _ok({"issue": {"team": {"key": team}}}),
            _ok({"workflowStates": {"nodes": [
                {"id": i, "name": n, "type": "started", "team": {"key": team}}
                for i, n in states]}}),
        ]

    def test_a_state_move_sends_the_resolved_ids(self):
        client, transport = _client(
            *self._state_responses(),
            _ok({"issueUpdate": {"success": True,
                                 "issue": {"identifier": "PPR-86",
                                           "state": {"name": "In Progress"}}}}),
        )
        got = client.set_state("PPR-86", "In Progress")
        self.assertEqual(got["state"]["name"], "In Progress")
        self.assertEqual(transport.calls[-1]["body"]["variables"],
                         {"id": "uuid", "stateId": "s1"})

    def test_the_state_name_is_matched_case_insensitively(self):
        client, _ = _client(
            *self._state_responses(),
            _ok({"issueUpdate": {"success": True, "issue": {}}}),
        )
        client.set_state("PPR-86", "in progress")

    def test_an_unknown_state_lists_the_real_ones(self):
        client, _ = _client(
            *self._state_responses(states=(("s1", "Todo"), ("s2", "Done"))),
            _ok({"workflowStates": {"nodes": [
                {"id": "s1", "name": "Todo", "type": "unstarted", "team": {"key": "PPR"}},
                {"id": "s2", "name": "Done", "type": "completed", "team": {"key": "PPR"}},
            ]}}),
        )
        with self.assertRaises(lc.LinearError) as caught:
            client.set_state("PPR-86", "Shipped")
        self.assertIn("Todo", str(caught.exception))
        self.assertIn("Done", str(caught.exception))

    def test_a_refused_update_is_not_reported_as_success(self):
        client, _ = _client(
            *self._state_responses(),
            _ok({"issueUpdate": {"success": False, "issue": None}}),
        )
        with self.assertRaises(lc.LinearError):
            client.set_state("PPR-86", "In Progress")

    def test_a_comment_is_attached_to_the_resolved_issue_id(self):
        client, transport = _client(
            _ok({"issue": {"id": "uuid", "identifier": "PPR-86"}}),
            _ok({"commentCreate": {"success": True, "comment": {"id": "c1", "url": "u"}}}),
        )
        got = client.comment("PPR-86", "on it")
        self.assertEqual(got["id"], "c1")
        self.assertEqual(transport.calls[-1]["body"]["variables"],
                         {"issueId": "uuid", "body": "on it"})

    def test_a_refused_comment_raises(self):
        client, _ = _client(
            _ok({"issue": {"id": "uuid"}}),
            _ok({"commentCreate": {"success": False, "comment": None}}),
        )
        with self.assertRaises(lc.LinearError):
            client.comment("PPR-86", "x")


class TheSurfaceIsReadMoveAndCommentTests(unittest.TestCase):
    def test_no_destructive_operation_exists_in_the_module(self):
        # A property of the module, not of any one call: rein moves work
        # forward on the board and never removes anything from it.
        with open(os.path.join(LIB_DIR, "linear_client.py"), encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("issueDelete", "issueArchive", "Delete(", "Archive("):
            self.assertNotIn(forbidden, source, f"{forbidden} must not exist here")


class UnreachableIsDistinctFromRejectedTests(unittest.TestCase):
    """One is the operator's connection, the other is this code. The same
    message for both sends people to debug the wrong thing."""

    def test_a_transport_failure_surfaces_as_unreachable(self):
        class Dead:
            def post(self, *a, **k):
                raise lc.LinearUnreachable(OSError("no route to host"))

        client = lc.LinearClient(transport=Dead(), env=ENV, sleep=lambda s: None)
        with self.assertRaises(lc.LinearUnreachable) as caught:
            client.issue("PPR-1")
        self.assertIn("no route to host", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
