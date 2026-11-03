#!/usr/bin/env python3
"""Read the secretary calendar for the next thirty days; store event basics only."""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

MCP_URL = "https://connect.composio.dev/mcp"
KEY_FILE = Path(os.environ.get("COMPOSIO_KEY_FILE", "/run/secrets/composio.key"))
ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
OUTPUT = ROOT / "staging" / "agenda.json"
CALENDAR_ALIAS = "ifckobrasol.secretaria"
CALENDAR_ID = os.environ.get("IFC_CALENDAR_ID", "primary")
TOOL = "GOOGLECALENDAR_EVENTS_LIST"
ZONE = ZoneInfo("America/Sao_Paulo")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
JID_RE = re.compile(r"\b[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)\b", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
SECRET_RE = re.compile(r"(?i)\b(?:senha|password|token|api[_ -]?key|secret|c[oó]digo de acesso|access code)\s*[:=]\s*\S+")


def rpc(payload: dict, key: str, transport_session: str | None = None) -> tuple[dict, str | None]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "x-consumer-api-key": key}
    if transport_session:
        headers["Mcp-Session-Id"] = transport_session
    request = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(8_000_000)
            content_type = response.headers.get("Content-Type", "")
            mcp_session = response.headers.get("Mcp-Session-Id")
    except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError):
        raise ValueError("composio_request_failed") from None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise ValueError("composio_response_invalid")
        try:
            return json.loads(data_lines[-1]), mcp_session
        except json.JSONDecodeError:
            raise ValueError("composio_response_invalid") from None
    try:
        return json.loads(text), mcp_session
    except json.JSONDecodeError:
        raise ValueError("composio_response_invalid") from None


def text_items(response: dict) -> list[str]:
    result = response.get("result", {})
    if not isinstance(result, dict):
        return []
    content = result.get("content", [])
    if not isinstance(content, list):
        return []
    return [item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]


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


def parsed_result_objects(response: dict) -> list[dict]:
    objects = []
    for text in text_items(response):
        for candidate in (text, text[text.find("{"):] if "{" in text else ""):
            try:
                objects.extend(walk(json.loads(candidate)))
                break
            except Exception:
                pass
    structured = response.get("result", {}).get("structuredContent") if isinstance(response.get("result"), dict) else None
    if isinstance(structured, dict):
        objects.extend(walk(structured))
    return objects


def session_id_from(response: dict) -> str | None:
    for obj in parsed_result_objects(response):
        if isinstance(obj.get("session_id"), str):
            return obj["session_id"]
        if isinstance(obj.get("sessionId"), str):
            return obj["sessionId"]
        session = obj.get("session")
        if isinstance(session, dict) and isinstance(session.get("id"), str):
            return session["id"]
    return None


def result_payload(response: dict) -> dict:
    for obj in parsed_result_objects(response):
        if isinstance(obj, dict) and any(key in obj for key in ("items", "events", "data")):
            return obj
    raise ValueError("composio_payload_missing")


def scrub(value: object, limit: int = 300) -> str:
    if not isinstance(value, str):
        return ""
    value = CPF_RE.sub("[removido]", value)
    value = PHONE_RE.sub("[removido]", value)
    value = JID_RE.sub("[removido]", value)
    value = EMAIL_RE.sub("[removido]", value)
    value = SECRET_RE.sub("[credencial removida]", value)
    def scrub_url(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,);]")
        try:
            parsed = urlsplit(raw)
            if parsed.username is not None or parsed.password is not None or not parsed.hostname:
                return "[link removido]"
            netloc = parsed.hostname
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))
        except ValueError:
            return "[link removido]"
    value = URL_RE.sub(scrub_url, value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def normalize_event_time(value: object, time_zone: object = None) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        if len(value) == 10:
            return value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            try:
                parsed = parsed.replace(tzinfo=ZoneInfo(time_zone) if isinstance(time_zone, str) else ZONE)
            except (KeyError, ValueError):
                parsed = parsed.replace(tzinfo=ZONE)
        return parsed.astimezone(ZONE).isoformat(timespec="seconds")
    except ValueError:
        return scrub(value, 50)


def safe_event_url(value: object) -> str:
    """Keep only a standard Google Calendar event link, without credentials."""
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in {"calendar.google.com", "www.google.com"}
                or parsed.path != "/calendar/event" or parsed.username or parsed.password):
            return ""
        eid = next((part.split("=", 1)[1] for part in parsed.query.split("&") if part.startswith("eid=")), "")
        if not eid or not re.fullmatch(r"[A-Za-z0-9_@%=-]+", eid):
            return ""
        return f"https://{parsed.hostname}/calendar/event?eid={eid}"
    except (ValueError, IndexError):
        return ""


