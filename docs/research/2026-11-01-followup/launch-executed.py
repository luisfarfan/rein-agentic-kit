from pathlib import Path
import json,subprocess,sys,datetime,hashlib
root=Path('/Users/luisfarfan/Developer/labs/agents/rein');folder=root/'.rein/followup'
group,name=sys.argv[1:];manifest=json.loads((folder/'manifest.json').read_text());row=manifest[group][name]
proof=json.loads(Path(row['preflight']['proof_path']).read_text());assert proof['cwd']==row['path'] and proof['context']['config']['root']==row['path']
log=folder/'launches.json';runs=json.loads(log.read_text()) if log.exists() else []
assert not any(r['group']==group and r['name']==name for r in runs),'Already launched: never resend'
assert len(runs)<4
sys.path.insert(0,str(root/'plugins/rein/lib'));import quota
limits=quota.checked_limits(json.loads(subprocess.check_output(['orca','account','list','--json'])))
assert not quota.brake_reason(limits,'codex'),quota.brake_reason(limits,'codex')
prompt=row['prompt'] if group=='p1' else manifest['p2_prompt']
if group=='p1':assert hashlib.sha256(prompt.encode()).hexdigest()==row['prompt_sha256']
entry={'group':group,'name':name,'cwd':row['path'],'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'prompt':prompt,'receipt':None}
runs.append(entry);log.write_text(json.dumps(runs,indent=2)+'\n')
result=json.loads(subprocess.check_output(['orca','agent','launch','--agent','codex','--model','gpt-6.1-sol','--effort','medium','--worktree','path:'+row['path'],'--prompt',prompt,'--json'],cwd=row['path']))
entry['receipt']=result;log.write_text(json.dumps(runs,indent=2)+'\n')
print(group,name,json.dumps(result))
