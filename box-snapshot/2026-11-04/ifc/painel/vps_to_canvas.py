#!/usr/bin/env python3
"""Alimenta o Painel IFC com os dados da VPS — 100% determinístico, sem LLM.
Uso: python3 /home/box/ifc/painel/vps_to_canvas.py [--canvas-src ARQUIVO]
 1) baixa /opt/ifc-painel/published/dados.json (só leitura, via vpsp.py)
 2) converte cada seção para o formato do dados.json local (o que montar_canvas.py espera);
    onde a VPS não tem o dado, mantém o último valor bom e registra em relatorio.json
 3) monta a Page em vps_teste/painel_montado.tsx.txt (mesmo template/estilo)
 4) gera vps_teste/edits.json: só as linhas que mudaram versus a Page publicada (para EditCanvas)
 Não altera o dados.json local; ao final salva o resultado como último bom em vps_teste/dados.json."""
import json, subprocess, base64, gzip, os, shutil, sys, re, unicodedata, datetime as dt
P = "/home/box/ifc/painel/"; T = os.environ.get("VPS_OUT", P + "vps_teste/")
CANVAS = "/cursor/stores/store-ea3f629f-b3ff-4b52-8b2f-00f5e20767f2/canvases/71171228-d779-4ce3-a91c-b9ca79c7f788/source.canvas.tsx"
if "--canvas-src" in sys.argv: CANVAS = sys.argv[sys.argv.index("--canvas-src") + 1]
os.makedirs(T, exist_ok=True)
VELHO = open(CANVAS).read() if os.path.exists(CANVAS) else None
BRT = dt.timezone(dt.timedelta(hours=-3)); AGORA = dt.datetime.now(BRT)
MES = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
def rot(m): return MES[int(m[5:7]) - 1] + "/" + m[2:4]
def norm(s): return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower())
def iso2br(s):
    try: return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(BRT).strftime("%d/%m %H:%M")
    except Exception: return s or ""

# 1) VPS
VPS_SRC = sys.argv[sys.argv.index("--vps-path") + 1] if "--vps-path" in sys.argv else "/opt/ifc-painel/published/dados.json"
out = subprocess.run(["/workspace/.venv-ssh/bin/python", "/workspace/vpsp.py",
    "gzip -c %s | base64 -w0" % VPS_SRC], capture_output=True, text=True, timeout=120).stdout
V = json.loads(gzip.decompress(base64.b64decode(out.strip().splitlines()[-1])))
# último bom: resultado anterior deste script, senão o dados.json do bot IFC
base = T + "dados.json" if os.path.exists(T + "dados.json") else P + "dados.json"
L = json.load(open(base)); LOCAL = json.load(open(P + "dados.json"))
for k, v in LOCAL.items(): L.setdefault(k, v)
R = {"vps_atualizado_em": V.get("atualizado_em"), "base_ultimo_bom": base, "secoes": {}}
def marca(k, status, nota=""): R["secoes"][k] = status + (" — " + nota if nota else "")

# idade da VPS
try:
    vt = dt.datetime.strptime(V["atualizado_em"], "%d/%m/%Y às %H:%M").replace(tzinfo=BRT)
    idade_h = (AGORA - vt).total_seconds() / 3600
except Exception: idade_h = 99
R["idade_vps_horas"] = round(idade_h, 1); STALE = idade_h > 4

def same_shape(a, b):
    if type(a) is not type(b): return False
    if isinstance(a, dict): return set(a) <= set(b)
    if isinstance(a, list) and a and b and type(a[0]) is not type(b[0]): return False
    if isinstance(a, list) and a and isinstance(a[0], dict): return bool(b) and all(isinstance(y, dict) and set(a[0]) <= set(y) for y in b)
    return True

F = {}
if V.get("fontes") and isinstance(V["fontes"][0], dict):
    V["fontes"] = [[x.get("fonte", ""), "falhou" if re.search(r"falh|erro|indispon", str(x.get("status")), re.I) else "ok", (str(x.get("status") or "") + ". " + str(x.get("detalhe") or "")).strip(". ")] for x in V["fontes"]]
