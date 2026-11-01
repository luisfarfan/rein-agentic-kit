from pathlib import Path
import hashlib,json,shlex,shutil,subprocess
root=Path.cwd();base=root/'.rein/p0-live-entry';base.mkdir(exist_ok=True)
plugin=Path(subprocess.check_output(['/tmp/rein-entry-installed/bin/python','-c','import pathlib,rein_kit;print(pathlib.Path(rein_kit.__file__).parent)'],text=True).strip())
assert (plugin/'ENTRY.md').read_bytes()==(root/'plugins/rein/ENTRY.md').read_bytes()
protocol={'implementation':'fde29c3','plugin':str(plugin),'runs':[]}
common='Quiero agregar soporte para euros. Trabaja sólo en este checkout local; puedes leer las skills instaladas si las necesitas. No consultes otras carpetas/casos, informes anteriores ni evaluadores. Sin red, instalaciones, workers, commits o push. Máximo 12 llamadas a herramientas y 180 segundos. Conserva las limitaciones que no puedas resolver y reporta verificaciones reales.'
for number,(case,arm,fixture,model,effort) in enumerate([('A','native','easy','gpt-6.1-sol','medium'),('A','entry','easy','gpt-6.1-sol','medium'),('B','entry','hidden','gpt-6-astra','high'),('C','entry','ambiguous','gpt-6-astra','high')],1):
 p=base/('project'+str(number));assert not p.exists()
 shutil.copytree(root/'tests/fixtures/discovery'/fixture,p,ignore=shutil.ignore_patterns('__pycache__'))
 (p/'README.md').write_text('Local Python example. No dependency installation is needed.\n'+('Run existing checks with:\n\n`python3 -B -c \'import test_currency as t; [v() for k,v in vars(t).items() if k.startswith("test_")]\'`\n' if case=='A' else 'No test runner is configured in this checkout.\n'))
 if arm=='entry':
  (p/'AGENTS.md').write_bytes((plugin/'ENTRY.md').read_bytes());(p/'CLAUDE.md').write_text('@AGENTS.md\n')
 skills=p/'.agents/skills';skills.mkdir(parents=True)
 for skill in (plugin/'skills').iterdir():
  if skill.is_dir():(skills/skill.name).symlink_to(skill,target_is_directory=True)
 subprocess.run(['git','init','-q',str(p)],check=True)
 subprocess.run(['git','add','.'],cwd=p,check=True)
 subprocess.run(['git','-c','user.name=REIN evaluation','-c','user.email=eval@localhost','commit','-qm','Isolated project baseline'],cwd=p,check=True)
 repo=json.loads(subprocess.check_output(['orca','repo','add','--path',str(p),'--json']))
 output=Path('/tmp/rein-entry-project'+str(number)+'-preflight.json')
 code="import pathlib,json,subprocess;p=pathlib.Path.cwd();assert str(p)=="+repr(str(p))+";c=json.loads(subprocess.check_output(['python3',"+repr(str(plugin/'bin/rein'))+",'context','.']));assert c['config']['root']==str(p) and not c['plan']['tasks'];assert not subprocess.check_output(['git','remote']);pathlib.Path("+repr(str(output))+").write_text(json.dumps({'cwd':str(p),'context':c,'remote':None}));print('PREFLIGHT_OK',p)"
 receipt=json.loads(subprocess.check_output(['orca','terminal','create','--worktree','path:'+str(p),'--title','Entry preflight '+str(number),'--command','python3 -c '+shlex.quote(code),'--json'],cwd=p))
 hashes={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file()}
 protocol['runs'].append({'case':case,'arm':arm,'path':str(p),'model':model,'effort':effort,'prompt':common,'repo':repo['result'],'preflight_receipt':receipt,'preflight_path':str(output),'initial_hashes':hashes})
(base/'manifest.json').write_text(json.dumps(protocol,indent=2)+'\n')
print('Four independent roots prepared; no model turns started.')
