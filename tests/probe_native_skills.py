"""Opt-in Codex discovery smoke: no model turn, worker, or user configuration write.
Run from the REIN checkout with `python3 tests/probe_native_skills.py`.
"""
import subprocess,json,selectors,time,sys,os,tempfile
with tempfile.TemporaryDirectory(prefix='rein-codex-home-') as home:
 env={**os.environ,'CODEX_HOME':home}
 p=subprocess.Popen(['codex','app-server','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env)
 def rpc(i,method,params):
  p.stdin.write(json.dumps({'id':i,'method':method,'params':params})+'\n');p.stdin.flush()
  sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
  end=time.monotonic()+25
  try:
   while time.monotonic()<end:
    if sel.select(max(0,end-time.monotonic())):
     line=p.stdout.readline()
     if not line: raise RuntimeError('server exited')
     r=json.loads(line)
     if r.get('id')==i:
      if 'error' in r: raise RuntimeError(r['error'])
      return r['result']
   raise TimeoutError(method)
  finally:sel.close()
 try:
  rpc(1,'initialize',{'clientInfo':{'name':'rein-validation','version':'1'}})
  p.stdin.write('{"method":"initialized"}\n');p.stdin.flush()
  r=rpc(2,'skills/list',{'cwds':[os.getcwd()],'forceReload':True})
  for item in r['data']:
   skills=[{'name':s['name'],'path':s['path']} for s in item['skills'] if s['path'].startswith(os.path.join(os.getcwd(), 'plugins', 'rein') + os.sep)]
   assert {s['name'].split(':')[-1] for s in skills} == {'answer','quick','ship','rein-plan','rein-discover','rein-audit','rein-role'}, skills
   assert not item.get('errors'), item['errors']
   print(json.dumps({'skills':skills,'errors':item.get('errors',[])}))
 finally:
  p.terminate()
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:p.kill();p.wait()
