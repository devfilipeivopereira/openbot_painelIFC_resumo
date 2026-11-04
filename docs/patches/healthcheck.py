#!/usr/bin/env python3
"""IFC panel/digest health check. Alerts ONLY the fixed summary recipient (Filipe), at most once per issue per day."""
import fcntl,json,os,shutil,subprocess,sys,urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
M=Path('/opt/ifc-painel/mcp');RUNS=M/'digest-runs';STATE=M/'health';STATE.mkdir(mode=0o700,exist_ok=True)
z=ZoneInfo('America/Sao_Paulo');now=datetime.now(z);day=now.date().isoformat()
issues=[]
# 1 job runner smoke test (import/regression)
try:
 r=subprocess.run(['docker','exec','ifc-mcp','python3','/app/job_runner.py','status'],capture_output=True,text=True,timeout=60)
 json.loads(r.stdout.strip().splitlines()[-1])
except Exception as e:issues.append('job_runner_status_quebrado')
# 2 containers running
for name in ('ifc-mcp','openbot','litellm'):
 r=subprocess.run(['docker','inspect','-f','{{.State.Running}}',name],capture_output=True,text=True)
 if r.stdout.strip()!='true':issues.append('container_parado:'+name)
# 3 editions failed after all retries (checked after the send window)
due={'matinal':(7,50),'tarde':(14,50),'noturno':(23,50)}
for ed,(h,mi) in due.items():
 if (now.hour,now.minute)<(h,mi):continue
 f=RUNS/f'{day}.{ed}.json'
 try:st=json.loads(f.read_text()).get('status')
 except FileNotFoundError:st='nao_rodou'
 except Exception:st='estado_corrompido'
 if st!='finished':issues.append(f'edicao_{ed}:{st}')
 suffix='' if ed=='matinal' else '.'+ed
 sent=M/'delivery'/f'{day}{suffix}.sent.json'
 if st=='finished' and not sent.exists():issues.append(f'envio_{ed}:nao_enviado')
# 4 stale lock (held > 60 min)
lock=RUNS/'schedule.lock'
if lock.exists():
 with lock.open('a+') as fh:
  try:fcntl.flock(fh,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(fh,fcntl.LOCK_UN)
  except BlockingIOError:
   age=(now.timestamp()-lock.stat().st_mtime)/60
   if age>60:issues.append('lock_preso_%dmin'%age)
# 5 panel freshness and disk
pub=Path('/opt/ifc-painel/published/dados.json')
if not pub.exists() or (now.timestamp()-pub.stat().st_mtime)>26*3600:issues.append('painel_desatualizado')
du=shutil.disk_usage('/');
if du.used/du.total>0.9:issues.append('disco_%d%%'%(100*du.used/du.total))
result={'checked_at':now.isoformat(),'issues':issues}
print(json.dumps(result,ensure_ascii=False))
(STATE/'last.json').write_text(json.dumps(result));os.chmod(STATE/'last.json',0o600)
if not issues or '--no-alert' in sys.argv:sys.exit(0)
sent_file=STATE/f'{day}.alerted.json'
try:already=set(json.loads(sent_file.read_text()))
except Exception:already=set()
new=[i for i in issues if i not in already]
if not new:sys.exit(0)
try:
 sys.path.insert(0,str(M));import whatsapp_delivery as w
 number=w.RECIPIENT.read_text().strip()
 if not (number.isdigit() and number.startswith('55')):sys.exit(2)
 key=[l.split(':',1)[1].strip() for l in w.HEADER.read_text().splitlines() if l.lower().startswith('apikey:')][0]
 text='⚠️ Painel/Resumo IFC: problema detectado ('+now.strftime('%d/%m %H:%M')+'): '+', '.join(new)+'. Detalhes em /opt/ifc-painel/mcp/health/last.json.'
 req=urllib.request.Request(w.BASE+'/message/sendText/'+quote(w.INSTANCE,safe=''),data=json.dumps({'number':number,'text':text,'linkPreview':False}).encode(),headers={'apikey':key,'Content-Type':'application/json'},method='POST')
 urllib.request.urlopen(req,timeout=30).read(1000)
 sent_file.write_text(json.dumps(sorted(already|set(new))));os.chmod(sent_file,0o600)
except Exception as e:print(json.dumps({'alert_failed':type(e).__name__}));sys.exit(1)
