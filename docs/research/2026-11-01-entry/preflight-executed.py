import json,subprocess,selectors,time
from pathlib import Path
base=Path('.rein/p0-live-entry');m=json.loads((base/'manifest.json').read_text());rows=m['runs'];proof=[]
for r in rows:
 p=json.loads(Path(r['preflight_path']).read_text());assert p['cwd']==r['path'];proof.append(p)
p=subprocess.Popen(['codex','app-server','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
def rpc(i,method,params):
 p.stdin.write(json.dumps({'id':i,'method':method,'params':params})+'\n');p.stdin.flush()
 sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ);end=time.monotonic()+25
 try:
  while time.monotonic()<end:
   if sel.select(max(0,end-time.monotonic())):
    line=p.stdout.readline()
    if not line:raise RuntimeError('server exited')
    r=json.loads(line)
    if r.get('id')==i:
     if 'error' in r:raise RuntimeError(r['error'])
     return r['result']
  raise TimeoutError(method)
 finally:sel.close()
try:
 rpc(1,'initialize',{'clientInfo':{'name':'rein-entry-validation','version':'1'}})
 p.stdin.write('{"method":"initialized"}\n');p.stdin.flush()
 result=rpc(2,'skills/list',{'cwds':[r['path'] for r in rows],'forceReload':True})
 data=[]
 for item in result['data']:
  skills=[{'name':s['name'],'path':s['path']} for s in item['skills'] if str(Path(s['path']).resolve()).startswith(str(Path(m['plugin']).resolve())+'/')]
  assert len(skills)==7,skills
  assert not item.get('errors'),item['errors']
  data.append({'cwd':item['cwd'],'skills':skills,'errors':item.get('errors',[])})
 Path('docs/research/2026-11-01-entry/preflight.json').write_text(json.dumps({'shells':proof,'packaged_skills':data,'manifest':m},indent=2)+'\n')
 print('Actual Orca cwd/context + seven packaged Codex skills verified for',len(data),'projects; no model turn.')
finally:
 p.terminate()
 try:p.wait(timeout=5)
 except subprocess.TimeoutExpired:p.kill();p.wait()
