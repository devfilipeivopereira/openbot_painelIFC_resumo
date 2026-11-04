# Canvas LEVE do Painel IFC (executado por vps_to_canvas.py com os globais F, V, R, T, AGORA, VELHO).
# Saída em T/leve/: painel_leve.tsx.txt (bootstrap), edits_001.json (atualização), relatorio em R.
# Regras: só atualiza quando algo IMPORTANTE muda (impressão digital "FP" sem campos voláteis);
# na rodada das 14h só publica se houver alerta novo ("pular" caso contrário).
import json, os, re, hashlib, datetime as _dt, subprocess
LV = T + "leve/"; os.makedirs(LV, exist_ok=True)
VPS_URL = "https://openclaw.filipeivopereira.com/ifc-painel/"
CID = "71171228-d779-4ce3-a91c-b9ca79c7f788"
hoje = AGORA.date(); h7 = hoje + _dt.timedelta(days=7)
def d_(s):
    try: return _dt.date.fromisoformat(str(s)[:10])
    except Exception: return None
def br(s): x = d_(s); return x.strftime("%d/%m") if x else str(s or "")
A = lambda k, dflt=None: F.get(k) if F.get(k) is not None else dflt
# ---------- dados ----------
aten = [{"area": a.get("area", ""), "titulo": a.get("titulo", ""), "detalhe": a.get("detalhe", ""), "tom": a.get("tom", "")} for a in A("atencao", [])][:8]
pend = []
for r_ in (A("diretas", {}) or {}).get("remetentes", []):
    for p in r_.get("pendencias", []) or []:
        pend.append({"de": r_.get("nome", ""), "pedido": (p.get("pedido") if isinstance(p, dict) else str(p))[:180]})
for p in ((A("mulheres", {}) or {}).get("pendentes") or [])[:4]:
    pend.append({"de": "Mulheres IFC", "pedido": str(p.get("resumo", ""))[:180]})
vis = A("visitantes", {}) or {}; vt = vis.get("totais", {})
vnov = [{"nome": x.get("nome", ""), "data": br(x.get("data")), "vez": x.get("vez", "")} for x in vis.get("lista", []) if d_(x.get("data")) and (hoje - d_(x.get("data"))).days <= 7][:6]
agenda = [{"data": br(e.get("data")), "titulo": e.get("titulo", "")} for e in A("eventos", []) if d_(e.get("data")) and hoje <= d_(e.get("data")) <= h7][:8]
for e in A("escala_pregacao", []):
    if d_(e.get("data")) and hoje <= d_(e.get("data")) <= h7: agenda.append({"data": br(e["data"]), "titulo": "Prega: " + e.get("pregador", "") + (" · abertura " + e["abertura"] if e.get("abertura") else "")})
aniv = [x.get("nome", "").split(" ")[0] + " " + br(x.get("data")) for x in A("aniversariantes", []) if d_(x.get("data")) and hoje <= d_(x.get("data")) <= h7][:10]
dest = [x.get("texto", "") if isinstance(x, dict) else str(x) for x in A("destaques", [])][:3]
fi = A("financeiro", {}) or {}; ult = fi.get("ultimo") or {}
contas = {"nota": "A VPS ainda não tem uma fonte de contas a vencer; mostrando o último mês fechado.", "mes": ult.get("rotulo", ""),
          "entradas": ult.get("entradas"), "saidas": ult.get("saidas"), "resultado": ult.get("resultado"), "saldo": ult.get("saldo")}
