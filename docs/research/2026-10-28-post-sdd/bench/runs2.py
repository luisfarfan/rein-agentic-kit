import json, os, re, glob, statistics as st, collections
S = json.load(open('states/states.json'))
led = collections.defaultdict(lambda: dict(total=0, wall=0.0, turns=0, agents=0))
for l in open(os.path.expanduser('~/.claude/rein/runs.jsonl')):
    try: d = json.loads(l)
    except: continue
    if not str(d.get('wf_id','')).startswith('ship-'): continue
    c = d.get('change'); 
    if not c: continue
    L = led[c]; L['total'] += d.get('total',0); L['wall'] = max(L['wall'], d.get('wall_clock_min') or 0); L['turns'] += d.get('turns',0); L['agents'] += d.get('agents_counted',0)
rows = []
for r in S['runs']:
    d = json.load(open(r['file']))
    pp = d.get('plan_path') or ''
    pdir = os.path.dirname(pp)
    pbytes = 0; pfiles = 0
    if pdir and os.path.isdir(pdir):
        for f in glob.glob(pdir + '/**/*', recursive=True):
            if os.path.isfile(f): pbytes += os.path.getsize(f); pfiles += 1
    ts = d['tasks'].values()
    sev = collections.Counter()
    for t in ts:
        for f in (t.get('audit_findings') or []):
            s = f if isinstance(f,str) else json.dumps(f)
            m = re.match(r'\s*(BLOCKING|IMPORTANT|SUGGESTION)', s)
            sev[m.group(1) if m else 'other'] += 1
    starts = [t.get('started_at') or 0 for t in ts if t.get('started_at')]
    rows.append(dict(change=r['change'], plan=r['n_plan'], afix=r['n_audit_fix'], gfix=r['n_gate_fix'], disp=r['dispatches'],
        rounds=r['audit_rounds'] or 0, dec=r['decisions'], files=r['changed_files'], repos=r['repos'], pbytes=pbytes, pfiles=pfiles,
        tok=led[r['change']]['total'], wall=led[r['change']]['wall'], sev=dict(sev), audit=r['audit'],
        start=min(starts) if starts else 0, mtime=os.path.getmtime(r['file'])))
json.dump(rows, open('runs2.json','w'), default=str)
def q(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return '-'
    return f"med {st.median(xs):.1f} p25 {xs[len(xs)//4]:.1f} p75 {xs[3*len(xs)//4]:.1f} max {xs[-1]:.1f} (n={len(xs)})"
print('runs', len(rows))
for k in ['plan','afix','disp','rounds','dec','files','repos','pbytes','pfiles']:
    print(k, q([r[k] for r in rows]))
print('tokens(M) w/ ledger', q([r['tok']/1e6 for r in rows if r['tok']]))
print('elapsed hours start->state mtime', q([(r['mtime']-r['start'])/3600 for r in rows if r['start']]))
small = [r for r in rows if 0 < r['files'] <= 5]
print('\nSMALL runs (<=5 changed files):', len(small))
for k in ['plan','afix','disp','rounds','dec','pbytes']:
    print(' ', k, q([r[k] for r in small]))
print('  share small runs with audit fix', sum(1 for r in small if r['afix'])/max(1,len(small)))
print('  tokens(M)', q([r['tok']/1e6 for r in small if r['tok']]))
big = [r for r in rows if r['files'] > 15]
print('BIG runs (>15 files):', len(big)); print('  afix', q([r['afix'] for r in big])); print('  disp',q([r['disp'] for r in big]))
sev = collections.Counter()
for r in rows: sev.update(r['sev'])
print('\nfinding severities in audit-fix tasks', sev)
print('plan bytes per changed file', q([r['pbytes']/r['files'] for r in rows if r['files'] and r['pbytes']]))
print('dispatches / plan task', q([r['disp']/r['plan'] for r in rows if r['plan']]))
print('runs needing >=1 owner decision', sum(1 for r in rows if r['dec']), '/', len(rows))
print('runs with audit rounds>=2', sum(1 for r in rows if r['rounds']>=2))
