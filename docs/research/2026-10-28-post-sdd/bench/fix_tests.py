"""For each audit-fix task: commits from its launch_head to the next task's launch on the same repo
(or the branch tip). Does the fix touch tests? Read-only git."""
import json, subprocess, re, os, collections
S = json.load(open('states/states.json'))
REPOS = {}
for base in ('~/Developer/products/proxima', '~/Developer/products', '~/Developer'):
    b = os.path.expanduser(base)
    if os.path.isdir(b):
        for n in os.listdir(b):
            if os.path.isdir(os.path.join(b, n, '.git')) and n not in REPOS: REPOS[n] = os.path.join(b, n)
def git(repo, *a):
    r = subprocess.run(['git', '-C', repo] + list(a), capture_output=True, text=True)
    return (r.stdout or 'ok') if r.returncode == 0 else None
TEST = re.compile(r'(^|/)(tests?|__tests__|spec)/|\.(spec|test)\.[jt]sx?$|(^|/)test_[^/]+\.py$|_test\.py$')
res = collections.Counter(); rows = []
for r in S['runs']:
    d = json.load(open(r['file']))
    tasks = sorted(d['tasks'].values(), key=lambda t: t.get('started_at') or 0)
    for t in tasks:
        if not t['id'].startswith('AUDIT') or not t.get('launch_head'): continue
        repo = REPOS.get(t['repo'])
        if not repo: res['repo not found'] += 1; continue
        later = [u for u in tasks if u['repo'] == t['repo'] and (u.get('started_at') or 0) > (t.get('started_at') or 0) and u.get('launch_head') and u['launch_head'] != t['launch_head']]
        end = later[0]['launch_head'] if later else None
        if not git(repo, 'cat-file', '-e', t['launch_head'] + '^{commit}'): res['base commit gone'] += 1; continue
        if end is None:
            # branch tip: any ref containing launch_head named like the change
            kids = git(repo, 'rev-list', '--all', '--reflog', '--ancestry-path', '^' + t['launch_head']) or ''
            kids = [k for k in kids.split() if k != 'ok']
            if not kids: res['no descendant commit'] += 1; continue
            end = kids[0]
        files = git(repo, 'diff', '--name-only', t['launch_head'], end) or ''
        fl = [f for f in files.split() if f]
        if not fl: res['empty diff'] += 1; continue
        has_test = any(TEST.search(f) for f in fl)
        nblock = len(re.findall(r'BLOCKING', json.dumps(t.get('audit_findings') or '')))
        res['with test change' if has_test else 'no test change'] += 1
        rows.append(dict(change=d['change'], task=t['id'], repo=t['repo'], base=t['launch_head'][:10], end=str(end)[:40], files=len(fl), test=has_test, blocking=nblock))
print(res)
json.dump(rows, open('fix_tests.json', 'w'), indent=1)
print(sum(1 for x in rows if x['blocking']), 'rows with BLOCKING; of those with test change:', sum(1 for x in rows if x['blocking'] and x['test']))
