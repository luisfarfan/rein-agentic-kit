"""Difficulty signals per approved change, for picking screen candidates (read-only on the repos).

escaped = later `fix` commits (<=21 days after the run ended) that touch the change's source files:
defects that got past REIN's gates and audit. plan_ok = tasks.md readable at the plan repo's base."""
import json, os, re, subprocess, sys
SP = sys.argv[1]  # scratchpad holding states/ and candidates.json from candidates.py
S = json.load(open(os.path.join(SP, 'states/states.json')))
C = {x['change']: x for x in json.load(open(os.path.join(SP, 'candidates.json')))}
PROD = os.path.expanduser('~/Developer/products/proxima')
DOC = re.compile(r'(^|/)(openspec|docs)/|\.md$|i18n/|/bench/')
TEST = re.compile(r'(\.spec\.|\.test\.|(^|/)tests?/|test_)')


def git(repo, *a):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True).stdout


rows = []
for r in S['runs']:
    d = json.load(open(r['file']))
    if d['change'] not in C or d.get('phase') != 'done':
        continue
    end = int(os.path.getmtime(r['file']))
    ts = [t for t in d['tasks'].values() if not re.match(r'^(AUDIT|GATE)\d', t['id'])]
    bases = {}
    for t in sorted(ts, key=lambda t: t.get('started_at') or 0):
        bases.setdefault(t['repo'], t.get('launch_head'))
    src = [f for f in d.get('changed_files') or [] if not DOC.search(f)]
    escaped, plan_ok = [], False
    for repo, base in bases.items():
        path = os.path.join(PROD, repo)
        if not os.path.isdir(path):
            continue
        mine = [f.split('/', 1)[1] for f in src if f.startswith(repo + '/') and not TEST.search(f)]
        if mine:
            out = git(path, 'log', 'develop', '--no-merges', '--format=%h %at %s', '--since=%d' % end,
                      '--until=%d' % (end + 21 * 86400), '--', *mine)
            escaped += ['%s:%s' % (repo, l) for l in out.splitlines() if re.search(r' fix(\(|:)', l)]
    plan_repo = (d.get('plan_path') or '').split('/openspec/')[0]
    if os.path.isdir(plan_repo):
        plan_ok = bool(git(plan_repo, 'log', '--all', '--diff-filter=A', '--format=%h', '--',
                           'openspec/changes/%s/tasks.md' % d['change'],
                           'openspec/changes/archive/*%s/tasks.md' % d['change']).strip())
    c = C[d['change']]
    rows.append(dict(change=d['change'], plan_repo=os.path.basename(plan_repo), stratum=c['stratum'], repos=c['repos'], tasks=c['tasks'], files=c['files'],
                     src_files=len(src), tests_in_change=sum(1 for f in src if TEST.search(f)),
                     blocking=c['blocking'], witness=c['witness'], escaped=len(escaped), escaped_list=escaped[:6],
                     base_ok=c['base_ok'], plan_ok=plan_ok, dispatches=c['dispatches']))
json.dump(rows, open(os.path.join(SP, 'screen_signals.json'), 'w'), indent=1)
for x in sorted(rows, key=lambda x: (-(x['escaped'] > 0) - (x['blocking'] > 0), x['stratum'])):
    print('%s %-40s %-28s t%d f%-3d src%-3d tst%-2d B%d w%d esc%d base%d plan%d' % (
        x['stratum'], x['change'][:40], ','.join(x['repos'])[:28], x['tasks'], x['files'], x['src_files'],
        x['tests_in_change'], x['blocking'], x['witness'], x['escaped'], x['base_ok'], x['plan_ok']))