g = (A("grupos", {}) or {}).get("totais", {}); lo = (A("livro_oracao", {}) or {}).get("totais", {})
n8 = A("n8n", []); n8a = [x.get("nome", "") for x in n8 if x.get("status") not in ("ok", None)]
mu = A("mulheres", {}) or {}; di = A("diretas", {}) or {}
res = [
 {"t": "Grupos e participantes", "v": f'{g.get("grupos", 0)} grupos, {g.get("unicos_grupos", 0)} pessoas, {g.get("participacoes", 0)} participações; {g.get("homens_fora", 0)} homens e {g.get("agenda_fora", 0)} contatos da agenda fora dos grupos (total IFC estimado {g.get("total_ifc_estimado", 0)}).'},
 {"t": "Livro de Oração", "v": f'{lo.get("pedidos", 0)} pedidos: {lo.get("em_oracao", 0)} em oração, {lo.get("atualizados", 0)} atualizados, {lo.get("respondidos", 0)} respondidos.'},
 {"t": "Visitantes", "v": f'{vt.get("total", 0)} no total, {vt.get("d30", 0)} em 30 dias, {vt.get("d7", 0)} em 7 dias.'},
 {"t": "Financeiro", "v": f'{ult.get("rotulo", "")}: entradas R$ {ult.get("entradas") or 0:,.2f}, saídas R$ {ult.get("saidas") or 0:,.2f}, resultado R$ {ult.get("resultado") or 0:,.2f}.'.replace(",", "X").replace(".", ",").replace("X", ".")},
 {"t": "Mulheres IFC", "v": f'{(mu.get("participantes") or {}).get("total", 0)} participantes; {mu.get("mensagens_lidas", 0)} mensagens lidas na última coleta.'},
 {"t": "Mensagens diretas de líderes", "v": f'{di.get("conversas_com_mensagem", 0)} conversas com mensagem de {di.get("monitorados", 0)} monitoradas.'},
 {"t": "Automações (n8n)", "v": f'{len(n8)} fluxos; {len(n8a)} com atenção' + (": " + ", ".join(n8a) if n8a else ".")},
]
num = [{"v": str(len(aten)), "l": "Atenção agora"}, {"v": str(len(pend)), "l": "Esperam sua resposta"}, {"v": str(vt.get("d7", 0)), "l": "Visitantes (7 dias)"},
       {"v": str(lo.get("em_oracao", 0)), "l": "Pedidos em oração"}, {"v": str(g.get("unicos_grupos", 0)), "l": "Pessoas nos grupos"}]
IMPORT = {"aten": aten, "pend": pend, "vnov": vnov, "agenda": agenda, "dest": dest, "contas": contas, "n8a": n8a}
FP = hashlib.sha256(json.dumps(IMPORT, ensure_ascii=True, sort_keys=True).encode()).hexdigest()[:12]
DATA = {"meta": {"atualizado": V.get("atualizado_em") or AGORA.strftime("%d/%m %H:%M"), "url": VPS_URL}, "num": num, "dest": dest, "aten": aten, "pend": pend,
        "vnov": vnov, "agenda": agenda, "aniv": aniv, "contas": contas, "res": res}
def linhas(o):  # um item por linha, ASCII, ordem estável; cada linha única (prefixo de chave + índice)
    out = []
    for k, v in o.items():
        if isinstance(v, list):
            out.append(json.dumps(k) + ": [")
            for i, x in enumerate(v): out.append("/*%s%d*/" % (k, i) + json.dumps(x, ensure_ascii=True) + ",")
            out.append("],")
        else: out.append(json.dumps(k) + ": " + json.dumps(v, ensure_ascii=True) + ",")
    return "\n".join(out)