# seções que já têm o mesmo formato
for k in ["atualizado_em","hoje","escala_pregacao","escalas_ministerios","celulas","celulas_inativas","atividade_whatsapp_7d",
          "aniversariantes","pregacoes","videos","email_nota","acervo_sermoes","lideranca","escala_recepcao","fontes"]:
    if k in V and same_shape(L.get(k), V[k]): F[k] = V[k]; marca(k, "VPS")
    else: F[k] = L.get(k); marca(k, "mantido", "formato diferente/ausente")
F["atencao"] = [dict({"tom": ""}, **x) for x in V.get("atencao", [])] or L["atencao"]; marca("atencao", "VPS" if V.get("atencao") else "mantido")
ev = [{"data": e.get("date",""), "fim": e.get("end",""), "titulo": e.get("title",""), "tipo": "agenda"} for e in V.get("eventos", []) if e.get("status") != "cancelled"]
F["eventos"] = ev or L["eventos"]; marca("eventos", "VPS" if ev else "mantido")

# grupos + participantes
pg = V.get("participantes_grupos", {}); vg = pg.get("groups", [])
if vg:
    loc = {norm(g["nome"]): g for g in L["grupos"]["lista"]}
    lista = []
    for g in sorted(vg, key=lambda x: -x.get("t", 0)):
        o = loc.get(norm(g["g"]), {}); item = {"nome": g["g"], "categoria": o.get("categoria") or g.get("category", "IFC"), "n": g.get("t", 0)}
        if o.get("nota"): item["nota"] = o["nota"]
        lista.append(item)
    cats = {}
    for g in lista: c = cats.setdefault(g["categoria"], {"categoria": g["categoria"], "grupos": 0, "participacoes": 0}); c["grupos"] += 1; c["participacoes"] += g["n"]
    tot = dict(L["grupos"]["totais"]); tot.update({"grupos": len(lista), "participacoes": sum(g["n"] for g in lista)})
    if pg.get("unique_contacts"): tot["unicos_grupos"] = pg["unique_contacts"]
    F["grupos"] = {"lista": lista, "categorias": sorted(cats.values(), key=lambda c: -c["participacoes"]), "totais": tot}
    marca("grupos", "VPS", "homens_fora/agenda_fora/total_ifc_estimado mantidos do último bom")
    # participantes: nomes por grupo a partir dos contatos
    nomes = {}
    for c in pg.get("contatos", []):
        for g in c.get("grupos", []): nomes.setdefault(norm(g if isinstance(g, str) else g.get("g", "")), []).append(c.get("nome") or "")
    locp = {norm(g["g"]): g for g in L["participantes_grupos"]["grupos"]}; gs = []
    for g in vg:
        o = dict(locp.get(norm(g["g"]), {"g": g["g"], "c": g.get("category", "IFC"), "a": 0, "s": 0, "e": "", "n": []}))
        o["g"] = g["g"]; o["t"] = g.get("t", o.get("t", 0)); nn = sorted(x for x in nomes.get(norm(g["g"]), []) if x)
        if nn: o["n"] = nn
        gs.append(o)
    F["participantes_grupos"] = {"atualizado_em": (pg.get("generated_at") or "")[:16].replace("T", " "), "grupos": gs}
    marca("participantes_grupos", "VPS", "admins (a) e silenciosos (s) mantidos do último bom")
else:
    F["grupos"] = L["grupos"]; F["participantes_grupos"] = L["participantes_grupos"]; marca("grupos", "mantido"); marca("participantes_grupos", "mantido")

# destaques: frases da curadoria da VPS (grupos/mulheres) + anteriores
hoje = AGORA.strftime("%d/%m"); novos = []
for area, sec in [("Grupos", V.get("grupos", {})), ("Mulheres", V.get("mulheres", {}))]:
    for campo in ("decisoes", "fatos"):
        for t in (sec.get(campo) or [])[:4]: novos.append({"area": area, "data": hoje, "grupo": "", "texto": t})
