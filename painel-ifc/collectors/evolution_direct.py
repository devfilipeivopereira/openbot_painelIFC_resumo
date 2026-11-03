#!/usr/bin/env python3
"""Read recent private messages from the reviewed IFC leader/admin allowlist."""
from __future__ import annotations

import concurrent.futures
import base64
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_URL = os.environ.get("EVOLUTION_BASE_URL", "").rstrip("/")
INSTANCES = tuple(item.strip() for item in os.environ.get("IFC_DIRECT_INSTANCES", "").split(",") if item.strip())
if not BASE_URL or not INSTANCES:
    raise SystemExit("evolution_direct_configuration_missing")
KEY_FILE = Path(os.environ.get("EVOLUTION_KEY_FILE", "/run/secrets/evolution-apikey"))
CONTACTS_FILE = Path(os.environ.get("IFC_DIRECT_CONTACTS_FILE", "/run/secrets/ifc-direct-contacts.json"))
ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
OUTPUT = ROOT / "staging" / "mensagens_diretas.json"
ZONE = ZoneInfo("America/Sao_Paulo")
PAGE_SIZE = 50
MAX_IDENTIFIERS = 500
MAX_MESSAGES = 20000
TIMEOUT_SECONDS = 25
DB_READER_TOKEN_FILE = Path(os.environ.get("IFC_DIRECT_READER_TOKEN_FILE", "/run/secrets/ifc-direct-reader.token"))
DB_READER_URL = os.environ.get("IFC_DIRECT_READER_URL", "http://ifc-direct-db-reader:8790/v1/direct-messages")
TRANSCRIPTION_TOKEN_FILE = Path(os.environ.get("IFC_TRANSCRIPTION_TOKEN_FILE", "/run/secrets/litellm-transcription.token"))
TRANSCRIPTION_URL = os.environ.get("IFC_TRANSCRIPTION_URL", "http://ifc-transcription-gateway:8776/v1/audio/transcriptions")
TRANSCRIPT_CACHE = ROOT / "cache" / "evolution_direct_transcripts.json"
MAX_AUDIO_BYTES = 24 * 1024 * 1024

PHONE_RE = re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
JID_RE = re.compile(r"\b[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)\b", re.I)
MENTION_RE = re.compile(r"(?<!\w)@\d{10,}(?!\d)")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
SECRET_RE = re.compile(r"(?i)\b(?:senha|password|token|api[_ -]?key)\s*[:=]\s*\S+")


def now_brt() -> datetime:
    return datetime.now(ZONE).replace(microsecond=0)


