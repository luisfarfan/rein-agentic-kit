from pathlib import Path
import json,re,subprocess,hashlib
root=Path('/Users/luisfarfan/Developer/labs/agents/rein');base=root/'.rein/p0-live-entry';doc=root/'docs/research/2026-11-01-entry';out=doc/'reports';out.mkdir(exist_ok=True)
launches=json.loads((base/'launches.json').read_text());manifest=json.loads((base/'manifest.json').read_text());entry=(root/'plugins/rein/ENTRY.md').read_text();runs=[]
for p in Path.home().joinpath('.codex/sessions/2026/11/01').glob('*.jsonl'):
 with p.open() as f:first=json.loads(f.readline())
 run=next((r for r in launches if r['path']==first.get('payload',{}).get('cwd')),None)
 if run is None:continue
 rows=[json.loads(l) for l in p.read_text().splitlines()]
 done=[r['payload'] for r in rows if r.get('payload',{}).get('type')=='task_complete']
 if not done:print('RUNNING',run['case'],run['arm']);continue
 key=run['case']+'-'+run['arm'];calls=[];counts={};usage=None;contexts=[];outputs=[];users=[]
 for r in rows:
  q=r.get('payload',{})
  if r.get('type')=='turn_context':contexts.append({k:q.get(k) for k in ('cwd','model','effort')})
  if q.get('role')=='user':users.extend(c.get('text','') for c in q.get('content',[]))
  if q.get('type') in ('custom_tool_call','function_call'):
   s=q.get('input',q.get('arguments',''));calls.append(s);found=re.findall(r'tools\.([a-zA-Z_0-9]+)\(',s)
   for tool in found or [q.get('name','unknown')]:counts[tool]=counts.get(tool,0)+1
  if q.get('type')=='custom_tool_call_output':outputs.append(q.get('output',''))
  if q.get('type')=='token_count' and q.get('info'):usage=q['info']['total_token_usage'].copy()
 if usage:usage['uncached_input_tokens']=usage['input_tokens']-usage['cached_input_tokens']
 (out/(key+'-tools.json')).write_text(json.dumps(calls,indent=2,ensure_ascii=False)+'\n')
 (out/(key+'-outputs.json')).write_text(json.dumps(outputs,indent=2,ensure_ascii=False)+'\n')
 (out/(key+'-final.md')).write_text(done[-1].get('last_agent_message','')+'\n')
 (out/(key+'.patch')).write_text(subprocess.check_output(['git','diff'],cwd=run['path'],text=True))
 startup={'exact_entry_in_user_instruction_messages':sum(entry.strip() in x for x in users),'entry_header_occurrences':sum(x.count('## REIN task entry') for x in users),'entry_sha256':hashlib.sha256(entry.encode()).hexdigest(),'agents_on_disk':(Path(run['path'])/'AGENTS.md').exists()}
 runs.append({'case':run['case'],'arm':run['arm'],'session':p.name,'terminal':run['receipt']['result']['outcome']['handle'],'contexts':contexts,'startup':startup,'duration_ms':done[-1].get('duration_ms'),'tool_calls':counts,'usage':usage,'validity':'PENDING_TRACE_REVIEW'})
 print(key,runs[-1]['duration_ms'],counts,usage,startup)
(doc/'metrics.json').write_text(json.dumps({'source':'Actual completed Codex sessions, excluding parent evaluation/coding work. Static instruction sizes are separate; reused development fixtures, not held-out evaluation.','runs':runs},indent=2)+'\n')
(doc/'launches.json').write_text(json.dumps(launches,indent=2,ensure_ascii=False)+'\n')
