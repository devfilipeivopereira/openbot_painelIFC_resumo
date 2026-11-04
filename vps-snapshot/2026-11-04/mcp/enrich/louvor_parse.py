#!/usr/bin/env python3
"""Escala do Ministério de Louvor (bandas por culto) — SOMENTE LEITURA.
Uso: set -a; . /home/box/secrets/vps.env; set +a; python3 coletar_louvor.py [dias=7]   (carga inicial: 60)
Fonte: grupo WhatsApp "Ministério de Louvor IFC" (<JID-REDACTED>@g.us), instância Evolution "Filipe".
Só POST /chat/findMessages (e, para mídia pendente, nada é baixado aqui). NUNCA envia, responde, reage nem marca como lida.
Lê posts de escala em texto (linhas "➡️ DD/MM - Ministro; vocal: ...; teclado: ...") e funde em escala_louvor.json
(por data: vale o post mais recente). Imagens/PDFs sem texto entram em raw/louvor_midia_pendente.json para o agente ver.
Sem telefones. Imprime as datas novas/alteradas."""
import os, sys, re, json, time, datetime as dt, requests
P = "/home/box/ifc/painel/"
JID = "<JID-REDACTED>@g.us"
B = os.environ.get("EVOLUTION_BASE_URL", "https://evolution.filipeivopereira.com").rstrip("/")
DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 7

ROLES = [  # (regex do rótulo, campo, nome do instrumento/função)
    (r"viol[aã]o e (voz|vocal)", "violao_voz", "Violão e voz"), (r"teclado e (voz|vocal)", "teclado_voz", "Teclado e voz"),
    (r"vocais|vocal|voz|backs?", "vocais", None), (r"viol[aã]o", "inst", "Violão"), (r"teclado", "inst", "Teclado"),
    (r"guitarra", "inst", "Guitarra"), (r"baixo", "inst", "Baixo"), (r"bateria", "inst", "Bateria"), (r"cajon", "inst", "Cajón"),
    (r"sax\w*", "inst", "Sax"), (r"t[eé]c(\.|nico)? de som|som", "func", "Técnico de som"), (r"m[ií]dia|projeç[aã]o|telão", "func", "Mídia"),
    (r"transmiss[aã]o", "func", "Transmissão"), (r"ministro|ministra|louvor", "ministro", None)]
ROLE_RE = "|".join(r for r, _, _ in ROLES)

def limpa(s): return re.sub(r"\s+", " ", s.replace("*", "")).strip(" .;,-–")
def nomes(v):
    v = limpa(v)
    if v in ("(?)", "?", ""): return ["a definir"]
    return [limpa(x) for x in re.split(r"\s+e\s+|/|&", v) if limpa(x)]

def parse_linha(resto):
    e = {"ministro": "", "vocais": [], "instrumentos": [], "funcoes": [], "obs": []}
    for seg in re.split(r"[;,]", resto):
        seg = seg.strip()
        if not limpa(seg): continue
        if re.fullmatch(r"\*?\s*sem\s+\w+\s*\*?", seg.strip(), re.I): e["obs"].append(limpa(seg)); continue
        m = re.match(rf"^\s*\*?\s*({ROLE_RE})\b\s*\*?\s*(?::|-|–)?\s*(.*)$", seg, re.I)
        if m and limpa(m.group(len(m.groups()))):
            rot, val = m.group(1), m.group(len(m.groups()))
            for rx, campo, nome in ROLES:
                if re.fullmatch(rx, rot, re.I): break
            ns = nomes(val)
            if campo == "vocais": e["vocais"] += ns
            elif campo == "ministro": e["ministro"] = ", ".join(ns)
            elif campo in ("violao_voz", "teclado_voz"):
                e["instrumentos"].append({"instrumento": nome, "nomes": ns})
            elif campo == "inst": e["instrumentos"].append({"instrumento": nome, "nomes": ns})
            else: e["funcoes"].append({"funcao": nome, "nomes": ns})
            continue
        t = re.match(r"^\s*\*([^*]+)\*\s*(?:-|–)\s*(.*)$", seg)  # "*Culto de batismo* - Gabriel"
        if t: e["obs"].append(limpa(t.group(1))); seg = t.group(2)
        ns = nomes(seg)
        if not e["ministro"]: e["ministro"] = ns[0]; e["vocais"] += ns[1:]
        else: e["vocais"] += ns
    e["obs"] = " · ".join(e["obs"])
    return e

