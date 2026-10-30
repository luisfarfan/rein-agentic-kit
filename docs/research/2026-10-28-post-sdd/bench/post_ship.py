import json, re, collections, statistics as st
res = json.load(open('sessions_by_kind.json'))
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
out = []
for s in res['plan/discover'] + res.get('ship-launcher', []):
    after = False; turns = 0; tok = 0; ctxs = []; notif = 0; watch_cmds = 0; human = 0
    for line in open(s['f'], errors='ignore'):
        try: d = json.loads(line)
        except: continue
        m = d.get('message') or {}; c = m.get('content'); names = []
        if d.get('type') == 'user':
            txt = c if isinstance(c, str) else ' '.join(b.get('text','') for b in c if isinstance(b, dict) and b.get('type')=='text') if isinstance(c, list) else ''
            names = [n.strip() for n in pat.findall(txt or '')]
            if after and txt and '<task-notification' in txt: notif += 1
            elif after and txt and not txt.startswith('<'): human += 1
        if d.get('type') == 'assistant' and isinstance(c, list):
            for b in c:
                if b.get('type') == 'tool_use':
                    if b.get('name') == 'Skill': names.append((b.get('input') or {}).get('skill',''))
                    if after and b.get('name') == 'Bash' and re.search(r'rein (watch|answer|ship)', (b.get('input') or {}).get('command','')): watch_cmds += 1
        if 'rein:ship' in names: after = True
        u = m.get('usage') if d.get('type') == 'assistant' else None
        if u and after:
            ctx = u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)
            turns += 1; tok += ctx + u.get('output_tokens',0); ctxs.append(ctx)
    if after: out.append(dict(turns=turns, tok=tok, ctx=st.median(ctxs) if ctxs else 0, notif=notif, watch=watch_cmds, human=human))
def q(xs):
    xs = sorted(xs); return f"med {st.median(xs):.1f} p75 {xs[3*len(xs)//4]:.1f} sum {sum(xs):.1f} n={len(xs)}"
print('sessions with /ship', len(out))
for k in ['turns','notif','watch','human']: print(k, q([o[k] for o in out]))
print('tokens after /ship (M)', q([o['tok']/1e6 for o in out]))
print('median ctx per turn after /ship (k)', q([o['ctx']/1e3 for o in out]))
