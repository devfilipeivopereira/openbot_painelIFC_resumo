#!/usr/bin/env python3
"""Enriquecimento SOMENTE LEITURA do painel (04/11, Grok Bot) -> chave "painel_ifc" em published/dados.json.
Roda no host (cron */5). Só trabalha quando o publisher grava um dados.json novo (mtime mudou).
Fontes (todas leitura): Supabase public.visitantes / transactions (psql read-only), n8n API GET,
Evolution GET /group/participants + POST /chat/findMessages (Louvor), Composio Instagram (coletor do container, só leitura),
staging do ifc-mcp (mensagens_diretas, evolution_mulheres). Nunca grava telefones/JIDs na saída.
Cada parte falha sozinha e cai no último valor bom (cache). Preserva mtime/dono/modo do dados.json."""
import hashlib, json, os, re, sys, time, fcntl, tempfile, subprocess, datetime as dt, urllib.parse
from pathlib import Path
import requests
PANEL = Path("/opt/ifc-painel/published/dados.json")
STATE = Path("/var/lib/ifc-panel-enrich"); CACHE = STATE / "cache.json"; LOCK = STATE / "lock"
MCP = Path("/opt/ifc-painel/mcp"); SEC = MCP / "secrets"
STG = Path("/var/lib/docker/volumes/ifc-mcp-state/_data/data/staging")
EVO = "https://evolution.filipeivopereira.com"; N8N = "https://work.filipeivopereira.com/api/v1"
BRT = dt.timezone(dt.timedelta(hours=-3)); NOW = dt.datetime.now(BRT)
N8N_FLOWS = {"8EfOLwKPt2duN3Qhz_I0y": "IFC - Visitantes - Webhook Baserow WhatsApp V2", "gafW6vM_9WfzbsQYXsYaZ": "Mensagens Visitantes",
    "cOiLM1zYMqVMsrWR": "Visitantes - Verificar Respostas", "4pbQKo6woOtsvaojQlKLP": "Formulario Encorajar", "mBDblVN3QDTRNNf0": "Instagram Direct → IFC",
    "jO8Dguoh1MdjHmuT_jJ2k": "", "D4j4mH6biMZ3zLOdtvMHd": "", "Y5NlHw4XFFxrtSVLVwU8w": ""}
LOUVOR_JID = "554899412339-1449606142@g.us"
def hdr(path, name):
    for l in Path(path).read_text().splitlines():
        if l.lower().startswith(name.lower() + ":"): return l.split(":", 1)[1].strip()
    raise ValueError("header_missing")
def evo_key(): return hdr(SEC / "evolution-global.h", "apikey")
def psql(sql):
    db = subprocess.run(["docker", "ps", "-q", "-f", "name=supabase_supabase_db"], capture_output=True, text=True).stdout.split()[0]
    r = subprocess.run(["docker", "exec", "-i", db, "psql", "-U", "postgres", "-At"], input="set default_transaction_read_only=on;\n" + sql, capture_output=True, text=True, timeout=120)
    return [json.loads(l) for l in r.stdout.splitlines() if l[:1] in "[{"]
def br(iso):
    try: return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BRT)
    except Exception: return None

def visitantes():
    lista, tot, inter = psql("""select coalesce(json_agg(t),'[]')::jsonb::text from (select trim(nome) nome, trim(sobrenome) sobrenome, data::text data, visitando, origem, msg_enviada, respondeu_msg
      from public.visitantes order by data_submissao desc nulls last limit 20) t;
select json_build_object('total',count(*),'d30',count(*) filter (where data >= current_date-30),'d7',count(*) filter (where data >= current_date-7))::text from public.visitantes;
select json_build_object('visitantes',count(*),'enviadas',count(*) filter (where msg_enviada is true),'responderam',count(*) filter (where respondeu_msg is true),
  'sem_mensagem',count(*) filter (where msg_enviada is not true))::text from public.visitantes;""")
    t = lambda s: " ".join(w.capitalize() for w in (s or "").split())
    inter["taxa"] = round(100 * inter["responderam"] / inter["enviadas"]) if inter.get("enviadas") else 0
    return {"totais": tot, "lista": [{"nome": (t(v["nome"]) + " " + t(v["sobrenome"])).strip(), "data": v["data"], "vez": v.get("visitando")} for v in lista[:12]],
            "interacao_totais": inter, "fonte": "Supabase public.visitantes (somente leitura)"}

