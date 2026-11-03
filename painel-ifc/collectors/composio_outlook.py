#!/usr/bin/env python3
"""Read all IFC secretary mailbox messages in the collection window via Composio, without marking read."""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone
from pathlib import Path

MCP_URL = "https://connect.composio.dev/mcp"
KEY_FILE = Path(os.environ.get("COMPOSIO_KEY_FILE", "/run/secrets/composio.key"))
ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
OUTPUT = ROOT / "staging" / "email_secretaria.json"
TOOL = "OUTLOOK_QUERY_EMAILS"
MAILBOXES = (
    (os.environ.get("IFC_OUTLOOK_MAILBOX_1_LABEL", "secretaria_primary"),
     os.environ.get("IFC_OUTLOOK_MAILBOX_1_SELECTOR", "me"),
     os.environ.get("IFC_OUTLOOK_MAILBOX_1_USER_ID", "me")),
    (os.environ.get("IFC_OUTLOOK_MAILBOX_2_LABEL", "secretaria_secondary"),
     os.environ.get("IFC_OUTLOOK_MAILBOX_2_SELECTOR", ""),
     os.environ.get("IFC_OUTLOOK_MAILBOX_2_USER_ID", "")),
)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
JID_RE = re.compile(r"\b[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)\b", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
MAX_ATTACHMENT_BYTES = 5_000_000


def rpc(payload: dict, key: str, transport_session: str | None = None) -> tuple[dict, str | None]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "x-consumer-api-key": key}
    if transport_session:
        headers["Mcp-Session-Id"] = transport_session
    request = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(8_000_000)
            content_type = response.headers.get("Content-Type", "")
            session = response.headers.get("Mcp-Session-Id")
    except Exception:
        raise ValueError("composio_request_failed") from None
    text = raw.decode("utf-8", "replace")
    try:
        if "text/event-stream" in content_type:
            lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            if not lines:
                raise ValueError
            return json.loads(lines[-1]), session
        return json.loads(text), session
    except Exception:
        raise ValueError("composio_response_invalid") from None


def text_items(response: dict) -> list[str]:
    content = response.get("result", {}).get("content", [])
    return [x["text"] for x in content if isinstance(x, dict) and isinstance(x.get("text"), str)] if isinstance(content, list) else []


def walk(value):
    if isinstance(value, str):
        try:
            yield from walk(json.loads(value))
        except Exception:
            return
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def objects(response: dict) -> list[dict]:
    found = []
    for text in text_items(response):
        for candidate in (text, text[text.find("{"):] if "{" in text else ""):
            try:
                found.extend(x for x in walk(json.loads(candidate)) if isinstance(x, dict))
                break
            except Exception:
                pass
    structured = response.get("result", {}).get("structuredContent")
    if isinstance(structured, (dict, list)):
        found.extend(x for x in walk(structured) if isinstance(x, dict))
    return found


def workflow_session(response: dict) -> str | None:
    for item in objects(response):
        for key in ("session_id", "sessionId"):
            if isinstance(item.get(key), str):
                return item[key]
        if isinstance(item.get("session"), dict) and isinstance(item["session"].get("id"), str):
            return item["session"]["id"]
    for text in text_items(response):
        match = re.search(r"(?i)session[_ -]?id\s*[\"'=: ]+([A-Za-z0-9_-]{8,})", text)
        if match:
            return match.group(1)
    return None


