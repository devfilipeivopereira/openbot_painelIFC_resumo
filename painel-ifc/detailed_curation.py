"""Validate person-by-person curation and evidenced worship roster changes."""
import json,os,tempfile,re,unicodedata,hashlib
from datetime import date
from pathlib import Path
PATH=Path('/var/lib/docker/volumes/ifc-mcp-state/_data/data/curation/detailed.json')
ROLES={'ministro','vocais','instrumentos','transmissao','projecao','tecnicos','recepcao'}
def norm(text):return ''.join(c for c in unicodedata.normalize('NFD',str(text).lower()) if unicodedata.category(c)!='Mn')
def explicit_dates(proof):
 found=[]
 for match in re.finditer(r'(?<!\d)(\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{4})(?!\d)',proof or ''):
  raw=match.group(1)
  try:
   if '-' in raw:parsed=date.fromisoformat(raw)
   else:
    parts=re.split(r'[/.]',raw);parsed=date(int(parts[2]),int(parts[1]),int(parts[0]))
   found.append(parsed)
  except ValueError:pass
 return found
def previous_conversation_context():
 try:payload=json.loads(PATH.read_text(encoding='utf-8'))
 except FileNotFoundError:return {'generated_from':None,'conversations':[]}
 if not isinstance(payload,dict) or not isinstance(payload.get('conversations',[]),list) or not isinstance(payload.get('conversation_history',[]),list):raise ValueError('prior_conversations_invalid')
 latest={}
 for snapshot in payload.get('conversation_history',[]):
  if not isinstance(snapshot,dict) or not isinstance(snapshot.get('conversations'),list):raise ValueError('prior_conversations_invalid')
  stamp=snapshot.get('source_timestamp')
  for row in snapshot['conversations']:
   if not isinstance(row,dict) or not isinstance(row.get('person'),str):raise ValueError('prior_conversations_invalid')
   latest[row['person']]={**row,'source_timestamp':stamp}
 stamp=payload.get('source_timestamps',{}).get('mensagens_diretas')
 for row in payload.get('conversations',[]):
  if not isinstance(row,dict) or not isinstance(row.get('person'),str):raise ValueError('prior_conversations_invalid')
  latest[row['person']]={**row,'source_timestamp':stamp}
 return {'generated_from':stamp,'conversations':list(latest.values())}

def save(conversations,updates,sources,timestamps,patterns):
 direct=sources['mensagens_diretas']['records'];groups=sources['grupos']['records']
 if not isinstance(conversations,list) or not isinstance(updates,list):raise ValueError('detailed_schema_invalid')
 expected={r['person'] for r in direct};seen=set()
 for row in conversations:
  if set(row)!={'person','request','response','status','insight','record_indexes'} or row['person'] not in expected or row['person'] in seen:raise ValueError('conversation_person_invalid')
  seen.add(row['person']);indexes=row['record_indexes']
  required={i for i,r in enumerate(direct) if r['person']==row['person']}
  if not isinstance(indexes,list) or any(type(i)!=int for i in indexes) or set(indexes)!=required:raise ValueError('conversation_coverage_incomplete')
  if any(not isinstance(row[k],str) or not row[k].strip() or len(row[k])>5000 for k in ('person','request','response','status','insight')):raise ValueError('conversation_text_invalid')
 if seen!=expected:raise ValueError('conversation_people_missing')
 try:old=json.loads(PATH.read_text(encoding='utf-8'))
 except FileNotFoundError:old={}
 if not isinstance(old,dict):raise ValueError('prior_details_invalid')
 prior=old.get('worship_updates',[])
 history=old.get('conversation_history',[])
 if not isinstance(prior,list) or not isinstance(history,list):raise ValueError('prior_details_invalid')
 for saved in prior:
  if isinstance(saved,dict) and saved.get('date_confidence')!='year_unverified':
   try:saved_day=date.fromisoformat(str(saved.get('date','')))
   except ValueError:saved_day=None
   if saved_day is None or saved_day not in explicit_dates(saved.get('date_evidence','')):saved['date_confidence']='year_unverified'
 old_stamp=old.get('source_timestamps',{}).get('mensagens_diretas')
 old_conversations=old.get('conversations',[])
 if old_stamp and old_stamp!=timestamps.get('mensagens_diretas') and isinstance(old_conversations,list) and old_conversations and not any(x.get('source_timestamp')==old_stamp for x in history if isinstance(x,dict)):
  history.append({'source_timestamp':old_stamp,'conversations':old_conversations})
 for row in updates:
  if set(row)!={'date','role','person','previous_person','operation','evidence','date_evidence','record_index'}:raise ValueError('worship_schema_invalid')
  i=row['record_index']
  if type(i)!=int or not 0<=i<len(groups) or row['role'] not in ROLES or row['operation'] not in ('adicionar','remover','substituir'):raise ValueError('worship_change_invalid')
  record=groups[i];text=record.get('text','');evidence=row['evidence'];day=date.fromisoformat(row['date'])
  if not re.search(r'louvor|transmiss|projec|multimidi|tecnic|recepc',norm(record.get('group',''))):raise ValueError('worship_group_invalid')
  if not evidence or evidence not in text or not row['person'] or norm(row['person']) not in norm(evidence):raise ValueError('worship_evidence_invalid')
  proof=row['date_evidence']
  # A day/month alone is not enough: historical posts often omit the year and
  # must never be rebound to this year's or a future service by model inference.
  if not proof or proof not in text or day not in explicit_dates(proof):
   # Keep the observation for audit/history, but mark the inferred year as
   # unverified. The publisher must archive it and never merge it into a roster.
   cleaned={k:v for k,v in row.items() if k!='record_index'}
   cleaned.update(group=record.get('group'),at=record.get('at'),date_confidence='year_unverified')
   fingerprint=hashlib.sha256(json.dumps(cleaned,sort_keys=True).encode()).hexdigest()
   if not any(x.get('fingerprint')==fingerprint for x in prior):prior.append({**cleaned,'fingerprint':fingerprint})
   continue
  if row['operation']=='substituir' and (not row['previous_person'] or norm(row['previous_person']) not in norm(evidence)):raise ValueError('worship_previous_person_missing')
  cleaned={k:v for k,v in row.items() if k not in ('record_index',)};cleaned.update(group=record.get('group'),at=record.get('at'))
  fingerprint=hashlib.sha256(json.dumps(cleaned,sort_keys=True).encode()).hexdigest()
  if not any(x.get('fingerprint')==fingerprint for x in prior):prior.append({**cleaned,'fingerprint':fingerprint})
 public=[{k:v for k,v in r.items() if k!='record_indexes'} for r in conversations]
 if any(p.search(json.dumps({'conversations':public,'updates':prior},ensure_ascii=False)) for p in patterns):raise ValueError('detailed_pii_detected')
 payload={'source_timestamps':timestamps,'conversations':public,'conversation_history':history,'worship_updates':prior}
 PATH.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=PATH.parent,prefix='.detail-');os.fchmod(fd,0o600);os.fchown(fd,65534,65534)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False)
  os.replace(tmp,PATH)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 return {'people':len(public),'worship_changes':len(updates)}
