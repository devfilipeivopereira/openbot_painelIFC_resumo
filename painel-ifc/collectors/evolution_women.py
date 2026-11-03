#!/usr/bin/env python3
"""Read the fixed Women IFC WhatsApp group from Evolution into private staging."""
from __future__ import annotations
import json,os,re,tempfile,time,urllib.error,urllib.request,hashlib
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import evolution_direct as audio_tools

BASE=os.environ.get('EVOLUTION_BASE_URL','').rstrip('/'); INSTANCE=os.environ.get('EVOLUTION_WOMEN_INSTANCE','')
if not BASE or not INSTANCE: raise SystemExit('evolution_women_configuration_missing')
HEADER_FILE=Path(os.environ.get('EVOLUTION_GLOBAL_HEADER_FILE','/run/secrets/evolution-global.h'))
GROUP_FILE=Path(os.environ.get('EVOLUTION_WOMEN_FILE','/run/secrets/evolution-women.json'))
OUT=Path(os.environ.get('IFC_JOB_ROOT','/var/lib/ifc-job/data'))/'staging'/'evolution_mulheres.json'
ZONE=ZoneInfo('America/Sao_Paulo'); PAGE_SIZE=200; MAX_PAGES=8
PHONE=re.compile(r'(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)')
CPF=re.compile(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b'); JID=re.compile(r'\b[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)\b',re.I)
EMAIL=re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b'); SECRET=re.compile(r'(?i)\b(?:senha|password|token|api[_ -]?key)\s*[:=]\s*\S+')
MENTION=re.compile(r'(?<!\w)@\d{10,}(?!\d)')

def read_key():
 text=HEADER_FILE.read_text(encoding='utf-8').strip()
 for line in text.splitlines():
  if ':' in line:
   name,value=line.split(':',1)
   if name.strip().casefold()=='apikey' and value.strip():return value.strip()
  if '=' in line:
   name,value=line.split('=',1)
   if name.strip().casefold()=='evolution_global_api_key' and value.strip():return value.strip()
 raise ValueError('evolution_global_key_missing')

def scrub(value,limit):
 if not isinstance(value,str):return ''
 for pattern in (SECRET,CPF,PHONE,JID,EMAIL,MENTION):value=pattern.sub('[removido]',value)
 return re.sub(r'\s+',' ',value).strip()[:limit]

def message_text(record):
 return audio_tools.message_text(record)


def is_audio(record):return audio_tools.is_audio_record(record)

def records(payload):
 if not isinstance(payload,dict):raise ValueError('evolution_response_invalid')
 value=payload.get('messages',payload.get('records',payload.get('data')))
 if isinstance(value,dict):value=value.get('records',value.get('messages',value.get('data')))
 if not isinstance(value,list) or any(not isinstance(x,dict) for x in value):raise ValueError('evolution_response_invalid')
 return value

def fetch(key,jid,page):
 body={'where':{'key':{'remoteJid':jid}},'limit':PAGE_SIZE,'page':page,'offset':(page-1)*PAGE_SIZE,'sort':'desc'}
 url=BASE+'/chat/findMessages/'+quote(INSTANCE,safe='')
 req=urllib.request.Request(url,data=json.dumps(body,separators=(',',':')).encode(),headers={'apikey':key,'Accept':'application/json','Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=20) as res:return records(json.loads(res.read(8*1024*1024)))
 except urllib.error.HTTPError as e:raise ValueError('evolution_http_'+str(e.code)) from None
 except Exception:raise ValueError('evolution_request_failed') from None

def atomic(data):
 OUT.parent.mkdir(mode=0o700,parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.evolution-women-',suffix='.tmp',dir=OUT.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,separators=(',',':'));f.write('\n');f.flush();os.fsync(f.fileno())
  os.replace(tmp,OUT);os.chmod(OUT,0o600)
 finally:
  try:os.unlink(tmp)
  except FileNotFoundError:pass

def collect():
 config=json.loads(GROUP_FILE.read_text(encoding='utf-8'))
 jid=config.get('remote_jid') if isinstance(config,dict) and config.get('version')==1 and config.get('instance')==INSTANCE else None
 if not isinstance(jid,str) or not re.fullmatch(r'[0-9]{5,}-[0-9]{5,}@g\.us',jid):raise ValueError('evolution_group_config_invalid')
 key=read_key();now=datetime.now(ZONE).replace(microsecond=0);since=now-timedelta(hours=24);since_ts=int(since.timestamp());until_ts=int(now.timestamp())
 messages=[];seen=set();fetched=0;transcript_cache=audio_tools.load_transcript_cache();audio_transcribed=0;audio_unavailable=0
 for page in range(1,MAX_PAGES+1):
  batch=fetch(key,jid,page)
  if not batch:break
  fetched+=len(batch)
  if fetched>PAGE_SIZE*MAX_PAGES:raise ValueError('evolution_pagination_cap')
  stamps=[]
  for item in batch:
   ts=item.get('messageTimestamp')
   if isinstance(ts,str) and ts.isdigit():ts=int(ts)
   if isinstance(ts,(int,float)) and ts>0:ts=int(ts//1000 if ts>10_000_000_000 else ts);stamps.append(ts)
   else:ts=None
   key_obj=item.get('key') if isinstance(item.get('key'),dict) else {}
   if key_obj.get('remoteJid')!=jid or ts is None or not since_ts<=ts<=until_ts:continue
   mid=key_obj.get('id')
   if isinstance(mid,str) and mid in seen:continue
   if isinstance(mid,str):seen.add(mid)
   text=scrub(message_text(item),1000000)
   message_type=str(item.get('messageType') or 'unknown')[:60]
   if is_audio(item):
    key_obj=item.get('key') if isinstance(item.get('key'),dict) else {};mid=key_obj.get('id')
    if not isinstance(mid,str) or not mid:raise ValueError('evolution_audio_key_incomplete')
    cache_key=hashlib.sha256((jid+'\0'+mid).encode()).hexdigest();transcript=transcript_cache.get(cache_key)
    if not isinstance(transcript,str) or not transcript.strip():
     transcript=audio_tools.transcribe_audio(key,INSTANCE,item);transcript_cache[cache_key]=transcript;audio_tools.save_transcript_cache(transcript_cache)
    text=transcript if transcript.startswith('[Áudio indisponível') else '[áudio transcrito] '+transcript;audio_transcribed+=int(not transcript.startswith('[Áudio indisponível'));audio_unavailable+=int(transcript.startswith('[Áudio indisponível'))
   elif audio_tools.document_node(item) is not None or audio_tools.is_image_record(item) or audio_tools.video_node(item) is not None:
    before=len(transcript_cache)
    text=scrub(audio_tools.document_message(key,INSTANCE,item,text,transcript_cache),1000000)
    if len(transcript_cache)!=before:audio_tools.save_transcript_cache(transcript_cache)
    if audio_tools.video_node(item) is not None and '[áudio transcrito]' in text:audio_transcribed+=1
   if not text:text='[Conteúdo removido após sanitização de dados pessoais]'
   sender=scrub(item.get('pushName'),80)
   messages.append({'at':datetime.fromtimestamp(ts,ZONE).isoformat(timespec='minutes'),'sender':sender,'type':message_type,'text':text})
  if stamps and min(stamps)<since_ts:break
  if len(batch)<PAGE_SIZE:break
  if page==MAX_PAGES:raise ValueError('evolution_pagination_cap')
  time.sleep(.08)
 messages.sort(key=lambda x:x['at'])
 atomic({'version':1,'source':'evolution_mulheres','generated_at':now.isoformat(timespec='seconds'),'window_start':since.isoformat(timespec='minutes'),'window_end':now.isoformat(timespec='minutes'),'messages':messages})
 audio_tools.save_transcript_cache(transcript_cache)
 return {'status':'ok','messages':len(messages),'audio_transcribed':audio_transcribed,'audio_untranscribed':audio_unavailable}

if __name__=='__main__':
 try:print(json.dumps(collect(),ensure_ascii=False,separators=(',',':')))
 except Exception as e:
  reason=str(e);allowed={'evolution_global_key_missing','evolution_group_config_invalid','evolution_response_invalid','evolution_request_failed','evolution_pagination_cap','evolution_audio_key_incomplete'}
  if reason.startswith('Evolution audio download returned HTTP '):reason=reason
  elif reason.startswith('LiteLLM audio transcription returned HTTP '):reason=reason
  elif not reason.startswith('evolution_http_'):reason=reason if reason in allowed else 'evolution_collection_failed'
  print(json.dumps({'status':'failed','reason':reason},ensure_ascii=False));raise SystemExit(1)
