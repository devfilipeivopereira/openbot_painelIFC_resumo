#!/usr/bin/env python3
"""Read the visitor follow-up table from n8n's authenticated Baserow workflow."""
import datetime as dt
import json
import re
import unicodedata
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import supabase_finance as storage

storage.OUTPUT = storage.ROOT / 'staging' / 'visitantes.json'
TOKEN_FILE = Path('/run/secrets/visitors-n8n.token')
URL = os.environ.get('IFC_VISITORS_READONLY_URL', '')
if not URL: raise SystemExit('visitor_source_configuration_missing')
ZONE = ZoneInfo('America/Sao_Paulo')

ALIASES = {
    'nome': re.compile(r'^(nome|name|visitante|visitor)(\s|$)', re.I),
    'data': re.compile(r'^(data|date)$|(data|date).*(visita|cadastro|visit|registro)|(visita|visit).*(data|date)', re.I),
    'mensagem_enviada': re.compile(r'(mensagem|message).*(enviad|sent)|(enviad|sent).*(mensagem|message)', re.I),
    'resposta_registrada': re.compile(r'(respost|reply|retorno).*(registr|recebid|received)|(registr|recebid|received).*(respost|reply|retorno)', re.I),
    'status': re.compile(r'^(status|situacao|situação|etapa)$', re.I),
}
BLOCKED = re.compile(r'telefone|phone|whatsapp|jid|e.?mail|cpf|documento|endereco|address|token|senha|password|credencial|secret', re.I)


def _rows(payload):
    if isinstance(payload, dict):
        for key in ('data', 'body', 'items', 'results'):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    if isinstance(payload, list):
        return payload
    raise ValueError('visitor_response_invalid')


def _canonical(row):
    if isinstance(row, dict) and isinstance(row.get('json'), dict):
        row = row['json']
    if not isinstance(row, dict):
        return None
    safe = {}
    for key, value in row.items():
        if not isinstance(key, str) or BLOCKED.search(key) or value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            continue
        for target, pattern in ALIASES.items():
            if pattern.search(key):
                safe[target] = str(value)[:300]
                break
    if not safe:
        return None
    return safe


def _date(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            return dt.date.fromisoformat(text)
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            # Date-only and local-naive values from the visitor form are
            # interpreted on the panel's Brasília calendar day.
            return parsed.date()
        return parsed.astimezone(ZONE).date()
    except ValueError:
        pass
    if re.fullmatch(r'\d{9,13}', text):
        try:
            timestamp = int(text)
            if len(text) == 13:
                timestamp /= 1000
            return dt.datetime.fromtimestamp(timestamp, ZONE).date()
        except (OverflowError, OSError, ValueError):
            return None
    numeric = re.match(r'^\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})', text)
    if numeric:
        day, month, year = (int(part) for part in numeric.groups())
        if year < 100:
            year += 2000 if year < 70 else 1900
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    normalized = unicodedata.normalize('NFD', text.casefold())
    normalized = ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')
    month_names = {
        'january': 1, 'jan': 1, 'janeiro': 1, 'fevereiro': 2, 'february': 2, 'feb': 2,
        'marco': 3, 'march': 3, 'mar': 3, 'abril': 4, 'april': 4, 'apr': 4,
        'maio': 5, 'may': 5, 'junho': 6, 'june': 6, 'jun': 6, 'julho': 7,
        'july': 7, 'jul': 7, 'agosto': 8, 'august': 8, 'ago': 8, 'aug': 8,
        'setembro': 9, 'september': 9, 'set': 9, 'sep': 9, 'sept': 9, 'outubro': 10,
        'october': 10, 'out': 10, 'oct': 10, 'novembro': 11, 'november': 11,
        'nov': 11, 'dezembro': 12, 'december': 12, 'dez': 12, 'dec': 12,
    }
    named = re.search(r'\b(\d{1,2})(?:º|°)?\s*(?:de\s+)?([a-z]+)\.?\s*(?:de\s+)?(\d{4})\b', normalized)
    if named:
        day, month_name, year = named.groups()
        month = month_names.get(month_name)
        if month:
            try:
                return dt.date(int(year), month, int(day))
            except ValueError:
                return None
    month_first = re.search(r'\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b', normalized)
    if month_first:
        month_name, day, year = month_first.groups()
        month = month_names.get(month_name)
        if month:
            try:
                return dt.date(int(year), month, int(day))
            except ValueError:
                return None
    return None


def collect():
    token = TOKEN_FILE.read_text(encoding='utf-8').strip()
    request = urllib.request.Request(URL, headers={'X-IFC-Visitors-Read-Only': token, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read(2_000_000))
    rows = [row for raw in _rows(payload) if (row := _canonical(raw))]
    today = dt.datetime.now(ZONE).date()
    dated = [(row, _date(row.get('data'))) for row in rows]
    last7 = sum(bool(day and 0 <= (today - day).days < 7) for _, day in dated)
    last30 = sum(bool(day and 0 <= (today - day).days < 30) for _, day in dated)
    undated = sum(day is None for _, day in dated)
    sent = lambda row: (str(row.get('mensagem_enviada', '')).casefold() in {'true', '1', 'sim', 'yes', 'enviado', 'sent'}
                        or bool(re.search(r'enviad|sent|disparad', str(row.get('status', '')).casefold())))
    replied = lambda row: (str(row.get('resposta_registrada', '')).casefold() in {'true', '1', 'sim', 'yes', 'respondido', 'replied'}
                           or bool(re.search(r'respondid|responded|reply|retorno recebido', str(row.get('status', '')).casefold())))
    recent = []
    for row, day in sorted(dated, key=lambda pair: pair[1] or dt.date.min, reverse=True)[:100]:
        visible = dict(row)
        visible['data'] = day.isoformat() if day else ''
        recent.append(visible)
    data = {
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'total': len(rows), 'ultimos_30_dias': last30, 'ultimos_7_dias': last7,
        'datas_nao_identificadas': undated,
        'enviadas_sem_resposta_registrada': sum(sent(row) and not replied(row) for row in rows),
        'recentes': recent,
        'nota': 'Fonte: tabela de visitantes do Baserow operada pelo n8n. Contatos pessoais foram removidos.' +
                (f' {undated} cadastro(s) sem data reconhecível ficam fora dos recortes de 7/30 dias.' if undated else ''),
    }
    storage.write_private(data)
    return {'status': 'ok', 'records': len(rows)}


if __name__ == '__main__':
    try:
        print(json.dumps(collect()))
    except Exception:
        print('{"status":"failed"}')
        raise SystemExit(1)
