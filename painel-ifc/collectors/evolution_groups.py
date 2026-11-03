#!/usr/bin/env python3
"""Read a fixed allowlist of IFC WhatsApp groups from Evolution into private staging."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import hashlib
import hmac
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import evolution_direct as audio_tools

BASE_URL = os.environ.get("EVOLUTION_BASE_URL", "").rstrip("/")
INSTANCE = os.environ.get("EVOLUTION_GROUPS_INSTANCE", "")
if not BASE_URL or not INSTANCE:
    raise SystemExit("evolution_groups_configuration_missing")
ENDPOINT = f"{BASE_URL}/chat/findMessages/{INSTANCE}"
API_KEY_FILE = Path(os.environ.get("EVOLUTION_KEY_FILE", "/run/secrets/evolution-apikey"))
GROUPS_FILE = Path(os.environ.get("EVOLUTION_GROUPS_FILE", "/run/secrets/evolution-groups.json"))
OUTPUT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data")) / "staging" / "evolution_grupos.json"
ZONE = ZoneInfo("America/Sao_Paulo")
PAGE_SIZE = 200
MAX_PAGES_PER_GROUP = 20
MAX_MESSAGES_TOTAL = 20000
TIMEOUT_SECONDS = 20
DB_READER_URL = os.environ.get("IFC_GROUP_READER_URL", "http://ifc-direct-db-reader:8790/v1/group-messages")
PARTICIPANT_NAMES_READER_URL = os.environ.get("IFC_GROUP_PARTICIPANT_NAMES_URL", "http://ifc-direct-db-reader:8790/v1/group-participant-names")
DB_READER_TOKEN_FILE = Path(os.environ.get("IFC_GROUP_READER_TOKEN_FILE", "/run/secrets/ifc-direct-reader.token"))

PHONE_RE = re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
JID_RE = re.compile(r"\b[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)\b", re.I)
MENTION_RE = re.compile(r"(?<!\w)@\d{10,}(?!\d)")
SECRET_RE = re.compile(r"(?i)\b(?:senha|password|token|api[_ -]?key)\s*[:=]\s*\S+")
def now_brt() -> datetime:
    return datetime.now(ZONE).replace(microsecond=0)


def window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(hours=24), now


def scrub_text(value: str) -> str:
    value = SECRET_RE.sub("[removido]", value)
    value = CPF_RE.sub("[removido]", value)
    value = PHONE_RE.sub("[removido]", value)
    value = JID_RE.sub("[removido]", value)
    value = MENTION_RE.sub("[removido]", value)
    # Remove URL query/fragment strings that can carry private tokens.
    def scrub_url(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,);]")
        parsed = urlsplit(raw)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    value = re.sub(r"https?://[^\s<>]+", scrub_url, value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def message_text(record):
 return audio_tools.message_text(record)


def is_audio(record: dict) -> bool:
    return audio_tools.is_audio_record(record)


def timestamp(record: dict) -> int | None:
    value = record.get("messageTimestamp")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    value = int(value)
    return value // 1000 if value > 10_000_000_000 else value


def records_from(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("Evolution returned an unexpected response shape")
    records = payload.get("messages", payload.get("records", payload.get("data")))
    if isinstance(records, dict):
        records = records.get("records", records.get("messages", records.get("data")))
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError("Evolution message list is missing or malformed")
    return records


def fetch_page(api_key: str, remote_jid: str, page: int) -> list[dict]:
    body = {
        "where": {"key": {"remoteJid": remote_jid}},
        "limit": PAGE_SIZE,
        "page": page,
        "offset": (page - 1) * PAGE_SIZE,
        "sort": "desc",
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={"apikey": api_key, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise ValueError("Evolution search returned a non-success status")
                return records_from(json.loads(response.read(8 * 1024 * 1024)))
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise ValueError(f"Evolution search returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise ValueError("Evolution search failed") from None


def load_groups() -> list[dict[str, str]]:
    config = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("WhatsApp group catalog is invalid")
    groups = config.get("groups")
    if config.get("instance") != INSTANCE or not isinstance(groups, list) or not groups:
        raise ValueError("WhatsApp group catalog is invalid")
    expected_categories = {"IFC": 44, "COBIM": 6, "Família": 3, "CONPASJ": 1,
                           "Amigos": 3, "Pastores": 8, "Condomínio": 1}
    expected_count = config.get("expected_count")
    if type(expected_count) is not int or expected_count != sum(expected_categories.values()):
        raise ValueError("WhatsApp group catalog count is invalid")
    if config.get("category_counts") != expected_categories:
        raise ValueError("WhatsApp group catalog categories are invalid")
    output = []
    seen = set()
    category_counts = {category: 0 for category in expected_categories}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("WhatsApp group catalog entry is invalid")
        name, jid, category, description = (group.get("name"), group.get("remote_jid"),
                                            group.get("category"), group.get("description", ""))
        if not isinstance(name, str) or not name.strip() or not isinstance(jid, str) or not re.fullmatch(r"[0-9]{5,}(?:-[0-9]{5,})?@g\.us", jid):
            raise ValueError("WhatsApp group catalog entry is invalid")
        if category not in expected_categories or not isinstance(description, str) or len(description) > 300:
            raise ValueError("WhatsApp group category metadata is invalid")
        if jid in seen:
            raise ValueError("WhatsApp group catalog contains duplicates")
        seen.add(jid)
        category_counts[category] += 1
        output.append({"name": name.strip()[:100], "remote_jid": jid,
                       "category": category, "description": description.strip()})
    if len(output) != expected_count or category_counts != expected_categories:
        raise ValueError("WhatsApp group catalog does not match the reviewed category counts")
    return output


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".evolution-grupos-", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def fetch_ifc_participants(api_key: str, groups: list[dict], reader_token: str, generated: datetime) -> dict:
    ifc_groups=[group for group in groups if group.get('category')=='IFC']
    if len(ifc_groups)!=44:
        raise ValueError('IFC participant catalog is incomplete')
    url=f"{BASE_URL}/group/fetchAllGroups/{INSTANCE}?getParticipants=true"
    request=urllib.request.Request(url,headers={'apikey':api_key,'Accept':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=180) as response:
            payload=json.loads(response.read(60*1024*1024))
    except (urllib.error.URLError,TimeoutError,json.JSONDecodeError):
        raise ValueError('IFC participant source unavailable') from None
    rows=payload.get('groups',payload.get('data',payload)) if isinstance(payload,dict) else payload
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ValueError('IFC participant source shape invalid')
    def group_jid(row):
        for field in ('remote_jid','remoteJid','jid','id','groupJid'):
            value=row.get(field)
            if isinstance(value,str) and value.endswith('@g.us'):
                return value
        return ''
    live={group_jid(row):row for row in rows if group_jid(row)}
    if any(group['remote_jid'] not in live for group in ifc_groups):
        raise ValueError('IFC participant group coverage incomplete')
    memberships=[]
    identities={}
    for group in ifc_groups:
        item=live[group['remote_jid']]
        people=item.get('participants')
        if people is None:
            people=item.get('participantsList')
        if not isinstance(people,list) or any(not isinstance(person,dict) for person in people):
            raise ValueError('IFC participant list shape invalid')
        for person in people:
            raw_id=person.get('id') or person.get('jid')
            phone=''.join(ch for ch in str(person.get('phoneNumber') or '') if ch.isdigit())
            if not phone and isinstance(raw_id,str) and raw_id.endswith('@s.whatsapp.net'):
                phone=''.join(ch for ch in raw_id.split('@',1)[0] if ch.isdigit())
            if len(phone)<7 or len(phone)>15:
                phone=''
            if not isinstance(raw_id,str) or not raw_id.strip():
                raw_id=f'{phone}@s.whatsapp.net' if phone else ''
            raw_id=raw_id.strip().lower()
            valid_id=bool(re.fullmatch(r'[0-9]{7,20}(?:-[0-9]{5,20})?@(?:s\.whatsapp\.net|lid)',raw_id) or re.fullmatch(r'[0-9]{7,15}',raw_id))
            if not valid_id:
                continue
            key=raw_id
            identities.setdefault(key,{'id':raw_id,'phone':phone,'api_name':'','groups':set()})
            current=identities[key]
            if phone and not current['phone']:
                current['phone']=phone
            api_name=person.get('pushName') or person.get('name') or person.get('notify')
            if isinstance(api_name,str) and api_name.strip() and not current['api_name']:
                current['api_name']=re.sub(r'\s+',' ',api_name).strip()[:100]
            current['groups'].add(group['name'])
    if len(identities)>1000:
        raise ValueError('IFC participant safety cap exceeded')
    request_rows=[{'id':person['id'],'phone':person['phone'] or None} for person in identities.values()]
    reader_names={}
    if request_rows:
        reader_request=urllib.request.Request(
            PARTICIPANT_NAMES_READER_URL,
            data=json.dumps({'participants':request_rows},separators=(',',':')).encode(),
            headers={'Authorization':'Bearer '+reader_token,'Accept':'application/json','Content-Type':'application/json'},
            method='POST')
        try:
            with urllib.request.urlopen(reader_request,timeout=200) as response:
                name_payload=json.loads(response.read(4*1024*1024))
            if isinstance(name_payload,dict) and name_payload.get('status')=='ok' and name_payload.get('scope')=='IFC':
                name_rows=name_payload.get('participants',[])
                if isinstance(name_rows,list):
                    reader_names={row.get('id','').lower():row for row in name_rows if isinstance(row,dict) and isinstance(row.get('id'),str)}
        except (urllib.error.URLError,TimeoutError,json.JSONDecodeError):
            reader_names={}
    contacts={}
    for person in identities.values():
        key='p:'+person['phone'] if person['phone'] else 'i:'+person['id']
        found=reader_names.get(person['id'],{})
        resolved=found.get('name') if isinstance(found.get('name'),str) and found.get('name').strip() else person['api_name']
        source=found.get('name_source') if resolved and found.get('name') else ('evolution_group_participant' if resolved else 'unresolved')
        observed=found.get('name_observed_at') if resolved and found.get('name') else None
        evolution_id=person['id']
        current=contacts.setdefault(key,{'nome':resolved or 'Não identificado','telefone':person['phone'],
            'evolution_id':evolution_id,'grupos':set(),'nome_fonte':source,'nome_observado_em':observed})
        if person['phone'] and not current['telefone']:
            current['telefone']=person['phone']
        if current['nome']=='Não identificado' and resolved:
            current.update({'nome':resolved,'nome_fonte':source,'nome_observado_em':observed})
        if '@s.whatsapp.net' in evolution_id and '@s.whatsapp.net' not in current['evolution_id']:
            current['evolution_id']=evolution_id
        current['grupos'].update(person['groups'])
    contact_rows=[{**person,'grupos':sorted(person['grupos'])} for person in contacts.values()]
    contact_rows.sort(key=lambda row:(row['nome'].casefold(),row['telefone'] or row['evolution_id']))
    group_rows=[]
    for group in ifc_groups:
        count=sum(1 for person in contacts.values() if group['name'] in person['grupos'])
        group_rows.append({'g':group['name'],'category':'IFC','t':count})
    return {'generated_at':generated.isoformat(timespec='seconds'),'scope':'IFC','status':'current',
            'groups':group_rows,'contatos':contact_rows,'memberships':sum(row['t'] for row in group_rows),
            'unique_contacts':len(contact_rows),'named_contacts':sum(row['nome']!='Não identificado' for row in contact_rows),
            'unresolved_contacts':sum(row['nome']=='Não identificado' for row in contact_rows)}


def collect() -> dict:
    groups = load_groups()
    generated = now_brt()
    since, until = window(generated)
    since_ts = int(since.timestamp())
    try:
        reader_token = DB_READER_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        raise ValueError("IFC group reader credential is unavailable") from None
    if len(reader_token) < 32:
        raise ValueError("IFC group reader credential is unavailable")
    request = urllib.request.Request(DB_READER_URL,
        data=json.dumps({"window_start_ts": since_ts, "window_end_ts": int(until.timestamp())}, separators=(",", ":")).encode(),
        headers={"Authorization": "Bearer " + reader_token, "Accept": "application/json", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            reader_payload = json.loads(response.read(64 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        raise ValueError(f"IFC group reader returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise ValueError("IFC group reader failed") from None
    if not isinstance(reader_payload, dict) or reader_payload.get("status") != "ok":
        raise ValueError("IFC group reader returned an invalid response")
    allowed = {group["remote_jid"]: group for group in groups}
    reader_records = reader_payload.get("messages")
    if not isinstance(reader_records, list) or len(reader_records) > MAX_MESSAGES_TOTAL:
        raise ValueError("IFC group reader message list is invalid")
    messages = []
    counts = {group["remote_jid"]: 0 for group in groups}
    transcript_cache = audio_tools.load_transcript_cache()
    audio_transcribed = 0
    audio_unavailable = 0
    group_counts = []
    seen_ids = set()
    api_key = None
    for record in reader_records:
        if not isinstance(record, dict) or record.get("instance") != INSTANCE:
            raise ValueError("IFC group reader returned an invalid record")
        key = record.get("key") if isinstance(record.get("key"), dict) else {}
        jid, message_id = key.get("remoteJid"), key.get("id")
        if jid not in allowed or not isinstance(message_id, str) or not message_id:
            raise ValueError("IFC group reader scope validation failed")
        ts = timestamp(record)
        if ts is None or not since_ts <= ts <= int(until.timestamp()):
            raise ValueError("IFC group reader time-window validation failed")
        identity = (jid, message_id)
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        text = scrub_text(message_text(record))
        message_type = str(record.get("messageType") or "unknown")[:60]
        if is_audio(record):
            cache_key = hashlib.sha256((jid + "\0" + message_id).encode()).hexdigest()
            transcript = transcript_cache.get(cache_key)
            if not isinstance(transcript, str) or not transcript.strip():
                if api_key is None:
                    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
                    if not api_key:
                        raise ValueError("Evolution key is unavailable")
                transcript = audio_tools.transcribe_audio(api_key, INSTANCE, record)
                transcript_cache[cache_key] = transcript
                audio_tools.save_transcript_cache(transcript_cache)
            text = transcript if transcript.startswith("[Áudio indisponível") else "[áudio transcrito] " + transcript
            audio_transcribed += int(not transcript.startswith("[Áudio indisponível"))
            audio_unavailable += int(transcript.startswith("[Áudio indisponível"))
        elif audio_tools.document_node(record) is not None or audio_tools.is_image_record(record) or audio_tools.video_node(record) is not None:
            before = len(transcript_cache)
            if api_key is None:
                api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
                if not api_key:
                    raise ValueError("Evolution key is unavailable")
            text = scrub_text(audio_tools.document_message(api_key, INSTANCE, record, text, transcript_cache))
            if audio_tools.video_node(record) is not None and "[áudio transcrito]" in text:
                audio_transcribed += 1
            if len(transcript_cache) != before:
                audio_tools.save_transcript_cache(transcript_cache)
        if not text:
            text = "[Conteúdo removido após sanitização de dados pessoais]"
        sender = scrub_text(record.get("pushName") if isinstance(record.get("pushName"), str) else "")[:80]
        group_meta = allowed[jid]
        messages.append({"group": group_meta["name"], "group_category": group_meta["category"],
                         "group_description": group_meta["description"],
                         "at": datetime.fromtimestamp(ts, ZONE).isoformat(timespec="minutes"),
                         "sender": sender, "type": message_type, "text": text})
        counts[jid] += 1
    group_counts = [{"group": group["name"], "category": group["category"],
                     "description": group["description"], "messages": counts[group["remote_jid"]]}
                    for group in groups]

    participant_directory=None
    try:
        participant_api_key=API_KEY_FILE.read_text(encoding='utf-8').strip()
        if not participant_api_key:
            raise ValueError('Evolution participant credential unavailable')
        participant_directory=fetch_ifc_participants(participant_api_key,groups,reader_token,generated)
    except Exception:
        participant_directory={'generated_at':generated.isoformat(timespec='seconds'),'scope':'IFC','status':'unavailable'}

    messages.sort(key=lambda item: item["at"])
    payload = {
        "version": 1,
        "source": "evolution_grupos",
        "generated_at": generated.isoformat(timespec="seconds"),
        "window_start": since.isoformat(timespec="minutes"),
        "window_end": until.isoformat(timespec="minutes"),
        "groups": group_counts,
        "messages": messages,
        "participantes_ifc": participant_directory,
    }
    atomic_write(OUTPUT, payload)
    audio_tools.save_transcript_cache(transcript_cache)
    return {"status": "ok", "groups": len(groups), "messages": len(messages),
            "audio_transcribed": audio_transcribed, "audio_untranscribed": audio_unavailable,
            "participants": participant_directory.get('unique_contacts',0) if participant_directory.get('status')=='current' else 0,
            "participants_status": participant_directory.get('status','unavailable')}


if __name__ == "__main__":
    try:
        result = collect()
    except urllib.error.HTTPError as error:
        # Report status only; provider response bodies can contain private data.
        print(json.dumps({"status": "failed", "reason": "evolution_http_error",
                          "http_status": int(error.code)}, separators=(",", ":")))
        raise SystemExit(1)
    except Exception as error:
        message = str(error)
        known = {
            "Evolution key is unavailable", "IFC group allowlist is invalid",
            "IFC group allowlist entry is invalid", "IFC group allowlist contains duplicates",
            "IFC group allowlist does not match the reviewed catalog",
            "Evolution returned an unexpected response shape", "Evolution message list is missing or malformed",
            "Evolution search returned a non-success status", "Evolution search failed",
            "Evolution query reached the configured safety cap",
            "Evolution group pagination reached the configured cap",
        }
        if message in known:
            reason = message.lower().replace(" ", "_")[:100]
        elif message.startswith("Evolution search returned HTTP "):
            reason = "evolution_search_http_error"
        else:
            reason = "evolution_collector_error_" + type(error).__name__[:40]
        print(json.dumps({"status": "failed", "reason": reason}, separators=(",", ":")))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
