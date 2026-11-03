#!/usr/bin/env python3
"""Collect public IFC YouTube metadata and Portuguese captions, never video/audio."""
import base64,html,json,os,re,subprocess,tempfile,urllib.error,urllib.request,xml.etree.ElementTree as ET
from datetime import datetime,timedelta,timezone
from pathlib import Path
from composio_calendar import scrub

CHANNEL=os.environ.get('IFC_YOUTUBE_CHANNEL_ID','')
if not CHANNEL: raise SystemExit('youtube_channel_configuration_missing')
URL='https://www.youtube.com/feeds/videos.xml?channel_id='+CHANNEL
CHANNEL_PAGE='https://www.youtube.com/channel/'+CHANNEL+'/videos'
READER_PAGE='https://r.jina.ai/'+CHANNEL_PAGE
ROOT=Path(os.environ.get('IFC_JOB_ROOT','/var/lib/ifc-job/data'))
CACHE=ROOT/'cache'/'youtube-captions.json'
COMPOSIO_COOLDOWN=CACHE.parent/'youtube-composio-cooldown.json'
NS={'a':'http://www.w3.org/2005/Atom','yt':'http://www.youtube.com/xml/schemas/2015','m':'http://search.yahoo.com/mrss/'}
MAX_ATTEMPTS=3
RETRY_HOURS=8
MAX_CAPTION_BYTES=5_000_000
MAX_TRANSCRIPT_CHARS=45_000
CAPTION_LOOKBACK=timedelta(days=30)

def parse_relative_age(label):
 match=re.search(r'(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>years?|yrs?|y|months?|mos?|mo|weeks?|wks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\s+ago\b',label,re.I)
 portuguese=re.search(r'\bhá\s+(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>anos?|meses?|m[eê]s|semanas?|dias?|horas?|minutos?|segundos?)\b',label,re.I)
 if not match and not portuguese:return None,None
 match=match or portuguese
 amount=float(match.group('n').replace(',','.'));unit=match.group('u').casefold()
 if unit.startswith(('year','yr','ano')) or unit=='y':seconds=amount*365*86400;code='y'
 elif unit.startswith(('month','mo','mes')) or unit in {'mês','mes'}:seconds=amount*30*86400;code='mo'
 elif unit.startswith(('week','wk','semana')) or unit=='w':seconds=amount*7*86400;code='w'
 elif unit.startswith(('day','dia')) or unit=='d':seconds=amount*86400;code='d'
 elif unit.startswith(('hour','hr','hora')) or unit=='h':seconds=amount*3600;code='h'
 elif unit.startswith(('minute','minuto')) or unit=='m':seconds=amount*60;code='m'
 else:seconds=amount;code='s'
 return seconds,code

def relative_label(label):
 seconds,unit=parse_relative_age(label)
 if seconds is None:return scrub(label,100)
 age_match=re.search(r'(?P<n>\d+(?:[.,]\d+)?)\s*(?:years?|yrs?|y|months?|mos?|mo|weeks?|wks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\s+ago\b',label,re.I)
 age_match=age_match or re.search(r'\bhá\s+(?P<n>\d+(?:[.,]\d+)?)\s*(?:anos?|meses?|m[eê]s|semanas?|dias?|horas?|minutos?|segundos?)\b',label,re.I)
 value=int(float(age_match.group('n')))
 names={'s':('segundo','segundos'),'m':('minuto','minutos'),'h':('hora','horas'),'d':('dia','dias'),'w':('semana','semanas'),'mo':('mês','meses'),'y':('ano','anos')}
 singular,plural=names[unit]
 return f'há {value} {singular if value==1 else plural} (aprox.)'

