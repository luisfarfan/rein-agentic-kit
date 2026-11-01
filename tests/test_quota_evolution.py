import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/rein/lib'))
import quota
import quick


def envelope(**providers):
    return {'result': {'rateLimits': providers}}


class QuotaEvolutionTest(unittest.TestCase):
    def test_unknown_does_not_outrank_measured_capacity(self):
        limits = {'claude': {'weekly': 97}, 'codex': {'weekly': None},
                  'antigravity': {'weekly': 5}}
        self.assertEqual(quota.choose_implementer(limits, 60), quota.ANTIGRAVITY)
        self.assertEqual(quota.availability(limits, 'codex'), 'unknown')

    def test_unknown_audit_never_launches_opus(self):
        self.assertIsNone(quick.reviewer(quota.parse_rate_limits({})))
        self.assertEqual(quick.reviewer({'codex': {'weekly': 10}}), quota.CODEX)

    def test_provider_limited_without_percent_brakes(self):
        for state in ('limited', 'exhausted', 'rate_limited', 'limit_reached'):
            limits = quota.parse_rate_limits(envelope(codex={'status': state}))
            self.assertEqual(quota.availability(limits, 'codex'), 'limited')
            self.assertTrue(quota.brake_reason(limits, 'codex', 100))

    def test_invalid_percentages_are_unknown(self):
        for value in (-1, 101, float('nan'), float('inf'), True, '5', 10**1000):
            limits = quota.parse_rate_limits(envelope(codex={'status': 'ok', 'weekly': {'usedPercent': value}}))
            self.assertEqual(quota.availability(limits, 'codex'), 'unknown')

    def test_reset_is_not_negative_consumption(self):
        self.assertIsNone(quota.usage_delta({'codex': {'weekly': 95}}, {'codex': {'weekly': 1}})['codex']['weekly'])

    def test_plan_changes_are_not_cached_and_resets_are_preserved(self):
        raw = envelope(codex={'status': 'ok', 'weekly': {'usedPercent': 5, 'resetsAt': 123, 'windowMinutes': 10080}})
        before = quota.snapshot(raw, {'codex': 'Pro'})
        after = quota.snapshot(raw, {'codex': 'Plus'})
        self.assertEqual(before['codex']['plan'], 'Pro')
        self.assertEqual(after['codex']['plan'], 'Plus')
        self.assertEqual(after['codex']['windows']['weekly']['resetsAt'], 123)
        self.assertIsNone(after['claude']['plan'])

    def test_unknown_ship_capacity_cannot_launch_a_parallel_burst(self):
        from ship_state import ShipState, TaskState
        from ship_decide import decide, Tick, ShipConfig, Dispatch
        state = ShipState(change='c', plan_path='p.md', phase='running')
        state.tasks = {str(i): TaskState(id=str(i), repo='repo'+str(i), deps=[], human_review=False, verification='', type='impl') for i in range(3)}
        state, actions = decide(state, Tick(now=1, limits={}), ShipConfig())
        self.assertEqual(sum(isinstance(a, Dispatch) for a in actions), 1)
        _, again = decide(state, Tick(now=2, limits={}), ShipConfig())
        self.assertFalse(any(isinstance(a, Dispatch) for a in again))
