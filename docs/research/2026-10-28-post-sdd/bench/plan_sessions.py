import json, os, re, time, glob, collections, statistics as st
root = os.path.expanduser('~/.claude/projects'); cut = time.time() - 30*86400
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
WORKER = ('rein ship', 'worker_done', 'You are a Rein worker', 'orca orchestration')
res = collections.defaultdict(list)
def tset(ts): 
    from datetime import datetime
    return datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp()
for f in glob.glob(root + '/*/*.jsonl'):
    if os.path.getmtime(f) < cut: continue
    tags=set(); tok=0; turns=0; models=collections.Counter(); t0=t1=None; first_user=None; ctxmax=0; user_msgs=0
    for line in open(f, errors='ignore'):
        try: d=json.loads(line)
        except: continue
        ts=d.get('timestamp')
        if ts:
            try:
                x=tset(ts); t0=x if t0 is None else min(t0,x); t1=x if t1 is None else max(t1,x)
            except: pass
        m=d.get('message') or {}
        c=m.get('content')
        if d.get('type')=='user':
            txt = c if isinstance(c,str) else ' '.join(b.get('text','') for b in c if isinstance(b,dict) and b.get('type')=='text') if isinstance(c,list) else ''
            if txt and not txt.startswith('<') : user_msgs+=1
            if first_user is None and txt: first_user=txt[:400]
            for n in pat.findall(txt or ''): tags.add(n.strip())
        if d.get('type')=='assistant':
            if isinstance(c,list):
                for b in c:
                    if b.get('type')=='tool_use' and b.get('name')=='Skill': tags.add((b.get('input') or {}).get('skill',''))
            u=m.get('usage') or {}
            if u:
                turns+=1
                ctx=u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)
                tok+=ctx+u.get('output_tokens',0); ctxmax=max(ctxmax,ctx)
                models[(m.get('model') or '?').split('-2')[0]]+=1
    worker = bool(first_user and any(w in first_user for w in WORKER))
    kind = 'worker' if worker else None
    if not kind:
        if 'rein:rein-plan' in tags or 'rein:rein-discover' in tags: kind='plan/discover'
        elif 'rein:ship' in tags: kind='ship-launcher'
        elif 'rein:quick' in tags: kind='quick-launcher'
        elif turns==0: kind='empty'
        else: kind='other-interactive'
    res[kind].append(dict(tok=tok, turns=turns, mins=((t1-t0)/60 if t0 else 0), model=models.most_common(1)[0][0] if models else '', ctxmax=ctxmax, um=user_msgs, f=f))
def q(xs):
    xs=sorted(xs); 
    return f"med {st.median(xs):.1f} p75 {xs[3*len(xs)//4]:.1f} sum {sum(xs):.0f}" if xs else '-'
for k,v in res.items():
    print(f"\n{k}: n={len(v)}  models={collections.Counter(x['model'] for x in v).most_common(3)}")
    print('  tokens M', q([x['tok']/1e6 for x in v])); print('  turns', q([x['turns'] for x in v])); print('  user msgs', q([x['um'] for x in v])); print('  ctxmax k', q([x['ctxmax']/1e3 for x in v]))
json.dump(res, open('sessions_by_kind.json','w'))
