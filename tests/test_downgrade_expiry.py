"""A weaker verify mode than the project is, now expires.

`flow.config.json` may always ask for MORE than detection inferred — that needs
no permission. Asking for less is the opt-out that turned off the render gate
on a real Astro app: `verify.mode: "unit"` sat in proxima-storefront-v2 two
lines below the `subtypes: ["frontend", "astro"]` it contradicted. Nothing
failed, nothing warned, and it stayed.

The capability is kept, because a frontend sometimes genuinely cannot render in
CI yet. What changed is that the exception carries a date: an exception without
an expiry is not an exception, it is the new default.

`_downgrade_verdict` takes `today` so the answer is decided by the test rather
than by the calendar it happens to run on.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import detect  # noqa: E402

TODAY = "2026-09-10"
PKG_FRONTEND = json.dumps({"name": "x", "devDependencies": {"astro": "^4"},
                           "scripts": {"dev": "astro dev"}})


class WhatCountsAsADowngradeTestCase(unittest.TestCase):

    def test_asking_for_less_than_detected_is_a_downgrade(self):
        self.assertTrue(detect._is_downgrade("rendered", "unit"))
        self.assertTrue(detect._is_downgrade("plan-only", "unit"))

    def test_asking_for_more_never_is(self):
        """A project demanding a browser render of a library needs no
        permission to be stricter than it has to be."""
        self.assertFalse(detect._is_downgrade("unit", "rendered"))
        self.assertFalse(detect._is_downgrade("unit", "plan-only"))

    def test_agreeing_with_detection_is_not_a_downgrade(self):
        for mode in ("unit", "rendered", "plan-only"):
            with self.subTest(mode=mode):
                self.assertFalse(detect._is_downgrade(mode, mode))


class ExpiryVerdictTestCase(unittest.TestCase):

    def test_a_future_date_is_honoured(self):
        v = detect._downgrade_verdict("2026-12-31", TODAY)
        self.assertTrue(v["honoured"])
        self.assertEqual(v["until"], "2026-12-31")

    def test_today_itself_still_counts(self):
        """An exception expiring today has not expired yet -- an off-by-one
        here would revoke it a day early, mid-working-day."""
        self.assertTrue(detect._downgrade_verdict(TODAY, TODAY)["honoured"])

    def test_yesterday_does_not(self):
        v = detect._downgrade_verdict("2026-09-09", TODAY)
        self.assertFalse(v["honoured"])
        self.assertIn("expired on 2026-09-09", v["reason"])

    def test_no_date_at_all_is_refused_and_says_why(self):
        for missing in ("", None, "   "):
            with self.subTest(until=missing):
                v = detect._downgrade_verdict(missing, TODAY)
                self.assertFalse(v["honoured"])
                self.assertIn("no expiry", v["reason"])

    def test_an_unreadable_date_is_refused_rather_than_assumed(self):
        """"soon" must not read as "forever"."""
        for bad in ("soon", "31-12-2026", "2026-13-01", "next sprint"):
            with self.subTest(until=bad):
                v = detect._downgrade_verdict(bad, TODAY)
                self.assertFalse(v["honoured"])
                self.assertIn("unreadable expiry", v["reason"])


class EndToEndTestCase(unittest.TestCase):
    """Through `resolve`, which is what every caller actually reads."""

    def _resolve(self, cfg):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "package.json"), "w", encoding="utf-8") as fh:
                fh.write(PKG_FRONTEND)
            with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
            return detect.resolve(root)

    def test_an_undated_opt_out_does_not_take_effect(self):
        r = self._resolve({"verify": {"mode": "unit"}})
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")

    def test_the_warning_names_the_fix_not_just_the_problem(self):
        r = self._resolve({"verify": {"mode": "unit"}})
        warning = next(w for w in r["verifyWarnings"] if "weaker" in w)
        self.assertIn('"until"', warning)
        self.assertIn("YYYY-MM-DD", warning)

    def test_a_dated_opt_out_takes_effect_and_says_when_it_ends(self):
        r = self._resolve({"verify": {"mode": "unit", "until": "2099-01-01"}})
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertTrue(any("until 2099-01-01" in w for w in r["verifyWarnings"]))

    def test_an_expired_opt_out_brings_the_gate_back_on_its_own(self):
        """Nobody has to remember to remove it."""
        r = self._resolve({"verify": {"mode": "unit", "until": "2020-01-01"}})
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertTrue(r["verifyPolicy"]["requires"])

    def test_asking_for_more_still_needs_no_date(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8") as fh:
                fh.write("")
            with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
                json.dump({"verify": {"mode": "rendered"}}, fh)
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")

    def test_the_policy_dict_gains_no_new_key(self):
        """loop.js copies verifyPolicy literally into a schema declared with
        additionalProperties: false and exactly these four keys."""
        r = self._resolve({"verify": {"mode": "unit", "until": "2099-01-01"}})
        self.assertEqual(set(r["verifyPolicy"]), {"mode", "requires", "forbids", "tools"})


if __name__ == "__main__":
    unittest.main()
