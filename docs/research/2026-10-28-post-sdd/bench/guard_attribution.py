"""Strict test: does the worker's own tool call write the exact outside file named in the rejection,
or run git commit/merge/cherry-pick/push inside the outside checkout?"""
import json, re, collections
exec(open(__file__.replace('guard_attribution.py','guard_check.py')).read().split("res = collections.Counter()")[0])
out = collections.Counter(); rows = []
for c in cases:
    fs = [f for f, h in idx if c['change'] in h and re.search(r'\b%s\b' % re.escape(c['task']), h)]
    path = c['outside'].rstrip('/')
    files = re.findall(r'([\w./-]+\.\w+)', c['what']) if 'changed:' in c['what'] else []
    ev = []
    for f in fs:
        for line in open(f, errors='ignore'):
            if path not in line: continue
            try: d = json.loads(line)
            except: continue
            blobs = []
            msg = d.get('message') or {}
            if isinstance(msg.get('content'), list):
                for b in msg['content']:
                    if isinstance(b, dict) and b.get('type') == 'tool_use': blobs.append((b.get('name'), b.get('input') or {}))
            p = d.get('payload') or {}
            if p.get('type') in ('function_call', 'custom_tool_call', 'local_shell_call'): blobs.append((p.get('name'), p.get('arguments') or p.get('input') or p.get('action') or ''))
            for name, inp in blobs:
                txt = inp if isinstance(inp, str) else json.dumps(inp)
                if name in ('Edit', 'Write', 'MultiEdit') and isinstance(inp, dict) and str(inp.get('file_path','')).startswith(path + '/'):
                    ev.append('Edit/Write ' + inp['file_path'][len(path)+1:])
                    continue
                cmd = inp.get('command', txt) if isinstance(inp, dict) else txt
                cmd = cmd if isinstance(cmd, str) else json.dumps(cmd)
                for seg in re.split(r'&&|;|\n|\|\|', cmd):
                    if re.search(r'(cd|-C)\s+["\']?' + re.escape(path) + r'["\']?(\s|$)', seg) or (path + '/') in seg:
                        if re.search(r'git\s+(-C\s+\S+\s+)?(commit|merge|cherry-pick|rebase|am|reset --hard|checkout|stash)', cmd):
                            ev.append('git-write in outside: ' + seg.strip()[:90]); break
                        if any(fn in seg for fn in files) and re.search(r"(>|sed -i|tee|cat <<|apply_patch|python)", seg):
                            ev.append('shell write of named file: ' + seg.strip()[:90]); break
    v = 'worker wrote it' if ev else ('no transcript' if not fs else 'no write by worker')
    out[v] += 1; rows.append((v, c['change'], c['task'], c['what'][:60], ev[:1]))
print(out)
for r in sorted(rows, key=lambda x: x[0]): print(r)