GEN = {"PESSOAL": "Pessoal (remuneração e serviços)", "MISSÕES": "Missões (missionários e projetos)"}
def financeiro():
    ini = (NOW.date().replace(day=1) - dt.timedelta(days=330)).replace(day=1).isoformat()
    [fi] = psql(f"""with c as (select c.id::text id, c.name, c.type, coalesce(p.name, c.name) parent from public.categories c left join public.categories p on p.id=c.parent_id),
t as (select t.transaction_date d, t.amount a, case when c.type = 'NEUTRA' then 'NEUTRA' else t.entry_type end e, t.method m,
 coalesce(c.name, nullif(t.category,''), 'Sem categoria') cat, coalesce(c.parent, case when t.category is null or t.category='' then 'Sem categoria' else '—' end) par
 from public.transactions t left join c on c.id = t.category or c.name = t.category)
select json_build_object(
 'categorias', (select json_agg(k) from (select to_char(d,'YYYY-MM') mes, e tipo, par, cat, round(abs(sum(a)),2) v, count(*) n from t where e in ('RECEITA','DESPESA') and d >= '{ini}'::date - interval '12 months' group by 1,2,3,4) k),
 'maiores', (select json_agg(g) from (select mes, par, cat, m, v, d from (select to_char(d,'YYYY-MM') mes, par, cat, m, round(-a,2) v, d::text d,
   row_number() over (partition by to_char(d,'YYYY-MM') order by a) r from t where e='DESPESA' and d >= '{ini}'::date - interval '12 months') q where r<=8) g))::jsonb::text;""")
    porcat, maiores = {}, {}
    for k in fi["categorias"] or []:
        if k["tipo"] == "RECEITA": nome = "Sem categoria" if k["cat"] == "Sem categoria" else k["cat"].replace("Dizimos", "Dízimos")
        else: nome = "Sem categoria" if k["cat"] == "Sem categoria" else (GEN.get(k["par"]) or k["par"].title())
        dm = porcat.setdefault(k["mes"], {"RECEITA": {}, "DESPESA": {}})[k["tipo"]]; dm[nome] = round(dm.get(nome, 0) + k["v"], 2)
    for g in fi["maiores"] or []:
        semcat = g["cat"] == "Sem categoria" or g["par"] in ("—", "Sem categoria")
        catg = "Sem categoria" if semcat else (GEN.get(g["par"]) or g["par"].title())
        desc = {"PESSOAL": "Folha e serviços de pessoal", "MISSÕES": "Oferta missionária"}.get(g["par"]) or ("Lançamento ainda sem categoria" if semcat else g["cat"])
        maiores.setdefault(g["mes"], []).append({"data": g["d"], "categoria": catg, "descricao": desc + (" (banco)" if g["m"] == "BANCO" else " (caixa)"), "valor": g["v"]})
    return {"categorias": {m: {t: sorted(v.items(), key=lambda kv: -kv[1]) for t, v in d.items()} for m, d in porcat.items()}, "maiores": maiores}

def n8n():
    H = {"X-N8N-API-KEY": hdr(SEC / "n8n.h", "x-n8n-api-key"), "accept": "application/json"}
    get = lambda p, **q: requests.get(N8N + p, headers=H, params=q, timeout=60).json()
    corte = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7); out = []
    for wid, nome in N8N_FLOWS.items():
        try: w = get(f"/workflows/{wid}")
        except Exception: continue
        if not isinstance(w, dict) or not w.get("id"): continue
        exs = get("/executions", workflowId=wid, limit=100).get("data", [])
        rec = [x for x in exs if br(x.get("startedAt")) and br(x["startedAt"]) >= corte]
        ok = sum(1 for x in rec if x.get("status") == "success"); err = sum(1 for x in rec if x.get("status") in ("error", "crashed", "failed"))
        last = max(exs, key=lambda x: x.get("startedAt") or "", default=None)
        st = "inativo" if not w.get("active") else ("erro" if last and last.get("status") in ("error", "crashed", "failed") else ("atencao" if err else "ok"))
        out.append({"nome": w.get("name") or nome, "ativo": bool(w.get("active")), "ultima": br(last["startedAt"]).strftime("%d/%m %H:%M") if last else None,
                    "ultima_status": last.get("status") if last else None, "ok7": ok, "err7": err, "status": st, "nota": "", "erro": None})
    return out

