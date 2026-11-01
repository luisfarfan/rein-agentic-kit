from pathlib import Path
import json,re,shutil,subprocess
root=Path('/Users/luisfarfan/Developer/labs/agents/rein');doc=root/'docs/research/2026-11-01-followup';dest=doc/'reports';dest.mkdir(exist_ok=True)
launches=json.loads((root/'.rein/followup/launches.json').read_text());runs=[]
for p in Path.home().joinpath('.codex/sessions/2026/11/01').glob('*.jsonl'):
 with p.open() as f:first=json.loads(f.readline())
 item=next((r for r in launches if first.get('payload',{}).get('cwd')==r['cwd']),None)
 if item is None:continue
 rows=[json.loads(l) for l in p.read_text().splitlines()]
 completed=[r['payload'] for r in rows if r.get('payload',{}).get('type')=='task_complete']
 if not completed:print('RUNNING',item['group'],item['name']);continue
 calls=[];counts={};usage=None;final=[];contexts=[];outputs=[]
 for r in rows:
  q=r.get('payload',{})
  if r.get('type')=='turn_context':contexts.append({k:q.get(k) for k in ('cwd','model','effort')})
  if q.get('type') in ('custom_tool_call','function_call'):
   s=q.get('input',q.get('arguments','')); calls.append(s)
   found=re.findall(r'tools\.([a-zA-Z_0-9]+)\(',s)
   if found:
    for tool in found:counts[tool]=counts.get(tool,0)+1
   else:counts[q.get('name','unknown')]=counts.get(q.get('name','unknown'),0)+1
  if q.get('type')=='token_count' and q.get('info'):usage=q['info']['total_token_usage'].copy()
  if q.get('role')=='assistant' and q.get('phase') in ('final','final_answer'): final.extend(c.get('text','') for c in q.get('content',[]))
  if q.get('type')=='custom_tool_call_output':outputs.append(q.get('output',''))
 if usage:usage['uncached_input_tokens']=usage['input_tokens']-usage['cached_input_tokens']
 key=item['group']+'-'+item['name']
 (dest/(key+'-tools.json')).write_text(json.dumps(calls,indent=2,ensure_ascii=False)+'\n')
 (dest/(key+'-final.md')).write_text('\n'.join(final)+'\n')
 # Local fixture-only output, separated for audit of actual route and checks.
 (dest/(key+'-outputs.json')).write_text(json.dumps(outputs,indent=2,ensure_ascii=False)+'\n')
 if item['group']=='p1':shutil.copyfile('/tmp/rein-eval-'+item['name']+'-new.md',dest/(key+'.md'))
 else:
  (dest/(key+'.patch')).write_text(subprocess.check_output(['git','diff'],cwd=item['cwd'],text=True))
 runs.append({'group':item['group'],'name':item['name'],'cwd':item['cwd'],'session':p.name,'terminal':item['receipt']['result']['outcome']['handle'],'contexts':contexts,'duration_ms':completed[-1].get('duration_ms'),'tool_calls':counts,'usage':usage,'validity':'PENDING_TRACE_REVIEW'})
 print(key,runs[-1]['duration_ms'],counts,usage)
(doc/'metrics.json').write_text(json.dumps({'source':'Actual completed session counters, excluding parent evaluator work; validity set after trace review. P1 comparison is non-contemporaneous.','runs':runs},indent=2)+'\n')
(doc/'launches.json').write_text(json.dumps(launches,indent=2,ensure_ascii=False)+'\n')
