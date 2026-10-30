"""Post-/ship turns in planning sessions, attributed to the episode that triggered them:
a background notification (watch) or a human message."""
import json, re, collections
exec(open('cost.py').read().split("pat = re.compile")[0])  # reuse P/price
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
res = json.load(open('sessions_by_kind.json'))
agg = collections.defaultdict(collections.Counter)
for s in res['plan/discover']:
    after = False; trig = 'human'
    for line in open(s['f'], errors='ignore'):
        try: d = json.loads(line)
        except: continue
        m = d.get('message') or {}; c = m.get('content')
        if d.get('type') == 'user':
            if isinstance(c, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c):
                pass  # continuation of the same episode
            else:
                txt = c if isinstance(c, str) else ' '.join(b.get('text','') for b in c if isinstance(b, dict) and b.get('type')=='text') if isinstance(c, list) else ''
                if 'rein:ship' in pat.findall(txt or '') or '/rein:ship' in (txt or ''): after = True
                if txt and ('<task-notification' in txt or 'side-sessions-event' in txt): trig = 'watch-notification'
                elif txt and not txt.startswith('<system-reminder'): trig = 'human'
        if d.get('type') == 'assistant' and isinstance(c, list):
            if any(isinstance(b, dict) and b.get('type')=='tool_use' and b.get('name')=='Skill' and (b.get('input') or {}).get('skill')=='rein:ship' for b in c): after = True
        u = m.get('usage') if d.get('type') == 'assistant' else None
        if not u or not after: continue
        pi, po, pr = price(m.get('model'))
        cc = u.get('cache_creation') or {}; w5 = cc.get('ephemeral_5m_input_tokens') or 0; w1 = cc.get('ephemeral_1h_input_tokens')
        if w1 is None: w1 = u.get('cache_creation_input_tokens', 0)
        usd = (u.get('cache_read_input_tokens',0)*pr + (w5*1.25 + w1*2)*pi + u.get('input_tokens',0)*pi + u.get('output_tokens',0)*po)/1e6
        A = agg[trig]; A['turns'] += 1; A['usd'] += usd
        A['vol'] += u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)+u.get('input_tokens',0)+u.get('output_tokens',0)
for k, A in agg.items(): print(k, dict(turns=A['turns'], volB=round(A['vol']/1e9,2), usd=round(A['usd'])))
