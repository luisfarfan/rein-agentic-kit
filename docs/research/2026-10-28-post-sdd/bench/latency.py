"""Latency by stage for each `rein ship` change: worker intervals (transcripts) + gate intervals
(.gates.jsonl). Idle = wall-clock not covered by any worker or gate (dispatch, startup, owner gates)."""
import json, glob, os, collections, statistics as st
W = [json.loads(l) for l in open('workers/workers.jsonl')]
G = collections.defaultdict(list)
for f in glob.glob(os.path.expanduser('~/orca/workspaces/**/.rein/ship/*.gates.jsonl'), recursive=True) + glob.glob(os.path.expanduser('~/Developer/**/.rein/ship/**/*.gates.jsonl'), recursive=True):
    for l in open(f):
        try: g = json.loads(l)
        except: continue
        el = sum((s.get('elapsedMs') or 0) for s in g.get('slots') or [] if not s.get('cached')) / 1000
        G[g.get('change')].append((g['at'] - el, g['at'], 'gate_repo' if g.get('tier') == 'full' else 'gate_task'))
def union(iv):
    iv = sorted(iv); tot = 0; cur = None
    for a, b in iv:
        if cur is None or a > cur[1]: 
            if cur: tot += cur[1] - cur[0]
            cur = [a, b]
        else: cur[1] = max(cur[1], b)
    if cur: tot += cur[1] - cur[0]
    return tot
bych = collections.defaultdict(list)
for w in W:
    if w.get('start') and w.get('end') and w.get('change'):
        k = {'task': 'implement', 'retry': 'retry', 'audit_fix': 'fix', 'gate_fix': 'fix', 'audit': 'audit', 'reaudit': 'audit'}.get(w['kind'], 'other')
        bych[w['change']].append((w['start'], w['end'], k))
rows = []
for ch, iv in bych.items():
    iv = iv + G.get(ch, [])
    t0 = min(a for a, b, k in iv); t1 = max(b for a, b, k in iv)
    wall = t1 - t0
    if wall > 3 * 86400: continue  # multi-day reruns distort; reported separately
    r = dict(change=ch, wall=wall / 60, gates=bool(G.get(ch)))
    for k in ('implement', 'retry', 'fix', 'audit', 'gate_task', 'gate_repo'):
        r[k] = union([(a, b) for a, b, kk in iv if kk == k]) / 60
    r['active'] = union([(a, b) for a, b, k in iv]) / 60
    r['idle'] = r['wall'] - r['active']
    rows.append(r)
print('changes', len(rows), 'with gate logs', sum(r['gates'] for r in rows))
def q(xs):
    xs = sorted(xs); return f"med {st.median(xs):6.1f}  p75 {xs[3*len(xs)//4]:6.1f}  sum {sum(xs):7.0f}"
for k in ('wall', 'active', 'idle', 'implement', 'retry', 'fix', 'audit', 'gate_task', 'gate_repo'):
    print(f"{k:10s} min: {q([r[k] for r in rows])}")
gr = [r for r in rows if r['gates']]
print('\nonly changes with gate logs:', len(gr))
for k in ('wall', 'active', 'idle', 'implement', 'retry', 'fix', 'audit', 'gate_task', 'gate_repo'):
    print(f"{k:10s} min: {q([r[k] for r in gr])}")
S = sum(r['wall'] for r in gr)
print('share of total wall:', {k: round(sum(r[k] for r in gr) / S, 3) for k in ('implement', 'retry', 'fix', 'audit', 'gate_task', 'gate_repo', 'idle')})
json.dump(rows, open('latency.json', 'w'))