def parse_channel_page(body,now,tab='videos'):
 if len(body)>2_000_000:raise ValueError('youtube_page_too_large')
 text=body.decode('utf-8','replace')
 canonical='https://www.youtube.com/channel/'+CHANNEL+'/'+tab
 if canonical not in text or 'Igreja da Família Cristã' not in text:raise ValueError('youtube_channel_mismatch')
 pattern=re.compile(r'### \[(.*?)\]\(https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})[^)]*\)\s*\n\s*([^\n]{1,150})')
 rows=[];seen=set()
 for title,video_id,age_line in pattern.findall(text):
  if video_id in seen:continue
  seen.add(video_id)
  age_line=html.unescape(age_line.strip())
  age_seconds,age_unit=parse_relative_age(age_line)
  description=''
  if age_seconds is not None:
   description='Idade relativa exibida pelo canal: '+relative_label(age_line)+'. Horário exato de publicação não foi fornecido.'
  rows.append({'video_id':video_id,'data':'','titulo':scrub(html.unescape(title),500),'descricao':description,
               'published_relative':relative_label(age_line),'_relative_age_seconds':age_seconds,'_relative_unit':age_unit,
               'url':'https://www.youtube.com/watch?v='+video_id,'caption_status':'metadata_only'})
  if len(rows)>=50:break
 if not rows:raise ValueError('youtube_page_entries_missing')
 latest_seconds=rows[0].get('_relative_age_seconds')
 latest_unit=rows[0].get('_relative_unit')
 # A day-level label is rounded and cannot prove which side of the 24h edge it falls on.
 coverage=bool(latest_seconds is not None and latest_seconds>=86400 and not (latest_unit=='d' and latest_seconds<2*86400))
 cutoff=now-timedelta(hours=24)
 recent=[row for row in rows if row.get('_relative_unit') in {'s','m','h'} and row.get('_relative_age_seconds') is not None and row['_relative_age_seconds']<86400]
 return {'generated_at':now.isoformat(),'window_start':cutoff.isoformat(),'channel_id':CHANNEL,'videos':rows,
         'recent_24h':recent,'coverage_24h_complete':coverage,'metadata_source':'public_channel_page_reader',
         'limitations':'Metadados da página pública do canal via Jina Reader; horários relativos aproximados. Legendas só são usadas quando recuperáveis; vídeo e áudio não são baixados.'}

def parse(body,now):
 if len(body)>2000000 or b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():raise ValueError('youtube_feed_invalid')
 root=ET.fromstring(body)
 if root.findtext('yt:channelId',namespaces=NS) not in (CHANNEL,CHANNEL[2:]) or not any(link.get('href')=='https://www.youtube.com/channel/'+CHANNEL for link in root.findall('a:link',NS)):raise ValueError('youtube_channel_mismatch')
 rows=[]
 for e in root.findall('a:entry',NS):
  video=e.findtext('yt:videoId',namespaces=NS);owner=e.findtext('yt:channelId',namespaces=NS);title=e.findtext('a:title',default='',namespaces=NS);stamp=e.findtext('a:published',namespaces=NS)
  if not video or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video) or owner!=CHANNEL or not stamp:raise ValueError('youtube_entry_invalid')
  when=datetime.fromisoformat(stamp.replace('Z','+00:00'))
  if when.tzinfo is None:raise ValueError('youtube_timestamp_invalid')
  description=e.findtext('m:group/m:description',default='',namespaces=NS)
  if len(description)>12000:raise ValueError('youtube_description_too_large')
  rows.append({'video_id':video,'data':when.isoformat(),'titulo':scrub(title,500),'descricao':scrub(description,12000),'url':'https://www.youtube.com/watch?v='+video})
 rows.sort(key=lambda r:r['data'],reverse=True)
 cutoff=now-timedelta(hours=24)
 recent=[r for r in rows if datetime.fromisoformat(r['data'])>=cutoff]
 full_24h=len(rows)<15 or bool(rows and datetime.fromisoformat(rows[-1]['data'])<=cutoff)
 return {'generated_at':now.isoformat(),'window_start':cutoff.isoformat(),'channel_id':CHANNEL,'videos':rows,'recent_24h':recent,'coverage_24h_complete':full_24h}

