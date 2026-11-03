"""Install two daily IFC editions and verify them without sending any message."""
import sys,base64,shlex
from pathlib import Path
import paramiko
cfg={}
for line in Path(sys.argv[1]).read_text(encoding='utf-8-sig').splitlines():
 if line.strip() and not line.lstrip().startswith('#') and '=' in line:
  k,v=line.split('=',1);cfg[k.strip()]=v.strip().strip('"').strip("'")
ssh=paramiko.SSHClient();ssh.load_system_host_keys();ssh.set_missing_host_key_policy(paramiko.RejectPolicy());ssh.connect(cfg['VPS_HOST'],username=cfg['VPS_USER'],password=cfg['VPS_PASSWORD'],timeout=15,allow_agent=False,look_for_keys=False)
try:
 sftp=ssh.open_sftp()
 for name,text in {'ifc-daily-panel':'# Python enforces BRT: prepare digests 06:19/22:15 and panel 07:20/12:30; publish panel 08:00/13:30.\n* * * * * root /usr/bin/python3 /opt/ifc-painel/mcp/digest_schedule.py >>/var/log/ifc-daily-panel.log 2>&1\n','ifc-whatsapp-summary':'# Python enforces 07:00 and 23:15 America/Sao_Paulo; separate edition ledgers.\n*/5 * * * * root /usr/bin/python3 /opt/ifc-painel/mcp/whatsapp_delivery.py --scheduled >>/var/log/ifc-whatsapp-summary.log 2>&1\n'}.items():
  with sftp.file('/etc/cron.d/'+name,'w') as f:f.write(text)
  sftp.chmod('/etc/cron.d/'+name,0o644)
 sftp.close()
 script=r'''
import ast,sys,json,tempfile,os
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
root=Path('/opt/ifc-painel/mcp');sys.path.insert(0,str(root))
ns={'datetime':datetime,'timedelta':timedelta}
for file,name in [('collectors/evolution_groups.py','window'),('digest_schedule.py','due_edition')]:
 tree=ast.parse((root/file).read_text(encoding='utf-8-sig'));node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name);exec(compile(ast.Module(body=[node],type_ignores=[]),file,'exec'),ns)
for hour,minute in [(7,0),(23,15),(0,0)]:
 now=datetime(2026,10,31,hour,minute,tzinfo=ZoneInfo('America/Sao_Paulo'));a,b=ns['window'](now);assert (b-a).total_seconds()==86400
assert ns['due_edition'](datetime(2026,10,31,6,19))=='matinal'
assert ns['due_edition'](datetime(2026,10,31,22,15))=='noturno'
assert ns['due_edition'](datetime(2026,10,31,7,0)) is None
import daily_artifacts as artifacts
with tempfile.TemporaryDirectory(prefix='ifc-edition-test-') as folder:
 target=Path(folder);artifacts.OUTPUT=target/'raw';artifacts.OUTPUT.mkdir()
 artifacts.Path=lambda x: target/'delivery' if x=='/opt/ifc-painel/mcp/delivery' else Path(x)
 for edition in ('matinal','noturno'):
  os.environ['IFC_DIGEST_EDITION']=edition;artifacts.prepare_delivery('Resumo de teste\nhttps://openclaw.filipeivopereira.com/ifc-painel/',{},{} )
 manifests=list((target/'delivery').glob('*.json'));assert len(manifests)==2
 assert {json.loads(p.read_text())['edition'] for p in manifests}=={'matinal','noturno'}
 assert len(list((target/'raw').glob('*.txt')))==2
# Exercise the real save_curation function in a temporary directory with a fake publisher.
import types,re
source=ast.parse((root/'mcp_gateway.py').read_text())
function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='save_curation')
fields={'temas','fatos','decisoes','pedidos','riscos','oportunidades','proximos_passos','insights'}
from datetime import timezone
with tempfile.TemporaryDirectory(prefix='ifc-publish-test-') as folder:
 calls=[]
 def fake_run(*args,**kwargs):
  calls.append(1);return types.SimpleNamespace(returncode=0,stdout=b'{"status":"ok","published":true}')
 ns={'json':json,'os':os,'tempfile':tempfile,'datetime':datetime,'timezone':timezone,'EDITORIAL_SOURCES':{'grupos':('evolution_grupos',)},'stage_payload':lambda name:{'generated_at':'test-stamp'},'CURATION_FIELDS':fields,'PII_PATTERNS':[],'CURATION_PATH':Path(folder)/'curation.json','WHATSAPP_DRAFT_PATH':Path(folder)/'draft.json','subprocess':types.SimpleNamespace(run=fake_run,DEVNULL=-1,PIPE=-1),'PYTHON':'python3','RUNNER':Path('/unused'),'JOB_ROOT':Path(folder)}
 exec(compile(ast.Module(body=[function],type_ignores=[]),'save_curation','exec'),ns)
 payload={'model':'gpt-6-luna','reasoning':'high','source_timestamps':{'evolution_grupos':'test-stamp'},'sections':{'grupos':{'sintese':'Teste de separação',**{field:[] for field in fields}}},'whatsapp_message':'Resumo de teste'}
 result=json.loads(ns['save_curation'](payload,False));assert result['status']=='ok' and result['published'] is False and not calls
 assert ns['CURATION_PATH'].exists() and ns['WHATSAPP_DRAFT_PATH'].exists()
 result=json.loads(ns['save_curation'](payload,True));assert result['published'] is True and len(calls)==1
print(json.dumps({'deferred_publication':'ok','explicit_publication':'ok'}))
print(json.dumps({'rolling_24h':'ok','schedule':'ok','separate_manifests':'ok','separate_summary_files':'ok','messages_sent':0,'cron_installed':True}))
'''
 encoded=base64.b64encode(script.encode()).decode();_,out,err=ssh.exec_command('python3 -c '+shlex.quote("import base64;exec(base64.b64decode('"+encoded+"'))"),timeout=45);print(out.read().decode());code=out.channel.recv_exit_status()
 if code:print(err.read().decode()[-1500:]);raise SystemExit(code)
finally:ssh.close()
