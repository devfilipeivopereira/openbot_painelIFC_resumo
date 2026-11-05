#!/usr/bin/env python3
"""contato: diretório de contatos do Filipe (SQLite /var/lib/contatos/contatos.db).
Uso: contato busca TEXTO | quem NUMERO_OU_JID | grupo NOME | papel TEXTO | sync | stats
Dados PRIVADOS: nunca compartilhar listas/números com terceiros."""
import sys, os, json, re, sqlite3, unicodedata, subprocess, csv, urllib.request, urllib.parse, time, datetime
DB = "/var/lib/contatos/contatos.db"
ETC = "/etc/contatos"
N8N_B = "https://work.filipeivopereira.com/api/v1"; DT = "GBjrHpVOEw7hGSjE"
EVO_B = "https://evolution.filipeivopereira.com"
EVO_H = "/var/lib/docker/volumes/openbot-data/_data/.config/evo/global.h"
FAMILIA = {"<FAMILY_1>": "Renata (esposa)", "<FAMILY_2>": "Daniel (filho)", "<FAMILY_3>": "Amanda (filha)"}

def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).strip()

def key(p):
    p = re.sub(r"\D", "", p or "")
    if len(p) in (10, 11) and not p.startswith("55"): p = "55" + p
    if p.startswith("55") and len(p) == 13 and p[4] == "9": p = p[:4] + p[5:]
    return p

def e164(p):
    k = key(p)
    if k.startswith("55") and len(k) == 12 and k[4] in "6789": return "+" + k[:4] + "9" + k[4:]
    return "+" + k if k else ""

def con():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.create_function("fold", 1, fold); return c

def jkey(jid):
    """jid/número -> chave de telefone (usa mapa LID)."""
    if not jid: return None
    j = str(jid)
    if j.endswith("@lid"):
        r = con().execute("select tel from jidmap where jid=?", (j,)).fetchone()
        return r[0] if r else None
    if "@g.us" in j: return None
    return key(j.split("@")[0].split(":")[0])

def row(r, c=None):
    d = {k: r[k] for k in r.keys() if k not in ("busca",) and r[k] not in (None, "")}
    d["telefone"] = e164(r["tel"])
    return d

# ---------- consultas ----------
def busca(q, lim=15):
    c = con(); f = fold(q); dig = re.sub(r"\D", "", q)
    if len(dig) >= 6:
        k = key(dig)
        rs = c.execute("select * from contato where tel like ? limit ?", ("%" + k[-8:] + "%", lim)).fetchall()
    else:
        toks = f.split()
        where = " and ".join(["busca like ?"] * len(toks)) or "1"
        rs = c.execute(f"select * from contato where {where} order by proprio desc, familia is null, msgs desc limit ?",
                       [f"%{t}%" for t in toks] + [lim]).fetchall()
        if not rs:  # fuzzy
            import difflib
            allr = c.execute("select * from contato").fetchall()
            sc = sorted(allr, key=lambda r: -max(difflib.SequenceMatcher(None, f, w).ratio() for w in (r["busca"] or " ").split("|")))
            rs = sc[:5]
    return [row(r) for r in rs]

def quem(x):
    c = con(); k = jkey(x) if "@" in x else key(x)
    if not k: return {"erro": "não resolvido (LID sem mapa ou grupo)", "jid": x}
    r = c.execute("select * from contato where tel=?", (k,)).fetchone()
    if not r: return {"erro": "número não está no diretório", "telefone": e164(k)}
    d = row(r)
    d["grupos_wa"] = [g[0] for g in c.execute("select distinct g.nome from membro m join grupo g on g.jid=m.grupo where m.tel=? and g.nome<>''", (k,))]
    return d