for t in V.get("destaques", []) if isinstance(V.get("destaques"), list) else []:
    if isinstance(t, str): novos.append({"area": "Leitura pastoral", "data": hoje, "grupo": "", "texto": re.sub(r"^Inferência:\s*", "", t)})
vistos = set(); dest = []
for d in novos + L["destaques"]:
    if norm(d["texto"])[:60] in vistos: continue
    vistos.add(norm(d["texto"])[:60]); dest.append(d)
F["destaques"] = dest[:14]; marca("destaques", "VPS" if novos else "mantido", "novos da VPS no topo; anteriores logo abaixo")

# batismo
reg = (V.get("batismo") or {}).get("registros") or []
if reg:
    feitos = [r for r in reg if (r.get("situacao") or "Realizado").lower().startswith("realiz")]
    por = {}
    for r in feitos: a = r.get("ano") or "sem ano"; por[a] = por.get(a, 0) + 1
    por = dict(sorted(por.items(), key=lambda x: (x[0] == "sem ano", x[0])))
    datas = sorted(r["data_batismo"] for r in feitos if r.get("data_batismo")); ult = datas[-1] if datas else L["batismo"].get("ultimo_batismo")
    b = dict(L["batismo"]); b.update({"total": len(feitos), "com_nascimento": sum(1 for r in feitos if str(r.get("idade_no_batismo") or "").strip()),
        "por_ano": por, "ultimo_batismo": ult, "ultimo_grupo": sorted(r["nome"] for r in feitos if r.get("data_batismo") == ult)})
    F["batismo"] = b; marca("batismo", "VPS")
else: F["batismo"] = L["batismo"]; marca("batismo", "mantido")

# instagram
ig = (V.get("instagram") or {}).get("publications") or []
novos_ig = [{"data": (p.get("timestamp") or p.get("data") or "")[:10], "tipo": p.get("media_type") or p.get("tipo") or "Post",
             "resumo": (p.get("caption") or p.get("resumo") or "")[:90], "url": p.get("permalink") or p.get("url") or ""} for p in ig]
urls = {p["url"] for p in novos_ig}
F["instagram"] = novos_ig + [p for p in L["instagram"] if p["url"] not in urls]
marca("instagram", "VPS" if novos_ig else "mantido", "VPS sem publicações nas últimas 24h; lista anterior mantida" if not novos_ig else "")

# n8n: a VPS não publica o nome dos fluxos -> casa pela ordem só se a contagem bater
vn = V.get("n8n") or []
if vn and all(x.get("nome") for x in vn):
    F["n8n"] = [{"nome": x["nome"], "ativo": x.get("ativo"), "ultima": iso2br(x.get("ultima_execucao")) or None, "ultima_status": None,
                 "ok7": x.get("execucoes_7d", 0) - x.get("erros_7d", 0), "err7": x.get("erros_7d", 0),
                 "status": "erro" if x.get("erros_7d") else "ok", "nota": "", "erro": None} for x in vn]; marca("n8n", "VPS")
else:
    F["n8n"] = L["n8n"]; marca("n8n", "mantido", "VPS publica a saúde dos fluxos sem o nome (%d itens); lista nomeada do último bom" % len(vn))

# visitantes: Baserow da VPS está com bem menos cadastros que a base do painel -> não rebaixa
vv = V.get("visitantes") or {}
if vv.get("total", 0) >= L["visitantes"]["totais"]["total"]:
    lv = [{"nome": r.get("nome", ""), "data": r.get("data", ""), "vez": r.get("vez", "")} for r in vv.get("recentes", [])]
    F["visitantes"] = dict(L["visitantes"], totais={"total": vv["total"], "d30": vv.get("ultimos_30_dias", 0), "d7": vv.get("ultimos_7_dias", 0)}, lista=lv or L["visitantes"]["lista"])
    marca("visitantes", "VPS", "interação mantida do último bom")
