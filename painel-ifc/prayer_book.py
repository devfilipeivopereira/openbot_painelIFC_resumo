"""Incremental prayer-book updates with current-message provenance."""

import copy,hashlib,json

from datetime import datetime

from pathlib import Path

from daily_artifacts import clean

LEGACY=Path('/opt/ifc-painel/box/ifc/painel/livro_oracao.json')

CURRENT=Path('/opt/ifc-painel/mcp/prayer/book.json')

def reference(row):

 return hashlib.sha256(str(row.get('id') or json.dumps(row,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:24]

def load():

 data=json.loads((CURRENT if CURRENT.exists() else LEGACY).read_text(encoding='utf-8'))

 if not isinstance(data,list) or any(not isinstance(row,dict) for row in data):raise ValueError('prayer_history_invalid')

 refs=[reference(row) for row in data]

 if len(refs)!=len(set(refs)):raise ValueError('prayer_history_duplicate_ids')

 return data

def context(rows):

 return [{'ref':reference(row),'person':clean(row.get('quem')),'request':clean(row.get('pedido')),'update':clean(row.get('atualizacao')),'resolution':clean(row.get('resolucao')),'status':clean(row.get('status')),'historical_not_reconfirmed':True} for row in rows]

def apply_updates(rows,updates,sources):

 if not isinstance(updates,list) or len(updates)>100:raise ValueError('prayer_updates_invalid')

 result=copy.deepcopy(rows);by_ref={reference(row):row for row in result};seen=set()

 for update in updates:

  if not isinstance(update,dict) or set(update)!={'action','prior_ref','source','record_index','person','text','evidence'}:raise ValueError('prayer_update_schema_invalid')

  action=update['action'];source=update['source'];index=update['record_index']

  if action not in {'new','update','resolve'} or source not in {'grupos','mulheres','mensagens_diretas'} or type(index) is not int or index<0:raise ValueError('prayer_source_invalid')

  records=sources.get(source,{}).get('records',[])

  if index>=len(records):raise ValueError('prayer_record_missing')

  record=records[index];evidence=update['evidence'];text=update['text'];person=update['person']

  if not isinstance(evidence,str) or not evidence.strip() or evidence not in str(record.get('text','')):raise ValueError('prayer_evidence_missing')

  for value,limit in ((text,1500),(person,160)):

   if not isinstance(value,str) or not value.strip() or len(value)>limit or clean(value)!=value:raise ValueError('prayer_text_invalid')

  if person.casefold() not in json.dumps(record,ensure_ascii=False).casefold():raise ValueError('prayer_person_not_in_source')

  stamp=record.get('at') or record.get('received_at')

  parsed=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))

  if parsed.tzinfo is None:raise ValueError('prayer_timestamp_invalid')

  event_id=hashlib.sha256(json.dumps([source,index,stamp,action,text,person],ensure_ascii=False).encode()).hexdigest()

  if event_id in seen:raise ValueError('prayer_duplicate_update')

  seen.add(event_id)

  if action=='new':

   if update['prior_ref']:raise ValueError('prayer_new_has_prior_reference')

   new_id='ifc-prayer-'+event_id

   if any(row.get('id')==new_id or (str(row.get('quem','')).casefold()==person.casefold() and any(p.get('source')==source and p.get('record_at')==stamp and p.get('evidence') and p['evidence'] in str(record.get('text','')) for p in row.get('provenance',[]))) for row in result):continue

   row={'id':new_id,'ts':int(parsed.timestamp()),'grupo':clean(record.get('group') or source),'quem':person,'pedido':text,'upd_ts':None,'atualizacao':'','res_ts':None,'resolucao':'','status':'Em oração','revisar':False,'upd_ids':[],'curado':True}

   result.append(row);by_ref[reference(row)]=row

  else:

   row=by_ref.get(update['prior_ref'])

   if row is None:raise ValueError('prayer_prior_reference_missing')

   if event_id in row.get('upd_ids',[]):continue

   row.setdefault('upd_ids',[]).append(event_id)

   if action=='update':row.update(upd_ts=int(parsed.timestamp()),atualizacao=text)

   else:row.update(res_ts=int(parsed.timestamp()),resolucao=text,status='Resolvido')

  row.setdefault('provenance',[]).append({'source':source,'record_at':stamp,'evidence':evidence,'event_id':event_id})

 return result



def save(rows,updates,sources):

 import os,tempfile,csv,io

 from datetime import timezone

 from zoneinfo import ZoneInfo

 result=apply_updates(rows,updates,sources)

 def write(path,text,uid):

  path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)

  fd,tmp=tempfile.mkstemp(prefix='.prayer-',dir=path.parent)

  try:

   os.fchmod(fd,0o600);os.fchown(fd,uid,uid)

   with os.fdopen(fd,'w',encoding='utf-8') as out:out.write(text);out.flush();os.fsync(out.fileno())

   os.replace(tmp,path)

  finally:

   if os.path.exists(tmp):os.unlink(tmp)

 write(CURRENT,json.dumps(result,ensure_ascii=False),0)

 def day(ts):return datetime.fromtimestamp(ts,ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y') if ts else ''

 buffer=io.StringIO();writer=csv.writer(buffer)

 writer.writerow(['Data','Grupo','Quem pediu','Pedido','Data da nova atualização','Atualização','Data da resolução','Resolução do pedido','Status'])

 for row in result:writer.writerow([day(row.get('ts')),clean(row.get('grupo')),clean(row.get('quem')),clean(row.get('pedido')),day(row.get('upd_ts')),clean(row.get('atualizacao')),day(row.get('res_ts')),clean(row.get('resolucao')),clean(row.get('status'))])

 public_rows=[{key:clean(row.get(key)) for key in ('grupo','quem','pedido','atualizacao','resolucao','status')}|{'data':day(row.get('ts')),'data_atualizacao':day(row.get('upd_ts')),'data_resolucao':day(row.get('res_ts'))} for row in result]

 root=Path('/var/lib/docker/volumes/openbot-data/_data/ifc/painel')

 write(root/'livro_oracao.csv',buffer.getvalue(),1000)

 stamps={value['source_key']:value['generated_at'] for key,value in sources.items() if key in {'grupos','mulheres','mensagens_diretas'}}

 payload={'generated_at':datetime.now(timezone.utc).isoformat(),'source_timestamps':stamps,'historical_records':len(rows),'validated_updates':len(updates),'pedidos':public_rows}

 write(Path('/var/lib/docker/volumes/ifc-mcp-state/_data/data/curation/prayer-book.json'),json.dumps(payload,ensure_ascii=False),65534)

 write(root/'livro_oracao_manifest.json',json.dumps({k:v for k,v in payload.items() if k!='pedidos'},ensure_ascii=False),1000)

 return {'status':'ok','records':len(result),'validated_updates':len(updates)}