def participantes(contatos):
    """Participantes por grupo, identificados pelo JID do grupo na Evolution (não pelo nome).
    Saída sem telefones/JIDs de pessoas: só nomes de exibição. 'id' = hash curto do JID do grupo."""
    K = {"apikey": evo_key()}; ids = {}
    for c in contatos or []:
        n = c.get("nome")
        if not n or n == "***": continue
        for k in (c.get("evolution_id"), c.get("telefone")):
            if k: ids[k] = n; ids[str(k).split("@")[0]] = n
    def limpa_nome(n):
        n = (n or "").strip()
        return "" if re.search(r"\d{8,}", re.sub(r"[\s+()\-.]", "", n)) else n
    def nome_de(p):
        for k in (p.get("id"), p.get("jid"), p.get("phoneNumber"), p.get("lid")):
            if k and (ids.get(k) or ids.get(str(k).split("@")[0])): return limpa_nome(ids.get(k) or ids.get(str(k).split("@")[0]))
        return limpa_nome(p.get("name") or p.get("pushName") or p.get("notify"))
    G = json.loads((SEC / "evolution-groups.json").read_text()); W = json.loads((SEC / "evolution-women.json").read_text())
    alvo = [(g["name"], g["remote_jid"], g.get("category", ""), G["instance"]) for g in G["groups"]] + [("Mulheres IFC", W["remote_jid"], "Ministério de Mulheres", W["instance"])]
    out = []
    for nome, jid, cat, inst in alvo:
        gid = hashlib.sha256(jid.encode()).hexdigest()[:8]
        try:
            r = requests.get(f"{EVO}/group/participants/{urllib.parse.quote(inst)}", params={"groupJid": jid}, headers=K, timeout=60).json()
            ps = r.get("participants", r) if isinstance(r, dict) else r
            if not isinstance(ps, list) or not all(isinstance(p, dict) for p in ps): raise LookupError("sem_acesso")
            adm = [p for p in ps if p.get("admin")]
            nomes_adm = sorted({nome_de(p) for p in adm} - {""})
            membros = sorted([n for n in map(nome_de, ps) if n], key=str.casefold)
            sem = sum(1 for p in ps if not nome_de(p))
            out.append({"id": gid, "g": nome, "label": nome, "c": cat, "t": len(ps), "a": len(adm), "s": sem, "admins": nomes_adm, "membros": membros})
        except Exception as e:
            out.append({"id": gid, "g": nome, "label": nome, "c": cat, "erro": type(e).__name__})
        time.sleep(0.3)
    cont = {}
    for x in out: cont[x["g"]] = cont.get(x["g"], 0) + 1
    for x in out:
        if cont[x["g"]] > 1:
            x["label"] = f'{x["g"]} ({x["t"]} membros)' if "t" in x else f'{x["g"]} #{x["id"][:4]}'
    lab = {}
    for x in out: lab.setdefault(x["label"], []).append(x)
    for xs in lab.values():
        if len(xs) > 1:
            for x in xs: x["label"] += f' #{x["id"][:4]}'
    return out

def stage(name):
    return json.loads((STG / f"{name}.json").read_text())
def diretas():
    d = stage("mensagens_diretas"); por = {}
    for m in d.get("messages", []):
        p = m.get("person") or ""
        if not p: continue
        x = por.setdefault(p, {"papel": m.get("role") or "", "n_msgs": 0, "ultima": ""})
        x["n_msgs"] += 1; t = br(m.get("at"))
        if t and t.strftime("%m/%d %H:%M") > x.get("_k", ""): x["_k"] = t.strftime("%m/%d %H:%M"); x["ultima"] = t.strftime("%d/%m %H:%M")
    for x in por.values(): x.pop("_k", None)
    ws, we = br(d.get("window_start")), br(d.get("window_end"))
    return {"por_pessoa": por, "monitorados": d.get("contacts_monitored"), "janela": (ws.strftime("%d/%m %H:%M") + " a " + we.strftime("%d/%m %H:%M")) if ws and we else ""}
def mulheres_msgs():
    d = stage("evolution_mulheres"); ms = d.get("messages", []) if isinstance(d, dict) else []
    ts = sorted(t for t in (br(m.get("at")) for m in ms) if t)
    return {"mensagens_lidas": len(ms), "ultima_mensagem": ts[-1].strftime("%Y-%m-%d %H:%M") if ts else ""}