TPL = r'''import { Page, Section, Text, Rows, Metrics, Callout, LinkCards, BulletList } from "grok/canvas";
type Tom = "danger" | "warning" | "success" | "info";
type Item = { [k: string]: any };
/*FP:__FP__*/
/*LEVE_INICIO*/
const L: { meta: { atualizado: string; url: string }; num: { v: string; l: string }[]; dest: string[]; aten: Item[]; pend: Item[]; vnov: Item[]; agenda: Item[]; aniv: string[]; contas: Item; res: { t: string; v: string }[] } = {
__DATA__
};
/*LEVE_FIM*/
export default function App() {
  return (
    <Page title="Painel IFC" summary={"Resumo do dia. O painel completo, com listas, Livro de Oração, Ata e financeiro, fica na VPS. Dados de " + L.meta.atualizado + "."} width="wide" palette={["#6D2FCB"]}>
      <LinkCards links={[{ title: "Abrir o Painel IFC completo (VPS)", site: "openclaw.filipeivopereira.com, com senha", href: L.meta.url }]} />
      <Metrics metrics={L.num.map((n) => ({ value: n.v, label: n.l }))} />
      <Section heading="Destaque do dia" collapsible defaultOpen>
        {L.dest.length ? <BulletList items={L.dest} /> : <Text>Nada de destaque nesta coleta.</Text>}
      </Section>
      <Section heading="Precisa da sua resposta" collapsible defaultOpen>
        {L.pend.length ? <Rows rows={L.pend.map((p) => ({ title: String(p.pedido), meta: String(p.de) }))} /> : <Callout tone="success">Ninguém aguardando resposta sua agora.</Callout>}
      </Section>
      <Section heading="Atenção agora" collapsible defaultOpen>
        {L.aten.length ? <Rows rows={L.aten.map((a) => ({ title: String(a.titulo), detail: String(a.detalhe), meta: String(a.area), status: a.tom === "danger" ? { label: "Urgente", tone: "danger" as Tom } : a.tom === "warning" ? { label: "Acompanhar", tone: "warning" as Tom } : undefined }))} /> : <Callout tone="success">Nenhum alerta.</Callout>}
      </Section>
      <Section heading="Visitantes novos (7 dias)" collapsible defaultOpen>
        {L.vnov.length ? <Rows rows={L.vnov.map((v) => ({ title: String(v.nome), meta: String(v.data), detail: String(v.vez) }))} /> : <Text>Nenhum visitante novo nos últimos 7 dias.</Text>}
      </Section>
      <Section heading="Contas e dinheiro" collapsible defaultOpen>
        <Text>{String(L.contas.nota)}</Text>
        <Metrics metrics={[{ value: String(L.contas.mes), label: "Mês" }, { value: "R$ " + Number(L.contas.entradas || 0).toLocaleString("pt-BR"), label: "Entradas" }, { value: "R$ " + Number(L.contas.saidas || 0).toLocaleString("pt-BR"), label: "Saídas" }, { value: "R$ " + Number(L.contas.resultado || 0).toLocaleString("pt-BR"), label: "Resultado" }]} />
      </Section>
      <Section heading="Agenda (próximos 7 dias)" collapsible defaultOpen>
        {L.agenda.length ? <Rows rows={L.agenda.map((e) => ({ title: String(e.titulo), meta: String(e.data) }))} /> : <Text>Nada na agenda nos próximos 7 dias.</Text>}
        {L.aniv.length ? <Text>{"Aniversários: " + L.aniv.join(", ")}</Text> : null}
      </Section>
      <Section heading="Resumos (totais)" collapsible>
        <Rows rows={L.res.map((r) => ({ title: r.t, detail: r.v }))} />
        <Text>Listas completas (participantes, pedidos de oração, interação com visitantes, Ata e lançamentos) no painel da VPS.</Text>
      </Section>
    </Page>
  );
}
'''
def asc(s): return s.encode("ascii", "backslashreplace").decode() if False else "".join(c if ord(c) < 128 else ("\\u%04x" % ord(c) if ord(c) < 0x10000 else "".join("\\u%04x" % u for u in __import__("struct").unpack("<2H", c.encode("utf-16-le")))) for c in s)
# template ASCII: textos JSX com acento viram {"\uXXXX"} via expressões — mais simples: manter UTF-8 só no template fixo (não muda), dados em ASCII.
TPLH = hashlib.sha256(TPL.encode()).hexdigest()[:10]
TA_ = P + "tools/tpl_leve.%s.txt" % TPLH
if not os.path.exists(TA_):
    open("/tmp/tpl_leve_in.tsx", "w").write(TPL.replace("__DATA__", '"__DATA__": 0'))
    subprocess.run(["node", P + "tools/asciify.js", "/tmp/tpl_leve_in.tsx", TA_], cwd=P + "tools", check=True, capture_output=True)
