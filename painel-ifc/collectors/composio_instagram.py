#!/usr/bin/env python3
"""Read the confirmed IFC Instagram account; no publishing or messaging calls."""
import json,os,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit
from composio_calendar import rpc,parsed_result_objects,session_id_from,scrub,KEY_FILE,ROOT
OUTPUT=ROOT/'staging'/'instagram.json'
def collect():
 key=KEY_FILE.read_text().strip()
 _,transport=rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'ifc-instagram-reader','version':'1'}}},key)
 request_id=2
 def call(name,args):
  nonlocal request_id
  request_id+=1
  result,_=rpc({'jsonrpc':'2.0','id':request_id,'method':'tools/call','params':{'name':name,'arguments':args}},key,transport)
  objects=parsed_result_objects(result)
  if result.get('error') or result.get('result',{}).get('isError') or any(o.get('successful') is False for o in objects):raise ValueError('instagram_provider_failed')
  return objects
 discovery=call('COMPOSIO_SEARCH_TOOLS',{'queries':[{'use_case':'Read the IFC Instagram profile and recent public media, read-only'}],'session':{'generate_id':True},'search_strategy':'auto'})
 sessions=[o.get('session_id') or o.get('sessionId') or (o.get('session') or {}).get('id') for o in discovery]
 session=next((s for s in sessions if isinstance(s,str)),None)
 if not session:raise ValueError('composio_session_missing')
 connections=call('COMPOSIO_MANAGE_CONNECTIONS',{'toolkits':[{'name':'instagram','action':'list'}],'session_id':session})
 accounts={o['id'] for o in connections if isinstance(o.get('id'),str) and 'account_type' in o and 'cased-brooky' in json.dumps(o).casefold() and str(o.get('status','')).lower() in {'active','connected','confirmed'}}
 if len(accounts)!=1:raise ValueError('instagram_account_ambiguous')
 account=next(iter(accounts))
 def execute(slug,args):
  return call('COMPOSIO_MULTI_EXECUTE_TOOL',{'tools':[{'tool_slug':slug,'account':account,'arguments':args}],'sync_response_to_workbench':False,'current_step':'READING_IFC_INSTAGRAM','current_step_metric':str(request_id),'thought':'Read church public captions and metadata only.','session_id':session})
 profile=execute('INSTAGRAM_GET_USER_INFO',{'fields':'id,username'})
 profiles=[o for o in profile if o.get('username')=='igrejadafamiliacrista' and o.get('id')]
 if not profiles:raise ValueError('instagram_identity_mismatch')
 user=str(profiles[0]['id']);now=datetime.now(timezone.utc);since=now-timedelta(hours=24)
 posts={};cursor=None;seen=set()
 for page in range(100):
  args={'ig_user_id':user,'fields':'id,caption,timestamp,permalink,media_type','limit':100,'since':int(since.timestamp())}
  if cursor:args['after']=cursor
  objects=execute('INSTAGRAM_GET_IG_USER_MEDIA',args)
  payloads=[o for o in objects if isinstance(o.get('data'),list)]
  if not payloads:raise ValueError('instagram_media_shape_invalid')
  payload=payloads[0]
  for row in payload['data']:
   if not isinstance(row,dict) or not row.get('id') or not row.get('timestamp'):raise ValueError('instagram_record_invalid')
   stamp=datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))
   if stamp<since:continue
   caption=str(row.get('caption') or '')
   if len(caption)>12000:raise ValueError('instagram_caption_too_large')
   link=str(row.get('permalink') or '')
   parsed_link=urlsplit(link)
   if parsed_link.scheme!='https' or parsed_link.hostname not in {'www.instagram.com','instagram.com'} or parsed_link.username or parsed_link.password:raise ValueError('instagram_permalink_invalid')
   posts[str(row['id'])]={'at':stamp.isoformat(),'text':scrub(caption,12000),'url':link,'type':str(row.get('media_type',''))}
  paging=payload.get('paging',{})
  if not paging.get('next'):break
  cursor=(paging.get('cursors') or {}).get('after')
  if not cursor or cursor in seen:raise ValueError('instagram_cursor_invalid')
  seen.add(cursor)
 else:raise ValueError('instagram_pagination_limit')
 captionless=sum(not row['text'].strip() for row in posts.values())
 limitations=[]
 if posts:limitations.append('Publicações consultadas por legenda e metadados; imagens, áudio e vídeo ainda não foram analisados.')
 if captionless:limitations.append(f'{captionless} publicação(ões) sem legenda textual disponível; conteúdo não inferido.')
 result={'generated_at':now.isoformat(),'window_start':since.isoformat(),'window_end':now.isoformat(),'coverage_24h_complete':True,'content_coverage_complete':not bool(posts),'analysis_scope':'captions_and_metadata','posts_without_caption':captionless,'limitations':limitations,'account':'igrejadafamiliacrista','messages':sorted(posts.values(),key=lambda x:x['at'],reverse=True),'content_curated':False}
 OUTPUT.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd,tmp=tempfile.mkstemp(prefix='.instagram-',dir=OUTPUT.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False)
  os.replace(tmp,OUTPUT)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 return {'status':'ok','posts':len(posts),'content_curated':False}
if __name__=='__main__':
 try:print(json.dumps(collect()))
 except Exception as error:print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,ValueError) else type(error).__name__}));raise SystemExit(1)