def grupo(q):
    c = con(); f = fold(q)
    gs = c.execute("select * from grupo where fold(nome) like ? order by tamanho desc limit 5", (f"%{f}%",)).fetchall()
    out = []
    for g in gs:
        ms = []
        for m in c.execute("select m.tel, m.jid, m.admin, ct.nome, ct.apelido, ct.papel, ct.papel_ifc, ct.familia, ct.proprio from membro m left join contato ct on ct.tel=m.tel where m.grupo=?", (g["jid"],)):
            ms.append({k: m[k] for k in m.keys() if m[k] not in (None, "", 0) and k not in ("tel", "jid")} | {"telefone": e164(m["tel"]) if m["tel"] else None, "jid": None if m["tel"] else m["jid"]})
        ms.sort(key=lambda d: (d.get("nome") is None, d.get("nome") or ""))
        out.append({"grupo": g["nome"], "jid": g["jid"], "instancia": g["instancia"], "membros": len(ms), "identificados": sum(1 for m in ms if m.get("nome")), "lista": ms})
    return out

def papel(q):
    c = con(); f = fold(q)
    alias = {"lideres": "lider", "líderes": "lider", "lider": "lider"}
    f = alias.get(f, f)
    rs = c.execute("select * from contato where fold(coalesce(papel,'')||' '||coalesce(papel_ifc,'')||' '||coalesce(familia,'')||' '||coalesce(observacoes,'')) like ? or fold(coalesce(grupos,'')) like ? order by papel_ifc is null, nome limit 200", (f"%{f}%", f"%{f}%")).fetchall()
    return [{"nome": r["nome"], "apelido": r["apelido"], "papel": r["papel"], "papel_ifc": r["papel_ifc"], "familia": r["familia"], "telefone": e164(r["tel"])} for r in rs]

def stats():
    c = con(); q = lambda s: c.execute(s).fetchone()[0]
    return {"contatos": q("select count(*) from contato"), "com_n8n": q("select count(*) from contato where fontes like '%n8n%'"),
            "com_evolution": q("select count(*) from contato where fontes like '%evolution%'"), "com_email": q("select count(*) from contato where email<>''"),
            "papel_ifc": q("select count(*) from contato where papel_ifc<>''"), "familia": q("select count(*) from contato where familia<>''"),
            "proprios": q("select count(*) from contato where proprio=1"), "grupos": q("select count(*) from grupo"), "membros": q("select count(*) from membro"),
            "membros_identificados": q("select count(*) from membro m join contato c on c.tel=m.tel"), "jidmap": q("select count(*) from jidmap"),
            "sync": q("select v from meta where k='sync'")}

# ---------- sync ----------
def http(url, h):
    rq = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(rq, timeout=300) as r: return json.loads(r.read())

def evo_h():
    h = {}
    for l in open(EVO_H):
        if ":" in l: k, v = l.split(":", 1); h[k.strip()] = v.strip()
    return h

def psql(sql):
    name = subprocess.run(["docker", "ps", "-qf", "name=postgres_postgres"], capture_output=True, text=True).stdout.split()[0]
    r = subprocess.run(["docker", "exec", "-i", name, "psql", "-U", "evo_ro", "-d", "evolution", "-At", "-P", "pager=off"],
                       input="BEGIN READ ONLY;" + sql + ";COMMIT;", capture_output=True, text=True, check=True)
    ls = [l for l in r.stdout.splitlines() if l not in ("BEGIN", "COMMIT", "")]
    return json.loads(ls[0]) if ls else []

