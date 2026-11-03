"""Validate evidenced family commitments before writing a private daily file."""
import json,os,tempfile
import re
from datetime import datetime,timedelta,date
from zoneinfo import ZoneInfo
from pathlib import Path
from daily_artifacts import clean,STAGING
OUTPUT=Path('/var/lib/docker/volumes/openbot-data/_data/family/ifc')
BLOCKED_KEY=re.compile(r'id$|url|email|telefone|phone|jid|cpf|token|secret|password|senha|contato|recipient',re.I)
def sanitize(value):
 if isinstance(value,str):return clean(value)
 if isinstance(value,list):return [sanitize(item) for item in value]
 if isinstance(value,dict):return {key:sanitize(item) for key,item in value.items() if not BLOCKED_KEY.search(str(key))}
 return value if value is None or isinstance(value,(bool,int,float)) else None
def strings(value):
 if isinstance(value,str):yield value
 elif isinstance(value,dict):
  for item in value.values():yield from strings(item)
 elif isinstance(value,list):
  for item in value:yield from strings(item)
def context():
 today=datetime.now(ZoneInfo('America/Sao_Paulo')).date();sources={}
 for filename,field in [('agenda.json','events'),('evolution_grupos.json','messages'),('evolution_mulheres.json','messages')]:
  payload=json.loads((STAGING/filename).read_text())
  stamp=datetime.fromisoformat(payload['generated_at'].replace('Z','+00:00'))
  age=(datetime.now(stamp.tzinfo)-stamp).total_seconds()
  if stamp.tzinfo is None or not -300<=age<=86400:raise ValueError('family_source_not_current')
  for index,row in enumerate(payload.get(field,[])):
   sources[filename+':'+str(index)]=sanitize(row)
 scale=json.loads(Path('/opt/ifc-painel/box/ifc/painel/escala_domingo.json').read_text())
 for index,row in enumerate(scale['escala']):
  day=date.fromisoformat(row['data'])
  if today<=day<=today+timedelta(days=7):sources['escala:'+str(index)]=sanitize(row)
 return {'today':today.isoformat(),'through':(today+timedelta(days=7)).isoformat(),'sources':sources}
def save(payload,source_context):
 if not isinstance(payload,dict) or set(payload)!={'events','uncertain'}:raise ValueError('family_schema_invalid')
 events=payload['events'];uncertain=payload['uncertain']
 if not isinstance(events,list) or not isinstance(uncertain,list):raise ValueError('family_schema_invalid')
 today=date.fromisoformat(source_context['today']);lines=['# Compromissos da família — '+today.isoformat(),'','Somente compromissos confirmados nas fontes desta atualização.']
 ordered=[]
 for item in events:
  if not isinstance(item,dict) or set(item)!={'date','time','location','title','people','source_ref','evidence'}:raise ValueError('family_event_invalid')
  day=date.fromisoformat(item['date'])
  if not today<=day<=today+timedelta(days=7):raise ValueError('family_date_outside_window')
  source=source_context['sources'].get(item['source_ref'])
  if not source or not item['evidence'].strip() or not any(item['evidence'] in text for text in strings(source)):raise ValueError('family_evidence_missing')
  if not isinstance(item['people'],list) or not item['people'] or len(set(item['people']))!=len(item['people']) or set(item['people'])-{'Filipe','Renata','Daniel','Amanda','todos'}:raise ValueError('family_people_invalid')
  for key in ('title','location','time'):
   if not isinstance(item[key],str) or len(item[key])>600 or clean(item[key])!=item[key]:raise ValueError('family_text_invalid')
  ordered.append(item)
 for item in sorted(ordered,key=lambda x:(x['date'],x['time'])):
  lines.append('- '+item['date']+' | '+(item['time'] or 'horário não informado')+' | '+item['title']+' | '+(item['location'] or 'local não informado')+' | '+', '.join(item['people']))
 if not ordered:lines.append('Nenhum compromisso confirmado extraído desta janela; isso não comprova ausência de compromissos.')
 if uncertain:lines.append('Há '+str(len(uncertain))+' item(ns) ambíguo(s) que exigem revisão; não foram incluídos como compromissos confirmados.')
 OUTPUT.mkdir(parents=True,exist_ok=True,mode=0o700)
 for folder in (OUTPUT.parent,OUTPUT):os.chown(folder,1000,1000);os.chmod(folder,0o700)
 fd,tmp=tempfile.mkstemp(prefix='.family-',dir=OUTPUT)
 try:
  os.fchmod(fd,0o600);os.fchown(fd,1000,1000)
  with os.fdopen(fd,'w',encoding='utf-8') as out:out.write('\n'.join(lines)+'\n');out.flush();os.fsync(out.fileno())
  os.replace(tmp,OUTPUT/(today.isoformat()+'.md'))
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 return {'status':'ok','events':len(ordered),'uncertain':len(uncertain)}
