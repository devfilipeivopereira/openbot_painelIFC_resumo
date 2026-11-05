#!/usr/bin/env python3
"""Planilha de Atividades — Filipe. Extrai, classifica, grava em Supabase (atividades.*) e sincroniza Google Sheet.
Uso: atividades.py run --start YYYY-MM-DD --end YYYY-MM-DD [--no-sync]   |  atividades.py sync  | atividades.py daily"""
import json, re, sys, time, hashlib, subprocess, datetime as dt, urllib.request, argparse, os
from zoneinfo import ZoneInfo
sys.path.insert(0, '/opt/atividades')
TZ = ZoneInfo('America/Sao_Paulo'); BASE = '/opt/atividades'
EVO = 'postgres_postgres.1.d1t42at7y4mexdjx4mt58hlh7'
def ctr(prefix):
    out = subprocess.run(['docker', 'ps', '--format', '{{.Names}}'], capture_output=True, text=True).stdout.split()
    return next(n for n in out if n.startswith(prefix))
INST = ('Filipe', 'Filipe_2', 'Pastor Filipe')
STATS = {'llm_calls': 0, 'tok_in': 0, 'tok_out': 0}
def log(*a): print(dt.datetime.now(TZ).strftime('%d/%m %H:%M:%S'), *a, flush=True)

# ---------- db ----------
def evo(sql):
    q = f"copy (select row_to_json(t) from ({sql}) t) to stdout"
    r = subprocess.run(['docker', 'exec', '-i', ctr('postgres_postgres'), 'psql', '-U', 'postgres', '-d', 'evolution', '-At', '-v', 'ON_ERROR_STOP=1'], input=q, capture_output=True, text=True)
    if r.returncode: raise RuntimeError('evo:' + r.stderr[:300])
    return [json.loads(l.replace('\\\\', '\\')) for l in r.stdout.splitlines() if l.strip()]
