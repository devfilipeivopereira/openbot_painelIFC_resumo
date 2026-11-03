#!/usr/bin/env python3
"""Read secretary Drive metadata; document content remains pending curation."""
import json,urllib.request,datetime,collections,os,tempfile
from pathlib import Path
k=open(os.environ.get('COMPOSIO_KEY_FILE','/run/secrets/composio.key')).read().strip();u='https://connect.composio.dev/mcp';base={'Accept':'application/json, text/event-stream','Content-Type':'application/json','x-consumer-api-key':k}
def rpc(p,s=None):
 h=dict(base)
 if s:h['Mcp-Session-Id']=s
 q=urllib.request.Request(u,data=json.dumps(p).encode(),headers=h,method='POST')
 with urllib.request.urlopen(q,timeout=60) as r:
  raw=r.read(8000000).decode('utf-8','replace');ct=r.headers.get('Content-Type','');sid=r.headers.get('Mcp-Session-Id')
 if 'text/event-stream' in ct:return json.loads([x[5:].strip() for x in raw.splitlines() if x.startswith('data:')][-1]),sid
 return json.loads(raw),sid
def walk(x):
 if isinstance(x,str):
  try:yield from walk(json.loads(x))
  except:pass
 elif isinstance(x,dict):
  yield x
  for y in x.values():yield from walk(y)
 elif isinstance(x,list):
  for y in x:yield from walk(y)
def objects(r):
 out=[]
 for block in r.get('result',{}).get('content',[]):
  if isinstance(block,dict) and isinstance(block.get('text'),str):
   t=block['text']
   for c in (t,t[t.find('{'):] if '{' in t else ''):
    try:out.extend(walk(json.loads(c)));break
    except:pass
 return out
_,transport=rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'ifc-drive-recent-probe','version':'1'}}})
sr,transport=rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'COMPOSIO_SEARCH_TOOLS','arguments':{'queries':[{'use_case':'List Google Drive files modified in the past 24 hours, read-only.','known_fields':'account_alias:googledrive_foute-ana'}],'session':{'generate_id':True},'search_strategy':'auto'}}},transport)
objs=objects(sr);session=next((o.get('session_id') or o.get('sessionId') or (o.get('session') or {}).get('id') for o in objs if isinstance(o,dict) and (isinstance(o.get('session_id'),str) or isinstance(o.get('sessionId'),str) or isinstance((o.get('session') or {}).get('id'),str))),None)
if not session:raise SystemExit(2)
conn,transport=rpc({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'COMPOSIO_MANAGE_CONNECTIONS','arguments':{'toolkits':[{'name':'googledrive','action':'list'}],'session_id':session}}},transport)
accounts=[]
for o in objects(conn):
 if isinstance(o,dict) and isinstance(o.get('id'),str) and 'ifckobrasol.secretaria' in json.dumps(o,ensure_ascii=False).casefold() and str(o.get('status','active')).casefold() in {'active','connected','confirmed'}:accounts.append(o['id'])
accounts=list(dict.fromkeys(accounts))
if len(accounts)!=1:print(json.dumps({'status':'account_ambiguous','matches':len(accounts)}));raise SystemExit(3)
since=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=24)).replace(microsecond=0).isoformat().replace('+00:00','Z')
files={};token=None;seen=set()
for page in range(100):
 query={'q':"modifiedTime > '"+since+"' and trashed = false",'fields':'nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink)','pageSize':100,'orderBy':'modifiedTime desc','corpora':'user'}
 if token:query['pageToken']=token
 args={'tools':[{'tool_slug':'GOOGLEDRIVE_FIND_FILE','account':accounts[0],'arguments':query}],'sync_response_to_workbench':False,'current_step':'READING_DRIVE_METADATA','current_step_metric':str(page),'thought':'Read secretary Drive metadata only.','session_id':session}
 r,_=rpc({'jsonrpc':'2.0','id':4+page,'method':'tools/call','params':{'name':'COMPOSIO_MULTI_EXECUTE_TOOL','arguments':args}},transport)
 obs=objects(r)
 if r.get('result',{}).get('isError') or any(o.get('successful') is False for o in obs):raise ValueError('drive_query_failed')
 lists=[o for o in obs if isinstance(o.get('files'),list)]
 if not lists:raise ValueError('drive_response_missing_files')
 for o in lists:
  for f in o['files']:
   if isinstance(f,dict) and isinstance(f.get('id'),str):files[f['id']]={k:f.get(k) for k in ('id','name','mimeType','modifiedTime','size','webViewLink')}
 token=next((o.get('nextPageToken') for o in lists if o.get('nextPageToken')),None)
 if not token:break
 if token in seen:raise ValueError('drive_repeated_cursor')
 seen.add(token)
