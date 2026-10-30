"""For each task whose last failure is Rein's outside-worktree guard: did that task's worker
ever reference the outside checkout path in a tool call? Read-only."""
import json, re, glob, os, collections
S = json.load(open('states/states.json'))
cases = []
for r in S['runs']:
    d = json.load(open(r['file']))
    for t in d['tasks'].values():
        lf = t.get('last_failure') or ''
        m = re.search(r'wrote outside its worktree, in (\S+?):\s*(.*?)(;|$)', lf)
        if m: cases.append(dict(change=d['change'], task=t['id'], agent=t.get('agent'), outside=m.group(1), what=m.group(2)[:160], wt=(d.get('worktree_paths') or {}).get(t['repo'], '')))
print('cases', len(cases))
claude = glob.glob(os.path.expanduser('~/.claude/projects/*orca-workspaces*/*.jsonl'))
codex = glob.glob(os.path.expanduser('~/.codex/sessions/**/*.jsonl'), recursive=True)
def first_user(f, n=60):
    out = []
    for i, line in enumerate(open(f, errors='ignore')):
        if i > n: break
        out.append(line)
    return ''.join(out)
idx = []
for f in claude + codex:
    try: head = first_user(f)
    except: continue
    if "You are a Rein ship worker" in head or "Audit the change as a whole; you wrote none of it" in head: idx.append((f, head))
print('worker transcripts indexed', len(idx))
res = collections.Counter(); detail = []
for c in cases:
    fs = [f for f, h in idx if c['change'] in h and re.search(r'\b%s\b' % re.escape(c['task']), h)]
    if not fs: res['no transcript'] += 1; continue
    hit = False
    path = c['outside'].rstrip('/')
    for f in fs:
        for line in open(f, errors='ignore'):
            if path not in line: continue
            if '"tool_use"' in line or 'function_call' in line or '"exec_command"' in line or 'local_shell' in line:
                # path appears in a tool call; ignore occurrences that are the worktree (which may contain the repo name)
                for mm in re.finditer(re.escape(path) + r'(?![-\w])', line):
                    hit = True; break
            if hit: break
        if hit: break
    k = ('worker referenced outside path' if hit else 'worker never referenced it') + ' | ' + ('commits' if 'commit' in c['what'] else 'files')
    res[k] += 1; detail.append((k, c['change'], c['task'], c['agent'], c['what'][:100]))
print(res)
for x in detail: print(x)
