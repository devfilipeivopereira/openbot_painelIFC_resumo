#!/usr/bin/env python3
"""Deliver the authorized private daily IFC digest to its fixed recipient once per day."""
import fcntl,json,os,sys,tempfile,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
from daily_artifacts import DELIVERY_SOURCE_KEYS,PATTERNS
ROOT=Path('/opt/ifc-painel/mcp/delivery')
RECIPIENT=Path('/opt/ifc-painel/mcp/secrets/summary-recipient')
HEADER=Path('/opt/ifc-painel/mcp/secrets/evolution-global.h')
INSTANCE='Pastor Filipe'
BASE='https://evolution.filipeivopereira.com'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')
def atomic(path,data):
 fd,tmp=tempfile.mkstemp(prefix='.delivery-',dir=ROOT)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(data,out);out.flush();os.fsync(out.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def source_snapshot_valid(data,now):
 stamps=data.get('source_timestamps');support=data.get('supporting_sources')
 if data.get('source_snapshot_validated') is not True or not isinstance(stamps,dict) or not isinstance(support,dict):return False
 for source in DELIVERY_SOURCE_KEYS:
  stamp=stamps.get(source);entry=support.get(source)
  if not isinstance(stamp,str) or not isinstance(entry,dict) or entry.get('status')!='current' or entry.get('generated_at')!=stamp:return False
  try:parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
  except ValueError:return False
  if parsed.tzinfo is None:return False
  age=(now-parsed.astimezone(now.tzinfo)).total_seconds()
  if not -300<=age<=86400:return False
 return True
def execute(send=False,edition="matinal",test=False,fallback=False):
 if edition not in ("matinal","tarde","noturno"):raise ValueError("digest_edition_invalid")
 suffix="" if edition=="matinal" else "."+edition
 ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (ROOT/'send.lock').open('a+') as lock:
  os.chmod(ROOT/'send.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX)
  now=datetime.now(ZoneInfo('America/Sao_Paulo'));day=now.date().isoformat();ledger=ROOT/(day+'.test.sent.json' if test else day+suffix+'.sent.json')
  if ledger.exists():return {'status':'already_attempted_no_retry'}
  degraded=None
  try:
   data=json.loads((ROOT/(day+suffix+'.json')).read_text())
   generated=datetime.fromisoformat(data['generated_at']);age=(now-generated.astimezone(now.tzinfo)).total_seconds()
   if data.get('version')!=1 or data.get('status')!='prepared_not_sent' or data.get('day')!=day or not 0<=age<=10800 or data.get('edition','matinal')!=edition:raise ValueError('delivery_manifest_stale')
   text=data.get('text')
   if not isinstance(text,str) or not 1<=len(text)<=4000 or any(p.search(text) for p in PATTERNS) or not text.endswith('https://openclaw.filipeivopereira.com/ifc-painel/'):raise ValueError('delivery_text_invalid')
   try:
    from text_polish import polish
    polished=polish(text)
    if polished.strip() and polished.endswith('https://openclaw.filipeivopereira.com/ifc-painel/') and not any(p.search(polished) for p in PATTERNS):text=polished
   except Exception:pass
   if not source_snapshot_valid(data,now):
    if not fallback:raise ValueError('delivery_source_snapshot_invalid')
    # partial summary: send it with a short note instead of nothing
    degraded='partial_sources';body_text=text[:-len('https://openclaw.filipeivopereira.com/ifc-painel/')].rstrip()
    if body_text.endswith('📊 Painel IFC:'):body_text=body_text[:-len('📊 Painel IFC:')].rstrip()
    text=body_text+'\n\n_⚠️ Algumas fontes não atualizaram a tempo; resumo parcial._\n📊 Painel IFC: https://openclaw.filipeivopereira.com/ifc-painel/'
  except (OSError,ValueError,KeyError,TypeError) as error:
   if not fallback or degraded:raise
   degraded='fallback:'+(str(error) if isinstance(error,ValueError) else type(error).__name__)
   wd=['segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado','domingo'][now.weekday()]
   icon='☀️' if edition=='matinal' else '🌤️' if edition=='tarde' else '🌙'
   text=icon+' *Resumo do dia — '+wd+', '+now.strftime('%d/%m')+'*\n\n⚠️ O resumo completo desta edição não ficou pronto a tempo (falha na preparação). Agenda, pendências e mensagens estão no painel atualizado.\n\n📊 Painel IFC: https://openclaw.filipeivopereira.com/ifc-painel/'
  number=RECIPIENT.read_text().strip()
  if not number.isdigit() or len(number) not in (12,13) or not number.startswith('55'):raise ValueError('delivery_recipient_invalid')
  header=HEADER.read_text().strip().splitlines()
  keys=[line.split(':',1)[1].strip() for line in header if line.lower().startswith('apikey:')]
  if len(keys)!=1 or not keys[0]:raise ValueError('delivery_credential_invalid')
  opener=urllib.request.build_opener(NoRedirect)
  req=urllib.request.Request(BASE+'/instance/connectionState/'+quote(INSTANCE,safe=''),headers={'apikey':keys[0]})
  with opener.open(req,timeout=15) as response:state=json.loads(response.read(64000))
  instance=state.get('instance',state)
  if str(instance.get('state','')).lower() not in ('open','connected'):raise ValueError('delivery_instance_disconnected')
  if not send:return {'status':'ready_not_sent','chars':len(text),'day':day,'instance_connected':True,'degraded':degraded,'preview':text if degraded else None}
  atomic(ledger,{'status':'attempted','at':now.isoformat(),'purpose':'test' if test else 'scheduled_digest','edition':edition,'degraded':degraded})
  body=json.dumps({'number':number,'text':text,'linkPreview':False}).encode()
  request=urllib.request.Request(BASE+'/message/sendText/'+quote(INSTANCE,safe=''),data=body,headers={'apikey':keys[0],'Content-Type':'application/json'},method='POST')
  try:
   with opener.open(request,timeout=30) as response:result=json.loads(response.read(100000))
   message_id=(result.get('key') or {}).get('id')
   if not message_id:raise ValueError('delivery_ack_missing')
  except Exception:
   atomic(ledger,{'status':'uncertain_requires_manual_review','at':now.isoformat(),'purpose':'test' if test else 'scheduled_digest','edition':edition})
   raise ValueError('delivery_not_confirmed_no_retry') from None
  atomic(ledger,{'status':'accepted_by_evolution','at':now.isoformat(),'message_id':message_id,'purpose':'test' if test else 'scheduled_digest','edition':edition})
  return {'status':'accepted_by_evolution','day':day,'edition':edition,'purpose':'test' if test else 'scheduled_digest','degraded':degraded}
def prep_running():
 """True while daily_ifc_pipeline.py holds its lock (preparation still in progress)."""
 try:
  with open('/run/lock/ifc-daily-panel.lock','a+') as fh:
   try:fcntl.flock(fh,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(fh,fcntl.LOCK_UN);return False
   except BlockingIOError:return True
 except OSError:return False
def manifest_ready(edition,now):
 suffix="" if edition=="matinal" else "."+edition
 f=ROOT/(now.date().isoformat()+suffix+'.json')
 try:
  d=json.loads(f.read_text())
  age=(now-datetime.fromisoformat(d['generated_at']).astimezone(now.tzinfo)).total_seconds()
  return d.get('day')==now.date().isoformat() and d.get('status')=='prepared_not_sent' and d.get('edition','matinal')==edition and 0<=age<=10800
 except Exception:return False
if __name__=='__main__':
 _h=datetime.now(ZoneInfo('America/Sao_Paulo')).hour;edition='matinal' if _h<12 else 'tarde' if _h<20 else 'noturno'
 is_test=sys.argv[1:]==['--test']
 try:
  if sys.argv[1:] not in ([],['--check'],['--check-fallback'],['--send'],['--send-fallback'],['--scheduled'],['--test']):raise ValueError('unsupported_option')
  scheduled=sys.argv[1:]==['--scheduled']
  current=datetime.now(ZoneInfo('America/Sao_Paulo'))
  fb=False
  if scheduled:
   # window 00..55 of 07/14/23h; prep windows end at :55 of the previous hour
   if not (current.hour in (7,14,23) and current.minute<=55):raise SystemExit(0)
   running=prep_running()
   if not manifest_ready(edition,current):
    # artifact missing/invalid: send the fallback right away unless a preparation is still running;
    # if one is running, wait for it until minute 50 (then send the fallback, never nothing).
    fb=(not running) or current.minute>=50
    if not fb:
     print(json.dumps({'status':'waiting_preparation','edition':edition,'purpose':'scheduled_digest','checked_at':current.isoformat()}));raise SystemExit(0)
   else:
    fb=current.minute>=40
  if sys.argv[1:] in (['--check-fallback'],['--send-fallback']):fb=True
  do_send=(scheduled or sys.argv[1:] in (['--send'],['--test'],['--send-fallback']))
  result=execute(do_send,edition,is_test,fb)
  result.update(edition=edition,purpose='test' if is_test else 'scheduled_digest',checked_at=datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat())
  print(json.dumps(result))
 except SystemExit:raise
 except Exception as error:
  print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,ValueError) else type(error).__name__,
                    'edition':edition,'purpose':'test' if is_test else 'scheduled_digest',
                    'checked_at':datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()}));raise SystemExit(1)