TPLA = open(TA_).read()
PAGE = TPLA.replace("__FP__", FP + " T" + TPLH).replace('"__DATA__": 0', linhas(DATA))
assert all(ord(c) < 128 for c in PAGE), "canvas leve com caractere nao-ASCII"
open(LV + "painel_leve.tsx.txt", "w").write(PAGE)
# compila (tsc strict) num diretório separado
TC = P + "tools/tc_leve/"; os.makedirs(TC, exist_ok=True)
for f_ in ("grok-canvas.d.ts",): open(TC + f_, "w").write(open(P + "tools/tc/" + f_).read())
open(TC + "tsconfig.json", "w").write(open(P + "tools/tc/tsconfig.json").read().replace('"typeRoots":["../node_modules/@types"]', '"typeRoots":["../node_modules/@types"]'))
open(TC + "page.tsx", "w").write(PAGE)
rc = subprocess.run([P + "tools/node_modules/.bin/tsc", "-p", "tsconfig.json"], cwd=TC, capture_output=True, text=True)
R["leve_compila"] = "ok" if rc.returncode == 0 else (rc.stdout + rc.stderr)[:600]
R["leve_base_bytes"] = len(PAGE.encode()); R["leve_dados_ascii"] = all(ord(c) < 128 for c in linhas(DATA))
for f_ in os.listdir(LV):
    if f_.startswith("edits_"): os.remove(LV + f_)
# ---------- decisão ----------
live = VELHO or ""
mfp = re.search(r"/\*FP:([0-9a-f]{12}) T([0-9a-f]{10})\*/", live)
alerta_novo = False
if mfp:
    mb = live.split("/*LEVE_INICIO*/")[-1].split("/*LEVE_FIM*/")[0]
    velhos = set(re.findall(r'/\*(?:aten|pend|vnov)\d+\*/(.*),$', mb, re.M))
    alerta_novo = any(json.dumps(x, ensure_ascii=True) not in velhos for x in aten + pend + vnov)
hora = int(os.environ.get("LEVE_HORA", AGORA.hour)); janela14 = 12 <= hora <= 17
FORCE = "--forcar" in sys.argv
if R["leve_compila"] != "ok": R["modo"] = "erro: canvas leve não compila"
elif not mfp or mfp.group(2) != TPLH or "/*LEVE_INICIO*/" not in live: R["modo"] = "bootstrap leve (WriteCanvas leve/painel_leve.tsx.txt)"
elif mfp.group(1) == FP and not FORCE: R["modo"] = "nada a fazer"
elif janela14 and not alerta_novo and not FORCE: R["modo"] = "pular (rodada das 14h sem alerta novo)"
else:
    # edições por linha: só linhas que mudaram; o bloco de dados inteiro como fallback se a contagem de linhas mudou
    old_blk = live.split("/*LEVE_INICIO*/")[1].split("/*LEVE_FIM*/")[0]
    new_blk = PAGE.split("/*LEVE_INICIO*/")[1].split("/*LEVE_FIM*/")[0]
    ed = [{"old_string": mfp.group(0), "new_string": "/*FP:%s T%s*/" % (FP, TPLH)}]
    ol, nl = old_blk.split("\n"), new_blk.split("\n")
    # agrupa por chave de topo: substitui o trecho de cada chave que mudou
    def trechos(ls):
        out, cur, key = {}, [], None
        for l in ls:
            m = re.match(r'^"([a-z]+)": ', l)
            if m: 
                if key: out[key] = "\n".join(cur)
                key, cur = m.group(1), [l]
            elif key: cur.append(l)
        if key: out[key] = "\n".join(cur)
        return out
    to, tn = trechos(ol), trechos(nl)
    for k, v in tn.items():
        if to.get(k) != v:
            if k in to and live.count(to[k]) == 1: ed.append({"old_string": to[k], "new_string": v})
            else: ed = [{"old_string": "/*LEVE_INICIO*/" + old_blk + "/*LEVE_FIM*/", "new_string": "/*LEVE_INICIO*/" + new_blk + "/*LEVE_FIM*/"}, ed[0]]; break
    json.dump({"canvas_id": CID, "edits": ed}, open(LV + "edits_001.json", "w"), ensure_ascii=True)
    R["modo"] = "EditCanvas(leve/edits_001.json)"; R["edits_bytes"] = len(json.dumps(ed, ensure_ascii=True))
R["fp"] = FP; R["alerta_novo"] = alerta_novo; R["leve_dir"] = LV
json.dump(R, open(LV + "relatorio.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False, indent=1))
