#!/usr/bin/env python3
"""Private read-only finance endpoint for the IFC job and n8n."""
from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

DSN_FILE = Path(os.environ.get("IFC_DB_DSN_FILE", "/run/secrets/db-url"))
TOKEN_FILE = Path(os.environ.get("IFC_DB_TOKEN_FILE", "/run/secrets/gateway-token"))
HOST = "0.0.0.0"
PORT = 8788

FINANCE_SQL = """
WITH c AS (
  SELECT cat_row.id::text AS id, cat_row.name, cat_row.type, COALESCE(parent_row.name, cat_row.name) AS parent
  FROM public.categories cat_row
  LEFT JOIN public.categories parent_row ON parent_row.id = cat_row.parent_id
), tx AS (
  SELECT t.transaction_date::date AS d, t.amount::numeric AS a,
         CASE
           WHEN c.type = 'NEUTRA' OR t.entry_type = 'NEUTRA' THEN 'NEUTRA'
           WHEN t.entry_type IN ('RECEITA', 'DESPESA') THEN t.entry_type
           WHEN c.type IN ('RECEITA', 'DESPESA') THEN c.type
           WHEN t.transaction_type = 'CREDIT' THEN 'RECEITA'
           WHEN t.transaction_type = 'DEBIT' THEN 'DESPESA'
           ELSE NULL
         END AS e,
         COALESCE(c.parent, c.name, 'Outros') AS par,
         COALESCE(c.name, 'Outros') AS cat
  FROM public.transactions t
  LEFT JOIN c ON c.id = t.category
), monthly AS (
  SELECT to_char(d, 'YYYY-MM') AS mes,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e = 'RECEITA'), 0), 2) AS entradas,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e = 'DESPESA'), 0), 2) AS saidas,
         round(COALESCE(sum(a) FILTER (WHERE e = 'NEUTRA'), 0), 2) AS neutras,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e = 'NEUTRA'), 0), 2) AS neutras_valor_bruto,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e = 'RECEITA'), 0) - COALESCE(sum(abs(a)) FILTER (WHERE e = 'DESPESA'), 0) + COALESCE(sum(a) FILTER (WHERE e = 'NEUTRA'), 0), 2) AS saldo,
         count(*) FILTER (WHERE e = 'RECEITA')::int AS entradas_n,
         count(*) FILTER (WHERE e = 'DESPESA')::int AS saidas_n,
         count(*) FILTER (WHERE e = 'NEUTRA')::int AS neutras_n,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e IS NULL), 0), 2) AS sem_classificacao_valor,
         count(*) FILTER (WHERE e IS NULL)::int AS sem_classificacao_n,
         count(*)::int AS n
  FROM tx
  WHERE d >= date_trunc('month', current_date) - interval '11 months'
  GROUP BY 1
), category_month AS (
  SELECT to_char(d, 'YYYY-MM') AS mes, e AS tipo, par, cat,
         round(COALESCE(sum(abs(a)), 0), 2) AS v, count(*)::int AS n
  FROM tx
  WHERE e IN ('RECEITA', 'DESPESA', 'NEUTRA')
    AND d >= date_trunc('month', current_date) - interval '11 months'
  GROUP BY 1, 2, 3, 4
), initial AS (
  SELECT year::text AS ano, round(sum(amount), 2) AS saldo
  FROM public.initial_balances
  WHERE account_type IN ('banco', 'caixa')
  GROUP BY year
), yearly AS (
  SELECT extract(year FROM d)::int AS ano,
         to_char(d, 'YYYY-MM') AS mes, round(COALESCE(sum(abs(a)) FILTER (WHERE e = 'RECEITA'), 0) - COALESCE(sum(abs(a)) FILTER (WHERE e = 'DESPESA'), 0) + COALESCE(sum(a) FILTER (WHERE e = 'NEUTRA'), 0), 2) AS res,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e = 'NEUTRA'), 0), 2) AS neutras_valor_bruto,
         count(*) FILTER (WHERE e = 'RECEITA')::int AS entradas_n,
         count(*) FILTER (WHERE e = 'DESPESA')::int AS saidas_n,
         count(*) FILTER (WHERE e = 'NEUTRA')::int AS neutras_n,
         round(COALESCE(sum(abs(a)) FILTER (WHERE e IS NULL), 0), 2) AS sem_classificacao_valor,
         count(*) FILTER (WHERE e IS NULL)::int AS sem_classificacao_n,
         count(*)::int AS n
  FROM tx
  WHERE (e IN ('RECEITA', 'DESPESA', 'NEUTRA') OR e IS NULL)
    AND d >= date_trunc('year', current_date) - interval '4 years'
  GROUP BY 1, 2
)
SELECT jsonb_build_object(
  'max_data', (SELECT max(d)::text FROM tx),
  'ult_import', (SELECT max(created_at)::text FROM public.transactions),
  'sem_data', (SELECT jsonb_build_object('n', count(*)::int, 'valor_bruto', round(COALESCE(sum(abs(a)), 0), 2)) FROM tx WHERE d IS NULL),
  'mensal', COALESCE((SELECT jsonb_agg(to_jsonb(m) ORDER BY m.mes) FROM monthly m), '[]'::jsonb),
  'saldo_inicial', COALESCE((SELECT jsonb_object_agg(ano, saldo) FROM initial), '{}'::jsonb),
  'por_ano_ate_mes', COALESCE((SELECT jsonb_agg(to_jsonb(y) ORDER BY y.ano, y.mes) FROM yearly y), '[]'::jsonb),
  'categorias', COALESCE((SELECT jsonb_agg(to_jsonb(k) ORDER BY k.mes, k.tipo, k.par, k.cat) FROM category_month k), '[]'::jsonb)
)::text
"""

TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
DSN = DSN_FILE.read_text(encoding="utf-8").strip()
if len(TOKEN) < 32 or not DSN.startswith(("postgres://", "postgresql://")):
    raise SystemExit("finance gateway configuration invalid")

SAFE_CATEGORY_WORDS = {
    "a", "ao", "aos", "com", "da", "das", "de", "do", "dos", "e", "em", "na", "nas", "no", "nos", "para", "por",
    "acao", "agua", "ajuda", "aluguel", "alimentacao", "apoio", "assessoria", "banco", "bancaria", "bancarias",
    "bens", "cantina", "ceia", "celular", "combustivel", "comunicacao", "condominio",
    "construcao", "contabilidade", "contribuicao", "doacao", "dizimo", "dizimos", "educacao",
    "energia", "equipamento", "evento", "eventos", "evangelismo", "familia", "gas", "igreja",
    "imposto", "impostos", "impressao", "internet", "limpeza", "luz", "manutencao", "material",
    "midia", "migracao", "missao", "missoes", "ministerio", "ministerios", "oferta", "ofertas",
    "obra", "obras", "patrimonio", "papelaria", "pastoral", "recepcao", "reforma", "reformas",
    "salario", "salarios", "secretaria", "seguro", "servico", "servicos", "social", "som",
    "sistema", "tarifa", "taxa", "taxas", "telefone", "templo", "transporte", "viagem", "viagens",
}
PII = (
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)"),
)


def safe_category(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()[:80]
    if not text:
        return "Outros"
    for pattern in PII:
        if pattern.search(text):
            return "Outros"
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    words = re.findall(r"[a-z]+", normalized)
    if not words or any(word not in SAFE_CATEGORY_WORDS for word in words):
        return "Outros"
    return text


_cache_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def finance_summary() -> dict:
    global _cache
    with _cache_lock:
        now = time.monotonic()
        if _cache and now - _cache[0] < 300:
            return _cache[1]
    with psycopg.connect(DSN, connect_timeout=8) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '20s'")
            result = connection.execute(FINANCE_SQL).fetchone()
    if not result or not isinstance(result[0], (str, dict)):
        raise ValueError("finance_result_invalid")
    payload = json.loads(result[0]) if isinstance(result[0], str) else result[0]
    if not isinstance(payload, dict):
        raise ValueError("finance_result_invalid")
    categories = payload.get("categorias", [])
    if not isinstance(categories, list):
        raise ValueError("finance_categories_invalid")
    # Keep only aggregate category rows, and collapse labels outside the
    # church-finance vocabulary into a generic bucket to avoid exposing names.
    collapsed: dict[tuple[str, str, str], dict] = {}
    for row in categories:
        if not isinstance(row, dict):
            continue
        month = str(row.get("mes", ""))[:7]
        kind = row.get("tipo") if row.get("tipo") in {"RECEITA", "DESPESA", "NEUTRA"} else ""
        parent = safe_category(row.get("par"))
        category = safe_category(row.get("cat"))
        if not month or not kind:
            continue
        key = (month, kind, parent + " / " + category)
        target = collapsed.setdefault(key, {"mes": month, "tipo": kind, "par": parent, "cat": category, "v": 0.0, "n": 0})
        target["v"] = round(target["v"] + float(row.get("v") or 0), 2)
        target["n"] += int(row.get("n") or 0)
    payload["categorias"] = list(collapsed.values())
    payload["generated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
    with _cache_lock:
        _cache = (time.monotonic(), payload)
    return payload


def visitors_summary() -> dict:
    """Fixed read-only query; never return visitor contacts or private form answers."""
    with psycopg.connect(DSN, connect_timeout=8) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '20s'")
            totals = connection.execute("""SELECT count(*), count(*) FILTER (WHERE data >= current_date-30),
                count(*) FILTER (WHERE data >= current_date-7),
                count(*) FILTER (WHERE msg_enviada IS TRUE AND respondeu_msg IS NOT TRUE)
                FROM public.visitantes""").fetchone()
            rows = connection.execute("""SELECT data::text, msg_enviada, respondeu_msg
                FROM public.visitantes ORDER BY data_submissao DESC NULLS LAST LIMIT 20""").fetchall()
    return {"generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
            "total": totals[0], "ultimos_30_dias": totals[1], "ultimos_7_dias": totals[2],
            "enviadas_sem_resposta_registrada": totals[3],
            "recentes": [{"data": day, "mensagem_enviada": sent, "resposta_registrada": reply} for day, sent, reply in rows],
            "nota": "Estados registrados no Supabase; não confirmam entrega nem leitura no WhatsApp."}


class Handler(BaseHTTPRequestHandler):
    server_version = "IFCFinanceGateway/2"
    sys_version = ""

    def log_message(self, _format, *_args):
        return

    def reply(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path == "/health":
            return self.reply(200, {"status": "ok"})
        route = urlsplit(self.path).path
        if route not in {"/v1/finance/summary", "/v1/visitors/summary"}:
            return self.reply(404, {"status": "not_found"})
        supplied = self.headers.get("Authorization", "")
        if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], TOKEN):
            return self.reply(401, {"status": "unauthorized"})
        try:
            return self.reply(200, visitors_summary() if route == "/v1/visitors/summary" else finance_summary())
        except Exception as exc:
            self.log_message("gateway query failed: %s", type(exc).__name__)
            return self.reply(503, {"status": "finance_unavailable", "reason": type(exc).__name__})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
