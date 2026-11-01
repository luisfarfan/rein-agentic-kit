import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/rein/lib'))
import impact
import routing


def report(**risks):
    return {'dimensions': {k: {'level': risks.get(k, 'low'), 'evidence': ['code.py:12; tests/test_code.py:4']} for k in impact.DIMENSIONS}}


class RoutingTest(unittest.TestCase):
    def test_simple_supported_feature_stays_native(self):
        r = routing.recommend(report(), 'codex', 'current')
        self.assertEqual((r['status'], r['execution'], r['plan'], r['audit']), ('ready', 'native', False, False))
        self.assertEqual(r['eligible_providers'], [])

    def test_hidden_money_permission_migration_contract_require_review(self):
        for risk in ('money', 'security', 'persistence', 'contracts', 'concurrency'):
            r = routing.recommend(report(**{risk: 'high'}), 'claude')
            self.assertTrue(r['audit'], risk)
            self.assertEqual(r['execution'], 'native')

    def test_unknown_never_recommends_ready(self):
        self.assertEqual(routing.recommend({}, 'codex')['status'], 'investigate')

    def test_reconsideration_is_pure_and_changes_requirements(self):
        original = report()
        saved = copy.deepcopy(original)
        before = routing.recommend(original, 'codex')
        self.assertEqual(original, saved)
        original['dimensions']['contracts']['level'] = 'high'
        after = routing.recommend(original, 'codex')
        self.assertFalse(before['plan'])
        self.assertTrue(after['plan'])
        self.assertTrue(after['audit'])

    def test_delegation_needs_evidence_and_capacity(self):
        r = report()
        r['delegation'] = {'reason': 'multi_repo'}
        self.assertEqual(routing.recommend(r, 'codex')['execution'], 'native')
        r['delegation']['evidence'] = ['api.py:2; web.ts:4']
        self.assertEqual(routing.recommend(r, 'codex')['status'], 'capacity_required')
        ready = routing.recommend(r, 'codex', limits={'codex': {'weekly': 10}})
        self.assertEqual((ready['status'], ready['execution'], ready['plan']), ('ready', 'ship', True))

    def test_override_retains_risk_review_and_brakes(self):
        r = routing.recommend(report(security='high'), 'codex',
            limits={'codex': {'weekly': 99}}, override={'execution': 'quick', 'audit': False})
        self.assertEqual(r['status'], 'capacity_required')
        self.assertTrue(r['audit'])
        with self.assertRaises(ValueError):
            routing.recommend(report(), 'codex', override={'execution': 'magic'})

    def test_cli_outputs_advice_without_orca(self):
        cli = Path(__file__).resolve().parents[1] / 'plugins/rein/bin/rein'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'assessment.json'
            path.write_text(json.dumps(report()))
            p = subprocess.run([sys.executable, str(cli), 'route', str(path), '--agent', 'codex'],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)['execution'], 'native')
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_evidenced_uncertainty_still_requires_investigation(self):
        r = routing.recommend(report(uncertainty='high'), 'codex')
        self.assertEqual(r['status'], 'investigate')
        self.assertTrue(r['plan'])

    def test_limited_current_provider_is_not_native_ready(self):
        r = routing.recommend(report(), 'claude', limits={'claude': {'weekly': 99}, 'codex': {'weekly': 5}})
        self.assertEqual(r['execution'], 'native')
        self.assertEqual(r['status'], 'capacity_required')
