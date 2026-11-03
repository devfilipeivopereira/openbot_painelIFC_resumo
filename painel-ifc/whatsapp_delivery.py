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
INSTANCE=os.environ.get('IFC_WHATSAPP_INSTANCE','')
BASE=os.environ.get('EVOLUTION_BASE_URL','').rstrip('/')
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
def execute(send=False,edition="matinal",test=False):
 if edition not in ("matinal","noturno"):raise ValueError("digest_edition_invalid")
 suffix="" if edition=="matinal" else ".noturno"
 ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (ROOT/'send.lock').open('a+') as lock:
  os.chmod(ROOT/'send.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX)
  now=datetime.now(ZoneInfo('America/Sao_Paulo'));day=now.date().isoformat();ledger=ROOT/(day+'.test.sent.json' if test else day+suffix+'.sent.json')
  if ledger.exists():return {'status':'already_attempted_no_retry'}
  data=json.loads((ROOT/(day+suffix+'.json')).read_text())
  generated=datetime.fromisoformat(data['generated_at']);age=(now-generated.astimezone(now.tzinfo)).total_seconds()
  if data.get('version')!=1 or data.get('status')!='prepared_not_sent' or data.get('day')!=day or not 0<=age<=10800 or data.get('edition','matinal')!=edition:raise ValueError('delivery_manifest_stale')
  text=data.get('text')
  if not isinstance(text,str) or not 1<=len(text)<=4000 or any(p.search(text) for p in PATTERNS) or not text.endswith('https://openclaw.filipeivopereira.com/ifc-painel/'):raise ValueError('delivery_text_invalid')
  if not source_snapshot_valid(data,now):raise ValueError('delivery_source_snapshot_invalid')
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
  if not send:return {'status':'ready_not_sent','chars':len(text),'day':day,'instance_connected':True}
  atomic(ledger,{'status':'attempted','at':now.isoformat(),'purpose':'test' if test else 'scheduled_digest','edition':edition})
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
  return {'status':'accepted_by_evolution','day':day,'edition':edition,'purpose':'test' if test else 'scheduled_digest'}
if __name__=='__main__':
 edition='noturno' if datetime.now(ZoneInfo('America/Sao_Paulo')).hour>=12 else 'matinal'
 is_test=sys.argv[1:]==['--test']
 try:
  if sys.argv[1:] not in ([],['--check'],['--send'],['--scheduled'],['--test']):raise ValueError('unsupported_option')
  scheduled=sys.argv[1:]==['--scheduled']
  if scheduled:
   current=datetime.now(ZoneInfo('America/Sao_Paulo'))
   if not (current.hour==7 and current.minute<=15 or current.hour==23 and 15<=current.minute<=25):raise SystemExit(0)
  result=execute(scheduled or sys.argv[1:] in (['--send'],['--test']),edition,is_test)
  result.update(edition=edition,purpose='test' if is_test else 'scheduled_digest',checked_at=datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat())
  print(json.dumps(result))
 except Exception as error:
  print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,ValueError) else type(error).__name__,
                    'edition':edition,'purpose':'test' if is_test else 'scheduled_digest',
                    'checked_at':datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()}));raise SystemExit(1)
