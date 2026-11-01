"""Independent executable facts for blind probes; never included in model copies."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

FIXTURES = Path(__file__).parent / 'fixtures/impact_eval'

def load(case, repo, file):
    path = FIXTURES / case / repo / (file + '.py')
    spec = importlib.util.spec_from_file_location('oracle_' + file, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class FiscalFacts(unittest.TestCase):
    def test_sequential_and_concurrent_requests_issue_two_documents(self):
        store_module = load('case1', 'api', 'store')
        with patch.dict(sys.modules, store=store_module):
            service = load('case1', 'api', 'service').InvoiceService
        class Issuer:
            calls = 0
            async def issue(self, *args):
                self.calls += 1
                await asyncio.sleep(0)
                return {'id': str(self.calls)}
        async def exercise(concurrent):
            store, issuer = store_module.Store(), Issuer()
            svc = service(store, issuer)
            payload = {'amount': '100.00', 'currency': 'PEN'}
            if concurrent:
                await asyncio.gather(svc.emit('shop', payload), svc.emit('shop', payload))
            else:
                await svc.emit('shop', payload)
                await svc.emit('shop', payload)
            self.assertEqual(len(store.list('shop')), 2)
            self.assertEqual(issuer.calls, 2)
        asyncio.run(exercise(False))
        asyncio.run(exercise(True))

    def test_external_effect_can_precede_failed_local_save(self):
        store_module = load('case1', 'api', 'store')
        with patch.dict(sys.modules, store=store_module):
            service = load('case1', 'api', 'service').InvoiceService
        calls = []
        class Issuer:
            async def issue(self, *args):
                calls.append(args)
                return {'id': 'issued'}
        store = store_module.Store()
        store.save = lambda row: (_ for _ in ()).throw(RuntimeError('db unavailable'))
        with self.assertRaises(RuntimeError):
            asyncio.run(service(store, Issuer()).emit('shop', {'amount': '1', 'currency': 'PEN'}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(store.rows, [])

class ApprovalFacts(unittest.TestCase):
    def test_batch_already_exists_but_discovery_hides_it(self):
        policy = load('case2', 'api', 'policy')
        menu = load('case2', 'admin', 'menu')
        with patch.dict(sys.modules, policy=policy, menu=menu):
            approve = load('case2', 'api', 'approvals').approve_batch
            view = load('case2', 'admin', 'view')
        for area in ('inventory', 'pricing'):
            user = {'business_id': 'a', 'permissions': [policy.AREA_PERMISSION[area]]}
            proposal = {'id': 'p', 'business_id': 'a', 'area': area, 'grant_key': 'grant'}
            self.assertFalse(view.show_approvals(user))
            self.assertEqual(approve(user, [proposal], 'grant')[0]['status'], 'approved')
            for changed_user, key in [(dict(user, business_id='b'), 'grant'),
                                      (dict(user, permissions=['catalog:manage']), 'grant'), (user, 'wrong')]:
                with self.assertRaises(PermissionError): approve(changed_user, [proposal], key)
            self.assertEqual(view.approval_request([proposal], 'grant')['body'], {'proposal_ids': ['p']})
