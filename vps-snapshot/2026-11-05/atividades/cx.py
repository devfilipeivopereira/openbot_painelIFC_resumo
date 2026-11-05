import json,urllib.request
KEYF='/var/lib/docker/volumes/openbot-data/_data/.config/opencode/secrets/composio.key'
U='https://connect.composio.dev/mcp'
class CX:
    def __init__(s):
        s.h={'Accept':'application/json, text/event-stream','Content-Type':'application/json','x-consumer-api-key':open(KEYF).read().strip()}
        s.n=0; s.sid=None
        _,s.sid=s.rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'atividades','version':'1'}})
        r,_=s.call('COMPOSIO_SEARCH_TOOLS',{'queries':[{'use_case':'read google calendar events, todoist completed tasks, outlook and gmail emails; write google sheets'}],'session':{'generate_id':True},'search_strategy':'auto'})
        s.session=next((o.get('session_id') for o in objs(r) if isinstance(o.get('session_id'),str)),None)
    def rpc(s,method,params):
        s.n+=1; h=dict(s.h)
        if s.sid: h['Mcp-Session-Id']=s.sid
        q=urllib.request.Request(U,data=json.dumps({'jsonrpc':'2.0','id':s.n,'method':method,'params':params}).encode(),headers=h,method='POST')
        with urllib.request.urlopen(q,timeout=120) as r:
            t=r.read(20000000).decode('utf-8','replace');ct=r.headers.get('Content-Type','');sid=r.headers.get('Mcp-Session-Id')
        if 'text/event-stream' in ct: t=[z[5:].strip() for z in t.splitlines() if z.startswith('data:')][-1]
        return json.loads(t),sid
    def call(s,name,args): return s.rpc('tools/call',{'name':name,'arguments':args})
    def accounts(s,toolkit):
        r,_=s.call('COMPOSIO_MANAGE_CONNECTIONS',{'toolkits':[{'name':toolkit,'action':'list'}],'session_id':s.session})
        out=[]
        for o in objs(r):
            if isinstance(o.get('id'),str) and isinstance(o.get('status'),str) and 'account_type' in o and o['status'].lower() in ('active','connected','confirmed'): out.append(o)
        seen=set();res=[]
        for o in out:
            if o['id'] not in seen: seen.add(o['id']);res.append(o)
        return res
    def run(s,slug,account,args):
        r,_=s.call('COMPOSIO_MULTI_EXECUTE_TOOL',{'tools':[{'tool_slug':slug,'account':account,'arguments':args}],'sync_response_to_workbench':False,'current_step':'ATIVIDADES','session_id':s.session})
        return r
    def schema(s,slugs):
        r,_=s.call('COMPOSIO_GET_TOOL_SCHEMAS',{'tool_slugs':slugs,'session_id':s.session}); return r
def walk(x):
    if isinstance(x,str):
        try: yield from walk(json.loads(x))
        except Exception: pass
    elif isinstance(x,dict):
        yield x
        for y in x.values(): yield from walk(y)
    elif isinstance(x,list):
        for y in x: yield from walk(y)
def texts(r): return [x.get('text','') for x in r.get('result',{}).get('content',[]) if isinstance(x,dict)]
def objs(r):
    out=[]
    for t in texts(r):
        for c in (t,t[t.find('{'):] if '{' in t else ''):
            try: out+=list(walk(json.loads(c)));break
            except Exception: pass
    return out
