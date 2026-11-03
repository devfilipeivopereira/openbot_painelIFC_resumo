#!/usr/bin/env python3
"""Run each IFC edition once at its Brasília preparation time, independent of host timezone."""
import fcntl,json,os,subprocess,tempfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import re
ROOT=Path('/opt/ifc-painel/mcp')
def due_edition(now):
 fixed={(6,19):'matinal',(7,20):'painel_08',(12,30):'painel_1330',(22,15):'noturno'}.get((now.hour,now.minute))
 if fixed:return fixed
 if now.hour==6 and 25<=now.minute<=55:return 'matinal'
 if now.hour==7 and 30<=now.minute<=50:return 'painel_08'
 if now.hour==12 and 40<=now.minute<=59:return 'painel_1330'
 if now.hour==13 and now.minute<=10:return 'painel_1330'
 if now.hour==22 and 25<=now.minute<=59 or now.hour==23 and now.minute<=5:return 'noturno'
 return None
def sync_published_history():
 try:
  result=subprocess.run(['/usr/bin/python3',str(ROOT/'sync_history.py')],capture_output=True,text=True,timeout=90)
  payload=json.loads(result.stdout)
  return result.returncode==0 and payload.get('status')=='ok' and payload.get('failed')==0
 except (OSError,subprocess.TimeoutExpired,ValueError,TypeError):return False
def main():
 now=datetime.now(ZoneInfo('America/Sao_Paulo'));edition=due_edition(now)
 publication=('painel_08' if now.hour==8 else
              'painel_1330' if (now.hour==13 and now.minute>=30) or (now.hour==14 and now.minute<=29) else None)
 if not edition and not publication:return
 folder=ROOT/'digest-runs';folder.mkdir(mode=0o700,exist_ok=True)
 with (folder/'schedule.lock').open('a+') as lock:
  os.chmod(lock.name,0o600)
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  if publication:
   prepared=folder/(now.date().isoformat()+'.'+publication+'.json')
   if not prepared.exists() or json.loads(prepared.read_text()).get('status')!='finished':
    print(json.dumps({'status':'publication_blocked','reason':'preparation_not_finished','slot':publication}));return
   curation=Path('/var/lib/docker/volumes/ifc-mcp-state/_data/data/curation/ifc-curation.json')
   began=datetime.fromisoformat(json.loads(prepared.read_text())['started_at'])
   if not curation.exists() or curation.stat().st_mtime<began.timestamp():
    print(json.dumps({'status':'publication_blocked','reason':'curation_not_current','slot':publication}));return
  edition=edition or publication+'-publish'
  marker=folder/(now.date().isoformat()+'.'+edition+'.json')
  attempt=1
  if marker.exists():
   try:previous=json.loads(marker.read_text())
   except (OSError,ValueError,TypeError):return
   if publication and previous.get('status')=='published_history_pending':
    if sync_published_history():
     previous.update(status='finished',history_synced=True,finished_at=now.isoformat())
     save_state=marker.with_name('.history-retry-'+marker.name)
     save_state.write_text(json.dumps(previous));os.chmod(save_state,0o600);os.replace(save_state,marker)
    return
   if edition not in ('matinal','noturno','painel_08','painel_1330','painel_08-publish','painel_1330-publish') or previous.get('status')!='failed':return
   attempt=int(previous.get('attempt',1))+1
   if attempt>(3 if publication else 2):return
   try:last=datetime.fromisoformat(previous.get('finished_at',previous['started_at']))
   except (KeyError,ValueError,TypeError):return
   if last.tzinfo is None or (now-last).total_seconds()<300:return
  def save(value):
   fd,tmp=tempfile.mkstemp(dir=folder,prefix='.run-');os.fchmod(fd,0o600)
   with os.fdopen(fd,'w') as stream:json.dump(value,stream)
   os.replace(tmp,marker)
  save({'status':'started','edition':edition,'started_at':now.isoformat(),'window_hours':24,'attempt':attempt})
  env=dict(os.environ,IFC_DIGEST_EDITION=edition,IFC_DEFER_PANEL_PUBLICATION="1");env.pop('IFC_REUSE_CURRENT_COLLECTION',None)
  try:
   command=['docker','exec','ifc-mcp','python3','/app/job_runner.py','publish'] if publication else ['/usr/bin/python3',str(ROOT/'daily_ifc_pipeline.py')]
   run=subprocess.run(command,capture_output=True,text=True,env=env,timeout=3000)
   try:result=json.loads(run.stdout.strip().splitlines()[-1])
   except (ValueError,IndexError):result={}
   valid=run.returncode==0 and (result.get('published') is True if publication else result.get('status') in ('ok','partial_success') and result.get('summary_delivery') in ('prepared_not_sent','not_requested'))
   history_synced=sync_published_history() if publication and valid else None
   status='published_history_pending' if publication and valid and not history_synced else 'finished' if valid else 'failed'
   reason=result.get('reason') if isinstance(result.get('reason'),str) else None
   if reason and not re.fullmatch(r'[A-Za-z0-9:_-]{1,160}',reason):reason='redacted'
   if not valid and not reason:
    reason='pipeline_output_not_json' if not result else 'summary_not_prepared' if not publication else 'publish_not_confirmed'
   model_route=result.get('model_route') if result.get('model_route') in ('codex_auth','litellm_alias','unknown') else None
   primary_reason=result.get('primary_model_reason') if isinstance(result.get('primary_model_reason'),str) else None
   if primary_reason and not re.fullmatch(r'[A-Za-z0-9:_;,=.-]{1,240}',primary_reason):primary_reason='redacted'
   save({'status':status,'edition':edition,'started_at':now.isoformat(),'finished_at':datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(),'attempt':attempt,'exit_code':run.returncode,'window_hours':24,'history_synced':history_synced,'reason':reason if not valid else None,'model_route':model_route,'primary_model_reason':primary_reason})
   print(run.stdout[-2400:],flush=True)
  except Exception as error:
   save({'status':'failed','edition':edition,'started_at':now.isoformat(),'finished_at':datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(),'attempt':attempt,'reason':type(error).__name__});raise
if __name__=='__main__':main()