def louvor():
    os.environ["EVOLUTION_API_KEY"] = evo_key(); sys.argv = [sys.argv[0], "60"]
    sys.path.insert(0, str(MCP / "enrich")); import louvor_parse as L
    L.DIAS = 60; por = {}
    for x in sorted(L.coletar(), key=lambda x: int(x["messageTimestamp"])):
        ts = int(x["messageTimestamp"])
        for e in L.parse_post(L.texto(x), ts):
            e["fonte"] = (x.get("pushName") or "").split()[0] + ", " + dt.datetime.fromtimestamp(ts, BRT).strftime("%d/%m") if x.get("pushName") else dt.datetime.fromtimestamp(ts, BRT).strftime("%d/%m")
            por[e["data"]] = e
    out = []
    for d_, e in sorted(por.items()):
        out.append({"data": d_, "ministro": e["ministro"], "vocais": ", ".join(e["vocais"]),
                    "instrumentos": " · ".join(f'{i["instrumento"]}: {", ".join(i["nomes"])}' for i in e["instrumentos"]),
                    "funcoes": " · ".join(f'{f["funcao"]}: {", ".join(f["nomes"])}' for f in e["funcoes"]), "obs": e["obs"], "fonte": e["fonte"]})
    return out

IG_CODE = r'''
import sys,json;sys.path.insert(0,"/app/collectors");sys.path.insert(0,"/app")
src=open("/app/collectors/composio_instagram.py").read().replace("timedelta(hours=24)","timedelta(days=150)")
assert "OUTPUT=ROOT/'staging'/'instagram.json'" in src
src=src.replace("OUTPUT=ROOT/'staging'/'instagram.json'","OUTPUT=ROOT/'enrich'/'instagram_recent.json'").split("if __name__")[0]
g={"__name__":"ig"}; exec(compile(src,"ig","exec"),g); g["collect"]()
print("@@"+json.dumps(json.load(open(g["OUTPUT"]))))
'''
def instagram():
    r = subprocess.run(["docker", "exec", "-i", "ifc-mcp", "python3", "-"], input=IG_CODE, capture_output=True, text=True, timeout=180)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    val = json.loads(line[-1][2:]) if line else {}
    msgs = val.get("messages") or []
    if not msgs: raise ValueError("instagram_vazio")
    return [{"data": m["at"][:10], "tipo": {"VIDEO": "Reels", "CAROUSEL_ALBUM": "Carrossel", "IMAGE": "Post"}.get(m.get("type"), "Post"),
             "resumo": (re.sub(r"\s+", " ", m.get("text") or "")[:90] + ("…" if len(m.get("text") or "") > 90 else "")), "url": m.get("url")} for m in msgs[:12]]

def main():
    STATE.mkdir(mode=0o700, exist_ok=True)
    with open(LOCK, "a+") as lk:
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        st = PANEL.stat(); data = json.loads(PANEL.read_text(encoding="utf-8"))
        cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        force = "--force" in sys.argv
        if not force and cache.get("base_mtime_ns") == st.st_mtime_ns and data.get("painel_ifc"): return 0
        out = dict(cache.get("painel_ifc") or {}); status = {}
        parts = [("visitantes", visitantes), ("financeiro_detalhe", financeiro), ("n8n", n8n), ("diretas_stats", diretas),
                 ("mulheres_stats", mulheres_msgs), ("louvor", louvor), ("instagram", instagram),
                 ("participantes", lambda: participantes((data.get("participantes_grupos") or {}).get("contatos")))]
        for k, f in parts:
            try: out[k] = f(); status[k] = "ok"
            except Exception as e: status[k] = "falhou (" + type(e).__name__ + "); último bom mantido" if k in out else "falhou (" + type(e).__name__ + ")"
        out["status"] = status; out["gerado_em"] = NOW.strftime("%d/%m/%Y %H:%M")
        CACHE.write_text(json.dumps({"base_mtime_ns": st.st_mtime_ns, "painel_ifc": out}, ensure_ascii=False)); os.chmod(CACHE, 0o600)
        data = json.loads(PANEL.read_text(encoding="utf-8")); st2 = PANEL.stat()
        if st2.st_mtime_ns != st.st_mtime_ns: return 0  # publisher gravou no meio; a próxima rodada refaz
        data["painel_ifc"] = out
        fd, tmp = tempfile.mkstemp(prefix=".painel-enrich-", dir=PANEL.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as o:
                json.dump(data, o, ensure_ascii=False, separators=(",", ":")); o.write("\n"); o.flush(); os.fsync(o.fileno())
            os.chown(tmp, st.st_uid, st.st_gid); os.chmod(tmp, st.st_mode & 0o777); os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(tmp, PANEL)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        print(NOW.strftime("%d/%m %H:%M"), json.dumps(status, ensure_ascii=False))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