def parse_vtt(raw):
 if len(raw)>MAX_CAPTION_BYTES:raise ValueError('youtube_caption_too_large')
 text=raw.decode('utf-8-sig','replace')
 if text.startswith('WEBVTT'):
  lines=text.splitlines()
 else:raise ValueError('youtube_caption_format_invalid')
 output=[];last_content=None;stamp=''
 for line in lines:
  line=line.strip()
  if not line or line=='WEBVTT' or line.startswith(('NOTE','STYLE','REGION')) or re.fullmatch(r'\d+',line):continue
  if '-->' in line:
   match=re.match(r'((?:\d{2}:)?\d{2}:\d{2})(?:\.\d+)?\s+-->',line)
   stamp=match.group(1) if match else ''
   continue
  line=re.sub(r'<[^>]*>','',line)
  line=html.unescape(line)
  line=scrub(line,2000).strip()
  if not line:continue
  normalized=re.sub(r'\s+',' ',line).casefold()
  if normalized==last_content:continue
  last_content=normalized
  output.append((f'[{stamp}] ' if stamp else '')+line)
 result=scrub(' '.join(output),MAX_TRANSCRIPT_CHARS).strip()
 return result

def load_cache():
 try:
  value=json.loads(CACHE.read_text(encoding='utf-8'))
  return value if isinstance(value,dict) and value.get('version')==1 and isinstance(value.get('videos'),dict) else {'version':1,'videos':{}}
 except (OSError,ValueError):return {'version':1,'videos':{}}

