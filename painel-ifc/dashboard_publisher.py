#!/usr/bin/env python3
"""Merge verified staging summaries into the private IFC dashboard atomically."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
STAGING = ROOT / "staging"
OUTPUT = Path(os.environ.get("IFC_PANEL_OUTPUT", "/run/publish/dados.json"))
HISTORY = OUTPUT.parent / "history"
ATA_FILE = OUTPUT.parent / "integrantes_ata2026.json"
ATA_HISTORY_KEY = "__ATA2026__"
ZONE = ZoneInfo("America/Sao_Paulo")
REVIEW_PII_PATTERNS = (
    re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)"),
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)", re.I),
    re.compile(r"(?<!\w)@\d{10,}(?!\d)"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
)

SOURCE_FILES = {
    "instagram": "instagram.json",
    "youtube": "youtube.json",
    "visitantes": "visitantes.json",
    "drive_secretaria": "drive_secretaria.json",
    "evolution_grupos": "evolution_grupos.json",
    "evolution_mulheres": "evolution_mulheres.json",
    "mensagens_diretas": "mensagens_diretas.json",
    "agenda": "agenda.json",
    "email_secretaria": "email_secretaria.json",
    "todoist": "todoist.json",
    "n8n": "n8n.json",
    "financeiro": "financeiro.json",
}


def load_stage(name: str) -> dict | None:
    path = STAGING / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def stage_is_current(value: dict, now: datetime) -> bool:
    stamp = value.get("generated_at", value.get("checked_at"))
    if not isinstance(stamp, str):
        return False
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZONE)
        age = (now - parsed.astimezone(ZONE)).total_seconds()
        return -300 <= age <= 24 * 60 * 60
    except ValueError:
        return False


def record(source: str, status: str, detail: str) -> dict[str, str]:
    return {"fonte": source, "status": status, "detalhe": detail}


def safe_review_quote(value: object) -> str:
    text = str(value or "")
    if any(pattern.search(text) for pattern in REVIEW_PII_PATTERNS):
        return "[trecho ocultado por conter dado pessoal]"
    return text


def normalized_group_name(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold().split())


def last_capture_note(value: dict | None) -> str:
    """Describe the age of the last staged batch when a current fetch is missing."""
    if not isinstance(value, dict):
        return ""
    stamp = value.get("captured_at", value.get("generated_at", value.get("checked_at")))
    if not isinstance(stamp, str):
        return ""
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZONE)
        rendered = parsed.astimezone(ZONE).strftime("%d/%m/%Y às %H:%M")
    except ValueError:
        return ""
    return f" Última captura disponível: {rendered} (horário de Brasília)."


def media_coverage(messages: list) -> dict[str, int]:
    """Summarize extracted content and media gaps for a source's visible panel card."""
    counts = {"audio_transcribed": 0, "audio_unavailable": 0, "ocr_text_extracted": 0,
              "document_text_extracted": 0, "media_content_unavailable": 0,
              "media_analysis_partial": 0,
              "visual_ai_descriptions": 0, "video_sampled_partial": 0}
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        body = str(message.get("text", ""))
        normalized = body.casefold()
        kind = str(message.get("type", "")).casefold()
        is_audio = any(token in kind for token in ("audio", "ptt", "voice"))
        if "[áudio transcrito]" in body:
            counts["audio_transcribed"] += 1
        elif body.startswith("[Áudio indisponível") or (is_audio and body.startswith("[áudio não transcrito")):
            counts["audio_unavailable"] += 1
        video_partial = any(marker in normalized for marker in (
            "vídeo não analisado: visual resumido por amostragem",
            "vídeo analisado parcialmente: visual por amostragem",
            "conteúdo visual analisado por amostragem, não integralmente",
        ))
        if video_partial:
            counts["video_sampled_partial"] += 1
        elif not is_audio and re.search(r"\bnão analisad[oa](?::|\])", normalized):
            unavailable = "conteúdo visual ou do arquivo indisponível" in normalized
            counts["media_content_unavailable" if unavailable else "media_analysis_partial"] += 1
        if "ocr da imagem" in normalized or "[ocr;" in normalized:
            counts["ocr_text_extracted"] += 1
        if "[descrição visual por ia" in normalized:
            counts["visual_ai_descriptions"] += 1
        if "conteúdo textual extraído" in normalized:
            counts["document_text_extracted"] += 1
    return counts


def media_status_rows(source_key: str, coverage: dict[str, int]) -> list[dict]:
    """Describe unavailable and partial media separately in source coverage."""
    rows = []
    unavailable = coverage.get("media_content_unavailable", 0)
    partial = coverage.get("media_analysis_partial", 0)
    videos = coverage.get("video_sampled_partial", 0)
    audio = coverage.get("audio_unavailable", 0)
    if unavailable:
        rows.append(record(f"midias_{source_key}", "Mídia sem conteúdo recuperável",
                           f"{unavailable} arquivo(s) sem conteúdo disponível nesta coleta; nenhum conteúdo foi inferido."))
    if partial:
        rows.append(record(f"midias_parciais_{source_key}", "Análise de mídia parcial",
                           f"{partial} arquivo(s) têm OCR/extração textual ou processamento incompleto; o conteúdo visual não foi interpretado por completo."))
    if videos:
        rows.append(record(f"videos_parciais_{source_key}", "Vídeo analisado por amostragem",
                           f"{videos} vídeo(s) tiveram análise parcial por quadros amostrados e áudio recuperável; não representam revisão integral."))
    if audio:
        rows.append(record(f"audios_{source_key}", "Áudio ignorado após três tentativas",
                           f"{audio} áudio(s) não puderam ser recuperados; a coleta seguiu com as demais mensagens."))
    return rows


