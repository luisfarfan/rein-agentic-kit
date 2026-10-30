import json, re, collections, statistics as st
res = json.load(open('sessions_by_kind.json'))
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
wins = []
for s in res['plan/discover']:
    state = None; w = None; lastctx = 0
    for line in open(s['f'], errors='ignore'):
        try: d = json.loads(line)
        except: continue
        m = d.get('message') or {}; c = m.get('content'); names = []; cmds = []
        if d.get('type') == 'user':
            txt = c if isinstance(c, str) else ' '.join(b.get('text','') for b in c if isinstance(b, dict) and b.get('type')=='text') if isinstance(c, list) else ''
            names = [n.strip() for n in pat.findall(txt or '')]
            if w and txt and not txt.startswith('<') and 'tool_use_id' not in line: w['user'] += 1
        if d.get('type') == 'assistant' and isinstance(c, list):
            for b in c:
                if b.get('type') == 'tool_use':
                    if b.get('name') == 'Skill': names.append((b.get('input') or {}).get('skill',''))
                    if b.get('name') == 'Bash': cmds.append((b.get('input') or {}).get('command',''))
        u = m.get('usage') if d.get('type') == 'assistant' else None
        if u:
            ctx = u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)
            lastctx = ctx
            if w: w['tok'] += ctx + u.get('output_tokens',0); w['turns'] += 1; w['end_ctx'] = ctx
            if w and w['t0'] is None: w['t0'] = d.get('timestamp')
        if 'rein:rein-plan' in names and w is None:
            w = dict(start_ctx=lastctx, tok=0, turns=0, user=0, end_ctx=0, t0=None, t1=None, done=False)
        if w and not w['done'] and any(re.search(r'\brein tasks\b', x) for x in cmds):
            w['done'] = True; w['t1'] = d.get('timestamp'); wins.append(w); w = None
    if w: w['done'] = False; wins.append(w)
from datetime import datetime
def ts(x): return datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
done = [w for w in wins if w['done']]
def q(xs):
    xs = sorted(xs); return f"med {st.median(xs):.1f} p25 {xs[len(xs)//4]:.1f} p75 {xs[3*len(xs)//4]:.1f} n={len(xs)}"
print('plan windows', len(wins), 'completed (rein tasks run)', len(done))
print('ctx when /rein-plan starts (k)', q([w['start_ctx']/1e3 for w in wins]))
print('turns /rein-plan -> written', q([w['turns'] for w in done]))
print('tokens /rein-plan -> written (M)', q([w['tok']/1e6 for w in done]))
print('user msgs in window', q([w['user'] for w in done]))
print('minutes', q([(ts(w['t1'])-ts(w['t0']))/60 for w in done if w['t0'] and w['t1']]))