def sync():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    tmp = DB + ".tmp"
    if os.path.exists(tmp): os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""create table contato(tel text primary key, nome text, apelido text, papel text, papel_ifc text, familia text, proprio int default 0,
      grupos text, email text, observacoes text, canal text, numero_envio text, ultima_conversa text, nomes_wa text, instancias text, msgs int default 0, fontes text, busca text);
      create table jidmap(jid text primary key, tel text); create table grupo(jid text primary key, nome text, instancia text, tamanho int);
      create table membro(grupo text, jid text, tel text, admin text, primary key(grupo,jid)); create table meta(k text primary key, v text);""")
    C = {}
    def get(k):
        return C.setdefault(k, {"tel": k, "nomes": {}, "fontes": set(), "inst": set(), "msgs": 0})
    # n8n
    n8k = open(ETC + "/n8n.key").read().strip(); cur = None; n8 = 0
    while True:
        d = http(f"{N8N_B}/data-tables/{DT}/rows?limit=250" + (f"&cursor={urllib.parse.quote(cur)}" if cur else ""), {"X-N8N-API-KEY": n8k})
        for r in d["data"]:
            k = key(r.get("telefone"))
            if not k: continue
            n8 += 1; x = get(k); x["fontes"].add("n8n")
            for f in ("nome", "apelido", "papel", "grupos", "canal_preferido", "numero_envio", "email", "observacoes", "ultima_conversa"):
                if r.get(f): x[f] = str(r[f])
        cur = d.get("nextCursor")
        if not cur: break
    # lid seed
    jm = {}
    if os.path.exists(ETC + "/lidmap.csv"):
        for r in csv.DictReader(open(ETC + "/lidmap.csv")): jm[r["a"]] = key(r["b"].split("@")[0])
    # Evolution DB
    inst = psql("""select json_agg(json_build_object('n',name,'o',"ownerJid",'s',"connectionStatus")) from "Instance" """) or []
    own = {}
    for i in inst:
        if i.get("o"): own.setdefault(key(i["o"].split("@")[0]), []).append(i["n"])
    cts = psql("""select json_agg(json_build_object('j',ct."remoteJid",'p',ct."pushName",'i',i.name)) from "Contact" ct join "Instance" i on i.id=ct."instanceId" where ct."remoteJid" like '%@s.whatsapp.net'""") or []
    for r in cts:
        k = key(r["j"].split("@")[0]); x = get(k); x["fontes"].add("evolution"); x["inst"].add(r["i"])
        if r.get("p"): x["nomes"][r["p"]] = x["nomes"].get(r["p"], 0) + 1
    chs = psql("""select json_agg(json_build_object('j',c."remoteJid",'n',c.name,'i',i.name)) from "Chat" c join "Instance" i on i.id=c."instanceId" where c.name is not null and c.name<>''""") or []
    gnames = {}
    for r in chs:
        if r["j"].endswith("@g.us"): gnames.setdefault(r["j"], (r["n"], r["i"]))
        elif r["j"].endswith("@s.whatsapp.net"):
            x = get(key(r["j"].split("@")[0])); x["fontes"].add("evolution"); x["inst"].add(r["i"]); x["nomes"][r["n"]] = x["nomes"].get(r["n"], 0) + 3
    ms = psql("""select json_agg(json_build_object('j',j,'n',n)) from (select m.key->>'remoteJid' j, count(*) n from "Message" m where m.key->>'remoteJid' like '%@s.whatsapp.net' and m."messageTimestamp" > extract(epoch from now()-interval '365 days') group by 1) z""") or []
    for r in ms:
        k = key(r["j"].split("@")[0])
        if k in C: C[k]["msgs"] += r["n"]
    # grupos via API (participantes)
    h = evo_h(); members = {}
    for i in inst:
        if i.get("s") != "open": continue
        try: gl = http(f"{EVO_B}/group/fetchAllGroups/{urllib.parse.quote(i['n'])}?getParticipants=true", h)
        except Exception as e: print("aviso grupos", i["n"], type(e).__name__, file=sys.stderr); continue
        for g in gl:
            gid = g["id"]
            if gid not in gnames or not gnames[gid][0]: gnames[gid] = (g.get("subject") or "", i["n"])
            for p in g.get("participants") or []:
                pj = p.get("id"); pn = p.get("phoneNumber") or (pj if pj and pj.endswith("@s.whatsapp.net") else None)
                t = key(pn.split("@")[0]) if pn else jm.get(pj)
                if pj and pj.endswith("@lid") and t: jm[pj] = t
                members[(gid, pj)] = (t, p.get("admin") or "")
    for (gid, pj), (t, adm) in members.items():
        if t: get(t)["fontes"].add("evolution")
    for gid, (n, i) in gnames.items():
        c.execute("insert into grupo values(?,?,?,?)", (gid, n, i, sum(1 for (g, _) in members if g == gid)))
    for (gid, pj), (t, adm) in members.items():
        c.execute("insert or ignore into membro values(?,?,?,?)", (gid, pj, t, adm))
    c.executemany("insert or replace into jidmap values(?,?)", jm.items())
    lid = json.load(open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "lideres.json")))
    for k, x in C.items():
        nomes = sorted(x["nomes"], key=lambda n: -x["nomes"][n])
        nome = x.get("nome") or (nomes[0] if nomes else "")
        fam = FAMILIA.get(k, ""); prop = 1 if k in own else 0
        pi = ""
        for ln, pr in lid.items():
            if fold(ln) and (fold(nome).startswith(fold(ln)) or fold(x.get("apelido", "")) == fold(ln)) and (len(ln.split()) > 1 or "lideres ifc" in fold(x.get("grupos", "")) or "secretaria" in fold(nome)):
                pi = pr; break
        if prop: pi = "NÚMERO DO PRÓPRIO FILIPE (instâncias: " + ", ".join(own[k]) + ")"
        if k == "<STAFF_SECRETARIA>": pi = "Secretaria IFC (Kelly)"
        busca = "|".join(fold(s) for s in [nome, x.get("apelido", ""), *nomes, x.get("email", ""), fam] if s)
        c.execute("insert into contato values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (k, nome, x.get("apelido", ""), x.get("papel", ""), pi, fam, prop,
                  x.get("grupos", ""), x.get("email", ""), x.get("observacoes", ""), x.get("canal_preferido", ""), x.get("numero_envio", ""),
                  x.get("ultima_conversa", ""), ", ".join(nomes[:5]), ", ".join(sorted(x["inst"])), x["msgs"], ",".join(sorted(x["fontes"])), busca))
    c.execute("insert into meta values('sync',?)", (datetime.datetime.now().strftime("%Y-%m-%d %H:%M BRT"),))
    c.execute("insert into meta values('n8n_rows',?)", (str(n8),))
    c.commit(); c.close(); os.chmod(tmp, 0o600); os.replace(tmp, DB)
    print(json.dumps({"ok": True, "n8n_rows": n8, **stats()}, ensure_ascii=False))

def enrich(obj):
    """Adiciona *_contato a chaves jid/chat/participant_jid (usado por evo-db)."""
    if not os.path.exists(DB): return obj
    c = con(); cache = {}
    def look(j):
        if j in cache: return cache[j]
        k = jkey(j); r = c.execute("select nome,apelido,papel,papel_ifc,familia from contato where tel=?", (k,)).fetchone() if k else None
        cache[j] = {kk: r[kk] for kk in r.keys() if r[kk]} if r else None; return cache[j]
    def walk(o):
        if isinstance(o, dict):
            for kk in list(o):
                v = o[kk]
                if kk in ("chat", "jid", "participant_jid", "remoteJid") and isinstance(v, str) and "@" in v and not v.endswith("@g.us"):
                    l = look(v)
                    if l: o[kk + "_contato"] = l
                else: walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(obj); return obj

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(2)
    cmd, arg = a[0], " ".join(a[1:])
    if cmd == "sync": sync(); sys.exit()
    if not os.path.exists(DB): print('{"erro":"diretório ainda não sincronizado"}'); sys.exit(2)
    f = {"busca": busca, "quem": quem, "grupo": grupo, "papel": papel, "stats": lambda _: stats()}.get(cmd)
    if not f: print(__doc__); sys.exit(2)
    print(json.dumps(f(arg), ensure_ascii=False, indent=1))
