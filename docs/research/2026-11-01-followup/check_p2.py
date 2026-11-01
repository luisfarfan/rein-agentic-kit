"""Frozen evaluator; never placed in agent workspaces. Run with one fixture path."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root / 'api'), str(root / 'admin')]
from approvals import approve_batch
from view import show_approvals, approval_request

checks = {}
def check(name, operation):
    try:
        checks[name] = bool(operation())
    except Exception as exc:
        checks[name] = {'error': type(exc).__name__, 'detail': str(exc)}
def denied(user, proposals, key):
    try:
        approve_batch(user, proposals, key)
    except PermissionError:
        return True
    return False

i = {'id': 'i', 'business_id': 'a', 'area': 'inventory', 'grant_key': 'g'}
p = dict(i, id='p', area='pricing')
for permission in ('inventory:manage', 'pricing:manage', 'catalog:manage', 'orders:manage'):
    check('visible_' + permission, lambda permission=permission: show_approvals({'permissions': [permission]}))
for label, permissions in [('empty', []), ('read_only', ['inventory:read', 'pricing:read'])]:
    check('hidden_' + label, lambda permissions=permissions: not show_approvals({'permissions': permissions}))
for area, proposal in [('inventory', i), ('pricing', p)]:
    user = {'business_id': 'a', 'permissions': [area + ':manage']}
    check('approve_' + area, lambda user=user, proposal=proposal: approve_batch(user, [proposal], 'g') == [{'id': proposal['id'], 'status': 'approved'}])
    check('wrong_business_' + area, lambda user=user, proposal=proposal: denied(dict(user, business_id='b'), [proposal], 'g'))
    check('wrong_grant_' + area, lambda user=user, proposal=proposal: denied(user, [proposal], 'bad'))
    check('wrong_area_' + area, lambda user=user, proposal=proposal: denied(dict(user, permissions=['pricing:manage' if area == 'inventory' else 'inventory:manage']), [proposal], 'g'))
check('mixed_batch_denied', lambda: denied({'business_id': 'a', 'permissions': ['inventory:manage']}, [i, p], 'g'))
check('visibility_not_mutation', lambda: denied({'business_id': 'a', 'permissions': ['catalog:manage', 'orders:manage']}, [i, p], 'g'))
expected = json.loads((Path(__file__).parent / 'preflight.json').read_text())['p2_initial_code_hashes']
for rel in ('api/policy.py', 'api/approvals.py', 'admin/view.py'):
    check('unchanged_' + rel, lambda rel=rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() == expected[rel])
# Unchanged view bytes pin the request payload/header contract as well as visibility consumer.
print(json.dumps({'fixture': str(root), 'checks': checks, 'passed': sum(v is True for v in checks.values()), 'total': len(checks)}, indent=2))
raise SystemExit(0 if all(v is True for v in checks.values()) else 1)
