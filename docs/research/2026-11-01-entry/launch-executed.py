from pathlib import Path
import datetime,json,subprocess,sys
root=Path('/Users/luisfarfan/Developer/labs/agents/rein');base=root/'.rein/p0-live-entry';m=json.loads((base/'manifest.json').read_text());idx=int(sys.argv[1])-1;row=m['runs'][idx]
proof=json.loads((root/'docs/research/2026-11-01-entry/preflight.json').read_text());assert len(proof['packaged_skills'])==4
record=base/'launches.json';runs=json.loads(record.read_text()) if record.exists() else [];assert not any(r['path']==row['path'] for r in runs);assert len(runs)<4
sys.path.insert(0,str(root/'plugins/rein/lib'));import quota
raw=json.loads(subprocess.check_output(['orca','account','list','--json']));limits=quota.checked_limits(raw);assert not quota.brake_reason(limits,'codex'),quota.brake_reason(limits,'codex')
entry={k:row[k] for k in ('case','arm','path','model','effort','prompt')};entry['started']=datetime.datetime.now(datetime.timezone.utc).isoformat();entry['quota']={k:v for k,v in quota.snapshot(raw).items() if k in ('claude','codex')};runs.append(entry);record.write_text(json.dumps(runs,indent=2)+'\n')
result=json.loads(subprocess.check_output(['orca','agent','launch','--agent','codex','--model',row['model'],'--effort',row['effort'],'--worktree','path:'+row['path'],'--prompt',row['prompt'],'--json'],cwd=row['path']))
entry['receipt']=result;record.write_text(json.dumps(runs,indent=2)+'\n');print(row['case'],row['arm'],result['result']['outcome'])
