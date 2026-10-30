import json, re, collections, statistics as st
res = json.load(open('sessions_by_kind.json'))
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
agg = collections.Counter(); per = []
for s in res['plan/discover']:
    phase = 'pre'; seg = collections.Counter(); segturns = collections.Counter(); nplan=0; nship=0; agent_calls=0; bg=0; tools=collections.Counter()
    for line in open(s['f'], errors='ignore'):
        try: d = json.loads(line)
        except: continue
        m = d.get('message') or {}; c = m.get('content')
        names = []
        if d.get('type') == 'user':
            txt = c if isinstance(c, str) else ' '.join(b.get('text','') for b in c if isinstance(b, dict) and b.get('type')=='text') if isinstance(c, list) else ''
            names = [n.strip() for n in pat.findall(txt or '')]
            if 'task-notification' in (txt or '') or 'background' in (txt or '')[:200]: bg += 1
        if d.get('type') == 'assistant' and isinstance(c, list):
            for b in c:
                if b.get('type') == 'tool_use':
                    tools[b.get('name')] += 1
                    if b.get('name') == 'Skill': names.append((b.get('input') or {}).get('skill',''))
                    if b.get('name') in ('Agent','Task'): agent_calls += 1
        for n in names:
            if n in ('rein:rein-discover',) and phase == 'pre': phase = 'discover'
            if n == 'rein:rein-plan': phase = 'plan'; nplan += 1
            if n == 'rein:ship': phase = 'post-ship'; nship += 1
        u = m.get('usage') if d.get('type') == 'assistant' else None
        if u:
            ctx = u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)+u.get('output_tokens',0)
            seg[phase] += ctx; segturns[phase] += 1
    tot = sum(seg.values()) or 1
    agg.update(seg)
    per.append(dict(tot=tot, nplan=nplan, nship=nship, agent=agent_calls, post=seg['post-ship']/tot, pre=(seg['pre']+seg['discover'])/tot, plan=seg['plan']/tot, tools=tools))
T = sum(agg.values())
print('token share by phase across plan/discover sessions:', {k: round(v/T,3) for k,v in agg.items()}, 'total B', round(T/1e9,2))
print('sessions with >=1 /ship in same session:', sum(1 for p in per if p['nship']), '/', len(per))
print('plans per session med', st.median(p['nplan'] for p in per), 'max', max(p['nplan'] for p in per))
print('Agent/subagent calls per session med', st.median(p['agent'] for p in per))
tt = collections.Counter()
for p in per: tt.update(p['tools'])
print('tool calls total', sum(tt.values()), tt.most_common(15))