def parse_post(txt, ts):
    pd = dt.date.fromtimestamp(ts); out = []
    for l in txt.splitlines():
        m = re.match(r"^\s*(?:➡️|➡|•|-)?\s*(\d{1,2})/(\d{1,2})\s*(?:-|–)\s*(.+)$", l)
        if not m or not re.search(r"vocal|voz|teclado|bateria|guitarra|baixo|som|m[ií]dia|transmiss", m.group(3), re.I): continue
        d, mo = int(m.group(1)), int(m.group(2)); y = pd.year + (1 if mo < pd.month - 6 else 0)
        try: data = dt.date(y, mo, d).isoformat()
        except ValueError: continue
        out.append({"data": data, **parse_linha(m.group(3))})
    return out

def texto(m):
    g = m.get("message") or {}
    return g.get("conversation") or (g.get("extendedTextMessage") or {}).get("text") or (g.get("imageMessage") or {}).get("caption") or (g.get("documentMessage") or {}).get("caption") or ""

def coletar():
    H = {"apikey": os.environ["EVOLUTION_API_KEY"]}; corte = time.time() - DIAS * 86400; recs = []
    for page in range(1, 200):
        for att in range(3):
            try:
                r = requests.post(f"{B}/chat/findMessages/Filipe", json={"where": {"key": {"remoteJid": JID}}, "limit": 50, "page": page}, headers=H, timeout=120)
                r.raise_for_status(); rr = r.json()["messages"]["records"]; break
            except Exception:
                if att == 2: raise
                time.sleep(5)
        recs += rr
        if not rr or min(int(x.get("messageTimestamp") or 0) for x in rr) < corte: break
    return [x for x in recs if int(x.get("messageTimestamp") or 0) >= corte]

if __name__ == "__main__":
    arq = P + "escala_louvor.json"
    est = json.load(open(arq)) if os.path.exists(arq) else {"cultos": []}
    por = {c["data"]: c for c in est["cultos"]}
    antes = json.dumps(por, sort_keys=True, ensure_ascii=False)
    recs = sorted(coletar(), key=lambda x: int(x["messageTimestamp"]))
    pend, mudou = [], set(); conf = set(est.get("midia_conferida", []))
    for x in recs:
        ts = int(x["messageTimestamp"]); t = texto(x); tipo = x.get("messageType", "")
        for e in parse_post(t, ts):
            old = por.get(e["data"])
            if old and (old.get("manual") or old.get("fonte", {}).get("ts", 0) > ts): continue
            e["fonte"] = {"post": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"), "autor": x.get("pushName") or "", "ts": ts, "id": x["key"]["id"]}
            if not old or {k: v for k, v in old.items() if k != "fonte"} != {k: v for k, v in e.items() if k != "fonte"}: mudou.add(e["data"])
            por[e["data"]] = e
        if tipo in ("imageMessage", "documentMessage", "documentWithCaptionMessage") and not parse_post(t, ts) and \
           (re.search(r"escala", t, re.I) or ("Liene" in (x.get("pushName") or "") and not t)) and x["key"]["id"] not in conf:
            pend.append({"id": x["key"]["id"], "post": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"), "autor": x.get("pushName"), "tipo": tipo, "legenda": t[:200]})
    est = {"_comentario": "Escala do Ministério de Louvor por culto (grupo WhatsApp Ministério de Louvor IFC). ministro = ministro de louvor. Gerado por coletar_louvor.py; correções manuais: edite e marque \"manual\": true (não é sobrescrito por posts mais antigos).",
           "atualizado_em": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "grupo": "Ministério de Louvor IFC", "jid": JID,
           "cultos": sorted(por.values(), key=lambda c: c["data"]),
           "midia_conferida": sorted(conf)}  # ids de imagens já vistas pelo agente (não eram escala); acrescente após conferir
    json.dump(est, open(arq, "w"), ensure_ascii=False, indent=1)
    json.dump(pend, open(P + "raw/louvor_midia_pendente.json", "w"), ensure_ascii=False, indent=1)
    print("datas novas/alteradas:", ", ".join(sorted(mudou)) or "nenhuma", "| mídia a conferir:", len(pend))