else:
    F["visitantes"] = L["visitantes"]; marca("visitantes", "mantido", "VPS tem %s cadastro(s) vs %s no painel; não substituído" % (vv.get("total"), L["visitantes"]["totais"]["total"]))

# mulheres
vm = V.get("mulheres") or {}; m = dict(L["mulheres"])
if vm.get("sintese"):
    m["avisos"] = [x for x in (vm.get("pedidos") or []) + (vm.get("fatos") or [])][:6]
    m["pendentes"] = (vm.get("proximos_passos") or [])[:6]
    m["atualizado_em"] = AGORA.strftime("%Y-%m-%d %H:%M"); m["fonte"] = "VPS (curadoria do grupo Mulheres IFC)"
    marca("mulheres", "VPS", "eventos e participantes mantidos do último bom")
else: marca("mulheres", "mantido")
F["mulheres"] = m

# financeiro
vf = V.get("financeiro") or {}; mens = sorted(vf.get("mensal") or [], key=lambda x: x["mes"])
if mens:
    si = vf.get("saldo_inicial") or {}; meses = []; saldo = None
    for x in mens:
        ano = x["mes"][:4]
        if x["mes"].endswith("-12") and str(int(ano) + 1) in si: s_fim = si[str(int(ano) + 1)]
        else: s_fim = None
        if x["mes"].endswith("-01") and ano in si: saldo = si[ano]
        res = round(x["entradas"] - x["saidas"], 2)
        if saldo is not None: saldo = round(saldo + res + x.get("neutras", 0), 2)
        if s_fim is not None: saldo = s_fim
        meses.append({"mes": x["mes"], "rotulo": rot(x["mes"]), "entradas": round(x["entradas"], 2), "saidas": round(x["saidas"], 2), "resultado": res, "saldo": saldo, "n": x["n"]})
    am = AGORA.strftime("%Y-%m"); atual = next((x for x in meses if x["mes"] == am), {"mes": am, "rotulo": rot(am), "entradas": 0, "saidas": 0, "resultado": 0, "saldo": None, "n": 0})
    ant_m = (AGORA.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m"); ant = next((x for x in meses if x["mes"] == ant_m), meses[-1])
    f = dict(L["financeiro"]); f.update({"ultimo_lancamento": vf.get("max_data", f["ultimo_lancamento"]), "ultima_importacao": iso2br(re.sub(r"(\.\d+)?\+00$", "+00:00", (vf.get("ult_import") or "").replace(" ", "T"))) if vf.get("ult_import") else f["ultima_importacao"],
        "atual": atual, "anterior": ant, "ultimo": meses[-1], "penultimo": meses[-2] if len(meses) > 1 else meses[-1], "doze": meses[-12:]})
    F["financeiro"] = f; marca("financeiro", "VPS", "categorias detalhadas e maiores lançamentos mantidos do último bom (a VPS só publica categorias agregadas)")
else: F["financeiro"] = L["financeiro"]; marca("financeiro", "mantido")

# louvor
vl = V.get("escala_louvor") or []
if vl:
    locl = {x["data"]: x for x in L["escala_louvor"]}; j = lambda a: ", ".join(a or [])
    for x in vl:
        o = dict(locl.get(x["data"], {"data": x["data"], "ministro": "", "obs": "", "fonte": ""}))
        fun = " · ".join(p for p in [("Técnico de som: " + j(x.get("tecnicos"))) if x.get("tecnicos") else "", ("Mídia: " + j(x.get("projecao"))) if x.get("projecao") else "", ("Transmissão: " + j(x.get("transmissao"))) if x.get("transmissao") else ""] if p)
        o.update({"vocais": j(x.get("vocais")) or o.get("vocais", ""), "instrumentos": j(x.get("instrumentos")) or o.get("instrumentos", ""), "funcoes": fun or o.get("funcoes", ""), "fonte": o.get("fonte") or "VPS (grupo do Louvor)"})
        locl[x["data"]] = o
    F["escala_louvor"] = [locl[k] for k in sorted(locl)]; marca("escala_louvor", "VPS", "datas da VPS atualizadas; ministro e instrumentos por função mantidos do último bom quando a VPS não traz")
else: F["escala_louvor"] = L["escala_louvor"]; marca("escala_louvor", "mantido")

# mensagens diretas
cp = V.get("conversas_por_pessoa") or []; vd = V.get("diretas") or {}
if cp:
    locd = {norm(r["nome"]): r for r in L["diretas"]["remetentes"]}; rem = []
    for c in cp:
        o = locd.get(norm(c.get("person")), {}); pend = []
        st = c.get("status") or ""
        if re.search(r"não confirmad|pendente|aguard", st, re.I): pend = [st.split(". ")[0].rstrip(".") + "."]
        rem.append({"nome": c.get("person", ""), "papel": o.get("papel", ""), "ilegiveis": 0, "ultima": o.get("ultima", ""), "n_msgs": o.get("n_msgs", 0),
                    "assuntos": [x for x in [c.get("request"), c.get("response")] if x], "pendencias": pend})
    d = dict(L["diretas"]); d.update({"atualizado_em": AGORA.strftime("%d/%m %H:%M"), "conversas_com_mensagem": len(rem), "remetentes": rem,
        "pendencias": (vd.get("proximos_passos") or [])[:6], "nota": vd.get("sintese") or d.get("nota", "")})
    F["diretas"] = d; marca("diretas", "VPS", "papel, horário da última mensagem e contagem mantidos do último bom")
else: F["diretas"] = L["diretas"]; marca("diretas", "mantido")

# livro de oração
vo = V.get("livro_oracao") or {}; ped = vo.get("pedidos") or []
if ped:
    campos = ("data","grupo","quem","pedido","data_atualizacao","atualizacao","data_resolucao","resolucao","status")
    ped = [{k: p.get(k, "") for k in campos} for p in ped]; porg = {}
    for p in ped: p["grupo"] = re.split(r" \(também em", p["grupo"])[0]
    for p in ped: porg[p["grupo"]] = porg.get(p["grupo"], 0) + 1
    st = lambda s: sum(1 for p in ped if p["status"].lower().startswith(s))
    F["livro_oracao"] = dict(L["livro_oracao"], atualizado_em=(vo.get("generated_at") or "")[:16], pedidos=ped,
        totais={"pedidos": len(ped), "em_oracao": st("em ora"), "atualizados": sum(1 for p in ped if p["atualizacao"]), "respondidos": st("respond"),
                "por_grupo": dict(sorted(porg.items(), key=lambda x: -x[1]))})
    marca("livro_oracao", "VPS", "link da planilha e 'histórico desde' mantidos")
else: F["livro_oracao"] = L["livro_oracao"]; marca("livro_oracao", "mantido")

# ---- enriquecimento da VPS (painel_ifc, gerado por panel_enrich.py) tem prioridade ----
PI = V.get("painel_ifc") or {}; PST = PI.get("status", {})
def ok_(k): return k in PI and str(PST.get(k, "ok")).startswith("ok")
if ok_("visitantes"):
    vi = PI["visitantes"]; F["visitantes"] = dict(L["visitantes"], totais=vi["totais"], lista=vi["lista"])
    marca("visitantes", "VPS", "Supabase; bloco 'interação' (respostas no WhatsApp) mantido do último bom: a tabela não registra envio/resposta")
if ok_("instagram") and PI["instagram"]:
    urls = {p["url"] for p in PI["instagram"]}; F["instagram"] = PI["instagram"] + [p for p in L["instagram"] if p["url"] not in urls][:max(0, 12 - len(PI["instagram"]))]
    marca("instagram", "VPS", "últimas %d publicações" % len(PI["instagram"]))
if ok_("n8n") and PI["n8n"]:
    notas = {x["nome"]: x.get("nota", "") for x in L["n8n"]}
    F["n8n"] = [dict(x, nota=x.get("nota") or notas.get(x["nome"], "")) for x in PI["n8n"]]; marca("n8n", "VPS", "nomes + saúde via API n8n (GET)")
if ok_("financeiro_detalhe"):
    fd = PI["financeiro_detalhe"]; f = F["financeiro"]
    meses = sorted(m for m in fd["categorias"] if m in {x["mes"] for x in f.get("doze", []) if x.get("n")})[-6:]
    if meses:
        f["meses_com_dados"] = [{"value": m, "label": rot(m)} for m in meses]
        f["categorias"] = {m: fd["categorias"][m] for m in meses}; f["maiores"] = {m: fd["maiores"].get(m, []) for m in meses}
        marca("financeiro", "VPS", "inclui categorias e maiores lançamentos")
if ok_("louvor") and PI["louvor"]:
    por = {x["data"]: x for x in F["escala_louvor"]}
    for x in PI["louvor"]: por[x["data"]] = x
    F["escala_louvor"] = [por[k] for k in sorted(por)]; marca("escala_louvor", "VPS", "posts do grupo do Louvor (ministro, vocais, instrumentos, funções)")
if ok_("participantes"):
    pp = {norm(x["g"]): x for x in PI["participantes"] if not x.get("erro")}
    for g in F["participantes_grupos"]["grupos"]:
        x = pp.get(norm(g["g"]))
        if not x: continue
        g["t"], g["a"], g["s"] = x["t"], x["a"], x["s"]; adm = set(x.get("admins") or [])
        if adm and g.get("n"): g["n"] = sorted({("*" + n if n in adm else n) for n in (y.lstrip("*") for y in g["n"])} | {"*" + a for a in adm if a not in {y.lstrip("*") for y in g["n"]}}, key=lambda n: (not n.startswith("*"), n.casefold()))
    marca("participantes_grupos", "VPS", "totais, admins e sem nome via Evolution (GET)")
    mx = pp.get(norm("Mulheres IFC"))
    if mx: F["mulheres"]["participantes"] = {"total": mx["t"], "admins": mx["a"]}
if ok_("mulheres_stats"): F["mulheres"].update(PI["mulheres_stats"])
evm = [{"id": "agenda-" + e["data"], "data": e["data"][:10], "hora": "", "titulo": e["titulo"], "local": "", "obs": "", "fonte": "Agenda da secretaria", "postado": ""} for e in F["eventos"] if re.search(r"mulher", e["titulo"], re.I)]
if evm:
    ja = {x["data"] for x in F["mulheres"].get("eventos", [])}; F["mulheres"]["eventos"] = F["mulheres"].get("eventos", []) + [x for x in evm if x["data"] not in ja]
F["mulheres"]["eventos"] = [x for x in F["mulheres"].get("eventos", []) if x.get("data", "") >= AGORA.strftime("%Y-%m-%d")] or F["mulheres"].get("eventos", [])
if "mulheres_stats" in PI or "participantes" in PI: marca("mulheres", "VPS", "curadoria + participantes/mensagens; eventos = agenda da secretaria + último bom")
if ok_("diretas_stats"):
    ds = PI["diretas_stats"]; pp = {norm(k): v for k, v in ds["por_pessoa"].items()}
    for r in F["diretas"]["remetentes"]:
        x = pp.get(norm(r["nome"]))
        if x: r.update(papel=x["papel"] or r["papel"], n_msgs=x["n_msgs"], ultima=x["ultima"])
    F["diretas"]["monitorados"] = ds.get("monitorados") or F["diretas"].get("monitorados"); F["diretas"]["janela"] = ds.get("janela") or F["diretas"].get("janela")
    marca("diretas", "VPS", "papel, horário e contagem do staging de mensagens diretas")
R["enriquecimento_vps"] = PST

# aviso de dados velhos + fonte
if STALE:
    F["atencao"] = [{"titulo": "Dados da VPS com mais de 4h", "detalhe": "A última publicação da VPS foi em %s. O painel mostra o último dado bom." % V.get("atualizado_em"), "tom": "warning", "area": "Sistema"}] + F["atencao"]
F["fontes"] = [["Dados da VPS (published/dados.json)", "ok" if not STALE else "atrasado",
                "Publicado pela VPS em %s; convertido sem IA às %s." % (V.get("atualizado_em"), AGORA.strftime("%d/%m %H:%M"))]] + [f for f in (F.get("fontes") or []) if not str(f[0]).startswith("Dados da VPS")]
for k in LOCAL:
    if k not in F: F[k] = L.get(k); marca(k, "mantido", "sem conversor")
json.dump(F, open(T + "dados.json", "w"), ensure_ascii=False)

# 3) monta
for f in ("template_canvas.tsx.txt", "integrantes_ata2026.json"):
    if os.path.exists(P + f): shutil.copy(P + f, T + f)
src = open(P + "montar_canvas.py").read().replace('P = "/home/box/ifc/painel/"', 'P = "%s"' % T)
G = {"__name__": "__main__"}; exec(compile(src, "montar_canvas", "exec"), G)
# re-renderiza com um item por linha em qualquer nível (diffs pequenos para EditCanvas); JSON com quebras de linha é JS válido
def fino(o, nivel=0):
    if isinstance(o, dict) and nivel < 3 and any(isinstance(v, (dict, list)) for v in o.values()):
        return "{\n" + ",\n".join(json.dumps(k, ensure_ascii=False) + ":" + fino(v, nivel + 1) for k, v in o.items()) + "}"
    if isinstance(o, list) and nivel < 3 and o and isinstance(o[0], (dict, list)):
        return "[\n" + ",\n".join(fino(x, 9) for x in o) + "]"
    if isinstance(o, dict) and nivel >= 9 and any(isinstance(v, list) and len(v) > 8 for v in o.values()):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + (("[\n" + ",\n".join(json.dumps(x, ensure_ascii=False) for x in v) + "]") if isinstance(v, list) and len(v) > 8 else json.dumps(v, ensure_ascii=False, separators=(",", ":"))) for k, v in o.items()) + "}"
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))
tpl = open(T + "template_canvas.tsx.txt").read()
open(T + "painel_montado.tsx.txt", "w").write(tpl.replace("__DADOS__", fino(G["d"])).replace("__LIVRO__", fino(G["LO"])).replace("__ATA2026__", G["compact"](G["ATA"])))
os.chmod(T + "painel_montado.tsx.txt", 0o600); os.chmod(T + "dados.json", 0o600)
novo = open(T + "painel_montado.tsx.txt").read()

