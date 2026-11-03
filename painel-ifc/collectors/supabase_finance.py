#!/usr/bin/env python3
"""Collect only aggregate IFC finance data through the private SQL gateway."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = os.environ.get("IFC_FINANCE_URL", "http://ifc-db-gateway:8788/v1/finance/summary")
TOKEN_FILE = Path(os.environ.get("IFC_FINANCE_TOKEN_FILE", "/run/secrets/finance-gateway.token"))
ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
OUTPUT = ROOT / "staging" / "financeiro.json"
ALLOWED_TOP = {"generated_at", "max_data", "ult_import", "sem_data", "mensal", "saldo_inicial", "por_ano_ate_mes", "categorias"}
BLOCKED = re.compile(r"telefone|phone|cpf|jid|descri[cç][aã]o|description|contribuinte|favorecido|beneficiario|recipient|nome", re.I)


def atom(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def valid_month(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value):
        return False
    return True


def write_private(payload: dict) -> None:
    OUTPUT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".financeiro-", suffix=".tmp", dir=OUTPUT.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, OUTPUT)
        os.chmod(OUTPUT, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def collect() -> dict:
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("gateway_token_missing")
    request = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=35) as response:
        if response.status != 200:
            raise ValueError("finance_gateway_status")
        raw = response.read(1_500_000)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != ALLOWED_TOP:
        raise ValueError("finance_schema_invalid")
    if any(BLOCKED.search(str(key)) for key in payload):
        raise ValueError("finance_blocked_field")
    no_date = payload.get("sem_data")
    if (not isinstance(no_date, dict) or set(no_date) != {"n", "valor_bruto"}
            or type(no_date.get("n")) is not int or no_date["n"] < 0
            or not finite_number(no_date.get("valor_bruto")) or no_date["valor_bruto"] < 0):
        raise ValueError("finance_undated_shape_invalid")
    for list_key in ("mensal", "por_ano_ate_mes", "categorias"):
        rows = payload.get(list_key)
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("finance_list_invalid")
        for row in rows:
            if not isinstance(row, dict) or any(BLOCKED.search(str(key)) for key in row):
                raise ValueError("finance_row_invalid")
            if any(not atom(value) for value in row.values()):
                raise ValueError("finance_row_nested_value")
    for row in payload["mensal"]:
        if (set(row) != {"mes", "entradas", "saidas", "neutras", "neutras_valor_bruto", "saldo", "entradas_n", "saidas_n", "neutras_n", "sem_classificacao_valor", "sem_classificacao_n", "n"}
                or not valid_month(row.get("mes"))
                or any(not finite_number(row.get(key)) for key in ("entradas", "saidas", "neutras", "neutras_valor_bruto", "saldo", "sem_classificacao_valor"))
                or row["entradas"] < 0 or row["saidas"] < 0 or row["neutras_valor_bruto"] < 0
                or row["sem_classificacao_valor"] < 0
                or any(type(row.get(key)) is not int or row[key] < 0 for key in ("entradas_n", "saidas_n", "neutras_n"))
                or type(row.get("sem_classificacao_n")) is not int or row["sem_classificacao_n"] < 0
                or type(row.get("n")) is not int or row["n"] < 0
                or row["n"] != row["entradas_n"] + row["saidas_n"] + row["neutras_n"] + row["sem_classificacao_n"]
                or abs(round(row["entradas"] - row["saidas"] + row["neutras"] - row["saldo"], 2)) > 0.02):
            raise ValueError("finance_month_shape_invalid")
    for row in payload["por_ano_ate_mes"]:
        if (set(row) != {"ano", "mes", "res", "neutras_valor_bruto", "entradas_n", "saidas_n", "neutras_n", "sem_classificacao_valor", "sem_classificacao_n", "n"} or not valid_month(row.get("mes"))
                or type(row.get("ano")) is not int or str(row["ano"]) != row["mes"][:4]
                or not finite_number(row.get("res"))
                or not finite_number(row.get("neutras_valor_bruto")) or row["neutras_valor_bruto"] < 0
                or not finite_number(row.get("sem_classificacao_valor")) or row["sem_classificacao_valor"] < 0
                or any(type(row.get(key)) is not int or row[key] < 0 for key in ("entradas_n", "saidas_n", "neutras_n", "sem_classificacao_n", "n"))
                or row["n"] != row["entradas_n"] + row["saidas_n"] + row["neutras_n"] + row["sem_classificacao_n"]):
            raise ValueError("finance_year_shape_invalid")
    for row in payload["categorias"]:
        if (set(row) != {"mes", "tipo", "par", "cat", "v", "n"}
                or not valid_month(row.get("mes")) or row.get("tipo") not in {"RECEITA", "DESPESA", "NEUTRA"}
                or not isinstance(row.get("par"), str) or not isinstance(row.get("cat"), str)
                or not finite_number(row.get("v")) or type(row.get("n")) is not int or row["n"] < 0):
            raise ValueError("finance_category_shape_invalid")
    category_counts = {}
    category_values = {}
    for row in payload["categorias"]:
        key = (row["mes"], row["tipo"])
        category_counts[key] = category_counts.get(key, 0) + row["n"]
        category_values[key] = round(category_values.get(key, 0.0) + float(row["v"]), 2)
    for row in payload["mensal"]:
        expected = {"RECEITA": row["entradas_n"], "DESPESA": row["saidas_n"], "NEUTRA": row["neutras_n"]}
        if any(category_counts.get((row["mes"], kind), 0) != count for kind, count in expected.items()):
            raise ValueError("finance_category_count_mismatch")
        expected_values = {"RECEITA": row["entradas"], "DESPESA": row["saidas"], "NEUTRA": row["neutras_valor_bruto"]}
        if any(abs(round(category_values.get((row["mes"], kind), 0.0) - amount, 2)) > 0.02
               for kind, amount in expected_values.items()):
            raise ValueError("finance_category_value_mismatch")
    if (not isinstance(payload.get("saldo_inicial"), dict)
            or any(not isinstance(k, str) or not re.fullmatch(r"\d{4}", k) or not finite_number(v)
                   for k, v in payload["saldo_inicial"].items())):
        raise ValueError("finance_balance_invalid")
    if payload.get("max_data") is not None and (not isinstance(payload["max_data"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["max_data"])):
        raise ValueError("finance_max_date_invalid")
    if payload.get("ult_import") is not None and not isinstance(payload["ult_import"], str):
        raise ValueError("finance_import_date_invalid")
    payload["source"] = "supabase_finance_aggregate"
    write_private(payload)
    return {"status": "ok", "months": len(payload["mensal"]), "categories": len(payload["categorias"])}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), ensure_ascii=False, separators=(",", ":")))
    except Exception as error:
        allowed = {"gateway_token_missing", "finance_schema_invalid", "finance_blocked_field",
                   "finance_undated_shape_invalid", "finance_list_invalid", "finance_row_invalid",
                   "finance_row_nested_value", "finance_month_shape_invalid", "finance_year_shape_invalid",
                   "finance_category_shape_invalid", "finance_category_count_mismatch",
                   "finance_category_value_mismatch", "finance_balance_invalid",
                   "finance_max_date_invalid", "finance_import_date_invalid"}
        reason = str(error)
        print(json.dumps({"status": "failed", "reason": reason if reason in allowed else "finance_collection_failed"}, separators=(",", ":")))
        raise SystemExit(1)
