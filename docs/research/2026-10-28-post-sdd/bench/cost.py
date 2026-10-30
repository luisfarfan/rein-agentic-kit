"""API-equivalent cost by token type. Prices $/MTok (claude-api skill, cached 2026-10-06):
input, output, cache read; cache write = 1.25x input (5m) / 2x input (1h)."""
import json, re, collections
P = {'opus-5-5': (4, 20, 0.20), 'sonnet-5-5': (2, 10, 0.20), 'haiku-5-5': (0.10, 0.50, 0.01), 'opus-5': (5, 25, 0.50), 'opus-4': (5, 25, 0.50), 'sonnet-4': (3, 15, 0.30)}
def price(model):
    m = (model or '').replace('claude-', '')
    for k in P:
        if m.startswith(k): return P[k]
    return P['opus-5-5']
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
res = json.load(open('sessions_by_kind.json'))
def blank(): return collections.Counter()
agg = collections.defaultdict(blank)
for kind, ss in res.items():
    for s in ss:
        after = False
        for line in open(s['f'], errors='ignore'):
            try: d = json.loads(line)
            except: continue
            m = d.get('message') or {}; c = m.get('content')
            if kind == 'plan/discover':
                names = []
                if d.get('type') == 'user':
                    txt = c if isinstance(c, str) else ' '.join(b.get('text','') for b in c if isinstance(b, dict) and b.get('type')=='text') if isinstance(c, list) else ''
                    names = pat.findall(txt or '')
                elif isinstance(c, list):
                    names = [(b.get('input') or {}).get('skill','') for b in c if isinstance(b, dict) and b.get('type')=='tool_use' and b.get('name')=='Skill']
                if any(n.strip()=='rein:ship' for n in names): after = True
            u = m.get('usage') if d.get('type') == 'assistant' else None
            if not u: continue
            pi, po, pr = price(m.get('model'))
            cc = u.get('cache_creation') or {}
            w5 = cc.get('ephemeral_5m_input_tokens'); w1 = cc.get('ephemeral_1h_input_tokens')
            wt = u.get('cache_creation_input_tokens', 0)
            if w5 is None and w1 is None: w5, w1 = 0, wt  # assume 1h for main sessions
            keys = [kind] + (['plan/discover:post-ship'] if after else [])
            for k in keys:
                A = agg[k]
                A['read'] += u.get('cache_read_input_tokens', 0); A['write'] += wt; A['input'] += u.get('input_tokens', 0); A['output'] += u.get('output_tokens', 0)
                A['$read'] += u.get('cache_read_input_tokens', 0) * pr / 1e6
                A['$write'] += (w5 * pi * 1.25 + w1 * pi * 2) / 1e6
                A['$input'] += u.get('input_tokens', 0) * pi / 1e6
                A['$output'] += u.get('output_tokens', 0) * po / 1e6
tot = sum(sum(v for k, v in A.items() if k.startswith('$')) for kk, A in agg.items() if ':' not in kk)
print(f"{'kind':28s} {'vol B':>7s} {'read%':>6s} {'API $':>9s} {'$ share':>7s}  $read  $write  $out")
for k, A in sorted(agg.items(), key=lambda x: -sum(v for kk, v in x[1].items() if kk.startswith('$'))):
    vol = A['read'] + A['write'] + A['input'] + A['output']; usd = A['$read'] + A['$write'] + A['$input'] + A['$output']
    print(f"{k:28s} {vol/1e9:7.2f} {A['read']/max(vol,1):6.1%} {usd:9.0f} {usd/tot:7.1%}  {A['$read']/usd:.0%}  {A['$write']/usd:.0%}  {A['$output']/usd:.0%}")
print('total API-equivalent $', round(tot))