def atomic_write(payload: dict) -> None:
    OUTPUT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".agenda-", suffix=".tmp", dir=OUTPUT.parent)
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
    init, transport_session = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ifc-agenda-collector", "version": "1"}}}, key)
    if "error" in init:
        raise ValueError("composio_initialize_failed")
    search_args = {"queries": [{"use_case": "list Google Calendar events for a date range, read-only", "known_fields": f"account_alias:{CALENDAR_ALIAS}, time_zone:America/Sao_Paulo, range:next_30_days"}], "session": {"generate_id": True}, "search_strategy": "auto"}
    search, transport_session = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "COMPOSIO_SEARCH_TOOLS", "arguments": search_args}}, key, transport_session)
    if search.get("result", {}).get("isError") or "error" in search:
        raise ValueError("composio_search_failed")
    workflow_session = session_id_from(search)
    if not workflow_session:
        raise ValueError("composio_session_missing")
    schema, transport_session = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "COMPOSIO_GET_TOOL_SCHEMAS", "arguments": {"tool_slugs": [TOOL], "include": ["input_schema"], "session_id": workflow_session}}}, key, transport_session)
    if schema.get("result", {}).get("isError") or "error" in schema or TOOL not in " ".join(text_items(schema)):
        raise ValueError("composio_schema_missing")
    connections, transport_session = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "COMPOSIO_MANAGE_CONNECTIONS", "arguments": {"toolkits": [{"name": "googlecalendar", "action": "list"}], "session_id": workflow_session}}}, key, transport_session)
    if connections.get("result", {}).get("isError") or "error" in connections:
        raise ValueError("composio_connections_failed")
    matches = []
    for obj in parsed_result_objects(connections):
        serialized = json.dumps(obj, ensure_ascii=False).casefold()
        if CALENDAR_ALIAS.casefold() in serialized and isinstance(obj.get("id"), str) and str(obj.get("status", "active")).casefold() in {"active", "connected", "confirmed"}:
            matches.append(obj["id"])
    accounts = list(dict.fromkeys(matches))
    if len(accounts) != 1:
        raise ValueError("composio_calendar_account_ambiguous")
    now = datetime.now(ZONE)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=30)
    execute_args = {
        "tools": [{"tool_slug": TOOL, "account": accounts[0], "arguments": {
            "calendarId": CALENDAR_ID,
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": "America/Sao_Paulo",
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 100,
            "showDeleted": True,
        }}],
        "sync_response_to_workbench": False,
        "current_step": "READING_IFC_CALENDAR",
        "current_step_metric": "0/1 calendar",
        "thought": "Read the secretary calendar for the next thirty days.",
        "session_id": workflow_session,
    }
    rows = []
    for page_number in range(100):
        response, _ = rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": execute_args}}, key, transport_session)
        if response.get("result", {}).get("isError") or "error" in response:
            raise ValueError("composio_calendar_call_failed")
        payload = result_payload(response)
        candidates = []
        def find_lists(value):
            if isinstance(value, dict):
                for key_name, child in value.items():
                    if key_name in {"items", "events"} and isinstance(child, list):
                        candidates.append(child)
                    find_lists(child)
            elif isinstance(value, list):
                for child in value:
                    find_lists(child)
        find_lists(payload)
        rows.extend(max(candidates, key=len, default=[]))
        next_token=next((obj.get('nextPageToken') for obj in walk(payload) if isinstance(obj,dict) and obj.get('nextPageToken')),None)
        if not next_token:break
        execute_args['tools'][0]['arguments']['pageToken']=next_token
    else:raise ValueError('calendar_pagination_limit')
    events = []
    seen_event_ids = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        event_id = item.get("id")
        if isinstance(event_id, str) and event_id:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
        start_obj = item.get("start") if isinstance(item.get("start"), dict) else {}
        end_obj = item.get("end") if isinstance(item.get("end"), dict) else {}
        raw_attendees = item.get("attendees") if isinstance(item.get("attendees"), list) else []
        attendee_rows = []
        rsvp_counts = {"accepted": 0, "tentative": 0, "declined": 0, "needsAction": 0, "unknown": 0}
        for attendee in raw_attendees:
            if not isinstance(attendee, dict):
                continue
            response = attendee.get("responseStatus")
            response = response if response in {"accepted", "tentative", "declined", "needsAction"} else "unknown"
            rsvp_counts[response] += 1
            name = scrub(attendee.get("displayName"), 100)
            if name and name != "[removido]":
                attendee_rows.append({
                    "name": name,
                    "response_status": response,
                    "optional": bool(attendee.get("optional")),
                    "resource": bool(attendee.get("resource")),
                    "organizer": bool(attendee.get("organizer")),
                    "self": bool(attendee.get("self")),
                })
        organizer_obj = item.get("organizer") if isinstance(item.get("organizer"), dict) else {}
        organizer_name = scrub(organizer_obj.get("displayName"), 100)
        events.append({
            "date": normalize_event_time(start_obj.get("date") or start_obj.get("dateTime"), start_obj.get("timeZone")),
            "end": normalize_event_time(end_obj.get("date") or end_obj.get("dateTime"), end_obj.get("timeZone")),
            "title": scrub(item.get("summary")),
            "location": scrub(item.get("location")),
            "description": scrub(item.get("description"), 3000),
            "organizer": organizer_name if organizer_name != "[removido]" else "",
            "attendees": attendee_rows[:50],
            "attendee_count": len(raw_attendees),
            "attendees_omitted": (item.get("attendeesOmitted") is True
                                  or len(attendee_rows) < len(raw_attendees)
                                  or len(attendee_rows) > 50),
            "attendee_rsvp_counts": rsvp_counts,
            "all_day": bool(start_obj.get("date") and not start_obj.get("dateTime")),
            "status": item.get("status") if item.get("status") in {"confirmed", "tentative", "cancelled"} else "unknown",
            "recurring": bool(item.get("recurringEventId")),
            "event_url": safe_event_url(item.get("htmlLink")),
        })
    output = {"generated_at": now.isoformat(timespec="seconds"), "window_start": start.isoformat(timespec="minutes"), "window_end": end.isoformat(timespec="minutes"), "events": events}
    atomic_write(output)
    return {"status": "ok", "events": len(events)}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), ensure_ascii=False, separators=(",", ":")))
    except Exception as error:
        reason = str(error)
        allowed = {"composio_key_missing", "composio_initialize_failed", "composio_search_failed", "composio_session_missing", "composio_schema_missing", "composio_connections_failed", "composio_calendar_account_ambiguous", "composio_calendar_call_failed", "composio_payload_missing", "composio_request_failed", "composio_response_invalid"}
        print(json.dumps({"status": "failed", "reason": reason if reason in allowed else "composio_calendar_failed"}, ensure_ascii=False))
        raise SystemExit(1)
