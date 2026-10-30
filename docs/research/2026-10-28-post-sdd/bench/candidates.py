"""Pilot candidates: one row per approved single-run change with a recoverable base commit."""
import json, re, os, subprocess, collections, glob
S = json.load(open('states/states.json')); FT = {(x['change'], x['task']): x for x in json.load(open('fix_tests.json'))}
REPOS = {}
for b in ('~/Developer/products/proxima', '~/Developer/products', '~/Developer'):
    b = os.path.expanduser(b)
    for n in (os.listdir(b) if os.path.isdir(b) else []):
        if os.path.isdir(os.path.join(b, n, '.git')) and n not in REPOS: REPOS[n] = os.path.join(b, n)
RISK = re.compile(r'invoic|sunat|fiscal|payment|mercadopago|auth|oauth|token|migration|credit|refund', re.I)
rows = []
for r in S['runs']:
    d = json.load(open(r['file']))
    if d.get('phase') != 'done' or d.get('audit_result') != 'approved': continue
    ts = sorted(d['tasks'].values(), key=lambda t: t.get('started_at') or 0)
    plan = [t for t in ts if not re.match(r'^(AUDIT|GATE)\d', t['id'])]
    repos = sorted({t['repo'] for t in ts})
    base = {}
    for t in plan:
        if t.get('launch_head') and t['repo'] not in base: base[t['repo']] = t['launch_head']
    ok = all(rp in REPOS and subprocess.run(['git', '-C', REPOS[rp], 'cat-file', '-e', base.get(rp, 'x') + '^{commit}'], capture_output=True).returncode == 0 for rp in repos)
    blocking = sum(len(re.findall('BLOCKING', json.dumps(t.get('audit_findings') or ''))) for t in ts)
    witness = sum(1 for t in ts if t['id'].startswith('AUDIT') and FT.get((d['change'], t['id']), {}).get('test') and FT[(d['change'], t['id'])]['files'] <= 40)
    files = d.get('changed_files') or []
    n = len(plan); risky = bool(RISK.search(' '.join(files)))
    stratum = 'C' if risky else ('L' if len(repos) > 1 or n >= 4 else ('S' if n == 1 and len(files) <= 12 else 'M'))
    rows.append(dict(change=d['change'], stratum=stratum, repos=repos, tasks=n, files=len(files), blocking=blocking, witness=witness,
                     base_ok=ok, plan_exists=os.path.exists(d.get('plan_path') or ''), decisions=len(d.get('owner_decisions') or []), dispatches=sum(t.get('dispatches', 0) for t in ts)))
c = collections.Counter((x['stratum'], x['blocking'] > 0, x['base_ok']) for x in rows)
print('approved runs', len(rows)); print(sorted(c.items()))
json.dump(rows, open('candidates.json', 'w'), indent=1)
for x in sorted(rows, key=lambda x: (x['stratum'], -x['blocking'])):
    if x['base_ok']: print(x['stratum'], x['change'], x['repos'], 'tasks', x['tasks'], 'files', x['files'], 'BLOCK', x['blocking'], 'witness', x['witness'], 'dec', x['decisions'], 'plan', x['plan_exists'])