def save_cache(cache):
 CACHE.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd,tmp=tempfile.mkstemp(prefix='.youtube-captions-',dir=CACHE.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(cache,out,ensure_ascii=False,separators=(',',':'))
  os.replace(tmp,CACHE)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def composio_quota_retry_after(now=None):
 try:
  value=json.loads(COMPOSIO_COOLDOWN.read_text(encoding='utf-8'))
  until=datetime.fromisoformat(str(value.get('retry_after','')).replace('Z','+00:00'))
  moment=now or datetime.now(timezone.utc)
  return until.isoformat() if until.tzinfo is not None and until>moment else None
 except (OSError,ValueError,TypeError,AttributeError):return None

def composio_quota_cooldown_active(now=None):
 return composio_quota_retry_after(now) is not None

def set_composio_quota_cooldown(now=None):
 moment=now or datetime.now(timezone.utc)
 target=COMPOSIO_COOLDOWN;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 payload={'version':1,'reason':'provider_quota','retry_after':(moment+timedelta(hours=24)).isoformat()}
 fd,tmp=tempfile.mkstemp(prefix='.youtube-composio-cooldown-',dir=target.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(payload,out,separators=(',',':'));out.flush();os.fsync(out.fileno())
  os.replace(tmp,target);os.chmod(target,0o600)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def fetch_caption(video_id):
 """Use yt-dlp subtitle-only mode; the video itself is never downloaded."""
 temp_root=CACHE.parent/'tmp';temp_root.mkdir(parents=True,exist_ok=True,mode=0o700)
 with tempfile.TemporaryDirectory(prefix='ifc-youtube-caption-',dir=temp_root) as tmp:
  output=str(Path(tmp)/'%(id)s.%(language)s.%(ext)s')
  command=['python3','-m','yt_dlp','--skip-download','--write-subs','--write-auto-subs','--sub-langs','pt.*','--sub-format','vtt/best','--no-playlist','--no-warnings','--quiet','--no-progress','--js-runtimes','node:/usr/bin/node','--socket-timeout','18','--retries','1','--extractor-retries','1','--fragment-retries','1','--no-cache-dir','-o',output,'https://www.youtube.com/watch?v='+video_id]
  try:process=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=55,check=False,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':tmp,'PYTHONIOENCODING':'utf-8'})
  except subprocess.TimeoutExpired:return None,'attempt_timeout'
  files=sorted(Path(tmp).glob(video_id+'.*.vtt'))
  for path in files:
   try:
    size=path.stat().st_size
    if size>MAX_CAPTION_BYTES:continue
    transcript=parse_vtt(path.read_bytes())
    if transcript:
     lang=path.name[len(video_id)+1:-4]
     return {'transcript':transcript,'language':lang or 'pt','attempts':0},'available'
   except (OSError,ValueError):continue
  if not process.returncode:return None,'caption_not_available'
  diagnostic=(process.stdout+process.stderr).decode('utf-8','replace').casefold()
  if 'sign in' in diagnostic or 'confirm you are not a bot' in diagnostic or "confirm you're not a bot" in diagnostic:return None,'authentication_required'
  return None,'extractor_error'

def fetch_caption_composio(video_id):
 """Read a caption track through the verified owner account; never edit YouTube."""
 if composio_quota_cooldown_active():return None,'composio_quota_cooldown'
 account_file=CACHE.parent/'youtube-composio-account.json'
 try:
  account=json.loads(account_file.read_text(encoding='utf-8'))
  if account.get('version')!=1 or account.get('channel_id')!=CHANNEL or not isinstance(account.get('account_id'),str):
   return None,'composio_account_unavailable'
  import composio_outlook as composio
  key=composio.KEY_FILE.read_text(encoding='utf-8').strip()
  _,transport=composio.rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'ifc-youtube-caption-read','version':'1'}}},key)
  args={'queries':[{'use_case':'read available caption tracks and captions for a YouTube video, read-only'}],'session':{'generate_id':True},'search_strategy':'tool_search'}
  searched,transport=composio.rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'COMPOSIO_SEARCH_TOOLS','arguments':args}},key,transport)
  workflow=composio.workflow_session(searched)
  if not workflow:return None,'composio_session_unavailable'
  def execute(rpc_id,slug,arguments):
   request={'tools':[{'tool_slug':slug,'account':account['account_id'],'arguments':arguments}],'sync_response_to_workbench':False,'current_step':'READING_IFC_YOUTUBE_CAPTIONS','current_step_metric':'read-only','thought':'Read captions for one public IFC video. Do not modify the YouTube channel.','session_id':workflow}
   return composio.rpc({'jsonrpc':'2.0','id':rpc_id,'method':'tools/call','params':{'name':'COMPOSIO_MULTI_EXECUTE_TOOL','arguments':request}},key,transport)[0]
  listed=execute(3,'YOUTUBE_LIST_CAPTION_TRACK',{'video_id':video_id,'part':'id,snippet'})
  list_objects=composio.objects(listed)
  failures=[obj for obj in list_objects if obj.get('successful') is False]
  if listed.get('result',{}).get('isError') or listed.get('error') or failures:
   diagnostic=' '.join(str(obj.get(k,'')) for obj in failures for k in ('error','message','detail')).casefold()
   category='permission' if any(x in diagnostic for x in ('forbidden','permission','unauthorized','insufficient','scope')) else 'quota' if any(x in diagnostic for x in ('quota','rate limit','429')) else 'provider'
   if category=='quota':set_composio_quota_cooldown()
   return None,'composio_caption_list_'+category+'_error'
  tracks=[]
  for obj in list_objects:
   snippet=obj.get('snippet') if isinstance(obj,dict) else None
   if isinstance(obj.get('id'),str) and isinstance(snippet,dict):
    lang=snippet.get('language')
    if isinstance(lang,str):tracks.append((obj['id'],lang))
  tracks=list(dict.fromkeys(tracks))
  if not tracks:return None,'caption_not_available'
  track=next((x for x in tracks if x[1].casefold().startswith('pt')),tracks[0])
  loaded=execute(4,'YOUTUBE_LOAD_CAPTIONS',{'id':track[0],'tfmt':'vtt'})
  if loaded.get('result',{}).get('isError') or loaded.get('error'):
   diagnostic=json.dumps(loaded,ensure_ascii=False).casefold()
   if any(x in diagnostic for x in ('quota','rate limit','429')):
    set_composio_quota_cooldown();return None,'composio_caption_load_quota_error'
   return None,'composio_caption_load_failed'
  candidates=[]
  for obj in composio.objects(loaded):
   if isinstance(obj,str):candidates.append(obj)
   elif isinstance(obj,dict):
    for field,value in obj.items():
     if isinstance(value,str) and field.casefold() in {'caption','captions','text','transcript','body','content','response','data'}:candidates.append(value)
  for value in candidates:
   raw=value.encode('utf-8')
   if len(raw)>MAX_CAPTION_BYTES:continue
   if value.startswith('WEBVTT'):
    transcript=parse_vtt(raw)
   else:
    try:
     decoded=base64.b64decode(value,validate=True)
     transcript=parse_vtt(decoded) if decoded.startswith(b'WEBVTT') else ''
    except (ValueError,base64.binascii.Error):transcript=''
   if transcript:return {'transcript':transcript,'language':track[1],'attempts':0},'available'
  return None,'caption_not_available'
 except Exception:
  return None,'composio_caption_unavailable'

