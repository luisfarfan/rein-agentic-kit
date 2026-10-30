import json, os, re, sys, time, collections, glob
root = os.path.expanduser('~/.claude/projects')
cut = time.time() - 30*86400
pat = re.compile(r'<command-name>/?([^<]+)</command-name>')
inv = collections.Counter(); sess_with = collections.defaultdict(set)
sessions = 0; sess_any_rein = set()
for f in glob.glob(root + '/**/*.jsonl', recursive=True):
    if os.path.getmtime(f) < cut: continue
    if '/subagents/' in f: continue
    sessions += 1
    try:
        for line in open(f, errors='ignore'):
            if 'rein' not in line and 'Skill' not in line and 'command-name' not in line: continue
            try: d = json.loads(line)
            except: continue
            m = d.get('message') or {}
            c = m.get('content')
            names = []
            if isinstance(c, str):
                names += pat.findall(c)
            elif isinstance(c, list):
                for b in c:
                    if b.get('type') == 'tool_use' and b.get('name') == 'Skill':
                        names.append((b.get('input') or {}).get('skill', ''))
                    if b.get('type') == 'text':
                        names += pat.findall(b.get('text', ''))
            for n in names:
                n = n.strip()
                inv[n] += 1; sess_with[n].add(f)
    except Exception as e:
        pass
print('main sessions last 30d:', sessions)
for n, k in inv.most_common(60):
    print(f'{k:5d} inv  {len(sess_with[n]):4d} sess  {n}')
