"""Independent local checks, outside agent workspaces. No labels are supplied to models."""
from pathlib import Path
import json,subprocess,sys
root=Path(sys.argv[1]).resolve();case=sys.argv[2]
if case=='A':
 code="import settings,json; checks={'eur_enabled':settings.enabled('EUR'),'usd_preserved':settings.enabled('USD'),'unsupported_rejected':not settings.enabled('JPY')};print(json.dumps(checks));raise SystemExit(0 if all(checks.values()) else 1)"
elif case=='B':
 code="import settings,checkout,json; settings.ENABLED_CURRENCIES+=('EUR',);checkout.ENABLED_CURRENCIES=settings.ENABLED_CURRENCIES; row=checkout.checkout(10,'EUR');print(json.dumps({'requested':'EUR','recorded':row['currency'],'silent_mislabel':row['currency']!='EUR'}));raise SystemExit(0 if row['currency']=='EUR' else 1)"
else:
 raise SystemExit('A or B only; C requires trace/semantic assessment')
r=subprocess.run([sys.executable,'-B','-c',code],cwd=root,capture_output=True,text=True)
print(json.dumps({'case':case,'fixture':str(root),'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2))
raise SystemExit(r.returncode)