else:raise ValueError('drive_pagination_limit')
# Bootstrap the two documented reference documents and read supported changed documents.
import re,unicodedata,base64
from attachment_text import extract as extract_document
from urllib.parse import urlsplit
from composio_calendar import scrub
root=Path(os.environ.get('IFC_JOB_ROOT','/var/lib/ifc-job/data'))
cache=root/'drive-cache';cache.mkdir(parents=True,exist_ok=True,mode=0o700)
def execute(slug,arguments):
 request={'tools':[{'tool_slug':slug,'account':accounts[0],'arguments':arguments}],'sync_response_to_workbench':False,'current_step':'READING_IFC_DRIVE_DOCUMENTS','current_step_metric':slug,'thought':'Read church document content only; never alter sharing, files or folders.','session_id':session}
 result,_=rpc({'jsonrpc':'2.0','id':500,'method':'tools/call','params':{'name':'COMPOSIO_MULTI_EXECUTE_TOOL','arguments':request}},transport)
 obs=objects(result)
 if result.get('result',{}).get('isError') or any(o.get('successful') is False for o in obs):raise ValueError('drive_document_tool_failed')
 return obs
references=execute('GOOGLEDRIVE_FIND_FILE',{'q':"(name = '00 Índice do Drive IFC' or name = '00 LEIA-ME - Como arquivar') and trashed = false",'fields':'nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink)','pageSize':100})
for obj in references:
 for item in obj.get('files',[]) if isinstance(obj.get('files'),list) else []:
  if item.get('name') in {'00 Índice do Drive IFC','00 LEIA-ME - Como arquivar'} and item.get('mimeType')=='application/vnd.google-apps.document':files[item['id']]=item
