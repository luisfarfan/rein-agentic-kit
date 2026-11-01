from pathlib import Path
import subprocess,json,hashlib,ast,shlex,shutil
root=Path.cwd(); doc=root/'docs/research/2026-11-01-followup'; scratch=root/'.rein/followup';scratch.mkdir(exist_ok=True)
def git(*args,cwd=root):return subprocess.check_output(['git',*args],cwd=cwd)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def init(p):
 subprocess.run(['git','init','-q',str(p)],check=True)
 subprocess.run(['git','add','.'],cwd=p,check=True)
 subprocess.run(['git','-c','user.name=REIN evaluation','-c','user.email=eval@localhost','commit','-qm','Frozen isolated evaluation input'],cwd=p,check=True)
 return json.loads(subprocess.check_output(['orca','repo','add','--path',str(p),'--json']))
def preflight(p,name):
 dest=Path('/tmp/rein-followup-'+name+'-preflight.json')
 code="import os,json,subprocess,pathlib; p=pathlib.Path.cwd(); assert str(p)=="+repr(str(p))+"; context=json.loads(subprocess.check_output(['python3',"+repr(str(root/'plugins/rein/bin/rein'))+",'context','.'])); proof={'cwd':str(p),'git_root':subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip(),'remotes':subprocess.check_output(['git','remote'],text=True),'context':context}; assert proof['git_root']==str(p) and not proof['remotes']; pathlib.Path("+repr(str(dest))+").write_text(json.dumps(proof,indent=2)); print('PREFLIGHT_OK',p)"
 r=json.loads(subprocess.check_output(['orca','terminal','create','--worktree','path:'+str(p),'--title',name+' cwd preflight','--command','python3 -c '+shlex.quote(code),'--json'],cwd=p))
 return {'receipt':r,'proof_path':str(dest)}
# Ensure exact frozen content; no prompt changes.
frozen=json.loads((root/'docs/research/2026-11-01-evaluation/frozen-hashes.json').read_text())
plugin=root/'.rein/p0-live-evaluation/new/plugin'
for rel in ['skills/rein-discover/SKILL.md','PROVIDERS.md','IMPACT.md']:
 assert (plugin/rel).read_bytes()==git('show','b2f4244:plugins/rein/'+rel),rel
before=ast.parse(git('show','b2f4244:plugins/rein/bin/rein').decode());after=ast.parse((root/'plugins/rein/bin/rein').read_text())
for name in ('cmd_context','_positional','_opt'):
 def fn(tree):return ast.dump(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name))
 assert fn(before)==fn(after),name
for rel in ['detect.py','plan.py']:
 assert (root/'plugins/rein/lib'/rel).read_bytes()==git('show','b2f4244:plugins/rein/lib/'+rel)
launches=json.loads((root/'.rein/p0-live-evaluation-launches.json').read_text())
manifest={'p1':{},'p2':{},'version':'b2f4244','context_code_unchanged':True}
for case in ('case1','case2'):
 p=root/'.rein/p0-live-evaluation/new'/case
 for source,h in frozen.items():
  if ('/'+case+'/') in source:
   rel=source.split('/'+case+'/')[1];assert digest(p/rel)==h
 assert not (p/'.git').exists()
 old=next(x for x in launches if x['arm']=='new' and x['case']==case)
 output=Path('/tmp/rein-eval-'+case+'-new.md')
 if output.exists():shutil.copyfile(output,scratch/(case+'-new-prior.md'))
 repo=init(p)
 manifest['p1'][case]={'path':str(p),'repo':repo['result'],'prompt':old['prompt'],'prompt_sha256':hashlib.sha256(old['prompt'].encode()).hexdigest(),'preflight':preflight(p,case)}
# Separate frozen complete plugin for P2. Never modifies canonical REIN.
resource=scratch/'frozen-plugin'; resource.mkdir()
import tarfile,io
archive=git('archive','b2f4244','plugins/rein')
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(resource)
resource=resource/'plugins/rein'
common='''# Local isolated implementation exercise
Only this repository may change. No network, production repositories, other case
folders, historical reports, evaluator files, installs, workers, commits or push.
This is an in-process menu-visibility example, not an HTTP deployment or a change
to mutation authority. Verification: python3 -B -m unittest test_panel -q.
Budget: twelve tool calls and 180 seconds for the complete task. Use the same
session to complete authorized work. Report actual strategy, checks and limits;
never claim an independent review you did not receive.
'''
extra=f'''
Experimental REIN entry policy: before choosing how to execute, inspect the task
using the Impact Discovery guidance in {resource}/skills/rein-discover/SKILL.md.
This is discovery within an authorized implementation, not a separate read-only
Discover invocation. Read its resources only as needed. Produce an evidence-backed
v2 report and consult {resource}/bin/rein route before implementing. Follow its
unresolved-evidence and required-review controls; CLI exit 0 alone is not clearance.
If review is required, do not self-approve: report the pending requirement.
This frozen plugin is the only extra directory you may read. No automatic chain
of other skills or new workers. Preserve the observed report and route output.
'''
workflow=root/'.rein/p0-live-evaluation/workflow'
paths=git('ls-tree','-r','--name-only','HEAD',cwd=workflow).decode().splitlines()
source_hashes={}
for arm in ('native','rein'):
 p=scratch/('p2-'+arm);p.mkdir()
 for rel in paths:
  if rel=='AGENTS.md':continue
  dst=p/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(git('show','HEAD:'+rel,cwd=workflow))
  source_hashes[rel]=digest(dst)
 (p/'AGENTS.md').write_text(common+(extra if arm=='rein' else ''))
 repo=init(p)
 manifest['p2'][arm]={'path':str(p),'repo':repo['result'],'preflight':preflight(p,'p2-'+arm)}
 (doc/('p2-'+arm+'-AGENTS.md')).write_text((p/'AGENTS.md').read_text())
manifest['p2_source_hashes']=source_hashes
manifest['p2_prompt']='Haz que el panel de aprobaciones sea visible también para quienes tienen inventory:manage o pricing:manage. Conserva los accesos existentes y las comprobaciones de autorización para aprobar. Trabaja sólo en este ejemplo local; no hagas push.'
(scratch/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Prepared four workspaces, no model launched. Manifest:',scratch/'manifest.json')
