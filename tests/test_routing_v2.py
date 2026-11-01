"""Checked Discover -> existing advisory policy, never a dispatch integration test."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests.test_discovery_v2 import ROOT, FIXTURES, case
import routing


class CheckedRoutingTest(unittest.TestCase):
    def route(self, name='easy', report=None, **kwargs):
        return routing.recommend(report or case(name), 'codex', roots={'.': FIXTURES / name}, **kwargs)

    def test_easy_native_and_hidden_risk_are_independent_of_complexity(self):
        easy = self.route()
        self.assertEqual(easy['strategies'], ['native'])
        hidden = self.route('hidden')
        self.assertEqual(hidden['strategies'], ['native', 'audit'])
        self.assertEqual(hidden['assessment']['classification']['implementation']['level'], 'low')
        self.assertIn('risk evidence:', ' '.join(hidden['reasons']))

    def test_multiple_repos_do_not_automatically_dispatch(self):
        r = self.route('complex')
        self.assertEqual(r['strategies'], ['plan', 'native', 'audit'])
        self.assertEqual(r['status'], 'ready')
        self.assertEqual(len(r['decisions']), 6)

    def test_ambiguous_and_stale_remain_blocked_despite_override_and_quota(self):
        for name in ('easy', 'ambiguous'):
            report = case(name)
            if name == 'easy':
                next(iter(report['evidence'].values()))['sha256'] = '0' * 64
            r = self.route(name, report, limits={'codex': {'weekly': 99}}, override={'execution': 'quick'})
            self.assertEqual(r['status'], 'investigate')
            self.assertIn('impact investigation incomplete', r['blockers'])
            self.assertIn('provider capacity required', r['blockers'])

    def test_evidenced_delegation_and_overrides_preserve_risk_review(self):
        r = case('complex')
        r['delegation'] = {'reason': 'multi_repo', 'evidence': ['api.py', 'client.ts']}
        available = {'codex': {'weekly': 20}}
        self.assertEqual(self.route('complex', r, limits=available)['execution'], 'ship')
        overridden = self.route('complex', r, override={'execution': 'native', 'plan': False, 'audit': False})
        self.assertEqual(overridden['strategies'], ['native', 'audit'])
        r['delegation']['evidence'] = ['missing']
        self.assertEqual(self.route('complex', r, limits=available)['execution'], 'native')
        r['delegation'] = {'reason': 'capability', 'evidence': ['api.py']}
        self.assertEqual(self.route('complex', r, limits=available)['execution'], 'quick')

    def test_reconsideration_uses_new_evidence_without_mutating_the_report(self):
        r = case('easy')
        saved = copy.deepcopy(r)
        before = self.route(report=r)
        self.assertEqual(r, saved)
        r['classification']['risk']['level'] = 'high'
        after = self.route(report=r)
        self.assertFalse(before['audit'])
        self.assertTrue(after['audit'])
        self.assertIn('cited file changed', after['reconsider_on'])

    def test_loop_goal_are_explicit_bounded_advice_only(self):
        for mode in ('loop', 'goal'):
            r = case('easy')
            r['iteration'] = {'requested': True, 'mode': mode}
            self.assertEqual(self.route(report=r)['status'], 'iteration_constraints_required')
            r['iteration'].update(max_attempts=2, max_seconds=120, success_check='python -m unittest test_currency')
            with patch('subprocess.run', side_effect=AssertionError('router must not execute')):
                decision = self.route(report=r)
            self.assertEqual(decision['iterations']['mode'], mode)
            self.assertFalse(decision['iterations']['new_loop'])
            self.assertIn(mode, decision['strategies'])
            r['iteration']['failure_kind'] = 'environment'
            self.assertEqual(self.route(report=r)['status'], 'iteration_constraints_required')
            r['iteration']['failure_kind'] = 'code'
            r['iteration']['repeated_without_progress'] = True
            self.assertEqual(self.route(report=r)['iterations']['mode'], 'native')
        self.assertEqual(self.route()['iterations']['mode'], 'native')

    def test_cli_checks_freshness_and_honors_false_plan_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            path.write_text(json.dumps(case('complex')))
            args = [sys.executable, str(ROOT / 'plugins/rein/bin/rein'), 'route', str(path),
                    '--agent', 'codex', '--root', str(FIXTURES / 'complex'), '--no-plan', '--no-audit']
            proc = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result['strategies'], ['native', 'audit'])
            self.assertEqual(result['override'], {'plan': False, 'audit': False})
            self.assertEqual(list(Path(tmp).iterdir()), [path])