def sb(sql, rows=False):
    if rows: sql = f"copy (select row_to_json(t) from ({sql}) t) to stdout"
    r = subprocess.run(['docker', 'exec', '-i', ctr('supabase_supabase_db'), 'psql', '-U', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1', '-q'], input=sql, capture_output=True, text=True)
    if r.returncode: raise RuntimeError('sb:' + r.stderr[:300])
    return [json.loads(l.replace('\\\\', '\\')) for l in r.stdout.splitlines() if l.strip()] if rows else r.stdout
def lit(v):
    tag = '$q' + hashlib.md5(json.dumps(v).encode()).hexdigest()[:8] + '$'
    return tag + json.dumps(v, ensure_ascii=False) + tag
COLS = ['id','fonte','id_origem','data','hora','tipo','direcao','com_quem','contato_key','resumo','area','duracao_min','status','pedido','pendente_desde','classificador','instancia','n_in','n_out']
def upsert(items):
    if not items: return 0
    for i in range(0, len(items), 500):
        ch = [{k: it.get(k) for k in COLS} for it in items[i:i+500]]
        upd = ','.join(f"{c}=excluded.{c}" for c in COLS if c not in ('id',)) + ',atualizado=now()'
        sb(f"insert into atividades.atividade({','.join(COLS)}) select {','.join(COLS)} from json_populate_recordset(null::atividades.atividade, {lit(ch)}::json) on conflict (id) do update set {upd};")
    return len(items)
def sid(fonte, origem): return hashlib.sha1(f'{fonte}|{origem}'.encode()).hexdigest()

# ---------- masking ----------
CPF = re.compile(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b')
PHONE = re.compile(r'\+?\d[\d\s().-]{7,}\d')
EMAIL = re.compile(r'([\w.+-]{2})[\w.+-]*@([\w-]+\.[\w.]+)')
def mask(t):
    t = CPF.sub('[cpf]', t or '')
    t = PHONE.sub(lambda m: '****' + re.sub(r'\D', '', m.group())[-4:], t)
    t = EMAIL.sub(r'\1***@\2', t)
    return re.sub(r'\s+', ' ', t).strip()
def mask_jid(j): return 'Contato ****' + re.sub(r'\D', '', j.split('@')[0])[-4:]

# ---------- rules ----------
FAM_NAME = re.compile(r'\b(renata|amanda|daniel(?! huff)|ivo|m[ãa]e|pai|v[óoô]|sogr[oa]|cunhad[oa]|irm[ãa]o?s?|pereira|fam[ií]lia|amor|esposa|filh[oa])\b', re.I)
IGR_NAME = re.compile(r'(ifc|igreja|c[ée]lula|louvor|culto|pastor|\bpr\.|discip|ora[çc][ãa]o|b[íi]blic|minist[ée]rio|\bmin\.|yfc|legend[áa]ri|lgnd|casais|deep fire|esfera|despertar|\bdna\b|avivamento|miss[ãa]o|mission|semin[áa]rio|adora[çc][ãa]o|guerreiro|secretaria|batismo|jovens|intercess|esdras|vineyard|church|congrega|devocional|evang)', re.I)
PROMO = re.compile(r'(promo[çc][ãa]o|desconto|cupom|oferta|frete gr[áa]tis|sorteio|black ?friday|aproveite|imperd[íi]vel|clique|inscreva|garanta (j[áa]|sua)|% ?off|liquida[çc][ãa]o|compre|parcel|link na bio|últimas vagas|ultimas vagas|vagas limitadas|cashback|empr[ée]stimo|cr[ée]dito pr[ée]|investimento garantido|renda extra|ganhe|pix premiado|seu pedido|c[óo]digo de verifica|c[óo]digo de seguran|your code|n[ãa]o responda|mensagem autom[áa]tica|unsubscribe|descadastr)', re.I)
BOTNAME = re.compile(r'(bot\b|assistente virtual|notifica|no-?reply|atendimento autom|delivery|ifood|banco|bradesco|ita[úu]|nubank|santander|caixa|claro|vivo|tim\b|oi\b|magalu|mercado livre|shopee|amazon|loja|store|oficial)', re.I)
GREET = re.compile(r'^\W*(bom dia|boa tarde|boa noite|ol[áa]|oi+|oie|e a[íi]|tudo bem\??|am[ée]m|gl[óo]ria|obrigad[oa]|valeu|ok|blz|beleza|kk+|rs+|top|show|👍|🙏|❤️|😊|😂|\W)*\W*$', re.I)
PEDIDO = re.compile(r'(\?|\bpode\b|\bpoderia|consegue|preciso|precisa|por favor|\bpfv|\bpf\b|me ajuda|ajuda|manda|envia|enviar|confirma|lembr|ora por|orar|ora[çc][ãa]o por|quando|qual|onde|quanto|marcar|agendar|reuni[ãa]o|liga|ligar|me chama|responde|aguardo)', re.I)
KEEP_T = {'conversation','extendedTextMessage','imageMessage','videoMessage','audioMessage','documentMessage','documentWithCaptionMessage','contactMessage','contactsArrayMessage','locationMessage','liveLocationMessage','albumMessage','ptvMessage'}
BIZ_T = {'templateMessage','buttonsMessage','listMessage','interactiveMessage','buttonsResponseMessage','listResponseMessage','highlyStructuredMessage','orderMessage','productMessage'}
LINK = re.compile(r'https?://|www\.|\.com\b|bit\.ly|wa\.me', re.I)

# ---------- LLM ----------
_LK = None
def llm_key():
    global _LK
    if not _LK:
        var = re.search(r'master_key:\s*os\.environ/(\w+)', open('/opt/ai-proxy/config.yaml').read()).group(1)
        _LK = subprocess.run(['docker', 'exec', 'litellm', 'printenv', var], capture_output=True, text=True).stdout.strip()
    return _LK
def llm(system, user, motivo):
    body = {'model': 'fast', 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], 'temperature': 0, 'response_format': {'type': 'json_object'}}
    for att in range(5):
        try:
            q = urllib.request.Request('http://172.18.0.1:4000/v1/chat/completions', data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + llm_key(), 'Content-Type': 'application/json'})
            with urllib.request.urlopen(q, timeout=120) as r: d = json.loads(r.read())
            u = d.get('usage') or {}
            STATS['llm_calls'] += 1; STATS['tok_in'] += u.get('prompt_tokens', 0); STATS['tok_out'] += u.get('completion_tokens', 0)
            sb(f"insert into atividades.uso_llm(modelo,chamadas,tokens_in,tokens_out,motivo) values ('fast',1,{int(u.get('prompt_tokens',0))},{int(u.get('completion_tokens',0))},{lit(motivo)});")
            txt = d['choices'][0]['message']['content']
            txt = txt[txt.find('{'):txt.rfind('}')+1]
            time.sleep(2.5)
            return json.loads(txt)
        except Exception as e:
            log('llm retry', att, type(e).__name__); time.sleep(10 * (att + 1))
    return {}
SYS_CONT = ("Você classifica contatos/conversas do Pastor Filipe (pastor da Igreja da Família Cristã - IFC, Kobrasol/São José SC). "
 "Para cada item devolva área: 'Igreja' (assuntos da igreja, membros, ministério, células, pastores, eventos cristãos), 'Família' (esposa Renata, filhos Daniel e Amanda, pais, irmãos, parentes, casa, escola dos filhos), "
 "'Pessoal' (trabalho próprio, estudos, finanças pessoais, fornecedores, serviços, amigos) ou 'Ruído' (propaganda, marketing, bot, notificação automática, spam, corrente, golpe). "
 "Responda JSON {\"r\":[{\"k\":<id>,\"a\":<área>}]}.")
SYS_PED = ("Você analisa mensagens recebidas pelo Pastor Filipe no WhatsApp num dia. Para cada item diga se há um PEDIDO real dirigido a ele (pedido de ação, resposta, oração, agenda, informação, ajuda) "
 "e resuma em até 12 palavras em pt-BR, sem nomes de terceiros sensíveis, sem números. Se for propaganda/bot/corrente marque ruido=true. "
 "Responda JSON {\"r\":[{\"k\":<id>,\"p\":true|false,\"ruido\":true|false,\"s\":\"resumo\"}]}.")
def classify_contacts(cands):
    """cands: dict key->{'nome','amostra'} ; returns key->area"""
    out = {}
    keys = list(cands)
    for i in range(0, len(keys), 40):
        ch = keys[i:i+40]
        payload = [{'k': n, 'nome': mask(cands[k]['nome'] or '?')[:60], 'msgs': [mask(x)[:120] for x in cands[k]['amostra'][:3]]} for n, k in enumerate(ch)]
        r = llm(SYS_CONT, json.dumps(payload, ensure_ascii=False), 'area_contato')
        for x in r.get('r', []) if isinstance(r, dict) else []:
            try: out[ch[int(x['k'])]] = {'igreja': 'Igreja', 'família': 'Família', 'familia': 'Família', 'pessoal': 'Pessoal', 'ruído': 'Ruído', 'ruido': 'Ruído'}.get(str(x['a']).lower(), 'Pessoal')
            except Exception: pass
    return out

# ---------- WhatsApp ----------
class WA:
    def __init__(s, start, end):
        a = int(dt.datetime.combine(start - dt.timedelta(days=1), dt.time(), TZ).timestamp()); b = int(dt.datetime.combine(end + dt.timedelta(days=4), dt.time(), TZ).timestamp())
        inst = ','.join(f"'{x}'" for x in INST)
        s.owner = {r['name']: r['o'] for r in evo('select name, "ownerJid" o from "Instance"')}
        s.self = {s.owner.get(x) for x in INST} | {None}
        s.daniel = s.owner.get('Daniel')
        s.lid = {}
        for r in evo(f"""select distinct key->>'remoteJid' j, key->>'remoteJidAlt' a from "Message" m join "Instance" i on i.id=m."instanceId" where i.name in ({inst}) and key->>'remoteJid' like '%@lid' and key->>'remoteJidAlt' like '%@s.whatsapp.net'"""):
            s.lid[r['j']] = r['a']
        s.names = {}
        for r in evo(f"""select c."remoteJid" j, c."pushName" n from "Contact" c join "Instance" i on i.id=c."instanceId" where i.name in ({inst}) and c."pushName" is not null and c."pushName"<>''"""):
            s.names[s.norm(r['j'])] = r['n']
        for r in evo(f"""select c."remoteJid" j, c.name n from "Chat" c join "Instance" i on i.id=c."instanceId" where i.name in ({inst}) and c.name is not null and c.name<>''"""):
            s.names.setdefault(s.norm(r['j']), r['n'])
        # contatos com quem Filipe já falou (enviou) — último ano
        s.known = {s.norm(r['j']) for r in evo(f"""select distinct key->>'remoteJid' j from "Message" m join "Instance" i on i.id=m."instanceId" where i.name in ({inst}) and (key->>'fromMe')='true' and m."messageTimestamp">extract(epoch from now()-interval '365 days')""")}
        # saídas por contato (para status respondido)
        s.out_ts = {}
        for r in evo(f"""select key->>'remoteJid' j, m."messageTimestamp"::bigint ts from "Message" m join "Instance" i on i.id=m."instanceId" where i.name in ({inst}) and (key->>'fromMe')='true' and m."messageTimestamp" between {a} and {b} and key->>'remoteJid' not like '%@g.us'"""):
            s.out_ts.setdefault(s.norm(r['j']), []).append(int(r['ts']))
        fam = json.load(open(BASE + '/family_jids.json')) if os.path.exists(BASE + '/family_jids.json') else {}
        s.family = {s.norm(j) for v in fam.values() for j in v} | ({s.norm(s.daniel)} if s.daniel else set())
        s.igr_members = set()
    def norm(s, j):
        if not j: return j
        return s.lid.get(j, j)
    def day(s, d):
        a = int(dt.datetime.combine(d, dt.time(), TZ).timestamp()); b = a + 86400
        inst = ','.join(f"'{x}'" for x in INST)
        rows = evo(f"""select i.name inst, m.key->>'id' kid, m.key->>'remoteJid' jid, m.key->>'remoteJidAlt' alt, (m.key->>'fromMe')='true' fm,
          coalesce(nullif(m.key->>'participant',''), m.participant) part, m."pushName" pn, m."messageType" mt, m."messageTimestamp"::bigint ts,
          left(coalesce(m.message->>'conversation', m.message->'extendedTextMessage'->>'text', m.message->'imageMessage'->>'caption', m.message->'videoMessage'->>'caption', m.message->'documentMessage'->>'fileName', ''),400) tx,
          coalesce(m."contextInfo"->>'isForwarded', m.message->'extendedTextMessage'->'contextInfo'->>'isForwarded', 'false')='true' fwd,
          coalesce(m."contextInfo"->>'forwardingScore', m.message->'extendedTextMessage'->'contextInfo'->>'forwardingScore', '0') fs
          from "Message" m join "Instance" i on i.id=m."instanceId" where i.name in ({inst}) and m."messageTimestamp" >= {a} and m."messageTimestamp" < {b}""")
        seen = set(); conv = {}; noise = {'tipo': 0, 'fwd': 0, 'promo': 0, 'link_desconhecido': 0, 'saudacao': 0, 'bot': 0, 'broadcast': 0}
        for r in sorted(rows, key=lambda r: (r['inst'] != 'Pastor Filipe', r['inst'] != 'Filipe')):
            if r['kid'] in seen: continue
            seen.add(r['kid'])
            j = r['jid'] or ''
            if r['alt'] and r['alt'].endswith('@s.whatsapp.net') and j.endswith('@lid'): s.lid[j] = r['alt']
            if 'broadcast' in j or 'newsletter' in j or j.startswith('status'): noise['broadcast'] += 1; continue
            k = s.norm(j)
            if k in s.self: continue
            grp = j.endswith('@g.us')
            c = conv.setdefault(k, {'grp': grp, 'inst': set(), 'msgs': [], 'biz': False, 'pn': None})
            c['inst'].add(r['inst'])
            if not r['fm'] and r['pn'] and not grp: c['pn'] = r['pn']
            if r['mt'] in BIZ_T: c['biz'] = True; continue
            if r['mt'] not in KEEP_T: noise['tipo'] += 1; continue
            fs = int(r['fs'] or 0) if str(r['fs']).isdigit() else 0
            if not r['fm'] and (r['fwd'] or fs >= 5): noise['fwd'] += 1; continue
            if grp and not r['fm'] and r['part']: s.igr_members.add((k, s.norm(r['part'])))
            tx = r['tx'] or {'audioMessage': '[áudio]', 'imageMessage': '[imagem]', 'videoMessage': '[vídeo]', 'documentMessage': '[documento]', 'contactMessage': '[contato]', 'locationMessage': '[localização]'}.get(r['mt'], '')
            c['msgs'].append({'fm': r['fm'], 'ts': int(r['ts']), 'tx': tx})
        items = []
        for k, c in conv.items():
            ms = c['msgs']; n_out = sum(m['fm'] for m in ms); n_in = len(ms) - n_out
            if not ms: continue
            if c['grp'] and n_out == 0: continue  # grupos só quando Filipe participou
            nome = s.names.get(k) or c['pn'] or ''
            inbound = [m for m in ms if not m['fm']]
            if not c['grp'] and n_out == 0:
                txt = ' '.join(m['tx'] for m in inbound)
                if c['biz'] or BOTNAME.search(nome) and k not in s.known: noise['bot'] += 1; continue
                if PROMO.search(txt): noise['promo'] += 1; continue
                if LINK.search(txt) and k not in s.known: noise['link_desconhecido'] += 1; continue
                if all(GREET.match(m['tx'] or '') and not m['tx'].startswith('[') for m in inbound): noise['saudacao'] += 1; continue
            ped_msgs = [m for m in inbound if PEDIDO.search(m['tx'] or '') or m['tx'] == '[áudio]'] if not c['grp'] else []
            first = min(m['ts'] for m in ms)
            it = {'fonte': 'whatsapp', 'id_origem': f"{k}|{d.isoformat()}", 'data': d.isoformat(), 'hora': dt.datetime.fromtimestamp(first, TZ).strftime('%H:%M'),
                  'tipo': 'conversa (grupo)' if c['grp'] else 'conversa', 'direcao': 'enviada' if n_in == 0 else ('recebida' if n_out == 0 else 'ambas'),
                  'com_quem': mask(nome)[:80] if nome else mask_jid(k), 'contato_key': hashlib.sha1(k.encode()).hexdigest()[:16], '_k': k, '_nome': nome,
                  'n_in': n_in, 'n_out': n_out, 'instancia': ','.join(sorted(c['inst'])), 'pedido': False, 'status': 'feito', 'classificador': 'regra',
                  'duracao_min': int(min(90, (1.0 * n_out + 0.3 * n_in) if c['grp'] else (2 + 1.0 * n_out + 0.5 * n_in))),
                  '_amostra': [m['tx'] for m in (inbound or ms) if m['tx'] and not m['tx'].startswith('[')][:4],
                  'resumo': mask(next((m['tx'] for m in (inbound or ms) if m['tx'] and not GREET.match(m['tx'])), (inbound or ms)[0]['tx']))[:160]}
            if ped_msgs:
                last = max(m['ts'] for m in ped_msgs)
                resp = any(t > last for t in s.out_ts.get(k, []))
                it.update({'pedido': True, 'tipo': 'pedido', '_ped_ts': last, 'status': 'respondido' if resp else 'pendente',
                           'pendente_desde': None if resp else dt.datetime.fromtimestamp(last, TZ).isoformat()})
            elif n_out == 0:
                it['status'] = 'recebida' if any(t > max(m['ts'] for m in inbound) for t in s.out_ts.get(k, [])) else 'sem resposta'
                if it['status'] == 'recebida': it['status'] = 'respondido'
            if 'Pastor Filipe' in c['inst']: it['_area_rule'] = 'Igreja'
            items.append(it)
        return items, noise

def area_rules(items, wa):
    """applies rules, then cached contato table, then LLM for unknowns"""
    cache = {r['contato_key']: r for r in sb("select contato_key, area, ruido from atividades.contato", rows=True)}
    igr_groups = set()
    for it in items:
        k = it.get('_k'); nome = it.get('_nome') or ''
        if it['fonte'] == 'whatsapp' and it['tipo'].startswith('conversa (grupo)') and IGR_NAME.search(nome): igr_groups.add(k)
    members = {p for g, p in wa.igr_members if g in igr_groups} if wa else set()
    unknown = {}
    for it in items:
        nome = it.get('_nome') or it.get('com_quem') or ''; k = it.get('_k')
        if k and wa and k in wa.family: it['area'] = 'Família'; continue
        if FAM_NAME.search(nome) and re.search(r'renata|amanda|daniel(?! huff)', nome, re.I): it['area'] = 'Família'; continue
        if it.get('_area_rule'): it['area'] = it['_area_rule']; continue
        if IGR_NAME.search(nome): it['area'] = 'Igreja'; continue
        if FAM_NAME.search(nome) and not re.search(r'filipe', nome, re.I): it['area'] = 'Família'; it['classificador'] = 'regra'; continue
        if k and k in members: it['area'] = 'Igreja'; it['classificador'] = 'regra:membro_grupo'; continue
        ck = it['contato_key']
        if ck in cache and cache[ck]['area']:
            if cache[ck]['area'] == 'Ruído':
                if it.get('n_out', 0) == 0: it['_drop'] = True
                it['area'] = 'Pessoal'
            else: it['area'] = cache[ck]['area']
            it['classificador'] = 'llm(cache)'; continue
        u = unknown.setdefault(ck, {'nome': nome, 'amostra': [], 'items': []})
        u['amostra'] += it.get('_amostra', []) or [it.get('resumo', '')]; u['items'].append(it)
    if unknown:
        log('LLM área: contatos', len(unknown))
        res = classify_contacts(unknown)
        rows = []
        for ck, u in unknown.items():
            a = res.get(ck, 'Pessoal')
            for it in u['items']:
                if a == 'Ruído' and it.get('n_out', 0) == 0: it['_drop'] = True
                it['area'] = 'Pessoal' if a == 'Ruído' else a; it['classificador'] = 'llm'
            rows.append({'contato_key': ck, 'nome': mask(u['nome'])[:80], 'area': a, 'ruido': a == 'Ruído', 'classificador': 'llm'})
        if rows: sb(f"insert into atividades.contato(contato_key,nome,area,ruido,classificador) select contato_key,nome,area,ruido,classificador from json_populate_recordset(null::atividades.contato,{lit(rows)}::json) on conflict (contato_key) do update set area=excluded.area, ruido=excluded.ruido, atualizado=now();")
    return [it for it in items if not it.get('_drop')]

def refine_pedidos(items):
    peds = [it for it in items if it.get('pedido') and it['fonte'] == 'whatsapp']
    for i in range(0, len(peds), 30):
        ch = peds[i:i+30]
        payload = [{'k': n, 'de': mask(it.get('_nome') or '')[:40], 'msgs': [mask(x)[:200] for x in it.get('_amostra', [])[:4]]} for n, it in enumerate(ch)]
        r = llm(SYS_PED, json.dumps(payload, ensure_ascii=False), 'pedido')
        for x in r.get('r', []) if isinstance(r, dict) else []:
            try: it = ch[int(x['k'])]
            except Exception: continue
            if x.get('ruido') and it.get('n_out', 0) == 0: it['_drop'] = True; continue
            if x.get('s') and x.get('p'): it['resumo'] = mask(str(x['s']))[:160]
            if not x.get('p'):
                it.update({'pedido': False, 'tipo': 'conversa', 'pendente_desde': None, 'status': 'respondido' if it['status'] == 'respondido' else ('feito' if it['n_in'] == 0 else 'sem resposta')})
            it['classificador'] = (it.get('classificador') or 'regra') + '+llm'
    return [it for it in items if not it.get('_drop')]

# ---------- Composio sources ----------
def cx():
    from cx import CX; return CX()
def results(r):
    from cx import objs
    return objs(r)
CAL = [('googlecalendar_marler-eker', 'secretaria', 'Igreja'), ('googlecalendar_bund-egma', 'pessoal', None), ('googlecalendar_atom-foody', 'pessoal2', None)]
def calendar(c, start, end):
    items = {}; self_emails = {'<OWNER_GMAIL>', '<OWNER_OUTLOOK_ALIAS>', 'pastor<OWNER_GMAIL>', '<IFC_SECRETARIA_GMAIL>'}
    for acc, lab, area in CAL:
        tok = None
        for _ in range(20):
            args = {'calendarId': 'primary', 'timeMin': f'{start}T00:00:00-03:00', 'timeMax': f'{end + dt.timedelta(days=1)}T00:00:00-03:00', 'singleEvents': True, 'maxResults': 250, 'orderBy': 'startTime', 'timeZone': 'America/Sao_Paulo'}
            if tok: args['pageToken'] = tok
            r = c.run('GOOGLECALENDAR_EVENTS_LIST', acc, args); ob = results(r)
            evs = [o for o in ob if o.get('kind') == 'calendar#event' or (isinstance(o.get('start'), dict) and 'id' in o and 'summary' in o)]
            for e in evs:
                if e.get('status') == 'cancelled' or e.get('eventType') in ('birthday', 'workingLocation', 'outOfOffice'): continue
                me = [a for a in e.get('attendees', []) or [] if a.get('self') or a.get('email') in self_emails]
                if me and all(a.get('responseStatus') == 'declined' for a in me): continue
                st = e['start'].get('dateTime') or e['start'].get('date'); en = (e.get('end') or {}).get('dateTime') or (e.get('end') or {}).get('date')
                allday = 'dateTime' not in e['start']
                try:
                    sd = dt.datetime.fromisoformat(st).astimezone(TZ) if not allday else dt.datetime.combine(dt.date.fromisoformat(st), dt.time(), TZ)
                    dur = 0 if allday else int(min(600, (dt.datetime.fromisoformat(en) - dt.datetime.fromisoformat(st)).total_seconds() // 60))
                except Exception: continue
                if not (start <= sd.date() <= end): continue
                uid = e.get('iCalUID') or e['id']; key = uid + '|' + (st or '')
                n_att = len(e.get('attendees') or [])
                it = {'fonte': f'agenda:{lab}', 'id_origem': key, 'data': sd.date().isoformat(), 'hora': '' if allday else sd.strftime('%H:%M'),
                      'tipo': 'evento' if allday or n_att <= 1 else 'reunião', 'direcao': '', 'com_quem': f'{n_att} participantes' if n_att > 1 else '',
                      'resumo': mask(e.get('summary') or '(sem título)')[:160], '_nome': e.get('summary') or '', 'duracao_min': dur,
                      'status': 'realizado' if sd < dt.datetime.now(TZ) else 'agendado', 'classificador': 'regra', 'instancia': lab, 'contato_key': None}
                if area: it['_area_rule'] = area
                if key not in items or lab == 'secretaria': items[key] = it
            tok = next((o.get('nextPageToken') for o in ob if o.get('nextPageToken')), None)
            if not tok: break
    return list(items.values())

def todoist(c, start, end):
    acc = 'todoist_upways-single'; projects = {}
    for o in results(c.run('TODOIST_GET_ALL_PROJECTS', acc, {})):
        if isinstance(o.get('id'), str) and 'name' in o: projects[o['id']] = o['name']
    items = []; cur = None
    for _ in range(30):
        args = {'since': f'{start}T00:00:00Z', 'until': f'{end + dt.timedelta(days=1)}T03:00:00Z', 'limit': 200}
        if cur: args['cursor'] = cur
        ob = results(c.run('TODOIST_GET_COMPLETED_TASKS_BY_COMPLETION_DATE', acc, args))
        tasks = [o for o in ob if 'content' in o and ('completed_at' in o or 'completed_date' in o)]
        for t in tasks:
            ca = t.get('completed_at') or t.get('completed_date')
            try: cd = dt.datetime.fromisoformat(ca.replace('Z', '+00:00')).astimezone(TZ)
            except Exception: continue
            pj = projects.get(str(t.get('project_id')), '')
            items.append({'fonte': 'todoist', 'id_origem': f"{t.get('id') or t.get('task_id')}|{ca}", 'data': cd.date().isoformat(), 'hora': cd.strftime('%H:%M'), 'tipo': 'tarefa', 'direcao': '',
                          'com_quem': mask(pj)[:60], 'resumo': mask(t['content'])[:160], '_nome': f"{pj} {t['content']}", 'duracao_min': 15, 'status': 'concluída', 'classificador': 'regra', 'instancia': 'todoist', 'contato_key': None})
        cur = next((o.get('next_cursor') for o in ob if o.get('next_cursor')), None)
        if not cur: break
    return items

GM = [('gmail_achras-streek', 'pessoal', None), ('gmail_speen-lulu', 'prof', None), ('gmail_undock-mysian', 'dev', None), ('gmail_pakawa-spave', 'pastor', 'Igreja')]
OL = [('outlook_dev-kern', 'secretaria', 'Igreja'), ('outlook_bana-merlin', 'pastor', 'Igreja'), ('outlook_hoit-rejoin', 'pessoal', None)]
def hdr(m, name):
    for h in ((m.get('payload') or {}).get('headers') or []):
        if h.get('name', '').lower() == name: return h.get('value')
    return m.get(name) or m.get(name.capitalize())
def gmail(c, start, end):
    items = []
    for acc, lab, area in GM:
        tok = None
        for _ in range(15):
            args = {'query': f'in:sent after:{start:%Y/%m/%d} before:{end + dt.timedelta(days=1):%Y/%m/%d}', 'max_results': 100, 'include_payload': False, 'verbose': False}
            if tok: args['page_token'] = tok
            ob = results(c.run('GMAIL_FETCH_EMAILS', acc, args))
            ms = [o for o in ob if (o.get('messageId') or o.get('id')) and ('subject' in o or 'messageTimestamp' in o or 'payload' in o)]
            for m in ms:
                ts = m.get('messageTimestamp') or m.get('internalDate')
                try:
                    d = dt.datetime.fromtimestamp(int(ts) / 1000, TZ) if str(ts).isdigit() else dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).astimezone(TZ)
                except Exception: continue
                to = m.get('to') or hdr(m, 'to') or ''
                subj = m.get('subject') or hdr(m, 'subject') or '(sem assunto)'
                it = {'fonte': f'gmail:{lab}', 'id_origem': m.get('messageId') or m.get('id'), 'data': d.date().isoformat(), 'hora': d.strftime('%H:%M'), 'tipo': 'e-mail', 'direcao': 'enviada',
                      'com_quem': mask(re.sub(r'<.*?>', '', str(to)))[:80], 'resumo': mask(subj)[:160], '_nome': f'{to} {subj}', 'duracao_min': 5, 'status': 'enviado', 'classificador': 'regra', 'instancia': lab, 'contato_key': None}
                if area: it['_area_rule'] = area
                items.append(it)
            tok = next((o.get('nextPageToken') for o in ob if o.get('nextPageToken')), None)
            if not tok: break
    return items
def outlook(c, start, end):
    items = []
    for acc, lab, area in OL:
        skip = 0
        for _ in range(20):
            args = {'folder': 'sentitems', 'filter': f'sentDateTime ge {start}T03:00:00Z and sentDateTime lt {end + dt.timedelta(days=1)}T03:00:00Z', 'select': ['id', 'subject', 'sentDateTime', 'toRecipients'], 'top': 100, 'skip': skip, 'user_id': 'me'}
            ob = results(c.run('OUTLOOK_QUERY_EMAILS', acc, args))
            ms = [o for o in ob if o.get('sentDateTime') and o.get('id')]
            for m in ms:
                d = dt.datetime.fromisoformat(m['sentDateTime'].replace('Z', '+00:00')).astimezone(TZ)
                to = ', '.join((r.get('emailAddress') or {}).get('name') or '' for r in m.get('toRecipients') or [])
                it = {'fonte': f'outlook:{lab}', 'id_origem': m['id'], 'data': d.date().isoformat(), 'hora': d.strftime('%H:%M'), 'tipo': 'e-mail', 'direcao': 'enviada',
                      'com_quem': mask(to)[:80], 'resumo': mask(m.get('subject') or '(sem assunto)')[:160], '_nome': f"{to} {m.get('subject')}", 'duracao_min': 5, 'status': 'enviado', 'classificador': 'regra', 'instancia': lab, 'contato_key': None}
                if area: it['_area_rule'] = area
                items.append(it)
            if len(ms) < 100: break
            skip += 100
    return items

def area_simple(items):
    """for non-whatsapp items without rule: regex then LLM by title (batched)"""
    unk = []
    for it in items:
        if it.get('_area_rule'): it['area'] = it['_area_rule']; continue
        t = it.get('_nome') or it.get('resumo') or ''
        if IGR_NAME.search(t): it['area'] = 'Igreja'
        elif FAM_NAME.search(t) and not re.search('filipe', t, re.I): it['area'] = 'Família'
        else: unk.append(it)
    for i in range(0, len(unk), 40):
        ch = unk[i:i+40]
        r = llm(SYS_CONT, json.dumps([{'k': n, 'nome': mask(it['com_quem'] or '')[:50], 'msgs': [mask(it['resumo'])[:120]]} for n, it in enumerate(ch)], ensure_ascii=False), 'area_item')
        got = {}
        for x in r.get('r', []) if isinstance(r, dict) else []:
            try: got[int(x['k'])] = str(x['a'])
            except Exception: pass
        for n, it in enumerate(ch):
            a = got.get(n, 'Pessoal').lower()
            it['area'] = 'Igreja' if a.startswith('igr') else 'Família' if a.startswith('fam') else 'Pessoal'
            it['classificador'] = 'llm'
    return items

def finalize(items):
    for it in items:
        it['id'] = sid(it['fonte'], it['id_origem'])
        it.setdefault('area', 'Pessoal')
        for f in ('resumo', 'com_quem'):
            if it.get(f): it[f] = re.sub(r'\d{4,}(\d{4})', r'****\1', mask(it[f]))
    return items

# ---------- runs ----------
def ck_done(dia, fonte): return bool(sb(f"select 1 from atividades.checkpoint where dia='{dia}' and fonte='{fonte}' and ok", rows=True))
def ck_set(dia, fonte, info): sb(f"insert into atividades.checkpoint(dia,fonte,ok,info) values ('{dia}','{fonte}',true,{lit(json.dumps(info))}) on conflict (dia,fonte) do update set ok=true, info=excluded.info, em=now();")
def run(start, end, force=False, sources=('whatsapp', 'composio')):
    log('run', start, end, sources)
    if 'whatsapp' in sources:
        wa = WA(start, end); d = start
        while d <= end:
            if not force and ck_done(d, 'whatsapp'): d += dt.timedelta(days=1); continue
            items, noise = wa.day(d)
            items = area_rules(items, wa); items = refine_pedidos(items); items = finalize(items)
            sb(f"delete from atividades.atividade where fonte='whatsapp' and data='{d}' and id <> all(array[{','.join(lit(i['id']) for i in items) or 'null'}]::text[]);")
            upsert(items); ck_set(d, 'whatsapp', {'n': len(items), 'ruido': noise})
            log('whatsapp', d, 'itens', len(items), 'ruído', sum(noise.values()), 'llm', STATS['llm_calls'])
            d += dt.timedelta(days=1)
    if 'composio' in sources:
        c = cx()
        for name, fn in (('agenda', calendar), ('todoist', todoist), ('gmail', gmail), ('outlook', outlook)):
            if not force and ck_done(end, name) and start == end: continue
            try:
                items = finalize(area_simple(fn(c, start, end))); upsert(items); ck_set(end, name, {'n': len(items), 'de': str(start)})
                log(name, 'itens', len(items))
            except Exception as e:
                log(name, 'ERRO', type(e).__name__, str(e)[:200])
    log('uso LLM', STATS)

# ---------- sheet sync ----------
HEAD = ['ID', 'Data', 'Hora', 'Fonte', 'Tipo', 'Direção', 'Com quem', 'Resumo', 'Área', 'Duração (min)', 'Status', 'Pedido', 'Pendente desde', 'Classificador']
def sync():
    from cx import CX
    c = CX(); cfg = json.load(open(BASE + '/sheet.json')); S, A = cfg['spreadsheet_id'], cfg['account']
    def put(rng, vals):
        r = c.run('GOOGLESHEETS_VALUES_UPDATE', A, {'spreadsheet_id': S, 'range': rng, 'values': vals, 'value_input_option': 'USER_ENTERED', 'auto_expand_sheet': True})
        s = json.dumps(r)
        if '"successful": false' in s.replace('\\"', '"') and 'auto_expand' not in s: log('sheet aviso', rng, s[:200])
    rows = sb("select id, to_char(data,'DD/MM/YYYY') d, hora, fonte, tipo, direcao, com_quem, resumo, area, duracao_min, status, case when pedido then 'sim' else '' end p, coalesce(to_char(pendente_desde at time zone 'America/Sao_Paulo','DD/MM HH24:MI'),'') pd, classificador from atividades.atividade order by data desc, hora desc nulls last, id", rows=True)
    vals = [HEAD] + [[r['id'][:12], r['d'], r['hora'] or '', r['fonte'], r['tipo'], r['direcao'] or '', r['com_quem'] or '', r['resumo'] or '', r['area'], r['duracao_min'] or '', r['status'] or '', r['p'], r['pd'], r['classificador'] or ''] for r in rows]
    prev = cfg.get('n_ativ', 0)
    vals += [[''] * len(HEAD)] * max(0, prev - len(vals))
    for i in range(0, len(vals), 1500): put(f"'Atividades'!A{i+1}", vals[i:i+1500])
    cfg['n_ativ'] = len(rows) + 1
    pend = sb("select to_char(data,'DD/MM/YYYY') d, hora, com_quem, resumo, area, round(extract(epoch from now()-pendente_desde)/3600) h, instancia from atividades.atividade where pedido and status='pendente' and pendente_desde > now()-interval '30 days' order by pendente_desde", rows=True)
    pv = [['Data', 'Hora', 'De quem', 'Pedido', 'Área', 'Horas pendente', 'Alerta', 'Instância']] + [[p['d'], p['hora'], p['com_quem'], p['resumo'], p['area'], int(p['h'] or 0), '🔴 >48h' if (p['h'] or 0) > 48 else ('🟡 >24h' if (p['h'] or 0) > 24 else ''), p['instancia']] for p in pend]
    pv += [[''] * 8] * max(0, cfg.get('n_pend', 0) - len(pv)); put("'Pedidos pendentes'!A1", pv); cfg['n_pend'] = len(pend) + 1
    wk = sb("""select to_char(date_trunc('week',data),'DD/MM/YYYY') semana, count(*) total,
      count(*) filter (where area='Igreja') igreja, count(*) filter (where area='Família') familia, count(*) filter (where area='Pessoal') pessoal,
      round(100.0*count(*) filter (where area='Igreja')/count(*)) pct_igr, round(100.0*count(*) filter (where area='Família')/count(*)) pct_fam,
      count(*) filter (where fonte='whatsapp') conversas, coalesce(sum(n_out) filter (where fonte='whatsapp'),0) msgs_env, coalesce(sum(n_in) filter (where fonte='whatsapp'),0) msgs_rec,
      count(*) filter (where pedido) pedidos, count(*) filter (where pedido and status='respondido') respondidos,
      round(100.0*count(*) filter (where pedido and status='respondido')/nullif(count(*) filter (where pedido),0)) taxa_resp,
      count(*) filter (where pedido and status='pendente' and pendente_desde < now()-interval '48 hours') pend48,
      count(*) filter (where fonte='todoist') tarefas, round(coalesce(sum(duracao_min) filter (where tipo='reunião'),0)/60.0,1) h_reuniao,
      count(*) filter (where tipo='e-mail') emails, round(coalesce(sum(duracao_min),0)/60.0,1) h_estimadas
      from atividades.atividade group by 1, date_trunc('week',data) order by date_trunc('week',data) desc""", rows=True)
    wv = [['Semana (seg)', 'Total atividades', 'Igreja', 'Família', 'Pessoal', '% Igreja', '% Família', 'Conversas WhatsApp', 'Msgs enviadas', 'Msgs recebidas', 'Pedidos recebidos', 'Pedidos respondidos', 'Taxa resposta %', 'Pendentes >48h', 'Tarefas concluídas', 'Horas em reunião', 'E-mails enviados', 'Horas estimadas']]
    wv += [[w[k] if w[k] is not None else '' for k in ('semana', 'total', 'igreja', 'familia', 'pessoal', 'pct_igr', 'pct_fam', 'conversas', 'msgs_env', 'msgs_rec', 'pedidos', 'respondidos', 'taxa_resp', 'pend48', 'tarefas', 'h_reuniao', 'emails', 'h_estimadas')] for w in wk]
    top = sb("select com_quem, area, count(*) n from atividades.atividade where fonte='whatsapp' and data > current_date-7 group by 1,2 order by 3 desc limit 10", rows=True)
    wv += [[''], ['Top 10 contatos (últimos 7 dias)', 'Área', 'Dias com conversa']] + [[t['com_quem'], t['area'], t['n']] for t in top]
    wv += [[''] * 18] * max(0, cfg.get('n_wk', 0) - len(wv)); put("'Resumo semanal'!A1", wv); cfg['n_wk'] = len(wv)
    cfg['ultima_sync'] = dt.datetime.now(TZ).isoformat(); json.dump(cfg, open(BASE + '/sheet.json', 'w'))
    # pendentes >48h para o resumo das 7h
    p48 = sb("select data, hora, com_quem, resumo, area, round(extract(epoch from now()-pendente_desde)/3600) horas from atividades.atividade where pedido and status='pendente' and pendente_desde < now()-interval '48 hours' and pendente_desde > now()-interval '14 days' order by pendente_desde", rows=True)
    os.makedirs(BASE + '/out', exist_ok=True)
    tmp = BASE + '/out/.pendentes_48h.json'; json.dump({'gerado_em': dt.datetime.now(TZ).isoformat(), 'total': len(p48), 'itens': p48}, open(tmp, 'w'), ensure_ascii=False, default=str); os.replace(tmp, BASE + '/out/pendentes_48h.json')
    log('sync ok: atividades', len(rows), 'pendentes', len(pend), 'semanas', len(wk), '>48h', len(p48))

def refresh_status():
    """re-checa pendentes dos últimos 14 dias: se Filipe respondeu depois, marca respondido"""
    pend = sb("select id, contato_key, extract(epoch from pendente_desde)::bigint ts from atividades.atividade where fonte='whatsapp' and pedido and status='pendente' and data > current_date-14", rows=True)
    if not pend: return
    today = dt.datetime.now(TZ).date(); wa = WA(today - dt.timedelta(days=14), today)
    keymap = {hashlib.sha1(k.encode()).hexdigest()[:16]: v for k, v in wa.out_ts.items()}
    done = [p['id'] for p in pend if any(t > p['ts'] for t in keymap.get(p['contato_key'], []))]
    if done: sb(f"update atividades.atividade set status='respondido', pendente_desde=null, atualizado=now() where id = any(array[{','.join(lit(i) for i in done)}]::text[]);")
    log('status atualizado: respondidos', len(done), 'de', len(pend))

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd'); ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--force', action='store_true'); ap.add_argument('--no-sync', action='store_true'); ap.add_argument('--only')
    a = ap.parse_args()
    src = tuple(a.only.split(',')) if a.only else ('whatsapp', 'composio')
    if a.cmd == 'run':
        run(dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end), a.force, src)
        if not a.no_sync: refresh_status(); sync()
    elif a.cmd == 'daily':
        today = dt.datetime.now(TZ).date()
        run(today - dt.timedelta(days=1), today, True, src); refresh_status(); sync()
    elif a.cmd == 'sync': sync()