# 4) edits mínimos (linha a linha) contra a Page publicada
import difflib
edits = []
if VELHO is not None:
    velho = VELHO; a, b = velho.split("\n"), novo.split("\n")
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal": continue
        lo, hi = i1, i2
        while True:  # amplia o trecho até ficar único na Page atual
            old = "\n".join(a[lo:hi]); 
            if old and velho.count(old) == 1: break
            if lo > 0: lo -= 1
            if hi < len(a) and velho.count("\n".join(a[lo:hi])) != 1: hi += 1
            if lo == 0 and hi == len(a): break
        new = "\n".join(a[lo:i1] + b[j1:j2] + a[i2:hi])
        edits.append({"old_string": old, "new_string": new})
    json.dump({"canvas_id": "71171228-d779-4ce3-a91c-b9ca79c7f788", "edits": edits}, open(T + "edits.json", "w"), ensure_ascii=False)
    os.chmod(T + "edits.json", 0o600)
    R["edits"] = len(edits); R["edits_bytes"] = os.path.getsize(T + "edits.json")
R["page_bytes"] = len(novo.encode())
R["modo"] = "EditCanvas(edits.json)" if R.get("edits_bytes", 1e9) < 0.6 * R["page_bytes"] else "WriteCanvas(painel_montado.tsx.txt) — diferença grande demais"
R["dados_bytes"] = os.path.getsize(T + "dados.json"); R["llm_calls"] = 0
json.dump(R, open(T + "relatorio.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False, indent=1))
