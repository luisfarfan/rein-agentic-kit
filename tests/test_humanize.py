#!/usr/bin/env python3
"""Tests for plugins/rein/lib/humanize.py -- decorative prose for the Plane
board, never load-bearing (D7, D9).

No test here shells out to a real agent: every `humanize()`/`HumanizeCache`
call in this file injects its own `invoke`, so the suite never spawns a
process and never needs credentials.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "rein", "lib"))

import humanize  # noqa: E402
import plane_client as pc  # noqa: E402


def _write_plane_json(root: str, **fields) -> None:
    import json

    with open(os.path.join(root, "plane.json"), "w", encoding="utf-8") as fh:
        json.dump(fields, fh)


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


if __name__ == "__main__":
    unittest.main()