def add_captions(payload,now):
 cache=load_cache();cache_rows=cache['videos'];attempts_this_run=0
 all_rows={row['video_id']:row for row in payload['videos']}
 for row in payload['videos']:
  video_id=row['video_id']
  try:
   if row.get('data'):
    published=datetime.fromisoformat(row['data']).astimezone(timezone.utc);age=(now-published).total_seconds()
   else:age=row.get('_relative_age_seconds')
  except (TypeError,ValueError):age=None
  # Keep the editorial transcript window aligned with the ~30-day channel
  # history retained in the dashboard. The per-run cap and retry schedule
  # still bound the public caption requests.
  if not isinstance(age,(int,float)) or age<0 or age>CAPTION_LOOKBACK.total_seconds():continue
  entry=cache_rows.get(video_id,{})
  if entry.get('status')=='transcribed' and isinstance(entry.get('transcript'),str):continue
  tries=int(entry.get('attempts',0));last=entry.get('last_attempt')
  if tries>=MAX_ATTEMPTS:
   entry.update(status='unavailable_after_three_attempts')
   cache_rows[video_id]=entry;continue
  if last:
   try:
    previous=datetime.fromisoformat(last.replace('Z','+00:00'))
    retry_delay=timedelta(hours=24) if any(x in str(entry.get('last_failure','')).casefold() for x in ('quota','cooldown')) else timedelta(hours=RETRY_HOURS)
    if now-previous<retry_delay:
     entry['status']='retry_pending';cache_rows[video_id]=entry;continue
   except (TypeError,ValueError):pass
  if attempts_this_run>=5:
   entry['status']='retry_pending' if tries else 'not_attempted_capacity'
   cache_rows[video_id]=entry;continue
  attempts_this_run+=1;tries+=1
  result,reason=fetch_caption(video_id)
  if not result and reason=='authentication_required':
   result,composio_reason=fetch_caption_composio(video_id)
   if not result:reason=composio_reason
  entry.update(attempts=tries,last_attempt=now.isoformat(),last_failure=reason)
  if result:
   entry.update(status='transcribed',transcript=result['transcript'],language=result['language'],last_failure=None)
  else:entry['status']='unavailable_after_three_attempts' if tries>=MAX_ATTEMPTS else 'retry_pending'
  cache_rows[video_id]=entry
 # Drop old cache entries once they no longer appear in the bounded feed.
 for key in list(cache_rows):
  if key not in all_rows:del cache_rows[key]
 save_cache(cache)
 transcribed=0;pending=0;unavailable=0
 for row in payload['videos']:
  entry=cache_rows.get(row['video_id'],{})
  status=entry.get('status','metadata_only')
  row['caption_status']=status
  row['caption_attempts']=int(entry.get('attempts',0))
  if status=='transcribed':
   row['caption_language']=entry.get('language','pt');row['transcript']=entry.get('transcript','');transcribed+=1
  elif status in ('retry_pending','not_attempted_capacity'):pending+=1
  elif status=='unavailable_after_three_attempts':unavailable+=1
  else:row['caption_status']='metadata_only'
 payload['recent_24h']=[row for row in payload['videos'] if (
  datetime.fromisoformat(row['data'])>=datetime.fromisoformat(payload['window_start']) if row.get('data') else
  row.get('_relative_unit') in {'s','m','h'} and isinstance(row.get('_relative_age_seconds'),(int,float)) and 0<=row['_relative_age_seconds']<86400)]
 reasons={}
 for entry in cache_rows.values():
  reason=entry.get('last_failure')
  if reason in ('attempt_timeout','authentication_required','extractor_error','caption_not_available'):reasons[reason]=reasons.get(reason,0)+1
 payload['caption_coverage']={'transcribed':transcribed,'pending':pending,'unavailable_after_three_attempts':unavailable,'attempts_per_video_max':MAX_ATTEMPTS,'failure_reasons':reasons}
 retry_after=composio_quota_retry_after(now)
 if retry_after:
  payload['caption_coverage']['authenticated_fallback_status']='temporarily_paused'
  payload['caption_coverage']['authenticated_fallback_retry_after']=retry_after
  payload['limitations']=(str(payload.get('limitations',''))+' A leitura autenticada de legendas está pausada por cota até '+retry_after+'.').strip()
 if not payload.get('limitations'):
  payload['limitations']='Somente legendas públicas em português; vídeo e áudio não são baixados. Vídeos sem legenda após três tentativas ficam como metadados, sem inferir conteúdo.'
 return payload