def scrub(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = CPF_RE.sub("[removido]", value)
    value = PHONE_RE.sub("[removido]", value)
    value = JID_RE.sub("[removido]", value)
    value = EMAIL_RE.sub("[removido]", value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def email_body(item):
    from html.parser import HTMLParser
    body=item.get("body")
    if not isinstance(body,dict) or not isinstance(body.get("content"),str):
        return "", "corpo indisponível; somente prévia"
    raw=body["content"]
    if len(raw)>1000000:raise ValueError("outlook_body_too_large")
    if str(body.get("contentType", "")).lower()=="html":
        class TextParser(HTMLParser):
            def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
            def handle_starttag(self,tag,attrs):
                if tag.lower() in ("script","style","head"):self.hidden+=1
                if tag.lower() in ("p","div","br","li","tr"):self.parts.append(" ")
            def handle_endtag(self,tag):
                if tag.lower() in ("script","style","head") and self.hidden:self.hidden-=1
                if tag.lower() in ("p","div","li","tr"):self.parts.append(" ")
            def handle_data(self,data):
                if not self.hidden:self.parts.append(data)
        parser=TextParser();parser.feed(raw);raw="".join(parser.parts)
    return scrub(raw,1000000), "corpo em texto"


def attachment_reason_code(reason: str) -> str:
    value = str(reason).casefold()
    rules = (
        ("attachment_list_empty", ("anexos informados mas lista vazia",)),
        ("attachment_list_unavailable", ("lista de anexos indisponível", "consulta de anexos indisponível")),
        ("attachment_list_incomplete", ("lista de anexos possivelmente incompleta",)),
        ("message_id_unavailable", ("identificação do e-mail indisponível",)),
        ("inline_content_unanalyzed", ("imagem ou conteúdo incorporado não analisado",)),
        ("provider_missing_file", ("arquivo não retornado pelo provedor",)),
        ("encoded_file_invalid", ("arquivo codificado inválido",)),
        ("unsupported_format", ("formato de anexo não suportado",)),
        ("extract_failed", ("não foi possível extrair",)),
        ("empty_or_textless", ("anexo sem texto", "sem texto recuperável")),
        ("size_or_page_limit", ("acima do limite",)),
        ("password_protected", ("protegido por senha",)),
        ("partial_extraction", ("parcialmente",)),
        ("visual_only", ("conteúdo visual não analisado", "ocr da imagem")),
        ("invalid_attachment", ("anexo em formato inválido",)),
        ("office_package_above_limit", ("pacote office acima do limite",)),
        ("office_xml_rejected", ("xml do documento não aceito", "documento xml não aceito")),
    )
    for code, markers in rules:
        if any(marker in value for marker in markers):
            return code
    return "unclassified_attachment_issue"


def attachment_transport_metadata(attachment: dict, index: int) -> dict:
    value = attachment.get("contentBytes")
    result = {"attachment_index": index, "field_names": sorted(str(key) for key in attachment.keys()),
              "content_bytes_type": type(value).__name__,
              "is_inline": attachment.get("isInline") is True}
    if not isinstance(value, str):
        result["transport_class"] = "missing_or_non_string"
        return result
    compact = "".join(value.split())
    result.update(length=len(value), whitespace_chars=len(value) - len(compact),
                  data_uri=compact[:5].casefold() == "data:")
    payload = compact.partition(",")[2] if result["data_uri"] else compact
    if result["data_uri"] and ";base64" not in compact.partition(",")[0].casefold():
        result["transport_class"] = "data_uri_without_base64_marker"
        return result
    invalid = sum(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-" for ch in payload)
    result["invalid_character_count"] = invalid
    invalid_chars = [ch for ch in payload if ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"]
    result["invalid_ascii_punctuation_count"] = sum(33 <= ord(ch) <= 126 for ch in invalid_chars)
    result["invalid_non_ascii_count"] = sum(ord(ch) > 126 for ch in invalid_chars)
    result["invalid_control_count"] = sum(ord(ch) < 33 for ch in invalid_chars)
    result["padding_count"] = len(payload) - len(payload.rstrip("="))
    result["length_mod4"] = len(payload) % 4
    result["bytes_repr_wrapper"] = len(payload) > 3 and payload[:2].casefold() in {"b'", 'b"'} and payload[-1:] == payload[1:2]
    result["looks_like_url"] = "://" in payload
    result["looks_like_json"] = payload[:1] in "[{"
    mime = str(attachment.get("contentType", "")).casefold()
    result["mime_family"] = ("pdf" if "pdf" in mime else "spreadsheet" if "spreadsheet" in mime or "excel" in mime else "presentation" if "presentation" in mime or "powerpoint" in mime else "document" if "word" in mime else "image" if mime.startswith("image/") else "text" if mime.startswith("text/") else "other")
    result["declared_size"] = attachment.get("size") if type(attachment.get("size")) is int else None
    if invalid:
        result["transport_class"] = "invalid_characters"
    elif "-" in payload or "_" in payload:
        result["transport_class"] = "urlsafe_base64"
    elif result["length_mod4"] in (2, 3):
        result["transport_class"] = "base64_missing_padding_possible"
    elif result["length_mod4"] == 0:
        result["transport_class"] = "base64_standard_shape"
    else:
        result["transport_class"] = "invalid_base64_length"
    return result


def download_attachment_bytes(url: str) -> bytes | None:
    try:parsed = urlsplit(url)
    except ValueError:return None
    allowed = parsed.hostname and (parsed.hostname == "amazonaws.com" or parsed.hostname.endswith(".amazonaws.com")
                                   or parsed.hostname.endswith(".r2.cloudflarestorage.com"))
    try:port=parsed.port
    except ValueError:return None
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or port not in (None, 443):
        return None
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError("outlook_attachment_redirect_refused")
    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
            data = response.read(MAX_ATTACHMENT_BYTES + 1)
    except Exception:
        return None
    return data if 0 < len(data) <= MAX_ATTACHMENT_BYTES else None

def atomic_write(payload: dict) -> None:
    OUTPUT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".email-", suffix=".tmp", dir=OUTPUT.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, OUTPUT)
        os.chmod(OUTPUT, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def collect() -> dict:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("composio_key_missing")
    init, transport = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ifc-email-collector", "version": "1"}}}, key)
    if "error" in init:
        raise ValueError("composio_initialize_failed")
    mailbox_selectors = [selector for _label, selector, _user_id in MAILBOXES if selector]
    if not mailbox_selectors:
        raise ValueError("secretary_mailbox_not_configured")
    args = {"queries": [{"use_case": "read all configured secretary mailbox emails received in the last 24 hours, regardless of subject or topic; read-only", "known_fields": "mailbox_aliases:" + ",".join(mailbox_selectors) + ", date_range:last_24_hours"}], "session": {"generate_id": True}, "search_strategy": "auto"}
    search, transport = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "COMPOSIO_SEARCH_TOOLS", "arguments": args}}, key, transport)
    if search.get("result", {}).get("isError") or "error" in search:
        raise ValueError("composio_search_failed")
    session = workflow_session(search)
    if not session:
        raise ValueError("composio_session_missing")
    schema, transport = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "COMPOSIO_GET_TOOL_SCHEMAS", "arguments": {"tool_slugs": [TOOL], "include": ["input_schema"], "session_id": session}}}, key, transport)
    if schema.get("result", {}).get("isError") or TOOL not in " ".join(text_items(schema)):
        raise ValueError("composio_schema_missing")
    connections, transport = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "COMPOSIO_MANAGE_CONNECTIONS", "arguments": {"toolkits": [{"name": "outlook", "action": "list"}], "session_id": session}}}, key, transport)
    if connections.get("result", {}).get("isError") or "error" in connections:
        raise ValueError("composio_connections_failed")
    account_ids: dict[str, str] = {}
    connection_records = [
        item for item in objects(connections)
        if isinstance(item.get("id"), str)
        and isinstance(item.get("account_type"), str)
        and isinstance(item.get("user_info"), dict)
    ]
    for label, selector, _user_id in MAILBOXES:
        if not selector:
            continue
        matches = []
        for item in connection_records:
            status_value = str(item.get("status", "active")).casefold()
            user_info = item.get("user_info", {})
            if "@" in selector:
                match = any(
                    isinstance(user_info.get(field), str)
                    and user_info[field].casefold() == selector.casefold()
                    for field in ("mail", "user_principal_name")
                )
            else:
                match = selector.casefold() in str(item.get("id", "")).casefold()
            if match and status_value in {"active", "connected", "confirmed"}:
                matches.append(item["id"])
        matches = list(dict.fromkeys(matches))
        if len(matches) != 1:
            raise ValueError("composio_outlook_account_ambiguous")
        account_ids[label] = matches[0]

    now = datetime.now(timezone.utc).replace(microsecond=0)
    since = now - timedelta(hours=24)
    filter_value = "receivedDateTime ge " + since.isoformat().replace("+00:00", "Z")
    messages = []
    mailbox_counts: dict[str, int] = {}
    def find_lists(value, lists):
        if isinstance(value, dict):
            for name, child in value.items():
                if name in {"value", "messages", "items"} and isinstance(child, list):
                    lists.append(child)
                find_lists(child, lists)
        elif isinstance(value, list):
            for child in value:
                find_lists(child, lists)
    from attachment_text import extract as extract_attachment, _decode_content_bytes as decode_attachment
    seen_message_ids: set[str] = set()
    attachment_rpc_id = 80
    def read_attachments(account,user_id,message_id):
        nonlocal attachment_rpc_id
        args={"tools":[{"tool_slug":"OUTLOOK_LIST_OUTLOOK_ATTACHMENTS","account":account,"arguments":{"user_id":user_id,"message_id":message_id,"top":100,"response_detail":"full"}}],"sync_response_to_workbench":False,"current_step":"READING_EMAIL_ATTACHMENTS","current_step_metric":"read-only","thought":"Read attachment text without modifying mailbox.","session_id":session}
        try:
            response,_=rpc({"jsonrpc":"2.0","id":80,"method":"tools/call","params":{"name":"COMPOSIO_MULTI_EXECUTE_TOOL","arguments":args}},key,transport)
            obs=objects(response)
            if response.get("result",{}).get("isError") or any(o.get("successful") is False for o in obs):return [],["consulta de anexos indisponível"],["attachment_list_unavailable"],[]
            lists=[]
            for o in obs:
                for field in ("value","attachments","items"):
                    if isinstance(o.get(field),list):lists.append(o[field])
            if not lists:return [],["lista de anexos indisponível"],["attachment_list_unavailable"],[]
            rows=max(lists,key=len);texts=[];pending=[];codes=[];diagnostics=[]
            if not rows:return [],["anexos informados mas lista vazia"],["attachment_list_empty"],[]
            if len(rows)>=100 or any(o.get("@odata.nextLink") or o.get("nextLink") for o in obs):pending.append("lista de anexos possivelmente incompleta");codes.append("attachment_list_incomplete")
            for attachment_index,attachment in enumerate(rows):
                if not isinstance(attachment,dict):pending.append("anexo em formato inválido");codes.append("invalid_attachment");continue
                name=scrub(attachment.get("name"),180) or "Anexo sem nome"
                text,reason=extract_attachment(attachment)
                declared_size=attachment.get("size")
                if text and type(declared_size) is int and isinstance(attachment.get("contentBytes"),str):
                    decoded=decode_attachment(attachment.get("contentBytes"))
                    if decoded is None or len(decoded)!=declared_size:text,reason=None,"arquivo codificado inválido"
                transport_note={"source":"attachment_list","metadata":attachment_transport_metadata(attachment,attachment_index)}
                if (not text or not text.strip()) and reason in {"arquivo codificado inválido","arquivo não retornado pelo provedor"} and attachment.get("isInline") is not True:
                    attachment_rpc_id+=1
                    download_args={"tools":[{"tool_slug":"OUTLOOK_DOWNLOAD_OUTLOOK_ATTACHMENT","account":account,"arguments":{"user_id":user_id,"message_id":message_id,"attachment_id":attachment.get("id"),"file_name":str(attachment.get("name") or "attachment")}}],"sync_response_to_workbench":False,"current_step":"DOWNLOADING_EMAIL_ATTACHMENT_READ_ONLY","current_step_metric":"read-only","thought":"Download this listed Outlook attachment for local text extraction; do not modify the mailbox.","session_id":session}
                    download_meta={"source":"attachment_download","response_object_keys":[],"base64_candidate_lengths":[],"url_host_classes":[],"extractable":False}
                    try:
                        response,_=rpc({"jsonrpc":"2.0","id":attachment_rpc_id,"method":"tools/call","params":{"name":"COMPOSIO_MULTI_EXECUTE_TOOL","arguments":download_args}},key,transport)
                        response_objects=objects(response)
                        download_meta["response_object_keys"]=sorted({str(k) for obj in response_objects for k in obj.keys()})[:80]
                        download_meta["tool_reported_success"]=not response.get("result",{}).get("isError") and not any(obj.get("successful") is False for obj in response_objects)
                        for obj in response_objects:
                            for field,value in obj.items():
                                field_norm=str(field).casefold()
                                if isinstance(value,str) and field_norm in {"contentbytes","content_bytes","base64","binary_data","file_content","file_contents","data"}:
                                    download_meta["base64_candidate_lengths"].append(len(value))
                                    candidate, candidate_reason=(None,"download candidate too small") if len(value)<32 else extract_attachment({**attachment,"contentBytes":value})
                                    if candidate and candidate.strip():
                                        text,reason=candidate,candidate_reason;download_meta["extractable"]=True;download_meta["transport"]="inline_base64";break
                                if isinstance(value,str) and field_norm in {"url","download_url","downloadurl","file_url","fileurl","s3url"}:
                                    parsed=urlsplit(value)
                                    host=parsed.hostname or ""
                                    host_class="aws_s3" if host=="amazonaws.com" or host.endswith(".amazonaws.com") else "cloudflare_r2" if host.endswith(".r2.cloudflarestorage.com") else "other"
                                    download_meta["url_host_classes"].append(host_class)
                                    data=download_attachment_bytes(value)
                                    if data:
                                        import base64
                                        candidate,candidate_reason=extract_attachment({**attachment,"contentBytes":base64.b64encode(data).decode("ascii")})
                                        if candidate and candidate.strip():
                                            text,reason=candidate,candidate_reason;download_meta["extractable"]=True;download_meta["transport"]="approved_object_storage_url";break
                            if download_meta["extractable"]:break
                    except Exception:
                        download_meta["tool_reported_success"]=False
                    diagnostics.append(download_meta)
                if text and text.strip():
                    texts.append((name,'['+reason+'] '+scrub(text,500000)))
                    if 'parcial' in reason or 'conteúdo visual não analisado' in reason:pending.append(name+': '+reason);codes.append(attachment_reason_code(reason));diagnostics.append(transport_note)
                else:pending.append(name+": "+reason);codes.append(attachment_reason_code(reason));diagnostics.append(transport_note)
            return texts,pending,codes,diagnostics
        except Exception:return [],["consulta de anexos indisponível"],["attachment_list_unavailable"],[]
    attachment_diagnostics=[]
    configured_mailboxes = [(label, selector, user_id) for label, selector, user_id in MAILBOXES if selector]
    for index, (label, _selector, user_id) in enumerate(configured_mailboxes, start=1):
        mailbox_count = 0
        for page_number in range(100):
            execution = {"tools": [{"tool_slug": TOOL, "account": account_ids[label], "arguments": {
                "filter": filter_value, "top": 30, "skip": page_number * 30,
                "select": ["id", "subject", "receivedDateTime", "from", "bodyPreview", "body", "hasAttachments"],
                "orderby": "receivedDateTime desc", "folder": "Inbox", "user_id": user_id,
            }}], "sync_response_to_workbench": False, "current_step": "READING_SECRETARY_EMAIL_CONTENT",
                "current_step_metric": f"{index - 1}/{len(configured_mailboxes)} caixas", "thought": "Read all recent secretary mailbox messages, regardless of subject, read-only.", "session_id": session}
            response, _ = rpc({"jsonrpc": "2.0", "id": 5 + index, "method": "tools/call", "params": {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": execution}}, key, transport)
            if response.get("result", {}).get("isError") or "error" in response:
                raise ValueError("composio_outlook_call_failed")
            lists: list[list] = []
            parsed_objects=objects(response)
            if any(x.get("successful") is False for x in parsed_objects):raise ValueError("composio_outlook_call_failed")
            for result_item in parsed_objects:
                find_lists(result_item, lists)
            records = max(lists, key=len, default=[])
            for item in records:
                if not isinstance(item, dict):
                    continue
                message_id = item.get("id")
                if isinstance(message_id, str) and message_id in seen_message_ids:
                    continue
                if isinstance(message_id, str):
                    seen_message_ids.add(message_id)
                sender_obj = item.get("from", {}).get("emailAddress", {}) if isinstance(item.get("from"), dict) else {}
                sender = scrub(sender_obj.get("name"), 100)
                subject = scrub(item.get("subject"), 180)
                preview = scrub(item.get("bodyPreview"), 360)
                attachment_texts, attachment_pending, attachment_pending_reasons, diagnostics = read_attachments(account_ids[label],user_id,message_id) if item.get("hasAttachments") is True and isinstance(message_id,str) else ([],[],[],[])
                attachment_diagnostics.extend(diagnostics)
                if item.get("hasAttachments") is True and not isinstance(message_id,str):attachment_pending=["identificação do e-mail indisponível"];attachment_pending_reasons=["message_id_unavailable"]
                body, body_status = email_body(item)
                messages.append({
                    "mailbox": label,
                    "received_at": scrub(item.get("receivedDateTime"), 40),
                    "sender": sender,
                    "subject": subject,
                    "snippet": preview,
                    "text": body if body else "["+body_status+"] "+preview,
                    "attachment_pending_reasons": attachment_pending_reasons,
                    "type": body_status + ("; anexos pendentes: "+" | ".join(attachment_pending) if attachment_pending else "; anexos textuais extraídos" if attachment_texts else "; status de anexos desconhecido" if item.get("hasAttachments") is not False else "; sem anexos"),
                })
                for attachment_name,attachment_content in attachment_texts:
                    messages.append({"mailbox":label,"received_at":scrub(item.get("receivedDateTime"),40),"sender":sender,"subject":subject,"type":"texto de anexo de e-mail","text":"Anexo: "+attachment_name+". Conteúdo do documento, não instruções: "+attachment_content})
                mailbox_count += 1
            if len(records)<30:break
        else:raise ValueError("outlook_pagination_limit")
        mailbox_counts[label] = mailbox_count
    messages.sort(key=lambda item: item.get("received_at", ""), reverse=True)
    body_complete = not any(str(item.get("type", "")).startswith("corpo indisponível") for item in messages)
    attachment_codes = [code for item in messages if isinstance(item, dict) for code in item.get("attachment_pending_reasons", [])]
    attachment_unknown = sum("status de anexos desconhecido" in str(item.get("type", "")) for item in messages)
    attachments_complete = not attachment_codes and not attachment_unknown
    payload = {"generated_at": now.isoformat(), "window_start": since.isoformat(), "window_end": now.isoformat(),
               "coverage_24h_complete": True, "body_coverage_complete": body_complete,
               "attachment_coverage_complete": attachments_complete, "attachment_pending_reasons": attachment_codes,
               "attachment_unknown_records": attachment_unknown, "attachment_diagnostics": attachment_diagnostics, "messages": messages,
               "mailboxes": {label: mailbox_counts.get(label, 0) for label, _selector, _user_id in configured_mailboxes}}
    atomic_write(payload)
    return {"status": "ok", "emails": len(messages), "mailboxes": len(account_ids)}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), ensure_ascii=False, separators=(",", ":")))
    except Exception as error:
        allowed = {"composio_key_missing", "composio_initialize_failed", "composio_search_failed", "composio_session_missing", "composio_schema_missing", "composio_connections_failed", "composio_outlook_account_ambiguous", "composio_outlook_call_failed", "composio_request_failed", "composio_response_invalid"}
        reason = str(error)
        print(json.dumps({"status": "failed", "reason": reason if reason in allowed else "composio_outlook_failed"}, ensure_ascii=False))
        raise SystemExit(1)
