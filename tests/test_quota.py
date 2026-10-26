"""`quota`: choose the implementer and brake from live Orca rate limits.

Unknown usage never brakes and never routes work onto Claude: a missing
number is treated as "do not spend Claude on it", not as "zero".
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import quota  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "orca")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class ParseRateLimitsTest(unittest.TestCase):
    def test_parses_real_fixture(self):
        lim = quota.parse_rate_limits(load("account_list.json"))
        self.assertEqual(lim["claude"]["weekly"], 77)
        self.assertEqual(lim["codex"]["weekly"], 15)
        self.assertEqual(lim["claude"]["session"], 11)

    def test_provider_in_error_status_has_none_values(self):
        lim = quota.parse_rate_limits(load("account_list.json"))
        self.assertEqual(lim["zai"], {"session": None, "weekly": None})

    def test_missing_provider_block_is_none(self):
        lim = quota.parse_rate_limits({"result": {"rateLimits": {}}})
        self.assertEqual(lim["claude"], {"session": None, "weekly": None})
        self.assertEqual(lim["codex"], {"session": None, "weekly": None})

    def test_garbage_input_is_all_none(self):
        for bad in ({}, {"result": None}, {"result": {"rateLimits": None}}, None):
            lim = quota.parse_rate_limits(bad)
            self.assertEqual(lim["claude"], {"session": None, "weekly": None})

    def test_non_ok_status_discards_numbers(self):
        lim = quota.parse_rate_limits({"result": {"rateLimits": {"claude": {
            "status": "error", "session": {"usedPercent": 99}, "weekly": {"usedPercent": 99}}}}})
        self.assertEqual(lim["claude"], {"session": None, "weekly": None})


class ChooseImplementerTest(unittest.TestCase):
    def test_claude_over_threshold_routes_to_codex(self):
        self.assertEqual(quota.choose_implementer({"claude": {"weekly": 60.0}}, 60), quota.CODEX)

    def test_claude_under_threshold_routes_to_sonnet(self):
        self.assertEqual(quota.choose_implementer({"claude": {"weekly": 59.9}}, 60), quota.SONNET)

    def test_unknown_claude_usage_routes_to_codex(self):
        self.assertEqual(quota.choose_implementer({"claude": {"weekly": None}}, 60), quota.CODEX)
        self.assertEqual(quota.choose_implementer({}, 60), quota.CODEX)

    def test_override_wins(self):
        self.assertEqual(
            quota.choose_implementer({"claude": {"weekly": 99}}, 60, quota.OPUS), quota.OPUS)

    def test_constants(self):
        self.assertEqual(quota.CODEX, ("codex", "gpt-6.1-sol"))
        self.assertEqual(quota.SONNET, ("claude", "sonnet"))
        self.assertEqual(quota.OPUS, ("claude", "opus"))
        self.assertEqual(quota.ANTIGRAVITY, ("antigravity", "gemini-4-argon-medium"))

    def test_braked_codex_falls_to_antigravity(self):
        lim = {"claude": {"session": 1, "weekly": 97}, "codex": {"session": 92, "weekly": 18},
               "antigravity": {"session": 0, "weekly": 2}}
        self.assertEqual(quota.choose_implementer(lim, 60), quota.ANTIGRAVITY)

    def test_braked_sonnet_falls_to_codex(self):
        lim = {"claude": {"session": 95, "weekly": 10}, "codex": {"session": 5, "weekly": 5}}
        self.assertEqual(quota.choose_implementer(lim, 60), quota.CODEX)

    def test_antigravity_needs_known_usage(self):
        braked_codex = {"claude": {"weekly": 97}, "codex": {"session": 95, "weekly": 1}}
        for ag in (None, {"session": None, "weekly": None}, {"session": 0, "weekly": None}):
            lim = dict(braked_codex)
            if ag is not None:
                lim["antigravity"] = ag
            self.assertEqual(quota.choose_implementer(lim, 60), quota.CODEX, ag)

    def test_everything_braked_returns_the_first_choice(self):
        hot = {"session": 95, "weekly": 95}
        lim = {"claude": {"session": 95, "weekly": 10}, "codex": hot, "antigravity": hot}
        self.assertEqual(quota.choose_implementer(lim, 60), quota.SONNET)
        lim["claude"] = {"session": 1, "weekly": 97}
        self.assertEqual(quota.choose_implementer(lim, 60), quota.CODEX)

    def test_codex_stays_first_when_both_fallbacks_are_fine(self):
        lim = {"claude": {"weekly": 97}, "codex": {"session": 68, "weekly": 18},
               "antigravity": {"session": 0, "weekly": 2}}
        self.assertEqual(quota.choose_implementer(lim, 60), quota.CODEX)

    def test_override_skips_the_brake_check(self):
        hot = {"session": 99, "weekly": 99}
        self.assertEqual(quota.choose_implementer({"codex": hot}, 60, quota.CODEX), quota.CODEX)


class BrakeReasonTest(unittest.TestCase):
    def test_full_quota_brake_boundary_in_both_windows(self):
        for window in ("session", "weekly"):
            for used, braked in ((95, False), (100, True)):
                with self.subTest(window=window, used=used):
                    limits = {"codex": {window: used}}
                    self.assertEqual(bool(quota.brake_reason(limits, "codex", 100)), braked)
                    self.assertTrue(quota.brake_reason(limits, "codex"))
                    self.assertEqual(quota.first_unbraked(
                        [quota.CODEX, quota.SONNET], limits, 100),
                        quota.SONNET if braked else quota.CODEX)
                    self.assertEqual(quota.choose_implementer(
                        {**limits, "antigravity": {"weekly": 1}}, 60, max_pct=100),
                        quota.ANTIGRAVITY if braked else quota.CODEX)

    def test_brake_on_session_or_weekly_at_90(self):
        self.assertIn("claude", quota.brake_reason({"claude": {"session": 90, "weekly": 10}}, "claude"))
        self.assertIn("codex", quota.brake_reason({"codex": {"session": 1, "weekly": 91}}, "codex"))
        self.assertEqual(quota.brake_reason({"codex": {"session": 1, "weekly": 10}}, "codex"), "")

    def test_reason_names_the_window(self):
        self.assertIn("session", quota.brake_reason({"claude": {"session": 95, "weekly": 1}}, "claude"))
        self.assertIn("weekly", quota.brake_reason({"claude": {"session": 1, "weekly": 95}}, "claude"))

    def test_unknown_usage_never_brakes(self):
        self.assertEqual(quota.brake_reason({"claude": {"session": None, "weekly": None}}, "claude"), "")
        self.assertEqual(quota.brake_reason({}, "codex"), "")

    def test_just_under_does_not_brake_and_max_pct_is_tunable(self):
        self.assertEqual(quota.brake_reason({"claude": {"session": 89.9, "weekly": 0}}, "claude"), "")
        self.assertIn("claude", quota.brake_reason({"claude": {"session": 50, "weekly": 0}}, "claude", 50))

    def test_other_providers_do_not_brake_this_agent(self):
        lim = {"claude": {"session": 99, "weekly": 99}, "codex": {"session": 1, "weekly": 1}}
        self.assertEqual(quota.brake_reason(lim, "codex"), "")


class UsageDeltaTest(unittest.TestCase):
    def test_delta(self):
        d = quota.usage_delta({"claude": {"session": 10, "weekly": 50}},
                              {"claude": {"session": 25, "weekly": 53}})
        self.assertEqual(d["claude"], {"session": 15, "weekly": 3})

    def test_none_on_either_side_is_none(self):
        d = quota.usage_delta({"claude": {"session": None, "weekly": 5}},
                              {"claude": {"session": 4, "weekly": None}})
        self.assertEqual(d["claude"], {"session": None, "weekly": None})

    def test_missing_provider_is_none(self):
        d = quota.usage_delta({"claude": {"session": 1, "weekly": 1}}, {})
        self.assertEqual(d["claude"], {"session": None, "weekly": None})


if __name__ == "__main__":
    unittest.main()