def write_payload(payload):
 target=ROOT/'staging'/'youtube.json';target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd,tmp=tempfile.mkstemp(prefix='.youtube-',dir=target.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(payload,out,ensure_ascii=False,separators=(',',':'))
  os.replace(tmp,target)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def load_previous_payload():
 """Return only the last validated staging snapshot, without changing its capture time."""
 target=ROOT/'staging'/'youtube.json'
 try:
  value=json.loads(target.read_text(encoding='utf-8'))
 except (OSError,ValueError):return None
 if not isinstance(value,dict) or value.get('channel_id')!=CHANNEL or not isinstance(value.get('videos'),list):return None
 if not all(isinstance(row,dict) and isinstance(row.get('titulo'),str) for row in value['videos']):return None
 return value

def reuse_previous_snapshot(now,error_code):
 previous=load_previous_payload()
 if previous is None:return None
 previous['captured_at']=previous.get('captured_at') or previous.get('generated_at')
 previous['collection_status']='stale_snapshot';previous['collection_error']=error_code
 previous['collection_attempted_at']=now.isoformat();previous['generated_at']=now.isoformat()
 previous['coverage_24h_complete']=False;previous['streams_status']='unavailable';previous['recent_24h']=[]
 old_limitations=str(previous.get('limitations',''))
 old_limitations=re.sub(r'\s+(?:A leitura autenticada de legendas está pausada|Leitura autenticada de legendas pausada) por cota até \S+\.', '', old_limitations)
 previous['limitations']='A coleta atual do canal falhou; o conteúdo abaixo vem da última captura identificada e não confirma a janela atual. '+old_limitations
 retry_after=composio_quota_retry_after(now)
 if retry_after:previous['limitations']+=' Leitura autenticada de legendas pausada por cota até '+retry_after+'.'
 # Captions are retained exactly as stored in the validated snapshot; no new
 # video lookup or download is made during this outage fallback.
 counts={'transcribed':0,'pending':0,'unavailable_after_three_attempts':0}
 for row in previous['videos']:
  status=row.get('caption_status','metadata_only')
  if status=='transcribed':counts[status]+=1
  elif status in ('retry_pending','not_attempted_capacity'):counts['pending']+=1
  elif status=='unavailable_after_three_attempts':counts[status]+=1
  elif status not in ('metadata_only',):row['caption_status']='metadata_only'
 previous['caption_coverage']={**counts,'attempts_per_video_max':MAX_ATTEMPTS,'failure_reasons':{}}
 if retry_after:
  previous['caption_coverage'].update(authenticated_fallback_status='temporarily_paused',authenticated_fallback_retry_after=retry_after)
 write_payload(previous)
 return {'status':'ok','videos':len(previous['videos']),'recent':0,'coverage_24h_complete':False,'stale_snapshot':True}

def collect():
 now=datetime.now(timezone.utc)
 try:
  request=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0 IFC-Dashboard/1.0','Accept':'application/atom+xml,application/xml'})
  with urllib.request.urlopen(request,timeout=30) as r:
   if r.geturl()!=URL:raise ValueError('youtube_redirect_refused')
   body=r.read(2000001)
  payload=parse(body,now)
 except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError,ET.ParseError):
  request=urllib.request.Request(READER_PAGE,headers={'User-Agent':'Mozilla/5.0 IFC-Dashboard/1.0','Accept':'text/plain','X-No-Cache':'true'})
  try:
   with urllib.request.urlopen(request,timeout=35) as r:
    if (r.geturl().split('/')[2]!='r.jina.ai'):raise ValueError('youtube_reader_redirect_refused')
    payload=parse_channel_page(r.read(2_000_001),now)
  except urllib.error.HTTPError as e:
   result=reuse_previous_snapshot(now,'youtube_reader_http_'+str(int(e.code)))
   if result is None:raise ValueError('youtube_reader_http_'+str(int(e.code))) from None
   return result
  except (urllib.error.URLError,TimeoutError):
   result=reuse_previous_snapshot(now,'youtube_reader_unavailable')
   if result is None:raise ValueError('youtube_reader_unavailable') from None
   return result
 # Live broadcasts are the main editorial source; inspect the streams tab
 # even when the general Atom feed succeeds.
 try:
  stream_url='https://r.jina.ai/https://www.youtube.com/channel/'+CHANNEL+'/streams'
  request=urllib.request.Request(stream_url,headers={'Accept':'text/plain','X-No-Cache':'true'})
  with urllib.request.urlopen(request,timeout=35) as response:
   if response.geturl().split('/')[2]!='r.jina.ai':raise ValueError('youtube_reader_redirect_refused')
   streams=parse_channel_page(response.read(2_000_001),now,'streams')
  for row in streams['videos']:row['source_tab']='Ao vivo'
  seen={row['video_id'] for row in streams['videos']}
  payload['videos']=streams['videos']+[row for row in payload['videos'] if row['video_id'] not in seen]
  payload['recent_24h']=streams['recent_24h']+[row for row in payload['recent_24h'] if row['video_id'] not in seen]
  payload['coverage_24h_complete']=bool(payload['coverage_24h_complete'] and streams['coverage_24h_complete'])
  payload['streams_status']='succeeded'
  payload['limitations']='Aba Ao vivo priorizada, incluindo transmissões gravadas. '+streams['limitations']
 except (urllib.error.URLError,TimeoutError,ValueError):
  payload['streams_status']='unavailable'
  payload['coverage_24h_complete']=False
  payload['limitations']='A aba Ao vivo não pôde ser confirmada nesta coleta; a cobertura do canal está incompleta. '+payload.get('limitations','')
 payload=add_captions(payload,now)
 payload['captured_at']=payload.get('captured_at') or payload.get('generated_at')
 payload['collection_status']='fresh'
 for row in payload['videos']:
  row.pop('video_id',None);row.pop('_relative_age_seconds',None);row.pop('_relative_unit',None)
 write_payload(payload)
 return {'status':'ok','videos':len(payload['videos']),'recent':len(payload['recent_24h']),'coverage_24h_complete':payload['coverage_24h_complete'],'transcribed':payload['caption_coverage']['transcribed'],'caption_pending':payload['caption_coverage']['pending'],'caption_unavailable_after_three_attempts':payload['caption_coverage']['unavailable_after_three_attempts']}
if __name__=='__main__':
 try:print(json.dumps(collect()))
 except urllib.error.HTTPError as e:
  status=int(e.code)
  reason='youtube_http_'+str(status) if 400<=status<=599 else 'youtube_http_error'
  print(json.dumps({'status':'failed','reason':reason}));raise SystemExit(1)
 except urllib.error.URLError:
  print(json.dumps({'status':'failed','reason':'youtube_network_error'}));raise SystemExit(1)
 except TimeoutError:
  print(json.dumps({'status':'failed','reason':'youtube_timeout'}));raise SystemExit(1)
 except ValueError as e:
  reason=str(e)
  if not re.fullmatch(r'youtube_[a-z0-9_]{1,72}',reason):reason='youtube_validation_error'
  print(json.dumps({'status':'failed','reason':reason}));raise SystemExit(1)
 except Exception:
  print(json.dumps({'status':'failed','reason':'youtube_unexpected_error'}));raise SystemExit(1)