def pending_media(messages: list, source: str) -> list[dict[str, str]]:
    """Expose only safe locators for media that could not be retrieved."""
    markers = {
        "imagem": "Imagem",
        "vídeo": "Vídeo",
        "video": "Vídeo",
        "documento": "Documento",
        "arquivo": "Arquivo",
    }
    rows = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        body = str(message.get("text", ""))
        match = re.search(
            r"\[(imagem|vídeo|video|documento|arquivo) não analisad[oa]: conteúdo visual ou do arquivo indisponível\]",
            body,
            re.IGNORECASE,
        )
        if not match:
            continue
        try:
            stamp = datetime.fromisoformat(str(message.get("at", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZONE)
            when = stamp.astimezone(ZONE).isoformat(timespec="minutes")
        except ValueError:
            when = ""
        if source == "groups":
            # Only expose locators for the IFC ministry groups on the private panel.
            if normalized_group_name(message.get("group_category")) != "ifc":
                continue
            location = str(message.get("group", "Grupo IFC não identificado"))[:160]
        elif source == "women":
            location = "Ministério de Mulheres IFC"
        else:
            location = str(message.get("person", "Contato autorizado"))[:100]
        rows.append({
            "quando": when,
            "local": location,
            "tipo": markers[match.group(1).casefold()],
            "situacao": "Conteúdo não recuperado nesta coleta",
        })
    return sorted(rows, key=lambda item: item["quando"])


def attention_items(tasks: list, statuses: list) -> list[dict[str, str]]:
    """Build actionable alerts only from explicit overdue dates or source states."""
    items = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict) or task.get("estado") != "atrasada":
            continue
        name = str(task.get("tarefa", "")).strip()
        if not name:
            continue
        due = task.get("prazo")
        priority = task.get("prioridade")
        detail = "Prazo vencido" + (f": {due}" if isinstance(due, str) and due else "")
        if isinstance(priority, int) and priority >= 3:
            detail += "; prioridade alta no Todoist"
        items.append({"area": "Todoist", "titulo": name, "detalhe": detail + "."})
    ignored_coverage = ("midias_", "audios_")
    for status in statuses if isinstance(statuses, list) else []:
        if not isinstance(status, dict):
            continue
        source = str(status.get("fonte", ""))
        state = str(status.get("status", ""))
        if source.startswith(ignored_coverage):
            continue
        actionable = (state.startswith(("Falhou", "Pendente:", "Não verificada:", "Atenção:"))
                      or "curadoria IA pendente" in state.casefold()
                      or (source == "curadoria_detalhada" and state == "Pendente"))
        if not actionable:
            continue
        detail = str(status.get("detalhe", "")).strip()
        items.append({"area": "Fonte · " + (source or "sem nome"),
                      "titulo": state,
                      "detalhe": detail or "Estado requer conferência nesta atualização."})
    return items


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".painel-ifc-", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def fixed_ata(data: dict, today: str) -> object:
    """Carry forward the one-time private ATA capture without daily source queries."""
    if ATA_HISTORY_KEY in data:
        return data[ATA_HISTORY_KEY]
    snapshots = sorted(HISTORY.glob("????-??-??.json"), reverse=True)
    for snapshot in snapshots:
        if snapshot.stem >= today:
            continue
        try:
            prior = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(prior, dict) and ATA_HISTORY_KEY in prior:
            return prior[ATA_HISTORY_KEY]
    try:
        value = json.loads(ATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value


def merge_worship(data, changes):
    import re,unicodedata
    from datetime import date
    def normal(v):return ''.join(c for c in unicodedata.normalize('NFD',str(v).lower()) if unicodedata.category(c)!='Mn').strip()
    def exact_day(value):
        value=str(value or '').strip()[:10]
        for fmt in ('%Y-%m-%d','%d/%m/%Y'):
            try:return datetime.strptime(value,fmt).date().isoformat()
            except (ValueError,AttributeError):pass
        return None
    today=datetime.now(ZONE).date().isoformat()
    rows=data.get('escala_louvor',[])
    if not isinstance(rows,list):rows=[]
    active=[];past=list(data.get('escala_louvor_historico',[])) if isinstance(data.get('escala_louvor_historico'),list) else []
    pending=list(data.get('escala_louvor_pendencias',[])) if isinstance(data.get('escala_louvor_pendencias'),list) else []
    for row in rows:
        if not isinstance(row,dict):continue
        day=exact_day(row.get('data'))
        if day and day<today:past.append(row)
        elif day and day>=today:active.append(row)
        else:past.append({**row,'status_historico':'data sem ano completo; não usada para alterar escala ativa'})
    rows=active
    for change in sorted(changes,key=lambda c:c.get('at') or ''):
        if change.get('date_confidence')=='year_unverified':
            past.append({'data':change.get('date_evidence') or 'Ano a confirmar','role':change.get('role'),'person':change.get('person'),'group':change.get('group'),'evidence':change.get('evidence'),'status_historico':'data sem ano explícito; preservada para conferência, sem alterar escala ativa'})
            continue
        day=exact_day(change.get('date'))
        if not day:continue
        if day<today:
            past.append({**change,'status_historico':'alteração passada; preservada sem modificar escala atual'})
            continue
        row=next((r for r in rows if exact_day(r.get('data'))==day),None)
        if row is None:
            # A single message can describe a role change, not the complete roster.
            # Do not turn it into a new active row and thereby imply the rest of
            # that service's schedule is known. Keep it visible for confirmation.
            item={k:change.get(k) for k in ('date','role','person','previous_person','operation','evidence','group','at')}
            item.update(status='aguardando escala-base confirmada',status_historico='alteração futura sem escala-base; não publicada como escala ativa')
            pending.append(item)
            continue
        role=change['role'];existing=row.get(role,[]);names=list(existing) if isinstance(existing,list) else [x.strip() for x in re.split(r'[,;]',str(existing)) if x.strip()]
        previous=change['previous_person'] if change['operation']=='substituir' else change['person']
        matches=[x for x in names if normal(x)==normal(previous)]
        note=change['operation']+': '+change['person']+(' no lugar de '+previous if change['operation']=='substituir' else '')+' · '+str(change.get('group',''))+' · '+str(change.get('at',''))
        if change['operation'] in ('remover','substituir') and names and not matches:
            if any(normal(x)==normal(change['person']) for x in names) and change['operation']=='substituir':continue
            row['conferir_alteracao']=note+' — conferir com a escala anterior.';continue
        if change['operation'] in ('remover','substituir'):names=[x for x in names if x not in matches]
        if change['operation']!='remover' and not any(normal(x)==normal(change['person']) for x in names):names.append(change['person'])
        row[role]=names
        notes=row.setdefault('alteracoes_confirmadas',[])
        if note not in notes:notes.append(note)
    data['escala_louvor']=sorted(rows,key=lambda r:str(r.get('data','')))
    current_pending=[]
    def role_names(row, role):
        value=row.get(role,[])
        return list(value) if isinstance(value,list) else [x.strip() for x in re.split(r'[,;]',str(value or '')) if x.strip()]
    for item in pending:
        day=exact_day(item.get('date')) if isinstance(item,dict) else None
        if day and day<datetime.now(ZONE).date():
            past.append({**item,'data':day.isoformat(),'status_historico':'alteração sem escala-base; arquivada após a data do culto'})
        elif day:
            baseline=next((r for r in rows if exact_day(r.get('data'))==day),None)
            if baseline is not None:
                people=[normal(name) for name in role_names(baseline,str(item.get('role') or ''))]
                person=normal(item.get('person',''))
                previous=normal(item.get('previous_person',''))
                reflected=(item.get('operation')=='adicionar' and person in people
                           or item.get('operation')=='remover' and person not in people
                           or item.get('operation')=='substituir' and person in people and previous not in people)
                if reflected:
                    continue
                item={**item,'status':'escala-base disponível; alteração aguarda conferência'}
            current_pending.append(item)
    pending_unique=[];pending_seen=set()
    for item in current_pending:
        marker=json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if marker not in pending_seen:pending_seen.add(marker);pending_unique.append(item)
    data['escala_louvor_pendencias']=sorted(pending_unique,key=lambda r:str(r.get('date','')))
    unique=[];seen=set()
    for row in past:
        # Keep historical rosters in a month bucket. This is presentation and
        # indexing metadata only; archived rows never re-enter the active list.
        historical_date=exact_day(row.get('data') or row.get('date')) if isinstance(row,dict) else None
        if isinstance(row,dict):
            row.setdefault('mes_historico', historical_date[:7] if historical_date else 'data-a-confirmar')
        marker=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if marker not in seen:seen.add(marker);unique.append(row)
    data['escala_louvor_historico']=sorted(unique,key=lambda r:str(r.get('data','') or r.get('date','')),reverse=True)


def archive_past_scales(data: dict, today: str) -> None:
    """Keep expired and undated roster rows in month history, outside active schedules."""
    from datetime import date

    def parse_day(value):
        value = str(value or '').strip()[:10]
        for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
        return None

    def archive(key: str, rows: list, day_getter, existing_history=None):
        active, history = [], list(data.get(f'{key}_historico', [])) if isinstance(data.get(f'{key}_historico'), list) else []
        if isinstance(existing_history, list): history.extend(existing_history)
        for row in rows:
            day = parse_day(day_getter(row)) if isinstance(row, (dict, list, tuple)) else None
            if day and day.isoformat() >= today:
                active.append(row)
            else:
                item = dict(row) if isinstance(row, dict) else {'data': str(day_getter(row) or 'Data a confirmar'), 'responsavel': ' · '.join(map(str, row[1:])) if isinstance(row, (list, tuple)) else str(row)}
                iso = day.isoformat() if day else None
                item.setdefault('mes_historico', iso[:7] if iso else 'data-a-confirmar')
                item.setdefault('status_historico', 'escala passada; preservada no histórico' if day else 'data sem ano completo; preservada no histórico sem alterar escalas ativas')
                history.append(item)
        unique, seen = [], set()
        for row in history:
            if not isinstance(row, dict):
                continue
            day = parse_day(row.get('data') or row.get('date'))
            row.setdefault('mes_historico', day.isoformat()[:7] if day else 'data-a-confirmar')
            marker = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if marker not in seen:
                unique.append(row); seen.add(marker)
        data[key] = active
        data[f'{key}_historico'] = sorted(unique, key=lambda row: str(row.get('data') or row.get('date') or ''), reverse=True)

    for key in ('escala_pregacao',):
        rows = data.get(key)
        if isinstance(rows, list):
            archive(key, rows, lambda row: row.get('data') or row.get('date') if isinstance(row, dict) else row[0] if row else None)
    # Louvor has a dedicated merge path that already separates past rows and
    # must only carry valid full-year dates into the current roster.
    archive('escala_louvor', data.get('escala_louvor', []) if isinstance(data.get('escala_louvor'), list) else [], lambda row: row.get('data') if isinstance(row, dict) else None)
    reception = data.get('escala_recepcao')
    if isinstance(reception, dict):
        history = list(data.get('escala_recepcao_historico', [])) if isinstance(data.get('escala_recepcao_historico'), list) else []
        for field in ('domingos', 'eventos'):
            if isinstance(reception.get(field), list):
                rows = reception[field]
                active = []
                for row in rows:
                    raw = row.get('data') or row.get('date') if isinstance(row, dict) else row[0] if row else None
                    day = parse_day(raw)
                    if day and day.isoformat() >= today:
                        active.append(row)
                    else:
                        item = dict(row) if isinstance(row, dict) else {'data': str(raw or 'Data a confirmar'), 'responsavel': ' · '.join(map(str, row[1:])) if isinstance(row, (list, tuple)) else str(row)}
                        item.setdefault('mes_historico', day.isoformat()[:7] if day else 'data-a-confirmar')
                        item.setdefault('status_historico', 'escala passada; preservada no histórico' if day else 'data sem ano completo; preservada no histórico sem alterar escalas ativas')
                        history.append({'tipo': field, **item})
                reception[field] = active
        if history:
            unique, seen = [], set()
            for row in history:
                if not isinstance(row, dict): continue
                marker = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                if marker not in seen: unique.append(row); seen.add(marker)
            data['escala_recepcao_historico'] = unique
    ministries = data.get('escalas_ministerios')
    if isinstance(ministries, list):
        past = list(data.get('escalas_ministerios_historico', [])) if isinstance(data.get('escalas_ministerios_historico'), list) else []
        for ministry in ministries:
            if not isinstance(ministry, dict) or not isinstance(ministry.get('itens'), list):
                continue
            active = []
            for row in ministry['itens']:
                raw = row.get('data') or row.get('date') if isinstance(row, dict) else row[0] if isinstance(row, (list, tuple)) and row else None
                day = parse_day(raw)
                if day and day.isoformat() >= today:
                    active.append(row)
                else:
                    item = dict(row) if isinstance(row, dict) else {'data': str(raw or 'Data a confirmar'), 'responsavel': ' · '.join(map(str, row[1:])) if isinstance(row, (list, tuple)) else str(row)}
                    item.setdefault('mes_historico', day.isoformat()[:7] if day else 'data-a-confirmar')
                    item.setdefault('status_historico', 'escala passada; preservada no histórico' if day else 'data sem ano completo; preservada no histórico sem alterar escalas ativas')
                    past.append({'ministerio': ministry.get('ministerio'), **item})
            ministry['itens'] = active
        unique, seen = [], set()
        for row in past:
            marker = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if marker not in seen:
                unique.append(row); seen.add(marker)
        data['escalas_ministerios_historico'] = sorted(unique, key=lambda row: str(row.get('data') or row.get('date') or ''), reverse=True)

def publish() -> dict:
    now = datetime.now(ZONE).replace(microsecond=0)
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("existing_dashboard_unavailable") from None
    if not isinstance(data, dict):
        raise ValueError("existing_dashboard_invalid")

    ata = fixed_ata(data, now.date().isoformat())
    if ata is not None:
        # This private snapshot copy is persisted to Supabase with each day.
        # The web server removes this key from the generic panel/history APIs;
        # the ATA section remains available only through its restricted route.
        data[ATA_HISTORY_KEY] = ata

    statuses: list[dict[str, str]] = []
    stage_states: dict[str, bool] = {}
    try:
        run_state = json.loads(Path(os.environ.get("IFC_JOB_STATE", "/var/lib/ifc-job/state.json")).read_text(encoding="utf-8"))
        run_steps = run_state.get("steps", {}) if isinstance(run_state, dict) else {}
    except (OSError, json.JSONDecodeError):
        run_steps = {}

    def collection_failed(source: str) -> bool:
        step = run_steps.get(source, {}) if isinstance(run_steps, dict) else {}
        return isinstance(step, dict) and step.get("status") == "failed"

    def failure_note(source: str) -> str:
        step = run_steps.get(source, {}) if isinstance(run_steps, dict) else {}
        if not isinstance(step, dict) or step.get("status") != "failed":
            return ""
        code = step.get("failure_code")
        detail = f" Código técnico: {code}." if isinstance(code, str) and code.replace("_", "").isalnum() else ""
        status = step.get("http_status")
        if type(status) is int and 400 <= status <= 599:
            detail += f" HTTP {status}."
        return detail or " A coleta desta atualização falhou; o dado anterior foi preservado."
    try:
        curation = json.loads((ROOT / "curation" / "ifc-curation.json").read_text(encoding="utf-8"))
        curation_sections = curation.get("sections", {}) if isinstance(curation, dict) else {}
        declared_model = curation.get("model_claimed_by_agent", "modelo não registrado")
        declared_reasoning = curation.get("reasoning_claimed_by_agent", "")
    except (OSError, json.JSONDecodeError):
        curation = {}
        curation_sections = {}
        declared_model = "modelo não registrado"
        declared_reasoning = ""
    if declared_model == "gpt-6-luna":
        model_label = "GPT-6 Luna via Codex Auth"
    elif declared_model in {"tudo", "freeai/tudo"}:
        model_label = "alias LiteLLM tudo; deployment upstream não identificado"
    else:
        model_label = str(declared_model)
    reasoning_label = f", raciocínio {declared_reasoning}" if declared_reasoning else ""
    model_suffix = f" Modelo registrado para esta curadoria: {model_label}{reasoning_label}."
    baptisms = load_stage("batismos_planilha.json")
    if isinstance(baptisms, dict) and stage_is_current(baptisms, now) and isinstance(baptisms.get("registros"), list):
        data["batismo"] = baptisms
        statuses.append(record("batismos_planilha", "Atualizada" if baptisms.get("copies_match") else "Atenção: cópias divergentes", "Fonte: planilha da secretaria; comparação com a cópia do Filipe."))
    else:
        statuses.append(record("batismos_planilha", "Pendente: consulta atual da planilha", "Último conteúdo de batismos preservado."))
    prayer_book = load_stage("../curation/prayer-book.json")
    prayer_sources = {name: load_stage(SOURCE_FILES[name]) for name in ("evolution_grupos", "evolution_mulheres", "mensagens_diretas")}
    prayer_stamps = {name: value.get("generated_at") for name, value in prayer_sources.items() if isinstance(value, dict)}
    if (isinstance(prayer_book, dict) and stage_is_current(prayer_book, now)
            and prayer_book.get("source_timestamps") == prayer_stamps
            and len(prayer_stamps) == 3 and isinstance(prayer_book.get("pedidos"), list)):
        data["livro_oracao"] = prayer_book
        statuses.append(record("livro_oracao", "Histórico preservado; atualizações validadas", f"{len(prayer_book['pedidos'])} registro(s); {prayer_book.get('validated_updates', 0)} alteração(ões) comprovada(s) nesta coleta."))
    else:
        statuses.append(record("livro_oracao", "Pendente: atualização do Livro de Oração", "Último conteúdo publicado preservado; curadoria atual não confirmada."))
    for source, filename in SOURCE_FILES.items():
        value = load_stage(filename)
        ok = value is not None and stage_is_current(value, now)
        stage_states[source] = ok
        if not ok:
            if source=="youtube":
                previous=data.get("youtube_curadoria") or data.get("youtube_curadoria_historica")
                if isinstance(previous,dict):data["youtube_curadoria_historica"]={**previous,"titulo":"Última curadoria do YouTube · histórico"}
                data.pop("youtube_curadoria",None)
                if isinstance(data.get("youtube_publico"),dict):data["youtube_publico"]["curation_current"]=False
            statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else "Pendente: coleta atual indisponível",
                                   "Dados publicados anteriores preservados." + last_capture_note(value) + failure_note(source)))
            continue
        if source == "youtube":
            videos=value.get("videos")
            if isinstance(videos,list):
                def public_video(video):
                    result={key:video[key] for key in ("data","titulo","descricao","url","caption_language","source_tab") if key in video}
                    relative=video.get("published_relative")
                    if isinstance(relative,str) and relative:
                        result["published_display"]=relative
                    else:
                        try:
                            published=datetime.fromisoformat(str(video.get("data","")).replace("Z","+00:00"))
                            if published.tzinfo is None:published=published.replace(tzinfo=ZONE)
                            result["published_display"]=published.astimezone(ZONE).strftime("%d/%m/%Y %H:%M")
                        except ValueError:
                            result["published_display"]="Data de publicação não disponível"
                    result["caption_status"]={"transcribed":"Legenda transcrita","retry_pending":"Nova tentativa pendente","not_attempted_capacity":"Aguardando tentativa","unavailable_after_three_attempts":"Sem legenda após três tentativas","metadata_only":"Somente metadados"}.get(video.get("caption_status"),"Somente metadados")
                    return result
                recent=value.get("recent_24h",[])
                curated=curation_sections.get("youtube") if isinstance(curation_sections,dict) else None
                current_curation=(not collection_failed(source) and isinstance(curation.get("source_timestamps"),dict) and curation["source_timestamps"].get("youtube")==value.get("generated_at"))
                data["youtube_publico"]={"videos":[public_video(item) for item in videos if isinstance(item,dict)],"recent_24h":[public_video(item) for item in recent if isinstance(item,dict)],"coverage_24h_complete":value.get("coverage_24h_complete",False),"generated_at":value.get("generated_at"),"window_start":value.get("window_start"),"window_end":value.get("window_end"),"collection_status":value.get("collection_status","current"),"caption_coverage":value.get("caption_coverage",{}),"limitations":value.get("limitations"),"curation_current":bool(isinstance(curated,dict) and current_curation)}
                if isinstance(curated,dict) and current_curation:
                    data["youtube_curadoria"]={"titulo":"Pregações recentes · curadoria de conteúdo",**curated}
                    data.pop("youtube_curadoria_historica",None)
                else:
                    previous=data.get("youtube_curadoria") or data.get("youtube_curadoria_historica")
                    if isinstance(previous,dict):data["youtube_curadoria_historica"]={**previous,"titulo":"Última curadoria do YouTube · histórico"}
                    data.pop("youtube_curadoria",None)
                recent_transcribed=sum(item.get("caption_status")=="transcribed" for item in recent if isinstance(item,dict))
                recent_missing=sum(item.get("caption_status")!="transcribed" for item in recent if isinstance(item,dict))
                caption_detail=(f"{recent_transcribed} legenda(s) transcrita(s) e {recent_missing} sem transcrição disponível."
                                if recent else "Nenhuma publicação na janela da última captura; não há legenda para analisar nessa janela.")
                stale_snapshot=value.get("collection_status")=="stale_snapshot"
                normal_status=("Atenção: última captura preservada; coleta atual indisponível" if stale_snapshot else ("Legenda e conteúdo curados" if isinstance(curated,dict) and current_curation else ("Metadados atualizados; curadoria pendente" if value.get("coverage_24h_complete") else "Atenção: feed limitado")))
                capture_note = last_capture_note(value)
                source_note=" Metadados lidos da página pública do canal." if value.get("metadata_source")=="public_channel_page_reader" else ""
                detail=(f"Acervo preservado: {len(videos)} vídeo(s); a última janela coletada contém {len(recent)} publicação(ões), "
                        f"{caption_detail} "
                        f"{capture_note.strip() or 'Data da captura indisponível.'}{' Coleta atual indisponível; conteúdo histórico, fora da confirmação das últimas 24 horas.' if stale_snapshot else ''}{source_note} Legendas privadas ao curador; o painel exibe síntese, não a transcrição integral.")
                statuses.append(record(source,"Falhou; último dado preservado" if collection_failed(source) else normal_status,detail+failure_note(source)))
            else:statuses.append(record(source,"Pendente: formato inválido","Metadados anteriores preservados."))
        elif source == "instagram":
            curated = curation_sections.get("instagram")
            if isinstance(curated, dict):
                coverage_complete = value.get("coverage_24h_complete") is True
                publications = []
                for item in value.get("messages", []):
                    if not isinstance(item, dict):
                        continue
                    link = item.get("url")
                    try:
                        parsed = urlsplit(link) if isinstance(link, str) else None
                    except ValueError:
                        parsed = None
                    if parsed and parsed.scheme == "https" and parsed.hostname in {"instagram.com", "www.instagram.com"}:
                        publications.append({"at": item.get("at"), "type": item.get("type"), "url": link})
                data["instagram"] = {"titulo": "Instagram da IFC", "coverage_24h_complete": coverage_complete,
                                     "window_start": value.get("window_start"), "window_end": value.get("window_end"),
                                     "publications_count": len(value.get("messages", [])) if isinstance(value.get("messages"), list) else 0,
                                     "collection_status": "stale_snapshot" if collection_failed(source) else "current",
                                     "publications": publications, **curated}
                count = len(value.get("messages", []))
                coverage_status = "janela completa" if coverage_complete else "cobertura incompleta ou não confirmada"
                normal_status = "Coletada e curada por IA" if coverage_complete else "Atenção: cobertura incompleta"
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else normal_status,
                                       f"{count} publicação(ões) retornada(s) na janela de 24 horas; {coverage_status}; análise das legendas, sem interpretação das mídias.{failure_note(source)}{model_suffix}"))
            else:
                statuses.append(record(source, "Pendente: curadoria das publicações", "Legendas coletadas; aguardando análise atual."))
        elif source == "visitantes":
            data["visitantes"] = {key: value.get(key) for key in ("total", "ultimos_30_dias", "ultimos_7_dias", "datas_nao_identificadas", "enviadas_sem_resposta_registrada", "recentes", "nota")}
            statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else "Atualizada",
                                   f"Cadastros e etapas de acompanhamento lidos da tabela Baserow operada pelo n8n, sem dados de contato. {value.get('datas_nao_identificadas', 0)} cadastro(s) sem data reconhecível não entram nos recortes de 7/30 dias." + failure_note(source)))
        elif source == "drive_secretaria":
            files = value.get("files")
            curated = curation_sections.get("drive_secretaria")
            if value.get("scope") == "secretary_drive_documents" and isinstance(curated, dict):
                data["drive_curadoria"] = {"titulo": "Documentos da secretaria", **curated}
                documents = []
                for item in files if isinstance(files, list) else []:
                    if not isinstance(item, dict):
                        continue
                    link = item.get("webViewLink")
                    try:
                        parsed = urlsplit(link) if isinstance(link, str) else None
                    except ValueError:
                        parsed = None
                    safe_link = link if parsed and parsed.scheme == "https" and parsed.hostname in {"drive.google.com", "docs.google.com"} and not parsed.username and not parsed.password else ""
                    documents.append({"name": item.get("name", ""), "mimeType": item.get("mimeType", ""),
                                      "modifiedTime": item.get("modifiedTime", ""), "url": safe_link})
                data["drive_documentos"] = documents
                pending = len(value.get("pending_documents", []))
                normal_status = "Pendente: formatos não processados" if pending else "Coletada e curada por IA"
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else normal_status,
                                       f"{len(value.get('messages', []))} fragmento(s) analisado(s); {pending} documento(s) com conteúdo pendente. Índice e guia são referências, não eventos atuais.{failure_note(source)}{model_suffix}"))
            elif not isinstance(files, list) or value.get("scope") not in {"secretary_drive_metadata_only", "secretary_drive_documents"}:
                stage_states[source] = False
                statuses.append(record(source, "Pendente: formato inválido", "Metadados do Drive não confirmados."))
            elif files:
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else "Pendente: curadoria de documentos",
                                       f"{len(files)} arquivo(s) alterado(s) na janela consultada; somente metadados verificados, conteúdo ainda não analisado." + failure_note(source)))
            else:
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else "Metadados verificados",
                                       "Nenhum arquivo alterado nas últimas 24 horas. Consulta não inclui leitura ou organização dos documentos." + failure_note(source)))
        elif source == "agenda":
            events = value.get("events")
            if isinstance(events, list) and all(isinstance(x, dict) for x in events):
                data["eventos"] = [
                    {key: item[key] for key in ("date", "title", "location", "end", "description", "organizer", "attendees", "attendee_count", "attendees_omitted", "attendee_rsvp_counts", "all_day", "status", "recurring", "event_url") if item.get(key) not in (None, "")}
                    for item in events
                ]
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else "Atualizada",
                                       f"{len(events)} evento(s) na agenda da secretaria." + failure_note(source)))
            else:
                stage_states[source] = False
                statuses.append(record(source, "Pendente: formato inválido", "Dados publicados anteriores preservados."))
        elif source == "n8n":
            workflows = value.get("workflows")
            if isinstance(workflows, list) and all(isinstance(x, dict) for x in workflows):
                data["n8n"] = [
                    {"ativo": bool(item.get("active")), "saude": str(item.get("health", "não verificado")),
                     "execucoes_7d": int(item.get("runs_7d", 0)), "erros_7d": int(item.get("errors_7d", 0)),
                     "ultima_execucao": item.get("last_started_at")}
                    for item in workflows
                ]
                errors = sum(int(item.get("errors_7d", 0)) for item in workflows)
                normal_status = "Atualizada" if errors == 0 else "Atenção"
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else normal_status,
                                       f"{len(workflows)} workflow(s); {errors} erro(s) em 7 dias." + failure_note(source)))
            else:
                stage_states[source] = False
                statuses.append(record(source, "Pendente: formato inválido", "Dados publicados anteriores preservados."))
        elif source == "financeiro":
            monthly = value.get("mensal")
            categories = value.get("categorias")
            if isinstance(monthly, list) and isinstance(categories, list):
                data["financeiro"] = {
                    "titulo": "Resumo financeiro da igreja",
                    "max_data": value.get("max_data"),
                    "ult_import": value.get("ult_import"),
                    "sem_data": value.get("sem_data", {"n": 0, "valor_bruto": 0}),
                    "mensal": monthly,
                    "saldo_inicial": value.get("saldo_inicial", {}),
                    "por_ano_ate_mes": value.get("por_ano_ate_mes", []),
                    "categorias": categories,
                    "generated_at": value.get("generated_at"),
                }
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else "Atualizada",
                                       f"Fonte: dashboard financeiro IFC no Supabase; totais dos últimos 12 meses e {len(categories)} agrupamento(s) de categorias; {value.get('sem_data', {}).get('n', 0)} lançamento(s) sem data válida; sem dados por pessoa." + failure_note(source)))
            else:
                stage_states[source] = False
                statuses.append(record(source, "Pendente: formato inválido", "Dados publicados anteriores preservados."))
        elif source == "todoist":
            tasks = value.get("tasks")
            if isinstance(tasks, list) and all(isinstance(x, dict) for x in tasks):
                data["pendencias_todoist"] = [
                    {"tarefa": str(item.get("content", ""))[:240],
                     "prazo": item.get("due_date") or item.get("deadline_date"),
                     "prazo_tipo": "Prazo" if item.get("due_date") else "Limite final" if item.get("deadline_date") else "",
                     "horario": item.get("due_time"),
                     "prazo_descricao": str(item.get("due_description", ""))[:100],
                     "limite_final": item.get("deadline_date"),
                     "descricao": str(item.get("description", ""))[:1200],
                     "etiquetas": item.get("labels", []) if isinstance(item.get("labels"), list) else [],
                     "recorrente": item.get("is_recurring") is True,
                     "duracao": str(item.get("duration", ""))[:40],
                     "subtarefa": item.get("has_parent") is True,
                     "estado": item.get("status", "sem_prazo"),
                     "prioridade": int(item.get("priority", 1)) if str(item.get("priority", 1)).isdigit() else 1}
                    for item in tasks if isinstance(item.get("content"), str) and item.get("content", "").strip()
                ]
                counts = {state: sum(1 for task in data["pendencias_todoist"] if task.get("estado") == state)
                          for state in ("atrasada", "hoje", "futura", "sem_prazo", "prazo_nao_interpretado")}
                coverage = "coleta completa" if value.get("coverage_complete") is True else "cobertura incompleta ou não confirmada"
                detail = (f"{len(data['pendencias_todoist'])} tarefa(s) aberta(s): {counts['atrasada']} atrasada(s), "
                          f"{counts['hoje']} para hoje, {counts['futura']} futura(s), {counts['sem_prazo']} sem prazo; "
                          f"{coverage}; leitura somente.")
                normal_status = "Atualizada" if value.get("coverage_complete") is True else "Atenção: cobertura incompleta"
                statuses.append(record(source, "Falhou; último dado preservado" if collection_failed(source) else normal_status,
                                       detail + failure_note(source)))
            else:
                stage_states[source] = False
                statuses.append(record(source, "Pendente: formato inválido", "Dados publicados anteriores preservados."))
        elif source == "evolution_grupos":
            groups = value.get("groups", [])
            messages = value.get("messages", [])
            count = len(messages) if isinstance(messages, list) else 0
            category_counts = {}
            for item in groups if isinstance(groups, list) else []:
                if isinstance(item, dict) and isinstance(item.get("category"), str):
                    category = item["category"]
                    bucket = category_counts.setdefault(category, {"categoria": category, "grupos": 0, "mensagens": 0})
                    bucket["grupos"] += 1
                    if isinstance(item.get("messages"), int): bucket["mensagens"] += item["messages"]
            category_order = {"IFC": 0, "COBIM": 1, "Família": 2, "CONPASJ": 3, "Amigos": 4, "Pastores": 5, "Condomínio": 6}
            category_summary = sorted(category_counts.values(), key=lambda x: category_order.get(x["categoria"], 99))
            coverage = media_coverage(messages)
            statuses.extend(media_status_rows("grupos", coverage))
            curated = curation_sections.get("grupos") if isinstance(curation_sections, dict) else None
            if isinstance(curated, dict):
                record_coverage = curation.get("record_coverage", {}) if isinstance(curation, dict) else {}
                group_record_coverage = record_coverage.get("grupos") if isinstance(record_coverage, dict) else None
                data["grupos"] = {"titulo": "Síntese dos grupos WhatsApp", **curated, "cobertura_midia": coverage,
                                  "midias_pendentes": pending_media(messages, "groups"),
                                  "cobertura_categorias": category_summary,
                                  "catalogo_grupos": groups if isinstance(groups, list) else [],
                                  "janela_coleta": {
                                      "status": "stale_snapshot" if collection_failed(source) else "current",
                                      "inicio": value.get("window_start"), "fim": value.get("window_end"),
                                      "capturada_em": value.get("generated_at"),
                                      "grupos_consultados": len(groups) if isinstance(groups, list) else 0,
                                      "categorias_consultadas": len(category_summary),
                                      "mensagens_retornadas": count,
                                      "registros_curados": group_record_coverage.get("covered") if isinstance(group_record_coverage, dict) else None,
                                      "registros_esperados": group_record_coverage.get("total") if isinstance(group_record_coverage, dict) else None,
                                  }}
                data["destaques"] = curated.get("insights", [])
                if collection_failed(source):
                    statuses.append(record(source, "Falhou; última coleta preservada", f"A síntese usa o último lote disponível ({len(groups) if isinstance(groups, list) else 0} grupos em {len(category_summary)} categorias, {count} mensagens); a coleta desta atualização falhou.{failure_note(source)}{model_suffix}"))
                else:
                    statuses.append(record(source, "Coletada e curada por IA", f"{len(groups) if isinstance(groups, list) else 0} grupos em {len(category_summary)} categorias, {count} mensagens analisadas.{model_suffix}"))
            else:
                statuses.append(record(source, "Coletada; curadoria IA pendente", f"{len(groups) if isinstance(groups, list) else 0} grupos, {count} mensagens. Síntese detalhada e insights ainda não revisados por modelo."))
        elif source == "evolution_mulheres":
            messages = value.get("messages", [])
            count = len(messages) if isinstance(messages, list) else 0
            coverage = media_coverage(messages)
            statuses.extend(media_status_rows("mulheres", coverage))
            curated = curation_sections.get("mulheres") if isinstance(curation_sections, dict) else None
            if isinstance(curated, dict):
                data["mulheres"] = {"titulo": "Síntese do Ministério de Mulheres", **curated, "cobertura_midia": coverage,
                                    "midias_pendentes": pending_media(messages, "women")}
                if collection_failed(source):
                    statuses.append(record(source, "Falhou; última coleta preservada", f"A síntese usa o último lote disponível ({count} mensagem(ns)); a coleta desta atualização falhou.{failure_note(source)}{model_suffix}"))
                else:
                    statuses.append(record(source, "Coletada e curada por IA", f"{count} mensagem(ns) analisadas.{model_suffix}"))
            else:
                statuses.append(record(source, "Coletada; curadoria IA pendente", f"{count} mensagem(ns) do grupo Mulheres IFC; síntese detalhada e insights ainda não revisados por modelo."))
        elif source == "mensagens_diretas":
            messages = value.get("messages", [])
            count = len(messages) if isinstance(messages, list) else 0
            coverage = media_coverage(messages)
            statuses.extend(media_status_rows("diretas", coverage))
            curated = curation_sections.get("mensagens_diretas") if isinstance(curation_sections, dict) else None
            if isinstance(curated, dict):
                data["diretas"] = {"titulo": "Síntese das mensagens privadas de líderes e administradores", **curated, "cobertura_midia": coverage,
                                   "midias_pendentes": pending_media(messages, "direct")}
                if collection_failed(source):
                    statuses.append(record(source, "Falhou; última coleta preservada", f"A síntese usa o último lote disponível ({count} mensagens); a coleta desta atualização falhou.{failure_note(source)}{model_suffix}"))
                else:
                    statuses.append(record(source, "Coletada e curada por IA", f"{count} mensagens de conversas privadas da lista autorizada; todos os assuntos incluídos.{model_suffix}"))
            else:
                statuses.append(record(source, "Coletada; curadoria IA pendente", f"{count} mensagens privadas de líderes e administradores; síntese aguardando curadoria detalhada."))
        else:
            messages = value.get("messages", [])
            count = len(messages) if isinstance(messages, list) else 0
            curated = curation_sections.get("email_secretaria") if isinstance(curation_sections, dict) else None
            data["email_mensagens"] = [
                {key: message.get(key, "") for key in ("mailbox", "received_at", "sender", "subject", "type")}
                for message in messages if isinstance(message, dict) and message.get("type") != "texto de anexo de e-mail"
            ] if isinstance(messages, list) else []
            mailbox_counts = value.get("mailboxes") if isinstance(value.get("mailboxes"), dict) else {}
            mailbox_labels = {key: str(key).replace("_", " ") for key in mailbox_counts}
            message_count = sum(mailbox_counts[key] for key in mailbox_labels if type(mailbox_counts.get(key)) is int)
            if not any(type(mailbox_counts.get(key)) is int for key in mailbox_labels):
                message_count = count
            mailbox_note = "Caixas na janela de 24h: " + "; ".join(
                f"{label} {mailbox_counts[key]}" for key, label in mailbox_labels.items()
                if type(mailbox_counts.get(key)) is int
            ) + ". " if any(type(mailbox_counts.get(key)) is int for key in mailbox_labels) else ""
            if isinstance(curated, dict):
                data["email_curadoria"] = {"titulo": "Síntese de e-mails da secretaria", **curated}
                attachments = sum('anexos pendentes:' in str(x.get('type','')) for x in messages if isinstance(x,dict))
                missing = sum('corpo indisponível' in str(x.get('type','')) for x in messages if isinstance(x,dict))
                attachment_texts = sum(str(x.get('type','')) == 'texto de anexo de e-mail' for x in messages if isinstance(x,dict))
                attachment_unknown = sum('status de anexos desconhecido' in str(x.get('type','')) for x in messages if isinstance(x,dict))
                body_incomplete = value.get('body_coverage_complete') is False
                attachment_incomplete = value.get('attachment_coverage_complete') is False
                partial = attachments or missing or attachment_unknown or body_incomplete or attachment_incomplete
                status_text = "Falhou; última coleta preservada" if collection_failed(source) else ("Atenção: conteúdo parcial" if partial else "Coletada e curada por IA")
                details = (f"{message_count} e-mail(s) coletados; {attachment_texts} texto(s) de anexo extraído(s) e incluído(s) na curadoria; "
                           f"{missing} sem corpo, {attachments} com anexos pendentes e {attachment_unknown} com estado de anexo desconhecido. ")
                if body_incomplete and not missing:
                    details += "A cobertura de corpos foi marcada incompleta. "
                if attachment_incomplete and not attachments and not attachment_unknown:
                    details += "A cobertura de anexos foi marcada incompleta. "
                statuses.append(record(source, status_text, details + mailbox_note + failure_note(source) + model_suffix))
            else:
                statuses.append(record(source, "Coletada; curadoria IA pendente", f"{message_count} e-mail(s) coletados; revisão do conteúdo ainda não consolidada. {mailbox_note}"))

    statuses.append(record(
        "notion", "Fora da rotina diária",
        "Por orientação do Filipe, o Notion não é consultado diariamente. Esta atualização não confirma a disponibilidade atual das bases."
    ))
    baptism_copy_note = "duas cópias comparadas" if isinstance(data.get("batismo"), dict) and data["batismo"].get("copies_match") else "cópias com divergência ou coleta pendente"
    statuses.append(record(
        "planilhas", "Cobertura por coletores especializados",
        f"Batizandos IFC: leitura dedicada das cópias da secretaria e do Filipe ({baptism_copy_note}). Livro de Oração: alimentado pelas mensagens IFC e sincronizado às planilhas; não há outra planilha registrada como fonte."
    ))
    statuses.append(record("ata", "Referência fixa", "ATA carregada uma vez e reaproveitada nos snapshots diários; sem consulta diária à fonte."))

    coverage_by_status = {
        "drive_secretaria": "drive_secretaria", "instagram": "instagram",
        "evolution_grupos": "grupos", "evolution_mulheres": "mulheres",
        "mensagens_diretas": "mensagens_diretas", "email_secretaria": "email_secretaria",
        "youtube": "youtube",
    }
    question_source_by_status = {
        "evolution_grupos": "grupos", "evolution_mulheres": "mulheres",
        "mensagens_diretas": "mensagens_diretas", "email_secretaria": "email_secretaria",
    }
    coverage = curation.get("record_coverage", {}) if isinstance(curation, dict) else {}
    question_reviews = curation.get("question_review") if isinstance(curation, dict) else None
    request_reviews = curation.get("request_review") if isinstance(curation, dict) else None
    def review_disposition_counts(reviews):
        counts = {}
        if isinstance(reviews, list):
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                source, disposition = review.get("source"), review.get("disposition")
                if not isinstance(source, str) or not isinstance(disposition, str):
                    continue
                source_counts = counts.setdefault(source, {})
                source_counts[disposition] = source_counts.get(disposition, 0) + 1
        return counts

    question_review_dispositions = review_disposition_counts(question_reviews)
    request_review_dispositions = review_disposition_counts(request_reviews)
    question_review_counts = {source: sum(items.values()) for source, items in question_review_dispositions.items()}
    request_review_counts = {source: sum(items.values()) for source, items in request_review_dispositions.items()}
    evidence = curation.get("evidence", []) if isinstance(curation, dict) else []
    evidence_by_source = {}
    if isinstance(evidence, list):
        for citation in evidence:
            if isinstance(citation, dict) and isinstance(citation.get("source"), str):
                evidence_by_source.setdefault(citation["source"], []).append(citation)
    for key, section_key in (("grupos", "grupos"), ("mulheres", "mulheres"), ("mensagens_diretas", "diretas"), ("email_secretaria", "email_curadoria"), ("instagram", "instagram"), ("drive_secretaria", "drive_curadoria"), ("youtube", "youtube_curadoria")):
        if isinstance(data.get(section_key), dict) and isinstance(evidence_by_source.get(key), list):
            data[section_key]["evidencias"] = evidence_by_source[key]
    expected_coverage_stamps = {}
    for key, filename in SOURCE_FILES.items():
        if key not in {"drive_secretaria", "instagram", "evolution_grupos", "evolution_mulheres", "mensagens_diretas", "email_secretaria", "youtube"}:
            continue
        staged = load_stage(filename)
        if isinstance(staged, dict) and stage_is_current(staged, now):
            expected_coverage_stamps[key] = staged.get("generated_at", staged.get("checked_at"))
    coverage_current = (isinstance(coverage, dict) and isinstance(curation, dict)
                        and curation.get("source_timestamps") == expected_coverage_stamps)
    if coverage_current:
        for item in statuses:
            source_key = coverage_by_status.get(item.get("fonte"))
            measured = coverage.get(source_key) if source_key else None
            if (isinstance(measured, dict) and type(measured.get("covered")) is int
                    and type(measured.get("total")) is int and measured["covered"] == measured["total"]):
                item["detalhe"] += f" Índices de entrada da curadoria validados: {measured['covered']}/{measured['total']}."
            question_source = question_source_by_status.get(item.get("fonte"))
            if question_source:
                if isinstance(question_reviews, list):
                    question_count = question_review_counts.get(question_source, 0)
                    request_count = request_review_counts.get(question_source, 0) if isinstance(request_reviews, list) else None
                    if request_count is None:
                        item["detalhe"] += f" Perguntas literais revisadas pela curadoria: {question_count}; revisão de pedidos sem interrogação indisponível nesta curadoria."
                    else:
                        q = question_review_dispositions.get(question_source, {})
                        r = request_review_dispositions.get(question_source, {})
                        q_open = q.get("actionable_open", 0)
                        q_answered = q.get("answered", 0)
                        q_non_actionable = q.get("informational", 0) + q.get("rhetorical", 0)
                        r_open = r.get("actionable_open", 0)
                        r_answered = r.get("answered", 0)
                        r_non_actionable = r.get("informational", 0) + r.get("not_a_request", 0)
                        item["detalhe"] += (
                            f" Revisão estruturada: {question_count} pergunta(s) literal(is) "
                            f"({q_open} em aberto, {q_answered} respondida(s), {q_non_actionable} informativa(s)/retórica(s)); "
                            f"{request_count} sinal(is) de pedido sem interrogação "
                            f"({r_open} em aberto, {r_answered} respondido(s), {r_non_actionable} informativo(s)/falso(s) positivo(s))."
                        )
                else:
                    item["detalhe"] += " Revisão estruturada de perguntas não disponível nesta curadoria."
        source_labels = {"grupos": "Grupos WhatsApp", "mulheres": "Mulheres IFC",
                         "mensagens_diretas": "Mensagens diretas", "email_secretaria": "E-mail da secretaria"}
        disposition_labels = {"actionable_open": "Em aberto", "answered": "Respondida / resolvida",
                              "informational": "Informativa", "rhetorical": "Retórica", "not_a_request": "Não era pedido"}
        review_rows = []
        for collection, kind, index_key in ((question_reviews, "Pergunta literal", "question_index"),
                                             (request_reviews, "Sinal de pedido", "request_index")):
            if not isinstance(collection, list):
                continue
            for review in collection:
                if not isinstance(review, dict) or review.get("source") not in source_labels or not isinstance(review.get("quote"), str):
                    continue
                section = curation_sections.get(review["source"], {}) if isinstance(curation_sections, dict) else {}
                items = section.get(review.get("field"), []) if isinstance(section, dict) else []
                item_index = review.get("item_index")
                target = items[item_index] if isinstance(items, list) and type(item_index) is int and 0 <= item_index < len(items) else ""
                review_rows.append({"fonte": source_labels[review["source"]], "tipo": kind,
                                    "trecho": safe_review_quote(review["quote"]),
                                    "classificacao": disposition_labels.get(review.get("disposition"), "Não classificada"),
                                    "resultado": target, "registro": (review.get("record_index", -1) + 1) if type(review.get("record_index")) is int else "—"})
        data["revisao_perguntas_pedidos"] = review_rows
    else:
        data.pop("revisao_perguntas_pedidos", None)
    data["atualizado_em"] = now.strftime("%d/%m/%Y às %H:%M")
    data["hoje"] = now.date().isoformat()
    detailed=load_stage('../curation/detailed.json')
    if isinstance(detailed,dict) and detailed.get('source_timestamps')==curation.get('source_timestamps'):
        data['conversas_por_pessoa']=detailed.get('conversations',[])
        data['conversas_historico']=detailed.get('conversation_history',[])
        merge_worship(data,detailed.get('worship_updates',[]))
    else:
        data.pop('conversas_por_pessoa',None)
        if isinstance(detailed,dict) and isinstance(detailed.get('conversations'),list):
            history=list(detailed.get('conversation_history',[])) if isinstance(detailed.get('conversation_history',[]),list) else []
            stamp=(detailed.get('source_timestamps') or {}).get('mensagens_diretas')
            if stamp and detailed['conversations'] and not any(isinstance(x,dict) and x.get('source_timestamp')==stamp for x in history):history.append({'source_timestamp':stamp,'conversations':detailed['conversations']})
            data['conversas_historico']=history
        statuses.append(record('curadoria_detalhada','Pendente','Resumos por pessoa e alterações de escala ainda não validados para esta coleta.'))
    participant_stage=load_stage('evolution_grupos.json') or {}
    current_directory=participant_stage.get('participantes_ifc')
    valid_current=isinstance(current_directory,dict) and current_directory.get('status')=='current' and current_directory.get('scope')=='IFC' and stage_is_current({'generated_at':current_directory.get('generated_at')},now)
    try:
        reviewed=json.loads(Path('/run/secrets/evolution-groups.json').read_text(encoding='utf-8'))
        allowed_ifc={normalized_group_name(group.get('name')) for group in reviewed.get('groups',[]) if isinstance(group,dict) and group.get('category')=='IFC' and group.get('name')}
    except (OSError,json.JSONDecodeError,AttributeError):
        allowed_ifc=set()
    if valid_current:
        current_groups=current_directory.get('groups')
        current_contacts=current_directory.get('contatos')
        valid_current=(isinstance(current_groups,list) and len(current_groups)==44 and
                       all(isinstance(group,dict) and group.get('category')=='IFC' and normalized_group_name(group.get('g')) in allowed_ifc for group in current_groups) and
                       isinstance(current_contacts,list) and 0<len(current_contacts)<=1000 and
                       current_directory.get('unique_contacts')==len(current_contacts))
        seen_contacts=set()
        if valid_current:
            for person in current_contacts:
                if not isinstance(person,dict) or not isinstance(person.get('nome'),str) or not isinstance(person.get('grupos'),list):
                    valid_current=False;break
                phone=str(person.get('telefone') or '')
                evolution_id=str(person.get('evolution_id') or '')
                if phone and not re.fullmatch(r'[0-9]{7,15}',phone):
                    valid_current=False;break
                if not re.fullmatch(r'[0-9]{7,20}(?:-[0-9]{5,20})?@(?:s.whatsapp.net|lid)|[0-9]{7,15}',evolution_id,re.I):
                    valid_current=False;break
                if any(normalized_group_name(group) not in allowed_ifc for group in person['grupos']):
                    valid_current=False;break
                identity='p:'+phone if phone else 'i:'+evolution_id.lower()
                if identity in seen_contacts:
                    valid_current=False;break
                seen_contacts.add(identity)
    if valid_current:
        directory=current_directory
        data['participantes_grupos']=directory
        statuses.append(record('participantes','Atualizada; IFC',f"{len(current_groups)} grupos IFC; {len(current_contacts)} contatos deduplicados, {int(current_directory.get('named_contacts',0))} com nome identificado e {int(current_directory.get('unresolved_contacts',0))} sem nome confiável. Coleta em {current_directory.get('generated_at')}."))
    else:
        directory=data.get('participantes_grupos')
        if isinstance(directory,dict) and isinstance(directory.get('grupos'),list):
            directory_groups=[group for group in directory['grupos'] if isinstance(group,dict)]
            try:
                reviewed_catalog=json.loads(Path('/run/secrets/evolution-groups.json').read_text(encoding='utf-8'))
                catalog=reviewed_catalog.get('groups',[]) if isinstance(reviewed_catalog,dict) else []
            except (OSError,json.JSONDecodeError):
                catalog=(data.get('grupos') or {}).get('catalogo_grupos',[])
            if not isinstance(catalog,list):catalog=[]
            # The people directory is scoped strictly to groups classified as IFC.
            # Unknown names and all other categories are excluded fail-closed.
            ifc_catalog={normalized_group_name(group.get('name')):group for group in catalog
                         if isinstance(group,dict) and group.get('name') and group.get('category')=='IFC'}
            directory_source_snapshot=None
            if not any(normalized_group_name(group.get('g')) in ifc_catalog for group in directory_groups):
                for snapshot_path in sorted(HISTORY.glob('????-??-??.json'),reverse=True):
                    try:
                        snapshot_data=json.loads(snapshot_path.read_text(encoding='utf-8'))
                        archived=snapshot_data.get('participantes_grupos')
                        archived_groups=archived.get('grupos',[]) if isinstance(archived,dict) else []
                    except (OSError,json.JSONDecodeError):
                        continue
                    if isinstance(archived_groups,list) and any(
                            isinstance(group,dict) and normalized_group_name(group.get('g')) in ifc_catalog
                            for group in archived_groups):
                        directory={**archived,'grupos':archived_groups}
                        directory_groups=[group for group in archived_groups if isinstance(group,dict)]
                        directory_source_snapshot=snapshot_data.get('hoje') or snapshot_path.stem
                        data['participantes_grupos']=directory
                        break
            grouped_directory={}
            for group in directory_groups:
                group_key=normalized_group_name(group.get('g'))
                if group_key not in ifc_catalog:
                    continue
                current=grouped_directory.setdefault(group_key,{**group,'n':[]})
                for name in (group.get('n') if isinstance(group.get('n'),list) else []):
                    if isinstance(name,str) and name.strip() and not any(
                            normalized_group_name(name)==normalized_group_name(existing)
                            for existing in current['n']):
                        current['n'].append(name.strip())
                for count_key in ('t','a','s'):
                    if isinstance(group.get(count_key),int):
                        current[count_key]=max(current.get(count_key,0),group[count_key])
            directory_groups=list(grouped_directory.values())
            directory['grupos']=directory_groups
            catalog_names=set(ifc_catalog)
            listed_names={normalized_group_name(group.get('g')) for group in directory_groups if group.get('g')}
            name_rows=sum(len(group.get('n',[])) for group in directory_groups if isinstance(group.get('n'),list))
            unique_names=len({normalized_group_name(name) for group in directory_groups for name in (group.get('n') if isinstance(group.get('n'),list) else []) if isinstance(name,str) and name.strip()})
            directory.update({
                'status':'partial_legacy_names',
                'scope':'IFC',
                'scope_category':'IFC',
                'directory_source_snapshot':directory_source_snapshot,
                'groups_listed':len(directory_groups),
                'catalog_groups':len(ifc_catalog),
                'catalog_group_name_count':len(catalog_names),
                'catalog_group_names_not_listed':len(catalog_names-listed_names),
                'group_names_not_matched':len(listed_names-catalog_names),
                'name_rows':name_rows,
                'unique_name_labels':unique_names,
                'phone_values_present':0,
                'evolution_ids_present':0,
            })
            directory_source_note=f" Origem: snapshot {directory_source_snapshot}." if directory_source_snapshot else ""
            statuses.append(record('participantes','Parcial; nomes legados',
                                   f"Lista antiga: {len(directory_groups)}/{len(ifc_catalog)} grupos IFC do catálogo, "
                                   f"{name_rows} entradas de nomes ({unique_names} rótulos distintos), "
                                   f"sem telefones ou IDs Evolution; {len(catalog_names-listed_names)} grupos do catálogo não aparecem na lista.{directory_source_note}"))
    data['fontes'] = statuses
    archive_past_scales(data, data['hoje'])
    data['atencao']=attention_items(data.get('pendencias_todoist',[]),statuses)
    data['agenda_cobertura']={k:v for k,v in (load_stage('agenda.json') or {}).items() if k in ('generated_at','window_start','window_end')}
    useful=data.get('links',[])
    if not isinstance(useful,list):useful=[]
    candidates=[{'titulo':'Canal oficial da IFC no YouTube','url':'https://www.youtube.com/igrejadafamiliacrista'},{'titulo':'Painel IFC','url':'https://openclaw.filipeivopereira.com/ifc-painel/'}]
    for owner,url in (data.get('batismo',{}).get('links',{}) or {}).items():candidates.append({'titulo':'Planilha de batizandos — '+owner,'url':url})
    for link in candidates:
        if not any(isinstance(x,dict) and x.get('url')==link['url'] for x in useful):useful.append(link)
    data['links']=useful
    atomic_write(OUTPUT, data)
    atomic_write(HISTORY / f"{now.date().isoformat()}.json", data)
    return {"status": "ok", "sources_fresh": sum(stage_states.values()), "sources_total": len(SOURCE_FILES), "published": True, "history_saved": True}


if __name__ == "__main__":
    try:
        print(json.dumps(publish(), ensure_ascii=False, separators=(",", ":")))
    except Exception:
        print('{"status":"failed","published":false}')
        raise SystemExit(1)
