"""Execution-event quota guard: captured numbers are not timeless capacity."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/rein/lib'))
import quota
import quick
import ship as driver
from tests.test_quick_driver import DriverCase, _Orca, _limits
from tests.test_ship_decide import ship, task, tick, sd

NOW = 1_800_000_000

def account(age=0, value=20, status='ok', plan='Plus', reset=1_800_600_000_000):
    return {'result': {'rateLimits': {'codex': {'status': status, 'updatedAt': (NOW-age)*1000,
        'planType': plan, 'weekly': {'usedPercent': value, 'windowMinutes': 10080, 'resetsAt': reset}}}}}

class FreshnessTest(unittest.TestCase):
    def test_cached_response_can_expire_without_a_transport_error(self):
        raw = account()
        obj = object.__new__(driver._Driver)
        obj.client = SimpleNamespace(account_list=lambda: raw['result'])
        with patch('quota.time.time', return_value=NOW):
            self.assertEqual(quota.availability(obj.limits(), 'codex'), 'available')
        with patch('quota.time.time', return_value=NOW + 901):
            limits = obj.limits()
        self.assertEqual(raw['result']['rateLimits']['codex']['updatedAt'], NOW * 1000)
        self.assertEqual(quota.availability(limits, 'codex'), 'unknown')
        self.assertIn('stale', quota.brake_reason(limits, 'codex'))

    def test_retry_requires_new_evidence_and_pause_finishes_without_dispatch(self):
        cfg = sd.ShipConfig(override=quota.CODEX)
        stale = quota.checked_limits(account(age=901), now=NOW)
        state, _ = tick(ship(task('1')), stale, cfg=cfg)
        # The driver persists OpenGate; resolving it does not manufacture capacity.
        def resolve(state, answer):
            state.pending_gates = []
            state.gates['quota-gate'] = {'task_id': None, 'kind': 'brake', 'message_id': ''}
            return sd.decide(state, sd.GateResolved('quota-gate', answer), cfg)
        retry, _ = resolve(copy.deepcopy(state), 'retry')
        blocked, actions = tick(retry, stale, cfg=cfg)
        self.assertEqual(blocked.phase, 'braked')
        self.assertFalse(any(isinstance(a, sd.Dispatch) for a in actions))
        retry, _ = resolve(blocked, 'retry')
        resumed, actions = tick(retry, quota.checked_limits(account(), now=NOW), cfg=cfg)
        self.assertEqual(sum(isinstance(a, sd.Dispatch) for a in actions), 1)
        self.assertEqual(resumed.tasks['1'].status, 'running')
        paused, actions = resolve(state, 'pause')
        self.assertTrue(any(isinstance(a, sd.Finish) for a in actions))
        self.assertEqual(paused.tasks['1'].status, 'pending')
        self.assertFalse(any(isinstance(a, sd.Dispatch) for a in actions))

    def test_recent_weekly_without_session_can_dispatch_but_unknown_cannot(self):
        for value, expected in [(20, 1), (None, 0)]:
            limits = quota.checked_limits(account(value=value), now=NOW)
            _, actions = tick(ship(task('1')), limits, cfg=sd.ShipConfig(override=quota.CODEX))
            self.assertIsNone(limits['codex']['session'])
            self.assertEqual(sum(isinstance(a, sd.Dispatch) for a in actions), expected)

    def test_old_missing_future_and_error_observations_cannot_authorize(self):
        cases = [account(age=901), account(age=-61), account(status='error'), account(value=None), {}]
        missing = account(); del missing['result']['rateLimits']['codex']['updatedAt']; cases.append(missing)
        invalid = account(); invalid['result']['rateLimits']['codex']['updatedAt'] = 10**1000; cases.append(invalid)
        for raw in cases:
            with self.subTest(raw=raw):
                limits = quota.checked_limits(raw, now=NOW)
                self.assertEqual(quota.availability(limits, 'codex'), 'unknown')
                self.assertTrue(quota.brake_reason(limits, 'codex'))
                self.assertIsNone(quick.reviewer(limits))
                state, actions = tick(ship(task('1')), limits, cfg=sd.ShipConfig(override=quota.CODEX))
                self.assertEqual(state.phase, 'braked')
                self.assertFalse(any(isinstance(a, sd.Dispatch) for a in actions))

    def test_boundary_preserves_brake_and_unknown_other_window(self):
        limits = quota.checked_limits(account(age=900), now=NOW)
        self.assertEqual(quota.availability(limits, 'codex'), 'available')
        self.assertIsNone(limits['codex']['session'])
        for value in [90, 100]:
            limits = quota.checked_limits(account(value=value), now=NOW)
            self.assertEqual(quota.availability(limits, 'codex'), 'limited')
            self.assertTrue(quota.brake_reason(limits, 'codex'))
        self.assertEqual(quota.availability(quota.checked_limits(account(status='limited'),now=NOW),'codex'),'limited')

    def test_both_limited_and_absent_override_provider_do_not_launch(self):
        raw = account(value=99)
        raw['result']['rateLimits']['claude'] = copy.deepcopy(raw['result']['rateLimits']['codex'])
        limits = quota.checked_limits(raw,now=NOW)
        self.assertTrue(quota.brake_reason(limits, 'gemini'))
        for override in [None, quota.CODEX, quota.OPUS]:
            state, actions = tick(ship(task('1')), limits, cfg=sd.ShipConfig(override=override))
            self.assertEqual(state.phase,'braked')
            self.assertFalse(any(isinstance(a,sd.Dispatch) for a in actions))

    def test_changed_plan_reset_or_window_does_not_become_usage(self):
        before = quota.checked_limits(account(value=20), now=NOW)
        after = quota.checked_limits(account(value=30), now=NOW)
        self.assertEqual(quota.usage_delta(before,after)['codex']['weekly'],10)
        for raw in [account(value=30,plan='Pro'), account(value=30,reset=1_801_000_000_000),account(value=30,age=901)]:
            self.assertIsNone(quota.usage_delta(before,quota.checked_limits(raw,now=NOW))['codex']['weekly'])
        raw=account(value=30); raw['result']['rateLimits']['codex']['weekly']['windowMinutes']=1440
        self.assertIsNone(quota.usage_delta(before,quota.checked_limits(raw,now=NOW))['codex']['weekly'])
        self.assertIsNone(quota.usage_delta({'codex':{'weekly':20}},after)['codex']['weekly'])

    def test_driver_queries_each_event_without_cached_plan_or_background_work(self):
        calls=[]
        raw=account(age=901)
        def read(): calls.append(1); return raw['result']
        obj=object.__new__(driver._Driver)
        obj.client=SimpleNamespace(account_list=read)
        with patch('quota.time.time',return_value=NOW):
            self.assertTrue(quota.brake_reason(obj.limits(),'codex'))
            raw=account(plan='Pro')
            self.assertEqual(obj.limits()['codex']['_quota_epoch']['plan'],'Pro')
        self.assertEqual(len(calls),2)
        obj.state = SimpleNamespace(quota_before={})
        obj.save = lambda: None
        obj.note_quota_before(quota.checked_limits(account(age=901),now=NOW))
        self.assertEqual(obj.state.quota_before,{})
        obj.note_quota_before(quota.checked_limits(account(),now=NOW))
        self.assertEqual(obj.state.quota_before['codex']['weekly'],20)

    def test_cli_refuses_delegation_from_stale_saved_limits(self):
        from tests.test_routing import report
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/'report.json'; evidence.write_text(json.dumps(report()))
            raw=account()
            raw['result']['rateLimits']['codex']['updatedAt']=(time.time()-86400)*1000
            limits=Path(tmp)/'limits.json'; limits.write_text(json.dumps(raw))
            p=subprocess.run([sys.executable,str(root/'plugins/rein/bin/rein'),'route',str(evidence),
                '--agent','codex','--execution','quick','--limits',str(limits)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads(p.stdout)['status'],'capacity_required')

class QuickFreshnessTest(DriverCase):
    def test_explicit_quick_refuses_stale_or_unknown_before_workspace_or_worker(self):
        for mode in ['stale','timestamp_missing','unknown']:
            limits=_limits(99,10)
            block=limits['rateLimits']['codex']
            if mode=='stale': block['updatedAt']=(time.time()-86400)*1000
            elif mode=='timestamp_missing': block.pop('updatedAt')
            else: block.update(session=None,weekly=None)
            fake=_Orca(self.repos,limits,self.worktree)
            code,_,_=self.run_quick(orca=fake,agent='codex',model='gpt-6.1-sol')
            self.assertEqual(code,1,self.text())
            self.assertEqual(fake.launches(),[])