def scrub_text(value: str, limit: int = 1000000) -> str:
    for pattern in (SECRET_RE, CPF_RE, PHONE_RE, JID_RE, MENTION_RE, EMAIL_RE):
        value = pattern.sub("[removido]", value)

    def scrub_url(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,);]")
        parsed = urllib.parse.urlsplit(raw)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    value = re.sub(r"https?://[^\s<>]+", scrub_url, value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def message_text(record: dict) -> str:
    """Preserve text and explicitly flag media whose contents were not inspected."""
    message=record.get("message")
    kind=str(record.get("messageType") or "")
    if not isinstance(message,dict):message={}
    conversation=message.get("conversation")
    if isinstance(conversation,str) and conversation.strip():return conversation
    extended=message.get("extendedTextMessage")
    if isinstance(extended,dict) and isinstance(extended.get("text"),str) and extended["text"].strip():return extended["text"]
    nested=message.get("documentWithCaptionMessage")
    if isinstance(nested,dict) and isinstance(nested.get("message"),dict):message={**message,**nested["message"]}
    reaction=message.get("reactionMessage")
    if isinstance(reaction,dict):
        emoji=reaction.get("text")
        if isinstance(emoji,str) and emoji.strip():return "[reação: "+scrub_text(emoji,80)+"]"
        return "[reação removida]"
    for key,label in (("imageMessage","imagem"),("videoMessage","vídeo"),("documentMessage","documento")):
        node=message.get(key)
        if isinstance(node,dict) or kind.casefold()==key.casefold():
            node=node if isinstance(node,dict) else {}
            caption=node.get("caption") if isinstance(node.get("caption"),str) else ""
            filename=node.get("fileName") if isinstance(node.get("fileName"),str) else ""
            details=[]
            if filename:details.append("Arquivo: "+filename)
            if caption:details.append("Legenda: "+caption)
            return "["+label+" não analisado: conteúdo visual ou do arquivo indisponível]"+(" "+" · ".join(details) if details else "")
    labels={"stickerMessage":"figurinha","contactMessage":"contato compartilhado","contactsArrayMessage":"lista de contatos compartilhada","locationMessage":"localização compartilhada","liveLocationMessage":"localização ao vivo compartilhada","reactionMessage":"reação","pollCreationMessage":"enquete","pollUpdateMessage":"resposta de enquete"}
    return "["+labels.get(kind,"mensagem sem conteúdo textual; tipo "+(kind or "desconhecido"))+"]"


def document_node(record):
    message=record.get("message")
    if not isinstance(message,dict):return None
    wrapper=message.get("documentWithCaptionMessage")
    if isinstance(wrapper,dict) and isinstance(wrapper.get("message"),dict):message=wrapper["message"]
    node=message.get("documentMessage")
    return node if isinstance(node,dict) else None

def image_node(record):
    message=record.get("message")
    if not isinstance(message,dict):return None
    node=message.get("imageMessage")
    return node if isinstance(node,dict) else None

def is_image_record(record):
    return image_node(record) is not None or str(record.get("messageType") or "").casefold()=="imagemessage"

def video_node(record):
    message=record.get("message")
    if not isinstance(message,dict):return None
    node=message.get("videoMessage")
    return node if isinstance(node,dict) else None

def read_document(api_key, instance, record):
    from attachment_text import extract,extract_video,MAX_BYTES,MAX_VIDEO_BYTES
    node=document_node(record) or image_node(record) or video_node(record)
    image_only=node is None and is_image_record(record)
    if node is None and not image_only:return None
    if image_only:node={}
    is_video=video_node(record) is not None
    mime=str(node.get("mimetype") or "").lower();name=str(node.get("fileName") or "")
    video_type=mime.startswith("video/") or name.lower().endswith((".mp4",".mov",".m4v",".webm",".3gp",".mkv"))
    if is_video and not video_type:
        return "formato de vídeo não suportado",None
    is_image=image_only or image_node(record) is not None or mime.startswith('image/') or name.lower().endswith((".jpg",".jpeg",".png",".webp",".tif",".tiff"))
    if not (is_video or is_image or mime in {"application/pdf","application/vnd.openxmlformats-officedocument.wordprocessingml.document","text/plain","text/csv","text/html"} or name.lower().endswith((".pdf",".docx",".txt",".csv",".html"))):
        return "formato de documento não suportado", None
    try:
        message={"key":media_key(record)}
        for field in ("message","messageType","messageTimestamp","pushName","participant","status","messageSecret","source","instanceId"):
            if field in record:message[field]=record[field]
    except ValueError:return "identificação do documento indisponível", None
    payload=None
    for _ in range(3):
        try:
            req=urllib.request.Request(f"{BASE_URL}/chat/getBase64FromMediaMessage/{urllib.parse.quote(instance,safe='')}",data=json.dumps({"message":message,"convertToMp4":False},separators=(",", ":")).encode(),headers={"apikey":api_key,"Accept":"application/json","Content-Type":"application/json"},method="POST")
            with urllib.request.urlopen(req,timeout=TIMEOUT_SECONDS) as response:
                media_limit=MAX_VIDEO_BYTES if is_video else MAX_BYTES
                raw=response.read(((media_limit+2)//3)*4+200000)
                payload=json.loads(raw)
                raw=None
            break
        except (urllib.error.URLError,TimeoutError,json.JSONDecodeError,ValueError):continue
    if not isinstance(payload,dict):return "arquivo indisponível após três tentativas", None
    data=payload.get("base64") or payload.get("data")
    if isinstance(data,dict):data=data.get("base64") or data.get("data")
    if isinstance(data,str) and data.startswith("data:") and "," in data:data=data.split(",",1)[1]
    if is_image and not mime.startswith("image/"):mime="image/jpeg"
    if is_video:
        if not isinstance(data,str):return "arquivo de vídeo indisponível",None
        try:video_bytes=base64.b64decode(data,validate=True)
        except (ValueError,base64.binascii.Error):return "arquivo de vídeo codificado inválido",None
        payload=None;data=None
        if not video_bytes or len(video_bytes)>MAX_VIDEO_BYTES:return "vídeo vazio ou acima do limite",None
        visual,audio,audit=extract_video(video_bytes)
        pieces=[]
        if visual:pieces.append(scrub_text(visual,1000000))
        if audio:
            transcript=_transcribe_audio_bytes(audio,"audio/mpeg")
            pieces.insert(0,"[áudio transcrito] (áudio extraído do vídeo) "+transcript)
        status="vídeo: "+str(audit)+"; conteúdo visual analisado por amostragem, não integralmente"
        if not pieces:return status,None
        return status," ".join(pieces)
    text,status=extract({"name":name,"contentType":mime,"contentBytes":data})
    return status,scrub_text(text,1000000) if text else None

def document_message(api_key,instance,record,description,cache):
    node=document_node(record) or image_node(record) or video_node(record)
    if node is None and not is_image_record(record):return description
    key=record.get("key") or {}; identity=str(key.get("remoteJid") or "")+"\0"+str(key.get("id") or "")
    if not key.get("id") or not key.get("remoteJid"):return description+" Motivo: identificação do documento indisponível"
    is_video=video_node(record) is not None
    media_digest=hashlib.sha256(identity.encode()).hexdigest()
    # Reprocess videos once under the corrected 40 MB analysis limit and the
    # full six-frame sample. Keep document and image cache entries stable, and
    # remove prior video entries so the private cache does not retain copies.
    legacy_video_key="doc:v2:"+media_digest
    previous_video_key="video:v3:"+media_digest
    cache_key=("video:v4:" if is_video else "doc:v2:")+media_digest
    if is_video:
        cache.pop(legacy_video_key,None)
        cache.pop(previous_video_key,None)
    cached=cache.get(cache_key)
    cache_valid=isinstance(cached,dict) and isinstance(cached.get("status"),str)
    cached_text=cached.get("text") if cache_valid else None
    # Keep successful extraction cached. A failed download/analysis is only
    # cached for five hours so the next midday scheduled collection can retry
    # media first encountered in the morning instead of waiting until tomorrow.
    retry_later=False
    attempted_at=cached.get("attempted_at") if cache_valid else None
    if cache_valid and not isinstance(cached_text,str) and isinstance(attempted_at,str):
        try:
            previous=datetime.fromisoformat(attempted_at.replace("Z","+00:00"))
            if previous.tzinfo is not None:
                retry_later=(datetime.now(timezone.utc)-previous.astimezone(timezone.utc)).total_seconds()<5*60*60
        except ValueError:
            retry_later=False
    if cache_valid and isinstance(cached_text,str) and cached_text:
        status,text=cached.get("status"),cached_text
    elif retry_later:
        status,text=cached.get("status"),None
    else:
        status,text=read_document(api_key,instance,record)
        entry={"status":status,"text":text}
        if not isinstance(text,str) or not text:
            entry["attempted_at"]=datetime.now(timezone.utc).isoformat()
        cache[cache_key]=entry
    if text:
        desc=description
        for label in ("documento","imagem","vídeo"):
            desc=desc.replace("["+label+" não analisado: conteúdo visual ou do arquivo indisponível]","["+label+": "+str(status)+"]")
        if "parcial" in status and not is_video:desc+=" [documento não analisado: algumas páginas não foram processadas]"
        if is_image_record(record) and "analisada visualmente por IA" not in str(status):desc+=" [imagem não analisada: somente o texto OCR foi lido; OCR sujeito a erros]"
        if document_node(record) is not None and "ocr" in str(status).casefold():desc+=" [documento não analisado: OCR não interpreta todo o conteúdo visual]"
        if is_video:desc+=" [vídeo analisado parcialmente: visual por amostragem e áudio transcrito somente quando recuperável]"
        return desc+" ["+str(status)+"] Conteúdo textual extraído, dado não confiável: "+text
    return description+" Motivo: "+str(status)

def is_audio_record(record: dict) -> bool:
    message = record.get("message")
    if not isinstance(message, dict):
        message = {}
    kind = str(record.get("messageType") or "").lower()
    return kind in {"audio", "audiomessage", "ptt", "pttmessage"} or any(
        key in message for key in ("audioMessage", "pttMessage"))


def media_key(record: dict) -> dict:
    key = record.get("key") if isinstance(record.get("key"), dict) else {}
    if not key.get("id") or not key.get("remoteJid"):
        raise ValueError("Evolution audio message key is incomplete")
    # Preserve Baileys' full message key. Group/LID messages can need
    # addressingMode, participant and participantAlt to resolve media.
    output = dict(key)
    output["fromMe"] = bool(key.get("fromMe"))
    return output


AUDIO_UNAVAILABLE = "[Áudio indisponível após 3 tentativas; conteúdo não analisado]"
def transcribe_audio(api_key: str, instance: str, record: dict) -> str:
    for attempt in range(3):
        try:return _transcribe_audio_once(api_key, instance, record)
        except ValueError as error:
            if not str(error).startswith(("Evolution audio download", "Evolution audio payload", "Evolution audio response", "Evolution audio message key")):raise
            if attempt == 2:return AUDIO_UNAVAILABLE
    raise RuntimeError("audio_retry_unreachable")

def _transcribe_audio_once(api_key: str, instance: str, record: dict) -> str:
    message = {"key": media_key(record)}
    for field in ("message", "messageType", "messageTimestamp", "pushName", "participant",
                  "status", "messageSecret", "source", "instanceId"):
        if field in record:
            message[field] = record[field]
    request = urllib.request.Request(
        f"{BASE_URL}/chat/getBase64FromMediaMessage/{urllib.parse.quote(instance, safe='')}",
        data=json.dumps({"message": message, "convertToMp4": False}, separators=(",", ":")).encode(),
        headers={"apikey": api_key, "Accept": "application/json", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(MAX_AUDIO_BYTES * 2))
    except urllib.error.HTTPError as error:
        body = error.read(65536).decode("utf-8", "replace").lower()
        category = "request"
        for needles, label in (
            (("message not found", "message_not_found"), "message_not_found"),
            (("media not found", "no media", "media unavailable"), "media_unavailable"),
            (("expired", "not available"), "media_unavailable"),
            (("invalid key", "invalid message key", "key is invalid"), "invalid_message_key"),
            (("validation", "invalid payload", "missing field", "required field"), "invalid_request"),
        ):
            if any(needle in body for needle in needles):
                category = label
                break
        raise ValueError(f"Evolution audio download returned HTTP {error.code}:{category}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise ValueError("Evolution audio download failed") from None
    if not isinstance(payload, dict):
        raise ValueError("Evolution audio response is malformed")
    raw = payload.get("base64") or payload.get("data")
    mimetype = payload.get("mimetype") or payload.get("mimeType") or "audio/ogg"
    if isinstance(raw, dict):
        mimetype = raw.get("mimetype") or raw.get("mimeType") or mimetype
        raw = raw.get("base64") or raw.get("data")
    if not isinstance(raw, str):
        raise ValueError("Evolution audio payload is unavailable")
    if raw.startswith("data:") and "," in raw:
        header, raw = raw.split(",", 1)
        if ";base64" in header:
            mimetype = header[5:].split(";", 1)[0]
    try:
        audio = base64.b64decode(raw, validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError("Evolution audio payload is invalid") from None
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("Evolution audio exceeds transcription size limit")
    if not isinstance(mimetype, str) or not mimetype.startswith("audio/"):
        mimetype = "audio/ogg"
    return _transcribe_audio_bytes(audio,mimetype)


def _transcribe_audio_bytes(audio:bytes,mimetype:str)->str:
    if not audio or len(audio)>MAX_AUDIO_BYTES:raise ValueError("video audio exceeds transcription size limit")
    token = TRANSCRIPTION_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("LiteLLM transcription credential is unavailable")
    boundary = "----ifc" + hashlib.sha256(os.urandom(32)).hexdigest()
    fields = [("model", b"groq/whisper-large-v3"), ("language", b"pt"), ("response_format", b"json")]
    chunks = []
    for name, value in fields:
        chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(), value, b"\r\n"))
    ext = "ogg" if "ogg" in mimetype else "mp3" if "mpeg" in mimetype else "wav" if "wav" in mimetype else "webm" if "webm" in mimetype else "bin"
    chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.{ext}\"\r\nContent-Type: {mimetype}\r\n\r\n".encode(), audio, f"\r\n--{boundary}--\r\n".encode()))
    body = b"".join(chunks)
    req = urllib.request.Request(TRANSCRIPTION_URL, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as error:
        raw_error = error.read(65536).decode("utf-8", "replace")
        body_text = raw_error.lower()
        if "format" in body_text or "unsupported file" in body_text:
            category = "audio_format"
        elif "size" in body_text or "too large" in body_text or "file too big" in body_text:
            category = "audio_size"
        elif "language" in body_text:
            category = "language"
        elif "model" in body_text:
            category = "model"
            try:
                detail = json.loads(raw_error).get("error", {})
                if isinstance(detail, dict):
                    model_message = detail.get("message", "")
                    if isinstance(model_message, str):
                        lower_message = model_message.lower()
                        if "unknown param" in lower_message or "unsupported parameter" in lower_message:
                            category = "provider_unknown_parameter"
                        elif "no model found" in lower_message or "model not found" in lower_message:
                            category = "model_not_found"
                        elif "not found" in lower_message or "does not exist" in lower_message:
                            category = "model_not_found"
                        elif "no deployment" in lower_message or "no healthy" in lower_message:
                            category = "model_no_deployment"
                        elif "not supported" in lower_message or "unsupported" in lower_message:
                            category = "model_unsupported"
                        elif "audio_transcription" in lower_message or "mode" in lower_message:
                            category = "model_mode"
                    code = detail.get("code") or detail.get("type") or detail.get("param")
                    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", code):
                        category += ":" + code.lower()
            except (json.JSONDecodeError, AttributeError):
                pass
        else:
            category = "request"
        raise ValueError(f"LiteLLM audio transcription returned HTTP {error.code}:{category}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise ValueError("LiteLLM audio transcription failed") from None
    text = result.get("text") if isinstance(result, dict) else None
    text = scrub_text(text, 1000000) if isinstance(text, str) else ""
    if not text:
        raise ValueError("LiteLLM returned an empty audio transcription")
    return text


def load_transcript_cache() -> dict:
    try:
        value = json.loads(TRANSCRIPT_CACHE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        raise ValueError("IFC audio transcript cache is unreadable") from None


def save_transcript_cache(cache: dict) -> None:
    TRANSCRIPT_CACHE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=".audio-cache-", dir=TRANSCRIPT_CACHE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, TRANSCRIPT_CACHE)
        os.chmod(TRANSCRIPT_CACHE, 0o600)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass


def timestamp(record: dict) -> int | None:
    value = record.get("messageTimestamp")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    value = int(value)
    return value // 1000 if value > 10_000_000_000 else value


def load_allowlist() -> list[dict]:
    value = json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
    people = value.get("pessoas") if isinstance(value, dict) else None
    if not isinstance(people, list) or not people:
        raise ValueError("IFC direct-message allowlist is invalid")
    identifiers = set()
    contacts = []
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("IFC direct-message allowlist entry is invalid")
        name = person.get("pessoa") or person.get("nome")
        role = person.get("papel", "")
        if not isinstance(name, str) or not name.strip():
            continue
        values = []
        for field in ("jids", "lids"):
            items = person.get(field, [])
            if not isinstance(items, list):
                raise ValueError("IFC direct-message allowlist entry is invalid")
            for jid in items:
                if not isinstance(jid, str) or not (jid.endswith("@s.whatsapp.net") or jid.endswith("@lid")):
                    raise ValueError("IFC direct-message identifier is invalid")
                values.append(jid)
        values = list(dict.fromkeys(values))
        for jid in values:
            identifiers.add(jid)
        if values:
            contacts.append({"person": scrub_text(name, 100), "role": scrub_text(role, 120), "jids": values})
    if not identifiers or len(identifiers) > MAX_IDENTIFIERS:
        raise ValueError("IFC direct-message allowlist size is invalid")
    return contacts


def atomic_write(data: dict) -> None:
    OUTPUT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".evolution-diretas-", suffix=".tmp", dir=OUTPUT.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
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
    api_key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("Evolution key is unavailable")
    contacts = load_allowlist()
    now = now_brt()
    since = now - timedelta(hours=24)
    since_ts, until_ts = int(since.timestamp()), int(now.timestamp())
    lookup: dict[str, list[dict]] = {}
    for contact in contacts:
        for jid in contact["jids"]:
            lookup.setdefault(jid, []).append(contact)
    if not lookup or len(lookup) > MAX_IDENTIFIERS:
        raise ValueError("IFC direct-message query size is invalid")
    reader_token = DB_READER_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not reader_token:
        raise ValueError("IFC direct-message reader credential is unavailable")
    reader_body = json.dumps({"window_start_ts": since_ts, "window_end_ts": until_ts}, separators=(",", ":")).encode()
    reader_request = urllib.request.Request(DB_READER_URL, data=reader_body,
        headers={"Authorization": f"Bearer {reader_token}", "Accept": "application/json",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(reader_request, timeout=180) as response:
            reader_payload = json.loads(response.read(64 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        raise ValueError(f"Evolution direct reader returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise ValueError("Evolution direct reader failed") from None
    if not isinstance(reader_payload, dict) or reader_payload.get("status") != "ok":
        raise ValueError("Evolution direct reader returned an invalid response")
    reader_records = reader_payload.get("messages")
    if not isinstance(reader_records, list) or len(reader_records) > MAX_MESSAGES:
        raise ValueError("Evolution direct reader message list is invalid")
    message_map: dict[tuple[str, str], dict] = {}
    for item in reader_records:
        if not isinstance(item, dict) or item.get("instance") not in INSTANCES:
            raise ValueError("Evolution direct reader returned an invalid record")
        key = item.get("key") if isinstance(item.get("key"), dict) else {}
        jid = key.get("remoteJid")
        if not isinstance(jid, str) or jid not in lookup or jid.endswith(("@g.us", "@broadcast")):
            raise ValueError("Evolution direct reader scope validation failed")
        ts = timestamp(item)
        if ts is None or not (since_ts <= ts <= until_ts):
            raise ValueError("Evolution direct reader time-window validation failed")
        message_id = key.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        unique = (jid, message_id)
        instance = item["instance"]
        entry = message_map.setdefault(unique, {"id": message_id, "jid": jid, "instances": [], "record": item,
                                                "contacts": lookup[jid], "instance": instance})
        if instance not in entry["instances"]:
            entry["instances"].append(instance)
    if len(message_map) > MAX_MESSAGES:
        raise ValueError("Evolution direct-message collection reached the configured safety cap")

    messages = []
    transcript_cache = load_transcript_cache()
    transcribed_count = 0
    audio_unavailable_count = 0
    for entry in message_map.values():
        record = entry["record"]
        key = record.get("key") if isinstance(record.get("key"), dict) else {}
        ts = timestamp(record)
        if ts is None:
            continue
        labels = entry["contacts"]
        contact = labels[0] if labels else {"person": "Contato autorizado", "role": ""}
        sender = "Filipe" if key.get("fromMe") is True else contact["person"]
        text = message_text(record)
        message_type = str(record.get("messageType", "desconhecido"))[:60]
        if is_audio_record(record):
            cache_key = hashlib.sha256((entry["jid"] + "\0" + entry["id"]).encode()).hexdigest()
            transcript = transcript_cache.get(cache_key)
            if not isinstance(transcript, str) or not transcript.strip():
                instance = entry.get("instance")
                if instance is None:
                    raise ValueError("Evolution audio source instance is unavailable")
                transcript = transcribe_audio(api_key, instance, record)
                transcript_cache[cache_key] = transcript
                save_transcript_cache(transcript_cache)
            text = transcript if transcript.startswith("[Áudio indisponível") else "[áudio transcrito] " + transcript
            transcribed_count += int(not transcript.startswith("[Áudio indisponível"))
            audio_unavailable_count += int(transcript.startswith("[Áudio indisponível"))
        elif document_node(record) is not None or is_image_record(record) or video_node(record) is not None:
            instance=entry.get("instance")
            before=len(transcript_cache)
            if instance:text=document_message(api_key,instance,record,text,transcript_cache)
            if len(transcript_cache)!=before:save_transcript_cache(transcript_cache)
            if video_node(record) is not None and "[áudio transcrito]" in text:transcribed_count+=1
        messages.append({
            "at": datetime.fromtimestamp(ts, ZONE).isoformat(timespec="minutes"),
            "person": contact["person"], "role": contact["role"], "sender": sender,
            "from_me": bool(key.get("fromMe")), "instances": ", ".join(sorted(entry["instances"])),
            "type": message_type,
            "text": scrub_text(text),
        })
    messages.sort(key=lambda item: (item["at"], item["person"], item["sender"]))
    payload = {"version": 1, "source": "mensagens_diretas", "generated_at": now.isoformat(timespec="seconds"),
               "window_start": since.isoformat(timespec="seconds"), "window_end": now.isoformat(timespec="seconds"),
               "contacts_monitored": len(contacts), "identifiers_checked": len(lookup), "instances_checked": list(INSTANCES),
               "messages": messages}
    atomic_write(payload)
    save_transcript_cache(transcript_cache)
    return {"status": "ok", "contacts": len(contacts), "identifiers": len(lookup), "instances": len(INSTANCES),
            "messages": len(messages), "audio_transcribed": transcribed_count,
            "audio_untranscribed": audio_unavailable_count}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), ensure_ascii=False, separators=(",", ":")))
    except Exception as error:
        message = str(error)
        allowed = {"Evolution key is unavailable", "IFC direct-message allowlist is invalid",
                   "IFC direct-message allowlist entry is invalid", "IFC direct-message identifier is invalid",
                   "IFC direct-message allowlist size is invalid", "IFC direct-message query size is invalid",
                   "Evolution direct-message collection reached the configured safety cap",
                   "IFC direct-message reader credential is unavailable", "Evolution direct reader failed",
                   "Evolution direct reader returned an invalid response", "Evolution direct reader message list is invalid",
                   "Evolution direct reader scope validation failed", "Evolution direct reader time-window validation failed"}
        if message in {"Evolution audio message key is incomplete", "Evolution audio download failed",
                       "LiteLLM audio transcription failed",
                       "Evolution audio response is malformed", "Evolution audio payload is unavailable",
                       "Evolution audio payload is invalid", "Evolution audio exceeds transcription size limit",
                       "LiteLLM transcription credential is unavailable", "LiteLLM audio transcription failed",
                       "LiteLLM returned an empty audio transcription", "IFC audio transcript cache is unreadable",
                       "Evolution audio source instance is unavailable"}:
            allowed.add(message)
        if message.startswith("LiteLLM audio transcription returned HTTP ") or message.startswith("Evolution audio download returned HTTP "):
            allowed.add(message)
        if message not in allowed and not message.startswith("Evolution direct reader returned HTTP "):
            message = "Evolution direct-message collector failed"
        print(json.dumps({"status": "failed", "reason": message}, ensure_ascii=False))
        raise SystemExit(1)