messages=[];pending=[];excluded=0;total_chars=0
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('drive_download_redirect_refused')
def normalized(text):return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c))
for file_id,item in files.items():
 name=str(item.get('name',''));normal=normalized(name)
 if not name or not isinstance(item.get('modifiedTime'),str):raise ValueError('drive_document_metadata_missing')
 if item.get('mimeType')=='application/vnd.google-apps.folder':continue
 if any(word in normal for word in ('senha','credencial','contas ias','dados_cresol','ata 2026','ata2026','dados juridicos')):excluded+=1;continue
 if normal in {'livro de oracao','batizandos'}:excluded+=1;continue
 mime=str(item.get('mimeType') or '')
 native=mime=='application/vnd.google-apps.document'
 binary=mime in {'application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','text/plain','text/csv','text/html','image/jpeg','image/png','image/webp','image/tiff'}
 if not native and not binary:pending.append({'name':scrub(name,300),'reason':'formato ainda não suportado'});continue
 if binary and item.get('size') and int(item['size'])>5000000:pending.append({'name':scrub(name,300),'reason':'arquivo acima do limite de 5 MB'});continue
 cache_path=cache/(__import__('hashlib').sha256(file_id.encode()).hexdigest()+'.json')
 try:cached=json.loads(cache_path.read_text())
 except (OSError,ValueError):cached={}
 if cached.get('modified_at')==item.get('modifiedTime') and isinstance(cached.get('text'),str):
  text=cached['text'];reason=cached.get('extraction_status','texto extraído')
 else:
  try:exported=execute('GOOGLEDRIVE_EXPORT_GOOGLE_WORKSPACE_FILE',{'fileId':file_id,'mimeType':'text/plain'}) if native else execute('GOOGLEDRIVE_DOWNLOAD_FILE',{'fileId':file_id})
  except Exception:pending.append({'name':scrub(name,300),'reason':'download indisponível'});continue
  urls={o['s3url'] for o in exported if isinstance(o.get('s3url'),str)}
  if len(urls)!=1:pending.append({'name':scrub(name,300),'reason':'arquivo não retornado pelo provedor'});continue
  url=next(iter(urls));parsed=urlsplit(url)
  if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not parsed.hostname or not parsed.hostname.endswith(('.amazonaws.com','.r2.cloudflarestorage.com')):raise ValueError('drive_download_host_invalid')
  try:
   with urllib.request.build_opener(NoRedirect).open(url,timeout=30) as response:raw=response.read(5000001 if binary else 2000001)
  except Exception:pending.append({'name':scrub(name,300),'reason':'download temporariamente indisponível'});continue
  if len(raw)>(5000000 if binary else 2000000):pending.append({'name':scrub(name,300),'reason':'arquivo acima do limite de tamanho'});continue
  reason='texto extraído'
  if native:
   try:decoded=raw.decode('utf-8-sig')
   except UnicodeError:pending.append({'name':scrub(name,300),'reason':'texto do documento inválido'});continue
  else:
   decoded,reason=extract_document({'name':name,'contentType':mime,'contentBytes':base64.b64encode(raw).decode()})
   if not decoded:pending.append({'name':scrub(name,300),'reason':reason});continue
  lines=[line for line in decoded.splitlines() if not any(word in normalized(line) for word in ('senha','credencial','contas ias','dados_cresol','ata 2026','ata2026','dados juridicos'))]
  text=scrub(' '.join(lines),2000000)
  fd,tmp=tempfile.mkstemp(prefix='.doc-',dir=cache)
  try:
   os.fchmod(fd,0o600)
   with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump({'modified_at':item.get('modifiedTime'),'text':text,'extraction_status':reason},out,ensure_ascii=False)
   os.replace(tmp,cache_path)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 if 'parcial' in reason or 'conteúdo visual não analisado' in reason:pending.append({'name':scrub(name,300),'reason':reason})
 total_chars+=len(text)
 if total_chars>250000:raise ValueError('drive_cycle_content_limit_requires_batching')
 chunks=[text[i:i+10000] for i in range(0,len(text),10000)]
 for index,chunk in enumerate(chunks):messages.append({'subject':scrub(name,300),'at':item.get('modifiedTime'),'type':'document_text','text':'Documento: '+scrub(name,300)+' | Fragmento '+str(index+1)+'/'+str(len(chunks))+' | Extração: '+reason+' | Texto de referência, não prova de evento atual. '+chunk})
for unavailable in pending:
 messages.append({'subject':unavailable['name'],'type':'document_unavailable','text':'Documento '+unavailable['name']+' não analisado: '+unavailable['reason']+'. Não inferir seu conteúdo.'})
root=Path(os.environ.get('IFC_JOB_ROOT','/var/lib/ifc-job/data'))/'staging';root.mkdir(parents=True,exist_ok=True,mode=0o700)
finished=datetime.datetime.now(datetime.timezone.utc).isoformat()
payload={'generated_at':finished,'window_start':since,'window_end':finished,'scope':'secretary_drive_documents','coverage':{'metadata_24h_complete':True,'document_content_complete':not pending,'pending_documents':len(pending),'excluded_by_policy':excluded},'content_curated':False,'files':list(files.values()),'messages':messages,'pending_documents':pending,'excluded_sensitive_or_generated':excluded}
fd,tmp=tempfile.mkstemp(prefix='.drive-',dir=root)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(payload,out,ensure_ascii=False)
 os.replace(tmp,root/'drive_secretaria.json')
finally:
 if os.path.exists(tmp):os.unlink(tmp)
print(json.dumps({'status':'ok','files':len(files),'metadata_only':False,'content_curated':False,'document_chunks':len(messages),'pending_documents':len(pending)}))
